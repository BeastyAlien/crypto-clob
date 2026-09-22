# -*- coding: utf-8 -*-
"""brain_server.py - Chrome dashboard for the NOBI AI brain.

Serves a self-refreshing "brain console" at:

    http://127.0.0.1:8090

It reads ONLY local files under <crypto-clob>/ai (state.json, thoughts.log,
recommended.json, report_latest.txt, dataset.csv) plus nobi_config.json and
the live metrics/signal files that config points to. Read-only: never
trades, never writes anything, never opens the internet.

Endpoints:
    /              dashboard HTML (brain_dashboard.html)
    /api/all       one JSON bundle with everything the page needs

Usage:
    python brain_server.py
    python brain_server.py --port 8091
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../crypto-clob/ai
CLOB = HERE.parent                              # .../crypto-clob
DASH = HERE / "brain_dashboard.html"
STATE = HERE / "state.json"
THOUGHTS = HERE / "thoughts.log"
REC = HERE / "recommended.json"
REPORT = HERE / "report_latest.txt"
CFG = CLOB / "nobi_config.json"
DATA_UI = CLOB.parent / "crypto-clob-ui" / "data-ui"
PORT = 8090

_row_cache = {"t": 0.0, "rows": 0}


def rd(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def latest_signal() -> dict:
    try:
        cfg = json.loads(rd(CFG, "{}") or "{}")
        sp = Path(cfg.get("signal_file") or "")
        if sp.is_file():
            line = rd(sp, "").strip()
            parts = line.split("|")
            if len(parts) >= 5:
                return {"serial": parts[0], "time": parts[1],
                        "prev": parts[2], "now": parts[3], "side": parts[4]}
    except Exception:
        pass
    return {}


def latest_metrics() -> dict:
    """Cheap live read: newest metrics file, last ~256 KB tail."""
    try:
        files = [f for f in DATA_UI.glob("metrics_*.jsonl")
                 if f.is_file() and f.stat().st_size > 0]
        if not files:
            return {}
        f = max(files, key=lambda p: p.stat().st_mtime)
        size = f.stat().st_size
        with open(f, "rb") as fh:
            fh.seek(max(0, size - 262144))
            data = fh.read()
        lines = [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
        if not lines:
            return {"file": f.name, "size": size, "age_s": 0}
        r = json.loads(lines[-1])
        nv = 0
        for k in ("cvd_venue", "cvd_window_venue"):
            v = r.get(k) or {}
            nv = max(nv, sum(1 for x in v.values() if abs(x or 0) > 1e-9))
        return {"file": f.name, "size": size, "ts": r.get("ts_ms"),
                "ofi": r.get("ofi_total"), "cvd": r.get("cvd_total"),
                "bid": r.get("best_bid_bucket"), "ask": r.get("best_ask_bucket"),
                "n_venues": nv, "aggr": r.get("aggression_ratio")}
    except Exception:
        return {}


def dataset_info() -> dict:
    d = HERE / "dataset.csv"
    if not d.is_file():
        return {"rows": 0, "bytes": 0}
    try:
        size = d.stat().st_size
        now = datetime.now(timezone.utc).timestamp()
        if now - _row_cache["t"] > 60 or _row_cache["rows"] == 0:
            _row_cache["rows"] = sum(1 for _ in d.open(encoding="utf-8", errors="replace"))
            _row_cache["t"] = now
        return {"rows": _row_cache["rows"], "bytes": size}
    except Exception:
        return {"rows": 0, "bytes": 0}


def build_all() -> dict:
    st = json.loads(rd(STATE, "{}") or "{}")
    rec = json.loads(rd(REC, "{}") or "{}")
    return {
        "now_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "state": st,
        "recommended": rec,
        "report": rd(REPORT),
        "thoughts": [ln for ln in rd(THOUGHTS).splitlines() if ln.strip()][-200:],
        "signal": latest_signal(),
        "metrics": latest_metrics(),
        "dataset": dataset_info(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the console quiet
        pass

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", rd(DASH, "missing brain_dashboard.html").encode("utf-8"))
        elif path == "/api/all":
            self._send(200, "application/json; charset=utf-8", json.dumps(build_all()).encode("utf-8"))
        elif path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
        else:
            self._send(404, "text/plain", b"not found")


def main() -> int:
    ap = argparse.ArgumentParser(description="NOBI brain console server")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"NOBI brain console: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())