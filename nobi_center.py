# -*- coding: utf-8 -*-
"""NOBI Trading Center - local control panel + first-run setup wizard.

A single-window Windows app (pure Python standard library, tkinter GUI) that:

  * First-run SETUP WIZARD:
      1. Welcome
      2. Account details (server + login; password is entered into MT5
         directly and is NEVER stored by this app)
      3. MetaTrader 5 detection (auto-detect / browse / open download page)
      4. Terminal data-folder detection (created by MT5 after first login)
      5. Installs the EA files + chart template into MT5 and fixes the
         signal/anchor paths in nobi_config.json automatically
      6. Starts engine + bridge + dashboard, launches MT5, verifies the
         dashboard is reachable and opens it
      7. Optional: Windows auto-start at logon
  * Save / resume: settings are kept in nobi_state.json next to this app,
    so reopening the app restores the wizard result and live status.
  * Main window: live status of run.py / nobi_bridge.py / ui_server.py,
    latest signal read by the EA, start/stop buttons, log tail.

This app only manages local processes and files. It never places,
modifies or cancels orders. Broker password is never persisted here.

Build as a standalone Windows app (bundles the EA files too):
    python -m pip install pyinstaller
    python -m PyInstaller --onefile --windowed --name NOBITradingCenter ^
        --add-data "MT5_files;MT5_files" nobi_center.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog
    _HAS_TK = True
except Exception as _e:                      # pragma: no cover - headless run
    _HAS_TK = False
    print("tkinter unavailable:", _e)


#--- path discovery --------------------------------------------------------+
def _meipass_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return None


def _discover_project():
    """Locate the crypto-clob project root regardless of launch mode."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    seen = set()
    candidates = [base,
                  os.path.dirname(base),
                  os.path.dirname(os.path.dirname(base)),
                  os.path.join(base, "crypto-clob"),
                  os.path.join(os.path.dirname(base), "crypto-clob")]
    for p in candidates:
        p = os.path.normpath(p)
        if p in seen:
            continue
        seen.add(p)
        if os.path.isfile(os.path.join(p, "nobi_config.json")):
            ui = os.path.normpath(os.path.join(os.path.dirname(p), "crypto-clob-ui"))
            return p, ui
    return base, os.path.normpath(os.path.join(base, "..", "crypto-clob-ui"))


BASE_DIR, UI_DIR      = _discover_project()
CONFIG_PATH           = os.path.join(BASE_DIR, "nobi_config.json")
STATE_PATH            = os.path.join(BASE_DIR, "nobi_state.json")
START_ALL             = os.path.join(UI_DIR, "start_all.bat")

# keyword in the python command line -> (friendly label, best log file)
PROC_MARKS = {
    "run.py":         ("Engine (run.py)",        os.path.join(BASE_DIR, "engine.out.log")),
    "nobi_bridge.py": ("Bridge (nobi_bridge)",   os.path.join(BASE_DIR, "bridge.err.log")),
    "ui_server.py":   ("Dashboard (ui_server)",  os.path.join(UI_DIR, "ui.err.log")),
}

MT_CANDIDATES = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal5.exe",
    r"C:\Program Files\MetaTrader 5\terminal.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal5.exe",
]

SINGLE_INSTANCE_PORT = 47812                 # keep one copy of the app running
DASHBOARD_URL        = "http://127.0.0.1:8080/dashboard.html"
MT5_DOWNLOAD_URL     = "https://download.mql5.com/terminal5"

#--- stream selection -----------------------------------------------------+
EXCHANGES = ["binance", "bybit", "okx", "kraken", "coinbase", "bitstamp", "bitfinex"]

