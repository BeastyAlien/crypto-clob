# -*- coding: utf-8 -*-
"""Start nobi_bridge.py detached with logging to bridge.err.log.

Run from the crypto-clob folder:
    python start_bridge_logged.py
Returns immediately; the bridge keeps running after this exits.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "bridge.err.log")

if sys.platform == "win32":
    # Detached process on Windows: start_new_session + STARTUPINFO
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    with open(LOG, "ab", 0) as f:
        p = subprocess.Popen(
            [sys.executable, "nobi_bridge.py", "--duration", "0"],
            cwd=HERE,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            startupinfo=si,
        )
else:
    with open(LOG, "ab", 0) as f:
        p = subprocess.Popen(
            [sys.executable, "nobi_bridge.py", "--duration", "0"],
            cwd=HERE,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
print("bridge started pid=%d logged to %s" % (p.pid, LOG))