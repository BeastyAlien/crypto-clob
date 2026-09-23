# -*- coding: utf-8 -*-
"""supervise.py -- NOBI process watchdog.

Keeps Bridge / Engine / UI / Brain alive, guards RAM + disk so the PC never
freezes, and watches signal freshness + the metrics multi-file flap bug.

Run from the repo root (VS Code terminal):
    _runtime\\python\\python.exe supervise.py
or double-click start_all.bat.

Components it guards:
    Bridge: nobi_bridge.py --duration 0   (writes nobi_signal.sig for the EA)
    Engine: run.py --duration 0 --out crypto-clob-ui\\data-ui
    UI    : crypto-clob-ui\\ui_server.py --duration 0
    Brain : ai\\brain_server.py --port 8090
    Gate  : data_bento\\bento_gate.py --print (DataBento futures verdict file)
"""
import datetime as _dt
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = HERE / "_runtime" / "python" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
UI_DIR = HERE.parent / "crypto-clob-ui"
METRICS_DIR = UI_DIR / "data-ui"
SIGNAL_FILE = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\nobi_signal.sig")

LOOP_SEC = 30
RAM_MIN_MB = 1024          # refuse to keep starting things below this free RAM
DISK_MIN_MB = 2048         # warn below this free disk on the repo drive
STALE_SEC = 15 * 60        # signal file older than this = warning
FLAP_ALERT = True          # warn when >1 live metrics file exists in data-ui
ARCHIVE_DIR = METRICS_DIR / "_archive"   # quarantine folder for stale metrics files
QUARANTINE_AGE_S = 120     # move stale metrics files older than this many seconds
STALL_SERIAL_GAP_CYCLES = 14  # restart bridge if the signal serial has not advanced for this many cycles (~7 min)
ENGINE_MAX_AGE_S = 120        # engine metrics file is 'live' if younger than this (upstream sanity gate)
STALE_RESTART_CD_SEC = 600    # cooldown between bridge restarts (prevents restart loops)

COMPONENTS = [
    ("Bridge", [str(PY), str(HERE / "nobi_bridge.py"), "--duration", "0"], str(HERE), "bridge.out.log"),
    ("Engine", [str(PY), str(HERE / "run.py"), "--duration", "0", "--out", str(METRICS_DIR)], str(HERE), "engine.out.log"),
    ("UI",     [str(PY), str(UI_DIR / "ui_server.py"), "--duration", "0"], str(UI_DIR), "ui.out.log"),
    ("Brain",  [str(PY), str(HERE / "ai" / "brain_server.py"), "--port", "8090"], str(HERE), "brain.out.log"),
    ("Gate",   [str(PY), str(HERE / "data_bento" / "bento_gate.py"), "--print"], str(HERE), "gate.out.log"),
]

log = logging.getLogger("nobi-supervisor")
log.addHandler(logging.StreamHandler())
_h = logging.FileHandler(str(HERE / "supervisor.log"), encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)
log.setLevel(logging.INFO)


def _ps(cmd_line_pat: str):
    """Return the number of python processes whose command line contains the pattern."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match '" + cmd_line_pat + "' } | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            text=True, timeout=20)
        return int(out.strip() or "0")
    except Exception:
        return None   # cannot check -> caller decides
def _pids(cmd_line_pat):
    """Return python PIDs whose command line contains the pattern."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match '" + cmd_line_pat + "' } | "
             "Select-Object -ExpandProperty ProcessId"],
            text=True, timeout=20)
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []

_last_serial = None    # last observed signal serial (advance = bridge is alive)
_no_progress = 0       # cycles without a serial advance
_last_restart_ts = 0.0       # last bridge auto-restart timestamp

def _restart_bridge():
    """Kill a stalled bridge; the component loop respawns it next pass."""
    global _last_restart_ts
    _last_restart_ts = time.time()
    for pid in _pids(r"nobi_bridge\.py"):
        try:
            subprocess.check_call(["taskkill", "/F", "/PID", str(pid)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            log.info("signal stale: killed bridge pid=%d (watchdog auto-restart)", pid)
        except Exception:
            pass
def _latest_metrics_mtime():
    """Newest mtime of the engine's active metrics files, or 0."""
    try:
        if not METRICS_DIR.exists():
            return 0.0
        mt = [f.stat().st_mtime for f in METRICS_DIR.glob('metrics_*.jsonl') if f.is_file()]
        return max(mt) if mt else 0.0
    except Exception:
        return 0.0

def _read_signal_serial():
    """Current signal serial, or None if unreadable/empty."""
    try:
        with open(SIGNAL_FILE, "r", encoding="utf-8") as fh:
            line = fh.readline().strip()
        return int(line.split("|", 1)[0])
    except Exception:
        return None

def _maybe_restart_bridge_stall():
    """Engine writing but the signal serial stuck => bridge is wedged: restart it."""
    global _last_serial, _no_progress
    serial = _read_signal_serial()
    if serial is None:
        return
    if _last_serial is None or serial != _last_serial:
        _last_serial = serial
        _no_progress = 0
        return
    if time.time() - _latest_metrics_mtime() > ENGINE_MAX_AGE_S:
        log.warning("engine metrics file stale - upstream feed issue, bridge restart skipped")
        _no_progress = 0
        return
    _no_progress += 1
    if(_no_progress >= STALL_SERIAL_GAP_CYCLES
       and time.time() - _last_restart_ts > STALE_RESTART_CD_SEC):
        mins = _no_progress * LOOP_SEC // 60
        log.warning("signal serial stuck at %d for %d cycles (~%d min) - restarting bridge", _last_serial, _no_progress, mins)
        _restart_bridge()
        _no_progress = 0


def free_ram_mb():
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024,0)"],
            text=True, timeout=20)
        return int(float(out.strip() or "0"))
    except Exception:
        return None


