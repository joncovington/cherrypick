# Changelog

The entries up to v0.9.0 are retrospective: the suite tagged no versions until then, so they were
reconstructed from commit history rather than written at release time. Versions mark
architectural boundaries (a new package, a scheduler cutover, a read-side or trading-mode change),
not commit counts. Per-package `pyproject.toml` versions remain at their `0.1.0` placeholder — this
file tracks the *suite*, not any one package. From v0.10.0 every version is a published GitHub
Release, cut from `main` when a batch of work is finished, and the install docs point at the latest
one; [docs/releasing.md](docs/releasing.md) has the procedure. `[Unreleased]` is what `main` holds
beyond the latest release.

## [Unreleased]

- **Fixed: the suite-wide account listing ran a bare argv.** `accounts._first_broker_module`
  called `cfgmod.broker_tool(mcfg)` without the module name, so for any module whose config block
  does not declare `broker_tool` itself (meic ships without one), `cherrypick account --set` and
  the Live Ops listing probed the login with `python list_accounts` — no module, no output, and
  "list_accounts not ok" on every such machine, while `account --module`, `connect` and
  `reconcile` (all of which pass the name) resolved the same tool fine. Now passes the name like
  every other call site; guarded by a regression test asserting the argv the probe hands the
  broker tool.
- **Hot options** in the morning pack (fact version 5). `scripts/fetch_market_files.py` lands OCC's
  daily option volume by underlying (one keyless CSV a session, reduced to call/put sides by account
  type) and Nasdaq Trader's symbol directory; `cherrypick.overview.occ` ranks the prior session the
  way the Options Insider's Hot Options Report does — VIX, SPY, SPX, IWM and QQQ, then the top ten
  single-name equities and top five funds — with call/put, the customer share of sides and volume
  against 20 prior sessions. Rendered on the console's Morning tab and in the markdown. Record-only.
- **One OCC fetch.** The stock universe's harvest no longer downloads OCC's file itself: it lands
  missing sessions through the same fetcher and reads contracts from the shared store. The two
  copies agreed on every underlying of all 13 sessions both held, and the universe builds
  identically from either. `universe/occ-volume/` is no longer read or written.
- **Releases.** v0.10.0 is published as the first GitHub Release, and README, INSTALL and
  QUICKSTART now install from the latest release instead of `main` (git users check out its tag;
  updating is a fetch and the same checkout). Pushing a `v*` tag on `main` publishes the next one
  with its changelog section as the notes (`.github/workflows/release.yml`, `tools/release_notes.py`).
  [docs/releasing.md](docs/releasing.md) has the procedure.
- **Charts page** (console). A live intraday futures chart — /ES, /NQ, /CL, /GC, /ZB in 1-, 5- or
  15-minute candles, extended hours, the bar in progress updating about once a second — over the
  console's own DXLink session, measured first with `scripts/probe_candles.py`. The technicals chart
  moved here from Reports (`/charts/technicals`; `/reports/chart` redirects). The console's feed now
  aggregates at 1s instead of the SDK's 10s.

## v0.10.0 — 2026-10-01 — the public release
The first release meant to be installed by someone other than its author: one command takes a fresh
clone to a running console, the experimental modules ship switched off, every example config
carries only its `control` arm, and a feature runs only where the machine can carry it. Around that,
the market report's own engines arrived as `packages/technicals`, and every trade surface now states
money one way.

- **Install.** `install.cmd`/`install.ps1`/`install.sh` (`c25d2cda`) check Python 3.11+ and Node 22+,
  take the disclaimer only on a typed YES, build the venv and console, and offer an optional Dolt
  setup; `uninstall.*` is the full stop. `DISCLAIMER.md` (`898cbd27`) and `QUICKSTART.md` (`9a7b0bb2`) are new,
  and the docs were reorganised for a reader arriving fresh.
- **Capabilities** (`6194577f`). `config.json` declares `capabilities: {claude, dolt}`, and
  `run.py capabilities [--detect --write]` resolves them; a module or feature missing one is never
  scheduled, and the console hides what is switched off or unequipped (`465fade5`). calendars, pmcc and curve
  carry an "experimental" chip.
