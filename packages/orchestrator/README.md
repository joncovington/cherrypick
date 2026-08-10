# cherrypick — the orchestrator

**The thing that runs everything else.** It drives the strategy modules on a schedule, watches that
they are actually working, tells you when they are not, and gives you one place to read the results
across all of them.

It drives the modules **by subprocess**, using paths from config — it never imports them, never edits
their internals, and **never places a live order**. Its one live-adjacent action is onboarding: helping
you pick which account a module would trade in if you ever enabled live trading.

New to the suite? Start with the [User Guide](../../docs/PROJECT.md) instead — it covers installing and
running the whole thing in plain language. This README is the orchestrator package itself.

## Install

From a fresh clone, the shared library goes first or every `import cherrypick.core…` fails:

```bash
pip install -e packages/core        # from the repo root — do this first
cd packages/orchestrator
pip install -e ".[dev]"
```

`scripts/dev-install.ps1` (or `.sh`) from the repo root does that plus every other package in one go.

## The five commands that matter

Run them as `python run.py <cmd>` from this directory, or as `cherrypick <cmd>` once pip-installed.

```bash
python run.py doctor      # is everything ready? green/red, read-only, safe to run any time
python run.py install     # register the one anchor task, start the supervisor, streamer, and services
python run.py status      # what the supervisor is running, and when each job last ran
python run.py report      # unified paper P&L across every module, gross and net of costs
python run.py dashboard   # regenerate the status page (--serve for the live one)
```

`doctor` is the one to reach for first when something looks wrong; it checks each module's paths,
credentials, jobs, and the data feed, and prints a line per check. `python run.py uninstall` stops
everything cleanly and leaves your recorded data and settings untouched.

Every other command — calibration, the EOD digest and reports, reconcile, archive, secrets, onboarding
— is in the [CLI reference](../../docs/orchestrator-cli.md), which documents all of them and is checked
against the code so it cannot quietly fall behind.

## Where things live

Nothing runtime lands in this checkout. Config, state, logs, reports, and the dashboard all resolve
under **`~/.cherrypick`** (relocate the lot with `$CHERRYPICK_HOME`):

```
~/.cherrypick/config.json     # this package's config — start from config.example.json, which annotates every key
~/.cherrypick/logs/           # suite + per-module logs, EOD digests, insights
~/.cherrypick/state/          # supervisor job state, heartbeats, watchdog state
~/.cherrypick/dashboard.html  # the static status page
```

Module paths inside `config.json` resolve **relative to that file's directory**, so nothing hardcodes
an absolute path. `python run.py settings` opens a local editor for every config file in the suite plus
the keyring secrets manager.

One scheduling fact worth knowing up front: since the 2026-08-09 cutover the OS scheduler holds exactly
**one** cherrypick entry (`cherrypick-supervisor`). Everything else is a supervisor job re-derived from
config on every pass, so changing a cadence is a config edit — there is no task to re-register.

## Testing

```bash
python -m pytest                    # default lane (-m "not live")
ruff check . && ruff format .       # line-length 110
```

## Further reading

- [CLAUDE.md](CLAUDE.md) — the architecture, and the invariants that constrain changes here. Read the
  invariants before changing anything on the watchdog or notification path; each one records the
  incident that produced it.
- [docs/orchestrator-cli.md](../../docs/orchestrator-cli.md) — every command and flag.
- [docs/operations.md](../../docs/operations.md) — the runbook: job inventory, ports, the morning check.
- [docs/README.md](../../docs/README.md) — the suite documentation index.
- `docs/design.md` and `ROADMAP.md` in this package are **frozen records**, not current reference; each
  says so in its own header.
