"""Strategy-card math and human-readable strategy text -- annualized return, probability of
worthless, model (Black-Scholes) greeks, a plain-language strategy explanation, the short-put
"consider selling..." suggestion, and a pass/warn/fail checklist. Pure + stdlib like the rest of
``analytics/``.

The annualized-return formula was reverse-engineered from a reference platform's own displayed
pairs and verified against two independent examples before being written down: raw return =
credit / max_risk, annualized = (1 + raw) ** (365 / dte) - 1 (COMPOUNDED, not linear -- a linear
raw * 365/dte reproduces neither example). $150 credit / $900 risk / 25 DTE -> 16.67% raw ->
849.3% annualized; $113 / $987 / 25 -> 11.45% -> 386.7%. Both match the reference display to
rounding. The compounding assumption (that the same trade could be repeated back-to-back all year
at the same return) is optimistic by construction -- the number is a comparison metric, not a
forecast, and the UI should keep the asterisk.

Model greeks are Black-Scholes analytics computed from strike/spot/IV/T/r. Scout DOES have a live
greeks source (`chain_service.get_greeks`, DXLink `Greeks` events -- see the package CLAUDE.md);
these model greeks are the clearly-labeled FALLBACK for legs that arrived without live values,
because a labeled model greek beats a silently absent one. They assume one flat IV across legs.

Checklist thresholds are calibrated against observed reference-platform gradings (2026-08-03
screenshots, five cards spanning the full range), not published numbers:
  - POW: 53.54% graded red ("very low"), 55.55/58.28/65.66% yellow ("lower than optimal"),
    75.69% and 81.39% green ("very good") -> warn >= 55%, pass >= 70%. The pass bound is bracketed
    to (65.66, 75.69]; 70 is used (the round number consistent with the platform's 15-20-delta
    conservative guidance), with 75 not yet excluded -- one observed card in the 67-74% band
    would settle it.
  - Annualized return: 6.30% already graded green ("very good") -> pass >= 5% (roughly
    beats-risk-free), warn >= 2%. No yellow/red example has been observed; the fail zone is
    extrapolated.
  - Spread: 1.0% of mid green, 7.5% yellow ("sizable"), 19.7%+ red ("very large") -> pass <= 5%,
    warn <= 15% -- exactly the bands guessed from the earnings module's liquidity gate, now
    empirically confirmed.
  - Earnings inside the expiration warns; "no earnings before expiration" passes (observed).

The covered-call card additionally shows a "12M Projected Yield" distinct from the annualized
option return -- reverse-engineered from a KWEB covered-call card (2026-08-03): option annualized
22.93% + trailing dividend yield 7.36% = 30.29%, matching the displayed projected yield exactly.
It's simple addition, not compounded -- the option side is already its own compounding
assumption, and the dividend side is a trailing yield, not a forecast either; stacking two
already-labeled estimates needs no further modeling. `dividend_yield` from
`metrics_service`/`MarketMetricInfo` arrives as a plain fraction already (KWEB live-verified at
0.0736 == 7.36%), unlike `iv_30d`/`hv_30d` which need the /100 -- confirmed against the real
account the same day, not assumed from the SDK's naming convention. A second reference card (USO,
same day) reconfirms the annualized-return formula at a third independent (credit, max_risk, dte)
and shows the zero-dividend edge case: a commodity ETF's 0% trailing yield makes the projected
yield identical to the annualized option return (16.48% both), not a formula difference.

The reference platform's "Score" badge (an overall-attractiveness number on roughly a 0-250+
scale) reduces, for DEFINED-RISK spreads, to a strikingly simple closed form fit from six
independent data points across two underlyings/days (APD 2026-08-04, three same-underlying/
same-expiration verticals varying only strike width; plus KWEB/HYG-day put and call verticals
from 2026-08-03) via least-squares regression, R^2 = 0.9997:
    score = 100 * pop * (max_reward + max_risk) / max_risk
          = 100 * pop * (1 + max_reward / max_risk)
Equivalently, 100 * pop is the score of a risk-free (reward == 0) trade, and every dollar of
reward relative to risk scales it up proportionally. All six fitted points landed within ~1
point of the regression line on a scale spanning 84-144. It does NOT hold for a naked long
option (a seventh point, reward/risk 10.5, predicted ~450 against an actual 99) -- an
undefined-both-sides position's theoretical max reward (near stock-to-zero) makes the ratio
huge and unusable; scout does not attempt a score for anything but a two-strikes-both-sides
spread with finite max_risk. It also does not hold for undefined-risk baskets (short straddle/
strangle scored 152-250 despite reward/risk collapsing to ~0 when risk is "Unlimited") -- those
almost certainly use the platform's real margin requirement as the risk denominator, a dollar
figure this package has no visibility into; `score()` returns None rather than guess at it.
"""

