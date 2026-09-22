# -*- coding: utf-8 -*-
"""nobi_launcher.pyw - one-click / one-key controller for the NOBI stack.

Starts + stops the whole pipeline with a single click or a global hotkey:

    engines (x2 redundant) -> OFI bridge -> nobi_signal.sig -> MT5 EA
    brain collector/think tasks -> ai/optimized settings -> bridge (apply)

It auto-locates:
    - MQL5 Common folder  (%%APPDATA%%\\MetaQuotes\\Terminal\\Common)
    - crypto-clob project (Common\\crypto-clob, Common\\crypto-clob-ui)
    - a Python interpreter (portable _runtime\\python, then pythoncore-3.14-64,
      then 'python' on PATH)

Global hotkeys (press anywhere, once registered):
    Ctrl+Shift+N   START ALL
    Ctrl+Shift+X   STOP ALL
    Ctrl+Shift+D   open dashboards (engine 8080 + brain 8090)

Never touches trading orders/account - it only starts/stops the local
python processes of this stack and reads their files.

Usage:
    pythonw nobi_launcher.pyw          (GUI)
    python  nobi_launcher.pyw --selftest   (headless: print detection, exit)
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_TITLE = "NOBI Controller"
VERSION = "1.0"
HOTKEYS = {"start": (1, "Ctrl+Shift+N"), "stop": (2, "Ctrl+Shift+X"), "dash": (3, "Ctrl+Shift+D")}
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
SW_HIDE = 0

CREATE_NO_WINDOW = 0x08000000
HIDE = subprocess.STARTUPINFO()
try:
    HIDE.dwFlags |= subprocess.STARTF_USESHOWWINDOW
except Exception:  # pragma: no cover
    pass

COLS = 46


# ----------------------------------------------------------------- detect --
def find_common() -> Path | None:
    appdata = os.environ.get("APPDATA", "")
    common = Path(appdata) / "MetaQuotes" / "Terminal" / "Common"
    if (common / "crypto-clob").is_dir():
        return common
    # allow side-by-side layout (Common folder next to this script)
    here = Path(__file__).resolve().parent
    if here.name == "crypto-clob" and (here / ".." / "crypto-clob-ui").resolve().is_dir():
        return here.parent
    if (here / "crypto-clob").is_dir():
        return here
    return None


def find_runtime() -> dict:
    """Locate python + launcher paths."""
    common = find_common()
    clob = common / "crypto-clob" if common else Path(__file__).resolve().parent
    ui = common / "crypto-clob-ui" if common else clob.parent / "crypto-clob-ui"
    py = None
    runtime = clob / "_runtime" / "python"
    for cand in (runtime / "pythonw.exe", runtime / "python.exe"):
        if cand.is_file():
            py = cand
            break
    if py is None:
        pythoncore = Path(os.environ.get("LOCALAPPDATA", "")) / "Python" / "pythoncore-3.14-64"
        for name in ("pythonw.exe", "python.exe"):
            c = pythoncore / name
            if c.is_file():
                py = c
                break
    if py is None:
        py = shutil.which("pythonw") or shutil.which("python")
    return {"common": common, "clob": clob, "ui": ui, "py": Path(py) if py else None,
            "runtime": runtime}


def run(args, timeout=60, cwd=None) -> str:
    """Run a command line list, capture combined output (no console flash)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, startupinfo=HIDE, creationflags=CREATE_NO_WINDOW)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as ex:
        return f"<run error: {ex}>"


def wmic_python_procs() -> list[tuple[str, str]]:
    """[(pid, commandline)] for all python.exe processes."""
    out = run(["wmic", "process", "where", "name='python.exe'",
               "get", "processid,commandline", "/format:list"], timeout=30)
    res, cmd, pid = [], "", ""
    for ln in out.splitlines():
        s = ln.strip()
        low = s.lower()
        if low.startswith("commandline="):
            cmd = s[len("commandline="):]
        elif low.startswith("processid="):
            pid = s[len("processid="):]
            if cmd:
                res.append((pid, cmd))
            cmd, pid = "", ""
    if cmd and pid:
        res.append((pid, cmd))
    return res


def status() -> dict:
    """component -> running bool."""
    procs = wmic_python_procs()
    pats = {"engine": r"ui_server", "backup": r"run\.py", "bridge": r"nobi_bridge",
            "console": r"brain_server"}
    out = {}
    for k, p in pats.items():
        out[k] = any(re.search(p, cmd, re.I) for _, cmd in procs)
    t1 = run(["schtasks", "/Query", "/TN", "NOBI_AI_Collect"], timeout=20)
    t2 = run(["schtasks", "/Query", "/TN", "NOBI_AI_Think"], timeout=20)
    out["tasks"] = "cannot find the file" not in t1.lower() + t2.lower()
    out["_procs"] = procs
    return out


