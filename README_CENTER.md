# NOBI Trading Center (Windows control panel)

A single-window Windows app that runs and monitors the whole crypto-clob
signal pipeline:

```
run.py  -->  nobi_bridge.py  -->  nobi_signal.sig  -->  MT5 EA (NobiScalpTrader)
```

## Run

- **Double-click `nobi_center.pyw`** (no console window), or
- `python nobi_center.py`

Requires only Python 3 + tkinter (included in standard installs).

## What it shows

| Component | Tracked process |
|---|---|
| Engine | `run.py --duration 0` |
| Bridge | `nobi_bridge.py --duration 0` |
| Dashboard | `ui_server.py` (http://127.0.0.1:8080/dashboard.html) |
| MT5 | terminal64.exe (best-effort detection) |
| Latest signal | serial \| UTC time \| prev OFI -> now OFI \| BUY/SELL |

The **Start all** button re-uses `crypto-clob-ui\start_all.bat`, which is
idempotent - it only starts the parts that are not already running.

## 24/7 setup

1. Check **Auto-start at logon** - installs a scheduled task
   (`NOBI_TradingCenter`) that starts this app when Windows logs in.
2. In Windows: Settings -> System -> Power -> disable sleep, enable
   auto-login, and set Windows Update to a maintenance window.
3. Press **Start MT5** so the terminal (and the attached EA) come back.
   Remember: the terminal needs *Algo Trading* enabled (toolbar) and
   Tools -> Options -> Expert Advisors -> allow trading, or the EA
   will log "program has no trading permission" and not trade.

## Build a standalone .exe

```bat
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name NOBITradingCenter nobi_center.py
```

The app appears in `dist\NOBITradingCenter.exe`. Copy it anywhere on a
Windows machine; it will find the pipeline via the config file paths.
Python 3.13/3.14: if PyInstaller complains, create a venv with Python 3.12
and rebuild there - the app itself is version-agnostic stdlib.

## Safety

This app only starts/stops local processes and reads local files. It never
places, modifies or cancels orders. Signals are heuristics from public
exchange order-flow data - validate with the Strategy Tester before relying
on live results.