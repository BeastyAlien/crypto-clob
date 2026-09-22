"""Exchange adapters: Binance, Bybit, OKX, Kraken, Coinbase Advanced,
Bitstamp and Bitfinex.  Each maps WS frames to normalized events.

All feeds are public, unauthenticated. Regional blocks (e.g. Binance/OKX
HTTP 451 in the US, Coinbase outside supported regions) surface as
connection/reconnect warnings — pick venues that respond from your network.
"""

from __future__ import annotations

import json

from .base import Feed, http_get_json
from ..common import now_ms

# --------------------------------------------------------------------------
# Binance (diff book + agg trades).  REST snapshot anchors lastUpdateId.
# --------------------------------------------------------------------------
class BinanceFeed(Feed):
    name = "binance"

    def ws_urls(self):
        return [f"wss://stream.binance.com:9443/stream?streams={self.symbol}@depth/{self.symbol}@aggTrade"]

    def subscribe_msgs(self):
        return []  # streams are encoded in the URL

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        if not isinstance(m, dict) or "stream" not in m:
            return []
        d = m.get("data") or {}
        if d.get("e") == "depthUpdate":
            bids = [(p, float(q)) for p, q in d.get("b") or [] if float(q) > 0]
            asks = [(p, float(q)) for p, q in d.get("a") or [] if float(q) > 0]
            return [("book_diff", d.get("u"), bids, asks, now_ms())]
        if d.get("e") == "aggTrade":
            return [("trade", "sell" if d.get("m") else "buy",
                     float(d["p"]), float(d["q"]), int(d.get("T") or now_ms()))]
        return []

    def request_snapshot(self):
        # api.binance.com can reject by region; data-api.binance.vision is the
        # public market-data host.  Try hosts x depth tiers.
        host_limits = [
            ("https://api.binance.com", [5000, 1000, 500, 100]),
            ("https://data-api.binance.vision", [5000, 1000, 500, 100]),
        ]
        last = None
        for host, limits in host_limits:
            for lim in limits:
                try:
                    j = http_get_json(f"{host}/api/v3/depth?symbol={self.symbol.upper()}&limit={lim}")
                    return j.get("bids") or [], j.get("asks") or [], j.get("lastUpdateId")
                except Exception as exc:
                    last = exc
        raise last or RuntimeError("binance snapshot failed")


# --------------------------------------------------------------------------
# Bybit (linear perp: incremental 500-level orderbook + public trades)
# --------------------------------------------------------------------------
class BybitFeed(Feed):
    name = "bybit"
    URL = "wss://stream.bybit.com/v5/public/linear"
    CATEGORY = "linear"

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [json.dumps({"op": "subscribe", "args": [
            f"orderbook.500.{self.symbol}", f"publicTrade.{self.symbol}" ]})]

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        topic = m.get("topic") or ""
        if topic.startswith("orderbook"):
            d = m.get("data")
            if not isinstance(d, dict):
                return []
            b = [(x[0], x[1]) for x in d.get("b") or [] if len(x) > 1]
            a = [(x[0], x[1]) for x in d.get("a") or [] if len(x) > 1]
            seq = d.get("seq")
            ts = int(d.get("cts") or now_ms())
            if d.get("type") == "snapshot":
                return [("book_snapshot", seq, b, a, ts)]
            return [("book_diff", seq, b, a, ts)]
        if topic.startswith("publicTrade"):
            d = m.get("data")
            out = []
            if isinstance(d, list):
                for t in d:
                    out.append(("trade",
                                "buy" if str(t.get("S", "")).upper() == "BUY" else "sell",
                                float(t["p"]), float(t["v"]), int(t.get("T") or now_ms())))
            return out
        return []

    def request_snapshot(self):
        j = http_get_json(
            f"https://api.bybit.com/v5/market/orderbook?category={self.CATEGORY}"
            f"&symbol={self.symbol}&limit=500")
        r = j.get("result") or {}
        return r.get("b") or [], r.get("a") or [], r.get("seq")


# --------------------------------------------------------------------------
# OKX (books channel: snapshot then incremental diffs with prevSeqId/seqId)
# --------------------------------------------------------------------------
class OKXFeed(Feed):
    name = "okx"
    URL = "wss://ws.okx.com:8443/ws/v5/public"

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [json.dumps({"op": "subscribe", "args": [
            {"channel": "books", "instId": self.symbol},
            {"channel": "trades", "instId": self.symbol} ]})]

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        if m.get("event"):
            return []
        arg = m.get("arg") or {}
        ch = arg.get("channel")
        d = m.get("data")
        if not d:
            return []
        if ch == "books":
            x = d[0]
            b = [(z[0], float(z[1])) for z in x.get("bids") or [] if len(z) >= 2]
            a = [(z[0], float(z[1])) for z in x.get("asks") or [] if len(z) >= 2]
            seq = x.get("seqId")
            if x.get("action") == "snapshot":
                return [("book_snapshot", int(seq or 0), b, a, now_ms())]
            return [("book_diff", int(seq or 0), b, a, now_ms())]
        if ch == "trades":
            out = []
            for t in d:
                out.append(("trade", str(t.get("side", "")).lower(),
                            float(t.get("px")), float(t.get("sz")),
                            int(t.get("ts") or now_ms())))
            return out
        return []

    def request_snapshot(self):
        j = http_get_json(f"https://www.okx.com/api/v5/market/books?instId={self.symbol}&sz=400")
        x = j["data"][0]
        return x.get("bids") or [], x.get("asks") or [], x.get("seqId")