from __future__ import annotations

import math
from datetime import date

from .payoff import Leg, breakevens, max_loss, max_profit, payoff_at
from .pop import norm_cdf, prob_below

DAYS_PER_YEAR = 365.0


def raw_return(credit: float, max_risk: float) -> float | None:
    if not max_risk or max_risk <= 0 or credit is None:
        return None
    return credit / max_risk

def annualized_return(credit: float, max_risk: float, dte: float) -> float | None:
    """Compounded annualization of credit/max_risk over the trade's own holding period."""
    raw = raw_return(credit, max_risk)
    if raw is None or raw <= -1 or not dte or dte <= 0:
        return None
    return (1.0 + raw) ** (DAYS_PER_YEAR / dte) - 1.0


def projected_yield_12m(annualized: float | None, dividend_yield: float | None) -> float | None:
    """Covered-call "12M Projected Yield": the annualized option return plus the position's
    trailing dividend yield (simple addition -- see module docstring for the KWEB evidence).
    None when either side is unknown, since a partial total would understate the real figure
    without saying so."""
    if annualized is None or dividend_yield is None:
        return None
    return annualized + dividend_yield


def score(pop_value: float | None, legs: list[Leg], max_reward: dict, max_loss: dict) -> float | None:
    """The reference platform's "Score" badge, for a DEFINED-RISK spread only (see module
    docstring for the six-point fit and why naked long options and unbounded-risk baskets are
    excluded): 100 * pop * (max_reward + max_risk) / max_risk. Requires >= 2 option legs (a
    naked single option is the one shape the fit is known to fail) and a finite max_risk/
    max_reward; returns None otherwise -- an inapplicable score should be absent, not wrong."""
    option_legs = [lg for lg in legs if lg.kind != "stock"]
    if len(option_legs) < 2 or pop_value is None:
        return None
    if max_loss.get("unbounded") or max_reward.get("unbounded"):
        return None
    risk_value, reward_value = max_loss.get("value"), max_reward.get("value")
    if risk_value is None or reward_value is None:
        return None
    max_risk = abs(risk_value)
    if max_risk <= 0:
        return None
    return 100.0 * pop_value * (reward_value + max_risk) / max_risk


def prob_worthless(legs: list[Leg], spot: float, sigma: float, t: float, r: float) -> float | None:
    """P(every SHORT option in the basket expires worthless) -- the premium-seller's "POW".
    For short puts that's P(S above the highest short-put strike); short calls, P(S below the
    lowest short-call strike); both, the interval probability. None when there is no short option
    (the metric doesn't apply to a pure debit position)."""
    short_put_strikes = [lg.strike for lg in legs if lg.kind == "put" and lg.quantity < 0 and lg.strike]
    short_call_strikes = [lg.strike for lg in legs if lg.kind == "call" and lg.quantity < 0 and lg.strike]
    if not short_put_strikes and not short_call_strikes:
        return None
    lo = max(short_put_strikes) if short_put_strikes else None
    hi = min(short_call_strikes) if short_call_strikes else None
    p_hi = prob_below(spot, hi, sigma, t, r) if hi is not None else 1.0
    p_lo = prob_below(spot, lo, sigma, t, r) if lo is not None else 0.0
    return max(0.0, p_hi - p_lo)


def _d1(spot: float, strike: float, sigma: float, t: float, r: float) -> float | None:
    if spot <= 0 or strike <= 0 or sigma <= 0 or t <= 0:
        return None
    return (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(legs: list[Leg], spot: float, sigma: float, t: float, r: float) -> dict:
    """Position-level model greeks (per the whole basket, 100 shares/contract): delta and gamma in
    $ P/L per $1 underlying move (and per $1 of delta change), theta in $ per DAY, vega in $ per
    one percentage point of IV. A leg without a strike (stock) contributes delta only."""
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    any_option = False
    for leg in legs:
        mult = leg.quantity * 100
        if leg.kind == "stock":
            totals["delta"] += 1.0 * mult
            continue
        d1 = _d1(spot, leg.strike or 0.0, sigma, t, r)
        if d1 is None:
            continue
        any_option = True
        d2 = d1 - sigma * math.sqrt(t)
        delta = norm_cdf(d1) if leg.kind == "call" else norm_cdf(d1) - 1.0
        gamma = _phi(d1) / (spot * sigma * math.sqrt(t))
        theta_year = -(spot * _phi(d1) * sigma) / (2.0 * math.sqrt(t))
        if leg.kind == "call":
            theta_year -= r * (leg.strike or 0.0) * math.exp(-r * t) * norm_cdf(d2)
        else:
            theta_year += r * (leg.strike or 0.0) * math.exp(-r * t) * norm_cdf(-d2)
        vega = spot * _phi(d1) * math.sqrt(t) / 100.0
        totals["delta"] += delta * mult
        totals["gamma"] += gamma * mult
        totals["theta"] += (theta_year / DAYS_PER_YEAR) * mult
        totals["vega"] += vega * mult
    if not any_option and totals["delta"] == 0.0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}
    return {k: round(v, 2) for k, v in totals.items()}


