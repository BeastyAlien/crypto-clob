"""nobi_bridge.py -- OFI epoch signal bridge: crypto-clob metrics -> MetaTrader 5 EA.

Tails the newest metrics_*.jsonl written by the crypto-clob engine and emits
BUY/SELL signals based ONLY on order flow imbalance (OFI).

OFI here is "ofi_total" (cumulative order-flow imbalance in BTC since the
engine started, positive = buying pressure / bearish = selling pressure when
negative). Because it is cumulative, this bridge derives the imbalance over a
fixed EPOCH (epoch_sec, default 1800 s = 30 min):

    epoch_ofi = ofi_total(now) - ofi_total(epoch start)

At every epoch boundary the OFI total effectively "resets" (a new baseline is
captured) and one decision is emitted:
    epoch_ofi < 0  (bearish)  -> SELL
    epoch_ofi >= 0 (bullish)  -> BUY        (buy and hold for the next epoch)

The EA holds the position between signals, so the side is maintained for one
full epoch and only re-evaluated (and possibly flipped) at the next 30 min mark.

ALL previous triggers (Delta-CVD, NOBI threshold, cooldown/dead-zone rules)
are removed - OFI epoch is the only trigger.

Signal file format (single line, '|' separated):
    <serial>|<UTC time ISO>|<prev epoch ofi>|<now epoch ofi>|<BUY|SELL>

The MQL5 EA NobiScalpTrader.mq5 polls this file in MetaTrader 5.

Usage:
    python nobi_bridge.py                     # live, nobi_config.json
    python nobi_bridge.py --duration 0        # run until Ctrl+C
    python nobi_bridge.py --config path\\to\\nobi_config.json
    python nobi_bridge.py --dry-run --file data\\metrics_20260906_184703.jsonl
    python nobi_bridge.py --dry-run --file data\\metrics_xxx.jsonl ^
        --signals-out "<MQL5 Files>\\nobi_replay.sig"   # tester replay timeline
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class _FlushHandler(logging.StreamHandler):
    """Stream handler that flushes after every record so redirected logs stay live."""

    def emit(self, record):
        super().emit(record)
        self.flush()


def now_iso() -> str:
    """UTC 'YYYY-MM-DD HH:MM:SS' timestamp used in the signal line."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class MetricsTailer:
    """Follows metrics_*.jsonl in a directory with a sticky single-stream lock.

    The engine writes one timestamped metrics_<start>.jsonl per start and
    rotates finished runs to `.bak`; a stale/duplicate engine can leave a
    second *live* file behind. Picking "newest by mtime" on every poll made
    the bridge flip between streams whenever the other one got a write - each
    flip reset the read offset and re-entered the OFI blackout window, which
    is the silent-bridge flap. We therefore lock onto one stream:

      1. A `metrics_stream` pin (literal file name, from nobi_config.json)
         hard-locks the tail; when the pinned file is missing we tail nothing
         (no guessing).
      2. Otherwise the current stream is kept while it is alive (modified
         within ROTATE_GRACE_S). A second live file may grow forever next to
         it - we never hop over to it.
      3. We switch only on rotation - current file gone or idle beyond
         ROTATE_GRACE_S - to the strict-newest non-empty candidate.
    """

    MAX_LINE_BYTES = 2_000_000   # skip pathological lines (corrupt/huge dumps)
    ROTATE_GRACE_S = 15.0        # stream idle this long before we treat it as rotated

    def __init__(self, metrics_dir: str, stream_pin: str = ""):
        self.dir = Path(metrics_dir)
        self.stream_pin = stream_pin.strip()
        self.path = None
        self.offset = 0
        self.carry = ""

    @staticmethod
    def _mtime(f: Path) -> float:
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _size(f: Path) -> int:
        try:
            return f.stat().st_size
        except OSError:
            return 0

    def _select(self):
        """Choose the stream to tail now (sticky lock, rotation-only switch)."""
        files = [f for f in self.dir.glob("metrics_*.jsonl") if f.is_file()]
        if not files:
            return None
        pin = self.stream_pin
        if pin:
            wanted = self.dir / pin
            return wanted if wanted in files else None
        now = time.time()
        if self.path is not None:                          # sticky: never hop while alive
            if self.path in files and now - self._mtime(self.path) <= self.ROTATE_GRACE_S:
                return self.path
        nonempty = [f for f in files if self._size(f) > 0] # rotation or first run:
        return max(nonempty or files, key=self._mtime)

    def rows(self) -> list:
        path = self._select()
        if path is None:
            return []
        if path != self.path:
            self.path = path
            self.offset = 0
            self.carry = ""
            log.info("tailing %s", path.name)
        out = []
        try:
            size = path.stat().st_size
            if size < self.offset:  # file was truncated
                self.offset = 0
                self.carry = ""
            if size <= self.offset:
                return out
            with open(path, "rb") as fh:
                fh.seek(self.offset)
                while True:
                    chunk = fh.read(4 * 1024 * 1024)   # bounded chunks, never the whole file
                    if not chunk:
                        break
                    self.offset += len(chunk)
                    carry = self.carry
                    self.carry = ""
                    if len(carry) > self.MAX_LINE_BYTES:
                        carry = ""                     # oversized carried line - drop it
                    text = carry + chunk.decode("utf-8", "replace")
                    lines = text.split("\n")
                    self.carry = lines.pop()
                    for ln in lines:
                        ln = ln.strip()
                        if not ln:
                            continue
                        if len(ln) > self.MAX_LINE_BYTES:
                            log.warning("skipping oversized line (%d bytes)", len(ln))
                            continue
                        try:
                            out.append(json.loads(ln))
                        except json.JSONDecodeError:
                            continue
        except OSError as exc:
            log.warning("read error: %s", exc)
            return []
        return out

