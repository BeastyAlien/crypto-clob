NOBI BOOT-START (start_on_boot.bat)
==================================

What it does
------------
On Windows logon this script:
  1. starts MetaTrader 5 terminal  (C:\Program Files\MetaTrader 5\terminal64.exe)
  2. waits 60 s for the terminal to settle
  3. starts the NOBI watchdog (supervise.py) hidden and detached

The watchdog then brings up / keeps alive Bridge, Engine, UI, Brain and Gate
exactly like "NOBI: START ALL" in VS Code, and enforces the RAM / disk guards
and the metrics quarantine.

Install once (run this exact line as Administrator, e.g. in a Command Prompt):
---------------------------------------------------------------------------
schtasks /create /tn "NOBI_BootStart" /tr "C:\Windows\System32\cmd.exe /c C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\start_on_boot.bat" /sc onlogon /ru "Administrator" /rl highest /f
---------------------------------------------------------------------------
- /sc onlogon = fires when you log in (GUI session exists, so MT5 can open).
- The default /sc onstart fires before login; GUI apps may fail there.

Verify it is scheduled:
    schtasks /query /tn "NOBI_BootStart" /v /fo list

Uninstall if ever needed:
    schtasks /delete /tn "NOBI_BootStart" /f

Manual test (without rebooting) - closes old watchdog first, then runs once:
    cmd /c "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\crypto-clob\start_on_boot.bat"

Toggles inside start_on_boot.bat:
    START_MT5    1 = start MetaTrader 5, 0 = watchdog only
    MT5_WAIT_SEC seconds to wait between MT5 and the watchdog

Notes / limits
--------------
- The watchdog supervises the Python pipeline only. It does NOT re-attach the
  EA to an MT5 chart - that is still a manual step after MT5 opens.
- The boot copy kills any previous supervise.py (command-line match only)
  before starting, so re-running the script is safe and idempotent.