def direction(legs: list[Leg], spot: float) -> str:
    """"bullish"/"bearish"/"neutral" from the payoff engine's own numbers: which tail the position
    prefers. Probes at +/-40% -- wide enough to reach past the strikes of a normal OTM structure.
    (A first draft probed +/-10%, which landed BOTH probes inside an OTM put spread's max-profit
    plateau and called the spread "neutral" -- caught live when a bullish vertical's market-trend
    checklist row warned instead of passing against a bullish S&P read.)"""
    up = payoff_at(legs, spot * 1.40)
    down = payoff_at(legs, spot * 0.60)
    if up > down:
        return "bullish"
    if down > up:
        return "bearish"
    return "neutral"


_direction = direction  # internal alias kept for existing callers


def combo_spread_pct(quoted_legs: list[dict]) -> float | None:
    """Bid/ask spread of the NET strategy price as a fraction of its mid -- what the reference
    platform's Spread & Liquidity row actually grades (per the observed CSX card: combo bid $0.00 /
    ask $1.30, not per-leg widths). Each leg: {"quantity" (signed), "bid", "ask"}. Conservative
    fill = sell legs at bid / buy at ask; generous = the reverse; spread = the gap between them.
    None when any leg lacks a two-sided quote -- an ungraded spread must warn, not pass."""
    conservative = 0.0
    generous = 0.0
    for leg in quoted_legs:
        qty, bid, ask = leg.get("quantity"), leg.get("bid"), leg.get("ask")
        if qty is None or bid is None or ask is None:
            return None
        if qty < 0:  # short: collect bid conservatively, ask generously
            conservative += -qty * bid
            generous += -qty * ask
        else:  # long: pay ask conservatively, bid generously
            conservative -= qty * ask
            generous -= qty * bid
    spread = abs(generous - conservative)
    mid = (generous + conservative) / 2.0
    if mid == 0:
        return None
    return spread / abs(mid)


def strategy_explanation(
    legs: list[Leg], spot: float, pop_value: float | None, expiration: date | None
) -> str:
    """The "This is a bullish strategy with limited risk of $X..." paragraph, from the payoff
    engine's own numbers -- every claim traceable to a computed quantity."""
    direction = _direction(legs, spot)
    loss = max_loss(legs)
    profit = max_profit(legs)
    risk_part = "unlimited risk" if loss["unbounded"] else f"limited risk of ${abs(loss['value']):,.2f}"
    reward_part = (
        "unlimited potential reward"
        if profit["unbounded"]
        else f"limited potential reward of ${profit['value']:,.2f}"
    )
    sentences = [f"This is a {direction} strategy with {risk_part} and {reward_part}."]

    breaks = breakevens(legs)
    by = f" by {expiration.isoformat()}" if expiration else ""
    if len(breaks) == 1:
        side = "above" if payoff_at(legs, breaks[0] * 1.01) > 0 else "below"
        sentences.append(f"It profits if the stock closes {side} ${breaks[0]:,.2f}{by}.")
    elif len(breaks) >= 2:
        mid = (breaks[0] + breaks[-1]) / 2
        word = "between" if payoff_at(legs, mid) > 0 else "outside"
        sentences.append(
            f"It profits if the stock closes {word} ${breaks[0]:,.2f} and ${breaks[-1]:,.2f}{by}."
        )
    if pop_value is not None:
        sentences.append(f"There is a {pop_value * 100:.1f}% model probability of that happening.")
    return " ".join(sentences)


def greeks_explanation(symbol: str, greeks: dict) -> str | None:
    """The greeks, read aloud: what a $1 move, a day of decay, and a vol point actually do to this
    position in dollars."""
    delta, theta, vega = greeks.get("delta"), greeks.get("theta"), greeks.get("vega")
    if delta is None:
        return None
    parts = [
        f"For every $1 {symbol} rises, this position makes about ${delta:,.2f}"
        if delta >= 0
        else f"For every $1 {symbol} rises, this position loses about ${abs(delta):,.2f}"
    ]
    if theta is not None:
        parts.append(
            f"time decay {'adds' if theta >= 0 else 'costs'} about ${abs(theta):,.2f} per day"
        )
    if vega is not None:
        parts.append(
            f"a one-point IV {'rise adds' if vega >= 0 else 'rise costs'} about ${abs(vega):,.2f}"
        )
    return "; ".join(parts) + ". Model greeks (Black-Scholes, flat IV), not a live feed."


