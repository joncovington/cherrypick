"""The fair-value file: one DCF estimate a name a session, for the income lists and the Screeners tab.

`cherrypick.core.valuation` holds the arithmetic (version 2, docs/fair-value-model-plan.md); this module
reads its inputs, every one with its source, and writes `valuation/<session>.json`. For every liquid
optionable name on the security list that the liquidity verdict calls options-tradable (`build`):

- **the fiscal years** are the company's own SEC filings as filed by the session (`secfacts`, point in
  time by each fact's filing date), any line a filing does not state filled from Dolt's statements for
  the same year, and every line further than 10% from Dolt's recorded (`disagree`);
- **consensus** is the analysts' revenue for the fiscal years after the last reported one, from Dolt's
  newest snapshot no older than three weeks, used only with at least `consensus.MIN_ANALYSTS`
  analysts (`consensus.years`): year one of the forecast is the current fiscal year;
- **beta** is the OLS slope of the name's weekly log returns on SPY's over the trailing
  `BETA_YEARS`, both from the store's adjusted (total-return) bars as adjusted on the session; the
  model Blume-adjusts it;
- **the risk-free rate** is the 10-year Treasury before the session (`core.treasury`), **the equity
  risk premium** Damodaran's implied ERP for the month, **the cost of debt** the risk-free rate plus
  the spread Damodaran's table gives the company's interest coverage (`damodaran`);
- **shares** are the filing's basic count, times every split with an ex-date after the fiscal year's
  end and on or before the session (`shares_after_splits`, flag `split_adjusted`), plus unvested RSUs
  and options (the model's treasury-stock method).

Refused before the model, with the reason in words on the tab: a fund; a bank, lender, broker,
insurer, REIT or other financial company (by its SIC code, else its sector); a foreign filer (form
20-F or 40-F: foreign rules and currencies); a manufacturer with a lending arm (`CAPTIVE_FINANCE`);
results older than `STALE_DAYS`.

Each valued row also carries the reverse DCF (`core.valuation.implied`): the margin, the year-one
growth and the discount rate at which the base case equals the price, beside the company's own best
margin and revenue growth, and `reach` in words (`below value`, `within history`, `beyond history`).
**A model estimate of value, tested 2018-26 with no edge (scripts/fair_value_history.py), never a
ranking.** The broker's beta and market cap ride along as a cross-check (`beta_disagrees`,
`shares_stale`); ours are the figures used.

    python -m cherrypick.fundamentals valuation [--day YYYY-MM-DD]

Local files only: the history store, the security list, the sector maps, the broker's IV file, the
SEC and Damodaran files and the Treasury curve.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio as _jsonio
from cherrypick.core import treasury as _treasury
from cherrypick.core import valuation as _val

# Technicals for prices only: its bars, splits, the security list, the sector map, the liquidity
# verdict and the broker's IV file. Technicals never imports this package.
from cherrypick.technicals import adjust as _adjust
from cherrypick.technicals import fundamentals as _score
from cherrypick.technicals import paths as _tpaths
from cherrypick.technicals import securities, store

from . import consensus as _consensus
from . import damodaran, paths, secfacts, statements

VERSION = 2
NOT_DCF_SECTORS = ("Financials", "Real Estate")
# SIC codes whose statements are not an operating business's: the first two digits, with the words
# the tab shows. Investment advice (6282: BX, APO) keeps its operating fee business and is valued.
FINANCIAL_SIC = {
    "60": "a bank",
    "61": "a lender",
    "62": "a broker or exchange",
    "63": "an insurer",
    "64": "an insurer",
    "65": "real estate",
    "67": "a trust or holding company",
}
VALUED_SIC = ("6282",)
# A manufacturer (SIC 2000-3999) that originates loans worth more than this share of its revenue in a
# year runs a lending business inside it; an operating DCF misprices both halves. On 2026-10-28: GM 20%,
# CAT 23% (refused); DELL 10.5% (valued). A flow measure overstates short loans that turn over within
# the year (Shopify's merchant advances, 36%), so a non-manufacturer is never caught by it.
CAPTIVE_FINANCE = 0.15
MANUFACTURER_SIC = ("2", "3")
# Results older than this are too old to value the company today (a new holding company, a late filer).
STALE_DAYS = 200
# A company known only from Dolt's annual statements has no quarterly reports to date it by: its newest
# fiscal year is allowed a year more, or every such name would read stale ten months after year end.
ANNUAL_ONLY_EXTRA_DAYS = 365
BETA_YEARS = 5
BETA_STEP = 5  # sessions between return samples: weekly
MIN_RETURNS = 104  # two years of weekly returns, or no beta
BETA_DISAGREE = 0.5
SHARES_STALE = 0.10
INDEX = "SPY"
# The equity premium when Damodaran's file is not on disk: his long-run range, flagged where used.
FALLBACK_ERP = 0.045


def out_dir() -> Path:
    return paths.valuation_dir()


def latest(day: str | None = None) -> Path | None:
    """The newest valuation file on or before `day`."""
    day = day or date.today().isoformat()
    root = out_dir()
    days = sorted(p.stem for p in root.glob("????-??-??.json")) if root.is_dir() else []
    pick = max((d for d in days if d <= day), default=None)
    return None if pick is None else root / f"{pick}.json"


def load(day: str | None = None) -> dict | None:
    """The newest file's document, or None when none has been built."""
    path = latest(day)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------- the inputs


