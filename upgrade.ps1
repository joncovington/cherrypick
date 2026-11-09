# Move this cherrypick install to a newer release on Windows.
#
#   Double-click upgrade.cmd, or run:  powershell -ExecutionPolicy Bypass -File upgrade.ps1 [options]
#
# This runs `run.py upgrade` from this folder's own environment, so it needs the install you already
# have. It shows the new release's notes and asks before it changes anything. Options pass straight
# through:
#   --to vX.Y.Z      the release to move to (default: the latest)
#   --dry-run        check everything and show the notes, change nothing
#   --yes            do not ask (the refusals still apply)
#   --force-window   run outside the quiet window after the close (an armed live loop or an open
#                    live position still refuses)
#   --rollback       undo the last upgrade: the old release and the backup taken before it
#
# Guide: docs/guide/upgrading.md

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
$RunPy = Join-Path $Root "packages\orchestrator\run.py"

if (-not (Test-Path $VPy)) {
    Write-Host "  X This folder has no .venv, so it is not an installed cherrypick. Run install.cmd first." -ForegroundColor Red
    exit 1
}
& $VPy $RunPy upgrade @args
exit $LASTEXITCODE