def short_put_suggestion(
    symbol: str, strike: float, expiration: date, credit_dollars: float, spot: float
) -> str:
    """The wheel-style framing: the assignment case as stock acquisition at a discount."""
    net = strike - credit_dollars / 100.0
    discount = (spot - net) / spot if spot > 0 else 0.0
    return (
        f"Consider selling the {expiration.isoformat()} ${strike:,.2f} put on {symbol} to "
        f"potentially acquire the stock at a {discount * 100:.1f}% discount. You collect "
        f"${credit_dollars:,.2f} in premium per contract and take on the obligation to buy 100 "
        f"shares at a net price of ${net:,.2f} if the stock closes below ${strike:,.2f} and the "
        "put is exercised."
    )


def has_weekly_cadence(expiration_isos: list[str]) -> bool:
    """True when the chain has a genuine weekly expiration cadence -- a gap of <= 10 days between
    two consecutive expirations somewhere in the chain (the earnings module's own
    `has_weekly_options` rule). A monthly-only name can coincidentally have a near expiration
    without actually running weeklies; cadence is the honest check."""
    from datetime import date

    try:
        dates = sorted(date.fromisoformat(iso) for iso in expiration_isos)
    except ValueError:
        return False
    return any((b - a).days <= 10 for a, b in zip(dates, dates[1:], strict=False))


def _spread_status(spread_pct: float | None, has_weeklies: bool | None) -> str:
    """Spread & Liquidity grade. User rule: HIGH liquidity must always have weekly expirations
    available -- so a pass requires both a tight spread AND confirmed weekly cadence; a tight
    spread on a monthly-only chain caps at warn. `has_weeklies=None` means the caller didn't
    evaluate cadence (pure-function default); the spread grade then stands alone."""
    if spread_pct is None:
        return "warn"
    if spread_pct <= 0.05:
        return "warn" if has_weeklies is False else "pass"
    if spread_pct <= 0.15:
        return "warn"
    return "fail"


def checklist_directional(
    strategy_direction: str,
    stock_trend_1m: str | None,
    market_trend_1m: str | None,
    earnings_inside: bool | None,
    spread_pct: float | None,
    has_weeklies: bool | None = None,
) -> list[dict]:
    """The credit-spread (directional-strategy) checklist variant, calibrated from four observed
    reference cards: Stock Trend and Market Trend grade the strategy's direction against the
    stock's / the S&P 500's 1M trend (aligned = pass, opposed = fail, neutral or unknown = warn);
    earnings and spread behave as in `checklist` (spread graded on the NET combo bid/ask, per the
    observed CSX card: combo bid $0.00 / ask $1.30 = red). The reference's own Score row is
    omitted here until scout has a score analog -- a missing row beats a fabricated one."""
    from .trend import BEARISH, BULLISH, MILDLY_BEARISH, MILDLY_BULLISH

    side = {BULLISH: 1, MILDLY_BULLISH: 1, MILDLY_BEARISH: -1, BEARISH: -1}
    want = 1 if strategy_direction == "bullish" else (-1 if strategy_direction == "bearish" else 0)

    def trend_status(label):
        if want == 0 or label is None or side.get(label) is None:
            return "warn"
        return "pass" if side[label] == want else "fail"

    items = [
        {"name": "Stock trend", "status": trend_status(stock_trend_1m)},
        {"name": "Market trend", "status": trend_status(market_trend_1m)},
    ]
    if earnings_inside is None:
        items.append({"name": "Earnings date", "status": "warn"})
    else:
        items.append({"name": "Earnings date", "status": "warn" if earnings_inside else "pass"})
    items.append({"name": "Spread & liquidity", "status": _spread_status(spread_pct, has_weeklies)})
    return items


def checklist(
    pow_value: float | None,
    annualized: float | None,
    earnings_inside: bool | None,
    spread_pct: float | None,
    has_weeklies: bool | None = None,
) -> list[dict]:
    """Pass/warn/fail per criterion (thresholds documented in the module docstring as guesses).
    An unknowable input warns rather than passing -- absence of data is not a green light."""
    items = []

    def grade(name, value, passed, warned):
        status = "warn" if value is None else ("pass" if passed else ("warn" if warned else "fail"))
        items.append({"name": name, "status": status})

    grade("Probability of worthless", pow_value,
          pow_value is not None and pow_value >= 0.70,
          pow_value is not None and pow_value >= 0.55)
    grade("Annualized return", annualized,
          annualized is not None and annualized >= 0.05,
          annualized is not None and annualized >= 0.02)
    if earnings_inside is None:
        items.append({"name": "Earnings date", "status": "warn"})
    else:
        items.append({"name": "Earnings date", "status": "warn" if earnings_inside else "pass"})
    items.append({"name": "Spread & liquidity", "status": _spread_status(spread_pct, has_weeklies)})
    return items