DEFAULT_CONFIG = Path(__file__).resolve().parent / "nobi_config.json"
log = logging.getLogger("nobi-bridge")


def load_cfg(path: Path) -> dict:
    if not Path(path).exists():
        sys.exit(f"Config not found: {path}")
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if not cfg.get("metrics_dir"):
        sys.exit("nobi_config.json: 'metrics_dir' is required")
    if not cfg.get("signal_file"):
        sys.exit("nobi_config.json: 'signal_file' is required")
    return cfg


class OfiEpochDetector:
    """OFI-only epoch detector: decide BUY/SELL once per epoch_sec, hold between.

    epoch_ofi = ofi_total(row) - ofi_total(epoch start); the baseline resets at
    every boundary (this is the 30-min OFI reset the strategy requires).
    """

    def __init__(self, cfg: dict):
        self.col = cfg.get("ofi_column", "ofi_total")
        self.epoch_sec = max(60.0, float(cfg.get("epoch_sec", 1800.0)))
        self.min_abs = float(cfg.get("min_abs", 0.0))   # 0 = always decide on sign
        # venue gate: do not fire while fewer than this many venues stream
        # (degraded OFI). 0 (default) disables the gate.
        self.venue_min = int(cfg.get("venue_min", 0) or 0)
        self.baseline = None                             # ofi_total at epoch start
        self.epoch_start = None                          # row ts of epoch start (s)
        self.last_epoch_val = None                       # previous epoch_ofi (reporting)
        self.fires = 0
        self.suppress_until = 0.0
        self.cur_path = None                             # metrics file being tailed

    @staticmethod
    def active_venues(row: dict) -> int:
        """Number of venues currently streaming real CVD (agreement filter)."""
        n = 0
        for key in ("cvd_venue", "cvd_window_venue"):
            v = row.get(key) or {}
            n = max(n, sum(1 for x in v.values() if abs(x or 0) > 1e-9))
        return n

    def rebase(self, why: str = "") -> None:
        """Zero the OFI baseline now; the next decision comes epoch_sec later."""
        self.baseline = None
        self.epoch_start = None
        self.last_epoch_val = None
        log.info("OFI baseline reset to zero (%s) - next decision ~%.0fs later",
                 why or "manual", self.epoch_sec)

    def feed(self, row: dict, now_s: float, tailer_path: str | None = None) -> dict | None:
        # a new metrics file = engine restarted -> ofi_total re-based to ~0
        if tailer_path is not None and tailer_path != self.cur_path:
            self.cur_path = tailer_path
            self.baseline = None
            self.epoch_start = None
            self.last_epoch_val = None
            log.info("engine metrics file changed to %s - OFI epoch baseline reset", tailer_path)
            self.suppress_until = time.time() + self.epoch_sec

        raw = row.get(self.col)
        if raw is None or raw == "":
            return None
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None

        ts_s = row.get("ts_ms", 0.0) / 1000.0
        if not ts_s or ts_s <= 0.0:
            ts_s = now_s

        if self.baseline is None:
            self.baseline = val
            self.epoch_start = ts_s
            return None                              # warmup: baseline the epoch

        epoch_ofi = val - self.baseline

        if ts_s - self.epoch_start < self.epoch_sec:
            return None                              # inside the epoch - hold

        prev = self.last_epoch_val if self.last_epoch_val is not None else 0.0
        self.last_epoch_val = epoch_ofi
        # replay-settle quiet gate: after startup or metrics-file rotation the
        # tailer re-reads buffered history; suppress epoch fires for one epoch.
        if time.time() < self.suppress_until:
            log.info("replay settle: epoch OFI %+.4f held (quiet %.0fs)",
                     epoch_ofi, max(0.0, self.suppress_until - time.time()))
            self.baseline = val
            self.epoch_start = ts_s
            return None

        # venue-quality gate: skip firing while too few venues stream (degraded OFI)
        if self.venue_min > 0 and OfiEpochDetector.active_venues(row) < self.venue_min:
            log.info("venue gate: active venues<%d epoch OFI %+.4f suppressed - holding side",
                     self.venue_min, epoch_ofi)
            self.baseline = val
            self.epoch_start = ts_s
            return None
        # decide + reset (the 30-min OFI reset)
        self.baseline = val
        self.epoch_start = ts_s

        if epoch_ofi > self.min_abs:
            side = "BUY"                             # bullish epoch -> buy & hold
        elif epoch_ofi < -self.min_abs:
            side = "SELL"                            # bearish epoch -> sell
        else:
            log.info("epoch OFI %+.4f inside |%.4f| dead band - holding current side",
                     epoch_ofi, self.min_abs)
            return None

        self.fires += 1
        return {
            "signal": side,
            "prev": prev,
            "now": epoch_ofi,
            "epoch_s": int(self.epoch_sec),
        }


