#!/usr/bin/env python3
"""NOBI backtest v2 - replay sweeper hunting configs with PROFIT FACTOR > 2.

Extends nobi_ai.replay() with:
  * profit factor, win rate, expectancy, max drawdown
  * entry/exit management (TP / SL / trailing stop / breakeven) in USD per 1 BTC
  * regime segmentation (trend / chop / revert via Hurst R/S + |dCVD| clustering)
      so consolidation and trend settings can be tuned separately

Usage (from crypto-clob dir):
  python nobi_backtest.py                 # full dataset
  python nobi_backtest.py --rows 120000   # only the LAST N rows (quick scan)
  python nobi_backtest.py --min-pf 2.0    # only print configs above PF target

Output: ai/replay_v2_report.txt + ai/recommended_v2.json
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_DIR = HERE / "ai"
DATASET = AI_DIR / "dataset.csv"
REPORT = AI_DIR / "replay_v2_report.txt"
RECO = AI_DIR / "recommended_v2.json"

GAP_MS = 120_000          # data hole: stops excursion tracking (same as nobi_ai)
MIN_SIGNALS = 8           # min trades for a config to be considered

GRID_EPOCH = [60, 90, 120, 180, 240, 300, 400, 600, 900]
GRID_MINABS = [10, 15, 25, 35, 50, 75, 100, 150, 200]
GRID_VENUE = [0]
GRID_CONF = [0, 1, 2]                   # 0=none, 1=cvd-confirm, 2=cvd+book-imbalance
# exit profiles: (tp_usd, sl_usd, trail_usd, be_usd) per 1 BTC; 0 = disabled
GRID_EXITS = [
    (0, 0, 0, 0),                        # hold to next signal (current EA behavior)
    (40, 60, 20, 15),                    # scalp: quick TP, protective SL
    (20, 40, 10, 10),                    # micro scalp
    (80, 40, 30, 25),                    # wider TP on tight SL
    (40, 120, 30, 25),                   # tight TP, roomy SL
    (120, 60, 60, 40),                   # let winners run + trail
    (200, 100, 100, 60),                 # swing-ish within 10-min epochs
    (240, 490, 390, 240),                # AI candidate (per 1 BTC) - milder
    (480, 980, 780, 480),                # AI #20: TP48/SL98/trail78/BE48 per 0.10 lot
]

# ------------------------------------------------------------- helpers ----
def log_rets(xs):
    out = []
    for i in range(1, len(xs)):
        a, b = xs[i - 1], xs[i]
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def autocorr(xs, lag=1):
    if len(xs) <= lag + 2:
        return None
    m = sum(xs) / len(xs)
    num = 0.0
    den = 0.0
    for i in range(len(xs) - lag):
        num += (xs[i] - m) * (xs[i + lag] - m)
    for x in xs:
        den += (x - m) * (x - m)
    return (num / (len(xs) - lag)) / (den / len(xs)) if den else None


def hurst_rs(xs):
    """Rescaled-range Hurst exponent (multi-scale R/S regression)."""
    n = len(xs)
    if n < 128:
        return None
    pts = []
    m = 16
    while m * 2 <= n:
        for start in range(0, n - m + 1, m):
            seg = xs[start:start + m]
            mean = sum(seg) / m
            dev = [seg[i] - mean for i in range(m)]
            z = []
            acc = 0.0
            for d in dev:
                acc += d
                z.append(acc)
            R = max(z) - min(z)
            S = math.sqrt(sum((d * d) for d in dev) / m)
            if R > 0 and S > 0:
                pts.append((math.log(m), math.log(R / S)))
        m *= 2
    if len(pts) < 6:
        return None
    nn = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    den = nn * sxx - sx * sx
    if den == 0:
        return None
    return (nn * sxy - sx * sy) / den


def regime_label(h):
    if h is None:
        return "chop"
    if h >= 0.55:
        return "trend"
    if h <= 0.45:
        return "revert"
    return "chop"


# ------------------------------------------------------------- loading ----
def load_rows(path: Path, max_rows: int = 0) -> list[dict]:
    """Parse dataset.csv compact rows chronologically; DFS order newest->oldest."""
    raw = []
    with open(path, "r", encoding="utf-8") as fh:
        for ln in fh:
            raw.append(ln.rstrip("\n"))
    if max_rows > 0:
        raw = raw[-max_rows:]
    rows = []
    for ln in raw:
        p = ln.split(",")
        if len(p) < 11:
            continue
        try:
            ts = int(p[0])
            mid = float(p[1]) if p[1] else None
            ofi = float(p[5]) if p[5] else None
            cvd = float(p[6]) if p[6] else None
            n_ven = int(p[8]) if p[8] else 0
            nbi = float(p[4]) if p[4] else 0.0
            aggr = float(p[7]) if p[7] else 0.0
        except ValueError:
            continue
        rows.append({"ts_ms": ts, "mid": mid, "ofi": ofi, "cvd": cvd, "n_ven": n_ven,
                     "nbi": nbi, "aggr": aggr})
    rows.sort(key=lambda r: r["ts_ms"])
    return rows


def label_regimes(rows: list[dict]) -> None:
    """Assign a regime tag to every row (per ~1h block, Hurst + |dCVD| cluster)."""
    BLOCK_MS = 3_600_000
    blocks = {}
    for r in rows:
        blocks.setdefault(r["ts_ms"] // BLOCK_MS, []).append(r)
    for bid, blk in blocks.items():
        mids = [r["mid"] for r in blk if r["mid"] is not None]
        cvds = [r["cvd"] for r in blk if r["cvd"] is not None]
        h = None
        if len(mids) > 128:
            h = hurst_rs(log_rets(mids))
        dc = [abs(cvds[i] - cvds[i - 1]) for i in range(1, len(cvds))]
        vc = autocorr(dc, 1) if len(dc) > 40 else None
        lab = regime_label(h)
        for r in blk:
            r["regime"] = lab
            r["h"] = h
            r["vol_cluster"] = vc


# ------------------------------------------------------------- replay ----
def replay_cfg(rows: list[dict], epoch_sec: float, min_abs: float,
               venue_min: int, tp: float, sl: float, trail: float, be: float,
               cost: float = 0.0, conf: int = 0) -> dict:
    """Epoch-OFI strategy replay with optional TP/SL/trail/BE exits (USD/1 BTC)."""
    baseline = None
    estart = None
    events = []                          # (idx, ts_ms, side)
    for i, r in enumerate(rows):
        v = r["ofi"]
        t = r["ts_ms"]
        if v is None or v != v or not t:
            continue
        if baseline is None:
            baseline = v
            cvd_base = r["cvd"]
            estart = t
            continue
            baseline = v
            estart = t
            continue
        if (t - estart) / 1000.0 < epoch_sec:
            continue
        eof = v - baseline
        cd = (r["cvd"] - cvd_base) if (r["cvd"] is not None and cvd_base is not None) else 0.0
        baseline = v
        cvd_base = r["cvd"]
        estart = t
        if venue_min > 0 and (r["n_ven"] or 0) < venue_min:
            continue
        if eof > min_abs and (conf == 0 or (conf == 1 and cd > 0) or (conf == 2 and cd > 0 and (r["nbi"] or 0) > 0)):
            events.append((i, t, "BUY"))
        elif eof < -min_abs and (conf == 0 or (conf == 1 and cd < 0) or (conf == 2 and cd < 0 and (r["nbi"] or 0) < 0)):
            events.append((i, t, "SELL"))
            events.append((i, t, "SELL"))
    if len(events) < 2:
        return {"n": 0}

    trades = []
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
        exit_code = "HH"                 # HH = next signal, TP, SL, TR (trail), BE stop
        prev_t = seg[0]["ts_ms"]
        stop_z = -sl if sl > 0 else -1e18    # worst-allowed adverse move (USD, z units)
        be_moved = False
        for r in seg:
            if r["ts_ms"] - prev_t > GAP_MS:
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
            if tp > 0 and z >= tp:
                endm = m0 + sign * tp
                exit_code = "TP"
                break
            if trail > 0 and z - trail > stop_z:
                stop_z = z - trail              # trailing stop ratchet (only improves)
            if be > 0 and not be_moved and z >= be:
                be_moved = True
                if 0.0 > stop_z:
                    stop_z = 0.0                # breakeven: stop can't be worse than entry
            if z <= stop_z:
                endm = m0 + sign * stop_z
                if be_moved and stop_z >= 0.0:
                    exit_code = "BE"
                elif trail > 0 and stop_z > -sl:
                    exit_code = "TR"
                else:
                    exit_code = "SL"
                break
            endm = m
        pnl = sign * (endm - m0) - cost
        trades.append({
            "pnl": pnl, "mfe": fav, "mae": -adv,
            "hold_min": (t1 - t0) / 60000.0,
            "regime": seg[0].get("regime", "chop") or "chop",
            "exit": exit_code,
        })
    if not trades:
        return {"n": 0}

    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for t in trades:
        eq += t["pnl"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > mdd:
            mdd = dd
    wins = [t for t in trades if t["pnl"] > 0]
    by_reg = {}
    for reg in ("trend", "chop", "revert"):
        tt = [t for t in trades if t["regime"] == reg]
        if not tt:
            continue
        gpr = sum(t["pnl"] for t in tt if t["pnl"] > 0)
        glr = -sum(t["pnl"] for t in tt if t["pnl"] < 0)
        by_reg[reg] = {
            "n": len(tt),
            "win_rate": sum(1 for t in tt if t["pnl"] > 0) / len(tt),
            "expectancy": sum(t["pnl"] for t in tt) / len(tt),
            "pf": (gpr / glr) if glr > 0 else (99.0 if gpr > 0 else 0.0),
        }
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades),
        "expectancy": sum(t["pnl"] for t in trades) / len(trades),
        "total_pnl": sum(t["pnl"] for t in trades),
        "pf": pf,
        "max_dd": mdd,
        "avg_hold_min": sum(t["hold_min"] for t in trades) / len(trades),
        "exits": {c: sum(1 for t in trades if t["exit"] == c) for c in ("HH", "TP", "SL", "TR", "BE")},
        "by_regime": by_reg,
    }


def score_cfg(st: dict) -> float:
    if st["n"] < MIN_SIGNALS:
        return -1e18
    wr = st["win_rate"]
    if not (0.30 <= wr <= 0.90):
        return -1e17
    if st["expectancy"] <= 0:
        return -1e16
    return st["expectancy"] * math.sqrt(st["n"]) / max(1.0, st["avg_hold_min"] / 60.0)


# ------------------------------------------------------------- main -------
def main() -> int:
    max_rows = 0
    min_pf = 0.0
    cost_usd = 0.0
    skip_last = 0
    min_pf = 0.0
    argv = sys.argv[1:]
    while argv:
        a = argv.pop(0)
        if a == "--rows":
            max_rows = int(argv.pop(0))
        elif a == "--skip":
            skip_last = int(argv.pop(0))
        elif a == "--cost":
            cost_usd = float(argv.pop(0))
        elif a == "--min-pf":
            min_pf = float(argv.pop(0))

    if not DATASET.exists():
        print(f"dataset not found: {DATASET}")
        return 1
    print(f"loading rows from {DATASET} (last {max_rows or 'all'})...")
    rows = load_rows(DATASET, max_rows)
    if skip_last > 0:
        rows = rows[:-skip_last] if len(rows) > skip_last else []
    print(f"{len(rows)} rows loaded")
    if len(rows) < 5000:
        print("not enough data")
        return 1
    label_regimes(rows)
    reg_dist = {}
    for r in rows:
        reg_dist[r["regime"]] = reg_dist.get(r["regime"], 0) + 1
    print("regime row distribution:", reg_dist)

    results = []
    for es in GRID_EPOCH:
        for ma in GRID_MINABS:
            for vm in GRID_VENUE:
                for (tp, sl, trail, be) in GRID_EXITS:
                    for conf in GRID_CONF:
                        cfg = {"epoch_sec": es, "min_abs": ma, "venue_min": vm,
                               "tp_usd": tp, "sl_usd": sl, "trail_usd": trail, "be_usd": be,
                               "conf": conf}
                        st = replay_cfg(rows, float(es), float(ma), vm, tp, sl, trail, be, cost_usd, conf)
                        results.append({"cfg": cfg, "stats": st, "score": score_cfg(st)})
    results.sort(key=lambda t: -t["score"])

    L = []
    L.append("NOBI backtest v2 - replay sweep (profit factor hunt)")
    L.append(f"rows={len(rows)} | regimes={reg_dist}")
    L.append(f"cost model : {cost_usd:.1f} USD round-trip per 1 BTC")
    L.append(f"grid: epochs={GRID_EPOCH} min_abs={GRID_MINABS} exits={GRID_EXITS}")
    L.append("")
    L.append("===== TOP 15 CONFIGS (score) =====")
    for t in results[:15]:
        s = t["stats"]
        if s["n"] < MIN_SIGNALS:
            continue
        pf_tag = " PF>2 !!" if s["pf"] >= min_pf and min_pf > 0 else ""
        L.append(
            f"e{t['cfg']['epoch_sec']:>3}s m{int(t['cfg']['min_abs']):>3} "
            f"tp{int(t['cfg']['tp_usd']):>4} sl{int(t['cfg']['sl_usd']):>4} "
            f"tr{int(t['cfg']['trail_usd']):>4} be{int(t['cfg']['be_usd']):>4} c{t['cfg'].get('conf',0)} | "
            f"n={s['n']:>3} wr={s['win_rate']:.1%} exp={s['expectancy']:+.1f} "
            f"PF={s['pf']:.2f} total={s['total_pnl']:+.0f} mdd={s['max_dd']:.0f} "
            f"hold={s['avg_hold_min']:.1f}m ex={s['exits']}{pf_tag}"
        )
    L.append("")
    L.append("===== BEST PER REGIME (PF>=2 eligible, min 8 trades) =====")
    for reg in ("trend", "chop", "revert"):
        best = None
        for t in results[:60]:
            br = t["stats"].get("by_regime", {}).get(reg)
            if not br or br["n"] < MIN_SIGNALS:
                continue
            if best is None or br["expectancy"] * math.sqrt(br["n"]) > best[0]:
                best = (br["expectancy"] * math.sqrt(br["n"]), t, br)
        if best:
            _, t, br = best
            L.append(
                f"{reg:>6}: e{t['cfg']['epoch_sec']:>3}s m{int(t['cfg']['min_abs']):>3} "
                f"tp{int(t['cfg']['tp_usd']):>4} sl{int(t['cfg']['sl_usd']):>4} "
                f"tr{int(t['cfg']['trail_usd']):>4} be{int(t['cfg']['be_usd']):>4} c{t['cfg'].get('conf',0)} | "
                f"n={br['n']:>3} wr={br['win_rate']:.1%} exp={br['expectancy']:+.1f} "
                f"PF={br['pf']:.2f}"
            )
    L.append("")
    L.append("===== CONFIGS WITH PF >= %.1f (min 8 trades) =====" % max(min_pf, 2.0))
    hits = [t for t in results if t["stats"]["pf"] >= max(min_pf, 2.0) and t["stats"]["n"] >= MIN_SIGNALS]
    if not hits:
        L.append("none - widen the grid or gather more data")
    for t in hits[:15]:
        s = t["stats"]
        L.append(
            f"e{t['cfg']['epoch_sec']:>3}s m{int(t['cfg']['min_abs']):>3} "
            f"tp{int(t['cfg']['tp_usd']):>4} sl{int(t['cfg']['sl_usd']):>4} "
            f"tr{int(t['cfg']['trail_usd']):>4} be{int(t['cfg']['be_usd']):>4} c{t['cfg'].get('conf',0)} | "
            f"n={s['n']:>3} wr={s['win_rate']:.1%} exp={s['expectancy']:+.1f} "
            f"PF={s['pf']:.2f} total={s['total_pnl']:+.0f} mdd={s['max_dd']:.0f}"
        )

    txt = "\n".join(L)
    print("\n" + txt)
    REPORT.write_text(txt, encoding="utf-8")

    rec = None
    if hits:
        rec = hits[0]
        reco = {
            "generated": str(Path(__import__("datetime").datetime.now(timezone_utc()).strftime("%Y-%m-%d %H:%M:%S"))),
            "target_pf": max(min_pf, 2.0),
            "cost_usd": cost_usd,
            "best": {
                "cfg": rec["cfg"],
                "stats": {k: (round(v, 4) if isinstance(v, float) else v)
                          for k, v in rec["stats"].items() if k != "by_regime"},
                "by_regime": rec["stats"]["by_regime"],
            },
            "bridge": {
                "epoch_sec": rec["cfg"]["epoch_sec"],
                "min_abs": rec["cfg"]["min_abs"],
                "venue_min": rec["cfg"]["venue_min"],
            },
            "ea_hint": {
                "tp_usd_per_0_10lot": round(rec["cfg"]["tp_usd"] / 10, 1),
                "sl_usd_per_0_10lot": round(rec["cfg"]["sl_usd"] / 10, 1),
                "trail_usd_per_0_10lot": round(rec["cfg"]["trail_usd"] / 10, 1),
                "be_usd_per_0_10lot": round(rec["cfg"]["be_usd"] / 10, 1),
            },
        }
        RECO.write_text(json.dumps(reco, indent=2), encoding="utf-8")
        print(f"\nwrote {REPORT}")
        print(f"wrote {RECO}")
    else:
        RECO.write_text(json.dumps({"generated": str(__import__("datetime").datetime.now(timezone_utc()).strftime("%Y-%m-%d %H:%M:%S")), "cost_usd": cost_usd, "best": {"stats": {"pf": 0.0, "n": 0}}}, indent=2), encoding="utf-8")
        print("no config reached the PF target yet - cleared recommended_v2.json")
        return 0


def timezone_utc():
    from datetime import timezone
    return timezone.utc


if __name__ == "__main__":
    sys.exit(main())