# ------------------------------------------------------------------- ctl --
def start_all(rt: dict, also_apply: bool, log) -> None:
    bat = rt["ui"] / "start_all.bat"
    log(f"START: running {bat}")
    if bat.is_file():
        run(["cmd", "/c", str(bat)], timeout=120, cwd=str(rt["ui"]))
    else:
        log(f"  ! start_all.bat missing in {rt['ui']}")
    for task in ("NOBI_AI_Collect", "NOBI_AI_Think"):
        out = run(["schtasks", "/run", "/tn", task], timeout=30)
        log(f"  brain task {task}: {out.strip()[:60] or '(ok)'}")
    if also_apply:
        log("  auto-apply enabled -> applying optimized bridge settings")
        log(apply_ai(rt))
    log("START completed.")


def stop_all(rt: dict, log) -> None:
    procs = wmic_python_procs()
    pats = re.compile(r"ui_server|run\.py|nobi_bridge|brain_server|nobi_ai", re.I)
    killed = 0
    for pid, cmd in procs:
        if pats.search(cmd):
            run(["taskkill", "/F", "/PID", pid], timeout=20)
            killed += 1
            log(f"  stopped pid {pid}: {cmd[:80]}")
    log(f"STOP completed ({killed} processes).  (MT5/EA untouched)")


def apply_ai(rt: dict) -> str:
    if rt["py"] is None:
        return "apply: no python found"
    return run([str(rt["py"]), str(rt["clob"] / "nobi_ai.py"), "apply", "--bridge"],
               timeout=120, cwd=str(rt["clob"]))


def selftest() -> int:
    rt = find_runtime()
    print("NOBI Controller selftest")
    print(f"  common  : {rt['common'] or 'NOT FOUND'}")
    print(f"  clob    : {rt['clob']}")
    print(f"  ui      : {rt['ui']}")
    print(f"  python  : {rt['py'] or 'NOT FOUND'}")
    if not rt.get("common"):
        print("  ERROR: place crypto-clob + crypto-clob-ui under "
              "<APPDATA>\\MetaQuotes\\Terminal\\Common")
        return 1
    if rt["py"] is None:
        print("  WARN: no python found - _runtime\\python or pythoncore-3.14-64 or PATH")
    st = status()
    print("  status  : " + ", ".join(f"{k}={v}" for k, v in st.items() if not k.startswith("_")))
    return 0


def find_terminal_files() -> Path | None:
    """Locate the MT5 MQL5\\Files sandbox for THIS install (per-terminal hash)."""
    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not base.is_dir():
        return None
    cands = []
    for tdir in base.glob("*"):
        ff = tdir / "MQL5" / "Files"
        if ff.is_dir():
            cands.append((ff, tdir.stat().st_mtime))
    if not cands:
        return None
    for ff, _ in cands:                     # prefer install already using us
        if (ff / "nobi_signal.sig").exists():
            return ff
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[0][0]


def ensure_db_paths(rt: dict, log=None) -> None:
    """Rewrite nobi_config.json to point at THIS machine's folders.

    Prevents the classic stale-path bug (metrics_dir / signal_file pointing
    at another user/PC) that silently starves the bridge. Backs up first.
    """
    cfg = rt["clob"] / "nobi_config.json"
    if not cfg.is_file():
        return
    try:
        cur = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return
    ui = rt["ui"] / "data-ui"
    if not ui.is_dir():
        ui = rt["ui"]
    tfiles = find_terminal_files()
    fresh = dict(cur)
    changed = []
    if cur.get("metrics_dir") != str(ui):
        fresh["metrics_dir"] = str(ui)
        changed.append(f"metrics_dir -> {ui}")
    if tfiles is not None:
        if cur.get("signal_file") != str(tfiles / "nobi_signal.sig"):
            fresh["signal_file"] = str(tfiles / "nobi_signal.sig")
            changed.append("signal_file -> terminal sandbox")
        if cur.get("anchor_file") != str(tfiles / "nobi_anchor.sig"):
            fresh["anchor_file"] = str(tfiles / "nobi_anchor.sig")
            changed.append("anchor_file -> terminal sandbox")
    if changed:
        cfg.with_suffix(".json.portable-bak").write_text(
            json.dumps(cur, indent=4), encoding="utf-8")
        cfg.write_text(json.dumps(fresh, indent=4), encoding="utf-8")
        msg = "paths fixed for this PC: " + "; ".join(changed)
        if log:
            log("CONFIG: " + msg)
        else:
            print(msg)


