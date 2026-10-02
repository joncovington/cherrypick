@echo off
rem Open the cherrypick console in its own desktop window (optional; the browser at
rem http://127.0.0.1:5070 shows exactly the same thing).
rem
rem A window only: it never starts or stops cherrypick, which keeps running in the background.
rem If cherrypick is not running, the window says so and how to fix it.

where pnpm >nul 2>nul
if errorlevel 1 (
    echo pnpm was not found. Run install.cmd first, then try again.
    pause
    exit /b 1
)

echo Opening the cherrypick console window. The first time takes a minute while it builds.
echo Leave this window open while you use it; closing the console window ends this one too.
cd /d "%~dp0packages\console\desktop"
call pnpm start
if errorlevel 1 (
    echo.
    echo The console window could not start. Run install.cmd again, then retry.
    pause
)