def write_signal(path: Path, serial: int, sig: dict) -> None:
    line = f"{serial}|{now_iso()}|{sig['prev']:+.4f}|{sig['now']:+.4f}|{sig['signal']}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(tmp, path)  # atomic: the EA never sees a half-written line


def run_live(cfg: dict, duration: float | None, cfg_path: Path | None = None) -> None:
    tailer = MetricsTailer(cfg["metrics_dir"], cfg.get("metrics_stream", ""))
    det = OfiEpochDetector(cfg)
    signal_path = Path(cfg["signal_file"])
    if signal_path.is_file():
        try:
            old = signal_path.read_text(encoding="utf-8").strip().split("|")[0]
            det.fires = int(old)
        except Exception:
            pass
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("metrics dir : %s", cfg["metrics_dir"])
    log.info("stream pin : %s (blank = auto sticky)", tailer.stream_pin or "(auto)")
    log.info("signal file : %s", signal_path)
    log.info("column      : %s", det.col)
    log.info("rule        : OFI epoch=%ds | SELL if epoch OFI < 0 | BUY if >= 0 | all previous triggers removed",
             int(det.epoch_sec))
    anchor_path = Path(cfg.get("anchor_file", str(signal_path.parent / "nobi_anchor.sig")))
    last_anchor = anchor_path.stat().st_mtime if anchor_path.exists() else 0.0
    log.info("anchor file : %s  (EA attach zeroes OFI and restarts the %.0fs cycle)",
             anchor_path, int(det.epoch_sec))
    t0 = time.time()
    det.suppress_until = t0 + det.epoch_sec   # startup settle
    cfg_mtime = cfg_path.stat().st_mtime if cfg_path and cfg_path.exists() else 0.0
    while True:
        try:
            #--- EA attach anchor: zero OFI baseline, restart the 30-min countdown ---
            if anchor_path.exists():
                an_mt = anchor_path.stat().st_mtime
                if an_mt > last_anchor:
                    last_anchor = an_mt
                    det.rebase("EA attach anchor")
