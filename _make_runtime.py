# -*- coding: utf-8 -*-
"""_make_runtime.py - bundle a portable 64-bit python into <crypto-clob>/_runtime.

Used by prepare_portable.bat (and by the NOBI Controller automatically when
possible). Makes the whole kit run on any Windows with ONLY MetaTrader 5
installed - no separate Python installation needed.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_source() -> Path | None:
    if sys.executable and Path(sys.executable).is_file() and \
            Path(sys.executable).name.lower().startswith("python"):
        return Path(sys.executable).parent
    la = Path(os.environ.get("LOCALAPPDATA", "")) / "Python"
    if la.is_dir():
        cands = sorted(la.glob("pythoncore-*-64"))
        if cands:
            return cands[-1]
    return None


def main() -> int:
    here = Path(__file__).resolve().parent
    dst = here / "_runtime" / "python"
    if (dst / "pythonw.exe").is_file():
        print("portable runtime already present:", dst)
        return 0
    src = find_source()
    if src is None or not (src / "pythonw.exe").is_file():
        src = Path(input("enter the full path to a 64-bit Python folder: ").strip())
    if not (src / "pythonw.exe").is_file():
        print("no pythonw.exe found in", src)
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"copying {src} -> {dst}  (one-time, may take a minute)...")
    if os.name == "nt":
        subprocess.run(["robocopy", str(src), str(dst), "/E", "/NFL", "/NDL", "/NJH", "/NJS"],
                       check=False)
    else:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    print("portable runtime ready:", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())