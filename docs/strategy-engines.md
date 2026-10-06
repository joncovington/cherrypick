# Strategy engines

**What this covers:** a suite-level overview of what each trading strategy actually does, so you
can decide which one(s) fit your goals before diving into an individual module's own docs. The suite
ships eight strategy modules — MEIC, Earnings, Flies and BWB, which are on in a fresh install
(Earnings only where Dolt is set up), and Calendars, PMCC, Curve and Contango, which are
**EXPERIMENTAL and off by default** — plus the GEX engine the console renders. MEIC, Earnings and Flies get a full
section below; the others a short one. Each module's own docs (linked) are the source of truth for its
internals.

| Module | Structure | Default | Live path |
|---|---|---|---|
| MEIC | 0DTE iron condors, multiple entries | on | agent session behind `enable_live_trading` |
| Earnings | defined-risk earnings plays, held overnight | on with the `dolt` capability | `enable_live_trading` |
| Flies | 0DTE net-credit butterflies | on | per-day armed pilot |
| BWB | ~7 DTE SPX put broken-wing butterflies, laddered daily | on | per-day armed pilot |
| Calendars | weekly SPY double calendars | **off** (experimental) | none |
| PMCC | deep-ITM covered calls on TQQQ | **off** (experimental) | none |
| Curve | VXX call credit spreads, regime-gated | **off** (experimental) | none |

Every shipped example config declares only the `control` arm. MEIC, flies and PMCC start on control
alone, and any other arm named below is added in a machine's own config. BWB, calendars and curve build
their comparison books into the module and run them unless the config sets that book's `enabled` to
`false`.

## MEIC — 0DTE multiple-entry iron condors

