# -*- coding: utf-8 -*-
"""Backtest 2026-09-08 day with an ACTIVITY GATE (per-epoch weighted depth)."""
import json
import os

DATA1 = r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob-ui\data-ui\metrics_20260908_114744.jsonl"
DATA2 = r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob-ui\data-ui\metrics_20260908_211837.jsonl"
EPOCH = 300.0
VOL = 0.10
from datetime import datetime, timezone
CUT = datetime(2026, 9, 9, 0, 0, 0, tzinfo=timezone.utc).timestamp()


def load():
    rows = []
    for path in (DATA1, DATA2):
        f2 = (path == DATA2)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    ts = o.get("ts_ms", 0)
                    ofi = o.get("ofi_total")
                    db = o.get("depth_w_bid")
                    da = o.get("depth_w_ask")
                    bid = o.get("best_bid_bucket")
                    ask = o.get("best_ask_bucket")
                    if not ts or ofi is None or db is None or da is None:
                        continue
                    rows.append((ts / 1000.0, float(ofi), float(db) + float(da),
                                bid, ask, f2))
                except Exception:
                    continue
    rows.sort(key=lambda r: r[0])
    return rows


def simulate(rows, thr, gate):
    pos = None
    entry = 0.0
    pnl = 0.0
    trades = wins = losses = 0
    gw = gl = 0.0
    balance = 1000.0
    peak = 1000.0
    maxdd = 0.0
    sig_total = 0
    base = None
    start = None
    acc = 0.0
    n = 0
    cur = rows[0][5]
    px_cache = {}
    for ts, ofi, dep, bid, ask, f2 in rows:
        if f2 != cur:
            cur = f2
            base = None
            start = None
            acc = 0.0
            n = 0
        if ts >= CUT:
            break
        acc += dep
        n += 1
        if base is None:
            base = ofi
            start = ts
            continue
        eo = ofi - base
        if ts - start < EPOCH:
            continue
        avg = acc / max(n, 1)
        base = ofi
        start = ts
        acc = 0.0
        n = 0
        if eo > thr:
            side = "BUY"
        elif eo < -thr:
            side = "SELL"
        else:
            continue
        sig_total += 1
        if gate and avg < gate:
            continue  # activity gate declines the epoch
        px = px_cache.get(int(ts / 60))
        if px is None:
            for r in rows:
                if r[0] >= ts - 1 and r[3] is not None and r[4] is not None:
                    px = (r[3] + r[4]) / 2.0
                    px_cache[int(ts / 60)] = px
                    break
        if px is None:
            continue
        if pos is None:
            pos = side
            entry = px
            continue
        if pos == side:
            continue
        d = (px - entry) if pos == "BUY" else (entry - px)
        p = d * VOL
        pnl += p
        trades += 1
        if p >= 0:
            wins += 1
            gw += p
        else:
            losses += 1
            gl -= p
        balance += p
        peak = max(peak, balance)
        maxdd = max(maxdd, peak - balance)
        pos = side
        entry = px
    return dict(gate=gate, signals=sig_total, trades=trades, wins=wins,
                losses=losses, pnl=pnl, maxdd=maxdd, gw=gw, gl=gl)


def main():
    rows = load()
    print("rows:", len(rows))
    print("mode=hold (skip epoch when avg depth < gate), min_abs=10")
    print("gate | sig | trd | W/L | net | maxDD | grossW | grossL")
    print("-" * 74)
    for gate in (0.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 45.0):
        r = simulate(rows, 10.0, gate)
        print(f"{gate:5.1f} | {r['signals']:3d} | {r['trades']:3d} | {r['wins']:2d}/{r['losses']:2d} | "
              f"{r['pnl']:+7.2f} | {r['maxdd']:5.2f} | {r['gw']:6.2f} | {r['gl']:6.2f}")


if __name__ == "__main__":
    main()