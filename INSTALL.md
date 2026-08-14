# Install

Just the commands. For what each step does and why, see the [README](README.md#quick-start); for a
plain-language walkthrough, the [User Guide](docs/PROJECT.md).

**You need:** a [tastytrade](https://tastytrade.com) account · Python 3.11+ · git · a machine that
stays awake during market hours (Windows recommended).
**Optional:** Node 22+ and [pnpm](https://pnpm.io) (web console) · [Dolt](https://github.com/dolthub/dolt)
(earnings engine) · [Claude Code](https://docs.claude.com/en/docs/claude-code) (agent-driven sessions).

## 1. Clone and install

```bash
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick

./scripts/dev-install.sh                   # macOS / Linux / Git Bash
powershell -File scripts\dev-install.ps1   # Windows PowerShell
```

Installs `packages/core` first (required), then every Python package, then builds the console if
pnpm is present (skipped with a notice if not).

## 2. Configure

```bash
cd packages/orchestrator
python run.py init          # writes ~/.cherrypick/config.json from the annotated template
python run.py settings      # optional: edit it in the local web editor (loopback :8804)
```

## 3. Connect your broker

```bash
python run.py connect       # wizard: tastytrade login, account designation, optional webhooks
```

Credentials go to the OS keyring, never a file. Everything after the login is skippable with Enter.

## 4. Check, then turn on

```bash
python run.py doctor        # green/red readiness — read-only, safe any time
python run.py install       # registers the one anchor task, starts the supervisor + data feed
```

Done. It now collects paper-trading data on its own.

## Verify

```bash
python run.py status        # what the supervisor is running
python run.py notify-test   # fire a test notification through your channels
python run.py report        # paper P&L once data has accumulated
```

The console is at <http://127.0.0.1:5070> — the supervisor keeps it running; nothing to start.

## Stop

```bash
python run.py uninstall     # stops everything; recorded data and settings stay untouched
```

Live trading is **off** by default everywhere, and none of the steps above enables it. Before you
consider changing that, read
[the warning in the README](README.md#before-you-go-anywhere-near-live-trading).
