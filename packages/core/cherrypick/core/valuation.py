"""An unlevered discounted-cash-flow value of one company: a model estimate of value, never a price.

The arithmetic every DCF shares, written once so the income lists and the Screeners fair-value tab
(technicals) and any later reader agree on it:

    FCF_t  = EBIT_t x (1 - tax) + D&A_t - capex_t - working-capital investment_t
    EV     = sum FCF_t / (1 + WACC)^t  +  TV / (1 + WACC)^N,   TV = FCF_N (1 + g) / (WACC - g)
    equity = EV - debt + cash;   value per share = equity / shares
    WACC   = ke x E/(D+E) + kd (1 - tax) x D/(D+E),   ke = rf + beta (rm - rf)

Reviewed 2026-10-27 from a student fund's spreadsheets (`Equity-Valuation-Modelling-in-Excel`) and
Market Taker Mentoring's episode on DCF strengths and weaknesses. Both say the same thing: the value
is only as good as its assumptions, the terminal value is most of the answer for a growth name, and
the right use is one data point beside other checks when picking short-put and covered-call names.
So this module builds **one mechanical base case** from the company's own history (medians of the
last five fiscal years, the consensus next-year sales growth when a caller has it) and states a
**low-high range** from a grid over the discount rate and the terminal growth, rather than asking
anyone to write bull and bear cases. Nothing here is a ranking, and no edge has been measured for it.

Every input is a plain number the caller read from its own store (statements, bars, estimates); the
module reaches no file and no network. Every constant is declared below with its reason. A company
the model cannot value -- too little history, no operating profit, a discount rate too close to the
growth rate, a cash flow that never turns positive -- gets a `Refusal` with its reason, never a
number. Banks and REITs are not DCF-able on operating cash flow; the caller refuses them by sector
before calling.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from cherrypick.core import incomerules

# Five forecast years: the paper's rule, and further out the drivers are guesses.
FORECAST_YEARS = 5
# Growth into perpetuity: bounded by nominal GDP growth; a company cannot outgrow the economy forever.
TERMINAL_GROWTH = 0.02
# The equity premium over the risk-free rate (the 9% market return the spreadsheets hard-coded, less
# a 4% rate); a long-run convention, declared, not estimated.
EQUITY_PREMIUM = 0.05
# A regression beta outside this band is noise or a regime, not a cost of capital.
BETA_BOUNDS = (0.5, 2.0)
# Year-one revenue growth outside this band is a one-off (an acquisition, a collapse), not a path.
GROWTH_BOUNDS = (-0.10, 0.25)
# An effective tax rate outside this band is a loss carry-forward or a one-off, not the going rate.
TAX_BOUNDS = (0.10, 0.35)
# The US statutory rate since 2018, used when a company's history gives no readable effective rate.
DEFAULT_TAX = 0.21
# A pre-tax cost of debt outside this band is a reporting artefact (interest on a near-zero balance).
KD_BOUNDS = (0.02, 0.12)
# Fewer fiscal years than this and a median is not a history.
MIN_YEARS = 3
# Below this spread between WACC and terminal growth the perpetuity is arithmetic, not a value.
MIN_SPREAD = 0.01
# A terminal value above this share of EV says the answer is the perpetuity, not the forecast.
TERMINAL_HEAVY = 0.75
# The sensitivity grid: WACC a point either way in half-point steps, terminal growth half a point
# either way in quarter-point steps.
WACC_STEPS = (-0.01, -0.005, 0.0, 0.005, 0.01)
GROWTH_STEPS = (-0.005, -0.0025, 0.0, 0.0025, 0.005)


@dataclass(frozen=True)
class FiscalYear:
    """One fiscal year as the statements state it. Money in one unit throughout (dollars or
    millions); `shares` in the same unit as the per-share answer wants."""

    end: str  # ISO date the year ended
    revenue: float
    ebit: float  # operating income after depreciation
    da: float  # depreciation and amortisation (positive)
    capex: float  # cash spent on fixed assets (positive)
    nwc_change: float  # the cash-flow statement's working-capital line: negative when cash was absorbed
    debt: float  # long-term debt plus its current portion and notes payable
    cash: float  # cash and equivalents
    shares: float  # shares outstanding at year end
    pretax: float | None = None
    taxes: float | None = None
    interest: float | None = None


@dataclass(frozen=True)
class Drivers:
    """What the forecast runs on: five growth rates, then everything as a share of revenue."""

    growth: tuple[float, ...]
    ebit_margin: float
    da_pct: float
    capex_pct: float
    nwc_pct: float  # working-capital investment as a share of revenue (positive absorbs cash)
    tax_rate: float
    terminal_growth: float


@dataclass(frozen=True)
class Projection:
    year: int
    revenue: float
    ebit: float
    nopat: float
    da: float
    capex: float
    nwc: float
    fcf: float


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Valuation:
    fair_value: float  # per share, the base case
    low: float  # the grid's minimum
    high: float  # the grid's maximum
    wacc: float
    beta: float  # the bounded beta the WACC used
    beta_raw: float
    drivers: Drivers
    ev: float
    terminal_value: float  # undiscounted
    terminal_share: float  # discounted TV over EV
    implied_ev_ebitda: float | None  # EV over the base year's EBITDA, a sanity figure
    projections: tuple[Projection, ...]
    flags: tuple[str, ...]


# -------------------------------------------------------------------------------------- the drivers


def _clamp(x: float, bounds: tuple[float, float]) -> float:
    return min(max(x, bounds[0]), bounds[1])


def cagr(first: float, last: float, years: int) -> float | None:
    """Compound annual growth from `first` to `last` over `years`; None when either is not positive."""
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def fade(start: float, end: float, n: int = FORECAST_YEARS) -> tuple[float, ...]:
    """`n` rates from `start` in year one to `end` in year `n`, linear."""
    if n == 1:
        return (start,)
    return tuple(start + (end - start) * i / (n - 1) for i in range(n))


def effective_tax(years: list[FiscalYear]) -> float:
    """The median effective rate over the years with a profit and a tax figure, clamped; the
    statutory default when none reads."""
    rates = [y.taxes / y.pretax for y in years if y.pretax and y.pretax > 0 and y.taxes is not None]
    if not rates:
        return DEFAULT_TAX
    return _clamp(statistics.median(rates), TAX_BOUNDS)


def drivers_from_history(
    years: list[FiscalYear],
    consensus_growth: float | None = None,
    terminal_growth: float = TERMINAL_GROWTH,
) -> Drivers | Refusal:
    """The base case from the last `FORECAST_YEARS` fiscal years: medians for the margin and the
    percent-of-revenue lines, year-one growth from the consensus when given (else the window's CAGR),
    faded to `terminal_growth` by year five."""
    hist = sorted(years, key=lambda y: y.end)[-FORECAST_YEARS:]
    if len(hist) < MIN_YEARS:
        return Refusal("too_few_years")
    if any(y.revenue <= 0 for y in hist):
        return Refusal("no_revenue")
    margin = statistics.median(y.ebit / y.revenue for y in hist)
    if margin <= 0:
        return Refusal("no_operating_profit")
    g1 = consensus_growth
    if g1 is None:
        g1 = cagr(hist[0].revenue, hist[-1].revenue, len(hist) - 1)
    if g1 is None:
        return Refusal("no_growth_read")
    return Drivers(
        growth=fade(_clamp(g1, GROWTH_BOUNDS), terminal_growth),
        ebit_margin=margin,
        da_pct=statistics.median(y.da / y.revenue for y in hist),
        capex_pct=statistics.median(y.capex / y.revenue for y in hist),
        nwc_pct=statistics.median(-y.nwc_change / y.revenue for y in hist),
        tax_rate=effective_tax(hist),
        terminal_growth=terminal_growth,
    )


# ------------------------------------------------------------------------------- the cost of capital


def beta(stock_returns: list[float], index_returns: list[float]) -> float | None:
    """The OLS slope of the stock's returns on the index's, same periods in the same order; None when
    there are fewer than two or the index did not move."""
    n = min(len(stock_returns), len(index_returns))
    if n < 2:
        return None
    s, m = stock_returns[:n], index_returns[:n]
    ms, mm = sum(s) / n, sum(m) / n
    var = sum((x - mm) ** 2 for x in m)
    if var <= 0:
        return None
    return sum((a - ms) * (b - mm) for a, b in zip(s, m, strict=True)) / var


def bounded_beta(b: float) -> float:
    return _clamp(b, BETA_BOUNDS)


def cost_of_equity(rf: float, b: float, erp: float = EQUITY_PREMIUM) -> float:
    return rf + b * erp


def cost_of_debt(interest: float | None, debt: float, tax_rate: float) -> float:
    """After tax. Interest over the debt balance, clamped; the floor when there is no debt or no
    interest figure (a company without debt borrows at the floor if it ever does)."""
    if interest is None or debt <= 0:
        pre = KD_BOUNDS[0]
    else:
        pre = _clamp(abs(interest) / debt, KD_BOUNDS)
    return pre * (1.0 - tax_rate)


def wacc(ke: float, kd_after_tax: float, debt: float, market_cap: float) -> float:
    total = max(debt, 0.0) + max(market_cap, 0.0)
    if total <= 0:
        return ke
    return ke * market_cap / total + kd_after_tax * max(debt, 0.0) / total


# ---------------------------------------------------------------------------------- the cash flows


def project(base: FiscalYear, d: Drivers) -> tuple[Projection, ...]:
    out = []
    revenue = base.revenue
    for i, g in enumerate(d.growth, 1):
        revenue = revenue * (1.0 + g)
        ebit = revenue * d.ebit_margin
        nopat = ebit * (1.0 - d.tax_rate)
        da = revenue * d.da_pct
        capex = revenue * d.capex_pct
        nwc = revenue * d.nwc_pct
        out.append(Projection(i, revenue, ebit, nopat, da, capex, nwc, nopat + da - capex - nwc))
    return tuple(out)


def terminal_value(fcf: float, rate: float, growth: float) -> float | None:
    """The perpetuity after the last forecast year; None when the spread is under `MIN_SPREAD`."""
    if rate - growth < MIN_SPREAD:
        return None
    return fcf * (1.0 + growth) / (rate - growth)


def present_value(flows: list[float] | tuple[float, ...], rate: float) -> float:
    return sum(f / (1.0 + rate) ** t for t, f in enumerate(flows, 1))


def equity_per_share(ev: float, debt: float, cash: float, shares: float) -> float | None:
    if shares <= 0:
        return None
    return (ev - debt + cash) / shares


def _per_share(
    projections: tuple[Projection, ...], rate: float, growth: float, base: FiscalYear
) -> tuple[float, float, float] | None:
    """(per share, EV, discounted TV) at one rate and growth, or None where the model refuses."""
    tv = terminal_value(projections[-1].fcf, rate, growth)
    if tv is None:
        return None
    n = len(projections)
    pv_tv = tv / (1.0 + rate) ** n
    ev = present_value([p.fcf for p in projections], rate) + pv_tv
    ps = equity_per_share(ev, base.debt, base.cash, base.shares)
    if ps is None:
        return None
    return ps, ev, pv_tv


def sensitivity(
    projections: tuple[Projection, ...], rate: float, growth: float, base: FiscalYear
) -> dict[tuple[float, float], float | None]:
    """Per-share value at each (WACC step, growth step) of the grid; None where the model refuses."""
    out: dict[tuple[float, float], float | None] = {}
    for dw in WACC_STEPS:
        for dg in GROWTH_STEPS:
            cell = _per_share(projections, rate + dw, growth + dg, base)
            out[(dw, dg)] = None if cell is None else cell[0]
    return out


# ------------------------------------------------------------------------------------- the answer


def value(
    years: list[FiscalYear],
    market_cap: float,
    beta_raw: float | None,
    consensus_growth: float | None = None,
    rf: float = incomerules.RATE,
    erp: float = EQUITY_PREMIUM,
    terminal_growth: float = TERMINAL_GROWTH,
) -> Valuation | Refusal:
    """The base-case value per share and its grid range, or why there is none.

    `years` are the fiscal years the caller may know as of its session (point in time is the caller's
    job); `market_cap` weights the WACC; `beta_raw` is the caller's regression beta (bounded here).
    """
    if beta_raw is None or not math.isfinite(beta_raw):
        return Refusal("no_beta")
    d = drivers_from_history(years, consensus_growth, terminal_growth)
    if isinstance(d, Refusal):
        return d
    base = max(years, key=lambda y: y.end)
    if base.shares <= 0:
        return Refusal("no_shares")
    b = bounded_beta(beta_raw)
    rate = wacc(
        cost_of_equity(rf, b, erp), cost_of_debt(base.interest, base.debt, d.tax_rate), base.debt, market_cap
    )
    if rate - d.terminal_growth < MIN_SPREAD:
        return Refusal("wacc_near_growth")
    projections = project(base, d)
    if projections[-1].fcf <= 0:
        return Refusal("negative_terminal_fcf")
    centre = _per_share(projections, rate, d.terminal_growth, base)
    if centre is None:
        return Refusal("wacc_near_growth")
    fair, ev, pv_tv = centre
    if ev <= 0:
        return Refusal("non_positive_ev")
    if fair <= 0:
        return Refusal("negative_equity")
    grid = [v for v in sensitivity(projections, rate, d.terminal_growth, base).values() if v is not None]
    flags = []
    terminal_share = pv_tv / ev
    if terminal_share > TERMINAL_HEAVY:
        flags.append("terminal_heavy")
    if projections[0].fcf <= 0:
        flags.append("negative_base_fcf")
    if b != beta_raw:
        flags.append("beta_bounded")
    ebitda = base.ebit + base.da
    return Valuation(
        fair_value=fair,
        low=min(grid),
        high=max(grid),
        wacc=rate,
        beta=b,
        beta_raw=beta_raw,
        drivers=d,
        ev=ev,
        terminal_value=terminal_value(projections[-1].fcf, rate, d.terminal_growth) or 0.0,
        terminal_share=terminal_share,
        implied_ev_ebitda=ev / ebitda if ebitda > 0 else None,
        projections=projections,
        flags=tuple(flags),
    )
