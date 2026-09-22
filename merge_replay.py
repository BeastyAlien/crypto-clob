# -*- coding: utf-8 -*-
"""Merge the two dry-run replay timelines into one tester replay file.

Why: nobi_bridge --dry-run --signals-out writes UTC timestamps, but the MQL5
EA parses replay timestamps with StringToTime() which interprets the string as
LOCAL time. If we feed UTC strings into the tester, every signal fires one
hour late/early relative to the price bars -> the backtest becomes wrong.

This script:
  1. reads replay_114744.sig  (11:47:xx -> 21:12:xx UTC, 2026-09-08)
  2. reads replay_211837.sig  (21:23:xx -> 02:05:xx UTC, 2026-09-08/09)
  3. keeps only signals with UTC datetime < 2026-09-08 23:00:00
     (i.e. the whole of yesterday local; drops tonight's tail)
  4. converts each signal's UTC instant to the machine's LOCAL time
  5. renumbers serials 1..N sequentially (EA must see strictly increasing ids)
  6. writes MQL5 Files\\nobi_replay.sig AND Common\\Files\\nobi_replay.sig

CRITICAL: output must use CRLF line endings. The MQL5 replay parser does
StringSubstr(s, 0, slen-1) to strip a trailing CR - with LF-only endings it
chops the LAST CHARACTER of every line ('BUY' -> 'BU'), so every signal fails
its direction check and the backtest silently trades nothing.

Output line format (MQL5 StringToTime compatible):
    <serial>|<yyyy.MM.dd HH:MM:SS local>|<prev>|<now>|<BUY|SELL>
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UTC_STRIP = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2}):(\d{2})$")

FILES = [
    os.path.join(HERE, "replay_114744.sig"),
    os.path.join(HERE, "replay_211837.sig"),
]
OUTS = [
    r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\nobi_replay.sig",
    r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\Files\nobi_replay.sig",
]

CUT_UTC = datetime(2026, 9, 8, 23, 0, 0, tzinfo=timezone.utc)  # end of yesterday local


def parse_utc(s: str) -> datetime | None:
    m = UTC_STRIP.match(s.strip())
    if not m:
        return None
    y, mo, d, h, mi, se = (int(v) for v in m.groups())
    try:
        return datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
    except ValueError:
        return None


def main() -> int:
    events = []  # (utc_dt, prev, now, side)
    for fp in FILES:
        if not os.path.isfile(fp):
            print("missing: %s" % fp)
            return 1
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) != 5:
                    continue
                dt = parse_utc(parts[1])
                if dt is None:
                    continue
                if dt >= CUT_UTC:
                    continue
                events.append((dt, parts[2], parts[3], parts[4]))

    events.sort(key=lambda e: e[0])  # chronological

    lines = []
    for i, (dt, prev, now, side) in enumerate(events, start=1):
        local = dt.astimezone().strftime("%Y.%m.%d %H:%M:%S")
        lines.append(f"{i}|{local}|{prev}|{now}|{side}\n")

    for dest in OUTS:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # CRLF is mandatory (see module docstring)
        with open(dest, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.writelines(lines)

    print("merged signals : %d" % len(lines))
    print("first          : %s" % lines[0].strip())
    print("last           : %s" % lines[-1].strip())
    print("written        : %s" % ", ".join(OUTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())