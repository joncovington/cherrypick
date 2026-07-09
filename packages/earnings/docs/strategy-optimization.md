# Strategy Optimization Research — Overnight Earnings (Defined-Risk)

Research-backed optimization hypotheses for the seven remaining **defined-risk** strategies
(`iron_fly`, `iron_condor`, `double_calendar`, `atm_calendar`, `directional_credit_spread`,
`broken_wing_butterfly`, `reverse_fly`), specific to this system's structure: **one position
opened before the close, closed once after the next open, unmonitored overnight** — so the
entire edge is the single overnight IV-crush event, and strike/structure choices at entry
matter more than intraday management (there is none).

**These are hypotheses to validate, not changes to apply.** The right way to act on them is
the existing paper-testing program (`docs/strategy-testing-plan.md`): `strategy_test_runner`
already force-samples all 7 strategies nightly, and `strategy_metrics` already computes
cost-adjusted expectancy and captured IV crush per strategy. Change **one** parameter,
re-run the paper program, compare expectancy — do not blind-tune to the literature. The
research itself warns that "if all your backtest metrics are excellent, you probably have
overfitting."

Every current config value cited below is from `config/config.json` at time of writing.

---

## 0. The removal itself is the #1 research-backed improvement

The strongest, most consistent caution in the earnings-options literature is against
**undefined risk on single-name earnings events** — a naked short can gap arbitrarily.
Making the whole system defined-risk-only (max loss known at entry for every trade) directly
implements that. This is already done; everything below optimizes *within* defined risk.

---

## 1. Profit target for IV-crush plays

**Research:** for earnings IV-crush plays, take profits early — commonly **50-75% of max**
(vs. the ~50% standard for non-event condors), because IV crush deflates the position in one
move and holding past that only re-exposes you to gamma/gap risk.

**Current values:** `iron_fly` / `iron_condor` / `directional_credit_spread` / `reverse_fly`
= `0.50`; `atm_calendar` / `double_calendar` = `0.25`; `broken_wing_butterfly` = **`0.10`**.

**Hypotheses:**
- **BWB `0.10` looks far too low** — a 10% profit target on an earnings crush leaves most of
  the captured move on the table. Test raising toward 0.25-0.50.
- Calendars at `0.25` and credit spreads at `0.50` sit at/below the earnings 50-75% band —
  test upward sensitivity (e.g. 0.60-0.75 on the credit spreads).
- **Nuance specific to this system:** the overnight hold captures the crush in a single move
  and the close-window backstop force-exits regardless, so the profit target mostly governs
  the Step-3c *early-exit* check the next morning, not a mid-hold decision. Its practical
  effect is smaller here than in a multi-day condor — measure whether it moves expectancy at
  all before tuning it.

**Metric to watch:** per-strategy expectancy and win rate vs. target; cross-reference
`iv_crush` (captured vol points) to confirm the crush actually happened on winners.

---

## 2. Iron Fly (ATM) vs Iron Condor (OTM) on an unmonitored hold

**Research:** short-strike **delta** sets probability of profit — ~16-delta shorts ≈ 68% POP
(conservative), 30-40-delta for more premium. An **iron fly sells ATM** = maximum premium but
**lowest POP and highest pin/gamma risk**; an **iron condor sells OTM** = wider profit zone,
higher POP, smaller credit.

**Current:** `iron_fly` sells the ATM straddle (capped at `max_atm_delta_abs 0.57`);
`iron_condor` places shorts at the expected-move boundary.

**Hypothesis:** on an *unmonitored* overnight hold, the ATM fly's pin/gamma risk is the worst
profile (nobody adjusts if the stock lands near the short strike), so **iron_condor should
out-expectancy iron_fly** despite the smaller credit. The paper program already trades both
head-to-head every night — let the data rank them rather than assuming the fly's larger
credit wins.

---

## 3. Skew-based strike/wing adjustment for fly, butterfly, condor

**Research:** symmetric strikes are often suboptimal. Put-side skew makes downside strikes
**richer** (more premium) *and* makes their delta **overstate** real risk, so mirroring wings
around spot leaves edge on the table. The fix is **asymmetric (broken) wings / asymmetric
short-strike deltas that lean with the skew** — push/widen the richer (usually put) side.
Broken-wing structures are the tool research most recommends for skewed earnings names.

**Current state is split:**
- `broken_wing_butterfly` and `directional_credit_spread` **already consume skew**
  (`min_skew_abs 0.02`, `skew_delta_target 0.25`, asymmetric near/far wings, put-vs-call side
  chosen by the skew sign).
- `iron_fly` (symmetric ATM straddle + equal wings via `wing_width_credit_multiple`) and
  `iron_condor` (symmetric expected-move boundaries) **ignore skew entirely**.

The skew signal is already computed in the shared engine (`scanner`'s call/put ATM-IV skew,
consumed by BWB/directional today), so it can be reused with no new data path.

**Hypotheses:**
1. Add optional **skew-leaned wings** to `iron_fly` / `iron_condor` — widen or push the
   richer-skew wing (usually the put side) and/or offset the short strikes toward the skew —
   and compare cost-adjusted expectancy vs. today's symmetric build head-to-head.
2. **Validate BWB's existing skew logic**: confirm the side selection and `skew_delta_target`
   actually place the fat wing on the correct (richer-IV) side on real candidates.

