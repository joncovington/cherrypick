# cherrypick installer for Windows.
#
#   Double-click install.cmd, or run:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Safe to run again: it reuses what is already there, never overwrites your config, and is how you
# add the optional Dolt data later. Options:
#   -Yes        accept every default without asking (the disclaimer still has to be accepted once
#               with -AcceptDisclaimer)
#   -AcceptDisclaimer   you have read DISCLAIMER.md and accept it
#   -SkipDolt   do not offer the Dolt setup (earnings and technicals stay off)
#   -WithDesk   also install the EXPERIMENTAL manual desk (packages/desk)
#   -NoStart    install only; do not start the suite
#   -ConfigHistory [-ConfigRemote <url>]   keep a git history of your settings (see below)

param(
    [switch]$Yes,
    [switch]$AcceptDisclaimer,
    [switch]$SkipDolt,
    [switch]$WithDesk,
    [switch]$NoStart,
    [switch]$ConfigHistory,
    [string]$ConfigRemote = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
$RunPy = Join-Path $Root "packages\orchestrator\run.py"
$ConsoleUrl = "http://127.0.0.1:5070"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" }
function Warn($text) { Write-Host "    ! $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ""; Write-Host "  X $text" -ForegroundColor Red; exit 1 }

function Ask($question, [bool]$default) {
    if ($Yes) { return $default }
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "    $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

# A native command's exit code, with its stderr left alone: redirecting stderr in Windows
# PowerShell 5.1 turns every pip progress line into an error record and aborts the script.
function Run($exe, [string[]]$argList, $what) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)." }
}

Write-Host ""
Write-Host "  cherrypick installer" -ForegroundColor White
Write-Host "  ---------------------"

# ---------------------------------------------------------------------------------- disclaimer
Step "Disclaimer"
Write-Host "    cherrypick is an EXPERIMENTAL PROTOTYPE for EDUCATIONAL use. It is NOT financial advice." -ForegroundColor Yellow
Write-Host "    Paper results are simulated. Its live-trading paths (off by default) place REAL," -ForegroundColor Yellow
Write-Host "    irreversible orders at your own risk; options trading can lose money quickly." -ForegroundColor Yellow
Note "Full text: DISCLAIMER.md in this folder."
if (-not $AcceptDisclaimer) {
    $typed = Read-Host "    Type YES to confirm you have read DISCLAIMER.md and accept it"
    if ($typed -ne "YES") { Fail "Not accepted. Nothing was installed." }
}

# ---------------------------------------------------------------------------------- prerequisites
Step "Checking prerequisites"
$Python = $null
foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3"))) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $extra = @($candidate | Select-Object -Skip 1)
    $ok = & $exe @extra -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ok -eq "True") { $Python = $candidate; break }
}
if (-not $Python) {
    Fail ("Python 3.11 or newer is required. Install it with:`n" +
          "      winget install -e --id Python.Python.3.13`n" +
          "    (or from https://www.python.org/downloads/ - tick 'Add python.exe to PATH'), then run this again.")
}
$PyExe = $Python[0]; $PyArgs = @($Python | Select-Object -Skip 1)
Note ("Python: " + (& $PyExe @PyArgs --version))

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail ("Node.js 22 or newer is required for the console. Install it with:`n" +
          "      winget install -e --id OpenJS.NodeJS.LTS`n" +
          "    then open a NEW terminal and run this again.")
}
$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 22) { Fail "Node.js $(node --version) is too old; version 22 or newer is required (winget install -e --id OpenJS.NodeJS.LTS)." }
Note "Node.js: $(node --version)"

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Note "pnpm not found; installing it with npm"
    Run "npm" @("install", "-g", "pnpm@11") "Installing pnpm"
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { Fail "pnpm installed but is not on PATH yet. Open a NEW terminal and run this again." }
}
Note "pnpm: $(pnpm --version)"

# ---------------------------------------------------------------------------------- python packages
Step "Creating the Python environment (.venv)"
if (-not (Test-Path $VPy)) { Run $PyExe ($PyArgs + @("-m", "venv", $Venv)) "Creating the virtual environment" }
Run $VPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip") "Upgrading pip"

