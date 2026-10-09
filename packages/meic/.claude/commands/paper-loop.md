Run one iteration of the MEIC parallel-shadow paper-trading loop.

As of the standalone runner, the paper loop is implemented in code (`cherrypick/meic/paper_loop.py`), not agent orchestration — this keeps a single source of truth for the iteration logic. Prefer the unattended daemon (`/paper-start`) for a full session; use this skill for a **single manual iteration** (e.g. a one-off force-close pass, or a quick check).

## Run one iteration

```bash
python -m cherrypick.meic.paper_loop --once
```

This runs, for every symbol in `config.json`'s `symbols`, one full pass: fetch the live underlying price + IV rank, the shared VIX / VIX1D (→ ratio, → VIX-banded short delta), and GEX; build wing-width candidates from `wing_widths_by_symbol`; then hand the snapshot to `paper.process_symbol`, which marks/exits every open paper IC across every enabled arm in the arm registry (`$MEIC_RISK_CONFIG`, else `~/.cherrypick/config/meic.risk.json`, else the shipped control-only `config.risk.example.json`) — see `docs/paper-experiments.md` — per-side stops, the settlement-aware force-close cascade with the physically-settled early close + assignment/pin friction, and cash-settled left-to-expire settlement — no profit target — and evaluates new entries per arm. All writes go to `~/.cherrypick/data/meic/paper_trades.db`; the live account and `~/.cherrypick/data/meic/meic_trades.db` are never touched.

Report the per-symbol, per-profile outcomes from the JSON it prints (fills, skip reasons, or exits).

## Unattended session

A full unattended session is the orchestrator supervisor's `meic-paper` job (`--once` on the configured cadence, time-gated to market hours, on every OS); `/paper-start` checks it is being driven. Useful by hand:

```bash
python -m cherrypick.meic.paper_loop --status          # daemon status + open-position count
python -m cherrypick.review build --session <date>      # the suite review for one session (all modules)
```

The daemon also writes that deterministic end-of-day report automatically, once, at the 16:00 settlement pass — a per-profile metrics table (trades, win rate, net P&L, expectancy, profit factor, max drawdown), an exits-by-reason breakdown, and per-symbol P&L. It's code-generated (no agent), distinct from the agent-synthesized `/paper-report`.

Without the supervisor, run `python -m cherrypick.meic.paper_loop` in a terminal. A long-running detached daemon (`--start`) also exists but is less robust on Windows than the supervisor. The module's own Windows scheduled task (`--install-task`) was removed on 2026-10-08.

Details of the metrics, gates, force-close cascade, and graduation criteria are in `docs/paper-trading.md`.
