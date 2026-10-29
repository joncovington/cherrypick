# Packages: what each one may do

Moved from the root CLAUDE.md. Each entry keeps the package's posture: live or paper, credentials,
what it may write. The package's own CLAUDE.md holds the detail.

- **packages/orchestrator** — watchdog, OS scheduler, notifications, and the read side (report /
  reconcile / calibrate / review). Drives modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module. Paper by default; its live
  loop is armed per day by `/live-meic-start` (the supervisor's `meic-live` job while that day's arm
  record is valid, self-disarming at 17:00) and sized from the config's `live` block, which may name
  one arm (`live.arm`).
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — the live GEX engine and spot-trail recorder; it computes and records, the console
  renders.
- **packages/streamer** — the **single** producer of the shared stream cache every module reads.
  Modules declare symbols via `state/stream_requests/`; nothing else may write that cache.
- **packages/flies** — 0DTE net-credit butterflies. **Retired by the developer in the live config on
  2026-10-19; may be reinstated at a later date** (`modules.flies.enabled`). Paper by default, with a
  narrow, per-day-armed live pilot (one arm, one symbol, a worst-case buying-power cap). Floors are
  measured after fees, and a book-level floor always carries the price band over which it holds.
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
  them late), with `shield_guard` from 2026-10-26 (rolls an assignment-exposed in-the-money short); `control` (a 21-DTE long re-bought each cycle) and its advised twins are retired and run
  off, and pmcc's advice is off. A short spanning an earnings announcement is refused, as one spanning
  an ex-date is. SLV is the intended first live symbol. **Early assignment
  is measured, never modelled**: ex-dividend spans refused, near-zero-extrinsic marks flagged
  assignment-exposed, so the paper result is an explicit upper bound. No live path yet.
- **packages/curve** — VXX call-credit spreads gated by a daily VIX/VIX3M regime read; **paper-only,
  credential-free**. Books `control`, `noflip` (byte-identical to control until a flip fires),
  `hook` (retired from 2026-10-15) and (from 2026-10-06) `near`, a 0.40-delta short, differ in entry gate, exit rule or short
  delta; $2-wide from 2026-10-06, its credit floor net of entry costs against max loss. The daily classification is recorded every session,
  traded or not. Early assignment and VXX reverse splits are measured, never modelled (an upper
  bound). `regime-history` is a signal-separation benchmark, never suite P&L. No live path.
- **packages/contango** — the same VIX/VIX3M signal held in shares: SVXY in contango, SHV
  (T-bills) otherwise, one decision ten minutes before the close; **paper-only, credential-free**,
  a stream-cache consumer with fund distributions read from the local technicals store. Arms
  `control` (out at 0.97) and `flipexit` (out at 1.0) differ only in thresholds. Nothing fills
  after the decision window. Validated against `scripts/contango_replay.py`, which runs the loop's
  own rule. Trade schema `contango_etf`; off by default in the example. No live path.
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
- **packages/fundamentals** — company fundamentals and the fair-value file (moved out of technicals
  on 2026-10-29): each company's SEC filings (point in time by filing date), Dolt's annual statements
  and revenue consensus, and Damodaran's public ERP and coverage-to-rating table, into its own store;
  the daily fair-value file the Screeners tab and the technicals income lists read. Credential-free
  and network-free (two scripts fetch); **reads technicals for prices, the one package besides core
  another declares**, and technicals reads it back by file only. A description, never a selector:
  tested 2018-26 with no edge.
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
- **packages/manage** — the daily position-management evaluation of the REAL account: a pure function
  of a broker snapshot (taken read-only by the orchestrator's `manage` verb) and a configurable ruleset,
  giving each trade an action, the rule behind it and a confidence, and reading the account as one
  book (beta-weighted delta and theta against bands, an XSP hedge sized when delta leaves its band,
  income candidates sized to the account). **Advice only** — no order path, no credentials, no
  network; read on the console's Live → positions and Live → portfolio tabs. Off until
  `manage.enabled`.
- **packages/advisor** — the deterministic half of the AI advisor: fact packs, validation of replies,
  and the paper A/B experiments. **It contains no AI** (the model is invoked by
  `scripts/advisor_checkpoint.py`). Read-only over every other package; its one output toward a loop is
  a bounded, expiring paper advice artifact (`cherrypick.core.advice`) applied to a synthetic
  `advised:<experiment name>` book. Off by default twice over: the suite must schedule it and each
  module must declare its own `advice` bounds.
- **packages/backtest** — the suite's own rules replayed over **licensed vendor history** (ThetaData).
  **The licence outranks everything:** vendor data and every result derived from it stay under
  `~/.cherrypick` and never enter this public repo, a PR, an issue or a published artifact; the data
  must stay purgeable (a marked directory the backup skips). The network is reached by the fetch only,
  the key lives in the keyring only, and a run is a pure function of the local store. No AI, no broker.
- **packages/desk** — ⚠️ **EXPERIMENTAL.** The suite's only *discretionary* live-order path (meic,
  earnings, flies and bwb each have a live loop behind their own gate: `enable_live_trading` for meic
  and earnings, flies' and bwb's own `live` gates and per-day arm). A
  foreground, human-initiated CLI, authorized entirely on its own (own config, own PIN, per-order
  ticket, own policy gates). Stores no broker secrets — it borrows a module's keyring session, because
  borrowing credentials is not borrowing permissions. No loop, no schedule, no ledger, never scheduled,
  and **no automated package may import it**.

`cherrypick.core` is **`packages/core`**, an editable dependency of every package. A user installs with
the root `install.cmd`/`install.ps1`/`install.sh` (a `.venv`, every package but desk, the console, the
supervisor); a developer runs `scripts/dev-install.ps1`/`.sh` (editable, with `[dev]` extras), or
`pip install -e packages/core` first by hand.
