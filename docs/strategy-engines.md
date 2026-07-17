# Strategy engines

The suite ships two trading engines plus a market-structure dashboard. This is the suite-level overview;
each engine's own docs (linked below) are the source of truth for its internals.

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

**How it runs.** Unattended paper is a codified daemon (`src/paper_loop.py`, self-healing OS task
`cherrypick-meic-paper-loop`) — the same decisions as the agent loop, in code, writing to
`~/.cherrypick/data/meic/paper_trades.db`. Live/interactive trading is agent-driven and gated behind
`enable_live_trading`; the orchestrator never runs that path.

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

**How it runs.** The orchestrator runs the forced-sampling paper harness `src/strategy_test_runner.py`
on daily entry (~15:45 ET) and exit (~09:45 ET) tasks it registers and watchdogs — this module has no
scheduler of its own. The harness opens the isolated `strat_test` book (every Tier 1/2 strategy on every
viable name) so each strategy accumulates a statistically useful sample fast; it's always paper-only.

→ Depth: [`earnings/docs/05-strategies.md`](../packages/earnings/docs/05-strategies.md),
[`earnings/docs/screening-criteria.md`](../packages/earnings/docs/screening-criteria.md),
[`earnings/docs/strategy-testing-plan.md`](../packages/earnings/docs/strategy-testing-plan.md),
[`earnings/CLAUDE.md`](../packages/earnings/CLAUDE.md).

## Risk-profile variance testing (the distinguishing capability)

A **risk profile** is a named set of parameters — short-strike delta, credit floor, stop policy, regime
gates, entry timing, wing width, symbol. Every profile trades the **same live market snapshots in
parallel** as its own shadow book, and every recorded trade is tagged with the profile that opened it, so
`report` breaks results down per profile.

The value is **controlled comparison**: clone a baseline, change *one* parameter, and measure that idea's
effect in isolation. Two tiers of profiles:

- **The risk ladder** — conservative → moderate → aggressive → very-aggressive: the everyday progression
  tiers (each a full partial-override preset).
- **Experiment cells** — `large-spx`, `small-xsp`, `…-holdtoexpiry`, `…-farotm`/`…-closeotm` (a delta
  sweep), `…-gexmag` (GEX-gated), `…-lateonly`/`…-trim`/`…-directional`, etc.: each pins one
  `(symbol, wing, credit)` cell or isolates one lever, so per-account-size / per-idea comparison reads
  directly off the report.

Read outcomes two ways: **gross** P&L (did the entry select good setups?) vs **net** (did it survive
commissions and slippage?), and use `calibrate` to see when a profile has met a documented threshold
(enough sessions, sustained win rate, sufficient sample) to justify a step up — advisory only.

→ Depth: [`meic/docs/risk-profiles.md`](../packages/meic/docs/risk-profiles.md),
[`meic/docs/paper-experiments.md`](../packages/meic/docs/paper-experiments.md),
[`earnings/docs/paper-trading-profiles.md`](../packages/earnings/docs/paper-trading-profiles.md).

## GEX — the gamma-exposure dashboard

A standalone, self-hosted **GEX (gamma-exposure)** dashboard — a lightweight take on what
gexbot/SpotGamma/MenthorQ sell — built on the shared `cherrypick.core.gex` engine (the *same* math MEIC's
GEX regime gate uses). Three tabs off one live option chain:

- **GEX** — net GEX by strike with open interest ("positioning") vs traded volume ("flow") side by side,
  the gamma-flip / zero-gamma level, the call/put walls, and a live spot marker + intraday spot trail.
- **IV Skew** — call vs put IV curve and open interest by strike.
- **Volume** — call/put/total traded volume by strike.

**Runs two ways:** *standalone* (`run.py stream` runs the shared streamer into its own
`data/stream_cache.db`) or *piggyback* (read a running MEIC streamer's cache read-only). It never places
orders. The orchestrator surfaces it as a compact live **section card** (`run.py section --json`, a
`cherrypick.core.viz` payload) and as a full **dashboard iframe embed**.

→ Depth: [`gex/README.md`](../packages/gex/README.md),
[`gex/CLAUDE.md`](../packages/gex/CLAUDE.md).

## Shared foundations

Both engines lean on `cherrypick.core`: `.fees` (the tastytrade commission/exchange/slippage model —
the same cost model across engines, so "net" figures are comparable), `.calendar` (NYSE holidays, FOMC,
quarterly/triple-witching, computed not hand-maintained), `.profiles` (attribution tagging + comparison),
and `.gex`/`.streamer` (the shared GEX + DXLink data path). See
[configuration-and-storage.md](configuration-and-storage.md) for how each engine's data is stored.
