@echo off
rem NOBI AI brain - thinker: replay grid, update state/thoughts (runs hourly via NOBI_AI_Think task)
cd /d "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob"
"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\_runtime\python\python.exe" nobi_ai.py think >> "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\ai\think.log" 2>&1
