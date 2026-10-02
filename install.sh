#!/usr/bin/env bash
# cherrypick installer for macOS and Linux.
#
#   ./install.sh            (or: bash install.sh)
#
# Safe to run again: it reuses what is already there, never overwrites your config, and is how you
# add the optional Dolt data later. Options:
#   --yes                accept every default without asking
#   --accept-disclaimer  you have read DISCLAIMER.md and accept it
#   --skip-dolt          do not offer the Dolt setup (earnings and technicals stay off)
#   --with-desk          also install the EXPERIMENTAL manual desk (packages/desk)
#   --no-start           install only; do not start the suite
#   --config-history [--config-remote URL]  keep a git history of your settings
#
# The supervisor's cron backend on POSIX is newer than the Windows one and less proven; see
# INSTALL.md.

set -euo pipefail

YES=0; ACCEPT=0; SKIP_DOLT=0; WITH_DESK=0; NO_START=0; CONFIG_HISTORY=0; CONFIG_REMOTE=""
EXPECT_REMOTE=0
for arg in "$@"; do
    if [ "$EXPECT_REMOTE" = 1 ]; then CONFIG_REMOTE="$arg"; EXPECT_REMOTE=0; continue; fi
    case "$arg" in
        --config-history) CONFIG_HISTORY=1 ;;
        --config-remote) EXPECT_REMOTE=1 ;;
        --yes) YES=1 ;;
        --accept-disclaimer) ACCEPT=1 ;;
        --skip-dolt) SKIP_DOLT=1 ;;
        --with-desk) WITH_DESK=1 ;;
        --no-start) NO_START=1 ;;
        -h|--help) sed -n 2,15p "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
VPY="$VENV/bin/python"
RUNPY="$ROOT/packages/orchestrator/run.py"
CONSOLE_URL="http://127.0.0.1:5070"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$1"; }
fail() { printf '\n  \033[31mX %s\033[0m\n' "$1"; exit 1; }
ask() {  # ask "question" default(y|n)
    [ "$YES" = 1 ] && { [ "$2" = y ]; return; }
    local hint="[y/N]"; [ "$2" = y ] && hint="[Y/n]"
    read -r -p "    $1 $hint " answer || answer=""
    [ -z "$answer" ] && { [ "$2" = y ]; return; }
    case "$answer" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

printf '\n  cherrypick installer\n  --------------------\n'

# ------------------------------------------------------------------------------- disclaimer
step "Disclaimer"
printf '\033[33m    cherrypick is an EXPERIMENTAL PROTOTYPE for EDUCATIONAL use. It is NOT financial advice.\n'
printf '    Paper results are simulated. Its live-trading paths (off by default) place REAL,\n'
printf '    irreversible orders at your own risk; options trading can lose money quickly.\033[0m\n'
note "Full text: DISCLAIMER.md in this folder."
if [ "$ACCEPT" != 1 ]; then
    read -r -p "    Type YES to confirm you have read DISCLAIMER.md and accept it: " typed || typed=""
    [ "$typed" = "YES" ] || fail "Not accepted. Nothing was installed."
fi

# ------------------------------------------------------------------------------- prerequisites
step "Checking prerequisites"
PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$candidate"; break
    fi
done
if [ -z "$PY" ]; then
    case "$(uname -s)" in
        Darwin) fail "Python 3.11 or newer is required. Install it with:  brew install python@3.13  (or from python.org), then run this again." ;;
        *) fail "Python 3.11 or newer is required. Install it with your package manager (e.g. sudo apt install python3.12 python3.12-venv), then run this again." ;;
    esac
fi
note "Python: $("$PY" --version)"

command -v node >/dev/null 2>&1 || fail "Node.js 22 or newer is required for the console. Install it from https://nodejs.org (or: brew install node), then run this again."
NODE_MAJOR="$(node --version | sed 's/^v//; s/\..*//')"
[ "$NODE_MAJOR" -ge 22 ] || fail "Node.js $(node --version) is too old; version 22 or newer is required."
note "Node.js: $(node --version)"

if ! command -v pnpm >/dev/null 2>&1; then
    note "pnpm not found; installing it with npm"
    npm install -g pnpm@11 || fail "Installing pnpm failed. Try: sudo npm install -g pnpm@11, then run this again."
