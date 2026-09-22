"""Cross-venue lead-lag and spread divergence.

A heuristic "price discovery" score: when venue A moves the mid first and
venue B follows in the same direction within the lead-lag window, credit
A over B.  Spreads are compared cross-venue; a venue whose spread z-score
exceeds the configured threshold reports a divergence event.
"""

from __future__ import annotations

from collections import defaultdict, deque


class LeadLag:
    def __init__(self, cfg: dict):
        self.win = int(cfg["leadlag_win_ms"])
        self.z_thr = float(cfg["spread_div_z"])
        self.mids: dict = {}                 # ex -> (ts, mid)
        self.last_move: dict = {}            # ex -> (ts, direction)
        self.spreads: dict = {}              # ex -> spread
        self.lead: dict = defaultdict(int)   # (leader, follower) -> count
        self.divergences: deque = deque(maxlen=50)

    def on_book(self, ex: str, best_bid: float, best_ask: float, ts: int) -> None:
        if not best_bid or not best_ask or best_ask <= best_bid:
            return
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        prev = self.mids.get(ex)
        self.mids[ex] = (ts, mid)
        self.spreads[ex] = spread

        if prev is not None and ts > prev[0]:
            d = 0
            if mid > prev[1]:
                d = 1
            elif mid < prev[1]:
                d = -1
            if d:
                self.last_move[ex] = (ts, d)
                # credit venues that moved first within the window
                for other, (ots, od) in self.last_move.items():
                    if other == ex or od != d:
                        continue
                    if 0 <= ts - ots <= self.win:
                        self.lead[(other, ex)] += 1

        # cross-venue spread divergence
        vals = list(self.spreads.values())
        if len(vals) >= 3:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = var ** 0.5
            if std > 0:
                z = (spread - mean) / std
                if z >= self.z_thr:
                    self.divergences.append({"ts": ts, "exchange": ex,
                                             "spread": round(spread, 4),
                                             "z": round(z, 2)})

    def leaderboard(self, top: int = 4) -> dict:
        agg = defaultdict(int)
        for (a, b), n in self.lead.items():
            agg[a] += n
        ranked = sorted(agg.items(), key=lambda kv: -kv[1])[:top]
        return {ex: n for ex, n in ranked}

    def recent_divergences(self, last_n: int = 5):
        return list(self.divergences)[-last_n:]