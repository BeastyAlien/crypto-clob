# -*- coding: utf-8 -*-
"""analyze_edge.py - cross-reference DataBento CME MBT tape with NOBI dataset.

Flat-array, step-sampled implementation for speed (stdlib only).

Output: data_bento/edge_stats.json + printed summary.
"""
from __future__ import annotations

import datetime
import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
OUT = HERE / "edge_stats.json"

PX = 1e9
LOT = 0.1
TAU = {5: 300, 10: 600, 15: 900, 30: 1800, 60: 3600}
EPOCHS = [180, 300, 600]
TH_Q = [0.70, 0.80, 0.85, 0.90, 0.95]
EXITS = {
    "tp35_sl130": (35, 9999, 130),
    "tp50_sl130": (50, 9999, 130),
    "tp75_sl130": (75, 9999, 130),
    "tp35_sl100": (35, 9999, 100),
    "tp50_sl100": (50, 9999, 100),
    "tp35_sl160": (35, 9999, 160),
    "tp35_trail50_sl130": (35, 50, 130),
    "tp50_trail50_sl130": (50, 50, 130),
}
TRAIL_START = 50
TRAIL_DIST = 50
STEP = 10  # sample path every 10s for MFE/MAE/exit sims


def load_bbo1s():
    """Return (secs, mids, ofis, s2i)."""
    raw = {}
    for f in sorted(RAW.glob("bbo-1s_MBTU6_*.csv")):
        with open(f, "r", encoding="utf-8") as fh:
            hdr = fh.readline().rstrip("\n").split(",")
            idx = {c: i for i, c in enumerate(hdr)}
            for ln in fh:
                p = ln.rstrip("\n").split(",")
                if len(p) < len(hdr):
                    continue
                try:
                    ts = int(p[idx["ts_event"]]) // 1_000_000_000
                    bid = int(p[idx["bid_px_00"]]) or 0
                    ask = int(p[idx["ask_px_00"]]) or 0
                    bsz = int(p[idx["bid_sz_00"]]) or 0
                    asz = int(p[idx["ask_sz_00"]]) or 0
                except (ValueError, IndexError, KeyError):
                    continue
                if bid > 0 and ask > 0:
                    raw[ts] = (bid, ask, bsz, asz)
    if not raw:
        return [], [], [], {}
    lo, hi = min(raw), max(raw)
    secs, mids, ofis = [], [], []
    last = None
    prev_sz = None
    for s in range(lo, hi + 1):
        last = raw.get(s, last)
        if last is None:
            continue
        bid, ask, bsz, asz = last
        mid = (bid + ask) / 2.0 / PX
        if prev_sz is None:
            ofi = 0.0
        else:
            ofi = float((bsz - prev_sz[0]) - (asz - prev_sz[1]))
        prev_sz = (bsz, asz)
        secs.append(s)
        mids.append(mid)
        ofis.append(ofi)
    s2i = {s: i for i, s in enumerate(secs)}
    return secs, mids, ofis, s2i