- **The base install** (`21a59bb5`, `240d14d5`). calendars, pmcc and curve are EXPERIMENTAL and off
  by default; module examples ship only `control`; push notifications ship off (every channel list
  is `["log"]`; the console's own toasts need no setting; `3d6a3cfa`). MEIC's arm registry left the repo
  (`packages/meic/config.risk.json` is gone): it reads `$MEIC_RISK_CONFIG`, else
  `~/.cherrypick/config/meic.risk.json`, else the control-only `config.risk.example.json`.
- **Packaging.** Every package declares the MIT licence and `requires-python >=3.11`, the streamer
  declares its tastytrade dependency, and `.gitattributes` pins line endings (`0d25c564`).
- **technicals** (new, `51f5378d`): the market report's end-of-day store over the local Dolt clones,
  its splits and dividends reconciled, with the vendor's level grid, scan rules, trend scores and
  1-10 rank fitted and scored out of sample; level selection is unsolved and says so (`fa180372`).
  SPX bars come from the broker's daily candles (`803da580`).
- **The market report.** A vendor-edition collector (`e96ac202`) and a stock universe (`cece8060`)
  feed it; the morning pack gained market files, the pre-market tape, the SPX 25-delta risk reversal
  and next week's earnings (`51007148`, `0cf79b2b`); report and chart page sit on the console's
  Morning tab (`bf846a58`, `1af3c615`).
- **One money layout** (`f5ba7daf`): net P&L, signed cash flow and whole-position dollars on every
  surface, settlement fees recorded as their own column in every module (`7b3340fe`, `f33589db`).
- **One word for a variant: `arm`** (`8d819b14`), in code, ledgers, review and advisor; the old
  spellings resolve for good, and a moved config key is now loud (`2ac18928`).
- **flies.** The delta-arm rollout, six arms plus the ATM twins, landed at the 2026-09-21 boundary
  (`aa23d5db`); the callwall arm retired. The live pilot sizes by its buying-power cap alone
  (`69f74f3a`) and starts at 10:15, on a replay of control's era under the live margin cap.
- **MEIC.** A `live-shadow` paper arm measures the live configuration it had never run on paper, and
  a buying-power cap carries three arms trading control at $5k, $10k and $25k (`57945bbc`).
- **Regime cuts** (`988612c3`): a nightly per-arm x per-regime artifact for flies and MEIC, stamped
  with one-session weight, paired contrasts, sign changes and a multiplicity count; MEIC's GEX bucket
  is re-derived sign-first, and flies' carries a gate replay with `entry_windows` an advice bound.
- **Calibration** gained a per-session Sharpe and its Probabilistic Sharpe Ratio (`session_sharpe`,
  `psr`) beside the per-trade one (`48ce143a`).
- **Elsewhere.** bwb scores its add-on as its own trade (`23307da9`); earnings closes at 15:30 on
  expiry day (`b58ddf27`); live loops skip quarter-end sessions (`93c6bd91`); a nightly suite backup
  (`d4329271`); CI runs every package's tests and shows each guard can fail (`dd3d6a93`); the
  console moved onto a module frame (`4e5c6c21`) with a System page (`6367ca04`); CLAUDE.md files
  consolidated (`f1bf8ecd`).
- **Not recorded before.** review (`92cc84ab`) and the console's desktop window (`6d0b2e02`) date
  from 2026-08-12. desk (`f19012d2`) is an EXPERIMENTAL prototype, not installed or enabled by
  default: it needs `enabled` and `CHERRYPICK_DESK_EXPERIMENTAL=1`, under low caps.

## v0.9.0 — 2026-09-18 — bwb's live path
`a6fd2bde` wired bwb's narrow live pilot into the guards, the supervisor, and the arm command — the
first new live-trading path added to the suite since MEIC and flies. One configurable arm, armed
per day, under a worst-case margin cap and a cost-derived credit floor; every fill is still the
broker's word.

## v0.8.0 — 2026-08-23 — the paper-only module wave
`22ec497d` added bwb and curve, completing a run of credential-free, stream-cache-only modules
started with calendars (2026-08-14), pmcc and overview (2026-08-16): each a forward experiment
against the shared streamer rather than a strategy with its own broker session.

## v0.7.0 — 2026-08-20 — the advisor era
`d81ac97b` opened "one experiment mechanism" — the deterministic advisor fact-packs and the
`advised:<experiment>` paper A/B books — and retired the module-specific champions/challengers
machinery (`1c7e52bd`) in its favor.

## v0.6.0 — 2026-08-12 — console becomes the one read surface
`6b3732ab` deleted the scout package after its watchlist/screener/builder/payoff surfaces were
ported into the new console (`packages/console`, first commit 2026-08-09); `de352a13` retired every
other read surface except it the same day.

## v0.5.0 — 2026-08-09 — the supervisor cutover
`a0a4b9d4` replaced the per-job Windows Task Scheduler entries with one supervisor daemon
(`run.py install`) — the orchestrator's single always-on process model that every module's resident
loop and the console now run under.

## v0.4.0 — 2026-07-30 — the settings surface
`9f2aa3a` added `cherrypick settings`, a loopback config editor and secrets manager — the suite's
one mutating HTTP surface, with guarded live-trading fields refused on both write paths.

## v0.3.0 — 2026-07-11 — the monorepo
`75b8539a` restructured the suite from sibling repos (MEICAgent, EarningsAgent, cherrypick-core as a
submodule) into one monorepo under `packages/`. `packages/core` landed the same window as an
ordinary editable dependency, and gex (07-11) and streamer (07-21) arrived as the first modules
built monorepo-native rather than migrated in.

## v0.2.0 — 2026-07-06 — Earnings joins
First commit of the earnings-play module (`e487db35`, "Scaffold EarningsFlyAgent project") as a
sibling to MEIC, still in the pre-monorepo, per-repo era.

## v0.1.0 — 2026-06-18 — origin
`c8e0fe73`, "Initial commit — MEICAgent MEIC trading agent." The suite's origin: a standalone
0DTE multiple-entry iron-condor agent, before any orchestrator, shared core, or sibling module
existed.
