# -*- coding: utf-8 -*-
"""Bulk downloader: CME Micro Bitcoin (MBTU6) from DataBento.

  bbo-1s : 2026-09-19T00:00Z .. 2026-09-22T12:00Z   (6h chunks)
  ohlcv-1m: 2026-07-01T00:00Z .. 2026-09-23T00:00Z (7d chunks)
  definition: one call

Output: data_bento/raw/<schema>_MBTU6_<startstamp>.csv
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
RAW.mkdir(parents=True, exist_ok=True)

SYM = "MBTU6"
DS = "GLBX.MDP3"


def stamp(iso: str) -> str:
    return iso[:10].replace("-", "") + ""  # YYYYMMDD


def fetch(schema: str, start: str, end: str, tag: str) -> bool:
    fn = RAW / f"{schema}_{SYM}_{start[:10]}_{tag}.csv"
    if fn.exists() and fn.stat().st_size > 0:
        print("skip", fn.name, fn.stat().st_size)
        return True
    st, body = get("/v0/timeseries.get_range", {
        "dataset": DS, "start": start, "end": end,
        "symbols": SYM, "schema": schema, "encoding": "csv",
    }, timeout=600)
    if st != 200:
        print("FAIL", schema, start, end, st, str(body)[:200])
        return False
    if not isinstance(body, bytes):
        print("FAIL non-bytes", schema, start, str(body)[:200])
        return False
    fn.write_bytes(body)
    print("ok", fn.name, len(body), "bytes")
    return True


def main():
    schema = sys.argv[1] if len(sys.argv) > 1 else "bbo-1s"

    if schema == "bbo-1s":
        # 84h in 6h chunks, Sep 19 00:00Z -> Sep 22 12:00Z
        start_ms = 1789776000000  # 2026-09-19T00:00:00Z
        end_ms = 1789905600000    # 2026-09-22T12:00:00Z
        chunk = 6 * 3600 * 1000
        t0 = start_ms
        while t0 < end_ms:
            t1 = min(t0 + chunk, end_ms)
            iso0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 / 1000))
            iso1 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t1 / 1000))
            fetch(schema, iso0, iso1, iso0[5:10].replace("-", "") + iso0[11:13])
            t0 = t1
            time.sleep(0.3)

    elif schema == "ohlcv-1m":
        start_ms = 1785542400000  # 2026-07-01T00:00:00Z
        end_ms = 1789948800000    # 2026-09-23T00:00:00Z (exclusive-ish)
        chunk = 7 * 24 * 3600 * 1000
        t0 = start_ms
        while t0 < end_ms:
            t1 = min(t0 + chunk, end_ms)
            iso0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 / 1000))
            iso1 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t1 / 1000))
            fetch(schema, iso0, iso1, iso0[5:10].replace("-", "") + iso0[11:13])
            t0 = t1
            time.sleep(0.3)

    elif schema == "definition":
        fetch(schema, "2026-09-19T00:00:00", "2026-09-20T00:00:00", "one-off")


if __name__ == "__main__":
    main()