Step "Installing the cherrypick packages (a few minutes the first time)"
# packages/core first: every other package depends on it and it is not on PyPI.
Run $VPy @("-m", "pip", "install", "--quiet", "-e", (Join-Path $Root "packages\core")) "Installing packages/core"
$packages = Get-ChildItem (Join-Path $Root "packages") -Directory |
    Where-Object { $_.Name -ne "core" -and (Test-Path (Join-Path $_.FullName "pyproject.toml")) } |
    Where-Object { $WithDesk -or $_.Name -ne "desk" } |
    Sort-Object Name
foreach ($pkg in $packages) {
    Note $pkg.Name
    Run $VPy @("-m", "pip", "install", "--quiet", "-e", $pkg.FullName) "Installing packages/$($pkg.Name)"
}
if (-not $WithDesk) { Note "desk skipped (EXPERIMENTAL manual live orders; re-run with -WithDesk to include it)" }

# ---------------------------------------------------------------------------------- console
Step "Building the console (the web page you will use)"
Push-Location (Join-Path $Root "packages\console")
try {
    Run "pnpm" @("install", "--silent") "pnpm install"
    Run "pnpm" @("build") "Building the console"
} finally { Pop-Location }

# ---------------------------------------------------------------------------------- config
Step "Writing your configuration (kept if it already exists)"
& $VPy $RunPy init | Out-Null
Note "Config: $HOME\.cherrypick\config.json"

Step "Checking optional extras on this machine"
$null = & $VPy $RunPy capabilities --detect --write
if ($LASTEXITCODE -ne 0) { Fail "Recording capabilities failed (exit $LASTEXITCODE)." }
$caps = & $VPy $RunPy capabilities | ConvertFrom-Json
$hasDolt = [bool]$caps.capabilities.dolt
$hasClaude = [bool]$caps.capabilities.claude
if ($hasClaude) { Note "Claude Code: found - the AI advisor and narratives can be switched on in the console's Config page." }
else { Note "Claude Code: not found - AI features stay off and hidden. Install Claude Code later and run this installer again to enable them." }

# ---------------------------------------------------------------------------------- dolt
if ($hasDolt) {
    Note "Dolt data: found - earnings and technicals are available."
} elseif (-not $SkipDolt) {
    Step "Optional: Dolt market data (for the earnings module and the technicals report)"
    Note "Dolt is a free database tool. cherrypick uses three free public datasets from DoltHub"
    Note "(earnings calendar, option history and stock history). The download is SEVERAL GB and"
    Note "can take an hour or more on a slow connection."
    if (Ask "Set up Dolt now?" $false) {
        if (-not (Get-Command dolt -ErrorAction SilentlyContinue)) {
            if (Ask "Dolt is not installed. Install it now with winget?" $true) {
                Run "winget" @("install", "-e", "--id", "DoltHub.Dolt", "--accept-source-agreements", "--accept-package-agreements") "Installing Dolt"
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            }
        }
        if (-not (Get-Command dolt -ErrorAction SilentlyContinue)) {
            Warn "Dolt is not on PATH. Install it from https://docs.dolthub.com/introduction/installation, open a NEW terminal, and run this installer again."
        } else {
            $null = & dolt config --global --get user.name 2>$null
            if ($LASTEXITCODE -ne 0) {
                Run "dolt" @("config", "--global", "--add", "user.name", "cherrypick") "Configuring Dolt"
                Run "dolt" @("config", "--global", "--add", "user.email", "cherrypick@localhost") "Configuring Dolt"
            }
            $dataDir = Join-Path $HOME ".cherrypick\data\earnings"
            New-Item -ItemType Directory -Force $dataDir | Out-Null
            foreach ($db in @("earnings", "options", "stocks")) {
                if (Test-Path (Join-Path $dataDir "$db\.dolt")) { Note "$db already cloned"; continue }
                Note "Downloading $db (this is the slow part)..."
                Push-Location $dataDir
                try { Run "dolt" @("clone", "post-no-preference/$db", $db) "Downloading $db" } finally { Pop-Location }
            }
            $null = & $VPy $RunPy capabilities --detect --write
            $hasDolt = [bool]((& $VPy $RunPy capabilities | ConvertFrom-Json).capabilities.dolt)
        }
    }
}
if (-not $hasDolt) {
    Warn "Without the Dolt data, the EARNINGS module and the TECHNICALS report are turned off"
    Warn "automatically and hidden from the console. Run this installer again any time to add it."
}

