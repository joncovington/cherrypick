# cherrypick — the orchestrator

**The thing that runs everything else.** It drives the strategy modules on a schedule, watches that
they are actually working, tells you when they are not, and gives you one place to read the results
across all of them.

It drives the modules **by subprocess**, using paths from config — it never imports them, never edits
their internals, and **never places a live order** itself. Its one live-adjacent configuration action is
onboarding: helping you pick which account a module would trade in if you ever enabled live trading.
(It does run a module's own live loop as a job while a human has armed that module for the day: MEIC,
and flies or bwb if you reinstate them. Earnings is paper-only. A person arms a loop with `--arm
--confirm YES` on its CLI; see [guardrails-and-modes.md](../../docs/guardrails-and-modes.md).)

New to the suite? Start with [QUICKSTART.md](../../QUICKSTART.md) or the
[User Guide](../../docs/PROJECT.md) instead — they cover installing and running the whole thing in plain
language. This README is the orchestrator package itself.

## Install

The root installer (`install.cmd` / `install.ps1` on Windows, `./install.sh` elsewhere) installs this
package with the rest of the suite into a `.venv` in the checkout and runs `run.py install` for you —
see [INSTALL.md](../../INSTALL.md). By hand, the shared library goes first or every
`import cherrypick.core…` fails:

```bash
pip install -e packages/core        # from the repo root — do this first
cd packages/orchestrator
pip install -e ".[dev]"
```

For development, `scripts/dev-install.ps1` (or `.sh`) from the repo root does that plus every other
package, with the `[dev]` extras, in one go.

## The commands that matter

Run them as `python run.py <cmd>` from this directory, or as `cherrypick <cmd>` once pip-installed.

```bash
python run.py doctor      # is everything ready? green/red, read-only, safe to run any time
python run.py install     # register the one anchor task, start the supervisor, streamer, and services
python run.py status      # what the supervisor is running, and when each job last ran
python run.py report      # unified paper P&L across every module, gross and net of costs
# The status page is the console: http://127.0.0.1:5070 (supervisor-managed; see /console)
```

`doctor` is the one to reach for first when something looks wrong; it checks each module's paths,
credentials, jobs, capabilities and the data feed, and prints a line per check. `python run.py
uninstall` removes the anchor task and stops the supervisor and its services, leaving the streamer, the
console and Dolt running by design; `python run.py stop --all` after it is the full stop (the root
`uninstall.cmd` / `uninstall.sh` runs both). Recorded data and settings are untouched.

`python run.py capabilities` shows whether this machine can carry the optional features (`dolt` for
earnings and technicals, `claude` for the advisor and narratives), and `--detect --write` records it.
Push notifications are **off by default**: every channel list in `config.example.json` is `["log"]`,
so add `desktop`, `discord`, `slack` or `telegram` to turn one on (webhooks, and the Telegram bot token
and chat ID, via `run.py secrets-set --channel <name>`; never in a config file).

**External reports** let you add your own daily report without touching the suite: list a script under
`~/.cherrypick` in `config.json`'s `external_reports`, and the supervisor runs it each day and the
console draws the JSON it writes (optionally posting a text card to Discord). `python run.py
external-check <file>` shows what a report file will look like, or why it will not. See the
[guide](../../docs/guide/external-reports.md).

**Retired modules ship off.** Flies, curve, pmcc and bwb are retired: each has `modules.<name>.enabled`
false, so the supervisor derives none of their jobs.
Their ledgers and code stay, and the console keeps their pages under Experiments. A retired module that
still holds open positions is wound down by its own setting (pmcc's `defaults.wind_down` closes what is
open and the module goes off once flat; curve's last spreads were managed to their expiry). To reinstate
one, set its module switch and its arms' `enabled` flags back to true (each module's CLAUDE.md says
which); a live loop of a retired module also refuses to arm while its switch is off, on both the
`--arm` and the slash-command path.

Every other command — calibration, the end-of-day review, reconcile, archive, backup, secrets, onboarding
— is in the [CLI reference](../../docs/orchestrator-cli.md), which documents all of them and is checked
against the code so it cannot quietly fall behind.

## Where things live

