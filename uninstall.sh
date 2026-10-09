#!/usr/bin/env bash
# Stop cherrypick completely on macOS/Linux: nothing scheduled, nothing running.
#
# Your data, configuration and saved broker login are KEPT (~/.cherrypick and the system keyring),
# so running ./install.sh again picks up where you left off. To remove them too, delete ~/.cherrypick
# and this folder by hand afterwards.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPY="$ROOT/.venv/bin/python"
[ -x "$VPY" ] || VPY=python3
RUNPY="$ROOT/packages/orchestrator/run.py"

echo "==> Removing the scheduled entry and stopping the supervisor and services"
"$VPY" "$RUNPY" uninstall
echo "==> Stopping the streamer, the console and anything else still running"
"$VPY" "$RUNPY" stop --all

# The Dolt server the earnings module kept alive, if it is on its usual port. Only a process that is
# actually `dolt` is stopped; anything else on the port is left alone.
# lsof when present; otherwise the suite's own probe (/proc on Linux), since a minimal server often
# has no lsof and Dolt was left running there.
if command -v lsof >/dev/null 2>&1; then
    DOLT_PIDS="$(lsof -ti tcp:3306 -sTCP:LISTEN 2>/dev/null)"
else
    DOLT_PIDS="$(PYTHONPATH="$ROOT/packages/orchestrator/src" "$VPY" -c \
        'from cherrypick.orchestrator.util import port_owner_pid; print(port_owner_pid(3306) or "")' 2>/dev/null)"
fi
for pid in $DOLT_PIDS; do
    if [ "$(ps -p "$pid" -o comm= 2>/dev/null | xargs basename 2>/dev/null)" = dolt ]; then
        echo "==> Stopping the Dolt server"
        kill "$pid"
    fi
done

echo
echo "cherrypick is stopped and will not restart. Your data and settings are kept in ~/.cherrypick."
