# -*- coding: utf-8 -*-
"""build_zip.py - package the whole NOBI rig for transfer to another PC.

Creates dist\\NOBI_Setup.zip containing:

    crypto-clob/        engine + bridge + config + control-center app + fix-paths.py
    crypto-clob-ui/     dashboard + start_all.bat
    MT5_files/          NobiScalpTrader.ex5/.mq5 + chart template
    SETUP_ON_NEW_PC.md  the 5-minute guide

Skips heavy/generated data (data/, data-ui/, __pycache__, logs, build).

Usage:  python build_zip.py
"""

from __future__ import annotations

import os
import sys
import zipfile
from datetime import datetime

HERE      = os.path.dirname(os.path.abspath(__file__))
COMMON    = os.path.dirname(HERE)
UI_DIR    = os.path.join(COMMON, "crypto-clob-ui")
MQL5_DIR  = r"C:\Users\USER\AppData\Roaming\MetaQuotes\Terminal\5B9C24F117C34D03F25BA926243C77EB\MQL5"

SKIP_DIRNAMES = {"__pycache__", "build", "data", "data-ui", ".git", "Temp"}
SKIP_EXTS     = {".log", ".tmp", ".pyc", ".spec", ".zip"}
SKIP_FILES    = {"NOBI_Setup.zip"}

EXCLUDE_TOP = {"crypto-clob-ui", "data", "__pycache__", "build", "dist+zips"}


def add_tree(zf: zipfile.ZipFile, root: str, arc_prefix: str) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRNAMES]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            # never embed previous dist zips (recursive bloat)
            if ("dist" in dirpath.replace(os.sep, "/").split("/")) and fn.lower().endswith(".zip"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            arc = os.path.join(arc_prefix, os.path.relpath(full, root))
            zf.write(full, arc)
            count += 1
    return count


def main() -> int:
    if not os.path.isdir(UI_DIR):
        print("crypto-clob-ui not found next to crypto-clob: %s" % UI_DIR)
        return 1

    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = os.path.join(dist, "NOBI_Setup_%s.zip" % stamp)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        n = add_tree(zf, HERE, "crypto-clob")
        print("crypto-clob   : %d files" % n)
        n = add_tree(zf, UI_DIR, "crypto-clob-ui")
        print("crypto-clob-ui: %d files" % n)

        # EA + template into MT5_files/
        for rel in ["Experts\\Advisors\\NobiScalpTrader.ex5",
                    "Experts\\Advisors\\NobiScalpTrader.mq5",
                    "Profiles\\Templates\\NobiScalpTrader_BTCUSD.tpl",
                    "Presets\\NobiScalpTrader_current.set"]:
            src = os.path.join(MQL5_DIR, rel)
            if not os.path.isfile(src):
                print("WARNING missing: %s" % src)
                continue
            dest = os.path.join("MT5_files", os.path.basename(rel))
            zf.write(src, dest)
            print("MT5_files     : %s" % os.path.basename(rel))

        guide = os.path.join(HERE, "SETUP_ON_NEW_PC.md")
        if os.path.isfile(guide):
            zf.write(guide, "SETUP_ON_NEW_PC.md")
            print("guide         : SETUP_ON_NEW_PC.md")

    size_mb = os.path.getsize(out) / 1024 / 1024
    print("\nCreated %s (%.1f MB)" % (out, size_mb))
    print("Transfer to the new PC, unzip, follow SETUP_ON_NEW_PC.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())