Nothing runtime lands in this checkout. Config, state, logs and reports all resolve
under **`~/.cherrypick`** (relocate the lot with `$CHERRYPICK_HOME`):

```
~/.cherrypick/config.json     # this package's config (and `capabilities`) — config.example.json annotates every key
~/.cherrypick/logs/           # suite + per-module logs (the EOD review lives in data/review/)
~/.cherrypick/state/          # supervisor job state, heartbeats, watchdog state
```

A module's relative `path` in `config.json` (`../meic`) resolves against **this package's directory**,
and data paths start with `~`, so nothing hardcodes an absolute path. With `$CHERRYPICK_HOME` set, a
relative module `path` resolves against that directory instead, so give absolute ones. `python run.py settings` opens a local editor for every config file in the suite plus
the keyring secrets manager.

One scheduling fact worth knowing up front: the OS scheduler holds exactly
**one** cherrypick entry (`cherrypick-supervisor`). Everything else is a supervisor job re-derived from
config on every pass, so changing a cadence is a config edit — there is no task to re-register.

## Testing

```bash
python -m pytest                    # default lane (-m "not live")
ruff check . && ruff format .       # line-length 110
```

## Further reading

- [CLAUDE.md](CLAUDE.md) — the architecture, and the invariants that constrain changes here. Read the
  invariants before changing anything on the watchdog or notification path; each one carries its
  reason.
- [docs/orchestrator-cli.md](../../docs/orchestrator-cli.md) — every command and flag.
- [docs/operations.md](../../docs/operations.md) — the runbook: job inventory, ports, the morning check.
- [docs/README.md](../../docs/README.md) — the suite documentation index.

## Commands

```bash
# Fresh clone: scripts\dev-install.ps1 (or .sh) from the repo root, or pip install -e packages/core first.
# From a source checkout use run.py (never create a root cherrypick.py — see Gotchas); if pip-installed, `cherrypick <cmd>`.
python run.py doctor             # green/red readiness (read-only)
python run.py install            # register the ONE anchor task, start supervisor + streamer/services, delete legacy per-job tasks; refuses while any live module is armed today (--force overrides)
python run.py uninstall          # remove cherrypick-managed tasks
python run.py status             # supervisor job registry + heartbeats
python run.py watchdog           # one watchdog pass (the supervisor's 10-minute job)
python run.py supervise          # supervisor daemon loop, foreground (--stop asks a running one to exit)
python run.py ensure-supervisor  # the anchor's probe: restart a dead/stale supervisor; one CRITICAL after 3 failed probes
python run.py streamer-health    # one streamer-liveness pass (the 60s in-session job)
python run.py preopen-check      # deprecated alias for streamer-health (honours the legacy preopen flag)
python run.py report             # cross-module paper P&L; --eod / --date YYYY-MM-DD for one session; --live reads the live-tagged ledgers (never feeds calibrate/promotion)
python run.py calibrate          # per-arm calibration readings + promotion recommendations
python run.py positions          # live P/L of the REAL broker account (read-only, on demand); --detail / --account <last4> / --json
python run.py manage             # daily position evaluation: broker snapshot (read-only) judged by packages/manage
python run.py archive            # monthly rotation of finished months' reports + logs to logs/archive/ (--dry-run / --month YYYY-MM)
python run.py backup             # nightly suite backup (the suite-backup job, 01:30 ET): ONE verified zip, rewritten; a bad night never replaces it (--dry-run / --list / --verify / --restore-to DIR)
python run.py settings           # local config editor + secrets manager on loopback:8804; --organize [target] [--apply] reorders configs instead
python run.py migrate-home       # dry-run move of config into ~/.cherrypick (--apply to perform)
python run.py queue-add --study item17 --task fetch-chains --task-args '{"requests": "<file>"}'  # queue a research task named study/step (slugs; step defaults to the kind; --output for a script's conclusion); queue-list [--study] / queue-cancel --task-id N; the research-queue job runs it
python -m pytest                 # default lane `-m "not live" -q`; markers unit, live, windows
python -m pytest tests/test_report.py::test_report_unifies_pnl_net_of_costs_across_modules  # one test
ruff check . ; ruff format .     # line-length 110
```