# ---------------------------------------------------------------------------------- broker
Step "Connect your tastytrade account"
Note "cherrypick reads live market data through your tastytrade login. Paper trading never sends"
Note "orders. You will need an OAuth client secret and refresh token (QUICKSTART.md shows where to"
Note "get them). They are stored in Windows Credential Manager, never in a file."
$connected = $false
try {
    $secrets = (& $VPy -m cherrypick.core.auth status | ConvertFrom-Json).secrets
    $connected = [bool]$secrets.client_secret -and [bool]$secrets.refresh_token
} catch { $connected = $false }
if ($connected) {
    Note "Already connected."
} elseif (Ask "Connect now?" $true) {
    & $VPy -m cherrypick.core.auth setup
    if ($LASTEXITCODE -ne 0) { Warn "Not connected. Run this installer again to try once more." }
} else {
    Warn "Skipped. Market data will not flow until you connect; run this installer again to do it."
}

# ---------------------------------------------------------------------------------- config history
Step "Optional: a history of your settings"
Note "cherrypick can keep a git history of your settings (config files only; never your trading"
Note "data or passwords) and, if you give it one, push it to a PRIVATE repository you own, so a"
Note "change can be undone and a new computer set up the same way."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Note "git is not installed, so this is skipped. Install git and run the installer again to add it."
} else {
    $existing = Test-Path (Join-Path $HOME ".cherrypick\.git")
    $want = $ConfigHistory -or $existing
    if (-not $want -and -not $Yes) { $want = Ask "Keep a history of your settings?" $false }
    if ($want) {
        $remote = $ConfigRemote
        if (-not $remote -and -not $Yes -and -not $existing) {
            $remote = (Read-Host "    Private git repository URL to push to (Enter to keep it on this computer only)").Trim()
        }
        $cbArgs = @($RunPy, "config-backup", "--init", "--enable")
        if ($remote) { $cbArgs += @("--remote", $remote) }
        $null = & $VPy @cbArgs
        if ($LASTEXITCODE -eq 0) { Note "On: your settings are committed every 15 minutes when they change." }
        else { Warn "Could not set it up; run '.venv\Scripts\python packages\orchestrator\run.py config-backup --init' to see why." }
    } else {
        Note "Skipped. You can switch it on later on the console's Config page or with run.py config-backup."
    }
}

# ---------------------------------------------------------------------------------- start
if ($NoStart) {
    Step "Installed (not started, as asked)"
    Note "Start it later with:  .venv\Scripts\python packages\orchestrator\run.py install"
    exit 0
}
Step "Starting cherrypick"
Note "This registers one Windows scheduled task that keeps cherrypick running in the background,"
Note "including after a restart. Uninstall any time with uninstall.cmd."
Run $VPy @($RunPy, "install") "Starting cherrypick"

Note "Waiting for the console to come up..."
$up = $false
foreach ($i in 1..45) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $ConsoleUrl | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 2 }
}
Write-Host ""
if ($up) {
    Write-Host "  Done. The console is at $ConsoleUrl" -ForegroundColor Green
    Start-Process $ConsoleUrl
} else {
    Write-Host "  Installed. The console is starting in the background; open $ConsoleUrl in a minute." -ForegroundColor Green
    Write-Host "  If it never loads, run:  .venv\Scripts\python packages\orchestrator\run.py doctor"
}
Write-Host "  Everything runs in PAPER mode. Live trading stays off until you deliberately turn it on."
Write-Host ""
Write-Host "  Open the console any time at $ConsoleUrl (bookmark it); cherrypick keeps running in the background."
Write-Host "  To run cherrypick commands yourself, open a terminal in this folder ($Root) and activate"
Write-Host "  the virtual environment first:  .venv\Scripts\Activate.ps1   (QUICKSTART.md explains)"
