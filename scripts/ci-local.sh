#!/usr/bin/env bash
# Run the CI workflow's checks locally, against the environment dev-install already set up.
#
# Mirrors .github/workflows/ci.yml's three jobs: docs (linter tests + guardrails), the Python matrix
# (ruff check, ruff format --check, pytest per package) and the console (install, build, test). The
# package list is read from the workflow's own matrix rather than kept here, so a package added to CI
# is run here too -- a hand-kept copy of that list is exactly how five packages once went unrun.
#
# Differences from CI: no fresh install (it tests what is installed, so run dev-install after a
# dependency change), and it runs on this machine's OS -- a case-sensitive-path failure on Linux can
# still pass here.
#
# Usage: scripts/ci-local.sh [package ...]   (no arguments: every job; "docs"/"console" name those)
#        Logs go to $CI_LOCAL_LOGS (default: a temp dir); the exit code is non-zero if anything fails.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="${CI_LOCAL_LOGS:-$(mktemp -d)}"
mkdir -p "$LOGS"

MATRIX="$(sed -n 's/^ *package: *\[\(.*\)\].*/\1/p' "$ROOT/.github/workflows/ci.yml" | tr -d ' ' | tr ',' ' ')"
if [ -z "$MATRIX" ]; then
    echo "could not read the package matrix from .github/workflows/ci.yml" >&2
    exit 2
fi

if [ $# -gt 0 ]; then TARGETS="$*"; else TARGETS="docs $MATRIX console"; fi

failed=0
step() {
    local name="$1"; shift
    printf '%-28s' "$name"
    if "$@" >"$LOGS/$name.log" 2>&1; then
        echo "ok"
    else
        echo "FAIL  ($LOGS/$name.log)"
        failed=1
    fi
}

for t in $TARGETS; do
    case "$t" in
        docs)
            cd "$ROOT"
            step docs-linter-tests python -m pytest tools/tests -q
            step docs-check python tools/check_docs.py
            ;;
        console)
            cd "$ROOT/packages/console"
            step console-install pnpm install --frozen-lockfile
            step console-build pnpm build
            step console-test pnpm test
            ;;
        *)
            if [ ! -d "$ROOT/packages/$t" ]; then echo "no such package: $t" >&2; failed=1; continue; fi
            cd "$ROOT/packages/$t"
            step "$t-ruff" ruff check .
            step "$t-format" ruff format --check .
            if [ "$t" = orchestrator ]; then
                # Same as CI: the auth-CLI smoke test needs a real keyring backend.
                PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring step "$t-pytest" pytest -q
            else
                step "$t-pytest" pytest -q
            fi
            ;;
    esac
done

echo "logs: $LOGS"
exit $failed
