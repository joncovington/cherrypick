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
      * Pins Windows Update's forced-reboot window to quiet overnight hours ("active hours"), so an
        update reboot does not interrupt data collection. Override with -ActiveHoursStart /
        -ActiveHoursEnd (0-23, span <= 18h) or leave Windows in charge with -SkipActiveHours.

    What it deliberately does NOT do: touch your password. Auto-login is the last step and you do
    it yourself in netplwiz so your credential is stored as an encrypted LSA secret and nothing
    here ever sees it. Screen lock is left ON -- locking does not stop the tasks, so the box can
    trade behind a locked screen.

    Note: this is machine setup, independent of a Windows password change. If you later rotate your
    Windows password, redo the netplwiz auto-login step or auto-login will silently stop working.

    THE WINDOWS HELLO PIN GOTCHA: netplwiz auto-login is *password*-based. If a Windows Hello PIN is
    configured, its credential provider sits in front of the logon screen and takes priority over the
    stored password -- so even with AutoAdminLogon=1 a cold reboot still prompts for the PIN, and the
    scheduled tasks never get a user session. The diagnostic step below detects this and tells you.
    A locked screen (walking away, screen sleep) is unaffected -- tasks keep running behind the lock;
    the PIN only matters at a full reboot. Remove the PIN (Settings > Accounts > Sign-in options) if
    you need truly unattended reboots; otherwise accept that a reboot needs one manual PIN entry.
#>

param(
    # Windows Update "active hours": the window during which Windows will NOT auto-reboot to install
    # updates. Defaults confine forced reboots to a quiet 00:00-07:00 overnight slot, keeping them out
    # of the daily data-collection window. With ARSO on, such an overnight reboot self-resumes (locked)
    # without the PIN. Hours are 0-23; the span must be <= 18h. Pass -ActiveHoursStart/-ActiveHoursEnd
    # to retune, or -SkipActiveHours to leave Windows' automatic ("smart") active hours untouched.
    [ValidateRange(0, 23)] [int] $ActiveHoursStart = 7,
    [ValidateRange(0, 23)] [int] $ActiveHoursEnd = 0,
    [switch] $SkipActiveHours
)

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
Write-Host "[1/4] Disabling system sleep + hibernate (AC and battery)..."
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0

# 2. Lid close = do nothing (0=nothing, 1=sleep, 2=hibernate, 3=shutdown), both AC and battery.
#    The lid-close setting (GUID 5ca83367-...) is hidden by default and the LIDACTION alias isn't
#    available on every build, so unhide it and address it by GUID under the SUB_BUTTONS subgroup.
#    Resolve the active scheme GUID at runtime rather than trusting the SCHEME_CURRENT alias.
Write-Host "[2/4] Setting lid-close action to 'do nothing'..."
$lid = '5ca83367-6e45-459f-a27b-476b1d01c936'
$scheme = ((powercfg /getactivescheme) -split 'GUID: ')[1].Split(' ')[0]
powercfg /attributes SUB_BUTTONS $lid -ATTRIB_HIDE | Out-Null
powercfg /setacvalueindex $scheme SUB_BUTTONS $lid 0
powercfg /setdcvalueindex $scheme SUB_BUTTONS $lid 0
powercfg /setactive $scheme

# 3. Make sure the netplwiz auto-login checkbox is visible (some Win11 builds hide it).
Write-Host "[3/4] Enabling the netplwiz auto-login option..."
$pl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
if (Test-Path $pl) {
    Set-ItemProperty -Path $pl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord
}

# 4. Pin Windows Update's auto-reboot window to quiet overnight hours via manual "active hours", so a
#    forced update reboot does not land in the middle of data collection. Setting SmartActiveHoursState
#    to 0 switches off Windows' automatic active hours so our explicit Start/End take effect.
if ($SkipActiveHours) {
    Write-Host "[4/4] Skipping active hours (-SkipActiveHours) -- Windows keeps managing them automatically."
} else {
    $span = ($ActiveHoursEnd - $ActiveHoursStart + 24) % 24
    if ($span -eq 0 -or $span -gt 18) {
        Write-Error ("Active-hours span must be 1-18h; {0}:00->{1}:00 is {2}h. Adjust -ActiveHoursStart/-ActiveHoursEnd." -f $ActiveHoursStart, $ActiveHoursEnd, $span)
        exit 1
    }
    Write-Host ("[4/4] Setting Windows Update active hours to {0:00}:00-{1:00}:00 (no auto-reboot in that window)..." -f $ActiveHoursStart, $ActiveHoursEnd)
    $ux = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
    if (-not (Test-Path $ux)) { New-Item -Path $ux -Force | Out-Null }
    Set-ItemProperty -Path $ux -Name SmartActiveHoursState -Value 0 -Type DWord
    Set-ItemProperty -Path $ux -Name ActiveHoursStart -Value $ActiveHoursStart -Type DWord
    Set-ItemProperty -Path $ux -Name ActiveHoursEnd -Value $ActiveHoursEnd -Type DWord
}

