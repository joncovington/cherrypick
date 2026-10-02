@echo off
rem Double-click to stop cherrypick completely. Your data and settings are kept.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
echo.
pause