If the paper data supports (1), the follow-up is a small skew-offset parameter on the two
symmetric strategies, mirroring how BWB/directional already do it — **doc/hypothesis only in
this pass, no strike-construction code changes yet.**

---

## 4. Calendar term-structure / DTE

**Research:** the calendar edge is front-month IV ≫ back-month IV (steep backwardation into
earnings), with the **front leg expiring ~2-3 days after the report** — enough to catch the
front-leg crush while the back leg keeps its value. The back leg also loses value in a crush,
so profit is the *net* differential, not just front-leg decay.

**Current:** `atm_calendar` / `double_calendar` use `back_month_min_days_after 21` and pick
the front expiration near the reaction date.

**Hypothesis:** confirm the selected **front** expiration lands a few days *after* earnings
(not a same-day 0DTE, not weeks out). The IV/RV + term-structure gate already encodes "front
IV elevated"; the open question is purely the front-leg DTE placement. Instrument it in the
paper `entry_context` and check the realized front-vs-back IV differential on winners.

---

## 5. Expected move > realized move (the core edge)

**Research:** prioritize names where the **expected (priced-in) move exceeds the historical
realized move** — options are overpriced, so the crush works in your favor. This is the
single most-cited edge for short-premium earnings trades.

**Current:** the agent already gates on **IV/RV ratio** (`min_iv_rv_ratio 1.25`) and
**realized-move dispersion** (`realized_move_dispersion_pct`) — a direct implementation of
"expected exceeds realized, consistently."

**Hypothesis:** this is the agent's primary edge and is already well-aligned with the
research — the lever is to **tighten rather than loosen** it. Test whether raising the IV/RV
floor (e.g. 1.25 → 1.35) improves per-trade expectancy at the cost of fewer trades.

---

## 6. Loss limit (stop)

**Research:** a common stop is **2-3× credit collected** ("don't hope and pray").

**Current:** `stop_loss_credit_multiple 1.5` on the credit strategies (tighter than the
research norm); calendars use `stop_loss_pct_of_debit 1.0`; `reverse_fly` stops at its defined
max loss.

**Hypothesis:** because the stop is only checked at the **open** (unmonitored overnight), a
tight 1.5× may cut positions that would have recovered intraday, while a looser 2-3× may just
book bigger gap losses. Test 1.5× vs 2.0× and measure the effect on the loss tail (avg loss,
max drawdown) — this is a where-does-it-actually-fire question the paper data answers.

---

## 7. Wing width (low priority)

**Research:** wing width is a risk/return trade-off (wider = lower % return but more buffer);
$5-10 on stocks is typical, and a credit-multiple approach is fine.

**Current:** credit-multiple wings (2.5-3.5× bands). Consistent with the research framing —
**low priority**, revisit only if a strategy underperforms after the higher-priority levers.

---

## Prioritized test queue

Evaluate in this order, one parameter at a time, only once a strategy clears the **30-trade
directional gate** (see `docs/strategy-testing-plan.md`):

1. **iron_fly vs iron_condor** head-to-head expectancy (§2) — no code change, just read the
   data the paper program already produces.
2. **BWB profit target** 0.10 → 0.25/0.50 (§1) — highest-suspicion single value.
3. **Skew logic**: validate BWB (§3.2), then prototype skew-leaned iron_condor (§3.1).
4. **IV/RV floor** 1.25 → 1.35 (§5) — tightening the core edge.
5. **Calendar front-leg DTE** placement (§4).
6. **Stop** 1.5× vs 2.0× (§6).
7. Wing width (§7) — only if needed.

## Protocol (do not blind-tune)

- Change exactly one parameter; keep every other value fixed.
- Re-run the paper program for a fresh window; compare **cost-adjusted expectancy** (not gross
  P&L, not the literature) between the old and new value.
- Require the change to hold up on ≥30 trades before believing it, ≥100 before trusting it.
- Generate tearsheets for **end-of-window evaluation**, never to fine-tune mid-window — that
  overfits to the specific test window.

## Sources

- Iron condor / iron fly earnings & IV crush, strike-by-delta, 50-75% earnings profit target:
  [apexvol.com](https://apexvol.com/strategies/iron-condor),
  [optionsamurai.com](https://optionsamurai.com/blog/iron-condor-earnings/),
  [projectoption.com](https://projectoption.com/learn/iron-condor-options-strategy),
  [schwab.com](https://www.schwab.com/learn/story/iron-condors-what-they-are-and-how-to-use-them)
- Calendar / double-calendar term structure & DTE spacing:
  [steadyoptions.com](https://steadyoptions.com/articles/calendar-spread/),
  [optionstradingiq.com](https://optionstradingiq.com/double-calendar-earnings-trade/),
  [menthorq.com](https://menthorq.com/guide/aces-earnings-calendar-strategy/)
- Skew / broken-wing / asymmetric strikes:
  [alpaca.markets](https://alpaca.markets/learn/iron-condor-vs-iron-butterfly),
  [datadrivenoptions.com](https://datadrivenoptions.com/strategies-for-option-trading/favorite-strategies/broken-wing-put-condor/),
  [optionsplaybook.com](https://www.optionsplaybook.com/option-strategies/broken-wing-butterfly-put)

## See also

- `docs/strategy-testing-plan.md` — the paper program that validates every hypothesis here
- `docs/05-strategies.md` — the 7 strategies' structures and current parameters
- `src/strategy_metrics.py` — expectancy / IV-crush / regime metrics the tests are judged on