Write-Host ""
Write-Host "Power + lid + update-reboot settings applied." -ForegroundColor Green
Write-Host ""

# 4. Diagnose the auto-login <-> Windows Hello PIN conflict (the reason a reboot can still prompt).
Write-Host "[diagnostic] Auto-login readiness for unattended reboots..." -ForegroundColor Cyan
$wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$wlp = Get-ItemProperty -Path $wl -ErrorAction SilentlyContinue
$autoOn = ($wlp.AutoAdminLogon -eq '1')
$defUser = $wlp.DefaultUserName
if ($autoOn) {
    Write-Host ("  AutoAdminLogon = 1 (user '{0}') -- netplwiz auto-login is registered." -f $defUser) -ForegroundColor Green
} else {
    Write-Host "  AutoAdminLogon is not enabled -- do the netplwiz step below to turn it on." -ForegroundColor Yellow
}

# Detect a configured Windows Hello PIN via the NGC container. It is ACL'd to SYSTEM, so even an
# elevated admin usually cannot enumerate it; treat access-denied as "cannot confirm" rather than
# "no PIN", and report honestly either way.
$ngc = Join-Path $env:windir 'ServiceProfiles\LocalService\AppData\Local\Microsoft\Ngc'
$pinState = 'unknown'
try {
    # Test-Path and Get-ChildItem both throw on the SYSTEM-owned container; with the script's
    # ErrorActionPreference=Stop that would abort the run, so swallow it and report 'unknown'.
    if (Test-Path -Path $ngc -ErrorAction Stop) {
        $kids = @(Get-ChildItem -Path $ngc -Directory -ErrorAction Stop)
        $pinState = if ($kids.Count -gt 0) { 'present' } else { 'none' }
    } else {
        $pinState = 'none'  # no NGC container at all -- no PIN enrolled on this machine
    }
} catch {
    $pinState = 'unknown'  # access denied -- cannot enumerate the SYSTEM-owned container
}
switch ($pinState) {
    'present' {
        Write-Host "  Windows Hello PIN: CONFIGURED." -ForegroundColor Yellow
        Write-Host "    -> A cold reboot will still prompt for the PIN and the scheduled tasks will NOT" -ForegroundColor Yellow
        Write-Host "       get a user session until you enter it. The PIN provider overrides password" -ForegroundColor Yellow
        Write-Host "       auto-login. A locked/asleep screen is fine -- tasks keep running behind it." -ForegroundColor Yellow
        Write-Host "    -> For truly unattended reboots: Settings > Accounts > Sign-in options > PIN > Remove." -ForegroundColor Yellow
    }
    'none' {
        Write-Host "  Windows Hello PIN: not configured -- password auto-login will work at reboot." -ForegroundColor Green
    }
    default {
        Write-Host "  Windows Hello PIN: could not confirm (NGC container is SYSTEM-owned)." -ForegroundColor Yellow
        Write-Host "    -> If a reboot still prompts for a PIN, that PIN is overriding auto-login; remove it" -ForegroundColor Yellow
        Write-Host "       via Settings > Accounts > Sign-in options for unattended reboots." -ForegroundColor Yellow
    }
}

# If a PIN is (or may be) keeping password auto-login from firing, ARSO is the mitigation that still
# resumes the session across a Windows Update reboot without removing the PIN. Report whether it is
# blocked. ARSO covers update-initiated reboots only -- a power-loss / manual cold boot still prompts.
if ($pinState -ne 'none') {
    $arsoBlocked = $false
    foreach ($k in @($wl, 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System')) {
        $v = (Get-ItemProperty -Path $k -Name DisableAutomaticRestartSignOn -ErrorAction SilentlyContinue).DisableAutomaticRestartSignOn
        if ($v -eq 1) { $arsoBlocked = $true }
    }
    if ($arsoBlocked) {
        Write-Host "  ARSO (auto sign-in after update): DISABLED -- even Windows Update reboots stay at the" -ForegroundColor Yellow
        Write-Host "    lock screen with nothing running. Enable Settings > Accounts > Sign-in options >" -ForegroundColor Yellow
        Write-Host "    'Use my sign-in info to automatically finish setting up after an update'." -ForegroundColor Yellow
    } else {
        Write-Host "  ARSO (auto sign-in after update): not disabled -- a Windows Update reboot should resume" -ForegroundColor Green
        Write-Host "    your session (locked) without the PIN. A power-loss / manual cold boot still prompts." -ForegroundColor Green
    }
}
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
