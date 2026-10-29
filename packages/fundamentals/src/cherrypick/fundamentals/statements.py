"""Dolt's annual financial statements, landed beside the bars for the fair-value build.

The `earnings` clone states each company's annual income statement, cash-flow statement and three
balance-sheet tables by period end (`period = 'Year'`, from 2012, ~9,700 names). The fair-value build
(`valuation.py`) needs the five lines a DCF runs on -- revenue, operating profit, depreciation,
capital spending, the working-capital change -- plus debt, cash and shares; the five tables are
joined on `(act_symbol, date)` into one `statements_annual` row per fiscal year, replaced wholesale
like `fundamentals.quarters`.

**Point in time**: Dolt keeps period ends, not filing dates, so a year counts only
`fundamentals.PUBLICATION_LAG` days after it ends (the same rule the fundamentals score uses). Signs
follow `cherrypick.core.valuation.FiscalYear`: capex positive (cash out), the working-capital line
as the cash-flow statement signs it (negative when cash was absorbed).

Landed by `python -m cherrypick.technicals history fundamentals` (the 06:25 job); read-only against
Dolt, local files only otherwise.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import valuation as _val
from cherrypick.technicals import fundamentals as _score
from cherrypick.technicals import land as _land

from . import db as _db

SCHEMA = """
CREATE TABLE IF NOT EXISTS statements_annual (
    symbol TEXT NOT NULL, period_end TEXT NOT NULL,
    revenue REAL, ebit REAL, da REAL, pretax REAL, taxes REAL, interest REAL, net_income REAL,
    cfo REAL, capex REAL, nwc_change REAL, debt_lt REAL, debt_current REAL, cash REAL,
    shares REAL, equity REAL,
    PRIMARY KEY (symbol, period_end));
"""

COLUMNS = (
    "symbol",
    "period_end",
    "revenue",
    "ebit",
    "da",
    "pretax",
    "taxes",
    "interest",
    "net_income",
    "cfo",
    "capex",
    "nwc_change",
    "debt_lt",
    "debt_current",
    "cash",
    "shares",
    "equity",
)

# One query, the five tables joined; Dolt's column names on the right.
QUERY = """
SELECT i.act_symbol, i.date,
       i.sales, i.income_after_depreciation_and_amortization, i.depreciation_and_amortization,
       i.pretax_income, i.income_taxes, i.interest_expense, i.net_income,
       c.net_cash_from_operating_activities, c.property_and_equipment, c.net_change_from_assets,
       l.long_term_debt, l.current_portion_long_term_debt, l.notes_payable,
       a.cash_and_equivalents, e.shares_outstanding, e.total_equity
FROM income_statement i
LEFT JOIN cash_flow_statement c ON c.act_symbol = i.act_symbol AND c.date = i.date AND c.period = 'Year'
LEFT JOIN balance_sheet_liabilities l ON l.act_symbol = i.act_symbol AND l.date = i.date AND l.period = 'Year'
LEFT JOIN balance_sheet_assets a ON a.act_symbol = i.act_symbol AND a.date = i.date AND a.period = 'Year'
LEFT JOIN balance_sheet_equity e ON e.act_symbol = i.act_symbol AND e.date = i.date AND e.period = 'Year'
WHERE i.period = 'Year'
"""


def connect(path: Path | None = None):
    return _db.connect(path, SCHEMA)


def row_from_dolt(r: tuple) -> tuple:
    """One `statements_annual` row from one joined Dolt row: the signs the DCF wants."""
    f = _land._f
    (
        sym,
        d,
        sales,
        ebit,
        da,
        pretax,
        taxes,
        interest,
        net_income,
        cfo,
        ppe,
        nwc,
        debt_lt,
        debt_cur,
        notes,
        cash,
        shares,
        equity,
    ) = r
    capex = None if ppe is None else abs(float(ppe))  # Dolt signs the purchase as an outflow
    debt_current = None if debt_cur is None and notes is None else (f(debt_cur) or 0.0) + (f(notes) or 0.0)
    return (
        sym,
        d.isoformat() if hasattr(d, "isoformat") else str(d),
        f(sales),
        f(ebit),
        f(da),
        f(pretax),
        f(taxes),
        f(interest),
        f(net_income),
        f(cfo),
        capex,
        f(nwc),
        f(debt_lt),
        debt_current,
        f(cash),
        f(shares),
        f(equity),
    )


def land(cfg: dict | None = None, path: Path | None = None) -> dict:
    """Copy every annual statement row from the `earnings` clone. Re-run safe: replaced wholesale."""
    cfg = {**_land.DEFAULTS, "earnings_db": "earnings", **(cfg or {})}
    started = time.monotonic()
    try:
        dolt = _land._connect(cfg, cfg["earnings_db"])
    except Exception as exc:  # noqa: BLE001 -- no server is a failed landing, reported, not a crash
        return {"ok": False, "reason": f"dolt unreachable: {type(exc).__name__}: {exc}"}
    conn = connect(path)
    try:
        cur = dolt.cursor()
        cur.execute(QUERY)
        rows = [row_from_dolt(r) for r in cur.fetchall()]
        marks = ", ".join("?" for _ in COLUMNS)
        conn.executemany(f"INSERT OR REPLACE INTO statements_annual VALUES ({marks})", rows)
        conn.commit()
    finally:
        dolt.close()
        conn.close()
    return {"ok": True, "statements": len(rows), "seconds": round(time.monotonic() - started)}


def published_by(as_of: str) -> str:
    """The last fiscal year end whose statements count on `as_of`."""
    return (date.fromisoformat(as_of) - timedelta(days=_score.PUBLICATION_LAG)).isoformat()


def years_from_rows(rows: list[tuple], as_of: str | None = None) -> list[_val.FiscalYear]:
    """`FiscalYear`s from `statements_annual` rows (in `COLUMNS` order), oldest first, only the years
    published by `as_of`. A year missing any line the DCF needs is left out."""
    cutoff = None if as_of is None else published_by(as_of)
    out = []
    for r in rows:
        d = dict(zip(COLUMNS, r, strict=True))
        if cutoff is not None and d["period_end"] > cutoff:
            continue
        needed = ("revenue", "ebit", "da", "capex", "nwc_change", "cash", "shares")
        if any(d[k] is None for k in needed):
            continue
        out.append(
            _val.FiscalYear(
                end=d["period_end"],
                revenue=d["revenue"],
                ebit=d["ebit"],
                da=d["da"],
                capex=d["capex"],
                nwc_change=d["nwc_change"],
                debt=(d["debt_lt"] or 0.0) + (d["debt_current"] or 0.0),
                cash=d["cash"],
                shares=d["shares"],
                pretax=d["pretax"],
                taxes=d["taxes"],
                interest=d["interest"],
            )
        )
    return sorted(out, key=lambda y: y.end)


def years(conn, symbol: str, as_of: str | None = None) -> list[_val.FiscalYear]:
    rows = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM statements_annual WHERE symbol = ? ORDER BY period_end",
        (symbol,),
    ).fetchall()
    return years_from_rows([tuple(r) for r in rows], as_of)
