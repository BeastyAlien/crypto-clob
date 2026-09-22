"""Start a single crypto-clob engine writing into crypto-clob-ui\\data-ui.

Guarded: refuses to start while any run.py python process is alive, so the
watchdog / start_all.bat can never create a second metrics writer (the root
cause of the signal-replay storm). Uses subprocess.Popen with a proper
argument list so paths containing spaces are never split.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "crypto-clob-ui" / "data-ui"


def _engine_running() -> bool:
    """True if any python process is running run.py."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'run.py' } | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            text=True, timeout=15)
        return int(out.strip() or "0") > 0
    except Exception:
        return True  # if we cannot check, do not risk a duplicate engine


def main() -> None:
    if _engine_running():
        print("run.py already running")
        return
    cmd = [sys.executable, "run.py", "--duration", "0", "--out", str(OUT)]
    with open(HERE / "engine.out.log", "ab") as o, \
         open(HERE / "engine.err.log", "ab") as e:
        p = subprocess.Popen(cmd, cwd=str(HERE), stdout=o, stderr=e,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(f"engine pid {p.pid}")


if __name__ == "__main__":
    main()