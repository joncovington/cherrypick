# The root CLAUDE.md as it stood before consolidation

**Frozen 2026-09-29.** The suite-root `CLAUDE.md` was consolidated that day to roughly half its
length. Every rule it held is still there; what moved out is the longer package descriptions and the
rationale around the vocabulary, spelling and money-layout conventions. This is the full text as it
stood immediately before, kept verbatim so that rationale is not lost. **It does not describe the
suite as it is now** — for that, read the root [CLAUDE.md](../../CLAUDE.md) and each package's own.

---

# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own CLAUDE.md:

- **packages/orchestrator** — watchdog, OS scheduler, notifications, and the read side (report /
  dashboard / reconcile / calibrate). Drives modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — the live GEX (gamma exposure) engine and spot-trail recorder; it computes and
  records, the console renders.
- **packages/streamer** — the suite's **single** producer of the shared stream cache every module
  reads. Modules declare symbols via `state/stream_requests/`; nothing else may write that cache.
- **packages/flies** — 0DTE net-credit butterfly ("profit forest") module. Paper by default, with a
  deliberately narrow, per-day-armed live pilot (one arm, one symbol, sized by a worst-case
  buying-power cap); built to make a negative result usable — floors are measured after fees, and a book-level
  floor always carries the price band over which it holds.
