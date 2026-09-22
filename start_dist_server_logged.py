# -*- coding: utf-8 -*-
"""Start serve_dist.py detached with logging to fileserver.err.log.

Run from the crypto-clob folder:
    python start_dist_server_logged.py
Returns immediately; the file server keeps running.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "fileserver.err.log")

si = subprocess.STARTUPINFO()
si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
with open(LOG, "ab", 0) as f:
    p = subprocess.Popen(
        [sys.executable, "serve_dist.py"],
        cwd=HERE,
        stdout=f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        startupinfo=si,
    )
print("file server started pid=%d logged to %s" % (p.pid, LOG))