# ----------------------------------------------------------- global keys --
class HotkeyThread(threading.Thread):
    """Registers global hotkeys and pushes events into a queue."""

    def __init__(self, q: queue.Queue):
        super().__init__(daemon=True)
        self.q = q
        self.ids = {1: 0x4E, 2: 0x58, 3: 0x44}   # N, X, D

    def run(self):
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            mod = MOD_CONTROL | MOD_SHIFT
            for hid, vk in self.ids.items():
                user32.RegisterHotKey(None, hid, mod, vk)
            # create this thread's message queue, then block on it
            user32.PeekMessageW(ctypes.byref(ctypes.c_void_p(0)), None, 0, 0, 1)

            class MSG(ctypes.Structure):
                _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                            ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_long),
                            ("time", ctypes.c_long), ("pt", ctypes.c_long * 2)]
            msg = MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    self.q.put(int(msg.wParam))
        except Exception:
            pass  # hotkeys unavailable -> in-window keys still work


# ------------------------------------------------------------------- gui --
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.rt = find_runtime()
        self.q: queue.Queue = queue.Queue()
        self.comps = ["engine", "backup", "bridge", "console", "tasks"]
        self.labels = {"engine": "engine (ui_server → data-ui)", "backup": "backup engine (run.py)",
                       "bridge": "OFI bridge (nobi_bridge)", "console": "brain console (8090)",
                       "tasks": "brain scheduled tasks"}
        root.title(f"{APP_TITLE} v{VERSION}")
        root.geometry("720x560")
        root.configure(bg="#0e1219")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        try:
            ensure_db_paths(self.rt, self.log)
        except Exception as ex:  # never block the UI on config problems
            self.log(f"CONFIG: path fix skipped ({ex})")
        self._refresh_status()
        self._refresh_status()
        self.root.after(3000, self._poll_loop)
        HotkeyThread(self.q).start()
        self.root.after(250, self._drain_queue)

    # -- widgets ---------------------------------------------------------
    def _build(self):
        f = ("Segoe UI", 10)
        bg, panel = "#0e1219", "#141a24"
        self.root.grid_columnconfigure(0, weight=1)

        head = tk.Label(self.root, text="NOBI CONTROLLER  ·  bridge → AI optimized",
                        font=("Segoe UI", 15, "bold"), bg=bg, fg="#4dd0e1", anchor="w")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))

        loc = tk.Label(self.root, text=f"common : {self.rt['common'] or 'NOT FOUND'}\n"
                                       f"python : {self.rt['py'] or 'NOT FOUND'}",
                       font=("Consolas", 9), bg=bg, fg="#7b869c", anchor="w", justify="left")
        loc.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        body = tk.Frame(self.root, bg=panel, padx=14, pady=12)
        body.grid(row=2, column=0, sticky="nsew", padx=16)
        self.root.grid_rowconfigure(2, weight=1)

        ttl = tk.Label(body, text="PIPELINE STATUS", font=("Segoe UI", 9, "bold"),
                       bg=panel, fg="#4dd0e1", anchor="w")
        ttl.pack(fill="x", pady=(0, 6))
        self.cells = {}
        for c in self.comps:
            row = tk.Frame(body, bg=panel)
            row.pack(fill="x", pady=2)
            self.cells[c] = tk.Label(row, text=f"  {self.labels[c]:36s}  ?",
                                     font=("Consolas", 10), bg=panel, fg="#7b869c", anchor="w")
            self.cells[c].pack(side="left")

        btns = tk.Frame(body, bg=panel, pady=(12, 4))
        btns.pack(fill="x")
        self.b_start = tk.Button(btns, text="▶ START ALL", font=(f, 12, "bold"), fg="#06170f",
                                 bg="#2ee6a8", activebackground="#3fffc0", padx=18, pady=6,
                                 command=lambda: self._safe(lambda: start_all(self.rt, self.autovar.get(), self.log)))
        self.b_start.pack(side="left", padx=(0, 8))
        self.b_stop = tk.Button(btns, text="■ STOP ALL", font=(f, 12, "bold"), fg="#fff",
                                bg="#e53d4f", activebackground="#ff5c6c", padx=18, pady=6,
                                command=lambda: self._safe(lambda: stop_all(self.rt, self.log)))
        self.b_stop.pack(side="left")

        row2 = tk.Frame(body, bg=panel, pady=(10, 0))
        row2.pack(fill="x")
        self.autovar = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="auto-apply optimized bridge settings after start",
                       variable=self.autovar, bg=panel, fg="#d7e0ef", selectcolor=panel,
                       font=("Segoe UI", 9)).pack(side="left")
        tk.Button(row2, text="⚙ Apply AI settings", font=("Segoe UI", 9), bg="#1b2637",
                  fg="#9fc6ff", command=lambda: self._safe(self._apply_handler)).pack(side="right")
        tk.Button(row2, text="🖥 Dashboards", font=("Segoe UI", 9), bg="#1b2637",
                  fg="#9fc6ff", command=lambda: self._safe(self._dash)).pack(side="right", padx=6)
        tk.Button(row2, text="⟳ Refresh", font=("Segoe UI", 9), bg="#1b2637",
                  fg="#9fc6ff", command=lambda: self._safe(self._refresh_status)).pack(side="right", padx=6)

        lb = tk.Label(body, text="LOG", font=("Segoe UI", 9, "bold"), bg=panel,
                      fg="#4dd0e1", anchor="w")
        lb.pack(fill="x", pady=(10, 4))
        self.logbox = tk.Text(body, height=11, font=("Consolas", 9), bg="#0a0e14",
                              fg="#a8d8ea", relief="flat", state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True)

        foot = tk.Label(self.root,
                        text="Hotkeys (global):  Ctrl+Shift+N start   Ctrl+Shift+X stop   Ctrl+Shift+D dashboards   ·   "
                             "STOP/apply never touch MT5 orders",
                        font=("Segoe UI", 9), bg="#0e1219", fg="#5b6a85", anchor="w")
        foot.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 10))

        # in-window keys
        for mod, k, h in (("Control-Shift", "Key-N", "start"), ("Control-Shift", "Key-X", "stop"),
                          ("Control-Shift", "Key-D", "dash")):
            self.root.bind(f"<{mod}-{k}>", lambda e, hh=h: self._hotkey(hh))

    # -- behaviour -------------------------------------------------------
    def _hotkey(self, hid):
        if hid == "start":
            self._safe(lambda: start_all(self.rt, self.autovar.get(), self.log))
        elif hid == "stop":
            self._safe(lambda: stop_all(self.rt, self.log))
        else:
            self._safe(self._dash)

    def _drain_queue(self):
        try:
            while True:
                hid = self.q.get_nowait()
                self._hotkey({1: "start", 2: "stop", 3: "dash"}.get(hid, "dash"))
        except queue.Empty:
            pass
        self.root.after(250, self._drain_queue)

    def _apply_handler(self):
        if not messagebox.askyesno("Apply AI settings",
                                   "Optimize the BRIDGE config (epoch/min_abs/venue gate)\n"
                                   "from the brain's latest think?\n\n"
                                   "The bridge hot-reloads it; the EA is NOT touched."):
            return
        self.log("APPLY: running nobi_ai.py apply --bridge")
        self.log(apply_ai(self.rt))

    def _dash(self):
        import webbrowser
        webbrowser.open("http://127.0.0.1:8090")
        webbrowser.open("http://127.0.0.1:8080/dashboard.html")

    def _safe(self, fn):
        try:
            fn()
            self._refresh_status()
        except Exception as ex:
            self.log(f"! error: {ex}")

    def log(self, msg):
        self.logbox.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        self.logbox.insert("end", f"[{stamp}] {msg}\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _refresh_status(self):
        st = status()
        colors = {"engine": "#2ee6a8", "backup": "#2ee6a8", "bridge": "#2ee6a8",
                  "console": "#2ee6a8", "tasks": "#2ee6a8"}
        for c in self.comps:
            ok = st.get(c, False)
            txt = f"  {self.labels[c]:36s}  RUNNING" if ok else f"  {self.labels[c]:36s}  stopped"
            self.cells[c].configure(text=txt, fg=colors[c] if ok else "#565f6e")

    def _poll_loop(self):
        self._refresh_status()
        self.root.after(3000, self._poll_loop)

    def on_close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if find_runtime().get("common") is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, "crypto-clob not found under\n"
                             "<APPDATA>\\MetaQuotes\\Terminal\\Common\\\n\n"
                             "Copy the crypto-clob + crypto-clob-ui folders there first.",
                             parent=root)
        root.destroy()
        return 2
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())