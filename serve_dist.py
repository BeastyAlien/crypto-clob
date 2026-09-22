# -*- coding: utf-8 -*-
"""Serve the dist folder over LAN on port 8123 (threaded, survives browser resets).

Run:  python serve_dist.py
Serves every file in ...\crypto-clob\dist on 0.0.0.0:8123 so any PC on the
same network can download the build via  http://<this-PC-IP>:8123/
"""
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
HOST = "0.0.0.0"
PORT = 8123

if not os.path.isdir(DIST):
    print("dist folder not found: %s" % DIST)
    sys.exit(1)

handler = partial(SimpleHTTPRequestHandler, directory=DIST)
httpd = ThreadingHTTPServer((HOST, PORT), handler)
print("serving %s on http://0.0.0.0:%d  (threaded)" % (DIST, PORT))
try:
    httpd.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    httpd.server_close()