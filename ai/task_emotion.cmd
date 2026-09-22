@echo off
rem NOBI emotion / self-regulate loop (runs every 30 min via NOBI_Emotion task)
cd /d "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob"
"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\_runtime\python\python.exe" nobi_emotion.py >> "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\ai\emotion_task.log" 2>&1