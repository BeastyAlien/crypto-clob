@echo off
rem Build NOBI_Controller.exe (run once; needs PyInstaller: pip install pyinstaller)
set "PY=%~1"
if "%PY%"=="" set "PY=python"
"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name NOBI_Controller "%~dp0nobi_launcher.pyw"
echo.
echo Done. exe is at: %~dp0dist\NOBI_Controller.exe