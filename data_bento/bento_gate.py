# -*- coding: utf-8 -*-
"""bento_gate.py - DataBento futures order-flow confirmation gate for NobiScalpTrader.

What it does
------------
The bridge (nobi_bridge.py) emits SPOT OFI epoch signals:
    <serial>|<UTC time>|<prev epoch OFI>|<now epoch OFI>|<BUY|SELL>

This gate CROSS-CHECKS that signal against the CME Micro BTC futures tape
(DataBento GLBX.MDP3 / MBTU6, the same instrument the data_bento research
pipeline already uses) and writes a verdict the EA / operator can rely on to
avoid unnecessary flips:

    <bridge serial>|BUY|SELL|CONFIRM|BLOCK|HOLD|fut_ofi|fut_cvd|utc

Verdict rules (all configurable via nobi_config.json -> "bento_gate"):
    - futures OFI (top-of-book size refresh) and futures CVD (aggressive
      trade delta) are computed over one epoch window ending at the signal
      time, using DataBento bbo-1s + trades.
    - CONFIRM : futures flow pointed the SAME way (OFI and CVD both agree).
    - BLOCK   : futures flow pointed the OPPOSITE way with real conviction
                (OFI and CVD both disagree beyond thresholds) -> do not flip.
    - HOLD    : no signal / mixed / data missing -> keep current side, do
                not flip on this serial.
    - NODATA  : DataBento unreachable or request failed -> gate leaves the
                decision to the EA's configured fail mode (default: treat as
                CONFIRM so the strategy keeps working if the gate is down).

The gate never places orders and never edits nobi_signal.sig; it only writes
the verdict file (nobi_gate.sig) next to the bridge signal.

Usage
-----
    python data_bento\bento_gate.py                # watch mode (tails signal file)
    python data_bento\bento_gate.py --once         # single check, exit
    python data_bento\bento_gate.py --dry-run      # recheck the current signal once, no loop
    python data_bento\bento_gate.py --signal-file path       # override signal file
    python data_bento\bento_gate.py --gate-file path         # override gate output
    python data_bento\bento_gate.py --dataset GLBX.MDP3 --symbol MBTU6

Key
---
Reads DATABENTO_KEY from the environment (set it, or put it in
data_bento\\.env which is gitignored).

Integration
-----------
Mode A (recommended): run the gate alongside the bridge (supervise.py /
VS Code task) and point the EA at nobi_gate.sig via a future input such as
InpGateFile + InpGateMode. The EA listens only when the gate says CONFIRM.
Mode B (operator): check nobi_gate.sig manually before letting the EA flip.

Heuristic research signal on public tape data - NOT a profitability
guarantee. Validate in the Strategy Tester before live use.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import urllib.request
    import urllib.error
    import urllib.parse
except Exception:  # pragma: no cover - stdlib always present
    pass

HERE = Path(__file__).resolve().parent

DEFAULT_CONFIG = HERE.parent / "nobi_config.json"
BASE = "https://hist.databento.com"
PX = 1_000_000_000          # DataBento fixed-point prices: 1e9 per USD
EPOCHS = [60, 120, 180, 300, 600, 900, 1800, 3600]
GATE_DELAY_S = 90           # DataBento intraday delay margin before 'now' (seconds)


# --------------------------------------------------------------------------
# minimal dot-env loader (no third-party deps)
# --------------------------------------------------------------------------
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def api_key() -> str:
    load_dotenv(HERE / ".env")
    key = (os.environ.get("DATABENTO_KEY") or "").strip()
    if not key:
        sys.exit("DATABENTO_KEY is required (set env var or data_bento\\.env)")
    return key


def _auth_header() -> dict:
    return {"Authorization": "Basic " + base64.b64encode((api_key() + ":").encode()).decode()}


def db_get(path: str, params: dict, timeout: int = 180):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_auth_header())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def availability_end(dataset: str) -> int:
    """Latest epoch-second (UTC) for which this dataset has intraday data.

    DataBento's `available_end` lags wall-clock by minutes, and a query with
    `end` past it fails with HTTP 422 (data_end_after_available_end). We read
    the availability control to clamp the gate window.
    """
    try:
        st, body = db_get("/v0/dataset/{}/availability".format(dataset), {}, timeout=60)
        if st != 200:
            return 0
        data = json.loads(body.decode("utf-8", "replace"))
        ctrl = data.get("controls", {})
        # Priority: intraday range is the live intraday availability;
        # live_data control is the streaming cap. delayed_data is a different
        # (delayed tape) control and must NOT cap these queries.
        candidates = [data.get("intraday", {}).get("end"),
                      ctrl.get("live_data", {}).get("end")]
        parsed = []
        for cand in candidates:
            if not cand:
                continue
            try:
                dt = datetime.fromisoformat(cand.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                parsed.append(int(dt.timestamp()))
            except Exception:
                continue
        if not parsed:
            return 0
        return min(parsed)
    except Exception:
        return 0


def load_cfg() -> dict:
    if not DEFAULT_CONFIG.exists():
        return {}
    return json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def parse_signal(line: str) -> dict | None:
    p = line.strip().split("|")
    if len(p) < 5:
        return None
    try:
        serial = int(p[0])
    except ValueError:
        return None
    return {"serial": serial, "when": p[1], "prev": p[2], "now": p[3], "side": p[4]}


def ds_row(t: str) -> dict:
    """Load one DataBento CSV response body into {cols, rows}."""
    text = t.decode("utf-8", "replace")
    lines = text.splitlines()
    if not lines:
        return {"cols": [], "rows": []}
    cols = lines[0].split(",")
    rows = [ln.split(",") for ln in lines[1:] if ln.strip()]
    return {"cols": cols, "rows": rows}


def idx(cols: list, name: str) -> int:
    return cols.index(name)


def fetch_epoch(symbol: str, dataset: str, signal_time: int, epoch_sec: int) -> dict:
    """Fetch bbo-1s + trades over the epoch ending at min(available_end, signal_time).

    DataBento intraday availability lags wall-clock by several minutes; a 422
    paints `available_end` in its payload. We use the availability endpoint
    first and, on 422, clamp `end` to the value the API reports, then retry.
    """
    out = {"bbo": None, "trades": None, "err": ""}
    data_end = availability_end(dataset)
    if data_end <= 0:
        out["err"] = "availability:not-available "
        return out
    if 0 < signal_time < data_end:
        data_end = signal_time                           # align window to the signal epoch
    for schema, key in (("bbo-1s", "bbo"), ("trades", "trades")):
        st, body = get_with_retry(symbol, dataset, schema, int(epoch_sec), data_end)
        if st != 200:
            out["err"] += f"{schema}:HTTP{st} "
            continue
        d = ds_row(body)
        if d["rows"]:
            out[key] = d
    return out


def get_with_retry(symbol: str, dataset: str, schema: str, epoch_sec: int,
                   data_end: int) -> tuple:
    """GET a timeseries range; on a 422 clamp end to API-reported availability."""
    if data_end <= 0:
        return 0, b""
    end_iso = datetime.fromtimestamp(data_end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    start_iso = datetime.fromtimestamp(data_end - epoch_sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    st, body = db_get("/v0/timeseries.get_range", {
        "dataset": dataset, "start": start_iso, "end": end_iso,
        "symbols": symbol, "schema": schema, "encoding": "csv",
        "pretty_px": "false", "pretty_ts": "false",
    }, timeout=120)
    if st != 422:
        return st, body
    try:
        payload = json.loads(body.decode("utf-8", "replace")).get("payload", {})
        avail = payload.get("available_end") or payload.get("end")
    except Exception:
        avail = None
    if not avail:
        return st, body
    try:
        dt = datetime.fromisoformat(str(avail).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data_end2 = int(dt.timestamp())
    except Exception:
        return st, body
    if data_end2 >= data_end:
        return st, body
    end_iso2 = datetime.fromtimestamp(data_end2, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    start_iso2 = datetime.fromtimestamp(data_end2 - epoch_sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return db_get("/v0/timeseries.get_range", {
        "dataset": dataset, "start": start_iso2, "end": end_iso2,
        "symbols": symbol, "schema": schema, "encoding": "csv",
        "pretty_px": "false", "pretty_ts": "false",
    }, timeout=120)


def compute_futures_flow(d: dict, epoch_sec: int) -> dict:
    """Futures OFI (bbo size refresh) + CVD (trades) over the fetched window."""
    res = {"ofi": 0.0, "cvd": 0.0, "bbo_rows": 0, "trade_rows": 0}
    # ---- OFI from bbo-1s: sum of (bid_sz change - ask_sz change) ----
    bbo = d.get("bbo")
    if bbo:
        cols = bbo["cols"]
        i_ts = idx(cols, "ts_event")
        i_bpx = idx(cols, "bid_px_00")
        i_apx = idx(cols, "ask_px_00")
        i_bsz = idx(cols, "bid_sz_00")
        i_asz = idx(cols, "ask_sz_00")
        prev = None
        for r in bbo["rows"]:
            try:
                bsz = int(r[i_bsz])
                asz = int(r[i_asz])
            except (ValueError, IndexError):
                continue
            if prev is not None:
                res["ofi"] += float((bsz - prev[0]) - (asz - prev[1]))
            prev = (bsz, asz)
            res["bbo_rows"] += 1
    # ---- CVD from trades: sum(B side size) - sum(A side size) ----
    tr = d.get("trades")
    if tr:
        cols = tr["cols"]
        i_side = idx(cols, "side")
        i_sz = idx(cols, "size")
        for r in tr["rows"]:
            try:
                side = r[i_side]
                sz = int(r[i_sz])
            except (ValueError, IndexError):
                continue
            if side == "B":
                res["cvd"] += sz
            elif side == "A":
                res["cvd"] -= sz
            res["trade_rows"] += 1
    return res


def verdict(bridge_side: str, flow: dict, th_ofi: float, th_cvd: float) -> str:
    ofi = flow["ofi"]
    cvd = flow["cvd"]
    if ofi == 0.0 and cvd == 0.0:
        return "NODATA" if (flow["bbo_rows"] == 0 and flow["trade_rows"] == 0) else "HOLD"
    fut_dir = 0
    if ofi >= th_ofi and cvd >= th_cvd:
        fut_dir = 1          # bullish futures
    elif ofi <= -th_ofi and cvd <= -th_cvd:
        fut_dir = -1         # bearish futures
    if fut_dir == 0:
        return "HOLD"        # mixed / inside thresholds -> don't flip
    br = 1 if bridge_side == "BUY" else -1
    return "CONFIRM" if fut_dir == br else "BLOCK"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def gate_for_signal(cfg: dict, signal: dict, dataset: str, symbol: str) -> dict:
    g = cfg.get("bento_gate", {})
    epoch_sec = int(g.get("epoch_sec", cfg.get("epoch_sec", 300)))
    th_ofi = float(g.get("min_abs_fut_ofi", 5.0))
    th_cvd = float(g.get("min_abs_fut_cvd", 5))
    try:
        when = datetime.strptime(signal["when"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        sig_time = int(when.timestamp())
    except Exception:
        sig_time = int(time.time())
    d = fetch_epoch(symbol, dataset, sig_time, epoch_sec)
    if d["err"]:
        return {"verdict": "NODATA", "error": d["err"].strip(), "ofi": 0.0, "cvd": 0.0}
    try:
        flow = compute_futures_flow(d, epoch_sec)
    except Exception as exc:                       # schema drift / malformed rows -> NODATA
        return {"verdict": "NODATA", "error": "compute: {}".format(exc), "ofi": 0.0, "cvd": 0.0}
    v = verdict(signal["side"], flow, th_ofi, th_cvd)
    return {"verdict": v, "ofi": round(flow["ofi"], 2), "cvd": round(flow["cvd"], 2),
            "bbo_rows": flow["bbo_rows"], "trade_rows": flow["trade_rows"], "error": ""}


def write_gate(path: Path, signal: dict, gate: dict) -> None:
    line = f"{signal['serial']}|{signal['side']}|{gate['verdict']}|{gate['ofi']:+.2f}|{gate['cvd']:+.2f}|{now_iso()}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description="DataBento futures confirmation gate for the NOBI bridge signal")
    ap.add_argument("--signal-file", default="")
    ap.add_argument("--gate-file", default="")
    ap.add_argument("--dataset", default="GLBX.MDP3")
    ap.add_argument("--symbol", default="MBTU6")
    ap.add_argument("--once", action="store_true", help="one check then exit")
    ap.add_argument("--dry-run", action="store_true", help="recheck current signal once and print")
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--print", dest="to_print", action="store_true", help="always print the verdict line")
    args = ap.parse_args()

    cfg = load_cfg()
    signal_file = Path(args.signal_file or cfg.get("signal_file", ""))
    if not signal_file.is_absolute():
        signal_file = HERE.parent / signal_file
    if args.gate_file:
        gate_file = Path(args.gate_file)
        if not gate_file.is_absolute():
            gate_file = signal_file.parent / gate_file
    else:
        gate_file = signal_file.with_name("nobi_gate.sig")

    last = None
    while True:
        try:
            if not signal_file.exists():
                # keep scanning (e.g. bridge starts later); gate stays silent
                if args.dry_run or args.once:
                    print("NODATA signal file missing:", signal_file)
                    break
                time.sleep(args.poll)
                continue
            line = signal_file.read_text(encoding="utf-8").strip()
            sig = parse_signal(line) if line else None
            if sig is None or sig["serial"] == last:
                if args.dry_run:
                    print("NODATA no/new signal")
                    break
                time.sleep(args.poll)
                continue
            try:
                gate = gate_for_signal(cfg, sig, args.dataset, args.symbol)
            except Exception as exc:               # never leave the gate file on an older serial
                gate = {"verdict": "NODATA", "ofi": 0.0, "cvd": 0.0, "error": str(exc)}
            write_gate(gate_file, sig, gate)
            last = sig["serial"]
            
            
            if args.to_print or args.dry_run:
                print(f"#{sig['serial']} {sig['side']:4s} -> {gate['verdict']:7s} "
                      f"fut_OFI={gate['ofi']:+.2f} fut_CVD={gate['cvd']:+.2f} {gate.get('error','')}".rstrip())
            if args.once or args.dry_run:
                break
            time.sleep(args.poll)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # keep the watcher alive on transient errors
            if args.dry_run:
                print("ERROR", exc)
                break
            time.sleep(args.poll)


if __name__ == "__main__":
    main()