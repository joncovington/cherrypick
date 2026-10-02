# Stop cherrypick completely on Windows: nothing scheduled, nothing running.
#
#   Double-click uninstall.cmd, or run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1
#
# Your data, configuration and saved broker login are KEPT (~\.cherrypick and Windows Credential
# Manager), so running install.cmd again picks up where you left off. To remove them too, delete the
# ~\.cherrypick folder and this folder by hand afterwards.

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) { $VPy = "python" }
$RunPy = Join-Path $Root "packages\orchestrator\run.py"

Write-Host "==> Removing the scheduled task and stopping the supervisor and services" -ForegroundColor Cyan
& $VPy $RunPy uninstall

Write-Host "==> Stopping the streamer, the console and anything else still running" -ForegroundColor Cyan
& $VPy $RunPy stop --all

# The Dolt server the earnings module kept alive, if it is on its usual port. Only a process that is
# actually `dolt` is stopped; anything else on the port is left alone.
$conn = Get-NetTCPConnection -LocalPort 3306 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "dolt") {
        Write-Host "==> Stopping the Dolt server" -ForegroundColor Cyan
        Stop-Process -Id $proc.Id -Confirm:$false
    }
}

Write-Host ""
Write-Host "cherrypick is stopped and will not restart. Your data and settings are kept in $HOME\.cherrypick." -ForegroundColor Green
