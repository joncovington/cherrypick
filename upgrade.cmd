@echo off
rem Double-click to move cherrypick to a newer release. It shows the notes and asks first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0upgrade.ps1" %*
echo.
pause
