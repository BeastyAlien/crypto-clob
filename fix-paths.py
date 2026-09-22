# -*- coding: utf-8 -*-
"""fix-paths.py - point nobi_config.json at THIS PC's MetaTrader 5 folder.

Run ON THE NEW PC after MetaTrader 5 has been installed and logged in once:

    python fix-paths.py            # auto-detect the newest terminal data folder
    python fix-paths.py --terminal "D:\\MT5\\data\\Terminal\\<HASH>"

It looks under %APPDATA%\\MetaQuotes\\Terminal and finds the newest folder
that contains MQL5\\Files (each installed+logged-in terminal has its own
random <HASH> folder). It then rewrites signal_file / anchor_file in
nobi_config.json to point at that folder, so the bridge and the EA agree
on where the signal file lives.

Never trades, only edits one JSON config.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def find_terminal_roots() -> list:
    roots = []
    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not base.is_dir():
        return roots
    for p in base.iterdir():
        if not p.is_dir():
            continue
        mql_files = p / "MQL5" / "Files"
        if mql_files.is_dir():
            try:
                mtime = mql_files.stat().st_mtime
            except OSError:
                mtime = 0.0
            roots.append((p, mtime))
    roots.sort(key=lambda x: x[1], reverse=True)   # newest first
    return [r for r, _ in roots]


def main() -> int:
    ap = argparse.ArgumentParser(description="Repoint nobi_config.json at this PC's MT5 folder")
    ap.add_argument("--terminal", default="", help="explicit Terminal\\<HASH> path")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    cfg_path = here / "nobi_config.json"
    if not cfg_path.is_file():
        print("nobi_config.json not found next to this script: %s" % here)
        return 1

    if args.terminal:
        mql_files = Path(args.terminal) / "MQL5" / "Files"
        if not mql_files.is_dir():
            print("Not an MT5 data folder with MQL5\\Files: %s" % args.terminal)
            return 1
        chosen = Path(args.terminal)
    else:
        roots = find_terminal_roots()
        if not roots:
            print("No MetaTrader 5 data folders found under %APPDATA%\\MetaQuotes\\Terminal.")
            print("Install MetaTrader 5 and log in once first, then re-run.")
            return 1
        chosen = roots[0]
        print("Found %d terminal data folder(s). Using newest:" % len(roots))
        for i, r in enumerate(roots):
            mark = " <--" if i == 0 else ""
            print("  [%d] %s%s" % (i, r, mark))

    mql_files = chosen / "MQL5" / "Files"
    mql_files.mkdir(parents=True, exist_ok=True)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    old_signal = cfg.get("signal_file", "")
    old_anchor = cfg.get("anchor_file", "")
    cfg["signal_file"] = str(mql_files / "nobi_signal.sig")
    cfg["anchor_file"] = str(mql_files / "nobi_anchor.sig")

    cfg_path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")

    print("\nUpdated %s" % cfg_path)
    print("  signal_file: %s" % cfg["signal_file"])
    print("  anchor_file: %s" % cfg["anchor_file"])
    print("\nOld values (first run on a fresh PC these were still this PC's paths):")
    print("  signal_file: %s" % old_signal)
    print("  anchor_file: %s" % old_anchor)
    print("\nDone. Now run NOBITradingCenter.exe -> Start all -> Start MT5.")
    return 0


if __name__ == "__main__":
    sys.exit(main())