# --------------------------------------------------------------------------
# Kraken (book snapshot as/bs then a/b diffs; trades are price-dir flagged)
# --------------------------------------------------------------------------
class KrakenFeed(Feed):
    name = "kraken"
    URL = "wss://ws.kraken.com"
    REST_PAIR = "XBTUSD"

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [
            json.dumps({"event": "subscribe", "pair": [self.symbol],
                        "subscription": {"name": "book", "depth": 1000}}),
            json.dumps({"event": "subscribe", "pair": [self.symbol],
                        "subscription": {"name": "trade"}}),
        ]

    @staticmethod
    def _levels(arr):
        out = []
        for row in arr or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    out.append((float(row[0]), float(row[1])))
                except (TypeError, ValueError):
                    continue
        return out

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        if not isinstance(m, list) or not m:
            return []
        data = m[1]
        if isinstance(data, dict):
            evs = []
            if "as" in data or "bs" in data:
                evs.append(("book_snapshot", None,
                            self._levels(data.get("bs") or []),
                            self._levels(data.get("as") or []), now_ms()))
            if "b" in data:
                evs.append(("book_diff", None, self._levels(data["b"]), [], now_ms()))
            if "a" in data:
                evs.append(("book_diff", None, [], self._levels(data["a"]), now_ms()))
            return evs
        if isinstance(data, list):  # trade message
            out = []
            for t in data:
                if isinstance(t, list) and len(t) >= 4:
                    out.append(("trade", str(t[3]).lower(),
                                float(t[0]), float(t[1]),
                                int(float(t[2]) * 1000)))
            return out
        return []

    def request_snapshot(self):
        j = http_get_json(f"https://api.kraken.com/0/public/Depth?pair={self.REST_PAIR}&count=500")
        px = j["result"][f"X{self.REST_PAIR}ZUSD" if self.REST_PAIR == "XBTUSD" else self.REST_PAIR]
        return px.get("bids") or [], px.get("asks") or [], None


# --------------------------------------------------------------------------
# Coinbase Advanced Trade (level2 snapshot+update, market_trades)
# --------------------------------------------------------------------------
class CoinbaseFeed(Feed):
    name = "coinbase"
    URL = "wss://advanced-trade-ws.coinbase.com"

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [json.dumps({
            "type": "subscribe", "product_ids": [self.symbol],
            "channels": [
                {"name": "level2", "product_ids": [self.symbol]},
                {"name": "market_trades", "product_ids": [self.symbol]}]})]

    @staticmethod
    def _qty(s):
        if s in ("", "0", None):
            return 0.0
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        ch = m.get("channel")
        if ch == "level2":
            out = []
            for e in m.get("events") or []:
                upd = e.get("updates") or []
                if e.get("type") == "snapshot":
                    b = [(u["price_level"], self._qty(u["new_quantity"]))
                         for u in upd if u.get("side") == "bid"]
                    a = [(u["price_level"], self._qty(u["new_quantity"]))
                         for u in upd if u.get("side") == "ask"]
                    out.append(("book_snapshot", None, b, a, now_ms()))
                elif e.get("type") == "update":
                    b = [(u["price_level"], self._qty(u["new_quantity"]))
                         for u in upd if u.get("side") == "bid"]
                    a = [(u["price_level"], self._qty(u["new_quantity"]))
                         for u in upd if u.get("side") == "ask"]
                    out.append(("book_diff", None, b, a, now_ms()))
            return out
        if ch == "market_trades":
            out = []
            for e in m.get("events") or []:
                if e.get("type") in ("last_match", "match"):
                    out.append(("trade",
                                "buy" if str(e.get("side", "")).upper() == "BUY" else "sell",
                                float(e.get("price") or 0), float(e.get("size") or 0),
                                now_ms()))
            return out
        return []

    def request_snapshot(self):
        j = http_get_json(
            f"https://api.coinbase.com/api/v3/brokerage/market/product_book"
            f"?product_id={self.symbol}&limit=1000")
        pb = j.get("pricebook") or {}
        bids = [(x["price"], x["size"]) for x in pb.get("bids") or []]
        asks = [(x["price"], x["size"]) for x in pb.get("asks") or []]
        return bids, asks, None


