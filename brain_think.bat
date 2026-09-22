@echo off
rem NOBI AI brain - thinker: replay grid, update state/thoughts (runs hourly)
"%~dp0_runtime\python\python.exe" "%~dp0nobi_ai.py" think >> "C:\Users\DELL 5580\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\ai\think.log" 2>&1