**What it trades.** Iron condors on same-day-expiring (0DTE) cash/ETF index products — SPX, XSP, QQQ,
IWM, and similar — trading every symbol in its `symbols` list concurrently within one loop against one
shared account-wide risk budget. (Only a handful of major indices/ETFs list 0DTE chains; a hard stop
rejects any entry whose fetched chain isn't actually expiring today.)

**How it decides.** Each loop iteration runs an account-wide pass (connection, buying power, NLV
drawdown halt, VIX + VIX1D) then a per-symbol pass (IV rank, VIX-banded short delta, wing-width
selection, session classification, skew/price-action signals, regime detection, GEX regime check, ORB
range) followed immediately by that symbol's entry decision and execution. Entries clear a stack of
**hard stops** (time window, buying power, 0DTE expiration, strike overlap, call-delta ceiling, OTM
distance, concurrent-IC cap, IV-rank floor, credit floor, fee-adjusted credit floor, FOMC/quarterly-expiry
rules, regime gate, late-entry bias) before judgment is applied.

**How it exits.** MEIC has **no profit target**. A condor leaves only by a per-side software stop
(exchange multi-leg stops aren't supported for combos), a time/event force-close (FOMC 13:30,
triple-witching/quarterly 14:00), or — for cash-settled symbols — by expiring and settling in cash
(OTM shorts keep full credit; ITM shorts settle at intrinsic capped at the wing width). Physically-settled
symbols (QQQ/IWM) are force-closed before the bell to avoid assignment.

**Regime gates.** VIX pause, VIX1D/VIX ratio (event-day), per-symbol ATR%, and dealer gamma (GEX:
negative net GEX / zero-gamma proximity blocks or tightens). A complementary **ORB** (opening-range
breakout) debit spread runs alongside and *is* allowed in the trending regimes that pause condors.

**How it runs.** Unattended paper is a codified loop (`cherrypick.meic.paper_loop`), spawned once a
minute by the supervisor as the `meic-paper` job — the same decisions as the agent loop, in code,
writing to `~/.cherrypick/data/meic/paper_trades.db`. Live/interactive trading is agent-driven
(`/meic-start`) and gated behind `enable_live_trading`; the orchestrator never runs that path.

→ Depth: [`meic/docs/strategy.md`](../packages/meic/docs/strategy.md),
[`meic/GATES.md`](../packages/meic/GATES.md),
[`meic/CLAUDE.md`](../packages/meic/CLAUDE.md) (full config table + loop steps).

## Earnings — defined-risk earnings plays

**What it trades.** Six **defined-risk** strategies (max loss known at entry): `iron_fly`,
`double_calendar`, `iron_condor`, `atm_calendar`, `directional_credit_spread`, `broken_wing_butterfly`. Naked/undefined-risk strategies were deliberately removed — an unmonitored overnight naked
short can blow out arbitrarily. Positions open once before the close and close once after the next open,
unmonitored overnight.

**How it selects.** A strategy-agnostic scanner computes term structure, expected move, IV/RV ratio, and
a historical win-rate backtest live from tastytrade chains + DoltHub datasets (via a locally-running
`dolt sql-server`), applies hard-filter/liquidity gates, and ranks candidates. Each strategy contributes
only its own thresholds and strike/order construction. **Always check win-rate `sample_size`** — historical
coverage is limited for less-liquid names.

**How it runs.** The supervisor spawns the managed paper loop (`cherrypick.earnings.paper_loop once`)
every 60 seconds as the `earnings-paper` job; the phase comes from the clock — a pre-market forward
scan, marking through the morning, management in the execution window, a forced-sampling entry scan
from 15:35 ET, and the end-of-day write. The entry scan opens the isolated `strat_test` book (every
Tier 1/2 strategy on every viable name) so each strategy accumulates a statistically useful sample
fast. A separate `earnings-dolt` job keeps the local Dolt server up. **Needs the `dolt` capability**:
without the Dolt binary and the `earnings`/`options`/`stocks` clones, the module is switched off and
hidden.

→ Depth: [`earnings/docs/05-strategies.md`](../packages/earnings/docs/05-strategies.md),
[`earnings/docs/screening-criteria.md`](../packages/earnings/docs/screening-criteria.md),
[`earnings/docs/strategy-testing-plan.md`](../packages/earnings/docs/strategy-testing-plan.md),
[`earnings/CLAUDE.md`](../packages/earnings/CLAUDE.md).

## Flies — 0DTE net-credit butterflies

**What it trades.** A long butterfly spread pays out somewhere between $0 and its maximum width
at expiration and can never go negative — so a butterfly held for a **net credit** guarantees a
profit at worst. No one will simply sell you one for a credit, so this module manufactures the
credit itself: sell a defined-risk credit spread, then buy the spread that completes it into a
full butterfly for a smaller debit. What's left is a butterfly held for the difference, and that
difference is the position's guaranteed floor. If the completing leg never gets cheap enough, the
position stays an ordinary credit spread — tracked and reported as its own outcome, since it's
expected to be common.

**What it measures.** Whether that manufactured credit survives real trading costs. Parallel
"arms" each change one variable (which strike is the centre, what time of day trades happen) and
are compared against a plain `control` baseline, so a result can be attributed to a specific idea rather
than a bundle of confounded changes. The completion rate — how often a credit spread actually
turns into a full butterfly — is the single number the whole exercise turns on.

**How it runs.** Paper trading (simulated) by default: a resident loop in session (`flies-paper`,
15-second ticks) plus an off-session job that owns settlement. A small, explicitly-armed live pilot is
also supported: one arm, one symbol, a worst-case buying-power cap, armed fresh every trading day by
`/live-flies-start` and valid for that day only; while armed, the supervisor runs it as the
`flies-live` job.

→ Depth: [`flies/README.md`](../packages/flies/README.md),
[`flies/docs/live-trading-plan.md`](../packages/flies/docs/live-trading-plan.md).

## BWB — daily-laddered put broken-wing butterflies

One net-credit SPX put broken-wing butterfly a session at the expected move, ~7 DTE, held to expiry, so
several ride at once. Its books trade the identical base structure and differ only in whether and when
a reversal-triggered put credit spread add-on fires (never / delta touch / confirmed bounce / gamma-flip
reclaim). SPX cash-settles, so there is no assignment machinery. Paper by default (`bwb-paper`); since
2026-09-18 a narrow live path in the flies posture — one arm, armed per day by `/live-bwb-start`, a
worst-case margin cap, a cost-derived credit floor, no closing orders.

→ Depth: [`bwb/README.md`](../packages/bwb/README.md), [`bwb/CLAUDE.md`](../packages/bwb/CLAUDE.md).

## Calendars — weekly double calendars (EXPERIMENTAL, off by default)

Weekly SPY double calendars (SPX until 2026-08-15) entered each Monday at the expected-move strikes,
shorts expiring Friday and longs the following Monday. An exit-parameter experiment: `control` closes
at Friday's bell, `path` holds to expiry recording a mark path, and a read-side replay scores exit
policies over that path, validated to the cent against the real books. Both European cash and American
physical settlement are modelled; ex-dividend weeks are skipped. Paper-only and credential-free.

→ Depth: [`calendars/README.md`](../packages/calendars/README.md),
[`calendars/CLAUDE.md`](../packages/calendars/CLAUDE.md).

## PMCC — deep-ITM covered calls (EXPERIMENTAL, off by default)

On TQQQ: buy an 85-90-delta ~21 DTE call as a stock substitute, sell the ATM ~7 DTE call nearest spot,
hold to the short's expiry and close both legs together. Early assignment is measured, never modelled —
ex-dividend spans are refused and near-zero-extrinsic marks flagged — so the paper result is an explicit
upper bound. Paper-only and credential-free.

→ Depth: [`pmcc/README.md`](../packages/pmcc/README.md), [`pmcc/CLAUDE.md`](../packages/pmcc/CLAUDE.md).

## Curve — VXX call credit spreads (EXPERIMENTAL, off by default)

Harvests the VIX term-structure roll yield with VXX call credit spreads, gated by a daily VIX/VIX3M
regime read. Its books (`control`, `noflip`, `hook`) differ only in entry gate and exit rule, and the
daily regime classification is recorded every session, traded or not. Early assignment and VXX reverse
splits are measured, never modelled. Paper-only and credential-free.

→ Depth: [`curve/README.md`](../packages/curve/README.md), [`curve/CLAUDE.md`](../packages/curve/CLAUDE.md).

## Contango — the same signal, held in shares (EXPERIMENTAL, off by default)

Holds SVXY while VIX/VIX3M is in contango and SHV (T-bills) while it isn't, deciding once a session
ten minutes before the close. Its arms (`control` out at 0.97, `flipexit` out at 1.0) differ only in
thresholds. It exists because curve's option costs take about half of each credit. Replayed from
2018, the switch earned less than holding SVXY for about 20 points less drawdown, and the paper run
measures whether that trade holds after real switching costs. Paper-only and credential-free.

→ Depth: [`contango/README.md`](../packages/contango/README.md), [`contango/CLAUDE.md`](../packages/contango/CLAUDE.md).

## Variance testing with arms (the distinguishing capability)

An **arm** is one configured variant of a strategy, run as its own portfolio (MEIC's config and older
docs call it a **risk profile**; other modules a *book* or *profile*). It is a named set of parameters — short-strike delta, credit floor, stop policy, regime
gates, entry timing, wing width, symbol. Every profile trades the **same live market snapshots in
parallel** as its own shadow book, and every recorded trade is tagged with the profile that opened it, so
`report` breaks results down per profile.

The value is **controlled comparison**: clone a baseline, change *one* parameter, and measure that idea's
effect in isolation. Two tiers of profiles:

- **`control`** — the `active_profile` and the shared baseline, and the only arm MEIC's shipped
  registry declares. Without a naive baseline a profitable arm would prove nothing.
- **Your own arms** — each differing from `control` in exactly one thing (an uncapped entry count, a
  pinned wing width, a buying-power cap, a GEX gate, …). MEIC reads its arm registry from
  `$MEIC_RISK_CONFIG` if set, else `~/.cherrypick/config/meic.risk.json`, else the shipped control-only
  `packages/meic/config.risk.example.json`; arms are a machine's configuration, not the repo's.
- **The advisor's arms** — with the advisor enabled, its admitted proposals run as
  `advised:<experiment>` paper arms beside the control.

Names from earlier eras (the `conservative` → `very-aggressive` risk ladder, retired 2026-08-07; the
`large-spx-*` / `small-xsp` cells, removed 2026-07-18) still appear in historical reports; if you find
one named in an old note, that is what it was. Keep a retired arm in your registry with
`enabled: false` if you want its book to stay readable under its name.

Read outcomes two ways: **gross** P&L (did the entry select good setups?) vs **net** (did it survive
commissions and slippage?), and use `calibrate` to see when a profile has met a documented threshold
(enough sessions, sustained win rate, sufficient sample; the readings include the per-session Sharpe,
`session_sharpe`, and the Probabilistic Sharpe Ratio, `psr`) to justify a step up — advisory only.

→ Depth: [`meic/docs/risk-profiles.md`](../packages/meic/docs/risk-profiles.md),
[`meic/docs/paper-experiments.md`](../packages/meic/docs/paper-experiments.md),
[`earnings/docs/paper-trading.md`](../packages/earnings/docs/paper-trading.md).

## GEX — the gamma-exposure engine

A self-hosted **GEX (gamma-exposure)** engine — a lightweight take on what gexbot/SpotGamma/MenthorQ
sell — built on the shared `cherrypick.core.gex` engine (the *same* math MEIC's GEX regime gate uses).
The package computes and records; the console's GEX page renders it, off one live option chain:

net GEX by strike with open interest ("positioning") against traded volume ("flow"), the gamma-flip /
zero-gamma level, the call/put walls, a live spot marker and intraday spot trail, and the call/put IV
skew.

**How it runs.** It reads the shared stream cache the standalone `streamer` package writes (the
suite's single producer), declaring the symbols it needs through `state/stream_requests/`. The
always-on `gex-recorder` service (on by default) records the spot trail, the GEX history and the
market-regime series the morning overview reads. It never places orders. A standalone
`run.py stream` mode with its own cache remains for use outside the suite.

→ Depth: [`gex/README.md`](../packages/gex/README.md),
[`gex/CLAUDE.md`](../packages/gex/CLAUDE.md).

## Shared foundations

Every engine leans on `cherrypick.core`: `.fees` (the tastytrade commission/exchange/slippage model —
the same cost model across engines, so "net" figures are comparable), `.calendar` (NYSE holidays, FOMC,
quarterly/triple-witching, computed not hand-maintained), `.profiles` (attribution tagging + comparison),
`.gex`/`.streamer` (the shared GEX + DXLink data path), `.ledgers` (the per-schema net and risk rules
every read surface uses) and `.metrics` (the calibration bundle). See
[configuration-and-storage.md](configuration-and-storage.md) for how each engine's data is stored.
