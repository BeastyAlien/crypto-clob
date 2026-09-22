@echo off
rem ============================================================
rem  NOBI one-click installer for a fresh PC
rem  Run this file AFTER extracting the zip (it copies the kit
rem  into the MetaTrader 5 Common folder automatically).
rem ============================================================
setlocal
set "DEST=%APPDATA%\MetaQuotes\Terminal\Common"
if not exist "%DEST%" (
  echo ERROR: MetaTrader 5 Common folder not found at:
  echo   %DEST%
  echo.
  echo Start MetaTrader 5 once (so it creates this folder) and run me again.
  pause
  exit /b 1
)
set "SRC=%~dp0"
echo Installing NOBI into:
echo   %DEST%
echo.
robocopy "%SRC%crypto-clob" "%DEST%\crypto-clob" /E /NFL /NDL /NJH /NJS /NP
robocopy "%SRC%crypto-clob-ui" "%DEST%\crypto-clob-ui" /E /NFL /NDL /NJH /NJS /NP
echo.
if exist "%DEST%\crypto-clob\dist\NOBI_Controller.exe" (
  echo SUCCESS! Now do the final two manual steps:
  echo   1) Copy  crypto-clob\MT5_files\NobiScalpTrader.ex5
  echo      into the terminal folder  MQL5\Experts\Advisors
  echo      and attach it to a BTCUSDm chart.
  echo   2) Run the controller and press START ALL:
  echo      %DEST%\crypto-clob\dist\NOBI_Controller.exe
  echo      (or press Ctrl+Shift+N anywhere after it is open)
  echo.
  start "" explorer "%DEST%\crypto-clob\dist"
) else (
  echo Something did not copy correctly - check the messages above.
)
pause