# -*- coding: utf-8 -*-
"""package_portable.py - build one movable NOBI kit ZIP for another PC.

Packs crypto-clob (engines + bridge + AI brain + controller + portable
python), crypto-clob-ui (dashboard), the MT5 EA files and instructions.

EXCLUDED (a fresh PC should start clean and re-download its own data):
  - all historical market data: data/, data-ui/*.jsonl, ui-own-data/, exports/
  - brain runtime state: ai/dataset.csv, ai/state.json, ai/thoughts.log,
    ai/recommended.json, ai/report_latest.txt
  - logs, __pycache__, old webapp dist zip builds, config backups

Run:  python package_portable.py
Out:  <Common>\\NOBI_Setup_<ts>.zip
"""
from __future__ import annotations

import fnmatch
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMON = HERE.parent
UI = COMMON / "crypto-clob-ui"
OUT = COMMON / ("NOBI_Setup_%s.zip" % datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")[:-3])

EXCLUDE_DIR_NAMES = {"__pycache__", "data", "data-ui", "ui-own-data", "exports",
                     "build", "settings_backup"}
EXCLUDE_SUFFIX = (".jsonl", ".log", ".pyc", ".pyo", ".zip")
AI_KEEP = {"brain_server.py", "brain_dashboard.html"}
AI_DROP = {"dataset.csv", "state.json", "thoughts.log", "recommended.json",
           "report_latest.txt", "collect.log", "think.log"}


def keep(p: Path, root: Path) -> bool:
    rel = p.relative_to(root)
    parts = rel.parts
    if "dist" in parts:
        return p.name == "NOBI_Controller.exe"
    name = p.name
    if any(seg in EXCLUDE_DIR_NAMES for seg in parts[:-1]):
        return False
    if name.endswith(EXCLUDE_SUFFIX):
        return False
    if "MT5_files" in parts and name.endswith((".ex5", ".mq5", ".tpl", ".set")):
        return True
    if name == "nobi_config.json.bak":
        return False
    if name == "NOBI_Controller.spec":
        return True
    return True


def main() -> int:
    missing = [p for p in (HERE, UI) if not p.is_dir()]
    if missing:
        print("missing folders:", missing)
        return 1
    print(f"packing -> {OUT}")
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, arc in ((HERE, "crypto-clob"), (UI, "crypto-clob-ui")):
            for p in sorted(root.rglob("*")):
                if not p.is_file():
                    continue
                if "ai" in p.relative_to(root).parts:
                    if p.name in AI_DROP or p.suffix in (".log", ".pyc"):
                        continue
                    if p.name not in AI_KEEP and p.suffix not in (".py", ".html", ".md", ".txt"):
                        continue
                if not keep(p, root):
                    continue
                z.write(p, f"{arc}/{p.relative_to(root)}")
                n += 1
        z.writestr("INSTALL_FIRST.txt", INSTALL_TEXT)
        bat = HERE / "install_here.bat"
        if bat.is_file():
            z.writestr("0_INSTALL_ME_FIRST.bat", bat.read_bytes())
        n += 1
    size = OUT.stat().st_size
    print(f"ok: {n} files packed, {size/1e6:.1f} MB ->\n  {OUT}")
    return 0


INSTALL_TEXT = """NOBI PORTABLE KIT - install on another Windows PC

1. Install MetaTrader 5 and log in once.
2. Copy the two folders from this zip into your Common folder (the path is:
      %APPDATA%\\MetaQuotes\\Terminal\\Common
   i.e.  C:\\Users\\<you>\\AppData\\Roaming\\MetaQuotes\\Terminal\\Common
   You should end up with:
      Common\\crypto-clob\\      (engines, bridge, AI brain, controller)
      Common\\crypto-clob-ui\\   (dashboard + live data folder)
3. Copy crypto-clob\\MT5_files\\NobiScalpTrader.ex5 into the terminal's
      <Terminal>\\MQL5\\Experts\\Advisors\\
   folder, then attach the EA to a BTCUSDm chart (it will trade with the
   settings you choose - review SL/TP before enabling trading).
4. Double-click  crypto-clob\\dist\\NOBI_Controller.exe
   - it auto-detects everything and FIXES the bridge paths for this PC.
5. Click START ALL  (or press Ctrl+Shift+N anywhere).
   Dashboards:  http://127.0.0.1:8080  and  http://127.0.0.1:8090.

The controller never places/modifies/cancels orders. Full docs:
crypto-clob\\LAUNCHER_README.md
"""


if __name__ == "__main__":
    raise SystemExit(main())