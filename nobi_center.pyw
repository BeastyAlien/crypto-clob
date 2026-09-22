# -*- coding: utf-8 -*-
"""NOBI Trading Center - console-less launcher.

Double-click nobi_center.pyw to run the app without a console window
(windows opens it with pythonw automatically).
"""
import os

_here = os.path.dirname(os.path.abspath(__file__))
_code = open(os.path.join(_here, "nobi_center.py"), "r", encoding="utf-8").read()
exec(compile(_code, "nobi_center.py", "exec"))