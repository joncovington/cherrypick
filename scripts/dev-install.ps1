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
    & $Python -m pip install -e "$root\$relativePath" 2>&1 | Write-Host
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
Install-Editable "packages\streamer[dev]"     "packages/streamer"

Write-Host "==> done"
