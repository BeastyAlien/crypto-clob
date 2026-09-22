# NOBI Trading Rig — Setup on a NEW PC (5-minute version)

This package contains everything: the order-flow engine, the bridge, the
dashboard, the control-center app (`.exe`, no Python needed), the EA and the
chart template. Do these in order on the new machine (Windows 10/11, 64-bit).

---
## Step 1 — Unzip
Unzip `NOBI_Setup.zip` to `D:\nobi\` (anywhere is fine). You should see:

```
D:\nobi\crypto-clob\        engine + bridge + config + app (dist\NOBITradingCenter.exe)
D:\nobi\crypto-clob-ui\     dashboard + start_all.bat
D:\nobi\MT5_files\          NobiScalpTrader.ex5/.mq5 + chart template
```

**Keep the two folders as siblings** — `crypto-clob-ui` must sit next to
`crypto-clob` (the app and start_all.bat rely on that layout).

---
## Step 2 — Install MetaTrader 5
Download from your broker (Exness portal → MetaTrader 5). Install, run, and
**log into your account once** (so the terminal creates its data folder):
`C:\Users\<user>\AppData\Roaming\MetaQuotes\Terminal\<NEW-HASH>\MQL5\Files\`.

---
## Step 3 — Copy the EA + template into MT5
From `D:\nobi\MT5_files\` copy:

| file | destination |
|---|---|
| `NobiScalpTrader.ex5` | `<NEW terminal>\MQL5\Experts\Advisors\` |
| `NobiScalpTrader.mq5` | `<NEW terminal>\MQL5\Experts\Advisors\` |
| `NobiScalpTrader_BTCUSD.tpl` | `<NEW terminal>\MQL5\Profiles\Templates\` |

(if the terminal is running, restart it so it picks up the new files)

---
## Step 4 — Fix the config paths (the only manual edit)
Open a command prompt in `D:\nobi\crypto-clob\` and run:

```
python fix-paths.py
```

It finds the newest MT5 data folder and rewrites `signal_file` / `anchor_file`
in `nobi_config.json` to point at THIS PC's terminal. (If it picked the wrong
folder, pass it explicitly: `python fix-paths.py --terminal "C:\...\Terminal\<HASH>"`)

---
## Step 5 — Attach the EA and enable trading
1. Open MT5 → menu **File → Open Data Folder** note the path (sanity check).
2. Open a **BTCUSDm, H1** chart.
3. Navigator → **Expert Advisors** → drag **NobiScalpTrader** onto the chart.
   (Or: chart right-click → Templates → Apply **NobiScalpTrader_BTCUSD**.)
4. In EA inputs set: `InpEnableTrading = true`, `InpUseCommon = false`,
   `InpSignalFile = nobi_signal.sig`, `InpMagic = 77812`.
5. **Enable Algo Trading** (toolbar button turns green) and
   **Tools → Options → Expert Advisors → Allow Expert Advisors to trade**.
   Without this the EA logs `program has no trading permission`.

---
## Step 6 — Run the app
Double-click `D:\nobi\crypto-clob\dist\NOBITradingCenter.exe`.

1. Click **Start all** → watch Engine/Bridge/Dashboard turn green.
2. Click **Start MT5**.
3. Check the **Latest signal** panel — a fresh signal appears once per
   30-min epoch; the EA reacts within ~1 s of a new serial.
4. Verify in MT5 Journal: `NobiScalpTrader ready ... lastSerial=…` then
   heartbeats every 30 s.

---
## Step 7 — Make it 24/7 (VPS recommended)
1. In the app tick **Auto-start at logon** (scheduled task).
2. Windows: Settings → System → Power → **never sleep**; enable auto-login.
3. On a VPS, remote in / dashboard at `http://<VPS-IP>:8080/dashboard.html`.

---
## Troubleshooting
| Symptom | Fix |
|---|---|
| App shows all STOPPED after Start all | folders not siblings / renamed `crypto-clob-ui` |
| EA logs "no trading permission" | Step 5.5 Algo Trading / Options checkbox |
| EA reads a stale serial and never trades | re-run `fix-paths.py`; check `InpUseCommon=false` |
| No signals for hours | Engine or Bridge crashed — check the app's log tail; verify internet reachability of the exchanges (some are region-blocked) |

---
## New in this build
- The app main window now carries everything: **Start MT5**, **Download MT5**
  (opens the official MetaQuotes download page) and **Login** (opens a dialog that
  launches MT5 with your server + login + password via the terminal CLI - the
  password is passed once and is NEVER saved by the app).
- A **Streams** panel lets you pick which symbol (BTC-USD / ETH-USD / SOL-USD /
  XRP-USD / DOGE-USD / ADA-USD / LINK-USD) to stream and which of the 7 brokers
  (binance, bybit, okx, kraken, coinbase, bitstamp, bitfinex) to use, then press
  **Apply stream**. It writes `config.json`, restarts engine/bridge/dashboard, and
  the dashboard title follows the selected symbol.
- The app works like a normal desktop app and is bundled as one `.exe`; the EA,
  the JSON dashboard, the `.bat` launcher and the Python engine/bridge all run in
  the background automatically.

## Reminders
- This is a research heuristic on public order-flow data — **test in the
  Strategy Tester first**, one backtest is not proof of profitability.
- The `.exe` is unsigned (no code-signing cert). Windows may warn: click
  *More info → Run anyway* — it is your own file.