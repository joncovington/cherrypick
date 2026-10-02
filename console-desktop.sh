#!/usr/bin/env bash
# Open the cherrypick console in its own desktop window (optional; the browser at
# http://127.0.0.1:5070 shows exactly the same thing).
#
# A window only: it never starts or stops cherrypick, which keeps running in the background.
# If cherrypick is not running, the window says so and how to fix it.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v pnpm >/dev/null 2>&1; then
    echo "pnpm was not found. Run ./install.sh first, then try again."
    exit 1
fi

echo "Opening the cherrypick console window. The first time takes a minute while it builds."
echo "Leave this terminal open while you use it; closing the console window ends this one too."
cd "$ROOT/packages/console/desktop" && pnpm start || {
    echo
    echo "The console window could not start. Run ./install.sh again, then retry."
    exit 1
}
