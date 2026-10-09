# Grant "Log on as a service" (SeServiceLogonRight) to this user, so the optional cherrypick
# supervisor service can run as them. Windows Home has no secpol.msc; secedit does the same thing.
# Run in an ADMINISTRATOR PowerShell:  powershell -ExecutionPolicy Bypass -File <this file>
# Printed as a step by `run.py service prepare`. Safe to run twice: it says so when the right is held.
# Needed on 2026-10-08: the Services app did not grant the right, and Windows Home has no secpol.msc.
$ErrorActionPreference = 'Stop'
$account = "$env:COMPUTERNAME\$env:USERNAME"
$sid = (New-Object System.Security.Principal.NTAccount($account)).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$dir = Join-Path $env:TEMP "cp-logon-right"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$inf = Join-Path $dir "rights.inf"
$db = Join-Path $dir "rights.sdb"
secedit /export /cfg $inf /areas USER_RIGHTS | Out-Null
$lines = Get-Content $inf
$i = [Array]::FindIndex([string[]]$lines, [Predicate[string]]{ param($l) $l -like 'SeServiceLogonRight*' })
if ($i -ge 0) {
    if ($lines[$i] -match [regex]::Escape($sid)) { Write-Output "$account already has Log on as a service"; exit 0 }
    $lines[$i] = $lines[$i] + ",*$sid"
} else {
    $p = [Array]::IndexOf([string[]]$lines, '[Privilege Rights]')
    $lines = $lines[0..$p] + "SeServiceLogonRight = *$sid" + $lines[($p + 1)..($lines.Length - 1)]
}
Set-Content -Path $inf -Value $lines -Encoding Unicode
secedit /configure /db $db /cfg $inf /areas USER_RIGHTS | Out-Null
Remove-Item -Recurse -Force $dir
Write-Output "Granted 'Log on as a service' to $account ($sid)"
