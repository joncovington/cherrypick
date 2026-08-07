Run one iteration of the MEIC parallel-shadow paper-trading loop.

As of the standalone runner, the paper loop is implemented in code (`cherrypick/meic/paper_loop.py`), not agent orchestration — this keeps a single source of truth for the iteration logic. Prefer the unattended daemon (`/paper-start`) for a full session; use this skill for a **single manual iteration** (e.g. a one-off force-close pass, or a quick check).

## Run one iteration

```bash
python -m cherrypick.meic.paper_loop --once
```

This runs, for every symbol in `config.json`'s `symbols`, one full pass: fetch the live underlying price + IV rank, the shared VIX / VIX1D (→ ratio, → VIX-banded short delta), and GEX; build wing-width candidates from `wing_widths_by_symbol`; then hand the snapshot to `paper.process_symbol`, which marks/exits every open paper IC across every enabled arm in `config.risk.json` (currently `control`/`open`/`width-5`/`width-10` — see `docs/paper-experiments.md`) — per-side stops, the settlement-aware force-close cascade with the physically-settled early close + assignment/pin friction, and cash-settled left-to-expire settlement — no profit target — and evaluates new entries per arm. All writes go to `~/.cherrypick/data/meic/paper_trades.db`; the live account and `~/.cherrypick/data/meic/meic_trades.db` are never touched.

Report the per-symbol, per-profile outcomes from the JSON it prints (fills, skip reasons, or exits).

## Unattended session

For a full session that runs on its own without per-iteration invocation, use `/paper-start`, which registers a Windows scheduled task running `--once` every 2 minutes (robust, self-healing, persists across sessions, time-gated to market hours). Manage it directly with:

```bash
python -m cherrypick.meic.paper_loop --install-task    # register + fire the first run (recommended)
python -m cherrypick.meic.paper_loop --status          # daemon/task status + open-position count
python -m cherrypick.meic.paper_loop --uninstall-task  # stop the unattended session
python -m cherrypick.meic.paper_loop --eod-report       # write logs/paper-eod-<date>.md now (--date to backfill)
```

The daemon also writes that deterministic end-of-day report automatically, once, at the 16:00 settlement pass — a per-profile metrics table (trades, win rate, net P&L, expectancy, profit factor, max drawdown), an exits-by-reason breakdown, and per-symbol P&L. It's code-generated (no agent), distinct from the agent-synthesized `/paper-report`.

On non-Windows hosts, run `python -m cherrypick.meic.paper_loop` in a terminal (or wire a cron job to `--once`). A long-running detached daemon (`--start`) also exists but is less robust on Windows than the scheduled task.

Details of the metrics, gates, force-close cascade, and graduation criteria are in `docs/paper-trading.md`.