def free_disk_mb(path: str):
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "$d=(Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
             "Where-Object { $_.DeviceID -eq (Split-Path -Qualifier '" + path + "') }); "
             "[math]::Round($d.FreeSpace/1MB,0)"],
            text=True, timeout=20)
        return int(float(out.strip() or "0"))
    except Exception:
        return None


def _start(name, cmd, cwd, out_log):
    logpath = HERE / out_log
    try:
        with open(logpath, "ab", 0) as o, open(str(logpath) + ".errlog", "ab", 0) as e:
            subprocess.Popen(cmd, cwd=cwd, stdout=o, stderr=e,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.info("started %s (%s)", name, os.path.basename(cmd[1] if len(cmd) > 1 else ""))
    except Exception as exc:
        log.error("failed to start %s: %s", name, exc)


def quarantine_stale_metrics():
    """Move stale metrics/live files out of data-ui so the bridge cannot stall
    and the disk cannot fill up overnight. The engine keeps the ACTIVE file
    open, so a Windows move of the busy file fails naturally and it stays put."""
    if not METRICS_DIR.exists():
        return
    try:
        ARCHIVE_DIR.mkdir(exist_ok=True)
    except Exception:
        return
    try:
        cand = [f for f in METRICS_DIR.glob("metrics_*.jsonl*") if f.is_file()]
        cand += [f for f in METRICS_DIR.glob("_old_*.jsonl") if f.is_file()]
        if not cand:
            return
        newest = max(cand, key=lambda f: f.stat().st_mtime)
        now = time.time()
        moved = 0
        for f in sorted(cand, key=lambda f: f.stat().st_mtime):
            if f == newest or now - f.stat().st_mtime < QUARANTINE_AGE_S:
                continue                # active writer or still fresh
            try:
                f.rename(ARCHIVE_DIR / f.name)
                moved += 1
            except (PermissionError, OSError):
                continue                # busy/locked - leave it for next cycle
        if moved:
            log.info("quarantined %d stale metrics file(s) -> %s", moved, ARCHIVE_DIR)
    except Exception as exc:
        log.warning("quarantine scan failed: %s", exc)


def _health():
    stale = False
    if SIGNAL_FILE.exists():
        age = time.time() - SIGNAL_FILE.stat().st_mtime
        if age > STALE_SEC:
            stale = True
            log.warning("signal file stale %.0f min (last serial visible read) - check bridge log", age / 60)
    quarantine_stale_metrics()
    if FLAP_ALERT and METRICS_DIR.exists():
        live = [f for f in METRICS_DIR.glob("metrics_*.jsonl") if f.stat().st_size > 0]
        if len(live) > 1:
            log.warning("FLAP RISK: %d live metrics files in data-ui - bridge may stall. "
                        "Quarantine stale ones (move, don't delete while engine runs).", len(live))
    return stale


def main():
    log.info("nobi supervisor up | loop=%ss | ram_min=%dMB | disk_min=%dMB",
             LOOP_SEC, RAM_MIN_MB, DISK_MIN_MB)
    while True:
        ram = free_ram_mb()
        disk = free_disk_mb(str(HERE))
        _maybe_restart_bridge_stall()
        _health()
        if ram is not None and ram < RAM_MIN_MB:
            log.warning("LOW RAM %.0fMB < %dMB - not starting anything this cycle", ram, RAM_MIN_MB)
        elif disk is not None and disk < DISK_MIN_MB:
            log.warning("LOW DISK %.0fMB < %dMB on %s ", disk, DISK_MIN_MB, str(HERE)[:2])
        for name, cmd, cwd, out_file in COMPONENTS:
            pat = os.path.basename(cmd[1])
            n = _ps(pat)
            if n is None:
                log.warning("cannot check %s (powershell unavailable) - skipping", name)
                continue
            if n == 0:
                if ram is not None and ram < RAM_MIN_MB:
                    continue
                _start(name, cmd, cwd, out_file)
            elif n > 1:
                log.warning("%s has %d instances - duplicate risk, check manually", name, n)
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("supervisor stopped by user")