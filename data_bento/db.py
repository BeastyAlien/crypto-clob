# -*- coding: utf-8 -*-
"""Minimal DataBento HTTP REST helper (stdlib only).

Auth: HTTP Basic, API key as username, empty password.
Key is read from env DATABENTO_KEY (never hard-coded here).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://hist.databento.com"


def _auth_header() -> dict:
    key = (os.environ.get("DATABENTO_KEY") or "").strip()
    if not key:
        sys.exit("DATABENTO_KEY env var is required")
    return {"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode()}


def _read(resp):
    ct = resp.headers.get("Content-Type", "")
    body = resp.read()
    if "json" in ct:
        return json.loads(body)
    return body


def get(path: str, params: dict | None = None, timeout: int = 180):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_auth_header())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _read(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            detail = e.read().decode("utf-8", "replace")[:4000]
        return e.code, detail


def post_json(path: str, payload: dict, timeout: int = 600):
    req = urllib.request.Request(BASE + path, method="POST",
                                 headers={**_auth_header(), "Content-Type": "application/json"},
                                 data=json.dumps(payload).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _read(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            detail = e.read().decode("utf-8", "replace")[:4000]
        return e.code, detail


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "me"
    if which == "me":
        st, d = get("/v0/users/me")
        print(st, json.dumps(d, indent=1)[:2000])
    elif which == "datasets":
        st, d = get("/v0/metadata.list_datasets")
        print(st, json.dumps(d)[:4000])
    elif which == "schemas":
        st, d = get("/v0/metadata.list_schemas", {"dataset": sys.argv[2]})
        print(st, json.dumps(d, indent=1)[:6000])
    elif which == "avail":
        ds = sys.argv[2]
        st, d = get(f"/v0/dataset/{ds}/availability")
        print(st, json.dumps(d, indent=1)[:8000])
    elif which == "resolve":
        st, d = get("/v0/symbology.resolve", {
            "dataset": sys.argv[2], "symbols": sys.argv[3],
            "stype_in": sys.argv[4] if len(sys.argv) > 4 else "raw_symbol",
            "stype_out": "instrument_id",
            "start_date": sys.argv[5] if len(sys.argv) > 5 else "2026-09-01",
        })
        print(st, json.dumps(d, indent=1)[:6000])
    elif which == "range":
        st, d = get("/v0/timeseries.get_range", {
            "dataset": sys.argv[2],
            "start": sys.argv[3],
            "end": sys.argv[4] if len(sys.argv) > 4 else "",
            "symbols": sys.argv[5] if len(sys.argv) > 5 else "ALL_SYMBOLS",
            "schema": sys.argv[6] if len(sys.argv) > 6 else "trades",
            "encoding": "csv",
        }, timeout=300)
        if isinstance(d, bytes):
            print(st, "BYTES", len(d))
            print(d[:2000].decode("utf-8", "replace"))
        else:
            print(st, json.dumps(d)[:4000])