# hot-reload: pick up AI-applied settings (epoch/min_abs/venue gate) live
            if cfg_path and cfg_path.exists():
                cm = cfg_path.stat().st_mtime
                if cm != cfg_mtime:
                    cfg_mtime = cm
                    try:
                        nc = json.loads(cfg_path.read_text(encoding="utf-8"))
                        det.epoch_sec = max(60.0, float(nc.get("epoch_sec", det.epoch_sec)))
                        det.min_abs = float(nc.get("min_abs", det.min_abs))
                        det.venue_min = int(nc.get("venue_min", det.venue_min) or 0)
                        log.info("AI settings hot-reloaded: epoch=%ds min_abs=%.1f venue_min=%d",
                                 int(det.epoch_sec), det.min_abs, det.venue_min)
                    except Exception as exc:
                        log.warning("config reload failed: %s", exc)
            for row in tailer.rows():
                hit = det.feed(row, time.time(), getattr(tailer, "path", None))
                if hit:
                    det.fires += 1
                    write_signal(signal_path, det.fires, hit)
                    log.info(">> EPOCH %s #%d  OFI %+.4f -> %+.4f (%.0fs)",
                             hit["signal"], det.fires, hit["prev"], hit["now"], hit["epoch_s"])
        except KeyboardInterrupt:
            break
        if duration is not None and time.time() - t0 >= duration:
            break
        time.sleep(float(cfg.get("poll_sec", 2.0)))
    log.info("stopped. signals written: %d", det.fires)


def run_dry(cfg: dict, file: str, signals_out: str = "") -> None:
    det = OfiEpochDetector(cfg)
    rows = 0
    buys = sells = 0
    first_ts = last_ts = None
    timeline = []                              # (serial, ts_s, side, prev, now)
    for line in Path(file).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("ts_ms", 0)
        if first_ts is None:
            first_ts = ts
        last_ts = ts
        hit = det.feed(row, ts / 1000.0, Path(file).name)
        if hit:
            timeline.append((len(timeline) + 1, ts / 1000.0, hit["signal"], hit["prev"], hit["now"]))
            if hit["signal"] == "BUY":
                buys += 1
            else:
                sells += 1
            when = datetime.fromtimestamp(ts / 1000.0, timezone.utc).strftime("%m-%d %H:%M:%S")
            print(f"  {when}  {hit['signal']:4s}  epoch OFI {hit['prev']:+.4f} -> {hit['now']:+.4f}")
    print("-" * 60)
    print(f"rows read      : {rows}")
    print(f"column         : {det.col}   epoch_sec={int(det.epoch_sec)}")
    print(f"epoch signals  : {buys + sells}  (BUY {buys} / SELL {sells})")
    print(f"window         : {datetime.fromtimestamp(first_ts / 1000, timezone.utc).strftime('%H:%M:%S') if first_ts else '-'}"
          f" -> {datetime.fromtimestamp(last_ts / 1000, timezone.utc).strftime('%H:%M:%S') if last_ts else '-'} UTC")
    if det.last_epoch_val is not None:
        print(f"last epoch OFI : {det.last_epoch_val:+.4f}")
    if signals_out:
        Path(signals_out).parent.mkdir(parents=True, exist_ok=True)
        with Path(signals_out).open("w", encoding="utf-8", newline="\n") as fh:
            for serial, ts_s, side, prev, now in timeline:
                when = datetime.fromtimestamp(ts_s, timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
                fh.write(f"{serial}|{when}|{prev:+.4f}|{now:+.4f}|{side}\n")
        print(f"replay timeline: {signals_out} ({len(timeline)} signals - MQL5 StringToTime format)")


def main() -> None:
    ap = argparse.ArgumentParser(description="crypto-clob OFI-epoch -> MetaTrader 5 scalp signal bridge")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--duration", type=float, default=None, help="seconds; 0 = run until Ctrl+C")
    ap.add_argument("--dry-run", action="store_true", help="replay a metrics file, print signals only")
    ap.add_argument("--file", default="", help="metrics file to replay in dry-run mode")
    ap.add_argument("--signals-out", default="", help="dry-run only: write a tester-replay timeline to this path "
                     "(lines: serial|yyyy.MM.dd HH:MM:SS UTC|prev|now|SIDE)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[_FlushHandler()],
    )

    cfg = load_cfg(args.config)
    if args.dry_run:
        if not args.file:
            sys.exit("--dry-run requires --file <metrics jsonl>")
        run_dry(cfg, args.file, args.signals_out)
        return
    duration = args.duration
    if duration is not None and duration <= 0:
        duration = None
    try:
        run_live(cfg, duration, Path(args.config))
    except KeyboardInterrupt:
        log.info("interrupted")


if __name__ == "__main__":
    main()