# Glossary

Suite-wide terms. For engine-specific vocabulary see
[`earnings/docs/14-glossary.md`](../packages/earnings/docs/14-glossary.md).

**0DTE** — Zero days to expiration. Options expiring the same trading day; MEIC trades these exclusively.

**ATR** — Average True Range. A realized-volatility measure; MEIC uses each symbol's 5-day ATR as a
fraction of price for a regime (trending) gate.

**Calibration** — The advisory `calibrate` reading that indicates when a risk profile has met a
documented threshold (enough sessions, sustained win rate, sufficient sample) to justify a step up. Never
changes settings automatically.

**Cash-settled** — Index products (SPX, XSP, NDX, RUT) that settle in cash at expiration rather than by
physical assignment. MEIC leaves OTM cash-settled shorts to expire; physically-settled ETFs (QQQ, IWM)
are force-closed before the bell.

**Defined-risk** — A position whose maximum loss is known at entry (both wings present). Every Earnings
strategy is defined-risk; naked strategies were removed.

**DXLink** — tastytrade's streaming market-data feed. The streamer maintains a WebSocket to it and caches
Quote/Greeks/Trade/Summary events in `stream_cache.db`.

**EOD analysis** — The deterministic, code-generated **7-section** conversational report each module
writes per session (`eod-analysis-<day>.md`) alongside the terse `paper-eod` metrics file.

**EOD digest** — The orchestrator's cross-module session roll-up (`eod-digest-<day>.md`) with a snapshot,
suite total, per-module table, and links to the module reports.

**EOD insight** — The **opt-in AI** synthesis (`eod-insight-<day>.md`) produced by Claude Code over the
day's deterministic reports. Off by default; off the reliability path.

**Expectancy** — Average net P&L per trade; a per-profile metric in the reports.

**GEX (gamma exposure)** — Dealer gamma positioning computed from open interest + greeks. Yields the
**gamma-flip / zero-gamma** level and the **call/put walls**. Used as a MEIC regime gate and shown in the
GEX dashboard.

**Gross vs. net** — Gross P&L is before commissions/slippage (does the *entry* select good setups?); net
is after costs (does it survive?). The reports show both; the gap matters at small size.

**IC (iron condor)** — A four-leg options structure (short put spread + short call spread) collecting
credit with defined risk. MEIC's core structure.

**IV crush** — The drop in implied volatility after an earnings release (`entry_iv − exit_iv`); the edge
Earnings plays harvest.

**IV rank** — Where current implied volatility sits within its recent range (0–1). MEIC's `min_iv_rank`
gate rejects entries when premium is too thin.

**IV/RV ratio** — Implied vol vs. realized vol at entry; an Earnings edge signal (>1 means options price
more move than the stock has realized).

**Managed home** — `~/.cherrypick`, the single per-user directory holding all runtime config, data, logs,
and state (relocatable with `$CHERRYPICK_HOME`).

**MEIC** — Multiple Entry Iron Condor. The 0DTE index-condor engine (`packages/meic`).

**ORB (opening-range breakout)** — A directional debit spread MEIC trades as a complement to condors; it
*is* allowed in the trending regimes that pause condor entries.

**Paper mode** — Simulation: live market data in, modeled fills + costs out, no real orders. The default
and what all automation runs.

**Risk profile** — A named parameter set (delta, credit floor, stop policy, gates, timing, wing, symbol)
run as its own shadow book for **variance testing**; every trade is tagged with the profile that opened
it.

**Section 1256** — U.S. tax treatment for broad-based cash-settled **index** options (SPX/XSP/NDX/RUT):
60/40 long/short mark-to-market, wash-sale exempt. Equity/ETF options (QQQ/IWM/single names) get ordinary
treatment. Surfaced *informationally* in the EOD analysis tax section — not tax advice.

**Slippage** — The modeled haircut off mid price applied to fills, on top of the real tastytrade fee
schedule, so reported "net" reflects realistic transaction costs.

**Streamer** — The daemon (`cherrypick.core.streamer`) that holds the DXLink WebSocket and writes the
stream cache; a stale streamer is the failure mode the watchdog guards against.

**VIX1D** — The 1-day volatility index; MEIC uses `vix1d / vix` as a same-day-specific event-day signal.

**Variance testing** — Running many one-parameter-apart risk profiles in parallel against the same market
snapshots to measure which entry rules add edge. The suite's distinguishing capability.

**Watchdog** — The scheduled reliability check that verifies data is flowing in-session, restarts a
stalled feed, and notifies on stalls — stdlib + OS shell only, no network/AI.
