; NOBI Trading Center - Inno Setup installer script
; Build a classic Windows installer with:
;   1. Install Inno Setup (https://jrsoftware.org/isinfo.php) - free
;   2. Right-click this file -> Compile (i.e. run ISCC.exe installer.iss)
;   Output: installer_output\NOBI_TradingCenter_Setup.exe
;
; NOTE the app itself has a first-run wizard, so the installer simply
; places the files; the wizard handles accounts, MT5 detection, EA install
; and path fixing on first run.

[Setup]
AppName=NOBI Trading Center
AppVersion=1.0.0
AppPublisher=crypto-clob
DefaultDirName={autopf}\NOBI Trading Center
DefaultGroupName=NOBI Trading Center
UninstallDisplayIcon={app}\NOBITradingCenter.exe
Compression=lzma2
SolidCompression=yes
OutputDir=installer_output
OutputBaseFilename=NOBI_TradingCenter_Setup
PrivilegesRequired=lowest
ArchitecturesAllowed=windows64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:English-1.5.1.inf"

[Files]
; the control-center app (self-contained, no Python needed)
Source: "dist\NOBITradingCenter.exe"; DestDir: "{app}"; Flags: ignoreversion
; engine + bridge + config (no data folders - they are generated)
Source: "run.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "nobi_bridge.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "nobi_center.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "nobi_center.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "fix-paths.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "build_zip.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "nobi_config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_CENTER.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "SETUP_ON_NEW_PC.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "clob\*.py"; DestDir: "{app}\clob"; Flags: ignoreversion
Source: "MT5_files\*"; DestDir: "{app}\MT5_files"; Flags: ignoreversion
; dashboard ui
Source: "..\crypto-clob-ui\*.py"; DestDir: "{app}\crypto-clob-ui"; Flags: ignoreversion
Source: "..\crypto-clob-ui\*.bat"; DestDir: "{app}\crypto-clob-ui"; Flags: ignoreversion
Source: "..\crypto-clob-ui\*.html"; DestDir: "{app}\crypto-clob-ui"; Flags: ignoreversion
Source: "..\crypto-clob-ui\*.json"; DestDir: "{app}\crypto-clob-ui"; Flags: ignoreversion
Source: "..\crypto-clob-ui\*.md"; DestDir: "{app}\crypto-clob-ui"; Flags: ignoreversion

[Icons]
Name: "{group}\NOBI Trading Center"; Filename: "{app}\NOBITradingCenter.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\NOBI Trading Center"; Filename: "{app}\NOBITradingCenter.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checked

[Run]
Filename: "{app}\NOBITradingCenter.exe"; Description: "Launch NOBI Trading Center now"; Flags: nowait postinstall skipifsilent