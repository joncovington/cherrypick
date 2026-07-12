# cherrypick

**Unattended paper-trading data collection with a walk-away reliability guarantee.**
cherrypick is the umbrella orchestrator for a trading-tool suite: it drives sibling trading modules on an
OS schedule for hands-off **paper** data collection, and a watchdog + notifications tell you the moment
anything stalls. It never places live trades.

<!-- Badges: swap the repo owner/name if you fork -->
![CI](https://img.shields.io/github/actions/workflow/status/joncovington/cherrypick/ci.yml?branch=main)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-active-brightgreen)

---

## Quick Start

```bash
git clone --recurse-submodules https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/umbrella
pip install -e ".[dev]"

cp config.example.json config.json     # edit module paths + notify channels
python run.py connect --module meic     # store broker creds + pick the account (OS keyring)
python run.py doctor                     # green/red readiness check (read-only)
python run.py install                    # register the unattended schedule (Windows/POSIX)
```

Look at collected data anytime: `python run.py report` · live dashboard: `python run.py dashboard --serve`.

## Key Features

- 🤖 **Hands-off paper collection** — drives each module's pipeline on an OS schedule (Task Scheduler / cron).
- 🛟 **Watchdog reliability guarantee** — health checks, benign auto-restart of a dead streamer, and
  dedup/re-notify/recovery alerting. Uses only the stdlib + OS shell — **no network/AI on the reliability path**.
- 🔔 **Notifications** — log · desktop · Slack · Discord, plus a low-latency trade-fill push. Secrets in the OS keyring only.
- 📊 **Read surfaces** — cross-module P&L (`report`), promotion advisor (`calibrate`), a self-contained
  status **dashboard** (static + live `--serve` with embedded module dashboards), and a paper↔live
  isolation guard (`reconcile`).
- 🩺 **`doctor`** one-shot readiness check · **`connect`/`account`** guided onboarding + live-account selection.
- 🔒 **Paper↔live isolation** — only ever touches paper engines/DBs; account numbers masked to `****1234`.

## Tech Stack

- **Python 3.11+** (stdlib-heavy; tested on 3.13) — monorepo: `packages/{umbrella,meic,earnings}`
- **cherrypick-core** shared library (git submodule) · **SQLite** paper DBs · **Dolt** (earnings market data)
- **tastytrade** OAuth broker SDK · **keyring** for all secrets · DXLink streaming
- **Windows Task Scheduler / POSIX cron** scheduling · stdlib `http.server` dashboard (no web framework)
- **pytest** · **Ruff** · **GitHub Actions** CI (matrix over all three packages)

## Documentation

Full project reference — architecture, setup, config, CLI, testing & deployment — in
**[`docs/PROJECT.md`](docs/PROJECT.md)**. Per-package guidance lives in each package's `CLAUDE.md`.

## License

[MIT](LICENSE) © joncovington
