"""crypto-clob entry point.

Consumes public BTC order-book + trade streams from up to 7 venues and computes
a consolidated global L2/L3-ish book plus order-flow / micro-structure metrics.

Research/analysis only — reads public market data, never places orders.

Usage:
    python run.py --duration 120                 # run 2 minutes, all venues
    python run.py --exchanges binance bitfinex   # subset of venues
    python run.py --duration 0                   # run until Ctrl+C
    python run.py --out data                     # JSONL output dir (default ./data)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

try:
    import websockets  # noqa: F401
except ImportError:
    sys.exit("Missing dependency 'websockets'. Install with:  python -m pip install -r requirements.txt")

from clob.app import Engine
from clob.feeds.adapters import FEEDS


def main() -> None:
    ap = argparse.ArgumentParser(description="Cross-venue BTC order-flow engine")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    ap.add_argument("--exchanges", nargs="*", default=None,
                    help="venue subset, e.g. --exchanges binance bybit okx")
    ap.add_argument("--duration", type=float, default=0, help="seconds; 0 = until Ctrl+C")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)  # don't spam frames
    log = logging.getLogger("crypto-clob")

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    exchanges = args.exchanges or list(cfg["exchanges"])
    exchanges = [e for e in exchanges if e in FEEDS]
    if not exchanges:
        log.error("No known exchanges selected: %s", args.exchanges)
        sys.exit(1)

    interval = float(cfg["summary_interval_sec"])
    log.info("Starting crypto-clob on %s | exchanges: %s | output: %s",
             cfg["symbol"], ",".join(exchanges), args.out)

    engine = Engine(cfg, args.out)
    try:
        asyncio.run(engine.run(exchanges, args.duration, interval))
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        log.info("Done. Metrics: %s / Events: %s",
                 engine.metrics_path, engine.events_path)


if __name__ == "__main__":
    main()