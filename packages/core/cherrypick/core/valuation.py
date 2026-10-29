"""An unlevered discounted-cash-flow value of one company: a model estimate of value, never a price.

The arithmetic, written once so the income lists, the Screeners fair-value tab and the history study
agree on it (docs/fair-value-model-plan.md, version 2 of 2026-10-28):

    FCFF_t   = EBIT_t (1 - tax) + D&A_t - capex_t - (WC_t - WC_t-1)          years 1..10
    FCFF_11  = NOPAT_11 (1 - g / ROIC),  ROIC = WACC                          the stable year
    EV       = sum FCFF_t / (1 + WACC)^(t - 1/2)  +  FCFF_11 / (WACC - g) / (1 + WACC)^10
    equity   = EV + cash - debt - finance leases - preferred - noncontrolling interest
    shares   = basic + unvested RSUs + options by the treasury-stock method at the modelled value
    WACC     = ke E/(D+E) + kd (1 - 25%) D/(D+E),   ke = rf + beta ERP,   kd = rf + spread(coverage)

The method is public and standard: ten forecast years that start from the analysts' revenue for the
years they cover and fade to the long-run rate; margins, D&A, capex and working capital from the
company's own five-year history; a **terminal value that pays for its growth** (the stable-growth
method in Aswath Damodaran's teaching: after year ten new investment earns the cost of capital, so
growth neither adds value nor removes it, and the perpetuity stops counting free growth); a
Blume-adjusted beta; a cost of debt from the **synthetic rating** an interest-coverage ratio maps to
(the caller passes Damodaran's coverage table); mid-year discounting; the full equity bridge.

Version 1 (2026-10-27) forecast five years, priced debt at its coupon, floored the WACC at 7%, bounded
beta and let the perpetuity grow for free. Checked name by name against a reference DCF platform on
2026-10-28, that read AAL at three times its price on cheap coupon debt, missed BMY's patent cliff and
could not value a company mid build-out (ORCL). This version replaces all four.

Every input is a plain number the caller read from its own store; the module reaches no file and no
network. Every constant is declared with its reason. A company the model cannot value gets a
`Refusal` with its reason, never a number. Banks, insurers, brokers, REITs, funds and foreign filers
are refused by the caller before it calls (their statements are not an operating business's).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace

# Ten forecast years: long enough for a company whose investment pays later (a build-out) to reach
# its payoff inside the forecast, which five years could not (ORCL, 2026-10-28).
FORECAST_YEARS = 10
# Long-run growth after year ten: nominal growth for a mature US business, under the 10-year Treasury.
TERMINAL_GROWTH = 0.025
# The company's own history: five fiscal years for every ratio, the median of them.
HISTORY_YEARS = 5
# Fewer fiscal years than this and a median is not a history.
MIN_YEARS = 3
# Blume's adjustment pulls a regression beta a third of the way toward 1: betas regress to the market
# over time, and the forecast is about the next ten years, not the last five.
BLUME = (0.67, 0.33)
# Even adjusted, a beta outside this band is a data fault, not a cost of capital; flagged when it binds.
BETA_SANITY = (0.2, 3.0)
# The US marginal corporate rate (21% federal plus a typical state rate): the tax shield on interest.
MARGINAL_TAX = 0.25
# An effective tax rate outside this band is a loss carry-forward or a one-off, not the going rate.
TAX_BOUNDS = (0.10, 0.35)
# The US statutory rate since 2018, used when a company's history gives no readable effective rate.
DEFAULT_TAX = 0.21
# Year-one growth from history, when no consensus covers the year, is clamped: an acquisition or a
# collapse in the window is a one-off, not a path. A consensus year is taken as the analysts give it,
# inside the wider sanity band.
HISTORY_GROWTH_BOUNDS = (-0.10, 0.25)
CONSENSUS_GROWTH_BOUNDS = (-0.60, 1.50)
# Working capital as a share of revenue is floored here: growth is never funded by suppliers for free.
WC_FLOOR = 0.0
# Below this spread between WACC and long-run growth the perpetuity is arithmetic, not a value.
MIN_SPREAD = 0.01
# A terminal value above this share of EV says the answer is the perpetuity, not the forecast.
TERMINAL_HEAVY = 0.85
# Options by the treasury-stock method are settled at the modelled value, which they change: iterate.
DILUTION_ROUNDS = 3
# The sensitivity grid: WACC a point either way in half-point steps, long-run growth half a point
# either way in quarter-point steps.
WACC_STEPS = (-0.01, -0.005, 0.0, 0.005, 0.01)
GROWTH_STEPS = (-0.005, -0.0025, 0.0, 0.0025, 0.005)


@dataclass(frozen=True)
class FiscalYear:
    """One fiscal year as the company stated it. Money in one unit throughout (dollars); `shares` the
    basic count. The optional lines feed the equity bridge and the share count when known."""

    end: str  # ISO date the year ended
    revenue: float
    ebit: float  # operating income after depreciation
    da: float  # depreciation and amortisation of the company's own assets (positive)
    capex: float  # cash spent on fixed assets (positive)
    nwc_change: float  # the cash-flow statement's working-capital line: negative when cash was absorbed
    debt: float  # short and long-term borrowings
    cash: float  # cash, equivalents and marketable securities
    shares: float  # basic shares outstanding
    pretax: float | None = None
    taxes: float | None = None
    interest: float | None = None
    wc_level: float | None = None  # operating working capital at year end
    finance_lease: float = 0.0
    preferred: float = 0.0
    nci: float = 0.0  # noncontrolling interest
    rsu: float = 0.0  # unvested restricted units, counted as shares
    options: float = 0.0
    options_strike: float | None = None


@dataclass(frozen=True)
class Rating:
    """One band of a coverage-to-rating table: coverage in [low, high] reads this rating and spread."""

    low: float
    high: float
    name: str
    spread: float


@dataclass(frozen=True)
class Drivers:
    """What the forecast runs on: ten growth rates, then everything as a share of revenue."""

    growth: tuple[float, ...]
    growth_sources: tuple[str, ...]  # "consensus" or "history" per year
    ebit_margin: float
    da_pct: float
    capex_pct: float
    wc_pct: float  # working capital as a share of revenue (a level), or its yearly investment (a flow)
    wc_is_level: bool
    tax_rate: float
    terminal_growth: float
    wc_floored: bool = False  # the history's working capital was negative and was floored


@dataclass(frozen=True)
class Projection:
    year: int
    revenue: float
    ebit: float
    nopat: float
    da: float
    capex: float
    wc_change: float  # investment in working capital (positive absorbs cash)
    fcf: float
    discount: float  # the mid-year factor
    pv: float


@dataclass(frozen=True)
class CostOfCapital:
    rf: float
    erp: float
    beta_raw: float
    beta: float  # Blume-adjusted, inside BETA_SANITY
    ke: float
    coverage: float | None  # EBIT over interest; None when the company has no interest expense
    rating: str
    spread: float
    kd_pre_tax: float
    kd_after_tax: float
    equity_weight: float
    debt_weight: float
    wacc: float


@dataclass(frozen=True)
class Bridge:
    pv_forecast: float
    pv_terminal: float
    ev: float
    cash: float
    debt: float
    finance_lease: float
    preferred: float
    nci: float
    equity: float
    basic_shares: float
    rsu: float
    option_shares: float
    shares: float  # the diluted count the value is divided by


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Valuation:
    fair_value: float  # per share, the base case
    low: float  # the grid's minimum
    high: float  # the grid's maximum
    wacc: float
    capital: CostOfCapital
    drivers: Drivers
    ev: float
    terminal_fcf: float  # year eleven's
    terminal_value: float  # undiscounted, at year ten
    terminal_share: float  # discounted TV over EV
    reinvestment_rate: float  # of year eleven's NOPAT, g / ROIC
    implied_ev_ebitda: float | None  # EV over the base year's EBITDA, a sanity figure
    projections: tuple[Projection, ...]
    bridge: Bridge
    grid: dict[tuple[float, float], float | None] = field(default_factory=dict)
    flags: tuple[str, ...] = ()

    @property
    def beta(self) -> float:
        return self.capital.beta

    @property
    def beta_raw(self) -> float:
        return self.capital.beta_raw


# -------------------------------------------------------------------------------------- the drivers


def _clamp(x: float, bounds: tuple[float, float]) -> float:
    return min(max(x, bounds[0]), bounds[1])


def cagr(first: float, last: float, years: int) -> float | None:
    """Compound annual growth from `first` to `last` over `years`; None when either is not positive."""
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def effective_tax(years: list[FiscalYear]) -> float:
    """The median effective rate over the years with a profit and a tax figure (a loss year is
    excluded), clamped; the statutory default when none reads."""
    rates = [y.taxes / y.pretax for y in years if y.pretax and y.pretax > 0 and y.taxes is not None]
    if not rates:
        return DEFAULT_TAX
    return _clamp(statistics.median(rates), TAX_BOUNDS)


def growth_path(
    base_revenue: float,
    consensus: list[float],
    history_growth: float,
    terminal: float,
    n: int = FORECAST_YEARS,
    clamp: bool = True,
) -> tuple[tuple[float, ...], tuple[str, ...]]:
    """Year-by-year revenue growth: the consensus revenue for the years it covers (year one is the
    current fiscal year), then a straight line from the last such year's growth to `terminal` in year
    `n`. With no consensus, year one is the history growth (clamped unless `clamp` is False: the reverse
    DCF searches beyond the clamp) and the line starts there."""
    growth: list[float] = []
    sources: list[str] = []
    prev = base_revenue
    for rev in consensus[:n]:
        growth.append(_clamp(rev / prev - 1.0, CONSENSUS_GROWTH_BOUNDS))
        sources.append("consensus")
        prev = rev
    if not growth:
        growth.append(_clamp(history_growth, HISTORY_GROWTH_BOUNDS) if clamp else history_growth)
        sources.append("history")
    k, g_k = len(growth), growth[-1]
    for t in range(k + 1, n + 1):
        growth.append(g_k + (terminal - g_k) * (t - k) / (n - k))
        sources.append("fade")
    return tuple(growth), tuple(sources)


def drivers_from_history(
    years: list[FiscalYear], consensus: list[float] | None = None, terminal_growth: float = TERMINAL_GROWTH
) -> Drivers | Refusal:
    """The base case from the newest `HISTORY_YEARS` fiscal years: medians of the operating margin, D&A,
    capex and working capital as shares of revenue, the median effective tax rate, and the growth path."""
    hist = sorted(years, key=lambda y: y.end)[-HISTORY_YEARS:]
    if len(hist) < MIN_YEARS:
        return Refusal("too_few_years")
    if any(y.revenue <= 0 for y in hist):
        return Refusal("no_revenue")
    margin = statistics.median(y.ebit / y.revenue for y in hist)
    if margin <= 0:
        return Refusal("no_operating_profit")
    levels = [y.wc_level / y.revenue for y in hist if y.wc_level is not None]
    if len(levels) >= MIN_YEARS:
        wc_pct, is_level = statistics.median(levels), True
    else:
        wc_pct, is_level = statistics.median(-y.nwc_change / y.revenue for y in hist), False
    # Negative working capital (customers and suppliers fund the business) would release cash in
    # proportion to growth forever: DELL's -25% read about $21B of free cash in a +72% year (2026-10-28).
    # The forecast takes none of it: the ratio is floored at zero, and the floor is flagged.
    floored = wc_pct < WC_FLOOR
    wc_pct = max(wc_pct, WC_FLOOR)
    g_hist = cagr(hist[0].revenue, hist[-1].revenue, len(hist) - 1)
    if g_hist is None and not consensus:
        return Refusal("no_growth_read")
    growth, sources = growth_path(hist[-1].revenue, list(consensus or []), g_hist or 0.0, terminal_growth)
    return Drivers(
        growth=growth,
        growth_sources=sources,
        ebit_margin=margin,
        da_pct=statistics.median(y.da / y.revenue for y in hist),
        capex_pct=statistics.median(y.capex / y.revenue for y in hist),
        wc_pct=wc_pct,
        wc_is_level=is_level,
        tax_rate=effective_tax(hist),
        terminal_growth=terminal_growth,
        wc_floored=floored,
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


def blume(raw: float) -> float:
    return BLUME[0] * raw + BLUME[1]


def rate_for(coverage: float | None, table: list[Rating]) -> Rating:
    """The band an interest coverage ratio falls in; no interest expense reads the top band."""
    bands = sorted(table, key=lambda b: b.low)
    if coverage is None or not math.isfinite(coverage):
        return bands[-1]
    for b in bands:
        if b.low <= coverage <= b.high:
            return b
    for b in bands:  # between two bands' rounded edges: the next band up
        if coverage < b.low:
            return b
    return bands[-1]


def cost_of_capital(
    base: FiscalYear, market_cap: float, beta_raw: float, rf: float, erp: float, table: list[Rating]
) -> CostOfCapital:
    b = _clamp(blume(beta_raw), BETA_SANITY)
    ke = rf + b * erp
    interest = base.interest if base.interest is not None and base.interest > 0 else None
    coverage = None if interest is None else base.ebit / interest
    band = rate_for(coverage, table)
    kd = rf + band.spread
    debt = max(base.debt, 0.0) + max(base.finance_lease, 0.0)
    total = debt + max(market_cap, 0.0)
    we = 1.0 if total <= 0 else max(market_cap, 0.0) / total
    wd = 1.0 - we
    after = kd * (1.0 - MARGINAL_TAX)
    return CostOfCapital(
        rf, erp, beta_raw, b, ke, coverage, band.name, band.spread, kd, after, we, wd, ke * we + after * wd
    )


# ---------------------------------------------------------------------------------- the cash flows


def project(base: FiscalYear, d: Drivers, rate: float) -> tuple[Projection, ...]:
    out = []
    revenue = base.revenue
    wc_prev = d.wc_pct * base.revenue
    for i, g in enumerate(d.growth, 1):
        revenue = revenue * (1.0 + g)
        ebit = revenue * d.ebit_margin
        nopat = ebit * (1.0 - d.tax_rate)
        da = revenue * d.da_pct
        capex = revenue * d.capex_pct
        if d.wc_is_level:
            wc = d.wc_pct * revenue
            wc_change, wc_prev = wc - wc_prev, wc
        else:
            wc_change = d.wc_pct * revenue
        fcf = nopat + da - capex - wc_change
        disc = 1.0 / (1.0 + rate) ** (i - 0.5)
        out.append(Projection(i, revenue, ebit, nopat, da, capex, wc_change, fcf, disc, fcf * disc))
    return tuple(out)


def terminal(
    last: Projection, d: Drivers, rate: float, roic: float | None = None
) -> tuple[float, float, float] | None:
    """(year eleven's FCFF, the undiscounted value at year ten, the reinvestment rate), or None when the
    spread is under `MIN_SPREAD`. Year eleven grows at the long-run rate and reinvests g / ROIC of its
    NOPAT to do so; ROIC defaults to the WACC (growth then neither adds value nor removes it)."""
    g = d.terminal_growth
    if rate <= g or rate - g < MIN_SPREAD:
        return None
    roic = rate if roic is None else roic
    nopat = last.revenue * (1.0 + g) * d.ebit_margin * (1.0 - d.tax_rate)
    reinvest = g / roic if roic > 0 else 1.0
    fcf = nopat * (1.0 - reinvest)
    return fcf, fcf / (rate - g), reinvest


def equity_bridge(ev: float, pv_f: float, pv_t: float, base: FiscalYear) -> tuple[float, Bridge] | None:
    """(value per share, the bridge) with options settled by the treasury-stock method at the value
    they help set; None when there are no shares."""
    equity = ev + base.cash - base.debt - base.finance_lease - base.preferred - base.nci
    basic = base.shares + max(base.rsu, 0.0)
    if basic <= 0:
        return None
    opt = 0.0
    for _ in range(DILUTION_ROUNDS):
        ps = equity / (basic + opt)
        k = base.options_strike
        opt = (
            base.options * max(0.0, ps - k) / ps if base.options > 0 and k is not None and ps > k > 0 else 0.0
        )
    shares = basic + opt
    b = Bridge(pv_f, pv_t, ev, base.cash, base.debt, base.finance_lease, base.preferred, base.nci, equity,
               base.shares, max(base.rsu, 0.0), opt, shares)  # fmt: skip
    return equity / shares, b


def _price(base: FiscalYear, d: Drivers, rate: float, roic: float | None = None):
    """(per share, bridge, projections, terminal) at one rate, or None where the model refuses."""
    proj = project(base, d, rate)
    term = terminal(proj[-1], d, rate, roic)
    if term is None:
        return None
    pv_f = sum(p.pv for p in proj)
    pv_t = term[1] / (1.0 + rate) ** len(proj)
    out = equity_bridge(pv_f + pv_t, pv_f, pv_t, base)
    if out is None:
        return None
    return out[0], out[1], proj, term


# ------------------------------------------------------------------------------------- the answer


def value(
    years: list[FiscalYear],
    price: float,
    beta_raw: float | None,
    rf: float,
    erp: float,
    ratings: list[Rating],
    consensus: list[float] | None = None,
    terminal_growth: float = TERMINAL_GROWTH,
    roic: float | None = None,
) -> Valuation | Refusal:
    """The base-case value per share, its grid range and every step behind it, or why there is none.

    `years` are the fiscal years the caller may know as of its session (point in time is the caller's
    job); `price` sets the market weights; `beta_raw` is the caller's regression beta (adjusted here);
    `ratings` the coverage-to-rating table; `consensus` the analysts' revenue for the fiscal years after
    the last reported one, nearest first; `roic` the return on capital after year ten (None: the WACC).
    """
    if beta_raw is None or not math.isfinite(beta_raw):
        return Refusal("no_beta")
    if not ratings:
        return Refusal("no_rating_table")
    d = drivers_from_history(years, consensus, terminal_growth)
    if isinstance(d, Refusal):
        return d
    base = max(years, key=lambda y: y.end)
    if base.shares <= 0:
        return Refusal("no_shares")
    cap = cost_of_capital(base, price * base.shares, beta_raw, rf, erp, ratings)
    rate = cap.wacc
    if rate - d.terminal_growth < MIN_SPREAD:
        return Refusal("wacc_near_growth")
    centre = _price(base, d, rate, roic)
    if centre is None:
        return Refusal("wacc_near_growth")
    fair, bridge, proj, (tfcf, tv, reinvest) = centre
    if bridge.ev <= 0:
        return Refusal("non_positive_ev")
    if fair <= 0:
        return Refusal("negative_equity")
    grid: dict[tuple[float, float], float | None] = {}
    for dw in WACC_STEPS:
        for dg in GROWTH_STEPS:
            cell = _price(base, replace(d, terminal_growth=d.terminal_growth + dg), rate + dw, roic)
            grid[(dw, dg)] = None if cell is None else cell[0]
    cells = [v for v in grid.values() if v is not None]
    flags = []
    terminal_share = bridge.pv_terminal / bridge.ev
    if terminal_share > TERMINAL_HEAVY:
        flags.append("terminal_heavy")
    if any(p.fcf <= 0 for p in proj[:3]):
        flags.append("negative_early_fcf")
    if cap.beta != blume(beta_raw):
        flags.append("beta_bounded")
    if any(
        s == "consensus" and g < d.terminal_growth for g, s in zip(d.growth, d.growth_sources, strict=True)
    ):
        flags.append("consensus_dip")
    if d.wc_floored:
        flags.append("wc_floored")
    ebitda = base.ebit + base.da
    return Valuation(
        fair_value=fair,
        low=min(cells),
        high=max(cells),
        wacc=rate,
        capital=cap,
        drivers=d,
        ev=bridge.ev,
        terminal_fcf=tfcf,
        terminal_value=tv,
        terminal_share=terminal_share,
        reinvestment_rate=reinvest,
        implied_ev_ebitda=bridge.ev / ebitda if ebitda > 0 else None,
        projections=proj,
        bridge=bridge,
        grid=grid,
        flags=tuple(flags),
    )


# ------------------------------------------------------------------------------- the reverse DCF

# The search band for the year-one growth a price implies (faded to the terminal rate the same way as
# the base case). Outside it the answer is "beyond any plausible path", reported as the edge it hit.
IMPLIED_GROWTH_BOUNDS = (-0.50, 2.00)
# The margin a price implies is reported only inside this band: an operating margin above 100% of
# revenue, or below -100%, is not a business.
IMPLIED_MARGIN_BOUNDS = (-1.0, 1.0)
# The discount rate a price implies is searched in this band.
IMPLIED_WACC_BOUNDS = (0.03, 0.30)


@dataclass(frozen=True)
class Implied:
    """What the price asks of the company, one lever at a time, every other driver held at the base
    case; beside each, the company's own best of the last `HISTORY_YEARS`.

    `margin` is the long-run EBIT margin at which the model's value equals the price (None outside
    `IMPLIED_MARGIN_BOUNDS`). `growth` is the year-one revenue growth, faded to the terminal rate by
    year ten, at which it does; `growth_edge` says when the answer sits beyond `IMPLIED_GROWTH_BOUNDS`
    ("above" or "below"), and `growth` is then that edge. `wacc` is the discount rate at which it does
    (None outside `IMPLIED_WACC_BOUNDS`)."""

    margin: float | None
    growth: float
    growth_edge: str | None
    best_margin: float
    best_growth: float | None
    wacc: float | None = None


def best_figures(years: list[FiscalYear]) -> tuple[float, float | None]:
    """(highest EBIT margin, highest year-over-year revenue growth) over the newest `HISTORY_YEARS`
    fiscal years; growth is None with fewer than two years."""
    hist = sorted(years, key=lambda y: y.end)[-HISTORY_YEARS:]
    margin = max(y.ebit / y.revenue for y in hist if y.revenue > 0)
    growth = [b.revenue / a.revenue - 1.0 for a, b in zip(hist, hist[1:], strict=False) if a.revenue > 0]
    return margin, (max(growth) if growth else None)


def implied(years: list[FiscalYear], v: Valuation, price: float, roic: float | None = None) -> Implied | None:
    """The reverse DCF: the margin, the growth and the discount rate at which `value` would read the
    price. None when the price is not positive."""
    if price <= 0:
        return None
    base = max(years, key=lambda y: y.end)
    d = v.drivers
    best_margin, best_growth = best_figures(years)

    def ps(dd: Drivers, rate: float = v.wacc) -> float | None:
        out = _price(base, dd, rate, roic)
        return None if out is None else out[0]

    # Value is close to linear in the margin while no option is in or out of the money: two points,
    # then one secant step to absorb the dilution's kink.
    margin = None
    step = 0.10
    f0, f1 = ps(d), ps(replace(d, ebit_margin=d.ebit_margin + step))
    if f0 is not None and f1 is not None and f1 != f0:
        m = d.ebit_margin + (price - f0) * step / (f1 - f0)
        fm = ps(replace(d, ebit_margin=m))
        if fm is not None and fm != f0 and m != d.ebit_margin:
            m = m + (price - fm) * (m - d.ebit_margin) / (fm - f0)
        if IMPLIED_MARGIN_BOUNDS[0] <= m <= IMPLIED_MARGIN_BOUNDS[1]:
            margin = m

    def at_growth(g: float) -> float:
        path, src = growth_path(base.revenue, [], g, d.terminal_growth, clamp=False)
        out = ps(replace(d, growth=path, growth_sources=src))
        return -math.inf if out is None else out

    lo, hi = IMPLIED_GROWTH_BOUNDS
    if at_growth(hi) < price:
        g_out, edge = hi, "above"
    elif at_growth(lo) > price:
        g_out, edge = lo, "below"
    else:
        for _ in range(60):
            mid = (lo + hi) / 2.0
            lo, hi = (mid, hi) if at_growth(mid) < price else (lo, mid)
        g_out, edge = (lo + hi) / 2.0, None
    # Value falls as the rate rises: bisect.
    wacc = None
    rlo, rhi = max(IMPLIED_WACC_BOUNDS[0], d.terminal_growth + MIN_SPREAD), IMPLIED_WACC_BOUNDS[1]
    vlo, vhi = ps(d, rlo), ps(d, rhi)
    if vlo is not None and vhi is not None and vhi <= price <= vlo:
        for _ in range(60):
            mid = (rlo + rhi) / 2.0
            vm = ps(d, mid)
            if vm is None or vm > price:
                rlo = mid
            else:
                rhi = mid
        wacc = (rlo + rhi) / 2.0
    return Implied(margin, g_out, edge, best_margin, best_growth, wacc)
