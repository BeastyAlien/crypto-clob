"""Order-flow engine: CVD, aggressiveness ratio, market sweeps, iceberg
detection, spoofing/layering flags and absorption detection.

Heuristic research metrics — thresholds are configurable and outputs indicate
statistical patterns in public data, never a guarantee of intent.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from .common import bucket_below, now_ms


class FlowEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tick = float(cfg["tick_size"])
        self.cvd_window = int(cfg["cvd_window_sec"]) * 1000
        self.aggr_window = int(cfg["aggression_window_sec"]) * 1000
        self.cfg_sw = int(cfg["sweep_window_ms"])
        self.sw_venues = int(cfg["sweep_min_venues"])
        self.sw_trades = int(cfg["sweep_min_trades"])
        self.sw_qty = float(cfg["sweep_min_qty"])
        self.ice_ratio = float(cfg["iceberg_ratio"])
        self.ice_look = int(cfg["iceberg_lookback_sec"]) * 1000
        self.spoof_notional = float(cfg["spoof_min_notional_usd"])
        self.spoof_ticks = float(cfg["spoof_min_ticks_from_mid"])
        self.spoof_life = int(cfg["spoof_max_lifetime_ms"])
        self.absorb_move = float(cfg["absorb_max_move_usd"])
        self.absorb_cvd = float(cfg["absorb_min_cvd_qty"])
        self.absorb_win = int(cfg["absorb_window_sec"]) * 1000

        self.cvd: dict[str, float] = defaultdict(float)
        self.cvd_total = 0.0
        self.trades: deque = deque()               # (ts, ex, side, price, qty)
        self.aggr: deque = deque()                 # (ts, buy_qty, sell_qty)
        self.display: dict = {}                    # (ex,side,bucket) -> (max_qty, ts)
        self.exec_slot: dict = {}                  # (ex,side,bucket) -> (qty, ts)
        self.iceberg_flags: deque = deque(maxlen=200)
        self.spoof_orders: dict = defaultdict(dict)  # ex -> {(side,price):(ts,qty)}
        self.spoof_flags: deque = deque(maxlen=200)
        self.sweeps: deque = deque(maxlen=200)
        self.last_sweep_ts = 0
        self.mids: dict = {}                        # ex -> (ts, mid)
        self.mid_hist: dict = defaultdict(lambda: deque(maxlen=800))
        self.absorb_flags: deque = deque(maxlen=200)
        self.last_absorb = {}                       # ex -> ts
        self.events_out: deque = deque(maxlen=10000)  # queued flag events for the writer

    # -- ingest -------------------------------------------------------------
    def on_book_changes(self, ex: str, changes, ts=None, mid=None) -> None:
        ts = ts or now_ms()
        if mid is not None:
            self.mids[ex] = (ts, mid)
            self.mid_hist[ex].append((ts, mid))
        for side, price, old, new in changes:
            if old == new:
                continue
            bucket = bucket_below(price, self.tick)
            # iceberg display tracking
            key = (ex, side, bucket)
            cur, _ = self.display.get(key, (0.0, 0))
            if new > cur:
                self.display[key] = (new, ts)
            # spoofing / layering
            if old <= 0 and new > 0:
                self._spoof_add(ex, side, price, new, ts, mid)
            elif new <= 0 and old > 0:
                flag = self._spoof_cancel(ex, side, price, ts)
                if flag:
                    self.events_out.append(flag)

    def on_trade(self, ex: str, side: str, price: float, qty: float, ts: int | None = None) -> list:
        ts = ts or now_ms()
        side = "buy" if str(side).lower() in ("buy", "b", "1", "true") else "sell"
        self.trades.append((ts, ex, side, price, qty))
        dv = qty if side == "buy" else -qty
        self.cvd[ex] += dv
        self.cvd_total += dv
        self.aggr.append((ts, qty if side == "buy" else 0.0, qty if side == "sell" else 0.0))
        self._prune(ts)

        events = []
        e = self._sweep_check(ts)
        if e:
            events.append(e)
        e = self._iceberg_check(ex, side, price, qty, ts)
        if e:
            events.append(e)
        self._spoof_fill(ex, price, ts)
        e = self._absorb_check(ex, side, qty, ts)
        if e:
            events.append(e)
        self.events_out.extend(events)
        return events

    def _prune(self, ts: int) -> None:
        while self.trades and ts - self.trades[0][0] > self.cvd_window:
            self.trades.popleft()
        while self.aggr and ts - self.aggr[0][0] > self.aggr_window:
            self.aggr.popleft()

    # -- metrics ------------------------------------------------------------
    def cvd_window_value(self, ts: int | None = None) -> dict:
        ts = ts or now_ms()
        win = {}
        for item in self.trades:
            if ts - item[0] <= self.cvd_window:
                win[item[1]] = win.get(item[1], 0.0) + (item[4] if item[2] == "buy" else -item[4])
        return {k: round(v, 4) for k, v in win.items()}

    def aggression_ratio(self) -> float:
        b = sum(x[1] for x in self.aggr)
        s = sum(x[2] for x in self.aggr)
        if b + s <= 0:
            return 0.5
        return round(b / (b + s), 4)

    # -- market sweeps ------------------------------------------------------
    def _sweep_check(self, ts: int) -> dict | None:
        if ts - self.last_sweep_ts < self.cfg_sw:
            return None
        per_venue: dict = {}
        qty_by_side = {"buy": 0.0, "sell": 0.0}
        n_by_side = {"buy": 0, "sell": 0}
        for t in reversed(self.trades):
            if ts - t[0] > self.cfg_sw:
                break
            ex, side, qty = t[1], t[2], t[4]
            per_venue.setdefault(side, {}).setdefault(ex, [0, 0.0])
            per_venue[side][ex][0] += 1
            per_venue[side][ex][1] += qty
            qty_by_side[side] += qty
            n_by_side[side] += 1
        for side in ("buy", "sell"):
            d = per_venue.get(side) or {}
            if len(d) >= self.sw_venues and n_by_side[side] >= self.sw_trades \
                    and qty_by_side[side] >= self.sw_qty:
                self.last_sweep_ts = ts
                return {"event": "sweep", "ts": ts, "side": side,
                        "venues": {k: {"trades": v[0], "qty": round(v[1], 4)} for k, v in d.items()},
                        "total_qty": round(qty_by_side[side], 4)}
        return None

    # -- iceberg ------------------------------------------------------------
    def _iceberg_check(self, ex, side, price, qty, ts) -> dict | None:
        bucket = bucket_below(price, self.tick)
        key = (ex, side, bucket)
        disp, dts = self.display.get(key, (0.0, 0))
        if disp <= 0 or ts - dts > self.ice_look:
            return None
        eq, ets = self.exec_slot.get(key, (0.0, 0))
        if ts - ets > self.ice_look:
            eq = 0.0
        eq += qty
        self.exec_slot[key] = (eq, ts)
        if eq >= self.ice_ratio * disp:
            self.iceberg_flags.append({"ts": ts, "exchange": ex, "side": side,
                                       "bucket": bucket, "exec_qty": round(eq, 4),
                                       "display_max": round(disp, 4)})
            self.exec_slot[key] = (0.0, ts)
            self.display[key] = (0.0, ts)  # re-arm flag until book refreshes
            return {"event": "iceberg", "ts": ts, "exchange": ex, "side": side,
                    "bucket": bucket, "exec_qty": round(eq, 4), "display_max": round(disp, 4)}
        return None

    # -- spoofing / layering ------------------------------------------------
    def _spoof_add(self, ex, side, price, qty, ts, mid) -> None:
        if mid is None or mid <= 0:
            return
        notional = qty * price
        dist_ticks = abs(price - mid) / self.tick
        if notional >= self.spoof_notional and dist_ticks >= self.spoof_ticks:
            self.spoof_orders[ex][(side, price)] = (ts, qty)

    def _spoof_cancel(self, ex, side, price, ts) -> dict | None:
        rec = self.spoof_orders[ex].pop((side, price), None)
        if not rec:
            return None
        ts_add, qty_add = rec
        if ts - ts_add <= self.spoof_life:
            flag = {"event": "spoof", "ts": ts, "exchange": ex, "side": side,
                    "price": price, "qty": round(qty_add, 4),
                    "lifetime_ms": ts - ts_add}
            self.spoof_flags.append(flag)
            return flag
        return None

    def _spoof_fill(self, ex, price, ts) -> None:
        # a trade near the price clears the spoof candidate (it got filled)
        hit = []
        for (side, p), (ta, q) in self.spoof_orders[ex].items():
            if abs(p - price) <= max(self.tick * 2, price * 1e-4):
                hit.append((side, p))
        for k in hit:
            self.spoof_orders[ex].pop(k, None)

    # -- absorption ---------------------------------------------------------
    def _absorb_check(self, ex, side, qty, ts) -> dict | None:
        hist = self.mid_hist.get(ex)
        if not hist or len(hist) < 2:
            return None
        t0, m0 = hist[0]
        t1, m1 = hist[-1]
        if ts - t0 > self.absorb_win:
            hist.clear()
            return None
        move = abs(m1 - m0)
        win_qty = 0.0
        for t in reversed(self.trades):
            if ts - t[0] > self.absorb_win:
                break
            if t[1] == ex:
                win_qty += t[4] if t[2] == "buy" else -t[4]
        if move <= self.absorb_move and abs(win_qty) >= self.absorb_cvd:
            if ts - self.last_absorb.get(ex, 0) > self.absorb_win:
                self.last_absorb[ex] = ts
                flag = {"event": "absorption", "ts": ts, "exchange": ex,
                        "cvd_qty": round(win_qty, 4), "move_usd": round(move, 4)}
                self.absorb_flags.append(flag)
                return flag
        return None