# per-venue tickers for each supported symbol (mirrors config.json "symbols")
SYMBOL_PRESETS = {
    "BTC-USD":  {"binance": "btcusdt",   "bybit": "BTCUSDT",  "okx": "BTC-USDT",
                 "kraken": "XBT/USD",    "coinbase": "BTC-USD", "bitstamp": "btcusd",
                 "bitfinex": "tBTCUSD"},
    "ETH-USD":  {"binance": "ethusdt",   "bybit": "ETHUSDT",  "okx": "ETH-USDT",
                 "kraken": "ETH/USD",    "coinbase": "ETH-USD", "bitstamp": "ethusd",
                 "bitfinex": "tETHUSD"},
    "SOL-USD":  {"binance": "solusdt",   "bybit": "SOLUSDT",  "okx": "SOL-USDT",
                 "kraken": "SOL/USD",    "coinbase": "SOL-USD", "bitstamp": "solusd",
                 "bitfinex": "tSOLUSD"},
    "XRP-USD":  {"binance": "xrpusdt",   "bybit": "XRPUSDT",  "okx": "XRP-USDT",
                 "kraken": "XRP/USD",    "coinbase": "XRP-USD", "bitstamp": "xrpusd",
                 "bitfinex": "tXRPUSD"},
    "DOGE-USD": {"binance": "dogeusdt",  "bybit": "DOGEUSDT", "okx": "DOGE-USDT",
                 "kraken": "DOGE/USD",   "coinbase": "DOGE-USD", "bitstamp": "dogeusd",
                 "bitfinex": "tDOGEUSD"},
    "ADA-USD":  {"binance": "adausdt",   "bybit": "ADAUSDT",  "okx": "ADA-USDT",
                 "kraken": "ADA/USD",    "coinbase": "ADA-USD", "bitstamp": "adausd",
                 "bitfinex": "tADAUSD"},
    "LINK-USD": {"binance": "linkusdt",  "bybit": "LINKUSDT", "okx": "LINK-USDT",
                 "kraken": "LINK/USD",   "coinbase": "LINK-USD", "bitstamp": "linkusd",
                 "bitfinex": "tLINKUSD"},
}


#--- state (save / resume) -------------------------------------------------+
def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    return st


