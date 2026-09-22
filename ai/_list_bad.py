# -*- coding: utf-8 -*-
import zipfile
import sys
from pathlib import Path

zpath = sys.argv[1]
z = zipfile.ZipFile(zpath)
names = z.namelist()
bad = [n for n in names if n.endswith((".jsonl", ".log")) or "/data" in n or "/data-ui" in n]
for n in bad:
    print(n)
outside = [n for n in bad if not n.startswith("crypto-clob/_runtime/")]
print("outside portable python:", len(outside))