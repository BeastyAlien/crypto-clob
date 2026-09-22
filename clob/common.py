"""Shared primitives: monotonic ms clock, price bucketing, per-venue order book state."""

from __future__ import annotations

import math
import time

CLEANUP_THRESHOLD = 1e-12


def now_ms() -> int:
    """Monotonic-ish wall clock aligned to UTC epoch milliseconds."""
    return time.time_ns() // 1_000_000


def bucket_below(price: float, tick: float) -> float:
    """Uniform price bucketing: floor `price` to the nearest `tick` grid point."""
    return math.floor(price / tick) * tick


def bucket_round(price: float, tick: float) -> float:
    return round(price / tick) * tick


def _update_map(m: dict, deltas: list) -> None:
    for p, v in deltas:
        p = float(p)
        v = float(v)
        if v <= 0.0:
            m.pop(p, None)
        else:
            m[p] = v


class OrderBook:
    """Per-venue L2 book keyed by price.

    Supports the snapshot + buffered-diff sync protocol: while a snapshot is
    in flight, incoming diffs are buffered and replayed once the snapshot is
    applied.  `apply_diff` returns the list of changed (side, price, old, new)
    tuples so downstream consumers can update aggregated state incrementally.
    """

    def __init__(self, exchange: str, cap: int = 4000):
        self.exchange = exchange
        self.cap = cap
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.has_snapshot = False
        self.snapshot_inflight = False
        self.buffered: list[tuple] = []
        self.seq: int | None = None
        self.updated_ms = 0
        self.events: list[tuple] = []  # (side, price, old, new) from last apply_diff

    # -- lifecycle ----------------------------------------------------------
    def start_snapshot(self) -> None:
        self.snapshot_inflight = True
        self.buffered.clear()

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.has_snapshot = False
        self.snapshot_inflight = False
        self.buffered.clear()
        self.seq = None
        self.events.clear()

    @staticmethod
    def _norm(rows) -> list:
        """Take first two numeric columns from any REST/WS level row shape."""
        out = []
        for r in rows:
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                continue
            try:
                p, v = float(r[0]), float(r[1])
            except (TypeError, ValueError):
                continue
            if v > 0.0:
                out.append((p, v))
        return out

    def apply_snapshot(self, bids, asks, seq=None, ts=None) -> None:
        self.bids = dict(self._norm(bids))
        self.asks = dict(self._norm(asks))
        self.has_snapshot = True
        self.snapshot_inflight = False
        self.seq = seq
        self.updated_ms = ts or now_ms()
        self._trim()
        # replay diffs buffered while the snapshot was in flight
        buf, self.buffered = self.buffered, []
        for bd, ad, sq in buf:
            self._apply(bd, ad, sq)
        self.events.clear()

    # -- diffs --------------------------------------------------------------
    def apply_diff(self, bd, ad, seq=None, ts=None, rule=None) -> bool:
        """Apply a diff; returns False when the sequence rule detected a gap
        (caller should trigger a fresh snapshot)."""
        if not self.has_snapshot:
            self.buffered.append((bd, ad, seq))
            return False
        return self._apply(bd, ad, seq, ts, rule)

    def _apply(self, bd, ad, seq=None, ts=None, rule=None) -> bool:
        if rule is not None and seq is not None and self.seq is not None:
            if not rule(seq, self.seq):
                self._resync()
                return False
        if seq is not None:
            self.seq = seq
        self.events.clear()
        for side, m, d in (("bid", self.bids, bd), ("ask", self.asks, ad)):
            for p, v in d:
                p = float(p)
                v = float(v)
                old = m.get(p, 0.0)
                if v <= 0.0:
                    m.pop(p, None)
                else:
                    m[p] = v
                self.events.append((side, p, old, float(v)))
        self.updated_ms = ts or now_ms()
        self._trim()
        return True

    def _resync(self) -> None:
        self.has_snapshot = False
        self.snapshot_inflight = True
        self.buffered.clear()
        self.seq = None

    # -- queries ------------------------------------------------------------
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def spread(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def _trim(self) -> None:
        for m, reverse in ((self.bids, True), (self.asks, False)):
            if len(m) > self.cap:
                keep = sorted(m, reverse=reverse)[: self.cap]
                m = {k: m[k] for k in keep}


def flatten_levels(rows) -> list:
    """Convert exchange level rows of any shape into (price, qty) pairs."""
    out = []
    for r in rows:
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            try:
                out.append((float(r[0]), float(r[1])))
            except (TypeError, ValueError):
                continue
    return out