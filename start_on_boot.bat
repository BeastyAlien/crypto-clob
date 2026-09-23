@echo off
rem ============================================================
rem  NOBI start_on_boot.bat
rem  Run once at Windows logon (scheduled task) to bring up:
rem    1) MetaTrader 5 terminal        (optional, toggle below)
rem    2) NOBI watchdog (supervise.py) (hidden, detached)
rem
rem  Safe to re-run: any stale supervise.py is stopped first via
rem  _kill_stale_supervisor.py (command-line match only; never
rem  touches Bridge/Engine/UI/Brain/Gate).
rem  Install once with schtasks - see README_START_ON_BOOT.txt
rem  in this same folder.
rem ============================================================
setlocal

rem --- config -------------------------------------------------
set "REPO=%~dp0"
set "PY=%REPO%_runtime\python\python.exe"
set "SUPER=%REPO%supervise.py"
set "MT5=C:\Program Files\MetaTrader 5\terminal64.exe"
set "START_MT5=1"          rem 1=start MT5, 0=watchdog only
set "MT5_WAIT_SEC=60"      rem settle time (s) before watchdog

rem --- stop any stale watchdog (command-line match only) ------
if exist "%PY%" (
    "%PY%" "%REPO%_kill_stale_supervisor.py" >nul 2>&1
)

rem --- 1) MetaTrader 5 terminal --------------------------------
if "%START_MT5%"=="1" (
    if exist "%MT5%" (
        start "" "%MT5%"
        if %MT5_WAIT_SEC% gtr 0 timeout /t %MT5_WAIT_SEC% /nobreak >nul
    )
)

rem --- 2) NOBI watchdog (hidden window, detached) --------------
if exist "%PY%" (
    powershell -NoProfile -Command "Start-Process -WorkingDirectory '%REPO%' -FilePath '%PY%' -ArgumentList 'supervise.py' -WindowStyle Hidden -RedirectStandardOutput '%REPO%supervisor.console.log' -RedirectStandardError '%REPO%supervisor.console.err.log'"
)

rem --- done ----------------------------------------------------
echo %date% %time% start_on_boot: MT5=%START_MT5% watchdog=started >> "%REPO%start_on_boot.log"
endlocal
exit /b 0