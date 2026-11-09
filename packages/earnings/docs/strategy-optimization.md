# Strategy Optimization Hypotheses — Overnight Earnings (Defined-Risk)

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

Optimization hypotheses for the **defined-risk** strategies, written around this system's structure: one
position opened before the close, held through the report, and managed from the next morning. The edge
is the overnight IV-crush event, so strike and structure choices at entry matter more than intraday
management. Credits for the written sources behind these ideas are in
[`docs/strategy-sources.md`](../../../docs/strategy-sources.md).

**These are hypotheses to validate, not changes to apply.** Act on them through the forced-sampling paper
program ([strategy-testing-plan.md](./strategy-testing-plan.md)): `strat_test_harness` force-samples
every strategy, and `strategy_metrics` computes cost-adjusted expectancy and captured IV crush per
strategy. Change **one** parameter, re-run the program, compare expectancy; do not blind-tune to the
literature, which itself warns that "if all your backtest metrics are excellent, you probably have
overfitting."

The shipped thresholds are in [10-exits.md](./10-exits.md#thresholds-per-strategy) and
`config/config.example.json`; this page does not restate them.

---

## 0. Defined risk is the first optimization

The strongest, most consistent caution in the earnings-options literature is against **undefined risk on
single-name earnings events**: a naked short can gap arbitrarily. Making the whole system defined-risk
only implements that. Everything below optimizes *within* defined risk.

---

## 1. Profit target for IV-crush plays

**Research:** for earnings IV-crush plays, take profits early, commonly **50-75% of max** (against the
~50% standard for non-event condors), because the crush deflates the position in one move and holding
past that only re-exposes you to gamma and gap risk.

**Hypothesis:** the credit structures' targets sit at or below that band, so test upward sensitivity
(for example 0.60-0.75 on the credit spreads). The target mostly governs the first-morning decision, so
measure whether it moves expectancy at all before tuning it.

**Metric to watch:** per-strategy expectancy and win rate against target; cross-reference `iv_crush`
(captured vol points) to confirm the crush happened on winners.

---

## 2. Iron fly (ATM) vs iron condor (OTM)

**Research:** short-strike **delta** sets probability of profit: ~16-delta shorts are about 68% POP
(conservative), 30-40-delta earn more premium. An **iron fly sells ATM**, which is maximum premium but
the **lowest POP and highest pin/gamma risk**; an **iron condor sells OTM**, with a wider profit zone,
higher POP and a smaller credit.

**Hypothesis:** with nobody adjusting a position overnight, the ATM fly's pin/gamma risk is the worst
profile, so **iron_condor should out-expectancy iron_fly** despite the smaller credit. The paper program
trades both head-to-head every night; let the data rank them.

---

## 3. Skew-based strike/wing adjustment

**Research:** symmetric strikes are often suboptimal. Put-side skew makes downside strikes **richer**
*and* makes their delta **overstate** real risk, so mirroring wings around spot leaves edge on the table.
The fix is **asymmetric (broken) wings or short-strike deltas that lean with the skew**.

**Current state is split:** `broken_wing_butterfly` and `directional_credit_spread` consume skew
(`min_skew_abs`, `skew_delta_target`, asymmetric wings, side chosen by the skew sign). `iron_fly` and
`iron_condor` ignore it. The skew signal is already computed in the shared engine, so it can be reused
with no new data path.

**Hypotheses:**
1. Add optional **skew-leaned wings** to `iron_fly` / `iron_condor` and compare cost-adjusted expectancy
   against today's symmetric build head-to-head.
2. **Validate BWB's existing skew logic**: confirm the side selection and `skew_delta_target` place the
   fat wing on the richer-IV side on real candidates.

---

## 4. Calendar term structure / DTE

**Research:** the calendar edge is front-month IV much above back-month IV, with the **front leg
expiring about 2-3 days after the report**: enough to catch the front-leg crush while the back leg keeps
its value. The back leg also loses value in a crush, so profit is the *net* differential.

**Hypothesis:** confirm the selected front expiration lands a few days *after* earnings (not a same-day
0DTE, not weeks out), by instrumenting it in the paper `entry_context` and checking the realised
front-versus-back IV differential on winners. The calendars are retired from the harness; the
time-spread work is in [time-spread-screen-plan.md](./time-spread-screen-plan.md).

---

## 5. Expected move above realised move (the core edge)

**Research:** prioritise names where the **expected (priced-in) move exceeds the historical realised
move**: options are overpriced, so the crush works in your favour. It is the most-cited edge for
short-premium earnings trades.

**Current:** the screen gates on **IV/RV ratio** (`min_iv_rv_ratio`) and **realised-move dispersion**, a
direct implementation of "expected exceeds realised, consistently."

**Hypothesis:** this is the primary edge and is well aligned with the research, so the lever is to
**tighten rather than loosen** it. Test whether raising the IV/RV floor (for example 1.25 to 1.35)
improves per-trade expectancy at the cost of fewer trades. The `symbol_screen` levels and the entry
replay ([screening-criteria.md](./screening-criteria.md#replaying-a-screen)) are the tools.

---

## 6. Loss limit (stop)

**Research:** a common stop is **2-3× credit collected**.

**Hypothesis:** a tight stop may cut positions that would have recovered intraday, while a looser one may
only book bigger gap losses. Test 1.5× against 2.0× and measure the effect on the loss tail (average
loss, max drawdown).

---

## 7. Wing width (low priority)

**Research:** wing width is a risk/return trade-off (wider means lower % return but more buffer); $5-10
on stocks is typical and a credit-multiple approach is fine. The system uses credit-multiple wings, so
revisit only if a strategy underperforms after the higher-priority levers.

---

## 8. ATM bid-ask spread gate (`max_bid_ask_spread_pct`)

A liquid, otherwise-clean name can be hard-rejected across every strategy for `bid_ask_spread_too_wide`
by a point or two against the cap. One miss on one name does not distinguish "threshold too tight" from
"this stock's spread genuinely widened that night". The cap (`scanner.apply_liquidity_gates`) is a
reasoned starting point, not backtested.

**Test:** query `scan_log` (`arm LIKE 'strat_test%'`) for how often the reason appears, how close the
misses run (median and maximum overage), and whether it disproportionately hits otherwise-qualifying
liquid names rather than thin ones. A frequent narrow-miss reason on liquid names is the signal to test
loosening it; a rare one, or one hitting thin names, is the signal to leave it. Loosening is not free:
the slippage haircut in `costs.py` scales with spread width, so admitting wider-spread names also worsens
cost-adjusted expectancy on those trades.

---

## Prioritised test queue

Evaluate in this order, one parameter at a time, only once a strategy clears the **30-trade
directional gate**:

1. **iron_fly vs iron_condor** head-to-head expectancy (§2): no code change, just read the data.
2. **Skew logic**: validate BWB (§3.2), then prototype a skew-leaned iron_condor (§3.1).
3. **IV/RV floor** (§5): tightening the core edge.
4. **Calendar front-leg DTE** placement (§4).
5. **Stop** 1.5× against 2.0× (§6).
6. Wing width (§7), only if needed.
7. **Bid-ask spread gate** (§8) once `scan_log` shows whether the reason is a frequent narrow miss.

## Protocol (do not blind-tune)

- Change exactly one parameter; keep every other value fixed.
- Re-run the paper program for a fresh window; compare **cost-adjusted expectancy** (not gross P&L, not
  the literature) between the old and new value.
- Require the change to hold up on at least 30 trades before believing it, at least 100 before trusting
  it.
- Generate tearsheets for **end-of-window evaluation**, never to fine-tune mid-window: that overfits to
  the specific test window.
- A change to a threshold is a measurement boundary: see
  [measurement boundaries](./measurement-boundaries.md).

## See also

- [strategy-testing-plan.md](./strategy-testing-plan.md): the paper program that validates every
  hypothesis here
- [05-strategies.md](./05-strategies.md): every strategy's structure and current parameters
- `cherrypick/earnings/strategy_metrics.py`: the expectancy, IV-crush and regime metrics the tests are
  judged on
