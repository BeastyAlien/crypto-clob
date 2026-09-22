# NOBI Controller — one-click / one-key setup (bridge → AI optimized)

Portable Windows controller for the whole NOBI stack. **One click** (or one
global hotkey) starts everything: redundant crypto-clob engines → OFI bridge
→ `nobi_signal.sig` → the MetaTrader 5 EA, plus the AI brain (collector +
hourly "think" that keeps optimizing the strategy settings).

## What you get
- `dist\NOBI_Controller.exe` — standalone GUI app (Python bundled, no
  installation needed on the target PC)
- `_runtime\python\` — portable 64-bit Python included (used to run the
  python engines/brain)
- `nobi_launcher.pyw` — source version (if you prefer running from source)
- `start_nobi.bat` — double-click launcher using the portable runtime
- `prepare_portable.bat` — (other PCs) bundle a python folder into
  `_runtime\python` once, so no Python install is needed
- `build_launcher.bat` — rebuild `NOBI_Controller.exe` after changes

## Install on any Windows PC (only MetaTrader 5 required)
1. Install MetaTrader 5 and log in.
2. Copy the whole `crypto-clob` folder and the `crypto-clob-ui` folder into
   `%APPDATA%\MetaQuotes\Terminal\Common\` (create the `crypto-clob-ui`
   folder there as a sibling). Layout:
   ```
   C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\Common\
       crypto-clob\      (engines, bridge, brain, controller, _runtime)
       crypto-clob-ui\   (dashboard + data-ui)
   ```
3. Pick EITHER:
   - **Use the EXE** (`crypto-clob\dist\NOBI_Controller.exe`) — double-click, done.
   - Or if you must rebuild: install Python 3.14 64-bit elsewhere, run
     `prepare_portable.bat`, then `start_nobi.bat`.
4. Place `NobiScalpTrader.ex5` (from `crypto-clob\MT5_files\`) into the
   terminal's `MQL5\Experts\Advisors\` and attach it to the BTCUSDm chart.
5. On the first run the controller auto-detect must find `Common` + Python —
   the selftest line at the top shows the paths it picked.

## Buttons
| Button | What it does |
|---|---|
| **▶ START ALL** | runs the guarded launcher: engine + backup engine + bridge + brain console, then fires the brain `collect`/`think` tasks, then (if checked) applies the optimized bridge settings |
| **■ STOP ALL** | kills only crypto-clob python processes (engines/bridge/brain/console) — MetaTrader 5 and the EA are untouched |
| **⚙ Apply AI settings** | writes the brain's latest recommendation (epoch/min_abs/venue gate) into `nobi_config.json`; the bridge hot-reloads it without restart |
| **🖥 Dashboards** | opens the engine dashboard (8080) + the brain console (8090) |
| **⟳ Refresh** | re-check every component status (auto-refresh every 3 s anyway) |

## Global hotkeys (work from anywhere once the app is open)
- `Ctrl+Shift+N` — START ALL
- `Ctrl+Shift+X` — STOP ALL
- `Ctrl+Shift+D` — open both dashboards

## Safety
- The controller only starts/stops local python processes and edits
  `nobi_config.json` (signal generation). It never places/modifies/cancels
  trading orders and never reads account data.
- "Apply AI settings" touches the BRIDGE only — EA inputs (SL/TP/lot) stay
  manual, always review brain recommendations in the console (8090).

## Troubleshooting
- "crypto-clob not found" → folders must be siblings under `Common\`.
- "no python" → run `prepare_portable.bat` (or it auto-bundles from the
  running python on the build machine).
- Watchdog: keep the `NOBI_Watchdog` scheduled task enabled; it re-arms
  engines/bridge/console every minute after a crash.