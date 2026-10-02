# Changelog

The entries up to v0.9.0 are retrospective: the suite tagged no versions until then, so they were
reconstructed from commit history rather than written at release time. Versions mark
architectural boundaries (a new package, a scheduler cutover, a read-side or trading-mode change),
not commit counts. Per-package `pyproject.toml` versions remain at their `0.1.0` placeholder — this
file tracks the *suite*, not any one package.

## [Unreleased]

## v0.10.0 — 2026-10-01 — the public release
The first release meant to be installed by someone other than its author: one command takes a fresh
clone to a running console, the experimental modules ship switched off, every example config
carries only its `control` arm, and a feature runs only where the machine can carry it. Around that,
the market report's own engines arrived as `packages/technicals`, and every trade surface now states
money one way.

- **Install.** `install.cmd`/`install.ps1`/`install.sh` (`6716a87c`) check Python 3.11+ and Node 22+,
  take the disclaimer only on a typed YES, build the venv and console, and offer an optional Dolt
  setup; `uninstall.*` is the full stop. `DISCLAIMER.md` (`11f92ae3`) and `QUICKSTART.md` (`25f75d61`) are new,
  and the docs were reorganised for a reader arriving fresh.
- **Capabilities** (`626d9dac`). `config.json` declares `capabilities: {claude, dolt}`, and
  `run.py capabilities [--detect --write]` resolves them; a module or feature missing one is never
  scheduled, and the console hides what is switched off or unequipped (`0a00aa6c`). calendars, pmcc and curve
  carry an "experimental" chip.
- **The base install** (`232525bb`, `7cf5565c`). calendars, pmcc and curve are EXPERIMENTAL and off
  by default; module examples ship only `control`; push notifications ship off (every channel list
  is `["log"]`; the console's own toasts need no setting; `e37676ff`). MEIC's arm registry left the repo
  (`packages/meic/config.risk.json` is gone): it reads `$MEIC_RISK_CONFIG`, else
  `~/.cherrypick/config/meic.risk.json`, else the control-only `config.risk.example.json`.
- **Packaging.** Every package declares the MIT licence and `requires-python >=3.11`, the streamer
  declares its tastytrade dependency, and `.gitattributes` pins line endings (`2db1037e`).
- **technicals** (new, `be6f316f`): the market report's end-of-day store over the local Dolt clones,
  its splits and dividends reconciled, with the vendor's level grid, scan rules, trend scores and
  1-10 rank fitted and scored out of sample; level selection is unsolved and says so (`e4d2cbc1`).
  SPX bars come from the broker's daily candles (`3e13ac2e`).
- **The market report.** A vendor-edition collector (`bc0f81ab`) and a stock universe (`9909bc4c`)
  feed it; the morning pack gained market files, the pre-market tape, the SPX 25-delta risk reversal
  and next week's earnings (`f04c9147`, `397b55f7`); report and chart page sit on the console's
  Morning tab (`8c9a30e3`, `579bf2f0`).
- **One money layout** (`efd40793`): net P&L, signed cash flow and whole-position dollars on every
  surface, settlement fees recorded as their own column in every module (`aef9f1c7`, `23e9c52c`).
- **One word for a variant: `arm`** (`b26120ae`), in code, ledgers, review and advisor; the old
  spellings resolve for good, and a moved config key is now loud (`e5736ade`).
- **flies.** The delta-arm rollout, six arms plus the ATM twins, landed at the 2026-09-21 boundary
  (`3c6a7681`); the callwall arm retired. The live pilot sizes by its buying-power cap alone
  (`a0a820d4`) and starts at 10:15, on a replay of control's era under the live margin cap.
- **MEIC.** A `live-shadow` paper arm measures the live configuration it had never run on paper, and
  a buying-power cap carries three arms trading control at $5k, $10k and $25k (`f88a226b`).
- **Regime cuts** (`bb8017ca`): a nightly per-arm x per-regime artifact for flies and MEIC, stamped
  with one-session weight, paired contrasts, sign changes and a multiplicity count; MEIC's GEX bucket
  is re-derived sign-first, and flies' carries a gate replay with `entry_windows` an advice bound.
- **Calibration** gained a per-session Sharpe and its Probabilistic Sharpe Ratio (`session_sharpe`,
  `psr`) beside the per-trade one (`361fef23`).
- **Elsewhere.** bwb scores its add-on as its own trade (`b859e930`); earnings closes at 15:30 on
  expiry day (`d0492417`); live loops skip quarter-end sessions (`5b6ce45e`); a nightly suite backup
  (`24a51f3a`); CI runs every package's tests and shows each guard can fail (`07f82fb4`); the
  console moved onto a module frame (`8d12e5f3`) with a System page (`3e07c782`); CLAUDE.md files
  consolidated (`be365b39`).
- **Not recorded before.** review (`d0cd1c14`) and the console's desktop window (`040807b1`) date
  from 2026-08-12. desk (`0d4821a4`) is an EXPERIMENTAL prototype, not installed or enabled by
  default: it needs `enabled` and `CHERRYPICK_DESK_EXPERIMENTAL=1`, under low caps.

## v0.9.0 — 2026-09-18 — bwb's live path
`58ebad48` wired bwb's narrow live pilot into the guards, the supervisor, and the arm command — the
first new live-trading path added to the suite since MEIC and flies. One configurable arm, armed
per day, under a worst-case margin cap and a cost-derived credit floor; every fill is still the
broker's word.

## v0.8.0 — 2026-08-23 — the paper-only module wave
`59502eb6` added bwb and curve, completing a run of credential-free, stream-cache-only modules
started with calendars (2026-08-14), pmcc and overview (2026-08-16): each a forward experiment
against the shared streamer rather than a strategy with its own broker session.

## v0.7.0 — 2026-08-20 — the advisor era
`b79c4677` opened "one experiment mechanism" — the deterministic advisor fact-packs and the
`advised:<experiment>` paper A/B books — and retired the module-specific champions/challengers
machinery (`68cb6e6d`) in its favor.

## v0.6.0 — 2026-08-12 — console becomes the one read surface
`cc3861ee` deleted the scout package after its watchlist/screener/builder/payoff surfaces were
ported into the new console (`packages/console`, first commit 2026-08-09); `357a3ec3` retired every
other read surface except it the same day.

## v0.5.0 — 2026-08-09 — the supervisor cutover
`b63c83f9` replaced the per-job Windows Task Scheduler entries with one supervisor daemon
(`run.py install`) — the orchestrator's single always-on process model that every module's resident
loop and the console now run under.

## v0.4.0 — 2026-07-30 — the settings surface
`614e1a4` added `cherrypick settings`, a loopback config editor and secrets manager — the suite's
one mutating HTTP surface, with guarded live-trading fields refused on both write paths.

## v0.3.0 — 2026-07-11 — the monorepo
`a4214b9f` restructured the suite from sibling repos (MEICAgent, EarningsAgent, cherrypick-core as a
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
