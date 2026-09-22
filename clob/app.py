"""Async engine: wires feeds -> global book -> flow -> lead-lag, runs the
snapshot+diff sync protocol, and writes JSONL output."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .aggregator import GlobalBook
from .flow import FlowEngine
from .leadlag import LeadLag
from .common import now_ms
from .feeds.adapters import FEEDS
from .feeds.base import run_feed

log = logging.getLogger("crypto-clob")


def _seq_rules():
    return {
        "binance": lambda cur, prev: prev is None or cur > prev,
        "bybit": lambda cur, prev: prev is None or cur == prev + 1,
        "okx": lambda cur, prev: prev is None or cur == prev + 1,
    }


class Engine:
    def __init__(self, cfg: dict, out_dir: str):
        self.cfg = cfg
        self.agg = GlobalBook(cfg)
        self.flow = FlowEngine(cfg)
        self.ll = LeadLag(cfg)
        self.rules = _seq_rules()
        self.start_ms = now_ms()
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.metrics_path = self.out / f"metrics_{stamp}.jsonl"
        self.events_path = self.out / f"events_{stamp}.jsonl"
        self._mf = open(self.metrics_path, "a", encoding="utf-8")
        self._ef = open(self.events_path, "a", encoding="utf-8")
        self.last_heatmap = {}
        self.snap_retry: dict = {}  # ex -> epoch seconds when a snapshot may be retried

    def close(self):
        self._mf.close()
        self._ef.close()

    # -- snapshot sync ------------------------------------------------------
    async def fetch_snapshot(self, ex: str) -> None:
        feed = FEEDS[ex](self.cfg)
        try:
            bids, asks, seq = await asyncio.to_thread(feed.request_snapshot)
        except Exception as exc:
            log.warning("%s: REST snapshot failed (%s)", ex, exc)
            self.agg.books[ex].snapshot_inflight = False
            self.snap_retry[ex] = now_ms() / 1000.0 + 5  # throttle re-requests
            return
        self.agg.apply_snapshot(ex, bids, asks, seq, now_ms())
        log.info("%s: snapshot %d bids / %d asks", ex, len(bids), len(asks))

    # -- event dispatch -----------------------------------------------------
    async def handle(self, ex: str, ev: tuple) -> None:
        ts = now_ms()
        kind = ev[0]
        if kind == "book_snapshot":
            if not ev[2] and not ev[3]:
                return
            self.agg.apply_snapshot(ex, ev[2], ev[3], ev[1], ev[4] or ts)
        elif kind == "book_diff":
            bk = self.agg.books[ex]
            if not bk.has_snapshot:
                cur = now_ms() / 1000.0
                if not bk.snapshot_inflight and cur >= self.snap_retry.get(ex, 0.0):
                    bk.snapshot_inflight = True
                    asyncio.get_running_loop().create_task(self.fetch_snapshot(ex))
                return
            ok = self.agg.apply_diff(ex, ev[2], ev[3], ev[1], ev[4] or ts, self.rules.get(ex))
            if not ok:
                cur = now_ms() / 1000.0
                if not bk.snapshot_inflight and cur >= self.snap_retry.get(ex, 0.0):
                    bk.snapshot_inflight = True
                    asyncio.get_running_loop().create_task(self.fetch_snapshot(ex))
                return
            bb, ba = self.agg.bests(ex)
            mid = (bb + ba) / 2.0 if bb and ba else None
            self.flow.on_book_changes(ex, bk.events, ts, mid)
            if bb and ba:
                self.ll.on_book(ex, bb, ba, ts)
        elif kind == "trade":
            _, side, price, qty, et = ev
            self.flow.on_trade(ex, side, price, qty, et or ts)
        else:
            return
        # per-venue mid for lead-lag (cheap, reuses last book state)
        bb, ba = self.agg.bests(ex)
        if bb and ba:
            self.ll.on_book(ex, bb, ba, ts)
        # flush any queued flag events (sweeps, icebergs, spoofs, absorption)
        while self.flow.events_out:
            self.write_event(self.flow.events_out.popleft())

    # -- periodic summary ---------------------------------------------------
    def current_row(self) -> dict:
        nobi = self.agg.noBi()
        bb, ba = self.agg.global_best()
        bv, av = self.agg.depth_sides()
        self.last_heatmap = self.agg.heatmap()
        return {
            "ts_ms": now_ms(),
            "noBi_n_%d" % int(self.cfg["nobi_levels"]): nobi,
            "best_bid_bucket": bb,
            "best_ask_bucket": ba,
            "depth_w_bid": round(bv, 4),
            "depth_w_ask": round(av, 4),
            "cvd_total": round(self.flow.cvd_total, 4),
            "cvd_venue": {k: round(v, 4) for k, v in sorted(self.flow.cvd.items())},
            "cvd_window_venue": self.flow.cvd_window_value(),
            "ofi_total": round(self.agg.ofi_total, 2),
            "ofi_venue": {k: round(v, 2) for k, v in sorted(self.agg.ofi_ex.items())},
            "aggression_ratio": self.flow.aggression_ratio(),
            "sweeps": len(self.flow.sweeps),
            "iceberg_flags": len(self.flow.iceberg_flags),
            "spoof_flags": len(self.flow.spoof_flags),
            "absorption_flags": len(self.flow.absorb_flags),
            "leadership": self.ll.leaderboard(),
            "spread_divergences": self.ll.recent_divergences(),
            "heatmap_bid": self.last_heatmap["bids"][:5],
            "heatmap_ask": self.last_heatmap["asks"][:5],
        }

    def write_metrics(self, row: dict) -> None:
        self._mf.write(json.dumps(row) + "\n")
        self._mf.flush()

    def write_event(self, e: dict) -> None:
        self._ef.write(json.dumps(e) + "\n")
        self._ef.flush()
        log.info("EVENT %s", json.dumps(e))

    # -- console ------------------------------------------------------------
    def format_row(self, row: dict) -> str:
        bb = row["best_bid_bucket"] if row["best_bid_bucket"] else 0
        ba = row["best_ask_bucket"] if row["best_ask_bucket"] else 0
        nobi = row.get("noBi_n_%d" % int(self.cfg["nobi_levels"]))
        lines = []
        lines.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                     f"BTC touch {bb:.1f} / {ba:.1f}  NOBI={nobi if nobi is None else round(nobi,3)}")
        lines.append(f"   depth {row['depth_w_bid']:.1f}B / {row['depth_w_ask']:.1f}A   "
                     f"CVD {row['cvd_total']:.2f}  OFI {row['ofi_total']:.1f}  "
                     f"agg_ratio {row['aggression_ratio']}")
        lead = ",".join(f"{k}:{v}" for k, v in row["leadership"].items()) or "-"
        lines.append(f"   sweeps {row['sweeps']}  iceberg {row['iceberg_flags']}  "
                     f"spoof {row['spoof_flags']}  absorb {row['absorption_flags']}  lead[{lead}]")
        hb = row["heatmap_bid"] or row["heatmap_ask"]
        if hb:
            lines.append("   " + self._fmt_heat(row["heatmap_bid"], row["heatmap_ask"]))
        return "\n".join(lines)

    @staticmethod
    def _fmt_heat(bids, asks) -> str:
        parts = []
        for b in bids[:4]:
            parts.append(f"B{b['bucket']:.0f}:{b['total']:.1f}")
        parts.append("|")
        for a in asks[:4]:
            parts.append(f"A{a['bucket']:.0f}:{a['total']:.1f}")
        return " ".join(parts)

    async def loop_summary(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            row = self.current_row()
            self.write_metrics(row)
            print(self.format_row(row), flush=True)

    async def run(self, exchanges, duration_s: float, interval: float) -> None:
        stop = asyncio.Event()
        tasks = [asyncio.create_task(run_feed(FEEDS[ex](self.cfg), self.handle, stop))
                 for ex in exchanges if ex in FEEDS]
        tasks.append(asyncio.create_task(self.loop_summary(interval)))
        try:
            if duration_s and duration_s > 0:
                await asyncio.sleep(duration_s)
            else:
                while True:
                    await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.close()