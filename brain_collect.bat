@echo off
rem NOBI AI brain - incremental collector (runs every 5 minutes)
"%~dp0_runtime\python\python.exe" "%~dp0nobi_ai.py" collect >> "C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\ai\collect.log" 2>&1