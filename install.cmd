@echo off
rem Double-click to install cherrypick. Runs install.ps1 with this window kept open at the end.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
