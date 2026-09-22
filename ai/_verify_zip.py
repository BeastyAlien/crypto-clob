# -*- coding: utf-8 -*-
import sys
import zipfile
from pathlib import Path

zip_path = sys.argv[1] if len(sys.argv) > 1 else None
if not zip_path:
    cands = sorted(Path(r"C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common").glob("NOBI_Setup_*.zip"))
    zip_path = str(cands[-1]) if cands else None
if not zip_path:
    print("no NOBI_Setup zip found")
    raise SystemExit(1)

z = zipfile.ZipFile(zip_path)
names = z.namelist()
checks = ["INSTALL_FIRST.txt",
          "crypto-clob/dist/NOBI_Controller.exe",
          "crypto-clob/_runtime/python/pythonw.exe",
          "crypto-clob/nobi_bridge.py",
          "crypto-clob/nobi_ai.py",
          "crypto-clob/nobi_config.json",
          "crypto-clob/start_all.bat",
          "crypto-clob/MT5_files/NobiScalpTrader.ex5",
          "crypto-clob/ai/brain_server.py",
          "crypto-clob/ai/brain_dashboard.html",
          "crypto-clob-ui/start_all.bat",
          "crypto-clob/LAUNCHER_README.md"]
for c in checks:
    print(("OK       " if c in names else "MISSING  ") + c)
bad = [n for n in names if n.endswith((".jsonl", ".log")) or "/data" in n or "/data-ui" in n]
print("accidentally packed data/log files:", len(bad))
print("total files:", len(names))