def save_state(**kw) -> None:
    st = load_state()
    st.update(kw)
    st["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=2)
    except OSError as exc:
        print("state save failed:", exc)


#--- small helpers ---------------------------------------------------------+
def read_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    return cfg


def tail_file(path: str, chars: int = 2200) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > chars:
                fh.seek(size - chars)
            data = fh.read()
        return data.decode("utf-8", "replace").rstrip()
    except OSError:
        return ""


def find_python_procs() -> dict:
    found: dict = {}
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return found
    for raw in out.splitlines()[1:]:
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        pid = parts[1].strip()
        cmd = ",".join(parts[2:])
        if not pid.isdigit():
            continue
        for key in PROC_MARKS:
            if key in cmd and key not in found:
                found[key] = int(pid)
    return found


def start_all() -> str:
    if not os.path.isfile(START_ALL):
        return "start_all.bat not found: %s" % START_ALL
    try:
        subprocess.run([START_ALL], cwd=UI_DIR, timeout=40,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "Start all launched (idempotent - missing parts only)."
    except Exception as exc:
        return "Start all failed: %s" % exc


def stop_all() -> str:
    killed = []
    for key, pid in find_python_procs().items():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed.append(key)
        except Exception:
            pass
    if killed:
        return "Stopped: " + ", ".join(killed) + "."
    return "Nothing to stop."


def read_engine_config() -> dict:
    """Read the ENGINE config (config.json: symbol + per-venue symbols + exchanges)."""
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def write_engine_config(cfg: dict) -> str:
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        return "config.json written"
    except OSError as exc:
        return "config write failed: %s" % exc


def apply_stream_selection(symbol: str, exchanges: list) -> str:
    """Write symbol + broker selection into config.json and restart the pipeline."""
    preset = SYMBOL_PRESETS.get(symbol)
    if not preset:
        return "unknown symbol preset: %s" % symbol
    want = set(exchanges)
    picks = [e for e in EXCHANGES if e in want]
    if not picks:
        return "no exchange selected"
    cfg = read_engine_config()
    cfg["symbol"] = symbol
    cfg["symbols"] = dict(preset)
    cfg["exchanges"] = picks
    msg = write_engine_config(cfg)
    if msg.startswith("config"):
        stop_all()
        time.sleep(1.2)
        msg += " | " + start_all()
    return "%s | stream: %s on %s" % (msg, symbol, ", ".join(picks))


def read_signal() -> dict:
    cfg = read_config()
    path = cfg.get("signal_file", "")
    res = {"path": path, "ok": False, "serial": "-", "utc": "-",
           "prev": "-", "now": "-", "side": "-", "age_s": None}
    if not path or not os.path.isfile(path):
        return res
    try:
        text = open(path, "r", encoding="utf-8").read().strip()
    except OSError:
        return res
    parts = text.split("|")
    if len(parts) >= 5:
        res.update({"ok": True, "serial": parts[0], "utc": parts[1],
                    "prev": parts[2], "now": parts[3], "side": parts[4]})
    try:
        res["age_s"] = int(time.time() - os.path.getmtime(path))
    except OSError:
        pass
    return res


def find_metatrader() -> str:
    for path in MT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def find_terminal_roots() -> list:
    """Newest-first list of Terminal\\<HASH> folders that contain MQL5\\Files."""
    roots = []
    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not base.is_dir():
        return roots
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if (p / "MQL5" / "Files").is_dir():
            try:
                mtime = (p / "MQL5" / "Files").stat().st_mtime
            except OSError:
                mtime = 0.0
            roots.append((str(p), mtime))
    roots.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in roots]


def ea_bundle_dir() -> str:
    """Where the EA .ex5/.mq5/.tpl live in this deployment."""
    meipass = _meipass_dir()
    if meipass:
        cand = os.path.join(meipass, "MT5_files")
        if os.path.isdir(cand):
            return cand
    for cand in [os.path.join(BASE_DIR, "..", "MT5_files"),
                 os.path.join(BASE_DIR, "MT5_files"),
                 os.path.join(BASE_DIR, "mt5"),]:
        if os.path.isdir(cand):
            return cand
    return ""


def install_ea_files(terminal_root: str) -> str:
    """Copy EA + template into the terminal data folder. Returns report."""
    if not terminal_root:
        return "no terminal folder"
    src = ea_bundle_dir()
    if not src:
        return "EA bundle folder not found"
    files = [("NobiScalpTrader.ex5", "Experts\\Advisors"),
             ("NobiScalpTrader.mq5", "Experts\\Advisors"),
             ("NobiScalpTrader_BTCUSD.tpl", "Profiles\\Templates")]
    done, missing = [], []
    for name, sub in files:
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            missing.append(name)
            continue
        d = os.path.join(terminal_root, "MQL5", sub)
        os.makedirs(d, exist_ok=True)
        shutil.copy2(s, os.path.join(d, name))
        done.append(name)
    msg = "installed: " + ", ".join(done)
    if missing:
        msg += " | missing: " + ", ".join(missing)
    return msg


def fix_config_paths(terminal_root: str) -> str:
    """Repoint signal_file/anchor_file in nobi_config.json at the terminal."""
    mql_files = os.path.join(terminal_root, "MQL5", "Files")
    os.makedirs(mql_files, exist_ok=True)
    cfg = read_config()
    cfg["signal_file"] = os.path.join(mql_files, "nobi_signal.sig")
    cfg["anchor_file"] = os.path.join(mql_files, "nobi_anchor.sig")
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        return "paths fixed -> " + cfg["signal_file"]
    except OSError as exc:
        return "fix paths failed: %s" % exc


def dashboard_ok(timeout: float = 2.5) -> bool:
    try:
        with urlopen(DASHBOARD_URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def dashboard_url() -> str:
    """Dashboard URL, carrying the currently streamed symbol as a query param."""
    sym = read_engine_config().get("symbol", "BTC-USD") or "BTC-USD"
    return "%s?symbol=%s" % (DASHBOARD_URL, sym)


def schtasks_status(task: str = "NOBI_TradingCenter") -> bool:
    try:
        out = subprocess.run(["schtasks", "/Query", "/TN", task, "/FO", "CSV"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0 and "ERROR" not in out.stdout
    except Exception:
        return False


def schtasks_toggle(enable: bool) -> str:
    task = "NOBI_TradingCenter"
    target = os.path.join(BASE_DIR, "nobi_center.pyw")
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    if enable:
        if not os.path.isfile(target):
            return "nobi_center.pyw missing - autostart not enabled."
        cmd = ["schtasks", "/Create", "/TN", task,
               "/TR", '"%s" "%s"' % (exe, target),
               "/SC", "ONLOGON", "/RL", "LOWEST", "/F"]
    else:
        cmd = ["schtasks", "/Delete", "/TN", task, "/F"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "Enabled at logon." if enable else "Autostart removed."
    except Exception as exc:
        return "Autostart error: %s" % exc


def fmt_age(secs) -> str:
    if secs is None:
        return "unknown"
    if secs < 0:
        return "future?!"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return "%dh %02dm" % (h, m)


def launch_mt5(path: str) -> str:
    if not path or not os.path.isfile(path):
        return "MetaTrader 5 not found"
    try:
        subprocess.Popen([path])
        return "MetaTrader 5 launching: %s" % path
    except Exception as exc:
        return "MT5 launch failed: %s" % exc


def launch_mt5_login(path: str, server: str, login: str, password: str) -> str:
    """Launch MT5 and log into the account using terminal CLI args.

    The password is passed once on the command line and is never persisted.
    If the terminal is already running, CLI login is ignored -> instruct the
    user to use the terminal's built-in login dialog.
    """
    if not path or not os.path.isfile(path):
        return "MetaTrader 5 not found - use Download or Browse first."
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if os.path.basename(path).lower() in out.lower():
            return "MT5 is already running - log in with its built-in dialog (password not stored)."
    except Exception:
        pass
    cmd = [path, "/account:%s" % login, "/password:%s" % password, "/server:%s" % server]
    try:
        subprocess.Popen(cmd)
        return "Launching MT5 and logging in to %s (%s) ..." % (server, login)
    except Exception as exc:
        return "MT5 login launch failed: %s" % exc


#--- first-run setup wizard ------------------------------------------------+
class SetupWizard:
    def __init__(self, root: tk.Tk, state: dict) -> None:
        self.root = root
        self.st = state
        self.step = 0
        root.title("NOBI Trading Center - Setup")
        root.geometry("720x520")
        root.minsize(660, 480)
        top = ttk.Frame(root)
        top.pack(fill=tk.X)
        ttk.Label(top, text="NOBI Trading Center  -  first-run setup",
                  font=("Segoe UI", 13, "bold")).pack(pady=6)
        self.card = ttk.Frame(root)
        self.card.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self.btns = ttk.Frame(root)
        self.btns.pack(fill=tk.X, padx=12, pady=8)
        self.back = ttk.Button(self.btns, text="< Back", command=self.go_back)
        self.next = ttk.Button(self.btns, text="Next >", command=self.go_next)
        self.msgs = []
        self.widgets = {}
        self.build_card()

    #--- card rendering ---------------------------------------------------+
    def clear(self) -> None:
        for w in self.card.winfo_children():
            w.destroy()
        self.msgs = []
        self.widgets = {}

    def label(self, text, bold=False, fg=None, size=10):
        w = ttk.Label(self.card, text=text, wraplength=640, justify=tk.LEFT,
                      font=("Segoe UI", size, "bold" if bold else "normal"))
        if fg:
            w.configure(foreground=fg)
        w.pack(anchor=tk.W, pady=(4, 10))
        return w

    def entry(self, key, value="", show=None):
        f = ttk.Frame(self.card)
        f.pack(fill=tk.X, pady=2)
        var = tk.StringVar(value=value)
        self.widgets[key] = var
        e = ttk.Entry(f, textvariable=var, show=show)
        e.pack(fill=tk.X)
        return var

    def entry_row(self, key, caption, value="", show=None, tip=""):
        f = ttk.Frame(self.card)
        f.pack(fill=tk.X, pady=3)
        ttk.Label(f, text=caption, width=22).pack(side=tk.LEFT)
        var = tk.StringVar(value=value)
        self.widgets[key] = var
        ttk.Entry(f, textvariable=var, show=show).pack(side=tk.LEFT, fill=tk.X, expand=True)
        if tip:
            ttk.Label(f, text=tip, foreground="#666666").pack(side=tk.LEFT, padx=6)
        return var

    def info(self, text, fg="#1f6feb"):
        w = ttk.Label(self.card, text=text, wraplength=640, justify=tk.LEFT,
                      font=("Consolas", 9), foreground=fg)
        w.pack(anchor=tk.W, pady=(2, 6))
        self.msgs.append(w)
        return w

    def button(self, text, cmd):
        b = ttk.Button(self.card, text=text, command=cmd)
        b.pack(anchor=tk.W, pady=4)
        return b

    #--- steps ------------------------------------------------------------+
    def build_card(self) -> None:
        if self.step == 0:
            self.step_welcome()
        elif self.step == 1:
            self.step_account()
        elif self.step == 2:
            self.step_mt5()
        elif self.step == 3:
            self.step_terminal()
        elif self.step == 4:
            self.step_ready()

    def step_welcome(self) -> None:
        self.label("Welcome!", bold=True, size=15)
        self.label("This wizard sets up the whole NOBI pipeline on this PC:\n\n"
                   "  1. Your trading account details (MetaTrader 5 account)\n"
                   "  2. MetaTrader 5 - detects it, or opens the download page\n"
                   "  3. Terminal data folder - found automatically after login\n"
                   "  4. Installs the Expert Advisor + chart template + fixes config paths\n"
                   "  5. Starts engine + bridge + dashboard and launches MT5\n\n"
                   "Important: your broker password is entered into MetaTrader 5 itself "
                   "and is NEVER stored by this app.")
        self.label("You can re-run this wizard later from the main window (Settings button).",
                   fg="#666666")

    def step_account(self) -> None:
        self.label("Step 1 of 5 - Trading account", bold=True, size=13)
        self.label("These details are saved locally (nobi_state.json) so the app can resume. "
                   "The PASSWORD field is only a reminder - type it into MetaTrader 5 when it opens; "
                   "this app never saves it.")
        self.entry_row("server", "MetaTrader server", self.st.get("server", "Exness-MT5Trial9"))
        self.entry_row("login", "Account login", self.st.get("login", ""))
        self.entry_row("password", "Password (not saved)", "", show="*",
                       tip="entered in MT5 only")
        self.entry_row("magic", "EA magic number", str(self.st.get("magic", 77812)))
        self.entry_row("volume", "Volume (lots)", str(self.st.get("volume", 0.01)))
        self.entry_row("sl", "Stop loss (points)", str(self.st.get("sl", 100000)))
        self.entry_row("tp", "Take profit (points)", str(self.st.get("tp", 50000)))

    def step_mt5(self) -> None:
        self.label("Step 2 of 5 - MetaTrader 5", bold=True, size=13)
        path = self.st.get("mt5_path") or find_metatrader()
        if path:
            self.info("MetaTrader 5 found: %s" % path, "#3bb44b")
        else:
            self.info("MetaTrader 5 not found in the standard install folders.", "#d9443f")
        self.button("Browse for terminal64.exe ...", self.browse_mt5)
        self.button("Open MetaTrader 5 download page", self.download_mt5)
        self.info("If you already installed it somewhere else, press the browse button "
                  "and pick terminal64.exe. Then press Next.")

    def step_terminal(self) -> None:
        self.label("Step 3 of 5 - Terminal data folder", bold=True, size=13)
        roots = find_terminal_roots()
        if roots:
            self.info("Detected: %s" % roots[0], "#3bb44b")
        else:
            self.info("No terminal data folder yet.\n"
                      "-> Open MetaTrader 5 and log into your account once\n"
                      "   (it creates the MQL5\\Files folder automatically),\n"
                      "   then press 'Check again'.", "#e0a72b")
        self.button("Check again", self.check_terminal)
        self.info("The EA and template will be copied into this folder when you press Next.")

    def step_ready(self) -> None:
        self.label("Step 4 of 5 - Apply settings", bold=True, size=13)
        self.label("Installing EA + fixing paths:")
        self.info("Press 'Apply' to copy the EA files into MT5 and point the "
                  "bridge at this PC's terminal folder.")
        self.button("Apply EA + fix paths", self.apply_all)
        self.label("Step 5 of 5 - Launch", bold=True, size=13)
        self.info("Then start everything and open the dashboard:")
        self.button("1) Start all (engine + bridge + dashboard)", self.do_start_all)
        self.button("2) Start MetaTrader 5", self.do_start_mt5)
        self.button("3) Open dashboard", self.do_open_dashboard)
        self.info("Dashboard is reachable: %s" % ("YES" if dashboard_ok() else "not yet"),
                  "#3bb44b" if dashboard_ok() else "#e0a72b")

    #--- actions -----------------------------------------------------------+
    def go_next(self) -> None:
        self.save_inputs()
        if self.step == 0:
            self.step = 1
        elif self.step == 1:
            self.step = 2
        elif self.step == 2:
            self.step = 3
        elif self.step == 3:
            self.step = 4
        elif self.step == 4:
            self.st["configured"] = True
            save_state(**self.st)
            self.root.destroy()
            return
        self.build_card()

    def go_back(self) -> None:
        if self.step == 0:
            return
        self.step -= 1
        self.build_card()

    def save_inputs(self) -> None:
        for k, var in self.widgets.items():
            self.st[k] = var.get()
        # configure Main window values
        for k in ("server", "login", "magic", "volume", "sl", "tp"):
            if k in self.st:
                save_state(**{k: self.st[k]})

    def browse_mt5(self) -> None:
        p = filedialog.askopenfilename(title="Select terminal64.exe",
                                       filetypes=[("MT5 terminal", "*.exe")],
                                       initialdir=r"C:\Program Files")
        if p:
            self.st["mt5_path"] = p
            self.info("Selected: %s" % p, "#3bb44b")
            save_state(**{"mt5_path": p})

    def download_mt5(self) -> None:
        webbrowser.open("https://download.mql5.com/terminal5")
        self.info("Download page opened. Install MT5, log in once, then come back "
                  "and press Next.", "#e0a72b")

    def check_terminal(self) -> None:
        roots = find_terminal_roots()
        if roots:
            self.st["terminal_root"] = roots[0]
            save_state(**{"terminal_root": roots[0]})
            self.info("Detected: %s" % roots[0], "#3bb44b")
        else:
            self.info("Still not found - log into MT5 once, then press again.", "#e0a72b")

    def apply_all(self) -> None:
        tr = self.st.get("terminal_root") or (find_terminal_roots() or [""])[0]
        if not tr:
            self.info("No terminal folder - step 3 needed.", "#d9443f")
            return
        msg1 = install_ea_files(tr)
        msg2 = fix_config_paths(tr)
        self.st["terminal_root"] = tr
        save_state(**{"terminal_root": tr, "configured": True})
        self.info("EA : " + msg1, "#3bb44b")
        self.info("CFG: " + msg2, "#3bb44b")

    def do_start_all(self) -> None:
        self.info(start_all())

    def do_start_mt5(self) -> None:
        p = self.st.get("mt5_path") or find_metatrader()
        self.info(launch_mt5(p))

    def do_open_dashboard(self) -> None:
        if dashboard_ok():
            webbrowser.open(dashboard_url())
        else:
            self.info("Dashboard not up yet - did you press 'Start all'?", "#e0a72b")


#--- MT5 login dialog -----------------------------------------------------+
class MT5LoginDialog:
    def __init__(self, parent: tk.Tk, state: dict) -> None:
        self.st = state
        top = tk.Toplevel(parent)
        top.title("MetaTrader 5 login")
        top.geometry("480x280")
        top.resizable(False, False)
        top.transient(parent)
        top.grab_set()
        f = ttk.Frame(top)
        f.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
        ttk.Label(f, text="MT5 login - the password is passed once to the terminal and "
                          "never saved by this app.", wraplength=430, justify=tk.LEFT,
                  foreground="#666666").pack(anchor=tk.W, pady=(0, 8))
        self.vars = {}
        for key, lbl in (("server", "Server"), ("login", "Account login"), ("password", "Password")):
            fr = ttk.Frame(f)
            fr.pack(fill=tk.X, pady=3)
            ttk.Label(fr, text=lbl, width=14).pack(side=tk.LEFT)
            var = tk.StringVar(value=str(self.st.get(key, "")))
            ttk.Entry(fr, textvariable=var, show="*" if key == "password" else None)\
                .pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.vars[key] = var
        self.msg = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.msg, foreground="#e0a72b",
                  wraplength=430, justify=tk.LEFT).pack(anchor=tk.W, pady=6)
        btns = ttk.Frame(top)
        btns.pack(fill=tk.X, padx=14, pady=(0, 12))
        ttk.Button(btns, text="Login", command=self.do_login).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Download MT5", command=self.download).pack(side=tk.RIGHT, padx=6)
        self.top = top

    def download(self) -> None:
        webbrowser.open(MT5_DOWNLOAD_URL)

    def do_login(self) -> None:
        server = self.vars["server"].get().strip()
        login = self.vars["login"].get().strip()
        password = self.vars["password"].get()
        if not server or not login or not password:
            self.msg.set("server, login and password are all required")
            return
        self.st["server"] = server
        self.st["login"] = login
        save_state(server=server, login=login)
        path = self.st.get("mt5_path") or find_metatrader()
        res = launch_mt5_login(path, server, login, password)
        self.vars["password"].set("")          # never keep the password in memory
        self.msg.set(res)
        if res.startswith("Launching"):
            self.top.after(1600, self.top.destroy)


#--- main window -----------------------------------------------------------+
class NobiCenterApp:
    def __init__(self, root: tk.Tk, state: dict) -> None:
        self.root = root
        self.st = state
        root.title("NOBI Trading Center")
        root.geometry("860x620")
        root.minsize(720, 520)

        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.procs = {}
        self.build_ui()
        self.refresh()
        self._tick()

    def build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X)
        ttk.Label(top, text="crypto-clob OFI signal pipeline",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, **pad)
        acct = self.st.get("server", "") + " / " + self.st.get("login", "")
        ttk.Label(top, text=("Account: " + acct) if acct not in (" / ", "/") else "",
                  foreground="#666666").pack(side=tk.RIGHT, **pad)

        grp = ttk.LabelFrame(self.root, text=" Components ", padding=10)
        grp.pack(fill=tk.X, padx=10, pady=6)
        self.status_vars = {}
        for key, (label, _log) in PROC_MARKS.items():
            row = ttk.Frame(grp)
            row.pack(fill=tk.X)
            self.status_vars[key] = tk.StringVar(value="...")
            ttk.Label(row, text=label, width=26).pack(side=tk.LEFT)
            ttk.Label(row, textvariable=self.status_vars[key], font=("Consolas", 9),
                      width=34, anchor=tk.W).pack(side=tk.LEFT, expand=True)

        mt = ttk.Frame(grp)
        mt.pack(fill=tk.X)
        self.mt_var = tk.StringVar(value="MT5: -")
        ttk.Label(mt, text="MetaTrader 5", width=26).pack(side=tk.LEFT)
        ttk.Label(mt, textvariable=self.mt_var, font=("Consolas", 9),
                  width=34, anchor=tk.W).pack(side=tk.LEFT, expand=True)
        ttk.Button(mt, text="Login", width=8, command=self.cmd_login_mt5).pack(side=tk.RIGHT, padx=2)
        ttk.Button(mt, text="Download", width=10, command=self.cmd_download_mt5).pack(side=tk.RIGHT, padx=2)
        ttk.Button(mt, text="Start MT5", width=10, command=self.cmd_start_mt5).pack(side=tk.RIGHT, padx=2)

        sig = ttk.LabelFrame(self.root, text=" Latest signal (what the EA reads) ", padding=10)
        sig.pack(fill=tk.X, padx=10, pady=6)
        self.sig_var = tk.StringVar(value="waiting for signal file ...")
        ttk.Label(sig, textvariable=self.sig_var, font=("Consolas", 10),
                  anchor=tk.W).pack(fill=tk.X)

        stm = ttk.LabelFrame(self.root, text=" Streams (symbol + brokers) ", padding=10)
        stm.pack(fill=tk.X, padx=10, pady=6)
        row1 = ttk.Frame(stm)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="Symbol", width=10).pack(side=tk.LEFT)
        ecfg = read_engine_config()
        cur_sym = ecfg.get("symbol", "BTC-USD")
        if cur_sym not in SYMBOL_PRESETS:
            cur_sym = "BTC-USD"
        self.stream_sym_var = tk.StringVar(value=cur_sym)
        ttk.Combobox(row1, textvariable=self.stream_sym_var, state="readonly",
                     values=list(SYMBOL_PRESETS.keys()), width=14).pack(side=tk.LEFT)
        ttk.Label(row1, text="   Brokers", width=12).pack(side=tk.LEFT)
        self.stream_ex_vars = {}
        active = set(ecfg.get("exchanges", EXCHANGES))
        row2 = ttk.Frame(stm)
        row2.pack(fill=tk.X)
        for i, ex in enumerate(EXCHANGES):
            var = tk.BooleanVar(value=ex in active)
            self.stream_ex_vars[ex] = var
            ttk.Checkbutton(row2, text=ex, variable=var).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Apply stream", command=self.cmd_apply_streams).pack(side=tk.RIGHT)
        self.stream_msg = tk.StringVar(
            value="Pick a symbol + brokers, then Apply stream (restarts engine / bridge / dashboard).")
        ttk.Label(stm, textvariable=self.stream_msg,
                  foreground="#666666").pack(anchor=tk.W, pady=(8, 0))

        btns = ttk.Frame(self.root)
        btns.pack(fill=tk.X, padx=10, pady=4)
        ttk.Button(btns, text="Start all", command=self.cmd_start_all).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Stop all", command=self.cmd_stop_all).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Open dashboard", command=self.cmd_dashboard).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Settings", command=self.cmd_settings).pack(side=tk.LEFT, padx=6)
        self.auto_var = tk.BooleanVar(value=schtasks_status())
        ttk.Checkbutton(btns, text="Auto-start at logon",
                        variable=self.auto_var, command=self.cmd_autostart).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Refresh", command=self.refresh).pack(side=tk.RIGHT)

        logf = ttk.LabelFrame(self.root, text=" Bridge log (tail) ", padding=6)
        logf.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self.log_txt = scrolledtext.ScrolledText(logf, height=11, font=("Consolas", 9),
                                                 state=tk.DISABLED, wrap=tk.NONE)
        self.log_txt.pack(fill=tk.BOTH, expand=True)

    #--- commands ----------------------------------------------------------+
    def cmd_start_all(self) -> None:
        self.flash(start_all())

    def cmd_stop_all(self) -> None:
        self.flash(stop_all())

    def cmd_dashboard(self) -> None:
        webbrowser.open(dashboard_url())

    def cmd_settings(self) -> None:
        st = load_state()
        SetupWizard(tk.Toplevel(self.root), st)

    def cmd_autostart(self) -> None:
        on = self.auto_var.get()
        self.flash(schtasks_toggle(on))
        self.auto_var.set(schtasks_status())

    def cmd_start_mt5(self) -> None:
        self.flash(launch_mt5(self.st.get("mt5_path") or find_metatrader()))

    def cmd_download_mt5(self) -> None:
        webbrowser.open(MT5_DOWNLOAD_URL)
        self.flash("Opening MetaTrader 5 download page")

    def cmd_login_mt5(self) -> None:
        MT5LoginDialog(self.root, self.st)

    def cmd_apply_streams(self) -> None:
        sym = self.stream_sym_var.get()
        picks = [e for e, v in self.stream_ex_vars.items() if v.get()]
        res = apply_stream_selection(sym, picks)
        self.stream_msg.set(res)
        save_state(stream_symbol=sym, stream_brokers=picks)
        self.flash(res)
        self.refresh()

    def flash(self, msg: str) -> None:
        self.root.title("NOBI Trading Center - " + msg)

    #--- refresh ------------------------------------------------------------+
    def refresh(self) -> None:
        self.procs = find_python_procs()
        running = set(self.procs.keys())
        for key, (_label, _log) in PROC_MARKS.items():
            if key in running:
                self.status_vars[key].set("RUNNING  pid=%d" % self.procs[key])
            else:
                self.status_vars[key].set("STOPPED")
        p = self.st.get("mt5_path") or find_metatrader()
        self.mt_var.set("found: " + os.path.basename(p) if p else "not found")

        sig = read_signal()
        if sig["ok"]:
            self.sig_var.set("serial %s | %s UTC | OFI %s -> %s | %s (file age %s)"
                             % (sig["serial"], sig["utc"], sig["prev"], sig["now"],
                                sig["side"], fmt_age(sig["age_s"])))
        else:
            self.sig_var.set("no signal file yet (%s)" % sig["path"] or CONFIG_PATH)

    def _tick(self) -> None:
        if self.auto_var.get() != schtasks_status():
            self.auto_var.set(schtasks_status())
        self.refresh()
        log = tail_file(os.path.join(BASE_DIR, "bridge.err.log"))
        if log:
            self.log_txt.configure(state=tk.NORMAL)
            self.log_txt.delete("1.0", tk.END)
            self.log_txt.insert(tk.END, log[-6000:])
            self.log_txt.configure(state=tk.DISABLED)
            self.log_txt.see(tk.END)
        self.root.after(2000, self._tick)


#--- entry ------------------------------------------------------------------+
def _single_instance() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _placeholder():
    pass


def main() -> None:
    if not _single_instance():
        if _HAS_TK:
            messagebox.showinfo("NOBI Trading Center", "The app is already running.")
        else:
            print("Already running.")
        return
    if not _HAS_TK:
        print("tkinter is required to run the GUI.")
        return

    state = load_state()
    root = tk.Tk()
    if not state.get("configured"):
        w = SetupWizard(root, state)
        root.mainloop()
        # after wizard closes the root is destroyed - start fresh
        state = load_state()
        root = tk.Tk()
    app = NobiCenterApp(root, state)
    root.mainloop()


if __name__ == "__main__":
    main()