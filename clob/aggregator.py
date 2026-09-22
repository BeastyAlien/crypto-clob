"""Global consolidated L2 book: uniform price bucketing across venues,
incremental bucket aggregates, NOBI (order book imbalance) and a per-bucket
liquidity heatmap with per-venue composition."""

from __future__ import annotations

from collections import defaultdict

from .common import OrderBook, bucket_below, now_ms


class GlobalBook:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tick = float(cfg["tick_size"])
        self.nobi_n = int(cfg["nobi_levels"])
        self.books: dict[str, OrderBook] = {
            ex: OrderBook(ex) for ex in cfg["exchanges"]} if isinstance(cfg["exchanges"], list) else {}
        # bucket aggregates keyed by uniform tick grid
        self.bid_bucket: dict[float, float] = defaultdict(float)  # bucket -> total vol
        self.ask_bucket: dict[float, float] = defaultdict(float)
        self.bid_comp: dict[float, dict[str, float]] = defaultdict(dict)  # bucket -> {ex: vol}
        self.ask_comp: dict[float, dict[str, float]] = defaultdict(dict)
        # Order-flow imbalance (OFI) — touch-region volume delta proxy
        self.ofi_total = 0.0
        self.ofi_ex: dict[str, float] = defaultdict(float)

    # -- updates ------------------------------------------------------------
    def apply_snapshot(self, ex: str, bids, asks, seq, ts=None) -> None:
        bk = self.books[ex]
        for p in list(bk.bids):
            self._sub("bid", ex, p, bk.bids[p])
        for p in list(bk.asks):
            self._sub("ask", ex, p, bk.asks[p])
        bk.apply_snapshot(bids, asks, seq, ts or now_ms())
        for p, v in bk.bids.items():
            self._add("bid", ex, p, v)
        for p, v in bk.asks.items():
            self._add("ask", ex, p, v)
        self.ofi_ex[ex] = 0.0  # restart the OFI baseline on a fresh book

    def _touch_region(self, ex: str, lo_b, hi_a):
        """Visible volume of one venue inside [lo_b, +inf) bids / (-inf, hi_a] asks."""
        bv = sum(c.get(ex, 0.0) for b, c in self.bid_comp.items() if b >= lo_b - 1e-9)
        av = sum(c.get(ex, 0.0) for b, c in self.ask_comp.items() if b <= hi_a + 1e-9)
        return bv, av

    def apply_diff(self, ex: str, bd, ad, seq=None, ts=None, rule=None) -> bool:
        """Returns False when the caller must re-sync this venue."""
        bk = self.books[ex]
        if not bk.has_snapshot:
            return False  # caller buffers until a snapshot anchors the book
        pre_bb, pre_ba = self.bests(ex)
        pre = None
        if pre_bb is not None and pre_ba is not None:
            lo = pre_bb - self.nobi_n * self.tick
            hi = pre_ba + self.nobi_n * self.tick
            b0, a0 = self._touch_region(ex, lo, hi)
            pre = (lo, hi, b0, a0)
        ok = bk.apply_diff(bd, ad, seq, ts or now_ms(), rule)
        if not ok:
            return False
        for side, price, old, new in bk.events:
            if side == "bid":
                if old > 0:
                    self._sub("bid", ex, price, old)
                if new > 0:
                    self._add("bid", ex, price, new)
            else:
                if old > 0:
                    self._sub("ask", ex, price, old)
                if new > 0:
                    self._add("ask", ex, price, new)
        if pre is not None:
            lo, hi, b0, a0 = pre
            b1, a1 = self._touch_region(ex, lo, hi)
            step = (b1 - b0) - (a1 - a0)          # bid refresh minus ask refresh
            self.ofi_ex[ex] += step
            self.ofi_total += step
        return True

    # -- bookkeeping --------------------------------------------------------
    def _add(self, side: str, ex: str, price: float, vol: float) -> None:
        b = bucket_below(price, self.tick)
        if side == "bid":
            self.bid_bucket[b] += vol
            comp = self.bid_comp[b]
            comp[ex] = comp.get(ex, 0.0) + vol
        else:
            self.ask_bucket[b] += vol
            comp = self.ask_comp[b]
            comp[ex] = comp.get(ex, 0.0) + vol

    def _sub(self, side: str, ex: str, price: float, vol: float) -> None:
        b = bucket_below(price, self.tick)
        if side == "bid":
            comp = self.bid_comp[b]
            comp[ex] = comp.get(ex, 0.0) - vol
            if comp[ex] <= 1e-12:
                comp.pop(ex)
            self.bid_bucket[b] -= vol
            if self.bid_bucket[b] <= 1e-12 or not comp:
                self.bid_bucket.pop(b, None)
                self.bid_comp.pop(b, None)
        else:
            comp = self.ask_comp[b]
            comp[ex] = comp.get(ex, 0.0) - vol
            if comp[ex] <= 1e-12:
                comp.pop(ex)
            self.ask_bucket[b] -= vol
            if self.ask_bucket[b] <= 1e-12 or not comp:
                self.ask_bucket.pop(b, None)
                self.ask_comp.pop(b, None)

    # -- queries ------------------------------------------------------------
    def bests(self, ex: str):
        bk = self.books[ex]
        bb = max(bk.bids) if bk.bids else None
        ba = min(bk.asks) if bk.asks else None
        return bb, ba

    def global_best(self):
        bb = max(self.bid_bucket) if self.bid_bucket else None
        ba = min(self.ask_bucket) if self.ask_bucket else None
        return bb, ba

    def noBi(self) -> float | None:
        """Normalized OBI across N tick levels around the global touch."""
        bb, ba = self.global_best()
        if bb is None or ba is None:
            return None
        lo = bb - self.nobi_n * self.tick
        hi = ba + self.nobi_n * self.tick
        bv = sum(v for k, v in self.bid_bucket.items() if k >= lo - 1e-9)
        av = sum(v for k, v in self.ask_bucket.items() if k <= hi + 1e-9)
        if bv + av <= 0:
            return 0.0
        return (bv - av) / (bv + av)

    def depth_sides(self, n: int | None = None):
        """Total visible bid/ask volume within n levels of the global touch."""
        n = int(n or self.nobi_n)
        bb, ba = self.global_best()
        if bb is None or ba is None:
            return 0.0, 0.0
        lo = bb - n * self.tick
        hi = ba + n * self.tick
        bv = sum(v for k, v in self.bid_bucket.items() if k >= lo - 1e-9)
        av = sum(v for k, v in self.ask_bucket.items() if k <= hi + 1e-9)
        return bv, av

    def heatmap(self, rows: int | None = None) -> dict:
        rows = int(rows or self.cfg.get("heatmap_rows", 10))
        bb, ba = self.global_best()
        out = {"bids": [], "asks": []}
        if bb is not None:
            for p in sorted((k for k in self.bid_bucket if k <= bb + 1e-9), reverse=True)[:rows]:
                comp = dict(sorted(self.bid_comp[p].items(), key=lambda kv: -kv[1]))
                out["bids"].append({"bucket": p, "total": round(self.bid_bucket[p], 4),
                                    "venues": {k: round(v, 4) for k, v in comp.items()}})
        if ba is not None:
            for p in sorted((k for k in self.ask_bucket if k >= ba - 1e-9))[:rows]:
                comp = dict(sorted(self.ask_comp[p].items(), key=lambda kv: -kv[1]))
                out["asks"].append({"bucket": p, "total": round(self.ask_bucket[p], 4),
                                    "venues": {k: round(v, 4) for k, v in comp.items()}})
        return out