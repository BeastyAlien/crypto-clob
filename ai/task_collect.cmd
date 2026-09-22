@echo off
rem NOBI AI brain - incremental collector (runs every 5 min via NOBI_AI_Collect task)
cd /d "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob"
"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\_runtime\python\python.exe" nobi_ai.py collect >> "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\ai\collect.log" 2>&1
