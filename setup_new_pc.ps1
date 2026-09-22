# setup_new_pc.ps1 - one-click installer for a NEW PC (no Python required).
#
# What it does:
#   1. Locates the newest MetaTrader 5 data folder (random <HASH> per PC)
#   2. Copies the whole rig (crypto-clob + crypto-clob-ui) into the shared
#      terminal folder  C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\Common
#   3. Copies the EA (.ex5/.mq5), chart template and .set preset into that
#      terminal's MQL5 folders
#   4. Rewrites nobi_config.json so the bridge writes into THIS PC's
#      <terminal>\MQL5\Files (the folder is different on every PC)
#   5. Prints the final manual steps (attach EA, enable algo trading)
#
# Run via:  setup_new_pc.bat   (or: powershell -File setup_new_pc.ps1)

$ErrorActionPreference = "Stop"

$srcRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path          # folder with this script ( = package root when script sits in <root>\crypto-clob\ )
$root     = Split-Path -Parent $srcRoot                                 # package root holding crypto-clob/ + crypto-clob-ui/ + MT5_files/
$common   = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common"      # shared, stable location

Write-Host ""
Write-Host "=== NOBI Trading Rig - one-click setup for this PC ===" -ForegroundColor Cyan

# -- 1) find the newest MT5 data folder -------------------------------------
$base = Join-Path $env:APPDATA "MetaQuotes\Terminal"
$terms = @()
if (Test-Path $base) {
    $terms = Get-ChildItem $base -Directory | Where-Object { Test-Path (Join-Path $_.FullName "MQL5\Files") }
}
if ($terms.Count -eq 0) {
    Write-Host "No MetaTrader 5 data folder found under $base" -ForegroundColor Red
    Write-Host "Install MT5, log in once, then run this again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
$terms = $terms | Sort-Object LastWriteTime -Descending
$term = $terms[0]
Write-Host ("MT5 terminal   : " + $term.FullName) -ForegroundColor Green

# -- 2) install engine + ui into the terminal's Common folder ------------
New-Item -ItemType Directory -Force -Path $common | Out-Null
$dstCL = Join-Path $common "crypto-clob"
$dstUI = Join-Path $common "crypto-clob-ui"

Write-Host "Copying engine       -> $dstCL"
robocopy (Join-Path $root "crypto-clob")   $dstCL /E /XD data data-ui __pycache__ build /NFL /NDL /NJH /NJS | Out-Null
Write-Host "Copying dashboard UI -> $dstUI"
robocopy (Join-Path $root "crypto-clob-ui") $dstUI /E /XD data-ui __pycache__ /NFL /NDL /NJH /NJS | Out-Null

# -- 3) copy EA + template + preset into this PC's MT5 --------------------
$mql = Join-Path $term.FullName "MQL5"
$eaDir  = Join-Path $mql "Experts\Advisors"
$tplDir = Join-Path $mql "Profiles\Templates"
$prDir  = Join-Path $mql "Presets"
foreach ($d in @($eaDir, $tplDir, $prDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

Copy-Item (Join-Path $root "MT5_files\NobiScalpTrader.ex5")  (Join-Path $eaDir  "NobiScalpTrader.ex5")  -Force
Copy-Item (Join-Path $root "MT5_files\NobiScalpTrader.mq5")  (Join-Path $eaDir  "NobiScalpTrader.mq5")  -Force
Copy-Item (Join-Path $root "MT5_files\NobiScalpTrader_BTCUSD.tpl") (Join-Path $tplDir "NobiScalpTrader_BTCUSD.tpl") -Force
Copy-Item (Join-Path $root "MT5_files\NobiScalpTrader_current.set") (Join-Path $prDir "NobiScalpTrader_current.set") -Force
Write-Host "EA + template + preset copied into $mql" -ForegroundColor Green

# --- 4) rewrite the bridge config for THIS PC's signal file -------------
$files   = Join-Path $mql "Files"
$cfgPath = Join-Path $dstCL "nobi_config.json"
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    $cfg.signal_file = Join-Path $files "nobi_signal.sig"
    $cfg.anchor_file = Join-Path $files "nobi_anchor.sig"
    if (-not $cfg.metrics_dir) { $cfg.metrics_dir = Join-Path $dstCL "data" }
    $cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
    Write-Host "nobi_config.json -> signal path rewritten for this PC" -ForegroundColor Green
}
# make sure the Files dir exists so the EA/anchor can land there
New-Item -ItemType Directory -Force -Path $files | Out-Null

# --- 5) final manual steps ------------------------------------------------
Write-Host ""
Write-Host "=== DONE - final steps (once) ===" -ForegroundColor Cyan
Write-Host " 1. In MT5: open a BTCUSDm chart (M1/H1 whatever you run)."
Write-Host " 2. Navigator -> Expert Advisors -> drag NobiScalpTrader onto the chart."
Write-Host " 3. In the inputs dialog press 'Load' and pick the preset"
Write-Host "    NobiScalpTrader_current.set  (Presets folder) - it loads your settings."
Write-Host " 4. Set InpEnableTrading=true and turn on Algo Trading"
Write-Host "    (toolbar button + Tools -> Options -> Expert Advisors -> allow trading)."
Write-Host " 5. Start the pipeline: double-click"
Write-Host "       $dstUI\start_all.bat"
Write-Host "    or launch the app:  $dstCL\dist\NOBITradingCenter.exe"
Write-Host " 6. Dashboard: http://127.0.0.1:8080/dashboard.html"
Write-Host ""
Write-Host "Backup files: this setup is fully portable - keep the zip/installer."
Write-Host ""
pause