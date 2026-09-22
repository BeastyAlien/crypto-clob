# -*- coding: utf-8 -*-
"""nobi_ai.py - the NOBI self-improving "brain" for the OFI epoch strategy.

Continuous learning loop over the data the crypto-clob engine / bridge
collects. Three verbs:

  collect   (run every few minutes)
      Appends ONLY new rows (byte-offset checkpoints, survives engine
      restarts and file rotations) from data-ui/metrics_*.jsonl into a
      compact persistent dataset: ai/dataset.csv (rolling window).
      Large sealed history files are sampled ONCE from their tail
      (backfill) and never re-read; the live file is delta-ingested.

  think     (run every hour)
      Replays the bridge epoch rule over the accumulated dataset across a
      grid of (epoch_sec x min_abs x venue_min), measures per config:
        - signals fired, hold time
        - win rate, expectancy, total PnL in USD (signal -> next signal)
        - MFE/MAE quantiles  -> recommended TP / trail / BE / SL
      Detects market regime (Hurst R/S, |dCVD| vol clustering, OFI
      autocorrelation), keeps a persistent trial ledger + thought log, and
      if a config is a robust improvement over the current one, marks it
      for application.

  apply --bridge
      Writes the chosen bridge settings (epoch_sec, min_abs, venue_min)
      into nobi_config.json. The bridge hot-reloads them WITHOUT restart.
      EA money-management numbers are only written to ai/recommended.json
      - never auto-applied to the live account.

  status - human-readable brain state.

Safety: this module NEVER trades and NEVER touches the MetaTrader account.
It only reads metrics files and writes its own state under <crypto-clob>/ai
plus (on explicit --bridge) the bridge config.

Stdlib only; depends on helpers from export_metrics.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from export_metrics import autocorr, hurst_rs, log_rets, mid_of, n_venues_of, q
except Exception:  # pragma: no cover - fallback if run standalone
    def autocorr(xs, k):  # noqa: F811
        if len(xs) < k + 4:
            return 0.0
        m = sum(xs) / len(xs)
        num = d1 = d2 = 0.0
        for i in range(len(xs) - k):
            a, b = xs[i] - m, xs[i + k] - m
            num += a * b
            d1 += a * a
            d2 += b * b
        return num / math.sqrt(d1 * d2) if d1 * d2 > 0 else 0.0

    def hurst_rs(xs):  # noqa: F811
        return None

    def log_rets(xs):  # noqa: F811
        out, prev = [], None
        for x in xs:
            if x is None or x != x or x <= 0:
                continue
            if prev is not None and prev > 0:
                out.append(math.log(x / prev))
            prev = x
        return out

    def mid_of(r):
        bid = r.get("best_bid_bucket")
        ask = r.get("best_ask_bucket")
        if not bid or not ask or bid <= 0 or ask <= 0:
            return None
        return (bid + ask) / 2.0

    def n_venues_of(r):
        n = 0
        for key in ("cvd_venue", "cvd_window_venue"):
            v = r.get(key) or {}
            n = max(n, sum(1 for x in v.values() if abs(x or 0) > 1e-9))
        return n

    def q(xs, p):
        xs = sorted(xs)
        if not xs:
            return 0.0
        return xs[max(0, min(len(xs) - 1, int(p * len(xs))))]

HERE = Path(__file__).resolve().parent
DATA_UI = HERE.parent / "crypto-clob-ui" / "data-ui"
AI_DIR = HERE / "ai"
DATASET = AI_DIR / "dataset.csv"
DEALS = AI_DIR / "deals.csv"
STATE = AI_DIR / "state.json"
THOUGHTS = AI_DIR / "thoughts.log"
RECOMMENDED = AI_DIR / "recommended.json"
REPORT = AI_DIR / "report_latest.txt"
BRIDGE_CFG = HERE / "nobi_config.json"

RETENTION_ROWS = 2_500_000          # dataset rolling window
THINK_ROWS = 300_000                # rows replayed per think
TAIL_ROWS = 120_000                 # one-time backfill rows per sealed big file
BIG_FILE = 64_000_000               # bytes; above this a history file is "sealed"
MIN_SIGNALS = 8                     # min signals for a config to be considered
MIN_IMPROVE = 0.15                  # relative edge improvement needed to switch
GAP_MS = 120_000                    # data hole: larger gap splits excursions
DENSE_MS = 60_000                   # density filter: rows closer than this are "live"
GRID_EPOCH = [180, 300, 600]
GRID_MINABS = [10, 25, 50, 100]
GRID_VENUE = [0, 3, 4]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": 3, "created": now_iso(), "last_collect": "", "last_think": "",
        "checkpoints": {}, "rows": 0, "collects": 0, "thinks": 0,
        "regimes": [], "trials": [], "ledger": [], "deals_seen": [], "deals_rows": 0,
        "maturity": {"epochs_seen": 0, "trades_analyzed": 0, "edge": 0.0, "level": "novice"},
        "current": {}, "recommendations": [],
    }


def save_state(st: dict) -> None:
    AI_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def think(msg: str) -> None:
    """Append one line to the thought log (the brain's diary)."""
    AI_DIR.mkdir(parents=True, exist_ok=True)
    with open(THOUGHTS, "a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} | {msg}\n")


def load_dataset(max_rows: int = THINK_ROWS) -> list[dict]:
    """Load the rolling dataset (chronological order), capped at max_rows tail."""
    if not DATASET.exists():
        return []
    out = []
    with open(DATASET, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("ts_ms"):
                continue
            p = ln.split(",")
            try:
                out.append({
                    "ts_ms": int(float(p[0])),
                    "mid": float(p[1]) if p[1] else None,
                    "dw_bid": float(p[2]) if p[2] else 0.0,
                    "dw_ask": float(p[3]) if p[3] else 0.0,
                    "nobi": float(p[4]) if p[4] else None,
                    "ofi": float(p[5]) if p[5] else None,
                    "cvd": float(p[6]) if p[6] else None,
                    "aggr": float(p[7]) if p[7] else 0.0,
                    "n_ven": int(float(p[8])) if p[8] else 0,
                    "sweeps": int(float(p[9])) if p[9] else 0,
                    "flags": int(float(p[10])) if p[10] else 0,
                })
            except (ValueError, IndexError):
                continue
            if len(out) > max_rows:
                out.pop(0)
    return out


def compact_row(r: dict) -> str:
    m = mid_of(r)
    nv = n_venues_of(r)
    return ",".join([
        str(r.get("ts_ms") or 0),
        ("%.3f" % m) if m else "",
        ("%.4f" % (r.get("depth_w_bid") or 0.0)),
        ("%.4f" % (r.get("depth_w_ask") or 0.0)),
        ("%.6g" % r["noBi_n_10"]) if r.get("noBi_n_10") is not None else "",
        ("%.6g" % r["ofi_total"]) if r.get("ofi_total") is not None else "",
        ("%.6g" % r["cvd_total"]) if r.get("cvd_total") is not None else "",
        ("%.4f" % (r.get("aggression_ratio") or 0.0)),
        str(nv),
        str(int(r.get("sweeps") or 0)),
        str(int((r.get("sweeps") or 0) + (r.get("iceberg_flags") or 0)
                + (r.get("spoof_flags") or 0) + (r.get("absorption_flags") or 0))),
    ]) + "\n"


def append_compact(rows) -> None:
    """rows may be dicts (parsed) or pre-compacted strings."""
    if not rows:
        return
    with open(DATASET, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r if isinstance(r, str) else compact_row(r))


def tail_rows(path: Path, n: int) -> list[str]:
    """LAST n rows of a big file as COMPACT STRINGS (kept chronological).

    Memory stays bounded: only compact ~120-byte strings are retained, the
    fat JSON dicts (heatmap arrays etc.) are transient.
    """
    rows = []
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        chunk = 1 << 20
        pos = size
        tail = b""
        while pos > 0 and len(rows) < n:
            pos = max(0, pos - chunk)
            fh.seek(pos)
            data = fh.read(min(chunk, size - pos))
            buf = data + tail
            lines = buf.split(b"\n")
            tail = lines[0]
            for ln in reversed(lines[1:]):
                if len(rows) >= n:
                    break
                s = ln.decode("utf-8", "replace").strip()
                if not s:
                    continue
                try:
                    rows.append(compact_row(json.loads(s)))
                except json.JSONDecodeError:
                    continue
    rows.reverse()
    return rows


def trim_dataset() -> None:
    if not DATASET.exists() or DATASET.stat().st_size == 0:
        return
    nlines = 0
    with open(DATASET, "r", encoding="utf-8") as fh:
        for _ in fh:
            nlines += 1
    if nlines <= RETENTION_ROWS:
        return
    keep = []
    with open(DATASET, "r", encoding="utf-8") as fh:
        for ln in fh:
            keep.append(ln)
            if len(keep) > RETENTION_ROWS:
                keep.pop(0)
    tmp = DATASET.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
    os.replace(tmp, DATASET)


def journal_path() -> Path | None:
    """nobi_deals.sig location: config deals_file, else signal_file's folder."""
    try:
        cfg = json.loads(BRIDGE_CFG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    p = cfg.get("deals_file")
    if p:
        return Path(p)
    sf = cfg.get("signal_file")
    if sf:
        return Path(sf).with_name("nobi_deals.sig")
    return None


def load_deals() -> list[dict]:
    """Real closed deals from ai/deals.csv (epoch columns order fixed)."""
    if not DEALS.is_file():
        return []
    out = []
    with open(DEALS, encoding="utf-8", errors="replace") as fh:
        next(fh, None)                      # header
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            f = ln.split(",")
            if len(f) < 8:
                continue
            try:
                out.append({"ticket": int(f[0]), "ts_close": float(f[1]), "side": f[2],
                            "volume": float(f[3]), "price_open": float(f[4]),
                            "price_close": float(f[5]), "profit": float(f[6]), "reason": f[7]})
            except ValueError:
                continue
    return out


def epoch_events(rows: list[dict], epoch_sec: float = 300.0, min_abs: float = 25.0) -> list:
    """Bridge-rule replica: (epoch_end_ts, side) per fired epoch boundary."""
    ev, base, e0 = [], None, None
    for r in rows:
        raw = r.get("ofi_total")
        if raw is None or raw == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        ts = (r.get("ts_ms", 0) or 0) / 1000.0
        if not ts:
            continue
        if base is None:
            base, e0 = v, ts
            continue
        ofi = v - base
        if ts - e0 < epoch_sec:
            continue
        base, e0 = v, ts
        if ofi > min_abs:
            ev.append((ts, "BUY"))
        elif ofi < -min_abs:
            ev.append((ts, "SELL"))
    return ev


def collect_cmd(args) -> int:
    src_dir = Path(args.dir)
    days = float(getattr(args, "days", 60.0))
    st = load_state()
    cps = st.get("checkpoints", {})
    AI_DIR.mkdir(parents=True, exist_ok=True)
    new_rows = 0

    cutoff = time.time() - days * 86400.0
    files = [f for f in src_dir.glob("metrics_*.jsonl")
             if f.is_file() and f.stat().st_size > 0 and f.stat().st_mtime >= cutoff]
    files.sort(key=lambda p: p.stat().st_mtime)
    if not files:
        print("collect: no metrics files in window")
        return 0
    newest = files[-1]

    for f in files:
        key = str(f)
        cp = cps.get(key)
        size = f.stat().st_size
        if cp and cp.get("size") == size and cp.get("offset", 0) >= size:
            continue                      # already fully consumed
        is_live = (f is newest) or size <= BIG_FILE
        if is_live:
            offset = int(cp.get("offset", 0)) if cp else 0
            if offset > size:
                offset = 0
            if offset >= size:
                cps[key] = {"offset": size, "size": size}
                continue
            try:
                with open(f, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
            except OSError:
                continue
            cps[key] = {"offset": size, "size": size, "mtime": f.stat().st_mtime}
            if not data:
                continue
            rows = []
            for ln in data.decode("utf-8", "replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
            append_compact(rows)
            new_rows += len(rows)
        else:
            # sealed big history file: one-time tail backfill, never re-read
            rows = tail_rows(f, TAIL_ROWS)
            append_compact(rows)
            cps[key] = {"offset": size, "size": size, "backfill": True}
            new_rows += len(rows)

    trim_dataset()
    st["checkpoints"] = cps
    # --- real-fill journal tail: nobi_deals.sig -> ai/deals.csv ---
    dp = journal_path()
    if dp is not None and dp.is_file() and dp.stat().st_size > 0:
        seen = set(st.get("deals_seen", []))
        newd = 0
        AI_DIR.mkdir(parents=True, exist_ok=True)
        if not DEALS.exists() or DEALS.stat().st_size == 0:
            DEALS.write_text("ticket,ts_close,side,volume,price_open,price_close,profit,reason\n",
                             encoding="utf-8")
        with open(DEALS, "a", encoding="utf-8", newline="\n") as fh:
            for ln in dp.read_text(encoding="ansi", errors="replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                fl = ln.split("|")
                if len(fl) < 9:
                    continue
                try:
                    tick = int(fl[0])
                    ts_c = datetime.strptime(fl[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if tick in seen:
                    continue
                seen.add(tick)
                newd += 1
                fh.write(f"{tick},{int(ts_c)},{fl[3]},{fl[4]},{fl[5]},{fl[6]},{fl[7]},{fl[8]}\n")
        if newd > 0:
            st["deals_seen"] = sorted(seen)[-5000:]
            st["deals_rows"] = st.get("deals_rows", 0) + newd
            print(f"deals: +{newd} (total {st['deals_rows']})")
    st["collects"] = st.get("collects", 0) + 1
    st["rows"] = st.get("rows", 0) + new_rows
    st["last_collect"] = now_iso()
    save_state(st)
    print(f"collect: +{new_rows} rows (total ingested {st['rows']}, dataset capped at {RETENTION_ROWS})")
    return 0


# --------------------------------------------------------------- replay ----
def replay(rows: list[dict], epoch_sec: float, min_abs: float, venue_min: int) -> dict:
    """Replay the bridge epoch rule over rows; measure forward excursions.

    Entry = epoch boundary where |epoch OFI| > min_abs (BUY if positive,
    SELL if negative). Exit = next epoch boundary (today's EA behavior).
    Excursion tracking stops at data holes (> GAP_MS) so reconnect gaps
    cannot corrupt MFE/MAE. Returns stats in USD per epoch (1 BTC).
    """
    baseline = None
    estart = None
    events = []                     # (row_idx, ts_ms, side)
    for i, r in enumerate(rows):
        v = r["ofi"]
        t = r["ts_ms"]
        if v is None or v != v or not t:
            continue
        if baseline is None:
            baseline = v
            estart = t
            continue
        if (t - estart) / 1000.0 < epoch_sec:
            continue
        eof = v - baseline
        baseline = v
        estart = t
        if venue_min > 0 and (r["n_ven"] or 0) < venue_min:
            continue
        if eof > min_abs:
            events.append((i, t, "BUY"))
        elif eof < -min_abs:
            events.append((i, t, "SELL"))
    if len(events) < 2:
        return {"n": 0}

    mfe, mae, pnl, holds = [], [], [], []
    for k in range(len(events) - 1):
        i0, t0, side = events[k]
        i1, t1, _ = events[k + 1]
        seg = rows[i0:i1 + 1]
        m0 = None
        for r in seg:
            if r["mid"] is not None:
                m0 = r["mid"]
                break
        if m0 is None:
            continue
        sign = 1.0 if side == "BUY" else -1.0
        fav = adv = 0.0
        endm = m0
        prev_t = seg[0]["ts_ms"]
        for r in seg:
            if r["ts_ms"] - prev_t > GAP_MS:      # data hole: stop tracking
                break
            prev_t = r["ts_ms"]
            m = r["mid"]
            if m is None:
                continue
            z = sign * (m - m0)
            if z > fav:
                fav = z
            if z < adv:
                adv = z
            endm = m
        mfe.append(fav)
        mae.append(-adv)
        pnl.append(sign * (endm - m0))
        holds.append((t1 - t0) / 60000.0)
    if not pnl:
        return {"n": 0}
    wins = sum(1 for p in pnl if p > 0)
    return {
        "n": len(pnl),
        "mfe": mfe, "mae": mae, "pnl": pnl, "holds": holds,
        "win_rate": wins / len(pnl),
        "expectancy": sum(pnl) / len(pnl),
        "total_pnl": sum(pnl),
        "mfe_q25": q(mfe, .25), "mfe_q50": q(mfe, .50),
        "mae_q75": q(mae, .75),
        "avg_hold_min": sum(holds) / len(holds),
    }


def score_cfg(cfg: dict, stats: dict) -> float:
    if stats["n"] < MIN_SIGNALS:
        return -1e18
    wr = stats["win_rate"]
    if not (0.30 <= wr <= 0.85):                  # degenerate direction bias
        return -1e17
    exp = stats["expectancy"]
    if exp <= 0:
        return -1e16
    return exp * math.sqrt(stats["n"]) / max(1.0, stats["avg_hold_min"] / 60.0)


# ---------------------------------------------------------------- think -----
def detect_regime(rows: list[dict]) -> dict:
    mids = [r["mid"] for r in rows if r["mid"] is not None]
    ofis = [r["ofi"] for r in rows if r["ofi"] is not None]
    cvds = [r["cvd"] for r in rows if r["cvd"] is not None]
    h = hurst_rs(log_rets(mids[-40000:])) if len(mids) > 2000 else None
    reg = "chop"
    if h is not None:
        if h >= 0.55:
            reg = "trend"
        elif h <= 0.45:
            reg = "revert"
    do = [ofis[i] - ofis[i - 1] for i in range(1, len(ofis))][-40000:]
    dc = [abs(cvds[i] - cvds[i - 1]) for i in range(1, len(cvds))][-40000:]
    return {
        "h": h,
        "ofi_ac1": autocorr(do, 1) if len(do) > 40 else None,
        "vol_cluster": autocorr(dc, 1) if len(dc) > 40 else None,
        "regime": reg,
        "n_mid": len(mids),
    }


def eval_grid(rows: list[dict]) -> list[dict]:
    out = []
    for es in GRID_EPOCH:
        for ma in GRID_MINABS:
            for vm in GRID_VENUE:
                cfg = {"epoch_sec": es, "min_abs": ma, "venue_min": vm}
                stats = replay(rows, es, ma, vm)
                out.append({"cfg": cfg, "stats": stats, "score": score_cfg(cfg, stats)})
    out.sort(key=lambda t: -t["score"])
    return out


def think_cmd(args) -> int:
    if not args.no_collect:
        collect_cmd(argparse.Namespace(dir=args.dir))
    rows = load_dataset(THINK_ROWS)
    # keep only time-continuous dense runs: sparse reconnect-era rows would
    # stretch pseudo-"epochs" over hours and fake MFE/MAE = 0
    dense = []
    prev = None
    for r in rows:
        if prev is None or (r["ts_ms"] - prev) <= DENSE_MS:
            dense.append(r)
        prev = r["ts_ms"]
    rows = dense
    # keep ONLY the most recent contiguous run (rewind from the end while the
    # gap stays <= DENSE_MS): stale reconnect-era rows must not pollute the
    # epoch/excursion statistics of the live session
    run = []
    for r in reversed(rows):
        if run and (run[-1]["ts_ms"] - r["ts_ms"]) > DENSE_MS:
            break
        run.append(r)
    run.reverse()
    rows = run
    if len(rows) < 500:
        print(f"think: only {len(rows)} dataset rows - keep collecting (need >=500)")
        print("  data flows every ~2s at ~22 rows/min; one hour of live data is enough")
        return 1

    st = load_state()
    AI_DIR.mkdir(parents=True, exist_ok=True)
    st["thinks"] = st.get("thinks", 0) + 1
    think_no = st["thinks"]

    reg = detect_regime(rows)
    st["regimes"].append({"ts": now_iso(), **reg})
    st["regimes"] = st["regimes"][-120:]

    grid = eval_grid(rows)
    valid = [t for t in grid if t["score"] > -1e15]
    total_epochs = sum(t["stats"]["n"] for t in valid)
    st["maturity"]["epochs_seen"] = st["maturity"].get("epochs_seen", 0) + total_epochs
    st["maturity"]["trades_analyzed"] = st["maturity"].get("trades_analyzed", 0) + \
        sum(t["stats"]["n"] for t in grid if t["stats"]["n"])

    for t in valid[:12]:
        st["trials"].append({
            "ts": now_iso(), "cfg": t["cfg"],
            "n": t["stats"]["n"], "win_rate": round(t["stats"]["win_rate"], 3),
            "expectancy": round(t["stats"]["expectancy"], 2),
            "score": round(t["score"], 2),
        })
    st["trials"] = st["trials"][-500:]

    best = grid[0] if grid and grid[0]["score"] > -1e15 else None
    cur_cfg = st.get("current", {}) or {}
    cur_stats = replay(rows, cur_cfg.get("epoch_sec", 300),
                       cur_cfg.get("min_abs", 25), cur_cfg.get("venue_min", 0)) \
        if cur_cfg else None
    cur_score = score_cfg(cur_cfg, cur_stats) if cur_stats else -1e15

    decision = "keep"
    notes = []
    if best is not None and (cur_stats is None or cur_stats["n"] < MIN_SIGNALS):
        decision = "adopt"
        notes.append("no robust current config - adopt best candidate")
    elif best is not None and cur_score > -1e15:
        rel = best["score"] / max(abs(cur_score), 1e-9) - 1.0
        if rel >= MIN_IMPROVE and best["stats"]["expectancy"] > max(cur_stats["expectancy"], 0.0):
            decision = "switch"
            notes.append(f"candidate beats current by {rel*100:.0f}% edge")
        else:
            notes.append(f"candidate edge gain {rel*100:.0f}% < {MIN_IMPROVE*100:.0f}% - keep current")
    else:
        notes.append("no viable candidate in grid")

    # --- real-fill guardrail: never tune toward replay fantasy while real fills lose ---
    real = load_deals()
    rn = len(real)
    st["maturity"]["real_fills"] = rn
    if rn >= 10:
        rw = sum(1 for d in real if d["profit"] > 0) / rn
        rex = sum(d["profit"] for d in real) / rn
        holds = []
        ev = epoch_events(rows, 300.0, 25.0)
        for d in real:
            entry = None
            for (t, _s) in ev:
                if t <= d["ts_close"]:
                    entry = t
                else:
                    break
            holds.append(d["ts_close"] - (entry if entry is not None else d["ts_close"] - 300.0))
        rh = sum(holds) / len(holds) if holds else 0.0
        st["maturity"]["real_win_rate"] = round(rw, 3)
        st["maturity"]["real_expectancy"] = round(rex, 2)
        st["maturity"]["real_hold_avg"] = round(rh, 1)
        if rex < 0:
            decision = "keep"
            notes.append("real fills negative - do NOT adopt new config until it recovers")
    else:
        st["maturity"].pop("real_win_rate", None)
        st["maturity"].pop("real_expectancy", None)
        st["maturity"].pop("real_hold_avg", None)
    chosen = best["cfg"] if decision != "keep" and best else cur_cfg
    chosen_stats = best["stats"] if decision != "keep" and best else cur_stats

    ea = {"tp_usd": 0, "sl_usd": 0, "trail_usd": 0, "be_usd": 0}
    if chosen_stats and chosen_stats["n"] >= MIN_SIGNALS:
        ea = {
            "tp_usd": max(1, round(chosen_stats["mfe_q25"])),
            "trail_usd": max(1, round(chosen_stats["mfe_q50"])),
            "be_usd": max(1, round(chosen_stats["mfe_q25"])),
            "sl_usd": max(2, round(chosen_stats["mae_q75"])),
        }

    ep = st["maturity"]["epochs_seen"]
    if ep < 50:
        lvl = "novice"
    elif ep < 200:
        lvl = "learner"
    elif ep < 800:
        lvl = "practitioner"
    else:
        lvl = "expert"
    best_exp = chosen_stats["expectancy"] if chosen_stats and chosen_stats["n"] else 0.0
    st["maturity"]["edge"] = round(max(0.0, min(1.0, best_exp / 120.0)), 3)
    st["maturity"]["level"] = lvl

    rec = {
        "generated": now_iso(),
        "think_no": think_no,
        "regime": reg,
        "maturity": dict(st["maturity"]),
        "decision": decision,
        "bridge": {
            "epoch_sec": int(chosen.get("epoch_sec", 300)) if chosen else 300,
            "min_abs": float(chosen.get("min_abs", 25)) if chosen else 25.0,
            "venue_min": int(chosen.get("venue_min", 0)) if chosen else 0,
        },
        "ea": ea,
        "chosen_stats": {
            "n": chosen_stats["n"] if chosen_stats else 0,
            "win_rate": round(chosen_stats["win_rate"], 3) if chosen_stats else 0.0,
            "expectancy": round(chosen_stats["expectancy"], 2) if chosen_stats else 0.0,
            "mfe_q25": round(chosen_stats["mfe_q25"], 1) if chosen_stats else 0.0,
            "mae_q75": round(chosen_stats["mae_q75"], 1) if chosen_stats else 0.0,
        },
        "notes": notes,
    }
    tmp = RECOMMENDED.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    os.replace(tmp, RECOMMENDED)

    st["current"] = rec["bridge"]
    st["recommendations"].append({"ts": now_iso(), "rec": rec})
    st["recommendations"] = st["recommendations"][-50:]
    st["last_think"] = now_iso()
    save_state(st)

    h = reg.get("h")
    tmsg = (f"think#{think_no} {reg['regime']}(H={(0.0 if h is None else h):.3f} volcl={(0.0 if reg.get('vol_cluster') is None else reg['vol_cluster']):.3f}) "
            "best=" + (f"e{best['cfg']['epoch_sec']}/m{best['cfg']['min_abs']}/v{best['cfg']['venue_min']} " if best else "n/a ") +
            f"exp={(best['stats']['expectancy'] if best else 0.0):.1f} wr={(best['stats']['win_rate'] if best else 0.0):.2f} "
            f"n={(best['stats']['n'] if best else 0)} decision={decision} ea_tp={ea['tp_usd']} sl={ea['sl_usd']}")
    think(tmsg)

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(render_report(rec, best, grid))
    print(render_report(rec, best, grid))
    return 0


def render_report(rec: dict, best: dict | None, grid: list[dict]) -> str:
    reg = rec["regime"]
    h = reg.get("h")
    L = [f"NOBI AI think #{rec['think_no']}  {rec['generated']} UTC", "-" * 60]
    L.append(f"regime   : {reg['regime']}  (Hurst={'%.3f' % h if h is not None else 'n/a'}, "
             f"|dCVD| lag1={(reg.get('vol_cluster') or 0.0):.3f}, OFI lag1={(reg.get('ofi_ac1') or 0.0):.3f}, mids={reg.get('n_mid') or 0})")
    m = rec["maturity"]
    L.append(f"maturity : {m['level']} - epochs analyzed {m['epochs_seen']}, edge {m['edge']:.3f}")
    if m.get("real_fills", 0) >= 10:
        L.append(f"REAL FILLS: n={m['real_fills']} wr={m.get('real_win_rate', 0.0):.3f} "
                 f"exp={m.get('real_expectancy', 0.0):+.2f} USD/trade")
    if best:
        bs = best["stats"]
        bc = best["cfg"]
        L.append("")
        L.append(f"best grid config: epoch={bc['epoch_sec']}s min_abs={bc['min_abs']} venue_min={bc['venue_min']}")
        L.append(f"  signals={bs['n']}  win_rate={bs['win_rate']:.1%}  expectancy={bs['expectancy']:+.1f} "
                 f"USD/epoch  total={bs['total_pnl']:+.0f} USD")
        L.append(f"  MFE p25={bs['mfe_q25']:.0f} p50={bs['mfe_q50']:.0f} USD  "
                 f"MAE p75={bs['mae_q75']:.0f} USD  avg_hold={bs['avg_hold_min']:.0f} min")
    L.append("")
    L.append(f"decision : {rec['decision'].upper()}  " + ("; ".join(rec['notes'])))
    bc = rec["bridge"]
    L.append(f"bridge   : epoch_sec={bc['epoch_sec']}  min_abs={bc['min_abs']}  venue_min={bc['venue_min']}")
    ea = rec["ea"]
    L.append(f"EA (review before use): TP={ea['tp_usd']} USD  trail={ea['trail_usd']}  BE={ea['be_usd']}  "
             f"SL={ea['sl_usd']}  (scale by lots: USD/0.10 lot)")
    L.append("")
    L.append("top-5 candidates:")
    for t in grid[:5]:
        s = t["stats"]
        if s["n"] < MIN_SIGNALS:
            continue
        c = t["cfg"]
        L.append(f"  e{c['epoch_sec']:>3}/m{c['min_abs']:>3}/v{c['venue_min']} "
                 f"exp={s['expectancy']:+.1f} wr={s['win_rate']:.2f} n={s['n']:>3} "
                 f"mfe25={s['mfe_q25']:.0f} mae75={s['mae_q75']:.0f}")
    L.append("")
    L.append("thoughts: ai/thoughts.log | apply bridge: python nobi_ai.py apply --bridge")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- apply -----
def apply_cmd(args) -> int:
    if not RECOMMENDED.exists():
        print("no ai/recommended.json yet - run 'think' first")
        return 1
    rec = json.loads(RECOMMENDED.read_text(encoding="utf-8"))
    bc = rec["bridge"]
    if not BRIDGE_CFG.exists():
        print(f"bridge config not found: {BRIDGE_CFG}")
        return 1
    cur = json.loads(BRIDGE_CFG.read_text(encoding="utf-8"))
    old = {k: cur.get(k) for k in ("epoch_sec", "min_abs", "venue_min")}
    if args.dry_run:
        print(f"would apply: epoch_sec {old.get('epoch_sec')} -> {bc['epoch_sec']}, "
              f"min_abs {old.get('min_abs')} -> {bc['min_abs']}, "
              f"venue_min {old.get('venue_min')} -> {bc['venue_min']}")
        print(f"decision from think#{rec['think_no']}: {rec['decision']} - {rec['notes']}")
        return 0
    if not args.bridge:
        print("use --bridge to write changes into nobi_config.json "
              "(the bridge hot-reloads them; the EA is NOT touched)")
        return 2
    if rec["decision"] == "keep":
        print("brain says keep current settings - no change applied")
        print("  (run 'think' after more data accumulates for a fresh evaluation)")
        return 0

    backup = BRIDGE_CFG.with_suffix(".json.ai-backup")
    backup.write_text(json.dumps(cur, indent=4), encoding="utf-8")
    cur["epoch_sec"] = int(bc["epoch_sec"])
    cur["min_abs"] = float(bc["min_abs"])
    cur["venue_min"] = int(bc["venue_min"])
    tmp = BRIDGE_CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=4), encoding="utf-8")
    os.replace(tmp, BRIDGE_CFG)
    st = load_state()
    st["ledger"].append({
        "ts": now_iso(), "kind": "apply", "from": old, "to": rec["bridge"],
        "reason": rec["notes"], "think_no": rec["think_no"],
    })
    save_state(st)
    think(f"APPLIED bridge settings {old} -> {rec['bridge']} (think#{rec['think_no']})")
    print(f"applied bridge settings: {old} -> {rec['bridge']}")
    print("bridge hot-reloads them automatically (no restart needed)")
    print("backup of previous config: nobi_config.json.ai-backup")
    return 0


# ---------------------------------------------------------------- status ----
def status_cmd(args) -> int:
    st = load_state()
    print(f"NOBI AI brain  |  created {st.get('created')}")
    print(f"collects       : {st.get('collects')}   last {st.get('last_collect')}")
    print(f"thinks         : {st.get('thinks')}   last {st.get('last_think')}")
    print(f"rows ingested  : {st.get('rows')}")
    m = st.get("maturity", {})
    rn = m.get("real_fills", 0)
    if rn >= 10:
        print(f"real fills : n={rn} win_rate={m.get('real_win_rate', 0.0):.3f} "
              f"expectancy={m.get('real_expectancy', 0.0):+.2f} USD/trade")
    else:
        print(f"real fills : n={rn} (need >=10 closed deals for stats)")
    print(f"maturity       : {m.get('level')}  epochs={m.get('epochs_seen')}  edge={m.get('edge')}")
    if st.get("regimes"):
        r = st["regimes"][-1]
        print(f"last regime    : {r.get('regime')}  H={r.get('h')}  volcl={r.get('vol_cluster')}")
    if RECOMMENDED.exists():
        rec = json.loads(RECOMMENDED.read_text(encoding="utf-8"))
        print(f"recommended    : think#{rec.get('think_no')} decision={rec.get('decision')} "
              f"bridge={rec.get('bridge')} ea={rec.get('ea')}")
    if st.get("ledger"):
        print(f"ledger (last)  : {st['ledger'][-1]}")
    print(f"thoughts       : {THOUGHTS}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="NOBI self-improving brain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="append new metrics rows to the dataset")
    p.add_argument("--dir", default=str(DATA_UI))
    p.add_argument("--days", type=float, default=60.0,
                   help="only ingest metrics files written within this many days")
    p.set_defaults(fn=collect_cmd)

    p = sub.add_parser("think", help="replay grid, decide best settings, write thoughts")
    p.add_argument("--dir", default=str(DATA_UI))
    p.add_argument("--no-collect", action="store_true", help="skip the collect step")
    p.set_defaults(fn=think_cmd)

    p = sub.add_parser("apply", help="apply recommended settings")
    p.add_argument("--bridge", action="store_true", help="write into nobi_config.json")
    p.add_argument("--dry-run", action="store_true", help="show what would change")
    p.set_defaults(fn=apply_cmd)

    p = sub.add_parser("status", help="print brain state")
    p.set_defaults(fn=status_cmd)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())