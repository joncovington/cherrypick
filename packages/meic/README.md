# cherrypick-meic

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. This module has a live-trading path (`enable_live_trading`, off by default)
> that places real orders through your broker account. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before use.**

**What this module does:** MEIC trades multiple-entry iron condors — a defined-risk,
premium-selling strategy — on same-day-expiring (0DTE) index options like SPX and XSP. It can
run as **paper trading** (simulated, no real money, the recommended starting point) or as a
**live** agent that watches the market and places real orders, gated behind an explicit setting
you have to turn on yourself. It's one strategy module in the cherrypick suite, alongside
earnings plays, a GEX engine, and the 0DTE butterfly module — see the suite overview below for
how they fit together. Most of what you do here is run terminal commands, or ask Claude to run
a `/`-prefixed command on your behalf — no coding required.

> **The MEIC module of the [cherrypick](../../README.md) suite.** cherrypick is a monorepo of trading
> modules driven by a shared **orchestrator**. This package (`packages/meic`) is the 0DTE iron-condor
> engine; its siblings are [`packages/earnings`](../earnings) (overnight earnings plays),
> [`packages/gex`](../gex) (the gamma-exposure engine), and [`packages/orchestrator`](../orchestrator)
> (the orchestrator). It can run standalone from this folder for live / interactive trading, or unattended
> for paper collection — where the orchestrator's supervisor drives it by subprocess (`cherrypick install`),
> never by import. See [How this fits the suite](#how-this-fits-the-suite) below, this module's own
> [docs/](docs/README.md), and the suite-wide [documentation index](../../docs/README.md).

An autonomous options trading agent running the **Multiple Entry Iron Condor (MEIC)** strategy on 0DTE index options. Rather than a traditional rules-only trading-bot framework, the agent itself runs the decision loop every few minutes during market hours, reading live market data, checking a stack of risk gates, and deciding whether to enter, hold, or close positions. It runs inside **[Claude Code](https://docs.claude.com/en/docs/claude-code)** (Anthropic's agentic CLI), which executes the operating instructions in `CLAUDE.md` and the skills in `.claude/commands/`. It talks to tastytrade directly via their official Python SDK (OAuth2, no middleman broker API). Live trading is gated behind an explicit config flag and defaults to dry-run.

Shared logic (market calendar, fee schedule) comes from the **`cherrypick.core`** library, a sibling package (`packages/core`) in this same monorepo. The suite's installer at the repo root (`install.cmd` on Windows, `install.sh` on macOS/Linux) installs it along with this package; installing by hand, `pip install -e ../core` has to come first, before any `import cherrypick.core...` resolves here. The distribution name in `pyproject.toml` is **`meicagent`** (the import package is `cherrypick.meic`), which is the name `pip show` and `pip uninstall` expect.

---

## Quick start

> **Installed the suite with the root installer?** Then this package and its paper loop are already
> set up: the supervisor (`run.py install`, which the installer runs) ticks MEIC's paper loop for you,
> the loop creates its own database on first run, and the console shows the results. Skip to
> [Paper trading](#paper-trading). The steps below are for working on this package on its own.

**Prerequisites:** Python 3.11+, a tastytrade account, and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's CLI coding assistant, which runs the agent's decision loop and every `/`-command below. Install it with `npm install -g @anthropic-ai/claude-code`, then launch it from the project folder with `claude`.

> Two kinds of commands appear in this guide: plain `python …` commands run in a normal terminal, and `/`-prefixed commands (like `/meic-start`) are Claude Code skills you type at the `claude` prompt. The skills just orchestrate the same underlying `python -m cherrypick.meic.<module>` calls.

**New to this? First, open a terminal and get the code.** You'll need [Git](https://git-scm.com/downloads) installed. Open your terminal:
- **Windows** — the Git installer includes "Git Bash"; use it for every command here.
- **macOS** — open **Terminal** (Applications → Utilities), or install Git via `xcode-select --install`.
- **Linux** — open your terminal; install Git with your package manager (e.g. `sudo apt install git`).

Then download the suite and move into this package's folder:

```bash
# 1. Clone the cherrypick monorepo
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/meic
```

Every command below is run from inside `packages/meic`. On macOS/Linux, if `python`/`pip` aren't found, use `python3`/`pip3` instead.

```bash
# 2. Install dependencies -- packages/core FIRST (it's not on PyPI, so pip can only resolve
# "cherrypick-core" from what's already installed), then this package's own (tastytrade, keyring,
# pytz, flask from pyproject.toml). Developers can run scripts/dev-install.ps1 (or .sh) from the
# repo root instead, which does both steps for every package at once.
pip install -e ../core
pip install -e .
pip install pytest pytest-asyncio     # optional — only needed to run the test suite

# 3. Initialize the database
python -m cherrypick.meic.db init_db

# 4. Launch Claude Code, then run the guided credential + config setup:
claude
```
```
/setup                                # inside Claude Code — stores and checks credentials
```

`/setup` stores and checks credentials; it does not write a config. With no config of its own, MEIC runs from the shipped `config.example.json` (read-only). To change settings, copy it to `~/.cherrypick/config/meic.json` and edit that copy. Credentials can also be stored by hand with `python -m cherrypick.meic.tt secrets_set`, run in your own terminal.

> **Running on a headless Linux server** (no desktop)? There's no OS keyring there, so credential storage needs an encrypted-file or cloud-secret-manager backend — see [Headless / server credentials](docs/setup.md#headless--server-credentials-linux-without-a-desktop) in the setup guide.

Then, inside Claude Code, pick a track:

**Paper trading (recommended first)** — no capital, no live orders, runs every enabled arm side by side. In the normal setup there is nothing to start: the orchestrator's supervisor runs the paper loop as its `meic-paper` job, evaluating every configured symbol every 60 seconds during market hours (`modules.meic.paper.tick_interval_seconds`). Without the supervisor — a bare checkout — keep the loop going in a terminal with `python -m cherrypick.meic.paper_loop`, or wire a cron job to `python -m cherrypick.meic.paper_loop --once` every minute. `/paper-start` starts nothing itself: it checks the streamer, the console and that the supervisor's `meic-paper` job is listed.

On a base install the paper loop runs one arm, `control`. Any other arm is this machine's own configuration in `~/.cherrypick/config/meic.risk.json` (see [Risk Profiles](#risk-profiles)). The AI advisor's `advised:<name>` books also need Claude Code and `advisor.enabled` in the suite config; both are off on a base install.

**Live / dry-run trading (experimental, off by default, at your own risk)** — the real agent loop. It stays in dry-run until you set `enable_live_trading: true` yourself:

```
/meic-start
```

Run it from the repo root. If `enable_live_trading` is true, it shows the disclaimer verbatim and stops unless you answer an explicit YES. It then verifies the shared market-data streamer and starts the agent loop. The live path trades one pinned symbol (`live.symbol`) and refuses to place orders until `live.gate0_confirmed` is filled in. Read [DISCLAIMER.md](../../DISCLAIMER.md) first. To watch the session, open the console with `/console` — the supervisor already has it running.

See [docs/setup.md](docs/setup.md) for the full walkthrough and [docs/paper-trading.md](docs/paper-trading.md) for the paper-trading design and graduation criteria.

---

## How this fits the suite

This package is self-contained — everything below works from `packages/meic` on its own. Inside the
cherrypick suite it plays two roles:

- **Live / interactive (this package, standalone).** You drive the agent loop and the `/`-commands
  yourself; start it with `/meic-start` from the repo root, which adds the disclaimer and YES check.
  This is the only MEIC path that can place live orders, and only when you set
  `enable_live_trading: true` — experimental, off by default, at your own risk. The orchestrator never
  touches it.
- **Unattended paper (orchestrator-orchestrated).** The [orchestrator](../orchestrator)'s supervisor runs
  this module's `cherrypick.meic.paper_loop --once` as its `meic-paper` job (a short-lived process every
  60 seconds) for hands-off paper collection, and reads the resulting `paper_trades.db` (in the shared
  data home) for cross-module reporting. The OS scheduler holds one entry for the whole suite, not one
  per module. The orchestrator drives this module **by subprocess only** —
  it never edits this code or config, never places or cancels an order, and never flips
  `enable_live_trading`. Its one live-config action is onboarding (`cherrypick connect`), which delegates to
  this module's own credential tool.

You can run the paper loop here directly too (in a terminal); letting the orchestrator manage it adds the watchdog, notifications, and the cross-module read
side (`cherrypick report` / the console / `calibrate`). The shared `cherrypick.core` code (calendar, fees) lives in `packages/core`, a sibling
in-repo package — see [Orchestrator & shared core](CLAUDE.md#orchestrator--shared-core) in `CLAUDE.md`
for the exact couplings.

---

## Design highlights

- **Parallel-shadow paper trading** — every trading day, `control` and every active AI-advisor experiment (each its own `advised:<name>` book, any number running at once since 2026-09-17) are evaluated deterministically against the *same* live-quote snapshot per symbol, each as its own virtual account. Advised books need Claude Code and `advisor.enabled`, both off on a base install. No capital, no live orders, apples-to-apples stream comparison. Optional SPX historical-replay mode front-loads samples from past days that actually paid. See [docs/paper-trading.md](docs/paper-trading.md) and [docs/paper-experiments.md](docs/paper-experiments.md).
- **Corrected MEIC exit rules** — iron condors have exactly three exits: a per-side software stop (switched off in the shipped paper `control` arm, which holds to settlement or force-close), a time-based force-close **before the bell for non-cash-settled symbols only** (QQQ/IWM/equities — avoids physical assignment), and **left-to-expire cash settlement for cash-settled symbols** (SPX/XSP). There is **no profit-target exit** — that was removed as it isn't part of MEIC. Event days (FOMC, triple-witching, quarterly) still force-close everything as risk overrides.
- **One read surface, both books** — the console tags every row with the mode it came from, so paper and live can never be confused for one another.
- **Realistic fee modeling** — the paper engine charges tastytrade's exact broad-based-index-options fee schedule per leg (commission, clearing, ORF, per-symbol exchange fee, TAF on sells), so simulated P&L reflects real cost drag.
- **Unattended, self-healing loop** — the suite's supervisor fires a short-lived paper-loop process every 60 seconds during market hours (the OS scheduler holds one entry for the whole suite, not one per module): headless, time-gated, and persistent across sessions. At the 16:00 settlement pass it rolls the session into `daily_summary`, which the suite review reads; this module's own EOD reports were retired on 2026-08-13.
- **Automated end-of-day reporting** — the suite review (`packages/review`) covers this module alongside the other modules, split by arm, on two daily passes. `/eod-report` still produces the agent-synthesized LIVE write-up. Bounded log rotation keeps every log file from growing without limit.

---

## Features

- **Multi-symbol, one shared risk budget** — trades multiple underlyings (e.g. SPX + QQQ + IWM) concurrently in a single loop pass. Live shares one account-wide buying-power/position-count budget across symbols; paper keeps one portfolio per arm and symbol. The shipped config trades SPX alone. Correlation risk is only partly guarded: a suite test refuses two vehicles on the same index (e.g. SPX and XSP), but broader correlation (SPY vs QQQ) is only reported, so avoid stacking such symbols deliberately.
- **No hardcoded contract logic** — all contract-specific parameters (instrument type, dollar multiplier, leg symbols) are read directly from the live strategy scan, so adding a new symbol needs no code changes, only a config entry.
- **Settlement-aware exits** — cash-settled index options are left to expire and settled in cash; physically-settled symbols are force-closed before the bell to avoid assignment, with a missed close on a non-cash symbol escalated as an assignment-risk failure rather than routine cleanup.
- **Live DXLink streaming** — the suite's shared streamer (`packages/streamer`) keeps a persistent WebSocket connection maintaining a rolling near-the-money option window per symbol (quotes, greeks, open interest, trade volume), so entry decisions and GEX calculations run off sub-second cached data instead of cold REST calls.
- **Per-symbol GEX (Gamma Exposure) engine** — computes net GEX, gamma flip, call wall, and put wall live from real open interest and greeks, both from open-interest positioning and from actual traded volume.
- **Adaptive per-side stop management** — call and put spreads managed independently; a stopped side doesn't force-close the untouched side.
- **Opening Range Breakout (ORB) sub-strategy** — a directional debit-spread complement to the core IC strategy, capturing the 9:30–9:35 ET range and trading breakouts. ORB keeps its own profit target and stop, distinct from the iron-condor exit rules.
- **Fee-aware credit floors** — rejects entries where estimated fees would eat most/all of the collected premium, using each symbol's own historical fee data once enough trades exist.
- **Full audit trail** — every loop iteration, entry, rejection reason, and stop adjustment is logged with reasoning, and the suite review builds a deterministic end-of-day fact set from the ledgers.

## Simplified entry gate logic

These are the base configuration's gates, which is what the live path trades. The shipped paper arm,
`control`, deliberately switches the study gates off (no IV-rank floor, OTM floor, VIX/ATR/GEX pauses
or late-entry bias; a 09:45–15:30 window; no per-side stop) so each gate's effect can be read from its
rows afterwards. In the base configuration, all of the following must pass — any one failure blocks
the trade:

1. **Time window** — no entries before 10:00 ET or after 14:30 ET. At end of day, non-cash-settled positions are force-closed before the bell; cash-settled positions are left to expire and settle in cash. Event days force-close everything.
2. **IV rank floor** — skip if IV rank is too low (insufficient premium to justify gamma risk).
3. **Late-entry bias** — on borderline-IV days, wait until noon rather than accept thin morning credit for the same directional exposure.
4. **Strike selection** — target a VIX-banded short-strike delta, then apply hard floors on top: minimum distance (%) from spot for both the short call and short put, and a ceiling on the actual call delta regardless of what the delta-target scan picked.
5. **Credit floors** — two independent checks: credit as a % of spread width, and a fee-adjusted floor (the credit must clear estimated fees by a real, width-aware margin). Both are width- and IV-aware, so narrow low-credit setups where fees would consume the premium are rejected.
6. **GEX regime gate** — no new iron condors when the symbol is in a *negative* gamma regime (dealers short gamma = trending/volatile conditions where mean-reversion strategies like MEIC underperform); ORB entries are exempt since they want that regime.
7. **Account-wide caps** — max concurrent condors and max daily entries are shared across every traded symbol, not per-symbol.
8. **Event calendars** — hard blackouts/tighter rules around FOMC announcements, quarterly expiry, and triple witching.

## Risk Profiles

> ⚠️ **This four-tier ladder is history, and no longer ships.** It was disabled for paper on
> 2026-08-07, and since 2026-10-01 the package carries only `config.risk.example.json`, an arm
> registry holding `control` alone. Every other arm, the ladder tiers included, is a machine's own
> configuration in `~/.cherrypick/config/meic.risk.json` (or the file `$MEIC_RISK_CONFIG` names);
> MEIC reads that file when it exists and the shipped example otherwise. `/set-risk-profile` reads
> that same registry and refuses to run when only the shipped example exists. The tiers' values and rationale stay in [docs/risk-profiles.md](docs/risk-profiles.md);
> the arm design is in [docs/paper-experiments.md](docs/paper-experiments.md).

On a machine whose own registry holds them, the tiers switch MEIC's live entry-gate thresholds with a single command instead of hand-editing `~/.cherrypick/config/meic.json`. A **risk profile** bundles IV-rank floors, credit minimums, delta limits, and stop triggers — each preset offsets its gate relaxations with a tighter stop so you're reallocating risk, not just adding it. (Concurrency caps used to be part of that offset too; since 2026-08-01 every profile runs uncapped, so the stop is the ladder's only remaining offset — see `docs/risk-profiles.md`.)

| Profile | What it does | Trade-off |
|---|---|---|
| **conservative** | Strict IV-rank (≥30%) and credit floors, wide OTM buffers, latest entry time (12:00 PM) | Fewest trades (~1–2/day), highest per-trade safety margin |
| **moderate** | Slightly relax IV-rank (≥22%) and credit floors, enter earlier (11:00 AM) | ~1 more trade/day, thinner credit cushion but offset by tighter 93% stop |
| **aggressive** | Tier 1 + accept closer-to-money strikes (delta 0.22, OTM tighter) | ~2–3 more trades/week, each one riskier but the 90% stop limits per-trade exposure |
| **very-aggressive** | Tier 2 + trade through higher-VIX (≤30) and trending (ATR ≤2.0% of price) conditions; stop at 85% | Most trades (~3–5 more/week on active weeks), each with high gamma/pin risk; only for deliberate short experiments |

Use `/set-risk-profile <name>` to switch (the config is backed up to `meic.json.bak` first, and the change takes effect on the next loop). It only applies a name your registry holds, and it changes what the **live** path would trade — experimental, off by default, at your own risk. Never apply a paper sampling arm (`control`, `bp-*`, …) to live config (see the warning in `.claude/commands/set-risk-profile.md`). See [docs/risk-profiles.md](docs/risk-profiles.md) for the full rationale, decision tree, and when to escalate.

## Paper trading

Before risking capital, run the parallel-shadow paper engine to build a performance record:

```
/paper-report                             # weekly (or custom-range) arm comparison
python -m cherrypick.review build --session <date>    # the suite review for one session, all modules
python -m cherrypick.meic.paper_loop --status         # loop status + open-position count
python -m cherrypick.meic.paper_loop --once           # one manual iteration
python run.py headline                                # per-arm results + what is still open (read-only)
```

Under the supervisor, the paper loop stops when the module is switched off (`modules.meic.enabled`
in `~/.cherrypick/config.json`). The standalone helpers `--install-task` / `--uninstall-task`
(Windows only) register and remove the loop's own scheduled task, for a machine running MEIC without
the supervisor.

Every 60 seconds during market hours, the engine takes one live-quote snapshot per symbol and runs every enabled arm against it deterministically — synthetic fills at mid less a modelled slippage haircut (`slippage_frac_of_spread`, 0.125 of the bid-ask by default), each arm its own virtual account, tastytrade's exact fee schedule applied per leg. Writes go only to `paper_trades.db` in the data home; the live account and `meic_trades.db` are never touched, and no live order is ever submitted (paper mode is not gated by `enable_live_trading`).

A pre-registered **graduation gate** (≥30 filled ICs, positive expectancy, ≥65% win rate, profit factor 1.3–4.0, bounded drawdown and worst day) is the written bar for judging whether an arm's record could justify live capital. Nothing applies it automatically: `cherrypick calibrate` gives an advisory reading, and going live stays your decision. See [docs/paper-trading.md](docs/paper-trading.md) for the full design, the SPX historical-replay accelerator, and the known limitations of a frictionless paper model.

## The read surface

The console — `http://127.0.0.1:5070/meic`, opened with `/console`. It reads **both** ledgers and
tags every row with the mode it came from, so paper and live are separated by the data rather than by
which port you opened (this module's own two-port dashboard, 5050 live / 5051 paper, was retired on
2026-08-12). The MEIC page has a left rail of tabs:

- **session** — one session's net, positions, costs, net by arm and why entries were refused
- **forest**, **attempts**, **exits**, **regime cuts** — each arm's expiry payoff, every evaluated
  entry, how positions ended, and outcomes by regime
- **calibration**, **performance** — arm comparison, cumulative net and drawdown, per-period
  statistics and risk-adjusted metrics (scaled to a notional $100k bankroll; filterable by symbol and
  arm)
- **advisor** — the AI advisor's experiments on this module (only when the advisor is switched on)
- **positions**, **history**, **sessions** — open trades, closed trades in the suite's standard money
  layout, and the per-session roll-up
- **help** — what each view means

GEX, IV skew and volume are on the console's own **GEX** page, off the same live stream cache; MEIC's
trading loop still uses the shared GEX engine (`cherrypick.core.gex`, via `tt.py get_gex`) for its
regime gate and stop tightening. Logs are on the **System** page.

Everything runs locally against your own tastytrade account — no cloud dependency for trade execution.

---

## Documentation

- [Setup](docs/setup.md) — installation, configuration, database init, going live
- [Operating](docs/operating.md) — starting the loop, status, the console, EOD report, logs
- [Strategy](docs/strategy.md) — MEIC structure, wing width selection, stops, exit rules, EOD settlement handling
- [Entry gates](GATES.md) — the full entry-gate stack in evaluation order
- [Paper trading](docs/paper-trading.md) — the parallel-shadow engine, fee model, historical replay, graduation gate, known limitations
- [Paper experiments](docs/paper-experiments.md) — how arms are designed and read, and the record of every closed or retired study
- [Risk Profiles](docs/risk-profiles.md) — the retired four-tier ladder (no longer shipped): its trade-offs and full rationale
- [History](docs/history.md) — dated incidents, audits and arm changes behind the rules in `CLAUDE.md`
- [`CLAUDE.md`](CLAUDE.md) — the agent's operating instructions (loop steps, config reference, guardrails)

**Suite-level:** [cherrypick README](../../README.md) · [suite user guide](../../docs/PROJECT.md) · [orchestrator](../orchestrator)

---

## Project structure

This package lives at `packages/meic/` inside the [cherrypick](../../README.md) monorepo:

```
cherrypick/
├── packages/core/                   # Shared cherrypick.core library (in-repo package: calendar, fees)
└── packages/meic/                   # ← this package (cherrypick-meic)
    ├── CLAUDE.md                    # Agent operational brain (loaded every loop iteration)
    ├── GATES.md                     # Reference: the full entry-gate stack in evaluation order
    ├── run.py                       # Read-side CLI launcher (python run.py headline, arms, exits, …)
    ├── config.example.json          # Config template; what MEIC runs from until it has a config of its own
    ├── config.risk.example.json     # Arm registry template, control only; the machine's own registry
    │                                 # is ~/.cherrypick/config/meic.risk.json (or $MEIC_RISK_CONFIG)
    ├── src/cherrypick/meic/         # the cherrypick.meic namespace package (run as -m cherrypick.meic.<mod>)
    │   ├── tt.py                    # tastytrade CLI — get_quote, get_strategies, execute_trade, etc.
    │   ├── streamer.py              # Persistent DXLink streaming daemon (rollback-only since the
    │   │                             # 2026-07-21 producer cutover — see packages/streamer)
    │   ├── stream_request.py        # Declares symbols + open legs to the shared streamer's cache
    │   ├── session.py               # OAuth2 session management
    │   ├── credentials.py           # OS-keyring credential storage
    │   ├── paths.py                 # Resolves the shared data/logs/config home (~/.cherrypick/...)
    │   ├── db.py                    # SQLite CLI + in-process db.call() dispatcher (live + paper DBs)
    │   ├── notify.py                # Structured log CLI helper
    │   ├── gex_math.py              # Gamma-exposure (GEX) computation helpers
    │   ├── regime.py                # Entry-time regime tagging (vol/GEX/skew/trend, 8 dimensions)
    │   ├── stop_policies.py         # Derived stop policies, computed read-side from open's paths
    │   ├── analytics.py             # Read-only query layer: by_arm, breakeven_scorecard, regime cuts
    │   ├── paper.py                 # Deterministic parallel-shadow paper engine (every enabled arm)
    │   ├── paper_loop.py            # Unattended paper loop (one --once tick per spawn) + daily roll-up
    │   ├── paper_practice.py        # 0DTESPX-backed practice-mode backtester (see paper-practice-plan.md)
    │   ├── paper_replay.py          # SPX historical-replay mode (0DTESPX data; bulk mode disabled)
    │   ├── cli.py                   # Read-only CLI behind run.py (headline, arms, regime, exits, stops, …)
    │   ├── regime_cuts.py           # Nightly regime-cuts artifact (the supervisor's meic-regime-cuts job)
    │   ├── live_loop.py             # Live agent-loop entry/exit mechanics (isolated from paper's arms)
    │   ├── live_orders.py           # Live order placement/adjustment helpers
    │   ├── live_smoke.py            # Supervised dry-run smoke test of the live broker write path
    │   ├── experiment.py            # Session-bootstrap comparison CLI for study arms
    │   └── gate_health.py           # Reports which regime gates are armed/stood-down right now
    ├── docs/
    │   ├── README.md                # Doc index
    │   ├── setup.md                 # Installation and configuration
    │   ├── operating.md             # Running and monitoring the agent
    │   ├── strategy.md              # MEIC strategy details and exit rules
    │   ├── paper-trading.md         # Paper-trading engine, fee model, graduation gate
    │   ├── paper-experiments.md     # The current forward test + retired-study design record
    │   ├── paper-practice-plan.md   # Structured plan for building paper-workflow confidence
    │   ├── risk-profiles.md         # The retired risk ladder and its rationale
    │   ├── history.md               # Dated incidents, audits and arm changes
    │   └── 0dtespx-api.md           # 0DTESPX API/ToS notes (historical-replay data source)
    ├── .claude/
    │   ├── settings.json            # Permissions and MCP environment overrides
    │   └── commands/
    │       ├── meic-start.md        # /meic-start — launch full live session
    │       ├── paper-start.md       # /paper-start — check the streamer, console and supervisor's paper job
    │       ├── setup.md             # /setup — store and check credentials
    │       ├── set-risk-profile.md  # /set-risk-profile — apply a registry preset to the live config
    │       ├── daily-check.md       # Daily broker-connection check (Step 3 of the loop)
    │       ├── execute-entry.md     # Entry execution (Step 7 of the loop)
    │       ├── stop-management.md   # Per-side stop management (Step 5 of the loop)
    │       ├── paper-loop.md        # /paper-loop — one paper iteration
    │       ├── eod-report.md        # /eod-report — the live EOD report
    │       ├── paper-report.md      # /paper-report — multi-day profile comparison
    │       ├── meic-status.md       # /meic-status — quick session status
    │       └── check-chain.md       # /check-chain — verify chain and strike selection
    └── tests/
```

Runtime **data** does not live in the package — it's kept in the shared cherrypick data home so the
orchestrator and this module read the same files. Resolved by [`cherrypick/meic/paths.py`](src/cherrypick/meic/paths.py):

```
~/.cherrypick/data/meic/             # default; override with the MEIC_DATA_DIR env var
├── meic_trades.db                   # Live trade history, loop log, daily summaries
├── paper_trades.db                  # Paper trade history (every enabled arm)
├── replay_cache/                    # Cached SPX historical-replay snapshots
├── streamer.pid                     # Rollback-producer PID file (MEIC's own streamer, normally off)
└── paper_loop.pid / paper_loop.once.lock   # --start daemon PID and the --once overlap lock

~/.cherrypick/logs/meic/             # default; override with the MEIC_LOGS_DIR env var (all rotated)
├── paper_loop.log                   # Paper loop log
├── tt.log                           # Broker CLI log
└── eod-<date>.md                    # Live end-of-day write-up from /eod-report (agent-synthesized)

~/.cherrypick/config/
├── meic.json                        # This machine's base config (else the package's config.json,
│                                     # else the shipped config.example.json)
└── meic.risk.json                   # This machine's arm registry (else config.risk.example.json)

~/.cherrypick/data/marketdata/       # shared across every module, not MEIC-owned
└── stream_cache.db                  # Live streamer cache (quotes/greeks/OI/volume/GEX history) —
                                      # written by the standalone streamer (packages/streamer), the
                                      # producer since the 2026-07-21 cutover
```

---

## License

MIT — see [LICENSE](LICENSE) for full terms.

---

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not financial advice, investment advice, trading advice, or any other type of advice.

- The authors and contributors are not registered investment advisors, broker-dealers, or financial planners.
- Nothing in this repository constitutes a recommendation to buy, sell, or hold any security or financial instrument.
- Options trading involves substantial risk of loss and is not appropriate for all investors. 0DTE options carry extreme risk due to rapid time decay and gamma exposure.
- Past performance of any strategy — simulated or live — does not guarantee future results.
- You are solely responsible for all trading decisions and any resulting gains or losses.
- Always consult a qualified financial professional before trading with real capital.

**Use this software at your own risk. The authors accept no liability for any financial losses incurred through its use.**
