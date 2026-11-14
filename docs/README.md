# cherrypick — Documentation

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. **Read [DISCLAIMER.md](../DISCLAIMER.md) before use.**

Reference documentation for the cherrypick suite, organised by functional area. New here? Start with
[QUICKSTART.md](../QUICKSTART.md) (install and first run in plain language) or [INSTALL.md](../INSTALL.md)
(the install reference), then the [User Guide](PROJECT.md) for daily use. Use the files below when you need
to understand *how* a part of the suite works or *why* it's built the way it is.

## Root reference docs (suite-wide)

| Doc | Covers |
|---|---|
| [QUICKSTART.md](../QUICKSTART.md) | **Start here if you are not a programmer** — install and first run in plain language, including where to get your tastytrade keys. |
| [INSTALL.md](../INSTALL.md) | The install reference: prerequisites, what the one-command installers do and their options, uninstalling, and the developer path. |
| [DISCLAIMER.md](../DISCLAIMER.md) | The canonical disclaimer: experimental, educational, not advice; live paths place real orders at your own risk. |
| [PROJECT.md](PROJECT.md) | **User Guide** — plain-language install, setup, daily use, troubleshooting. |
| [operations.md](operations.md) | **Runbook** — the full task/daemon/port inventory, dependency order and time constants, the 09:00 ET checklist, normal-vs-real warnings and the known gaps. |
| [architecture.md](architecture.md) | How the suite fits together: the orchestrator + strategy modules, the shared `cherrypick.core` package, the read/write halves, per-schema dispatch, and the managed `~/.cherrypick` home. |
| [guide/README.md](guide/README.md) | **The user guide**, which the console's **Help** page shows: one page per topic, in the order this index gives, plus the [FAQ](guide/faq.md). `tools/check_guide.py` keeps the index, links, images and quoted commands true in CI. |
| [guide/external-reports.md](guide/external-reports.md) | **Your own reports**: write a script, schedule it from config.json, see it under Reports > external and optionally post a text card to Discord. The file contract, an example, the rules and troubleshooting. ([guide/discord.md](guide/discord.md) is a stub for the webhook setup.) |
| [guide/upgrading.md](guide/upgrading.md) · [.claude/commands/cherrypick-upgrade.md](../.claude/commands/cherrypick-upgrade.md) | **Upgrading**: `upgrade` moves an install to a newer release (notes, backup, install, migrations, verify) and `--rollback` undoes it; the slash command `/cherrypick-upgrade` explains the target's notes and runs it. The manifest side is in [releasing.md](releasing.md). |
| [.claude/skills/external-report/SKILL.md](../.claude/skills/external-report/SKILL.md) | The repository's **external-report skill** for Claude Code: it loads on its own when someone asks for a report of their own and works from the guide and the schema (workflow, styling, rules, done checks). Optional; the guide covers the same by hand. |
| [orchestrator-cli.md](orchestrator-cli.md) | Every `cherrypick` / `run.py` command, what it does, and its flags — the operational surface. |
| [reporting-and-dashboard.md](reporting-and-dashboard.md) | The read side: unified P&L report, calibration, the cross-module suite review (one fact set per session + its renders), the console, and end-of-month log/report archiving. |
| [strategy-engines.md](strategy-engines.md) | Every strategy module at a suite level — MEIC, Earnings (paper-only), the experimental Calendars, Contango and Weeklyput, and the retired Flies, BWB, PMCC and Curve — plus GEX and the arm **variance testing** method, with pointers into each module's own docs. |
| [demo-home.md](demo-home.md) | A fake suite home and how to serve the console on it, for documentation screenshots only. |
| [configuration-and-storage.md](configuration-and-storage.md) | The config model (orchestrator + per-module), the managed-home layout, environment overrides, and the databases / logs / report files each module reads and writes. |
| [guardrails-and-modes.md](guardrails-and-modes.md) | Paper vs. live isolation, the load-bearing invariants (no AI/network on the reliability path, masked accounts, defined-risk, correlation), credentials, and the one narrow live-config boundary. |
| [releasing.md](releasing.md) | How a release is cut: `main` is development, a tag on `main` plus its GitHub Release is what installs get, versions, the changelog's `[Unreleased]` section, and the release workflow. |
| [glossary.md](glossary.md) | Suite-wide terms (0DTE, IC, GEX, IV rank, MEIC, arm, research item, defined-risk, 1256, …). |
| [conventions/packages.md](conventions/packages.md) · [conventions/working-rules.md](conventions/working-rules.md) · [conventions/money-layout.md](conventions/money-layout.md) | The suite's working conventions: what each package may do (live or paper, credentials, what it writes), the vocabulary, spelling and review rules, and how a trade's money is laid out on every surface. |
| [strategy-sources.md](strategy-sources.md) | Where each strategy engine's logic is documented, the origin commits, every external source the repo cites (the published MEIC convention, a bot platform's stop backtests, du Toit & Peskir's optimal-stopping paper, EarningsEdgeDetection), and the verified external lineage — the MEIC originator's videos, tastylive's broken-wing-butterfly genre, the PEAD drift papers, and the "profit forest" genre. |

