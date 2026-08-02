#!/usr/bin/env bash
# One-command dev environment setup for the cherrypick suite monorepo.
#
# packages/core is not on PyPI (Private :: Do Not Upload) -- every other package depends on it as a
# plain named dependency ("cherrypick-core") resolved only from what's already installed, so it MUST
# be installed first or every later `pip install -e .` fails to resolve it.
#
# Usage: scripts/dev-install.sh [python-executable]

set -euo pipefail

PYTHON="${1:-python}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> packages/core (must be first)"
"$PYTHON" -m pip install -e "$ROOT/packages/core[dev]"

for pkg in orchestrator meic gex flies streamer; do
    echo "==> packages/$pkg"
    "$PYTHON" -m pip install -e "$ROOT/packages/$pkg[dev]"
done

# earnings has no pyproject.toml yet (see the plan's Project B) -- its own requirements.txt carries
# `-e ../core` as its first line, and pip resolves that relative path against the CURRENT WORKING
# DIRECTORY, not the file's location, so this must run from inside packages/earnings.
echo "==> packages/earnings"
(cd "$ROOT/packages/earnings" && "$PYTHON" -m pip install -r requirements-dev.txt)

echo "==> done"