fi
note "pnpm: $(pnpm --version)"

# ------------------------------------------------------------------------------- python packages
step "Creating the Python environment (.venv)"
[ -x "$VPY" ] || "$PY" -m venv "$VENV" || fail "Creating the virtual environment failed (on Debian/Ubuntu: sudo apt install python3-venv)."
"$VPY" -m pip install --quiet --upgrade pip

step "Installing the cherrypick packages (a few minutes the first time)"
# packages/core first: every other package depends on it and it is not on PyPI.
"$VPY" -m pip install --quiet -e "$ROOT/packages/core"
for proj in "$ROOT"/packages/*/pyproject.toml; do
    pkg="$(basename "$(dirname "$proj")")"
    [ "$pkg" = core ] && continue
    if [ "$pkg" = desk ] && [ "$WITH_DESK" != 1 ]; then continue; fi
    note "$pkg"
    "$VPY" -m pip install --quiet -e "$ROOT/packages/$pkg"
done
[ "$WITH_DESK" = 1 ] || note "desk skipped (EXPERIMENTAL manual live orders; re-run with --with-desk to include it)"

# ------------------------------------------------------------------------------- console
step "Building the console (the web page you will use)"
(cd "$ROOT/packages/console" && pnpm install --silent && pnpm build)

# ------------------------------------------------------------------------------- config
step "Writing your configuration (kept if it already exists)"
"$VPY" "$RUNPY" init >/dev/null || true
note "Config: ~/.cherrypick/config.json"

step "Checking optional extras on this machine"
"$VPY" "$RUNPY" capabilities --detect --write >/dev/null
cap() { "$VPY" "$RUNPY" capabilities | "$VPY" -c "import json,sys; print(json.load(sys.stdin)['capabilities']['$1'])"; }
if [ "$(cap claude)" = True ]; then
    note "Claude Code: found - the AI advisor and narratives can be switched on in the console's Config page."
else
    note "Claude Code: not found - AI features stay off and hidden. Install Claude Code later and run this installer again to enable them."
fi

# ------------------------------------------------------------------------------- dolt
HAS_DOLT="$(cap dolt)"
if [ "$HAS_DOLT" = True ]; then
    note "Dolt data: found - earnings and technicals are available."
elif [ "$SKIP_DOLT" != 1 ]; then
    step "Optional: Dolt market data (for the earnings module and the technicals report)"
    note "Dolt is a free database tool. cherrypick uses three free public datasets from DoltHub"
    note "(earnings calendar, option history and stock history). The download is SEVERAL GB and"
    note "can take an hour or more on a slow connection."
    if ask "Set up Dolt now?" n; then
        if ! command -v dolt >/dev/null 2>&1; then
            case "$(uname -s)" in
                Darwin) note "Install Dolt with:  brew install dolt" ;;
                *) note "Install Dolt with:  sudo bash -c 'curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash'" ;;
            esac
            warn "Dolt is not installed yet. Install it with the command above, then run this installer again."
        else
            if ! dolt config --global --get user.name >/dev/null 2>&1; then
                dolt config --global --add user.name cherrypick
                dolt config --global --add user.email cherrypick@localhost
            fi
            DATA_DIR="$HOME/.cherrypick/data/earnings"
            mkdir -p "$DATA_DIR"
            for db in earnings options stocks; do
                if [ -d "$DATA_DIR/$db/.dolt" ]; then note "$db already cloned"; continue; fi
                note "Downloading $db (this is the slow part)..."
                (cd "$DATA_DIR" && dolt clone "post-no-preference/$db" "$db") || fail "Downloading $db failed; run this installer again to resume."
            done
            "$VPY" "$RUNPY" capabilities --detect --write >/dev/null
            HAS_DOLT="$(cap dolt)"
        fi
    fi
fi
if [ "$HAS_DOLT" != True ]; then
    warn "Without the Dolt data, the EARNINGS module and the TECHNICALS report are turned off"
    warn "automatically and hidden from the console. Run this installer again any time to add it."
fi

# ------------------------------------------------------------------------------- broker
step "Connect your tastytrade account"
note "cherrypick reads live market data through your tastytrade login. Paper trading never sends"
note "orders. You will need an OAuth client secret and refresh token (QUICKSTART.md shows where to"
note "get them). They are stored in your system keyring, never in a file."
CONNECTED="$("$VPY" -m cherrypick.core.auth status 2>/dev/null | "$VPY" -c "import json,sys; s=json.load(sys.stdin)['secrets']; print(bool(s.get('client_secret') and s.get('refresh_token')))" 2>/dev/null || echo False)"
if [ "$CONNECTED" = True ]; then
    note "Already connected."
elif ask "Connect now?" y; then
    "$VPY" -m cherrypick.core.auth setup || warn "Not connected. Run this installer again to try once more."
else
    warn "Skipped. Market data will not flow until you connect; run this installer again to do it."
fi

# ------------------------------------------------------------------------------- config history
step "Optional: a history of your settings"
note "cherrypick can keep a git history of your settings (config files only; never your trading"
note "data or passwords) and, if you give it one, push it to a PRIVATE repository you own, so a"
note "change can be undone and a new computer set up the same way."
if ! command -v git >/dev/null 2>&1; then
    note "git is not installed, so this is skipped. Install git and run the installer again to add it."
else
    WANT=$CONFIG_HISTORY; EXISTING=0
    [ -d "$HOME/.cherrypick/.git" ] && { WANT=1; EXISTING=1; }
    if [ "$WANT" != 1 ] && [ "$YES" != 1 ] && ask "Keep a history of your settings?" n; then WANT=1; fi
    if [ "$WANT" = 1 ]; then
        REMOTE="$CONFIG_REMOTE"
        if [ -z "$REMOTE" ] && [ "$YES" != 1 ] && [ "$EXISTING" != 1 ]; then
            read -r -p "    Private git repository URL to push to (Enter to keep it on this computer only): " REMOTE || REMOTE=""
        fi
        if [ -n "$REMOTE" ]; then
            "$VPY" "$RUNPY" config-backup --init --enable --remote "$REMOTE" >/dev/null && OK=1 || OK=0
        else
            "$VPY" "$RUNPY" config-backup --init --enable >/dev/null && OK=1 || OK=0
        fi
        if [ "$OK" = 1 ]; then note "On: your settings are committed every 15 minutes when they change."
        else warn "Could not set it up; run '.venv/bin/python packages/orchestrator/run.py config-backup --init' to see why."; fi
    else
        note "Skipped. You can switch it on later on the console's Config page or with run.py config-backup."
    fi
fi

# ------------------------------------------------------------------------------- start
if [ "$NO_START" = 1 ]; then
    step "Installed (not started, as asked)"
    note "Start it later with:  .venv/bin/python packages/orchestrator/run.py install"
    exit 0
fi
step "Starting cherrypick"
note "This adds one entry to your crontab that keeps cherrypick running in the background,"
note "including after a restart. Uninstall any time with ./uninstall.sh."
"$VPY" "$RUNPY" install

note "Waiting for the console to come up..."
UP=0
for _ in $(seq 1 45); do
    if "$VPY" -c "import urllib.request; urllib.request.urlopen('$CONSOLE_URL', timeout=2)" 2>/dev/null; then UP=1; break; fi
    sleep 2
done
echo
if [ "$UP" = 1 ]; then
    printf '  \033[32mDone. The console is at %s\033[0m\n' "$CONSOLE_URL"
    (command -v open >/dev/null && open "$CONSOLE_URL") || (command -v xdg-open >/dev/null && xdg-open "$CONSOLE_URL") || true
else
    printf '  \033[32mInstalled. The console is starting in the background; open %s in a minute.\033[0m\n' "$CONSOLE_URL"
    echo "  If it never loads, run:  .venv/bin/python packages/orchestrator/run.py doctor"
fi
echo "  Everything runs in PAPER mode. Live trading stays off until you deliberately turn it on."
echo
echo "  Open the console any time at $CONSOLE_URL (bookmark it); cherrypick keeps running in the background."
echo "  To run cherrypick commands yourself, open a terminal in this folder ($ROOT) and activate"
echo "  the virtual environment first:  source .venv/bin/activate   (QUICKSTART.md explains)"
