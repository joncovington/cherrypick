# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own
CLAUDE.md, which holds the detail; the entries here keep each package's posture. The fuller
descriptions and the rationale behind the conventions below, as they stood before this file was
consolidated, are in [docs/history/root-claude-md-2026-09-29.md](docs/history/root-claude-md-2026-09-29.md).

- **packages/orchestrator** — watchdog, OS scheduler, notifications, and the read side (report /
  dashboard / reconcile / calibrate). Drives modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — the live GEX engine and spot-trail recorder; it computes and records, the console
  renders.
- **packages/streamer** — the **single** producer of the shared stream cache every module reads.
  Modules declare symbols via `state/stream_requests/`; nothing else may write that cache.
- **packages/flies** — 0DTE net-credit butterflies. Paper by default, with a narrow, per-day-armed live
  pilot (one arm, one symbol, a worst-case buying-power cap). Floors are measured after fees, and a
  book-level floor always carries the price band over which it holds.
- **packages/calendars** — weekly SPY double calendars; **paper-only, credential-free**, a pure
  stream-cache consumer (chains via the streamer's `expirations` field). An exit-parameter experiment:
  `control` closes at Friday's bell, `path` holds to expiry recording a mark path, and a read-side
  replay (`exit_policies.py`) is validated to the cent against the real books every run. Holiday weeks
  are distinct structures, never pooled; both European cash and American physical settlement are
  modelled, and a symbol declared as neither is refused at entry; ex-dividend weeks are skipped. No
  live path.
- **packages/pmcc** — PMCC deep-ITM calls with a short call against them, on XSP, QQQ, GLD, IWM, SLV,
  SMH and the stocks AMZN, TSLA, AMD, NVDA and PLTR (one a market day, 2026-10-12 to 10-19);
  **paper-only, credential-free**, the calendars posture (chains via `expirations`/`window_hints`,
  year-long dates from the streamer's listing). The base trade since 2026-10-06 is `shield`/
  `shield_hold` (a ~1-year long held while a weekly short rolls against it, closed-only readers seeing
  them late); `control` (a 21-DTE long re-bought each cycle) and its advised twins are retired and run
  off, and pmcc's advice is off. A short spanning an earnings announcement is refused, as one spanning
  an ex-date is. SLV is the intended first live symbol. **Early assignment
  is measured, never modelled**: ex-dividend spans refused, near-zero-extrinsic marks flagged
  assignment-exposed, so the paper result is an explicit upper bound. No live path yet.
- **packages/curve** — VXX call-credit spreads gated by a daily VIX/VIX3M regime read; **paper-only,
  credential-free**. Books `control`, `noflip` (byte-identical to control until a flip fires),
  `hook` and (from 2026-10-06) `near`, a 0.40-delta short, differ in entry gate, exit rule or short
  delta; $2-wide from 2026-10-06, its credit floor net of entry costs against max loss. The daily classification is recorded every session,
  traded or not. Early assignment and VXX reverse splits are measured, never modelled (an upper
  bound). `regime-history` is a signal-separation benchmark, never suite P&L. No live path.
- **packages/bwb** — daily-laddered SPX put broken-wing butterflies, ~7 DTE, held to expiry; paper by
  default and credential-free. Four books differ only in whether/when a reversal-triggered put credit
  spread add-on fires; trigger latches persist on the position row. **A narrow live path exists** in
  the flies posture: one arm, armed per day by `/live-bwb-start`, a worst-case margin cap, a
  cost-derived credit floor, and a mark-drawdown breaker that blocks entries only. Every fill is the
  broker's word; settlement lands on an official print or not at all. Paper books are untouched by it.
- **packages/overview** — the pre-open market overview: one deterministic fact pack per session with a
  GREEN/YELLOW/RED phase from five declared gates — **missing data can never produce RED and always
  blocks GREEN**. Credential-free, network-free, read-only. The narrative is written outside the
  package by `scripts/morning_narrative.py`.
- **packages/technicals** — the market report's end-of-day store and technical engines
  (`docs/market-report-plan.md`), from the LOCAL Dolt clones (pulled by `scripts/refresh_dolt_data.py`).
  Credential-free and network-free; adjusted bars are computed on read and match the vendor's to the
  cent; engines are fitted against the vendor and scored out of sample. Level SELECTION is not yet
  reproduced, and the docs say so.
- **packages/console** — the reactive web UI (Node + TypeScript, React SPA on 127.0.0.1:5070), the
  suite's **only** read surface. **Holds no path that touches an order.** Kept alive by the supervisor
  (restarted on death and on a stale `state/console.heartbeat`). Read-only over every other package
  (own store `~/.cherrypick/data/console/`); reads the shared credential, never writes one. The one
  bounded exception is the **Config page**: the live-trading halt toggle and a short allow-list of
  settings, applied through the orchestrator's config editor as a subprocess, so the guarded
  live-trading fields stay unreachable.
- **packages/review** — the cross-module end-of-day review: one versioned fact set per session, read
  via `cherrypick.core.ledgers` (the single home for per-schema net/risk rules), written only to its own
  store. No credentials, no network, no AI — the narrative is written outside by a scheduled agent.
- **packages/advisor** — the deterministic half of the AI advisor: fact packs, validation of replies,
  and the paper A/B experiments. **It contains no AI** (the model is invoked by
  `scripts/advisor_checkpoint.py`). Read-only over every other package; its one output toward a loop is
  a bounded, expiring paper advice artifact (`cherrypick.core.advice`) applied to a synthetic
  `advised:<experiment name>` book. Off by default twice over: the suite must schedule it and each
  module must declare its own `advice` bounds.
- **packages/desk** — ⚠️ **EXPERIMENTAL.** The suite's only *discretionary* live-order path (meic,
  earnings, flies and bwb each have a live loop behind their own `enable_live_trading` gate). A
  foreground, human-initiated CLI, authorized entirely on its own (own config, own PIN, per-order
  ticket, own policy gates). Stores no broker secrets — it borrows a module's keyring session, because
  borrowing credentials is not borrowing permissions. No loop, no schedule, no ledger, never scheduled,
  and **no automated package may import it**.

`cherrypick.core` is **`packages/core`**, an editable dependency of every package. A user installs with
the root `install.cmd`/`install.ps1`/`install.sh` (a `.venv`, every package but desk, the console, the
supervisor); a developer runs `scripts/dev-install.ps1`/`.sh` (editable, with `[dev]` extras), or
`pip install -e packages/core` first by hand.

## Suite-wide guardrails

- Instruction files hold no code.
- Account numbers masked to `****1234`.
- Portable paths only.
- Human-voice docs and commits, no AI attribution.
- Deterministic solutions preferred over AI/agentic ones (below).
- Paper↔live isolation: the orchestrator only drives paper; its one live-config action is
  onboarding/account selection.

A package's CLAUDE.md states only what it adds or tightens beyond this, never restates it. A
developer-initiated documentation review must review and update package documentation too.

**Deterministic solutions are preferred over AI or agentic ones** — a standing preference, not a
prohibition. Where a problem can be solved by a pure function over data you already have, solve it
that way: this suite exists to measure whether strategies make money, and a measurement is only worth
its reproducibility (a deterministic path fails the same way twice and can be re-run over last month's
rows). That is why `engine.py`, `management.py`, `fly.py` are pure functions over a pre-fetched
snapshot. Network/MCP dependencies are non-deterministic inputs too: prefer the local stream cache,
and reach outward only to ACT or to confirm a fill — an external streamer once stalled silently for 34
hours and looked exactly like a quiet market. Where AI earns its place, contain it OUTSIDE the
packages, in `scripts/` (`eod_narrative.py`, `morning_narrative.py`, `advisor_checkpoint.py`), so a
failure costs a narrative and never a report, a ledger or a loop. Leaning away from this is a choice
to write down.

**Measurement-affecting changes are batched to declared boundaries.** A measurement-affecting change
alters what a session's numbers MEAN — tick cadence, entry pacing, gate semantics, a book's net
definition, which arms exist. Each landing restarts the evidence clock, so hold them to a declared
boundary, land them together, and journal the boundary once. A bug fix correcting a number the module
was recording WRONG is not in this category and lands immediately. Pure code changes (refactors, read
surfaces, dedup) aren't either.

## The suite's vocabulary

**An `arm` is one configured variant, run as its own portfolio** — control and treatment, in the
clinical sense. Modules shipped four names for it (`arm` in flies; `book` in bwb/pmcc/curve/calendars;
`risk_profile` in meic; `profile` in earnings), and `core/ledgers.py` (`profile`) and the console's
attempts reader (`armColumn`) disagree on the canonical one. **The word is now `arm`, everywhere a new
name is chosen.**

**Three words are taken, and must never be used for an arm:**

| Word | What it already means | Where |
|---|---|---|
| `book` | flies' per-session P&L roll-up **of** an arm — a time slice, not a variant | `fly_books`, `book_id` |
| `book` | the paper-vs-live ledger designation | review fact sets, `modules.<m>.book = "paper"` |
| `profile` | a config preset registry | MEIC's arm registry (`meic.risk.json`), `core.profiles.load_profiles` |
| `profile` | the gamma-by-strike curve | `GexProfileChart`, `useGexProfile` |
| `strategy` | earnings' **structure type** (iron_fly vs double_calendar) | earnings is the only two-axis module: profile × strategy |

**Existing spellings are never withdrawn.** A module config is a file a person keeps across upgrades
with no migration, so `books`/`profiles`/`base_book`/`base_profile` resolve for good through
`cherrypick.core.config`. That module also separates what `cfg.get(key, {})` collapses: a registry
**declared empty** is an operator turning every arm off; a registry **absent** is a moved key, and only
that one warns. Every config read defaults silently, so a moved key does not fail — every arm falls
through to its `enabled` default on `defaults`, and the A/B measures nothing while the P&L looks fine.

**Spelling: Python identifiers are American, TypeScript identifiers are British, prose is British, and
`cancelled` is British everywhere including stored values.** That is the existing convention
(`realized_net` in `.py`, `unrealisedNet` in `.ts`); the boundary re-spells, which is not a bug to fix.

## Trade histories and reports: one money layout

Every surface that shows a trade — console table, review fact set, report, notification — states money
the same way, so modules can be compared. Flies is the reference implementation;
`packages/console/CLAUDE.md` holds the console specifics.

- **"P&L" means net, always.** net = gross − fees − settlement (− slippage, where charged as a cost). A
  gross figure is labelled gross.
- **Signed cash flow.** A credit received is `+`, a debit paid is `−`, on every entry and exit.
- **Whole-position dollars** (price × multiplier × quantity). The one per-share figure is the net
  price, and it says so (`1.25 cr` / `0.40 db`).
- **Rows add up.** entry + exit = gross; gross − every cost column = net. A surface derives what a
  ledger lacks so the identities hold, and never shows two figures that cannot be reconciled.
- **Costs are separate.** Trading fees (commissions, exchange) and settlement (exercise, assignment,
  delivery) are different columns. Where a fee total includes settlement, it is subtracted once; where
  the split was never recorded, the total stays in fees and settlement reads `n/r` — never a zero.
- **Slippage is its own column and says which model it is.** Where the modelled fill already concedes
  it (flies, meic) it is inside gross — shown as a measure, never subtracted again. Where fills are at
  mid and slippage is charged as a cost (bwb, calendars, pmcc, curve, earnings) it is its own cost
  column, subtracted once, out of fees. A figure subtracted in one module and not another is never
  shown under one unqualified name.
- **Open and closed are different tables.** Open carries the entry side (and a mark, where recorded);
  history carries the exit, how it ended (`closed` / `settled` / `expired` / `assigned`), gross, costs
  and net. Cancelled and voided rows are in neither.
- **Fee drag** is fees ÷ premium collected, named that way.

This is a presentation rule. Recording a missing cost component in a ledger is not
measurement-affecting — it changes what a row *says*, not what the module did — so it lands
immediately, with a dry-run-by-default backfill where history can be recovered.

## Two working rules, both learned the hard way

**Measure a duplication before folding it, and normalize the identifier first.** "The same function in
six places" is the least reliable claim in this repo, both ways: one dedup pass found about ten false
premises (helpers that only look alike), and another reported 6 identical functions when there were
**22**, because it hashed table-name constants with the logic. Compare bodies with the varying
identifier normalized out, then read call sites before folding. Where copies differ, ask whether the
difference is the point — `_wing_width_multiple` is byte-identical across three earnings strategies and
stays copied, because it shapes an order and that file says so.

**A guard has to be shown to fail.** The config lints and enforcement tests (same-index correlation,
single cache writer, guarded-live-pointer table) are only worth their failures, and each was verified
by breaking the invariant on purpose and watching it report the right thing. Do the same for any new
one, and drive it off what the system itself declares (a module's config example, its own
`stream_requests` file) rather than a hand-kept list. A green check that cannot fire is worse than no
check: it reads as coverage.
