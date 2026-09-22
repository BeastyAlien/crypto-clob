@echo off
rem NOBI supervisor launcher - keeps Bridge/Engine/UI/Brain alive, guards RAM+disk
cd /d "%~dp0"
"_runtime\python\python.exe" supervise.py