- **packages/calendars** — weekly SPY double-calendar module, **paper-only** and credential-free: a
  pure stream-cache consumer whose 4DTE/7DTE chains come from the streamer's `expirations` request
  field. A forward exit-parameter experiment rather than a strategy with an opinion — a mechanical
  `control` book (close everything at Friday's bell), a permissive `path` book that holds every leg
  to expiry and records a per-tick mark path, and a read-side replay (`exit_policies.py`) scoring
  profit targets, stops, strike-touch and exit timings over that path, validated to the cent against
  the real books every run. Holiday weeks are tagged distinct structures and never pooled. Models
  both settlement styles — European cash and American physical delivery — and refuses at entry any
  symbol declared as neither. Ex-dividend weeks are skipped outright rather than modelling early
  assignment. No live path.
- **packages/pmcc** — PMCC-99 deep-ITM covered-call module on TQQQ, **paper-only** and
  credential-free in the calendars posture: a pure stream-cache consumer whose ~7DTE/~21DTE chains
  and deep strikes come from the streamer's `expirations`/`window_hints` fields. Buys an 85-90-delta
  call as a stock substitute and sells the ATM call nearest spot, holding to the short's own
  expiration before closing both legs together. Single book `control` plus the advisor's synthetic
  `advised:control` twin (the old tv-exhaustion exit, `tv_managed_exit`, as a tunable A/B against
  hold-to-expiry). American settlement uses the calendars decomposition; **early assignment is
  measured, never modelled** — ex-dividend spans are refused, and any near-zero-extrinsic mark is
  flagged assignment-exposed, so the paper result is an explicit upper bound. No live path.
- **packages/curve** — VXX call-credit-spread module harvesting the VIX term-structure roll yield,
  gated by a daily VIX/VIX3M regime read; **paper-only and credential-free** in the calendars/pmcc
  posture (VXX's target expiration and VIX/VIX3M quote-only legs declared via
  `state/stream_requests/`). Three books trade the identical short-call/long-wing structure and
  differ only in entry gate and exit rule: `control` (contango-gated entry, profit-take or a
  regime-flip hard exit or `close_dte`), `noflip` (control's entry, minus the flip exit —
  byte-identical to `control` until a flip fires), `hook` (only the rare two-day-confirmed
  deep-backwardation entry). The daily ratio/regime/hook classification is recorded every session,
  traded or not, as the module's second product. Early assignment and VXX's periodic reverse splits
  are measured, never modelled, so the paper result is an explicit upper bound. `regime-history`
  replays that classification as a signal-separation benchmark, never suite P&L — the credit-spread
  P&L has no synthetic backtest, only a forward-recorded mark path. No live path.
- **packages/bwb** — a daily-laddered SPX put broken-wing butterfly module, **paper by default**
  with its paper loop credential-free in the calendars/pmcc/curve posture: entering one BWB every
  session at the expected move for a net credit (zero-floor by design), ~7 DTE, held to expiry.
  Four books trade the identical base structure and differ only in whether/when a
  reversal-triggered put credit spread add-on fires, turning the fly into a 1-3-2: `control`
  (never), `delta` (raw delta touch), `bounce` (a confirmed pullback off a peak), `flip` (a
  gamma-flip reclaim, read fresh each tick from the same basis MEIC's own gate uses). Trigger
  latches persist on the position row so a supervisor restart can't amnesia a morning touch. SPX is
  cash-settled and European-style, the cleanest settlement model in the suite. **A narrow live path
  exists**, in the flies posture: one configurable arm, armed per day by `/live-bwb-start`, under a
  worst-case margin cap, a cost-derived credit floor, and a mark-drawdown breaker that blocks
  entries only — every fill is the broker's word, and settlement lands on an official print or not
  at all. The paper books are untouched by it.
- **packages/overview** — the pre-open **morning market overview**: one deterministic fact pack per
  session (index/vol/sector readings from the stream cache, gamma flip and walls from the suite's
  own GEX history) with a mechanical GREEN/YELLOW/RED phase from five declared gates — missing data
  can never produce RED and always blocks GREEN. A pure stream-cache + GEX consumer in the
  calendars/pmcc posture: credential-free, network-free, read-only over everything it touches. Its
  breadth symbols (VIX/VIX3M/VVIX, sector ETFs, USO/GLD as labeled proxies) are declared via
  `state/stream_requests/`. The morning narrative is written *outside* the package by
  `scripts/morning_narrative.py`, the same fence as `scripts/eod_narrative.py`.
- **packages/technicals** — the market report's **end-of-day store and technical engines**
  (`docs/market-report-plan.md`). Lands raw daily bars, splits, dividends and IV history from the
  LOCAL Dolt clones for the universe candidates, the rotation ETFs and the benchmarks; adjusted bars
  are a pure function over raw, computed on read, and match the vendor's own adjusted bars to the
  cent. Credential-free and network-free: the clones are pulled by `scripts/refresh_dolt_data.py`,
  and the universe and vendor captures it reads are written by scripts. Its engines -- the level
  grid, the rank, trend, stage, rotation and the scan rules -- are fitted against the vendor's
  output and scored out of sample; a daily report and a per-name chart file are what the console
  reads. Level SELECTION is not yet reproduced, and the docs say so.
- **packages/console** — the reactive web UI (Node + TypeScript, React SPA on 127.0.0.1:5070) and the
  suite's **only** read surface: every module's read models in one app. The research/screening
  surfaces inherited from scout (watchlist, screener, builder, payoff, staged tickets) have been
  **retired** — it holds no path that touches an order. The supervisor keeps it running as an
  always-on resident job, restarted on death and on a stale `state/console.heartbeat` (a wedged Node
  event loop stays alive). Read-only over every other package's data (own store:
  `~/.cherrypick/data/console/`); reads the shared suite credential and never writes one. Its
  **Config page** is the one bounded exception: the live-trading halt toggle and a short allow-list
  of settings, applied by invoking the orchestrator's own config editor as a subprocess, so the
  guarded live-trading fields stay unreachable from here.
- **packages/review** — the suite's cross-module **end-of-day review**: one versioned fact set per
  session covering meic/flies/earnings/calendars/pmcc, plus the renders of it. Read-only over every
  other package (via `cherrypick.core.ledgers`, the single home for per-schema net/risk rules),
  writes only into its own store — built because answering "what did the suite do today" inside
  each package had produced six incomparable report families. No broker credentials, no network, no
  AI: the narrative is written *outside* the package by a scheduled agent reading the fact set.
- **packages/advisor** — the deterministic half of the **AI advisor**: the fact packs a model reads
  four times a trading day, the validation of what it replies, and the paper A/B experiments its
  admitted proposals run as. It contains **no AI** — the model is invoked by
  `scripts/advisor_checkpoint.py`, outside every package, the same fence as `scripts/eod_narrative.py`.
  Read-only over every other package; the one thing it can emit toward a loop is a bounded, expiring
  paper advice artifact through `cherrypick.core.advice`, applied to a synthetic
  `advised:<experiment name>` book beside each module's control. Off by default twice over: the
  suite must schedule it, and each module must declare its own `advice` bounds.
- **packages/desk** — ⚠️ **EXPERIMENTAL.** The **manual trading desk** and the suite's only
  *discretionary* live-order path (meic/earnings/flies/bwb each have a live loop behind their own
  `enable_live_trading` gate; this has no loop): a foreground, human-initiated CLI for discretionary
  live orders, authorized entirely on its own (own config, own PIN, per-order ticket, own policy
  gates). It stores no broker secrets — it borrows a module's keyring session, because borrowing
  credentials is not borrowing permissions. Not a strategy module — no loop, no schedule, no ledger,
  never scheduled, and no automated package may import it.

The shared library `cherrypick.core` is **`packages/core`**, installed as an editable dependency by
every other package. Fresh clone: `pip install -e packages/core` first (or run
`scripts/dev-install.ps1`/`.sh` from the repo root, which installs it and every package).

Suite-wide guardrails apply across every package: instruction files hold no code; account numbers
masked to `****1234`; portable paths only; human-voice docs/commits (no AI attribution);
deterministic solutions preferred over AI/agentic ones (below); paper↔live isolation (the
orchestrator only drives paper; its one live-config action is onboarding/account selection). Each
package's CLAUDE.md should state only what it adds or tightens beyond this, not restate the list —
and any developer-initiated documentation review must review and update package documentation too.

**Deterministic solutions are preferred over AI or agentic ones** — a standing preference, not a
prohibition: where a problem can be solved by a pure function over data you already have, solve it
that way. Reason: this suite exists to measure whether strategies make money, and a measurement is
only worth what its reproducibility is worth — a deterministic path fails the same way twice and can
be re-run over last month's rows; an agentic one gives a different answer on Tuesday. That's why
`engine.py`, `management.py`, `fly.py` are pure functions over a pre-fetched snapshot. The same
reasoning covers network/MCP dependencies as non-deterministic inputs: prefer the local stream
cache; reach outward only to ACT or to confirm a fill — an external streamer dependency once stalled
silently for 34 hours with nothing on the decision path noticing, which looked exactly like a quiet
market. Where AI earns its place anyway, contain the failure: it runs OUTSIDE the packages, in
`scripts/` (`eod_narrative.py`, `morning_narrative.py`, `advisor_checkpoint.py`), so a failed
narrative costs a narrative and never a report, a ledger or a loop. None of this forbids anything
outright — it says which way to lean, and leaning away from it is a choice to write down.

**Measurement-affecting changes are batched to declared boundaries.** A measurement-affecting change
alters what a session's numbers MEAN rather than what they are — tick cadence, entry pacing, gate
semantics, a book's net definition, which arms exist. Landing them one at a time restarts the
evidence clock on every landing, so hold them until a declared boundary, land them together, and
journal the boundary once. A bug fix that corrects a number the module was recording WRONG is not in
this category and should land immediately — waiting only produces more rows resting on a defect.
Pure code changes (refactors, read surfaces, dedup) aren't measurement-affecting either.

## The suite's vocabulary

**An `arm` is one configured variant, run as its own portfolio.** Control and treatment, in the
clinical sense — which is exactly what these are, and why `arm` won over the alternatives. The
modules shipped four names for it (`arm` in flies, `book` in bwb/pmcc/curve/calendars,
`risk_profile` in meic, `profile` in earnings) and two normalization layers that disagreed about
which was canonical: `core/ledgers.py` maps every module's column to a field called `profile`,
while the console's attempts reader maps the same columns to `armColumn`. The word is now `arm`,
everywhere a new name is chosen.

**Three words are taken, and must never be used for an arm:**

| Word | What it already means | Where |
|---|---|---|
| `book` | flies' per-session P&L roll-up **of** an arm — a time slice, not a variant | `fly_books`, `book_id` |
| `book` | the paper-vs-live ledger designation | review fact sets, `modules.<m>.book = "paper"` |
| `profile` | a config preset registry | `meic/config.risk.json`, `core.profiles.load_profiles` |
| `profile` | the gamma-by-strike curve | `GexProfileChart`, `useGexProfile` |
| `strategy` | earnings' **structure type** (iron_fly vs double_calendar) | earnings is the only two-axis module: profile × strategy |

Renaming flies' `arm` to `book` would have put two meanings on one word inside one schema, which
is why `book` lost despite being four of the seven source columns.

**Existing spellings are never withdrawn.** A module config is a file a person edits and keeps
across upgrades, and there is no migration for one — so `books`/`profiles`/`base_book`/
`base_profile` keep resolving for good, through `cherrypick.core.config`. That module also draws
the distinction `cfg.get(key, {})` collapses: a registry **declared empty** is an operator turning
every arm off, while a registry **absent** is a key that moved, and only the second is worth a
warning. Every config read in the suite defaults silently, so a moved key does not fail — it
resolves empty, every arm falls through to its `enabled` default, every arm runs on `defaults`,
and the A/B measures nothing while the P&L looks fine.

**Spelling: Python identifiers are American, TypeScript identifiers are British, prose is
British, and `cancelled` is British everywhere including stored values.** That is the de-facto
convention rather than a new rule — `realized_net` 330 times in `.py` against 11 `realised`, and
not one American-spelled identifier of that family in `.ts` (`unrealisedNet`, `realisedNet`,
`UnrealisedPnlCell`). The Python→TypeScript boundary re-spells; that is the convention working,
not a bug to fix. Writing it down costs a paragraph; normalising it would rename ~200 identifiers
and change no behaviour.

## Trade histories and reports: one money layout

Every surface that shows a trade — a console table, a review fact set, a report, a notification —
states its money the same way. The suite had drifted into a column called "P&L" that was gross on
two trade logs, an at-risk figure 100× small on four modules, and settlement fees folded silently
into a fee total; each was correct by its own module's lights, and none could be compared with
another.

- **"P&L" means net, always.** net = gross − fees − settlement (− slippage, where it is charged as a
  cost). A gross figure is labelled gross.
- **Signed cash flow.** A credit received is `+`, a debit paid is `−`, on every entry and exit.
- **Whole-position dollars.** Money is price × multiplier × quantity. The one per-share figure is
  the net price, and it says so (`1.25 cr` / `0.40 db`).
- **Rows add up.** entry + exit = gross; gross − every cost column = net. A surface derives what a
  ledger lacks so the identities hold, and never shows two figures that cannot be reconciled.
- **Costs are separate.** Trading fees (commissions, exchange) and settlement (exercise, assignment,
  delivery) are different columns. Where a ledger records a fee total with settlement as a
  component, it is subtracted once; where the split was never recorded, the total stays in fees and
  settlement reads `n/r` — never a zero.
- **Slippage is its own column, and says which model it is.** The suite prices it two ways. Where
  the modelled fill price already concedes it (flies, meic), it is inside gross — shown beside the
  costs as a measure and never subtracted again. Where fills are taken at mid and slippage is
  charged as a cost (bwb, calendars, pmcc, curve, earnings), it is a cost: its own column, subtracted
  once, and out of the fee column. The header's title says which; a figure that is subtracted in
  one module and not in another is never shown under one unqualified name.
- **Open and closed are different tables.** Open positions carry the entry side (and a mark, where
  the module records one); history carries the exit, how it ended (`closed` / `settled` /
  `expired` / `assigned`), gross, costs and net. Cancelled and voided rows are in neither.
- **Fee drag** is fees ÷ premium collected, named that way.

This is a presentation rule. Recording a missing cost component in a ledger is not
measurement-affecting — it changes what a row *says*, not what the module did — so it lands
immediately, with a dry-run-by-default backfill where history can be recovered. Flies is the
reference implementation; `packages/console/CLAUDE.md` holds the console specifics.

## Two working rules, both learned the hard way

**Measure a duplication before folding it, and normalize the identifier first.** "These are the
same function in six places" is the single least reliable claim in this repo, in both directions —
a past dedup effort found roughly ten already-done-or-wrong premises (helpers that only look
alike), and separately once reported 6 identical functions when the real answer was **22**, because
the comparison hashed table-name string constants along with the logic. Compare bodies with the
varying identifier normalized out, then read call sites individually before folding. Where copies
differ, ask whether the difference is the point — `_wing_width_multiple` is byte-identical across
three earnings strategies and stays copied, because it shapes an order and that file says so.

**A guard has to be shown to fail.** This suite carries several config-lint and enforcement tests
whose entire value is failing — the same-index correlation lint, the single-cache-writer check, the
guarded-live-pointer table. Each was verified by breaking the invariant on purpose and watching the
test report the right thing. Do the same for any new one, and prefer driving it off what the system
itself declares (a module's own config example, its own `stream_requests` file) rather than a
hand-kept list. A green check that cannot fire is worse than no check: it reads as coverage.