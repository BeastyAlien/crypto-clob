@echo off
rem NOBI one-time step: bundle a portable 64-bit python into _runtime (optional,
rem only needed on a PC that has no Python installed)
set "PY=%~1"
if "%PY%"=="" set "PY=python"
"%PY%" "%~dp0_make_runtime.py"