def log_returns(closes: list[float], step: int = BETA_STEP) -> list[float]:
    """Log returns over every `step` closes, oldest first; a non-positive close breaks the series."""
    out = []
    for k in range(step, len(closes), step):
        a, b = closes[k - step], closes[k]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def aligned_returns(
    stock: dict[str, float], index: dict[str, float], as_of: str, years: int = BETA_YEARS
) -> tuple[list[float], list[float]]:
    """(stock returns, index returns) over the sessions both have in the trailing `years`."""
    start = (date.fromisoformat(as_of) - timedelta(days=365 * years)).isoformat()
    dates = sorted(d for d in stock if start <= d <= as_of and d in index)
    return log_returns([stock[d] for d in dates]), log_returns([index[d] for d in dates])


def shares_after_splits(shares: float, splits: list, fiscal_end: str, as_of: str) -> tuple[float, bool]:
    """(shares on the session, whether a split moved them): the fiscal year's count times each split's
    to/for ratio whose ex-date falls after the year's end and on or before the session."""
    factor = 1.0
    for s in splits:
        if fiscal_end < s.ex_date <= as_of and s.to_factor > 0 and s.for_factor > 0:
            factor *= s.to_factor / s.for_factor
    return shares * factor, factor != 1.0


def closes_as_of(conn, symbol: str, as_of: str) -> dict[str, float]:
    return {b.date: b.close for b in store.adjusted_bars(conn, symbol, as_of=as_of) if b.date <= as_of}


def spot_on(conn, symbol: str, as_of: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT date, close FROM bars WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (symbol, as_of),
    ).fetchone()
    return None if row is None or row[1] is None else (row[0], float(row[1]))


