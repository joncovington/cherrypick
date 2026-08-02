<#
.SYNOPSIS
  Run the MEIC core.broker DRY-RUN smoke (phase-5 gate) with pre-flight checks.

.DESCRIPTION
  Wrapper over cherrypick.meic.live_smoke: warns if the market is closed or the deploy governor is off,
  then runs the supervised dry run. Nothing is ever placed -- the harness has no live code path,
  and enable_live_trading stays false throughout. You will be shown the exact order and must
  type DRY-RUN to send it to the broker's dry-run preflight.

.EXAMPLE
  .\src\live_smoke.ps1                 # XSP, 1 contract, interactive confirm
  .\src\live_smoke.ps1 -Symbol SPX -WingWidth 10
#>
param(
    [string]$Symbol = "XSP",
    [int]$Quantity = 1,
    [double]$WingWidth = 0,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # script lives in src/; package root is one up
Set-Location $root

Write-Host "== MEIC core.broker dry-run smoke ==" -ForegroundColor Cyan

# Market-hours advisory (ET). The smoke needs today's 0DTE chains and live quotes; it fails
# cleanly outside RTH, but say so up front rather than after a broker round-trip.
$et = [System.TimeZoneInfo]::ConvertTime([DateTime]::UtcNow,
    [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time"))
$weekday = $et.DayOfWeek -notin @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)
$inRth = $weekday -and ($et.TimeOfDay -ge [TimeSpan]"09:30") -and ($et.TimeOfDay -le [TimeSpan]"15:30")
if (-not $inRth) {
    Write-Host ("WARNING: it is {0} ET - outside regular hours (Mon-Fri 09:30-15:30)." -f $et.ToString("ddd HH:mm")) -ForegroundColor Yellow
    Write-Host "         The scan will likely fail on dte/quotes. Best run mid-session on a trading day." -ForegroundColor Yellow
}

# Governor advisory: the smoke exists to verify real broker behavior, and an exercised deploy
# governor is part of that. account_deploy_limit_pct=0 reports OFF instead of a verdict.
$cfgPath = Join-Path $root "config.json"
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $limit = $cfg.account_deploy_limit_pct
        if (-not $limit) {
            Write-Host "NOTE: account_deploy_limit_pct is 0 (governor OFF). Set e.g. 50 in config.json" -ForegroundColor Yellow
            Write-Host "      to exercise a real governor verdict before the live loop is built on it." -ForegroundColor Yellow
        }
        if ($cfg.enable_live_trading) {
            Write-Host "WARNING: enable_live_trading is TRUE in config.json. The smoke never passes --live," -ForegroundColor Red
            Write-Host "         but for the smoke session it should be false. Consider flipping it first." -ForegroundColor Red
        }
    } catch {
        Write-Host "NOTE: could not parse config.json for advisories; continuing." -ForegroundColor Yellow
    }
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $py) { Write-Host "ERROR: python not found on PATH." -ForegroundColor Red; exit 1 }

$argv = @("-m", "cherrypick.meic.live_smoke", "--symbol", $Symbol, "--quantity", $Quantity)
if ($WingWidth -gt 0) { $argv += @("--wing_width", $WingWidth) }
if ($Yes) { $argv += "--yes" }

Write-Host ""
& $py.Source @argv
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host "`nSMOKE PASSED - remember the manual check: tastytrade UI must show NO working order." -ForegroundColor Green
} else {
    Write-Host "`nSmoke exited with code $code (see checks above)." -ForegroundColor Yellow
}
exit $code
