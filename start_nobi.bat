@echo off
rem NOBI Controller launcher: double-click this file (or run from anywhere)
set "ROOT=%~dp0"
set "PYW="
if exist "%ROOT%_runtime\python\pythonw.exe" set "PYW=%ROOT%_runtime\python\pythonw.exe"
if "%PYW%"=="" if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if "%PYW%"=="" set "PYW=pythonw"
start "" "%PYW%" "%ROOT%nobi_launcher.pyw"