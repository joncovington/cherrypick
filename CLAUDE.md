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
- **packages/pmcc** — PMCC-99 deep-ITM covered-call module on leveraged ETFs (TNA/TQQQ/UPRO),
  **paper-only** and credential-free in the calendars posture: a pure stream-cache consumer whose
  ~9DTE/~21DTE chains come from the streamer's `expirations` request field and whose 30–45%-ITM
  strikes come from its `window_hints` field. Buys the deepest ~99-delta call as a stock substitute,
  sells the deepest ITM call whose weekly time-value yield clears a declared floor, and closes both
  legs when that time value is exhausted. Three books isolate one variable each — `control` (as
  taught), `keltner` (entry gated on a pullback-and-reversal read of the Keltner channel; cold-starts
  ~21 trading days while daily bars accumulate), `roll` (rolls the short on a breach instead of
  holding; shares control's fills for exact pairing). American physical settlement is modelled with
  the calendars decomposition (assigned short shares ride to the next session's combined disposal);
  **early assignment is measured, never modelled** — ex-dividend spans are refused from a declared
  issuer calendar refreshed quarterly, and every mark with near-zero short extrinsic is flagged
  assignment-exposed, so the paper result is an explicit upper bound. There is no live path.
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
  suite's **only** read surface since 2026-08-12: every module's read models plus the research and
  screening surfaces in one app. The supervisor keeps it running as an always-on resident job, restarted on
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
(no AI attribution); no MCP/network/AI on any loop-decision or reliability path; paper↔live isolation
(the orchestrator only drives paper; its one live-config action is onboarding/account selection).

**The no-network rule, stated exactly.** It binds the *decision* and *reliability* paths: nothing
that decides an entry, an exit or a gate, and nothing the supervisor depends on to stay alive, may
reach the network, an MCP server or a model. It has never meant "no package makes a network call",
and reading it that way makes the real carve-outs look like violations rather than the deliberate,
bounded things they are:

- **Brokers and market data.** Placing an order, confirming a fill and streaming quotes are network
  by definition. The rule is that the *decision* is made from the local stream cache first; the
  broker is touched to act, or to confirm what only it can know. `packages/console` additionally
  opens its own DXLink session through the official SDK, off every loop.
- **The two networked notifiers.** `lossdog_notifier.py` and `follow_notifier.py` post outward.
  They are on the *notification* path — a failed send costs a message, never a tick or a trade.
  (`desk_notifier.py` and `trade_notifier.py` make no network calls of their own.)
- **Everything AI-shaped runs OUTSIDE every package**, in `scripts/` — `eod_narrative.py`,
  `morning_narrative.py`, `advisor_checkpoint.py`. That fence is the rule's real teeth: a failed
  narrative can damage a report, never a loop.

If a change would put a network call inside a loop's decision or on the supervisor's own path, the
rule is unchanged and the answer is no.

Any developer initiated documentation review and update must also review and update package documentation.

For front-end UI/UX testing, use a browser to confirm the build performs as expected. Don't rely solely on code tests.