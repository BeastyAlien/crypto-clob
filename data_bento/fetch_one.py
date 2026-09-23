# -*- coding: utf-8 -*-
"""Fetch one timeseries range to raw/<schema>_<sym>_<compact>.csv"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
RAW.mkdir(parents=True, exist_ok=True)
SYM = "MBTU6"
DS = "GLBX.MDP3"

if __name__ == "__main__":
    schema = sys.argv[1]
    iso0 = sys.argv[2]
    iso1 = sys.argv[3]
    tag = iso0[5:10].replace("-", "") + iso0[11:13]
    fn = RAW / f"{schema}_{SYM}_{iso0[:10]}_{tag}.csv"
    if fn.exists() and fn.stat().st_size > 0:
        print("skip", fn.name, fn.stat().st_size)
        sys.exit(0)
    st, body = get("/v0/timeseries.get_range", {
        "dataset": DS, "start": iso0, "end": iso1,
        "symbols": SYM, "schema": schema, "encoding": "csv",
    }, timeout=600)
    if st != 200:
        print("FAIL", schema, iso0, iso1, st, str(body)[:200])
        sys.exit(1)
    if not isinstance(body, bytes):
        print("FAIL non-bytes", st, str(body)[:200])
        sys.exit(1)
    fn.write_bytes(body)
    print("ok", fn.name, len(body))