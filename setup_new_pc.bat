@echo off
rem NOBI Trading Rig - run this on the NEW PC after unzipping/extracting.
rem Auto-finds the MT5 data folder, installs EA/template/preset, fixes config.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1"