"""Feed plumbing: REST snapshot helper, WS pump with reconnect/backoff."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

import websockets

log = logging.getLogger("crypto-clob")

USER_AGENT = "crypto-clob/0.1 research"


def http_get_json(url: str, timeout: float = 12.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Feed:
    """Base exchange adapter.

    Subclasses define `parse()` which converts raw WS frames into a flat list
    of event tuples:

      ("book_snapshot", seq, bids, asks, ts_ms)
      ("book_diff",     seq, bids_delta, asks_delta, ts_ms)
      ("trade",         "buy"|"sell", price, qty, ts_ms)

    bids/asks are lists of (price, qty); a qty <= 0 in a diff deletes a level.
    """

    name = ""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.symbol = cfg["symbols"][self.name]

    def ws_urls(self):
        raise NotImplementedError

    def subscribe_msgs(self):
        return []

    def parse(self, raw: str):
        raise NotImplementedError

    def request_snapshot(self):
        """Return (bids, asks, seq) from a REST endpoint."""
        raise NotImplementedError


async def run_feed(feed: Feed, emit, stop: asyncio.Event) -> None:
    """Connect, subscribe, and stream events until `stop` is set."""
    backoff = 1.0
    while not stop.is_set():
        try:
            url = feed.ws_urls()[0]
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=25,
                max_size=64 * 2**20,
                compression=None,
            ) as ws:
                for msg in feed.subscribe_msgs():
                    await ws.send(msg)
                log.info("%s: connected", feed.name)
                backoff = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    try:
                        events = feed.parse(raw)
                    except Exception as exc:  # never die on a bad frame
                        log.debug("%s: parse error %s", feed.name, exc)
                        continue
                    for ev in events:
                        try:
                            await emit(feed.name, ev)
                        except Exception as exc:  # keep the socket alive, log the bug
                            log.error("%s: emit error: %s", feed.name, exc, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("%s: %s — reconnect in %.1fs", feed.name, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
    log.info("%s: stopped", feed.name)