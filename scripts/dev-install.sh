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

for pkg in orchestrator meic earnings gex flies streamer desk; do
    echo "==> packages/$pkg"
    "$PYTHON" -m pip install -e "$ROOT/packages/$pkg[dev]"
done

# The console UI is the one Node package. Optional: skipped with a notice when pnpm is absent,
# so the Python-only setup stays one command with no new required toolchain.
if command -v pnpm >/dev/null 2>&1; then
    echo "==> packages/console (pnpm install + build)"
    (cd "$ROOT/packages/console" && pnpm install && pnpm build)
else
    echo "==> packages/console skipped -- install pnpm (npm install -g pnpm) to build the console UI"
fi

echo "==> done"