def broker_readings(as_of: str) -> dict[str, dict]:
    """{symbol: the broker's market-metrics reading} on the newest day at or before `as_of`."""
    try:
        doc = json.loads(_tpaths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = doc.get("days") or {}
    pick = max((d for d in days if d <= as_of), default=None)
    return dict(days.get(pick) or {}) if pick else {}


def ratings_table(root: Path | None = None) -> tuple[list[_val.Rating], str | None]:
    """Damodaran's non-financial coverage table as the model's bands, and its date."""
    try:
        doc = json.loads(((root or paths.damodaran_dir()) / "ratings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    return [
        _val.Rating(lo, hi, name, spread) for lo, hi, name, spread in doc.get("non_financial") or []
    ], doc.get("as_of")


def financial_reason(sic: str | None, sector: str | None) -> str | None:
    """Why a company's statements are not an operating business's, or None. The filing's SIC code
    decides when there is one (a payments company the sector map files under Financials, V or PYPL,
    is an operating business); the sector map only when the company has no filing on record."""
    if sic:
        return FINANCIAL_SIC[sic[:2]] if sic not in VALUED_SIC and sic[:2] in FINANCIAL_SIC else None
    if sector in NOT_DCF_SECTORS:
        return sector.lower()
    return None


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


# ------------------------------------------------------------------------------------------ one name


def _flags(v: _val.Valuation, broker: dict | None, market_cap: float) -> list[str]:
    flags = list(v.flags)
    b = (broker or {}).get("beta")
    if b is not None and abs(float(b) - v.beta_raw) > BETA_DISAGREE:
        flags.append("beta_disagrees")
    mc = (broker or {}).get("market_cap")
    if mc and market_cap and abs(market_cap / float(mc) - 1.0) > SHARES_STALE:
        flags.append("shares_stale")
    return flags


def _model_years(merged: list[secfacts.MergedYear]) -> list[_val.FiscalYear]:
    """The merged years with every line the model needs, in its shape."""
    out = []
    for m in merged:
        y = m.year
        if any(v is None for v in (y.revenue, y.ebit, y.da, y.capex, y.cash, y.shares)):
            continue
        out.append(
            _val.FiscalYear(
                end=y.end,
                revenue=y.revenue,
                ebit=y.ebit,
                da=y.da,
                capex=y.capex,
                nwc_change=y.wc_change or 0.0,
                debt=y.debt or 0.0,
                cash=y.cash,
                shares=y.shares,
                pretax=y.pretax,
                taxes=y.taxes,
                interest=y.interest,
                wc_level=y.wc_level,
                finance_lease=y.finance_lease or 0.0,
                preferred=y.preferred or 0.0,
                nci=y.nci or 0.0,
                rsu=y.rsu_unvested or 0.0,
                options=y.options or 0.0,
                options_strike=y.options_strike,
            )
        )
    return out


def value_one(
    conn,
    bars,
    symbol: str,
    as_of: str,
    kind: str | None,
    sector: str | None,
    index_closes: dict[str, float],
    broker: dict | None,
    rf: float = 0.04,
    rf_source: str = "",
    erp: tuple[float, str] | None = None,
    ratings: tuple[list[_val.Rating], str | None] = ([], None),
) -> dict[str, Any]:
    """One row of the file. Never raises on a name's data: the row says what stopped it."""
    row: dict[str, Any] = {
        "symbol": symbol,
        "type": kind,
        "sector": sector,
        "state": None,
        "reason": None,
        "spot": None,
        "spot_date": None,
        "fiscal_year_end": None,
        "fair_value": None,
        "low": None,
        "high": None,
        "gap": None,
        "wacc": None,
        "beta": None,
        "beta_raw": None,
        "beta_broker": None if broker is None or broker.get("beta") is None else float(broker["beta"]),
        "market_cap": None,
        "market_cap_broker": None
        if broker is None or not broker.get("market_cap")
        else float(broker["market_cap"]),
        "growth_y1": None,
        "ebit_margin": None,
        "terminal_share": None,
        "implied_ev_ebitda": None,
        "consensus_used": False,
        "implied_margin": None,
        "implied_growth": None,
        "implied_growth_edge": None,
        "implied_wacc": None,
        "best_margin": None,
        "best_growth": None,
        "reach": None,
        "terminal_growth": None,
        "forecast": None,
        "bridge": None,
        "capital": None,
        "drivers": None,
        "terminal": None,
        "consensus": None,
        "history": None,
        "grid": None,
        "data": None,
        "sources": None,
        "disagree": None,
        "flags": [],
    }
    if kind is not None and kind != "Stock":
        row.update(state="not-applicable", reason="fund")
        return row
    filer = secfacts.filer(conn, symbol) if secfacts._has_table(conn, "sec_filers") else None
    why = financial_reason((filer or {}).get("sic"), sector)
    if why is not None:
        row.update(state="not-applicable", reason=why)
        return row
    if filer and filer.get("foreign_filer"):
        row.update(state="not-applicable", reason="foreign_filer")
        return row
    spot = spot_on(bars, symbol, as_of)
    if spot is None:
        row.update(state="refused", reason="no_price")
        return row
    row.update(spot_date=spot[0], spot=round(spot[1], 4))
    merged = secfacts.merged_years(conn, symbol, as_of)
    years = _model_years(merged)
    if not years:
        row.update(state="no-statements", reason="no annual statements published by the session")
        return row
    base_m = next(m for m in reversed(merged) if m.year.end == years[-1].end)
    latest_p = secfacts.latest_period(conn, symbol, as_of) or (years[-1].end, base_m.year.filed)
    # The filing index knows a report its revenue facts may not (ABT's and NEE's 10-Qs): it counts when
    # it was filed by the session, so a history run never sees a later one.
    if filer and filer.get("last_period") and (filer.get("last_filed") or "9999") <= as_of:
        latest_p = max(latest_p, (filer["last_period"], filer["last_filed"]))
    row["fiscal_year_end"] = years[-1].end
    row["data"] = {
        "fiscal_year_end": years[-1].end,
        "filed": base_m.year.filed or None,
        "latest_period": latest_p[0],
        "latest_filed": latest_p[1],
        "sic": (filer or {}).get("sic"),
        "sic_description": (filer or {}).get("sic_description"),
        "source": "SEC filings" if base_m.year.filed else "Dolt statements",
    }
    row["sources"] = dict(base_m.year.sources)
    row["disagree"] = {k: [round(a, 2), round(b, 2)] for k, (a, b) in base_m.disagree.items()} or None
    stale_after = STALE_DAYS + (0 if base_m.year.filed else ANNUAL_ONLY_EXTRA_DAYS)
    if (date.fromisoformat(as_of) - date.fromisoformat(latest_p[0])).days > stale_after:
        row.update(state="refused", reason="stale_results")
        return row
    bought = base_m.year.finance_receivables_bought
    manufacturer = str((filer or {}).get("sic") or "").startswith(MANUFACTURER_SIC)
    if manufacturer and bought and years[-1].revenue > 0 and bought / years[-1].revenue > CAPTIVE_FINANCE:
        row.update(state="not-applicable", reason="captive_finance")
        return row
    s, i = aligned_returns(closes_as_of(bars, symbol, as_of), index_closes, as_of)
    b = _val.beta(s, i) if len(s) >= MIN_RETURNS else None
    if b is None:
        row.update(state="refused", reason="no_beta")
        return row
    row["beta_raw"] = round(b, 4)
    shares, split = shares_after_splits(
        years[-1].shares,
        _adjust.dedupe_splits(store.raw_bars(bars, symbol), store.splits(bars, symbol)),
        years[-1].end,
        as_of,
    )
    if split:
        years = [*years[:-1], dataclasses.replace(years[-1], shares=shares)]
    market_cap = spot[1] * shares
    row["market_cap"] = round(market_cap, 2)
    cons = []
    if _consensus._has(conn):
        cons = [
            c
            for c in _consensus.years(conn, symbol, as_of, years[-1].end)
            if (c.analysts or 0) >= _consensus.MIN_ANALYSTS
        ][:2]
    row["consensus_used"] = bool(cons)
    row["consensus"] = {
        "snapshot": cons[0].snapshot if cons else None,
        "years": [
            {
                "period_end": c.period_end,
                "revenue": c.revenue,
                "analysts": c.analysts,
                "high": c.high,
                "low": c.low,
            }
            for c in cons
        ],
    }
    erp_v, erp_month = erp if erp is not None else (FALLBACK_ERP, None)
    out = _val.value(years, spot[1], b, rf, erp_v, ratings[0], consensus=[c.revenue for c in cons])
    if isinstance(out, _val.Refusal):
        row.update(state="refused", reason=out.reason)
        return row
    cap, d, br = out.capital, out.drivers, out.bridge
    flags = _flags(out, broker, market_cap) + (["split_adjusted"] if split else [])
    if erp is None:
        flags.append("erp_fallback")
    if base_m.disagree:
        flags.append("filing_disagrees")
    row.update(
        state="valued",
        fair_value=round(out.fair_value, 2),
        low=round(out.low, 2),
        high=round(out.high, 2),
        gap=round(spot[1] / out.fair_value - 1.0, 4),
        wacc=round(out.wacc, 4),
        beta=round(out.beta, 4),
        growth_y1=round(d.growth[0], 4),
        ebit_margin=round(d.ebit_margin, 4),
        terminal_share=round(out.terminal_share, 4),
        implied_ev_ebitda=_r(out.implied_ev_ebitda, 2),
        terminal_growth=d.terminal_growth,
        flags=flags,
        forecast=forecast_rows(out),
        bridge={k: round(v, 2) for k, v in dataclasses.asdict(br).items()},
        capital={
            "rf": round(cap.rf, 4),
            "rf_source": rf_source,
            "erp": round(cap.erp, 4),
            "erp_month": erp_month,
            "beta_raw": round(cap.beta_raw, 4),
            "beta": round(cap.beta, 4),
            "ke": round(cap.ke, 4),
            "coverage": _r(cap.coverage, 2),
            "rating": cap.rating,
            "spread": round(cap.spread, 4),
            "ratings_as_of": ratings[1],
            "kd_pre_tax": round(cap.kd_pre_tax, 4),
            "kd_after_tax": round(cap.kd_after_tax, 4),
            "marginal_tax": _val.MARGINAL_TAX,
            "equity_weight": round(cap.equity_weight, 4),
            "debt_weight": round(cap.debt_weight, 4),
            "wacc": round(cap.wacc, 4),
        },
        drivers={
            "ebit_margin": round(d.ebit_margin, 4),
            "da_pct": round(d.da_pct, 4),
            "capex_pct": round(d.capex_pct, 4),
            "wc_pct": round(d.wc_pct, 4),
            "wc_is_level": d.wc_is_level,
            "tax_rate": round(d.tax_rate, 4),
            "history_years": _val.HISTORY_YEARS,
        },
        terminal={
            "fcf": round(out.terminal_fcf, 2),
            "value": round(out.terminal_value, 2),
            "reinvestment_rate": round(out.reinvestment_rate, 4),
            "roic": round(out.wacc, 4),
        },
        history=history_rows(years),
        grid=[[dw, dg, _r(v, 2)] for (dw, dg), v in out.grid.items()],
    )
    imp = _val.implied(years, out, spot[1])
    if imp is not None:
        row.update(
            implied_margin=_r(imp.margin),
            implied_growth=round(imp.growth, 4),
            implied_growth_edge=imp.growth_edge,
            implied_wacc=_r(imp.wacc),
            best_margin=round(imp.best_margin, 4),
            best_growth=_r(imp.best_growth),
            reach=reach(spot[1], out.fair_value, imp),
        )
    return row


def forecast_rows(out: _val.Valuation) -> list[dict[str, Any]]:
    """The base case's forecast years as the tab draws and tables them, in dollars."""
    return [
        {
            "year": p.year,
            "revenue": round(p.revenue, 2),
            "growth": round(out.drivers.growth[p.year - 1], 4),
            "growth_source": out.drivers.growth_sources[p.year - 1],
            "ebit_margin": round(p.ebit / p.revenue, 4) if p.revenue else None,
            "ebit": round(p.ebit, 2),
            "nopat": round(p.nopat, 2),
            "da": round(p.da, 2),
            "capex": round(p.capex, 2),
            "wc_change": round(p.wc_change, 2),
            "fcf": round(p.fcf, 2),
            "discount": round(p.discount, 4),
            "pv": round(p.pv, 2),
        }
        for p in out.projections
    ]


def history_rows(years: list[_val.FiscalYear]) -> list[dict[str, Any]]:
    """The fiscal years the ratios are medians of, as shares of revenue."""
    hist = sorted(years, key=lambda y: y.end)[-_val.HISTORY_YEARS :]
    out = []
    for y in hist:
        rev = y.revenue or None
        tax = y.taxes / y.pretax if y.pretax and y.pretax > 0 and y.taxes is not None else None
        out.append(
            {
                "end": y.end,
                "revenue": round(y.revenue, 2),
                "ebit_margin": _r(y.ebit / rev if rev else None),
                "da_pct": _r(y.da / rev if rev else None),
                "capex_pct": _r(y.capex / rev if rev else None),
                "wc_pct": _r(y.wc_level / rev if rev and y.wc_level is not None else None),
                "tax_rate": _r(tax),
            }
        )
    return out


def reach(price: float, fair: float, imp: _val.Implied) -> str:
    """How far the price sits from the company's own record, one lever at a time:

    - `below value`: the price is at or under the base case, so it asks for nothing new;
    - `within history`: the margin or the growth the price needs is no more than the company's best
      of the last five fiscal years (either lever alone gets there);
    - `beyond history`: neither lever alone gets there without beating the company's own best.
    """
    if price <= fair:
        return "below value"
    margin_ok = imp.margin is not None and imp.margin <= imp.best_margin
    growth_ok = imp.growth_edge is None and imp.best_growth is not None and imp.growth <= imp.best_growth
    return "within history" if margin_ok or growth_ok else "beyond history"


# ----------------------------------------------------------------------------------------- the build


def risk_free(as_of: str) -> tuple[float, str]:
    """(rate, where it came from): the 10-year Treasury par yield of the last curve before the
    session (`core.treasury`, 2026-10-28), else the suite's declared rate when no file is on disk."""
    read = _treasury.yield_before(as_of, "10y")
    if read is None:
        return _val_fallback_rf(), "incomerules.RATE (no Treasury file)"
    day, pct = read
    return pct / 100.0, f"10-year Treasury {day.isoformat()}"


def _val_fallback_rf() -> float:
    from cherrypick.core import incomerules

    return incomerules.RATE


def rule(
    rf: float, rf_source: str, erp: tuple[float, str] | None, ratings_as_of: str | None
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "forecast_years": _val.FORECAST_YEARS,
        "terminal_growth": _val.TERMINAL_GROWTH,
        "risk_free": rf,
        "risk_free_source": rf_source,
        "equity_premium": erp[0] if erp else FALLBACK_ERP,
        "equity_premium_source": f"Damodaran implied ERP, {erp[1]}"
        if erp
        else "fallback (no Damodaran file)",
        "ratings_as_of": ratings_as_of,
        "history_years": _val.HISTORY_YEARS,
        "beta": f"OLS of {BETA_YEARS} years of weekly total returns on {INDEX}, Blume-adjusted {_val.BLUME}",
        "marginal_tax": _val.MARGINAL_TAX,
        "tax_bounds": _val.TAX_BOUNDS,
        "min_years": _val.MIN_YEARS,
        "min_spread": _val.MIN_SPREAD,
        "terminal_heavy": _val.TERMINAL_HEAVY,
        "terminal": (
            "stable growth: year-eleven reinvestment = growth / return on capital, return on capital = WACC"
        ),
        "grid": {"wacc": _val.WACC_STEPS, "growth": _val.GROWTH_STEPS},
        "stale_days": STALE_DAYS,
        "captive_finance": CAPTIVE_FINANCE,
        "edge": "none measured; a model estimate of value, never a ranking",
    }


def build(
    day: str | None = None,
    path: Path | None = None,
    names: list[str] | None = None,
    allowed: set[str] | None = None,
    facts: Path | None = None,
) -> dict[str, Any]:
    """Value the options-tradable names as of `day` and write `valuation/<day>.json`. `path` is the
    technicals history store the bars are read from (read-only); `facts` this package's store.

    The universe is the security list's liquid optionable names cut to those the liquidity verdict
    calls tradable (2026-10-28, the user: the fair-value tab is a screener, and no screener lists a
    name that would never be traded; `liquidity.py`). `allowed` stands in for the verdict (tests);
    None reads it. Names passed in `names` are valued as given."""
    as_of = day or date.today().isoformat()
    db = path or _tpaths.history_db()
    if not db.exists():
        return {"ok": False, "reason": f"no technicals history store at {db} (technicals history land)"}
    rows_sec, pick = securities.latest(as_of)
    kinds = {str(r["symbol"]).replace("/", "."): r.get("type") for r in rows_sec if r.get("symbol")}
    not_tradable = 0
    if names is None:
        if allowed is None:
            from cherrypick.technicals import liquidity

            allowed = liquidity.tradable_names() | liquidity.reference_tradable()
        listed, source = securities.liquid_optionable(as_of)
        names = [s for s in listed if s in allowed]
        not_tradable = len(listed) - len(names)
        source = f"{source}, options-tradable only"
    else:
        source = "given"
    sectors = {s: v[0] for s, v in _score.sector_sources().items()}
    broker = broker_readings(as_of)
    rf, rf_source = risk_free(as_of)
    erp = damodaran.erp(as_of)
    ratings = ratings_table()
    bars = store.connect(db)
    conn = secfacts.connect(facts)
    try:
        conn.executescript(statements.SCHEMA)
        conn.executescript(_consensus.SCHEMA)
        index_closes = closes_as_of(bars, INDEX, as_of)
        if not index_closes:
            return {"ok": False, "reason": f"no {INDEX} bars in the technicals history store"}
        rows = {}
        for sym in sorted(names):
            rows[sym] = value_one(
                conn,
                bars,
                sym,
                as_of,
                kinds.get(sym),
                sectors.get(sym),
                index_closes,
                broker.get(sym),
                rf,
                rf_source,
                erp,
                ratings,
            )
    finally:
        conn.close()
        bars.close()
    counts: dict[str, int] = {}
    for r in rows.values():
        key = r["state"] if r["state"] == "valued" else f"{r['state']}: {r['reason']}"
        counts[key] = counts.get(key, 0) + 1
    out = {
        "version": VERSION,
        "session": as_of,
        "built_at": datetime.now(UTC).isoformat(),
        "universe": source,
        "security_list": pick.get("day"),
        "rule": rule(rf, rf_source, erp, ratings[1]),
        "names": len(rows),
        "not_tradable": not_tradable,
        "states": counts,
        "rows": rows,
    }
    written = _jsonio.write_json_atomic(out_dir() / f"{as_of}.json", out, indent=None, default=None)
    return {"ok": True, "file": str(written), "session": as_of, "names": len(rows), "states": counts}
