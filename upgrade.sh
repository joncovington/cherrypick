#!/usr/bin/env bash
# Move this cherrypick install to a newer release on macOS/Linux.
#
#   ./upgrade.sh [options]
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

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPY="$ROOT/.venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "  X This folder has no .venv, so it is not an installed cherrypick. Run ./install.sh first." >&2
    exit 1
fi
exec "$VPY" "$ROOT/packages/orchestrator/run.py" upgrade "$@"
