# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own CLAUDE.md:

- **packages/orchestrator** — the orchestrator: watchdog, OS scheduler, notifications, and the read side
  (report / dashboard / reconcile / calibrate). Drives the modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — the live GEX (gamma exposure) engine and spot-trail recorder. It computes and
  records; the console renders it.
- **packages/streamer** — the standalone market-data streamer: the suite's **single** producer of the
  shared stream cache every module reads. Modules declare their symbols via `state/stream_requests/`;
  it streams the union. Nothing else may write that cache.
- **packages/flies** — 0DTE net-credit butterfly ("profit forest") module. Paper by default, with a
  deliberately narrow, per-day-armed live pilot (one arm, one symbol, one incomplete position at a
  time); deliberately built to make a negative result usable: floors are measured after fees, and a
  book-level floor always carries the price band over which it holds.
- **packages/calendars** — weekly SPY double-calendar module, **paper-only** and credential-free: a
  pure stream-cache consumer whose 4DTE/7DTE chains come from the streamer's `expirations` request
  field. Built as a forward exit-parameter experiment rather than a strategy with an opinion — a
  mechanical `control` book (close everything at Friday's bell), a permissive `path` book that holds
  every leg to expiry and records a per-tick mark path, and a read-side replay (`exit_policies.py`)
  that scores profit targets, stops, strike-touch and exit timings over that path with exact
  pairing, validated to the cent against the real books on every run. Holiday weeks are tagged
  distinct structures and never pooled. Models both settlement styles — European cash and, since
  the 2026-08-15 move off SPX, American physical delivery, where an ITM short hands over shares held
  across the weekend — and refuses at entry any symbol declared as neither. Ex-dividend weeks are
  skipped outright, from a declared issuer calendar refreshed annually, rather than modelling early
  assignment. There is no live path.
- **packages/pmcc** — PMCC-99 deep-ITM covered-call module on TQQQ, **paper-only** and
  credential-free in the calendars posture: a pure stream-cache consumer whose ~7DTE/~21DTE chains
  come from the streamer's `expirations` request field and whose deep strikes come from its
  `window_hints` field. Buys an 85-90-delta call as a stock substitute and sells the ATM call
  nearest spot (no yield floor, either side of spot), holding to the short's own expiration before
  closing both legs together (2026-08-23 redesign, down from a 3-symbol/3-book design). Single book
  `control` plus the advisor's synthetic `advised:control` twin, which is where the old
  tv-exhaustion early exit survives as a tunable A/B (`tv_managed_exit`) against the new
  hold-to-expiry default. American physical settlement is modelled with the calendars decomposition
  (assigned short shares ride to the next session's combined disposal); **early assignment is
  measured, never modelled** — ex-dividend spans are refused from a declared issuer calendar
  refreshed quarterly, and every mark with near-zero short extrinsic is flagged assignment-exposed,
  so the paper result is an explicit upper bound. There is no live path.
- **packages/curve** — VXX call-credit-spread module harvesting the VIX term-structure roll yield,
  gated by a daily VIX/VIX3M regime read; **paper-only and credential-free** in the calendars/pmcc
  posture (a pure stream-cache consumer, VXX's target expiration and VIX/VIX3M quote-only legs
  declared via `state/stream_requests/`). Three books trade the identical short-call/long-wing
  structure and differ only in entry gate and exit rule: `control` (contango-gated entry,
  profit-take or a regime-flip hard exit or `close_dte`), `noflip` (control's entry exactly — its
  exit is control's minus the flip rule, so control/noflip are byte-identical until a flip fires by
  construction), `hook` (only the rare two-day-confirmed deep-backwardation entry). The daily
  ratio/regime/hook classification is recorded every session, traded or not, as the module's second
  product — RTH-gated and basis-stamped so an overnight-frozen quote can never masquerade as a
  measured reading. Early assignment and VXX's periodic reverse splits are measured, never
  modelled, so the paper result is an explicit upper bound. `regime-history` replays the VIX/VIX3M
  classification over stored history as a signal-separation benchmark (never suite P&L); the
  credit-spread P&L itself has no synthetic backtest, per the advisor's own no-replay-engine
  contract — only a forward-recorded per-tick mark path, replayable later. There is no live path.
- **packages/bwb** — a daily-laddered SPX put broken-wing butterfly module, **paper-only and
  credential-free** in the calendars/pmcc/curve posture: a pure stream-cache consumer entering one
  BWB every session at the expected move for a net credit (zero-floor by design), ~7 DTE, held to
  expiry. Four books trade the IDENTICAL base structure and differ only in whether/when a
  reversal-triggered put credit spread add-on fires, turning the fly into a 1-3-2: `control`
  (never), `delta` (raw delta touch), `bounce` (a confirmed pullback off a peak), `flip` (a
  gamma-flip reclaim, read fresh each tick from the same basis MEIC's own gate uses). Trigger
  latches persist on the position row so a supervisor restart can't amnesia a morning touch, and a
  cohort-keyed trigger-tick path — the module's second product — is recorded every session for a
  future read-side threshold replay. SPX is cash-settled and European-style, the cleanest
  settlement model in the suite. There is no live path.
- **packages/overview** — the pre-open **morning market overview**: one deterministic fact pack per
  session (index/vol/sector readings from the stream cache, gamma flip and walls from the suite's
  own GEX history) with a mechanical GREEN/YELLOW/RED phase from five declared gates — missing data
  can never produce RED and always blocks GREEN. A pure stream-cache + GEX consumer in the
  calendars/pmcc posture: credential-free, network-free, read-only over everything it touches,
  writing only into its own store. Its breadth symbols (VIX/VIX3M/VVIX, the sector ETFs, USO/GLD as
  labeled proxies) are declared via `state/stream_requests/`. The morning narrative — with its
  render-time macro-calendar lookups — is written *outside* the package by
  `scripts/morning_narrative.py`, the same fence that holds `scripts/eod_narrative.py`.
- **packages/console** — the reactive web UI (Node + TypeScript, React SPA on 127.0.0.1:5070) and the
  suite's **only** read surface since 2026-08-12: every module's read models in one app. The research
  and screening surfaces it inherited from scout (watchlist, screener, builder, payoff, staged
  tickets) were **retired 2026-08-31** — it is now a read surface for the trading modules alone, and
  holds no path that touches an order at all. The supervisor keeps it running as an always-on resident job, restarted on
  death and on a stale `state/console.heartbeat` (a wedged Node event loop stays alive). Read-only
  over every other package's data (its own store is `~/.cherrypick/data/console/`); reads the shared
  suite credential and never writes one, gating its write-oriented functions on the token's probed
  scope; no order-placement code paths. Its **Config page** is the one bounded exception to
  read-only, and holds no write logic of its own: the live-trading halt toggle and a short
  allow-list of frequently-changed settings, both applied by invoking the orchestrator's own config
  editor as a subprocess, so the guarded live-trading fields stay unreachable from here.
- **packages/review** — the suite's cross-module **end-of-day review**: one versioned fact set per
  session covering meic/flies/earnings/calendars/pmcc together, plus the renders of it. Read-only over every other
  package (via `cherrypick.core.ledgers`, the single home for per-schema net/risk rules), writes only
  into its own store. Exists because answering "what did the suite do today" inside each package
  produced six incomparable report families and two normalisation layers that had already drifted.
  No broker credentials, no network, no AI: the narrative is written *outside* the package by a
  scheduled agent reading the fact set, so a failed narrative can never damage a report.
- **packages/advisor** — the deterministic half of the **AI advisor**: the fact packs a model reads
  four times a trading day, the validation of what it replies, and the paper A/B experiments its
  admitted proposals run as. It contains **no AI** — the model is invoked by
  `scripts/advisor_checkpoint.py`, outside every package, the same fence that holds
  `scripts/eod_narrative.py`. Read-only over every other package (live data too, clearly labelled
  and only in `factpack.py`); the one thing it can emit toward a loop is a bounded, expiring paper
  advice artifact through `cherrypick.core.advice`, which each module applies to a synthetic
  `advised:<base>` book beside its control. Off by default twice over: the suite must schedule it,
  and each module must declare its own `advice` bounds.
- **packages/desk** — ⚠️ **EXPERIMENTAL.** The **manual trading desk** and the suite's only
  *discretionary* live-order path (meic/earnings/flies each have a live loop behind their own
  `enable_live_trading` gate; this has no loop): a foreground, human-initiated CLI for
  discretionary live orders, authorized entirely on its own (own config, own PIN kept as a salted
  verifier, per-order ticket, own policy gates) so it never touches a module's `enable_live_trading`.
  It stores no broker secrets — it borrows a module's keyring session, because borrowing credentials
  is not borrowing permissions. Not a strategy module — no loop, no
  schedule, no ledger. It exists so placing one discretionary order never requires temporarily
  flipping a guarded, plan-gated module flag. Never scheduled; no automated package may import it.

The shared library `cherrypick.core` is **`packages/core`**, a sibling package in this same monorepo,
installed as an editable dependency by every other package. Fresh clone: `pip install -e packages/core`
first (or run `scripts/dev-install.ps1`/`.sh` from the repo root, which installs it and every package).

Suite-wide guardrails apply across every package (each package's CLAUDE.md states them): instruction
files hold no code; account numbers masked to `****1234`; portable paths only; human-voice docs/commits
(no AI attribution); deterministic solutions preferred over AI/agentic ones (below); paper↔live isolation
(the orchestrator only drives paper; its one live-config action is onboarding/account selection).

**Deterministic solutions and workflows are preferred over AI or agentic ones.** This is a
standing design preference, not a prohibition: where a problem can be solved by a pure function over
data you already have, solve it that way rather than by asking a model or adding an agent.

The reason is what this suite is FOR. It exists to measure whether strategies make money, and a
measurement is only worth what its reproducibility is worth. A deterministic path fails the same way
twice, can be re-run over last month's rows, and can be pinned by a test that fails when it breaks.
An agentic one gives a different answer on Tuesday and leaves you unable to say whether the strategy
changed or the reasoning did. That is why the modules that carry real weight here — `engine.py`,
`management.py`, `fly.py` — are pure functions over a pre-fetched snapshot, and why the read side
re-derives from stored rows rather than remembering.

Where AI genuinely earns its place, keep the failure contained. The pattern already in use is worth
copying: every AI-shaped thing runs OUTSIDE the packages, in `scripts/` — `eod_narrative.py`,
`morning_narrative.py`, `advisor_checkpoint.py` — so a failed narrative costs a narrative and never
a report, a ledger or a loop. `packages/advisor` is the sharper version: it contains no AI at all,
and enforces that with `tests/test_guardrails.py`'s forbidden-import set, because what it produces
is validated parameter advice rather than prose.

The same preference applies to network and MCP dependencies, and for the same reason rather than as
a separate rule: they are non-deterministic inputs. The 2026-07-01 incident is the one to remember —
an external streamer dependency stalled silently for 34 hours, and nothing on the decision path
noticed, because a dependency that hangs looks exactly like a quiet market. Prefer the local stream
cache; reach outward to ACT (place an order) or to confirm what only the broker can know (a fill).

None of this forbids anything outright. It says which way to lean, and that leaning away from it is
a choice to make deliberately and write down — not a default.

**Measurement-affecting changes are BATCHED to declared boundaries.** Several modules already
record their own measurement breaks — flies' 60s→15s cadence change, earnings' 2026-08-12 managed
exits, calendars' tick cadence — and each states the same thing locally: results either side must
never be pooled. The suite-wide rule is about *when* to make such a change, not how to journal it.

A measurement-affecting change is one that alters what a session's numbers MEAN rather than what
they are: tick cadence, entry pacing, gate semantics, the definition of a book's net, which arms
exist. Landing them one at a time, whenever each is ready, restarts the evidence clock on every
landing — a module that changes something every few days has no comparable stretch longer than a
few days, and its longest-running experiment is worth exactly as much as its most recent
convenience fix. That is how a forward test quietly becomes a series of unrelated short ones.

So: hold measurement-affecting changes until a declared boundary, land them together, and journal
the boundary once. A bug fix that corrects a number the module was recording WRONG is not in this
category and should land immediately — the flies bwb roll-pricing defect is the example, where
waiting would only have produced more rows resting on a spread that was never the trade.

Pure code changes — refactors, read surfaces, dedup — are not measurement-affecting and are not
covered by this. If it does not change what a recorded number means, it is not a break.

## Two working rules, both learned the hard way

**Measure a duplication before folding it, and normalize the identifier first.** "These are the
same function in six places" is the single least reliable claim in this repo, in both directions.
During the 2026-08-20 dedup effort roughly ten such premises were already-done or simply wrong:
two of four named earnings helpers were never duplicated (six two-line per-strategy dispatchers
that merely look alike), the console's polling was already gated by its query library's default,
and a reader's queries were already bounded. It fails the other way too — the first LedgerStore
measurement reported 6 identical functions when the real answer was **22**, because the comparison
hashed table-name string constants along with the logic. So: compare the bodies with the varying
identifier normalized out, then read the call sites back individually before folding. Where the
copies differ, ask whether the difference is the point — `_wing_width_multiple` is byte-identical
across three earnings strategies and stays copied, because it shapes an order and that file says so.

**A guard has to be shown to fail.** This suite now carries several config-lint and enforcement
tests whose entire value is failing — the same-index correlation lint, the single-cache-writer
check, the guarded-live-pointer table. Each was verified by breaking the invariant on purpose and
watching the test report the right thing. Do the same for any new one, and prefer driving it off
what the system itself declares (a module's own config example, its own `stream_requests` file)
rather than a hand-kept list, so a new module is covered the moment it declares the thing. A green
check that cannot fire is worse than no check: it reads as coverage.

Any developer initiated documentation review and update must also review and update package documentation.

For front-end UI/UX testing, use a browser to confirm the build performs as expected. Don't rely solely on code tests.