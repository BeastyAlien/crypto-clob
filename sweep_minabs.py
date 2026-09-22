# -*- coding: utf-8 -*-
"""Sweep min_abs (OFI momentum threshold) 0..200 on the 2026-09-08 recorded day.

Reproduces nobi_bridge's OfiEpochDetector EXACTLY (epoch=300s, dead band
|min_abs|, rebase on metrics-file change), then simulates the EA:
  * no position  -> open on first signal
  * same side    -> hold
  * opposite side-> close at current price, realize P&L, re-open opposite
Volume 0.10, contract 1.0 ->  $/trade = (price move) * 0.10
Fill price = (best_bid_bucket + best_ask_bucket)/2 of the signal row (whole-$).

Output: one row per threshold with signals, trades, W/L, net P&L, gross W/L,
max drawdown (USD on $1000 deposit) and final open position note.
"""
import os
from datetime import datetime, timezone

DATA1 = r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob-ui\data-ui\metrics_20260908_114744.jsonl"
DATA2 = r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob-ui\data-ui\metrics_20260908_211837.jsonl"
CUT_UTC = datetime(2026, 9, 8, 23, 0, 0, tzinfo=timezone.utc)
EPOCH = 300.0
VOL = 0.10


def extract(path, tag):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            i = line.find('"ts_ms"')
            if i < 0:
                continue
            j = line.find(":", i)
            k = line.find(",", j)
            try:
                ts = int(float(line[j + 1:k]))
            except (ValueError, TypeError):
                continue
            if not ts:
                continue
            vals = {}
            ok = True
            for key in ("ofi_total", "best_bid_bucket", "best_ask_bucket"):
                a = line.find('"' + key + '"')
                if a < 0:
                    vals[key] = None
                    continue
                b = line.find(":", a)
                c = line.find(",", b)
                try:
                    vals[key] = float(line[b + 1:c])
                except (ValueError, TypeError):
                    vals[key] = None
                if key == "ofi_total" and vals[key] is None:
                    ok = False
            if not ok:
                continue
            rows.append((ts, vals["ofi_total"], vals["best_bid_bucket"], vals["best_ask_bucket"], tag))
    return rows


def detector(rows, thr):
    """Return list of (ts_s, side, row_idx) — mirror of OfiEpochDetector.feed."""
    sig = []
    baseline = None
    epo_start = None
    last_epoch_val = None
    cur_file = None
    for idx, (ts, ofi, bid, ask, tag) in enumerate(rows):
        if tag != cur_file:
            cur_file = tag
            baseline = None
            epo_start = None
            last_epoch_val = None
        if ofi is None:
            continue
        ts_s = ts / 1000.0
        if baseline is None:
            baseline = ofi
            epo_start = ts_s
            continue
        eo = ofi - baseline
        if ts_s - epo_start < EPOCH:
            continue
        last_epoch_val = eo
        baseline = ofi
        epo_start = ts_s
        if eo > thr:
            side = "BUY"
        elif eo < -thr:
            side = "SELL"
        else:
            continue
        utc = datetime.fromtimestamp(ts_s, timezone.utc)
        if utc >= CUT_UTC:
            break
        sig.append((ts_s, side, idx))
    return sig


def simulate(rows, thr):
    sig = detector(rows, thr)
    pos = None
    entry = 0.0
    pnl = 0.0
    trades = wins = losses = 0
    gw = gl = 0.0
    balance = 1000.0
    peak = 1000.0
    maxdd = 0.0
    for ts_s, side, idx in sig:
        bid, ask = rows[idx][2], rows[idx][3]
        if bid is None or ask is None:
            continue
        px = (bid + ask) / 2.0
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
    # mark final position to the last available price
    open_end = 0.0
    if pos is not None:
        for idx in range(len(rows) - 1, -1, -1):
            bid, ask = rows[idx][2], rows[idx][3]
            if bid is not None and ask is not None:
                open_end = ((bid + ask) / 2.0 - entry) * VOL * (1 if pos == "BUY" else -1)
                break
    return dict(thr=thr, signals=len(sig), trades=trades, wins=wins, losses=losses,
               pnl=pnl, gw=gw, gl=gl, maxdd=maxdd, open_end=open_end,
               final=pnl + open_end)


def main():
    print("loading metrics ...")
    rows = extract(DATA1, 1) + extract(DATA2, 2)
    print("rows: %d  (file1 %d / file2 %d)" % (len(rows),
          sum(1 for r in rows if r[4] == 1), sum(1 for r in rows if r[4] == 2)))
    thrs = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100,
            120, 140, 160, 180, 200]
    print("\nmin_abs | sig | trades | W/L | netPNL | final(+open) | maxDD | grossW | grossL")
    print("-" * 92)
    results = []
    for thr in thrs:
        r = simulate(rows, thr)
        results.append(r)
        print(f"{thr:6d} | {r['signals']:3d} | {r['trades']:4d} | {r['wins']:2d}/{r['losses']:2d} | "
              f"{r['pnl']:+8.2f} | {r['final']:+9.2f} | {r['maxdd']:6.2f} | {r['gw']:7.2f} | {r['gl']:6.2f}")
    print("-" * 92)
    best = max(results, key=lambda r: r["final"])
    print("BEST sweep     : min_abs=%-5g final=%+.2f  (%d trades, %dW/%dL)"
          % (best["thr"], best["final"], best["trades"], best["wins"], best["losses"]))
    ok = [r for r in results if r["final"] > 0]
    print("positive days  : %d of %d thresholds" % (len(ok), len(results)))


if __name__ == "__main__":
    main()