def load_dataset(ds_path: Path):
    secs, mids = [], []
    with open(ds_path, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            p = ln.split(",")
            try:
                m = float(p[1]) if p[1] else None
            except (ValueError, IndexError):
                continue
            if m is not None and m > 0:
                secs.append(int(float(p[0])) // 1000)
                mids.append(m)
    return secs, mids


def load_ohlcv_min():
    ts, o, h, l, c, v = [], [], [], [], [], []
    for f in sorted(RAW.glob("ohlcv-1m_MBTU6_*.csv")):
        with open(f, "r", encoding="utf-8") as fh:
            fh.readline()
            for ln in fh:
                p = ln.rstrip("\n").split(",")
                if len(p) < 6:
                    continue
                try:
                    ts.append(int(p[0]) // 1_000_000_000)
                    o.append(int(p[3]) / PX)
                    h.append(int(p[4]) / PX)
                    l.append(int(p[5]) / PX)
                    c.append(int(p[6]) / PX)
                    v.append(int(p[7]))
                except (ValueError, IndexError):
                    continue
    order = sorted(range(len(ts)), key=lambda i: ts[i])
    return ([ts[i] for i in order], [o[i] for i in order], [h[i] for i in order],
            [l[i] for i in order], [c[i] for i in order], [v[i] for i in order])


def quantile(xs, q: float):
    if not xs:
        return 0.0
    sxs = sorted(xs)
    k = max(0, min(len(sxs) - 1, int(q * len(sxs))))
    return sxs[k]


def corss(xs, ys):
    n = len(xs)
    if n < 4:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = d1 = d2 = 0.0
    for a, b in zip(xs, ys):
        ax, by = a - mx, b - my
        num += ax * by
        d1 += ax * ax
        d2 += by * by
    return num / math.sqrt(d1 * d2) if d1 * d2 > 0 else 0.0


def correlate(ds_secs, ds_mids, secs, mids, s2i):
    if not s2i:
        return {"n": 0}
    cset = set(ds_secs) & set(s2i)
    if len(cset) < 60:
        return {"n": len(cset), "note": "overlap too small"}
    common = sorted(cset)
    mbt = [mids[s2i[s]] for s in common]
    dsv = []
    mset = set(common)
    for i, s in enumerate(ds_secs):
        if s in mset:
            dsv.append(ds_mids[i])

    def lr(x):
        out = []
        for i in range(1, len(x)):
            a, b = x[i - 1], x[i]
            if a > 0 and b > 0:
                out.append(math.log(b / a))
        return out

    r1 = corss(lr(mbt), lr(dsv))
    r30 = corss(lr(mbt[::30]), lr(dsv[::30])) if len(mbt) > 60 else 0.0
    basis = [mbt[i] - dsv[i] for i in range(len(mbt))]
    return {"n": len(common),
            "corr_1s_logret": round(r1, 3),
            "corr_30s_logret": round(r30, 3),
            "basis_mean_usd": round(statistics.mean(basis), 2),
            "basis_p50_usd": round(statistics.median(basis), 2),
            "basis_std_usd": round(statistics.pstdev(basis), 2)}


def epoch_events_idx(secs, ofis, epoch_sec, thresh):
    ev, base, e0, eidx = [], None, None, 0
    for i in range(len(secs)):
        if base is None:
            base, e0, eidx = ofis[i], secs[i], i
            continue
        if secs[i] - e0 < epoch_sec:
            continue
        base, e0, eidx = ofis[i], secs[i], i
        if base > thresh:
            ev.append((eidx, 1, base))
        elif base < -thresh:
            ev.append((eidx, -1, base))
    return ev


def path_metrics_i(mids, i, side, n):
    p0 = mids[i]
    mfe = mae = 0.0
    j = i
    while j < n:
        dp = (mids[j] - p0) * side * LOT
        if dp > mfe:
            mfe = dp
        if dp < mae:
            mae = dp
        j += STEP
    ret = {}
    for name, h in TAU.items():
        j = i + h
        if j < n:
            ret[name] = round((mids[j] - p0) * side * LOT, 2)
        else:
            ret[name] = None
    return {"mfe": round(mfe, 2), "mae": round(mae, 2), "ret": ret}


def simulate_exit_i(mids, i, side, tp, trail, sl, n):
    p0 = mids[i]
    best = 0.0
    j = i + STEP
    while j < n:
        dp = (mids[j] - p0) * side * LOT
        if dp > best:
            best = dp
        if trail < 9999 and best >= TRAIL_START:
            if dp < best - TRAIL_DIST:
                return round(dp, 2)
        if dp >= tp:
            return round(tp, 2)
        if dp <= -sl:
            return round(-sl, 2)
        j += STEP
    return None


def main():
    print("loading bbo-1s ...", flush=True)
    secs, mids, ofis, s2i = load_bbo1s()
    n = len(secs)
    print(f"  seconds {n} span {secs[0]} -> {secs[-1]}", flush=True)

    print("loading dataset ...", flush=True)
    ds_secs, ds_mids = load_dataset(HERE.parent / "ai" / "dataset.csv")
    print(f"  dataset {len(ds_secs)} rows span {ds_secs[0]} -> {ds_secs[-1]}", flush=True)

    corr = correlate(ds_secs, ds_mids, secs, mids, s2i)
    print("PROXY VALIDATION:", json.dumps(corr), flush=True)

    print("loading ohlcv-1m ...", flush=True)
    tsb, ob, hb, lb, cb, vb = load_ohlcv_min()
    print(f"  bars {len(cb)} span {tsb[0]} -> {tsb[-1]}", flush=True)

    # ---- trend persistence / continuation on minute bars ----
    rets = []
    for i in range(1, len(cb)):
        a, b = cb[i - 1], cb[i]
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    lagval = {}
    for lag in [1, 2, 3, 5, 10, 20, 30, 60]:
        lagval[lag] = round(corss(rets[lag:], rets[:-lag]), 4)
    cont = {}
    for k in [1, 3, 5, 10, 30, 60]:
        same = tot = 0
        for i in range(k, len(rets) - k):
            a = sum(rets[i - k:i])
            b = sum(rets[i:i + k])
            if abs(a) > 1e-12 and abs(b) > 1e-12:
                tot += 1
                if (a > 0) == (b > 0):
                    same += 1
        cont[k] = round(same / tot, 3) if tot else None
    hod = {}
    for i in range(1, len(rets)):
        hh = datetime.datetime.fromtimestamp(tsb[i], datetime.timezone.utc).hour
        hod.setdefault(hh, []).append(abs(rets[i]))
    hod_prof = {str(h): round(statistics.median(v) * 100, 4) for h, v in sorted(hod.items())}
    print("AUTOCORR(1m):", lagval, flush=True)
    print("CONTINUATION:", cont, flush=True)
    print("HOD median|r1|%:", {k: v for k, v in list(hod_prof.items())[:6]},
          "mid", {k: v for k, v in list(hod_prof.items())[9:15]},
          "tail", {k: v for k, v in list(hod_prof.items())[-3:]}, flush=True)

    # ---- epoch signal edge ----
    summary = {"proxy": corr, "autocorr_1m": lagval, "continuation": cont,
               "hod_median_abs_ret_pct": hod_prof}
    table = {}
    for ep in EPOCHS:
        cum = []
        base = e0 = None
        for i in range(n):
            if base is None:
                base, e0 = ofis[i], secs[i]
                continue
            if secs[i] - e0 < ep:
                continue
            cum.append(base)
            base, e0 = ofis[i], secs[i]
        abs_cum = [abs(c) for c in cum]
        for tq in TH_Q:
            th = quantile(abs_cum, tq) if abs_cum else 0.0
            if th <= 0:
                continue
            ev = epoch_events_idx(secs, ofis, ep, th)
            pms = [path_metrics_i(mids, e[0], e[1], n) for e in ev]
            pms = [p for p in pms if p["ret"]["15"] is not None and p["ret"]["30"] is not None]
            ex = {}
            for name, (tp, trail, sl) in EXITS.items():
                pnls = [simulate_exit_i(mids, e[0], e[1], tp, trail, sl, n) for e in ev]
                pnls = [x for x in pnls if x is not None]
                if len(pnls) >= 4:
                    ex[name] = {"n": len(pnls),
                                "wr": round(sum(1 for x in pnls if x > 0) / len(pnls), 3),
                                "exp": round(statistics.mean(pnls), 2),
                                "total": round(sum(pnls), 2)}
            if pms:
                table.setdefault(str(ep), {})[str(tq)] = {
                    "thresh_cum": round(th, 2),
                    "n_events": len(ev),
                    "mfe_p25": round(quantile([p["mfe"] for p in pms], 0.25), 2),
                    "mfe_p50": round(quantile([p["mfe"] for p in pms], 0.50), 2),
                    "mfe_p75": round(quantile([p["mfe"] for p in pms], 0.75), 2),
                    "mae_p25": round(quantile([p["mae"] for p in pms], 0.25), 2),
                    "mae_p50": round(quantile([p["mae"] for p in pms], 0.50), 2),
                    "mae_p75": round(quantile([p["mae"] for p in pms], 0.75), 2),
                    "ret5_p50": round(quantile([p["ret"]["5"] for p in pms], 0.50), 2),
                    "ret15_p50": round(quantile([p["ret"]["15"] for p in pms], 0.50), 2),
                    "ret30_p50": round(quantile([p["ret"]["30"] for p in pms], 0.50), 2),
                    "exits": ex,
                }
            print(f"ep={ep} q={tq} done", flush=True)
    summary["epoch_grid"] = table

    # hour-of-day for the reference config (300s epoch, q0.90)
    th = quantile([abs(c) for c in cum], 0.90)
    ev = epoch_events_idx(secs, ofis, 300, th)
    hods = {}
    for e in ev:
        hh = datetime.datetime.fromtimestamp(secs[e[0]], datetime.timezone.utc).hour
        p = path_metrics_i(mids, e[0], e[1], n)
        if p["ret"]["15"] is not None:
            hods.setdefault(hh, []).append(p["ret"]["15"])
    hod_ret = {}
    for h, xs in sorted(hods.items()):
        if len(xs) >= 4:
            hod_ret[str(h)] = {"n": len(xs), "med15m": round(statistics.median(xs), 2),
                               "wr15m": round(sum(1 for x in xs if x > 0) / len(xs), 3)}
    summary["hod_events_300s_q90"] = hod_ret
    print("HOD EVENTS 300s/q90:", json.dumps(hod_ret), flush=True)

    OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()