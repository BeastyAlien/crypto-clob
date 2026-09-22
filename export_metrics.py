# -*- coding: utf-8 -*-
"""export_metrics.py - snapshot crypto-clob metrics for offline evaluation.

Downloads the newest metrics_*.jsonl (or a given dir) into two consumption
files under <out> (default <crypto-clob>/exports):

    metrics_snapshot_<ts>.jsonl   raw rows (last --rows)
    metrics_<ts>.csv              compact columns for Excel/analysis

With --analyze it also runs a first-pass, dependency-free study on the
snapshot and writes analysis_report_<ts>.txt:

  * feed health        - venues present, rows/min, warmup nulls
  * fractal regime     - Hurst exponent (R/S) of mid-price increments,
                         autocorrelation decay of increments and of
                         ofi_total / cvd_total
  * vol clustering     - |delta cvd| autocorrelation (vol-of-vol)
  * epoch excursion    - replays the bridge epoch rule (epoch_sec, min_abs
                         from nobi_config.json) over the snapshot and
                         measures the favorable vs adverse mid move in the
                         FOLLOWING epoch: p10/p25/median MFE and MAE.
                         That distribution is the honest basis for setting
                         TP / trailing / SL.

Read-only: never trades, never writes outside <out>.

Usage:
    python export_metrics.py
    python export_metrics.py --rows 20000
    python export_metrics.py --analyze
    python export_metrics.py --dir <folder-with-metrics-jsonl> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "crypto-clob-ui" / "data-ui"


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def newest_metrics(metrics_dir: Path) -> Path:
    files = [f for f in metrics_dir.glob("metrics_*.jsonl") if f.is_file()]
    nonempty = [f for f in files if f.stat().st_size > 0]
    pool = nonempty or files
    return max(pool, key=lambda f: f.stat().st_mtime)


def load_rows(path: Path, n: int) -> list:
    rows = []
    # read from the end in chunks to keep memory bounded
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        tail = b""
        size = fh.tell()
        chunk = 1 << 20
        pos = size
        while pos > 0 and len(rows) < n:
            pos = max(0, pos - chunk)
            fh.seek(pos)
            data = fh.read(min(chunk, size - pos))
            buf = data + tail
            # take complete lines only
            lines = buf.split(b"\n")
            tail = lines[0]
            for ln in reversed(lines[1:]):
                if len(rows) >= n:
                    break
                s = ln.decode("utf-8", "replace").strip()
                if not s:
                    continue
                try:
                    rows.append(json.loads(s))
                except json.JSONDecodeError:
                    continue
    rows.reverse()
    return rows


def mid_of(r) -> float | None:
    """Mid price in USD from the actual engine schema (bucket = price level)."""
    bid = r.get("best_bid_bucket")
    ask = r.get("best_ask_bucket")
    if not bid or not ask or not bid == bid or not ask == ask:
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def n_venues_of(r) -> int:
    """Active cross-venue agreement count from cvd_venue / cvd_window_venue."""
    n = 0
    for key in ("cvd_venue", "cvd_window_venue"):
        v = r.get(key) or {}
        n = max(n, sum(1 for k, x in v.items() if abs(x or 0) > 1e-9))
    return n


def write_csv(rows, csv_path: Path) -> int:
    cols = ["ts_ms", "bid", "ask", "mid", "depth_w_bid", "depth_w_ask",
            "noBi_n_10", "ofi_total", "cvd_total", "aggression_ratio",
            "n_venues", "sweeps", "iceberg", "spoof", "absorption"]
    seen = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            m = mid_of(r)
            w.writerow([
                r.get("ts_ms"),
                r.get("best_bid_bucket") or 0.0,
                r.get("best_ask_bucket") or 0.0,
                m or 0.0,
                r.get("depth_w_bid") or 0.0,
                r.get("depth_w_ask") or 0.0,
                r.get("noBi_n_10"), r.get("ofi_total"), r.get("cvd_total"),
                r.get("aggression_ratio"), n_venues_of(r),
                r.get("sweeps") or 0, r.get("iceberg_flags") or 0,
                r.get("spoof_flags") or 0, r.get("absorption_flags") or 0,
            ])
            seen += 1
    return seen


# --- analysis primitives (stdlib only) -----------------------------------
def log_rets(xs):
    out = []
    prev = None
    for x in xs:
        if x is None or x != x or x <= 0:
            continue
        if prev is not None and prev > 0:
            out.append(math.log(x / prev))
        prev = x
    return out


def autocorr(xs, k):
    if len(xs) < k + 4:
        return 0.0
    m = sum(xs) / len(xs)
    num, d1, d2 = 0.0, 0.0, 0.0
    for i in range(len(xs) - k):
        a, b = xs[i] - m, xs[i + k] - m
        num += a * b
        d1 += a * a
        d2 += b * b
    if d1 * d2 <= 0:
        return 0.0
    return num / math.sqrt(d1 * d2)


def hurst_rs(xs):
    """Rescaled-range (R/S) Hurst on a series of increments."""
    n = len(xs)
    if n < 64:
        return None
    blocks = []
    size = 16
    while size * 2 <= n:
        blocks.append(size)
        size *= 2
    if len(blocks) < 3:
        return None
    logn = []
    logrs = []
    for b in blocks:
        rs_vals = []
        for start in range(0, n - b + 1, b):
            seg = xs[start:start + b]
            m = sum(seg) / len(seg)
            dev = [seg[i] - m for i in range(len(seg))]
            cum = []
            s = 0.0
            for d in dev:
                s += d
                cum.append(s)
            rng = max(cum) - min(cum)
            var = sum(d * d for d in dev) / len(dev)
            if var <= 0 or rng == 0:
                continue
            rs_vals.append(rng / math.sqrt(var))
        if not rs_vals:
            continue
        logn.append(math.log(b))
        logrs.append(math.log(sum(rs_vals) / len(rs_vals)))
    if len(logn) < 3:
        return None
    n_ = len(logn)
    mx, my = sum(logn) / n_, sum(logrs) / n_
    cov = sum((logn[i] - mx) * (logrs[i] - my) for i in range(n_))
    var = sum((logn[i] - mx) ** 2 for i in range(n_))
    if var <= 0:
        return None
    return cov / var


def valid(x):
    return x is not None and x == x


def q(sorted_xs, p):
    xs = sorted(sorted_xs)
    if not xs:
        return 0.0
    k = max(0, min(len(xs) - 1, int(p * len(xs))))
    return xs[k]


def main() -> int:
    ap = argparse.ArgumentParser(description="snapshot + first-pass analysis of crypto-clob metrics")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--file", default="", help="explicit metrics file to analyze (overrides --dir)")
    ap.add_argument("--out", default=str(HERE / "exports"))
    ap.add_argument("--rows", type=int, default=3000)
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args()

    src = Path(args.file) if args.file else newest_metrics(Path(args.dir))
    rows = load_rows(src, args.rows)
    if not rows:
        print("no rows loaded from %s" % src)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = now_tag()
    snap = out / ("metrics_snapshot_%s.jsonl" % tag)
    with open(snap, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    csv_path = out / ("metrics_%s.csv" % tag)
    write_csv(rows, csv_path)

    print("source   : %s" % src)
    print("rows     : %d (from last %d)" % (len(rows), args.rows))
    print("snapshot : %s" % snap)
    print("csv      : %s" % csv_path)

    if not args.analyze:
        return 0

    ts = [r.get("ts_ms") for r in rows if r.get("ts_ms")]
    mid = [m for r in rows for m in [mid_of(r)] if m is not None]
    ofi = [r.get("ofi_total") for r in rows if valid(r.get("ofi_total"))]
    cvw = [r.get("cvd_total") for r in rows if valid(r.get("cvd_total"))]

    L = []
    if ts:
        dt_ms = max(ts) - min(ts)
        L.append("timespan : %.1f min (%.0f min/day unit)" % (dt_ms / 60000.0, dt_ms / 60000.0))
        span_s = (dt_ms / 1000.0)
        L.append("rows/min : %.1f" % (len(rows) / max(span_s / 60.0, 1e-9)))
    # feed health: distinct venues seen
    venues = set()
    for r in rows:
        v = r.get("cvd_venue") or {}
        for k in v:
            if abs(v[k] or 0) > 1e-9:
                venues.add(k)
    L.append("venues with data : %s" % (", ".join(sorted(venues)) or "none"))
    nnull = sum(1 for r in rows if r.get("ofi_total") is None)
    L.append("null ofi rows   : %d / %d" % (nnull, len(rows)))

    rets = log_rets(mid)
    if len(rets) > 64:
        h = hurst_rs(rets)
        L.append("")
        L.append("--- fractal / regime (mid-price increments) ---")
        L.append("Hurst R/S  : %s  (H>0.5 trending/momentum, H<0.5 mean-reverting)" %
                 ("%.3f" % h if h is not None else "n/a (not enough data)"))
        acs = []

        for k in (1, 2, 5, 10, 30):
            a = autocorr(rets, k)
            acs.append("lag%02d=%.3f" % (k, a))
        L.append("autocorr   : " + "  ".join(acs))
        if h is not None:
            if h > 0.55:
                L.append("=> trending regime: holds benefit from a trailing exit")
            elif h < 0.45:
                L.append("=> mean-reverting regime: take profit early")
            else:
                L.append("=> ~random walk on this scale: exits should be tight")

    if len(ofi) > 64:
        do = [ofi[i] - ofi[i - 1] for i in range(1, len(ofi))]
        L.append("")
        L.append("--- order-flow persistence (ofi_total) ---")
        L.append("ofi autocorr(lag1)=%.3f lag5=%.3f lag30=%.3f" %
                 (autocorr(do, 1), autocorr(do, 5), autocorr(do, 30)))
        L.append("ofi |step| mean=%.3f  max=%.3f  (BTC/tick)" %
                 (sum(abs(x) for x in do) / len(do), max(abs(x) for x in do)))

    if len(cvw) > 64:
        dc = [abs(cvw[i] - cvw[i - 1]) for i in range(1, len(cvw))]
        L.append("")
        L.append("--- vol clustering (|dcvd|) ---")
        L.append("|dcvd| autocorr lag1=%.3f lag5=%.3f (clustering if high)" %
                 (autocorr(dc, 1), autocorr(dc, 5)))

    # epoch excursion study: replay bridge rule, measure next-epoch mid move
    cfg_path = HERE / "nobi_config.json"
    epoch = 300.0
    min_abs = 0.0
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            epoch = float(cfg.get("epoch_sec", 300.0))
            min_abs = float(cfg.get("min_abs", 0.0))
        except Exception:
            pass
    signals = []
    baseline = None
    start_ts = None
    for r in rows:
        v = r.get("ofi_total")
        t = r.get("ts_ms")
        if v is None or not t:
            continue
        if baseline is None:
            baseline = v
            start_ts = t
            continue
        if (t - start_ts) / 1000.0 < epoch:
            continue
        e = v - baseline
        baseline = v
        start_ts = t
        if e > min_abs:
            signals.append((t, "BUY", e))
        elif e < -min_abs:
            signals.append((t, "SELL", e))
        else:
            signals.append((t, "HOLD", e))
    if len(signals) >= 2:
        mfe, mae = [], []
        for i in range(len(signals) - 1):
            t, side, e = signals[i]
            t_end = signals[i + 1][0]
            seg = [r for r in rows if r.get("ts_ms") is not None and t <= r.get("ts_ms") <= t_end]
            seg = [r for r in seg if mid_of(r) is not None]
            if len(seg) < 3:
                continue
            m0 = mid_of(seg[0])
            sign = 1.0 if side == "BUY" else -1.0
            favm = 0.0
            adm = 0.0
            for r in seg:
                m = mid_of(r)
                z = sign * (m - m0)
                if z > favm:
                    favm = z
                if z < adm:
                    adm = z
            mfe.append(favm)
            mae.append(-adm)
        if mfe:
            L.append("")
            L.append("--- epoch exit study (signal -> next signal) ---")
            L.append("epoch=%.0fs min_abs=%.1f | signals=%d" % (epoch, min_abs, len(signals)))
            L.append("MFE p10=%.0f p25=%.0f med=%.0f  (favorable excursion USD)" %
                     (q(mfe, .10), q(mfe, .25), q(mfe, .50)))
            L.append("MAE p25=%.0f p50=%.0f p75=%.0f  (worst excursion USD)" %
                     (q(mae, .25), q(mae, .50), q(mae, .75)))
            L.append("-> TP idea  : p25 MFE (%.0f USD) as first target; trail after." % q(mfe, .25))
            L.append("-> SL idea  : p75 MAE (%.0f USD) as hard floor (SL=0 today = runaway risk)." % q(mae, .75))

    report = out / ("analysis_report_%s.txt" % tag)
    report.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("-" * 66)
    print("\n".join(L))
    print("\nreport   : %s" % report)
    return 0


if __name__ == "__main__":
    sys.exit(main())