# --------------------------------------------------------------------------
# Bitstamp (diff_order_book pushes the full book each frame -> treated as snapshot)
# --------------------------------------------------------------------------
class BitstampFeed(Feed):
    name = "bitstamp"
    URL = "wss://ws.bitstamp.net"

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [
            json.dumps({"event": "bts:subscribe", "data": {"channel": f"diff_order_book_{self.symbol}"}}),
            json.dumps({"event": "bts:subscribe", "data": {"channel": f"live_trades_{self.symbol}"}}),
        ]

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        if m.get("event") != "data":
            return []
        d = m.get("data") or {}
        ch = m.get("channel") or ""
        if "diff_order_book" in ch:
            b = [(r[0], float(r[1])) for r in d.get("bids") or [] if len(r) > 1]
            a = [(r[0], float(r[1])) for r in d.get("asks") or [] if len(r) > 1]
            ts = int(d.get("microtimestamp") or 0) or now_ms()
            return [("book_snapshot", None, b, a, ts)]
        if "live_trades" in ch:
            return [("trade",
                     "buy" if d.get("type") == 0 else "sell",
                     float(d.get("price") or 0), float(d.get("amount") or 0),
                     now_ms())]
        return []

    def request_snapshot(self):
        j = http_get_json(f"https://www.bitstamp.net/api/v2/order_book/{self.symbol}/")
        return j.get("bids") or [], j.get("asks") or [], None


# --------------------------------------------------------------------------
# Bitfinex (raw R0 book = per-order events; first message is the snapshot)
# --------------------------------------------------------------------------
class BitfinexFeed(Feed):
    name = "bitfinex"
    URL = "wss://api-pub.bitfinex.com/ws/2"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.chan = {}            # channel id -> kind
        self.raw = {}             # order id -> (price, amount)
        self.agg = {"bid": {}, "ask": {}}  # price -> aggregated qty

    def ws_urls(self):
        return [self.URL]

    def subscribe_msgs(self):
        return [
            json.dumps({"event": "subscribe", "channel": "book", "symbol": self.symbol, "prec": "R0"}),
            json.dumps({"event": "subscribe", "channel": "trades", "symbol": self.symbol}),
        ]

    def _reset(self):
        self.raw.clear()
        self.agg = {"bid": {}, "ask": {}}

    def _apply_order(self, oid, price, amount, diffs):
        try:
            oid = int(oid)
            price = float(price)
            amount = float(amount)
        except (TypeError, ValueError):
            return
        old = self.raw.get(oid)
        if amount == 0.0 and price == 0.0 and old is not None:
            price = old[0]
        self.raw[oid] = (price, amount)
        if old is not None:
            self._adj("bid" if old[1] < 0 else "ask", old[0], -abs(old[1]), diffs)
        if amount != 0.0:
            self._adj("bid" if amount < 0 else "ask", price, abs(amount), diffs)
        else:
            self.raw.pop(oid, None)

    def _adj(self, side, price, dv, diffs):
        m = self.agg[side]
        m[price] = m.get(price, 0.0) + dv
        if abs(m[price]) < 1e-12:
            m.pop(price, None)
        key = "bids" if side == "bid" else "asks"
        diffs[key].append((price, dv))

    def parse(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:
            return []
        if not isinstance(m, list) or not m:
            return []
        if isinstance(m[0], str):
            if m[0] == "event":
                # {"event":"subscribed","channel":"book","chanId":..., ...}
                if isinstance(m[1], dict) and m[1].get("event") == "subscribed":
                    self.chan[m[1].get("chanId")] = m[1].get("channel")
            return []
        cid = m[0]
        kind = self.chan.get(cid)
        if kind is None:
            return []
        data = m[1]
        if kind == "book":
            diffs = {"bids": [], "asks": []}
            if isinstance(data, list) and data and isinstance(data[0], list):
                self._reset()
                for row in data:
                    if len(row) >= 3:
                        self._apply_order(row[0], row[1], row[2], diffs)
                b = list(self.agg["bid"].items())
                a = list(self.agg["ask"].items())
                return [("book_snapshot", None, b, a, now_ms())]
            if data is not None:
                rows = data if isinstance(data, list) and data and isinstance(data[0], list) else [data]
                for row in rows:
                    if len(row) >= 3:
                        self._apply_order(row[0], row[1], row[2], diffs)
            return [("book_diff", None, diffs["bids"], diffs["asks"], now_ms())]
        if kind == "trades":
            rows = data if isinstance(data, list) and data and isinstance(data[0], list) else [data]
            out = []
            for t in rows:
                if isinstance(t, list) and len(t) >= 4:
                    amt = float(t[2])
                    out.append(("trade", "buy" if amt > 0 else "sell",
                                float(t[3]), abs(amt), int(t[1]) or now_ms()))
            return out
        return []

    def request_snapshot(self):
        j = http_get_json(f"https://api-pub.bitfinex.com/v2/book/{self.symbol}/P0?len=100")
        bids, asks = [], []
        for row in j or []:  # [price, count, amount]
            p, cnt, amt = row[0], row[1], row[2]
            if cnt <= 0:
                continue
            (bids if amt < 0 else asks).append((p, abs(amt)))
        return bids, asks, None


FEEDS = {
    "binance": BinanceFeed,
    "bybit": BybitFeed,
    "okx": OKXFeed,
    "kraken": KrakenFeed,
    "coinbase": CoinbaseFeed,
    "bitstamp": BitstampFeed,
    "bitfinex": BitfinexFeed,
}