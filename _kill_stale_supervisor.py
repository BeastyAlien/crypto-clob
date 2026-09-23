# -*- coding: utf-8 -*-
"""One-shot helper used by start_on_boot.bat.

Terminates any running NOBI watchdog (supervise.py) so the boot script can
start exactly one fresh instance. Safe by construction: it matches the
process command line for 'supervise.py' ONLY - Bridge/Engine/UI/Brain/Gate
python processes are never touched.

Optional positional argument: a custom command-line pattern (used for tests).
"""
import subprocess
import sys

DEFAULT_PATTERN = r"supervise\.py"


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATTERN
    cmd = (
        'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
        "Where-Object { $_.CommandLine -match '" + pattern + "' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            text=True, timeout=30)
    except Exception as exc:
        print("guard error: %s" % exc)
        return 1
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    for pid in pids:
        try:
            subprocess.check_call(["taskkill", "/F", "/PID", str(pid)],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=15)
            print("killed stale supervise.py pid=%d" % pid)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())