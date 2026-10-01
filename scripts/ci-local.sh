#!/usr/bin/env bash
# Run the CI workflow's checks locally, against the environment dev-install already set up.
#
# Mirrors .github/workflows/ci.yml's jobs: docs (linter tests + guardrails), the Python matrix
# (ruff check, ruff format --check, pytest per package), the guard mutants (scripts/guard_mutants.py)
# and the console (install, build, test). The
# package list is read from the workflow's own matrix rather than kept here, so a package added to CI
# is run here too -- a hand-kept copy of that list is exactly how five packages once went unrun.
#
# Differences from CI: no fresh install (it tests what is installed, so run dev-install after a
# dependency change), and it runs on this machine's OS -- a case-sensitive-path failure on Linux can
# still pass here.
#
# Usage: scripts/ci-local.sh [package ...]   (no arguments: every job; "docs"/"console" name those)
#        scripts/ci-local.sh --changed [--plan]
#        Logs go to $CI_LOCAL_LOGS (default: a temp dir); the exit code is non-zero if anything fails.
#
# --changed runs only what the change can affect, measured against origin/main (committed, uncommitted
# and untracked files alike) -- the pre-push gate. The rules:
#   * docs always run (7 s);
#   * every package with a changed file runs;
#   * EVERYTHING runs when core, scripts/, .github/ or a root pyproject changes -- every package
#     depends on core, and the others are the CI machinery itself;
#   * the guard mutants run when any package they test changed (read from the mutant table itself);
#   * the console runs when it changed OR when any package it calls changed. That set is READ from the
#     console's own source (every `cherrypick.<pkg>` / `packages/<pkg>` its server code and tests name:
#     the mirror tests run module CLIs, the bridges spawn module verbs), not kept here, so a new bridge
#     is covered without anyone remembering to add it.
# What it cannot see: a package importing another's code other than through core. GitHub's CI still
# runs everything on push, so this is the fast gate, not the only one. --plan prints the targets and
# runs nothing; CI_CHANGED_FILES (newline-separated paths) overrides the git diff, for checking the rules.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="${CI_LOCAL_LOGS:-$(mktemp -d)}"
mkdir -p "$LOGS"

MATRIX="$(sed -n 's/^ *package: *\[\(.*\)\].*/\1/p' "$ROOT/.github/workflows/ci.yml" | tr -d ' ' | tr ',' ' ')"
if [ -z "$MATRIX" ]; then
    echo "could not read the package matrix from .github/workflows/ci.yml" >&2
    exit 2
fi

changed_targets() {
    local files
    if [ -n "${CI_CHANGED_FILES:-}" ]; then
        files="$CI_CHANGED_FILES"
    else
        cd "$ROOT"
        if ! git rev-parse --verify -q origin/main >/dev/null; then
            echo "docs $MATRIX guards console"  # no base to diff against: run everything
            return
        fi
        files="$( { git diff --name-only origin/main; git ls-files --others --exclude-standard; } | sort -u )"
    fi
    if echo "$files" | grep -qE '^(packages/core/|scripts/|\.github/|pyproject\.toml$)'; then
        echo "docs $MATRIX guards console"
        return
    fi
    local out="docs" pkgs="" p
    for p in $MATRIX; do
        if echo "$files" | grep -q "^packages/$p/"; then pkgs="$pkgs $p"; fi
    done
    out="$out$pkgs"
    for p in $(python "$ROOT/scripts/guard_mutants.py" --list-packages); do
        if echo "$files" | grep -q "^packages/$p/"; then out="$out guards"; break; fi
    done
    local run_console=0
    if echo "$files" | grep -q '^packages/console/'; then
        run_console=1
    else
        local deps
        deps="$(grep -rhoE 'cherrypick\.[a-z_]+|packages/[a-z_]+' \
            "$ROOT/packages/console/server/src" "$ROOT/packages/console/server/test" 2>/dev/null \
            | sed -E 's#^(cherrypick\.|packages/)##' | sort -u)"
        for p in $pkgs; do
            if echo "$deps" | grep -qx "$p"; then run_console=1; fi
        done
    fi
    if [ $run_console = 1 ]; then out="$out console"; fi
    echo "$out"
}

PLAN=0
if [ "${1:-}" = "--changed" ]; then
    [ "${2:-}" = "--plan" ] && PLAN=1
    TARGETS="$(changed_targets)"
elif [ $# -gt 0 ]; then
    TARGETS="$*"
else
    TARGETS="docs $MATRIX guards console"
fi
if [ $PLAN = 1 ]; then echo "$TARGETS"; exit 0; fi
echo "targets: $TARGETS"

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
        guards)
            cd "$ROOT"
            step guard-mutants python scripts/guard_mutants.py
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