## Research records

The research register and the studies behind it are in [`research/`](../research/README.md), not here.
They are records: each one states a test as it was declared before any data was read, along with its result.
`cherrypick hypotheses` reads the register, [`research/hypotheses.json`](../research/hypotheses.json).

These docs describe only the current version. Plans that have landed, superseded designs and dated
history were removed in November 2026; the tag `archive/docs-2026-11` keeps them as they stood.

## Module docs (kept inside each module — the source of truth for that engine)

Suite-wide docs point into these rather than duplicating them; a module's own docs live next to its code
so they can't drift from it.

- **Orchestrator** — [`packages/orchestrator/README.md`](../packages/orchestrator/README.md) (start here) and [`packages/orchestrator/CLAUDE.md`](../packages/orchestrator/CLAUDE.md) (build/architecture/invariants); shipped history is the git log.
- **MEIC** — [`packages/meic/CLAUDE.md`](../packages/meic/CLAUDE.md), [agent-loop](../packages/meic/docs/agent-loop.md) (the Loop Steps and `tt` table), [reference](../packages/meic/docs/reference.md) (CLI, arms, config), [`GATES.md`](../packages/meic/GATES.md) (entry-gate catalog), and [`packages/meic/docs/`](../packages/meic/docs/): [strategy](../packages/meic/docs/strategy.md), [risk-profiles](../packages/meic/docs/risk-profiles.md), [paper-experiments](../packages/meic/docs/paper-experiments.md), [paper-trading](../packages/meic/docs/paper-trading.md), [operating](../packages/meic/docs/operating.md), [setup](../packages/meic/docs/setup.md).
- **Earnings** (paper-only since v0.12.0) — [`packages/earnings/CLAUDE.md`](../packages/earnings/CLAUDE.md) and [`packages/earnings/docs/`](../packages/earnings/docs/): [strategies](../packages/earnings/docs/05-strategies.md), [screening-criteria](../packages/earnings/docs/screening-criteria.md), [configuration](../packages/earnings/docs/03-configuration.md), [entry-conditions](../packages/earnings/docs/04-entry-conditions.md), [exits](../packages/earnings/docs/10-exits.md), [strategy-testing-plan](../packages/earnings/docs/strategy-testing-plan.md), [glossary](../packages/earnings/docs/14-glossary.md).
- **GEX** — [`packages/gex/CLAUDE.md`](../packages/gex/CLAUDE.md), [`packages/gex/README.md`](../packages/gex/README.md).
- **Flies** (retired 2026-10-19, off by default) — [`packages/flies/README.md`](../packages/flies/README.md) (start here), [`packages/flies/CLAUDE.md`](../packages/flies/CLAUDE.md) (retirement status, strategy, honesty rules, guardrails), [`packages/flies/docs/reference.md`](../packages/flies/docs/reference.md) (layout, read side, the arms, completion timing, fill realism), [`packages/flies/docs/live.md`](../packages/flies/docs/live.md) (the gated live path), and [`packages/flies/docs/faq.md`](../packages/flies/docs/faq.md) (recurring questions: what the strategy is, why SPX/XSP and not SPY or /ES-/MES futures options).
- **Calendars** (⚠️ EXPERIMENTAL, off by default) — [`packages/calendars/README.md`](../packages/calendars/README.md) (start here: the trade and the experiment) and [`packages/calendars/CLAUDE.md`](../packages/calendars/CLAUDE.md) (the books, the read-side exit-policy derivation, the honesty rules). Every config key is documented inline in its `config.example.json`.
- **PMCC** (retired 2026-11-08, off by default) — [`packages/pmcc/README.md`](../packages/pmcc/README.md) (start here: the trade, its symbols and the shield arms) and [`packages/pmcc/CLAUDE.md`](../packages/pmcc/CLAUDE.md) (the experiment design, the honesty rules — early assignment is measured, never modelled — and the deep-window data story). Every config key is documented inline in its `config.example.json`.
- **Curve** (retired 2026-11-01, off by default) — [`packages/curve/README.md`](../packages/curve/README.md) (start here: the roll-yield trade and the four books) and [`packages/curve/CLAUDE.md`](../packages/curve/CLAUDE.md) (the regime read, the honesty rules — assignment and reverse splits are measured, never modelled). Every config key is documented inline in its `config.example.json`.
- **Contango** (⚠️ EXPERIMENTAL, off by default) — [`packages/contango/README.md`](../packages/contango/README.md) and [`packages/contango/CLAUDE.md`](../packages/contango/CLAUDE.md) (the SVXY/T-bill switch, its replay against buy-and-hold, and the honesty rules). Every config key is documented inline in its `config.example.json`.
- **Weeklyput** (⚠️ EXPERIMENTAL, off by default) — [`packages/weeklyput/README.md`](../packages/weeklyput/README.md) and [`packages/weeklyput/CLAUDE.md`](../packages/weeklyput/CLAUDE.md) (item 57's put in paper: the calendar, what paper adds to the rule, the settlement print, the stream needs). Every config key is documented inline in its `config.example.json`.
- **Ensemble** (⚠️ EXPERIMENTAL, off by default, no loop yet) — [`packages/ensemble/README.md`](../packages/ensemble/README.md) and [`packages/ensemble/CLAUDE.md`](../packages/ensemble/CLAUDE.md) (the arm schema, the gate registry, the strike rules, the fills and what a decision is made from).
- **BWB** (retired 2026-11-08, off by default) — [`packages/bwb/README.md`](../packages/bwb/README.md) (start here: the ladder and the four add-on books) and [`packages/bwb/CLAUDE.md`](../packages/bwb/CLAUDE.md) (the experiment design, the trigger-tick substrate, the opt-in call-wall book, and since 2026-09-18 the narrow per-day-armed live path). Every config key is documented inline in its `config.example.json`.
- **Overview** — [`packages/overview/README.md`](../packages/overview/README.md) and [`packages/overview/CLAUDE.md`](../packages/overview/CLAUDE.md) (the fact pack, the five gates, and why missing data can never produce RED). The morning narrative is written outside the package by `scripts/morning_narrative.py`.
- **Backtest** — [`packages/backtest/README.md`](../packages/backtest/README.md) (start here: the API key, the config, ending a subscription) and [`packages/backtest/CLAUDE.md`](../packages/backtest/CLAUDE.md) (the licence rules — vendor data and results never enter the repo — the fence, and what is built).
- **Fundamentals** — [`packages/fundamentals/README.md`](../packages/fundamentals/README.md) and [`packages/fundamentals/CLAUDE.md`](../packages/fundamentals/CLAUDE.md) (the SEC, Dolt and Damodaran inputs, the point-in-time rule, the fair-value file).
- **Technicals** — [`packages/technicals/README.md`](../packages/technicals/README.md) and [`packages/technicals/CLAUDE.md`](../packages/technicals/CLAUDE.md) (the end-of-day store, the adjustment and how it was checked against the vendor, and why Dolt is read in date windows), [`docs/reference.md`](../packages/technicals/docs/reference.md) (each engine's fitted rule, the outputs, the commands).
- **Streamer** — [`packages/streamer/README.md`](../packages/streamer/README.md) (start here: what it produces, the subscription registry, and why it is supervised harder than anything else) and [`packages/streamer/CLAUDE.md`](../packages/streamer/CLAUDE.md).
- **Console** — [`packages/console/README.md`](../packages/console/README.md), [`packages/console/CLAUDE.md`](../packages/console/CLAUDE.md), and [`packages/console/docs/`](../packages/console/docs/): [not-covered](../packages/console/docs/not-covered.md) (what the console deliberately leaves out), [verify-notes](../packages/console/docs/verify-notes.md). The suite's one TypeScript package; it has no architecture doc yet — a known gap, deliberately deferred while the package is still moving fast.
- **Review** — [`packages/review/README.md`](../packages/review/README.md) (start here: the commands and where the fact sets are written) and [`packages/review/CLAUDE.md`](../packages/review/CLAUDE.md). The cross-module end-of-day review: one versioned fact set per session over every engine, plus its renders — the suite's one EOD answer since 2026-08-13. Read-only over every other package; the narrative is written outside it by a scheduled agent, so a failed note can never damage the record.
- **Manage** — [`packages/manage/README.md`](../packages/manage/README.md) (start here: turning it on, the default ruleset and the evidence grade behind each rule) and [`packages/manage/CLAUDE.md`](../packages/manage/CLAUDE.md). The daily position-management evaluation of the real account at 10:00 ET, read on Live → positions and Live → portfolio. Advice only: no order path, no credentials; the orchestrator takes the read-only broker snapshot it judges.
- **Advisor** — [`packages/advisor/README.md`](../packages/advisor/README.md) (start here: what it can and cannot do) and [`packages/advisor/CLAUDE.md`](../packages/advisor/CLAUDE.md) (the operating contract — the AI fence, why paper arms are the dry run, and why an experiment outlives its advice artifact). The deterministic half of the AI advisor; the model itself is invoked by `scripts/advisor_checkpoint.py`, outside every package.
- **Core** — [`packages/core/README.md`](../packages/core/README.md) and [`packages/core/CLAUDE.md`](../packages/core/CLAUDE.md) (the shared library every package imports; the module map is in [docs/module-map.md](../packages/core/docs/module-map.md)).

## Conventions

- Everything the suite writes at runtime lives under **`~/.cherrypick`** (relocatable with
  `$CHERRYPICK_HOME`), never in a source checkout.
- Commands are shown as `python run.py <cmd>` from `packages/orchestrator`; a pip install also exposes
  them as `cherrypick <cmd>`.
- **Paper by default.** Nothing places a live order until you open a module's live gate yourself — see
  [guardrails-and-modes.md](guardrails-and-modes.md).
- **Commands** run under the interpreter that ran `install` — the root installer's `.venv`.
