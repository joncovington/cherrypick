<#
    cherrypick -- walk-away durability setup (Approach A: auto-login + never-sleep).

    Run ONCE, elevated (right-click > Run as administrator, or from an elevated PowerShell):
        powershell -ExecutionPolicy Bypass -File tools\setup-walkaway-durability.ps1

    What it does (all reversible):
      * Stops the system sleeping/hibernating on AC and battery, so scheduled tasks and the
        streamer keep running unattended.
      * Sets lid-close to "do nothing" (the laptop gotcha: the default sleeps the machine and
        kills everything the moment you close the lid).
      * Enables the netplwiz auto-login checkbox if Windows is hiding it.

    What it deliberately does NOT do: touch your password. Auto-login is the last step and you do
    it yourself in netplwiz so your credential is stored as an encrypted LSA secret and nothing
    here ever sees it. Screen lock is left ON -- locking does not stop the tasks, so the box can
    trade behind a locked screen.

    Note: this is machine setup, independent of a Windows password change. If you later rotate your
    Windows password, redo the netplwiz auto-login step or auto-login will silently stop working.
#>

$ErrorActionPreference = 'Stop'

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "This script must be run elevated (Run as administrator)."
        exit 1
    }
}

Assert-Admin
Write-Host "cherrypick walk-away durability setup" -ForegroundColor Cyan
Write-Host ("=" * 50)

# 1. Never sleep / hibernate (AC and battery).
Write-Host "[1/3] Disabling system sleep + hibernate (AC and battery)..."
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0

# 2. Lid close = do nothing (0=nothing, 1=sleep, 2=hibernate, 3=shutdown), both AC and battery.
#    The lid-close setting (GUID 5ca83367-...) is hidden by default and the LIDACTION alias isn't
#    available on every build, so unhide it and address it by GUID under the SUB_BUTTONS subgroup.
#    Resolve the active scheme GUID at runtime rather than trusting the SCHEME_CURRENT alias.
Write-Host "[2/3] Setting lid-close action to 'do nothing'..."
$lid = '5ca83367-6e45-459f-a27b-476b1d01c936'
$scheme = ((powercfg /getactivescheme) -split 'GUID: ')[1].Split(' ')[0]
powercfg /attributes SUB_BUTTONS $lid -ATTRIB_HIDE | Out-Null
powercfg /setacvalueindex $scheme SUB_BUTTONS $lid 0
powercfg /setdcvalueindex $scheme SUB_BUTTONS $lid 0
powercfg /setactive $scheme

# 3. Make sure the netplwiz auto-login checkbox is visible (some Win11 builds hide it).
Write-Host "[3/3] Enabling the netplwiz auto-login option..."
$pl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
if (Test-Path $pl) {
    Set-ItemProperty -Path $pl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord
}

Write-Host ""
Write-Host "Power + lid settings applied." -ForegroundColor Green
Write-Host ""
Write-Host "FINAL STEP (you do this -- the script never touches your password):" -ForegroundColor Yellow
Write-Host "  1. Press Win+R, type:  netplwiz   and press Enter."
Write-Host "  2. Select your user, UNCHECK 'Users must enter a user name and password'."
Write-Host "  3. Click OK and enter your Windows password twice when prompted."
Write-Host ""
Write-Host "That stores your credential as an encrypted LSA secret and auto-logs you in at boot,"
Write-Host "so the scheduled tasks (which need your user session for broker/keyring auth) always run."
Write-Host ""
Write-Host "Verify afterward:  reboot, wait for auto-login, then run  python run.py doctor"
