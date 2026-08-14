# One-command dev environment setup for the cherrypick suite monorepo.
#
# packages/core is not on PyPI (Private :: Do Not Upload) -- every other package depends on it as a
# plain named dependency ("cherrypick-core") resolved only from what's already installed, so it MUST
# be installed first or every later `pip install -e .` fails to resolve it.
#
# Usage: powershell -File scripts\dev-install.ps1 [-Python <path-to-python.exe>]

param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Install-Editable($relativePath, $label) {
    Write-Host "==> $label"
    # No `2>&1` here, deliberately. Redirecting a NATIVE command's stderr inside PowerShell wraps each
    # stderr line in an ErrorRecord (NativeCommandError); with $ErrorActionPreference = "Stop" that
    # aborts the script even when pip exited 0. pip writes progress/notices to stderr routinely, so
    # this killed the run right after the first successful install. $LASTEXITCODE is the real signal.
    & $Python -m pip install -e "$root\$relativePath"
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed for $label (exit $LASTEXITCODE)"
    }
}

Install-Editable "packages\core[dev]" "packages/core (must be first)"

Install-Editable "packages\orchestrator[dev]" "packages/orchestrator"
Install-Editable "packages\meic[dev]"         "packages/meic"
Install-Editable "packages\earnings[dev]"     "packages/earnings"
Install-Editable "packages\gex[dev]"          "packages/gex"
Install-Editable "packages\flies[dev]"        "packages/flies"
Install-Editable "packages\calendars[dev]"    "packages/calendars"
Install-Editable "packages\streamer[dev]"     "packages/streamer"
Install-Editable "packages\desk[dev]"         "packages/desk"
Install-Editable "packages\review[dev]"       "packages/review"
Install-Editable "packages\advisor[dev]"      "packages/advisor"

# The console UI is the one Node package. Optional: skipped with a notice when pnpm is absent,
# so the Python-only setup stays one command with no new required toolchain.
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    Write-Host "==> packages/console (pnpm install + build)"
    Push-Location "$root\packages\console"
    try {
        pnpm install
        if ($LASTEXITCODE -ne 0) { throw "pnpm install failed (exit $LASTEXITCODE)" }
        pnpm build
        if ($LASTEXITCODE -ne 0) { throw "pnpm build failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "==> packages/console skipped -- install pnpm (npm install -g pnpm) to build the console UI"
}

Write-Host "==> done"
