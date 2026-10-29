"""SEC filings' XBRL facts, landed beside the bars for the fair-value build (docs/fair-value-model-plan.md).

`scripts/fetch_sec_facts.py` keeps each company's filing index and its `companyfacts` file under
`paths.sec_dir()`. This module lands them into two tables of the package's store (`fundamentals.db`)
and reads fiscal years
back, network-free:

- `sec_facts`: one row per fact as a filing first stated it -- the tag, the period (`start` empty for a
  balance-sheet instant), the value and the date it was **filed**. A later filing that repeats a value
  adds nothing; one that restates it adds a row with its own date. A reader asking "as of" a session
  therefore sees exactly what had been filed by then: true point in time, where Dolt's statements
  needed a 45-day publication guess.
- `sec_filers`: one row per company from its filing index -- the CIK, the SIC code and its words, the
  fiscal year end, whether it files as a foreign private issuer (a 20-F or 40-F and no 10-K), and its
  newest periodic report (form, period, filing date).

**Which tag a line reads** is decided when a year is read, not when it lands (`FIELDS`): companies tag
the same line differently, and one company changes tags over the years (AAL's long-term debt moved
from `LongTermDebtNoncurrent` to `LongTermDebt` after 2012). Each field is a chain; a year takes the
first tag in the chain that has a value for that period, and says which (`SecYear.sources`).

Two lines the comparison of 2026-10-28 turned on:
- **D&A** is the company's own `DepreciationDepletionAndAmortization` (or its nearest tag), which
  excludes the amortisation of content and similar operating assets whose cash cost sits inside
  operating cash flow: NFLX's filing states $0.33B where Dolt's statements carried $16.8B.
- **Interest** reads `InterestExpenseNonoperating` before `InterestExpense`: since 2024 most filers
  moved to the first (BMY's $1.89B, which Dolt carried as zero).

Signs follow `cherrypick.core.valuation.FiscalYear`: capex positive (cash out), the working-capital
line negative when cash was absorbed.

    python -m cherrypick.fundamentals land sec      (lands every fetched company)
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cherrypick.core import valuation as _val

from . import db as _db
from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_facts (
    symbol TEXT NOT NULL, tag TEXT NOT NULL, unit TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL, value REAL NOT NULL,
    form TEXT, fp TEXT, filed TEXT NOT NULL, accession TEXT,
    PRIMARY KEY (symbol, tag, unit, start, end, filed, value));
CREATE INDEX IF NOT EXISTS sec_facts_period ON sec_facts (symbol, end);
CREATE TABLE IF NOT EXISTS sec_filers (
    symbol TEXT PRIMARY KEY, cik INTEGER NOT NULL, name TEXT, sic TEXT, sic_description TEXT,
    fiscal_year_end TEXT, foreign_filer INTEGER NOT NULL DEFAULT 0,
    last_form TEXT, last_period TEXT, last_filed TEXT, landed_at TEXT NOT NULL);
"""

#: The forms whose facts are read: annual and quarterly reports, US and foreign.
FORMS = ("10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A")
FOREIGN_FORMS = ("20-F", "20-F/A", "40-F", "40-F/A")
#: A duration this long is a fiscal year (52/53-week years and a short transition year's neighbours).
ANNUAL_DAYS = (330, 380)

# field -> (kind, tags in order). `D` a duration, `I` an instant. A tag may be `dei:` for the cover page.
FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": (
        "D",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    ),
    "ebit": ("D", ("OperatingIncomeLoss",)),
    "pretax": (
        "D",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
        ),
    ),
    "taxes": ("D", ("IncomeTaxExpenseBenefit",)),
    "interest": (
        "D",
        (
            "InterestExpenseNonoperating",
            "InterestExpense",
            "InterestExpenseDebt",
            "InterestAndDebtExpense",
            "InterestPaidNet",
        ),
    ),
    "da": (
        "D",
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
        ),
    ),
    "depreciation": ("D", ("Depreciation",)),
    "amortization": ("D", ("AmortizationOfIntangibleAssets",)),
    "capex": (
        "D",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForCapitalImprovements",
        ),
    ),
    "cfo": (
        "D",
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    "net_income": ("D", ("NetIncomeLoss",)),
    "wc_change": ("D", ("IncreaseDecreaseInOperatingCapital",)),
    # Loans a company originates or buys in a year: a manufacturer with a lending arm (GM's $36.7B in
    # 2025, a fifth of its revenue) runs a finance business inside it, which an operating DCF misprices.
    "finance_receivables_bought": (
        "D",
        (
            "PaymentsToAcquireFinanceReceivables",
            "PaymentsToAcquireLoansAndLeasesHeldForInvestment",
            "PaymentsToAcquireLoansReceivable",
        ),
    ),
    "receivables": ("I", ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent")),
    "inventory": ("I", ("InventoryNet",)),
    "payables": ("I", ("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent")),
    "accrued": ("I", ("AccruedLiabilitiesCurrent",)),
    "deferred_revenue": ("I", ("ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent")),
    "cash": (
        "I",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "Cash",
        ),
    ),
    "securities": (
        "I",
        (
            "MarketableSecuritiesCurrent",
            "ShortTermInvestments",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        ),
    ),
    # Convertible notes are many growth companies' only debt (DDOG, NET, ZS, SNOW, MDB, CRDO).
    "debt_noncurrent": (
        "I",
        (
            "LongTermDebtNoncurrent",
            "LongTermDebtAndCapitalLeaseObligations",
            "ConvertibleDebtNoncurrent",
            "ConvertibleNotesPayable",
            "LongTermNotesPayable",
            "SeniorNotes",
        ),
    ),
    "debt_current": (
        "I",
        (
            "DebtCurrent",
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "LongTermDebtCurrent",
            "ConvertibleDebtCurrent",
        ),
    ),
    "debt_total": ("I", ("LongTermDebt", "DebtLongtermAndShorttermCombinedAmount")),
    "short_borrowings": ("I", ("ShortTermBorrowings", "CommercialPaper")),
    "finance_lease": ("I", ("FinanceLeaseLiability",)),
    "finance_lease_noncurrent": ("I", ("FinanceLeaseLiabilityNoncurrent",)),
    "finance_lease_current": ("I", ("FinanceLeaseLiabilityCurrent",)),
    "operating_lease": ("I", ("OperatingLeaseLiability",)),
    "preferred": ("I", ("PreferredStockValue", "PreferredStockValueOutstanding")),
    "nci": ("I", ("MinorityInterest",)),
    "assets": ("I", ("Assets",)),
    "shares": ("I", ("CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding")),
    "diluted_shares": ("D", ("WeightedAverageNumberOfDilutedSharesOutstanding",)),
    "rsu_unvested": (
        "I",
        (
            "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsNonvestedNumber",
        ),
    ),
    "options": ("I", ("ShareBasedCompensationArrangementByShareBasedPaymentAwardOptionsOutstandingNumber",)),
    "options_strike": (
        "I",
        (
            "ShareBasedCompensationArrangementByShareBasedPaymentAwardOptionsOutstandingWeightedAverageExercisePrice",
        ),
    ),
}

TAGS = frozenset(t for _, chain in FIELDS.values() for t in chain)


def connect(path: Path | None = None) -> sqlite3.Connection:
    return _db.connect(path, SCHEMA)


# ------------------------------------------------------------------------------------------- landing


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def fact_rows(symbol: str, facts: dict) -> list[tuple]:
    """Every fact of a `companyfacts` document whose tag a field reads, from the report forms, once per
    (period, value): the first filing that stated it. A restated value is its own row."""
    out: dict[tuple, tuple] = {}
    for taxonomy, tags in (facts.get("facts") or {}).items():
        for tag, body in tags.items():
            name = tag if taxonomy == "us-gaap" else f"{taxonomy}:{tag}"
            if name not in TAGS:
                continue
            for unit, entries in (body.get("units") or {}).items():
                for e in entries:
                    if (
                        e.get("form") not in FORMS
                        or e.get("val") is None
                        or not e.get("end")
                        or not e.get("filed")
                    ):
                        continue
                    start = e.get("start") or ""
                    key = (name, unit, start, e["end"], float(e["val"]))
                    row = (
                        symbol,
                        name,
                        unit,
                        start,
                        e["end"],
                        float(e["val"]),
                        e.get("form"),
                        e.get("fp"),
                        e["filed"],
                        e.get("accn"),
                    )
                    if key not in out or row[8] < out[key][8]:
                        out[key] = row
    return list(out.values())


def filer_row(symbol: str, cik: int, sub: dict) -> tuple:
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    has_10k = any(f in ("10-K", "10-K/A", "10-KT") for f in forms)
    foreign = any(f in FOREIGN_FORMS for f in forms) and not has_10k
    last = next(
        (
            (forms[i], recent["reportDate"][i], recent["filingDate"][i])
            for i, f in enumerate(forms)
            if f in FORMS
        ),
        (None, None, None),
    )
    return (
        symbol,
        cik,
        sub.get("name"),
        str(sub.get("sic") or "") or None,
        sub.get("sicDescription"),
        sub.get("fiscalYearEnd"),
        int(foreign),
        *last,
        datetime.now(UTC).isoformat(),
    )


def land(path: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    """Land every fetched company: its facts and its filer row, replacing that company's rows."""
    root = root or paths.sec_dir()
    state_path = root / "fetched.json"
    if not state_path.exists():
        return {"ok": False, "reason": f"nothing fetched yet ({state_path}); run scripts/fetch_sec_facts.py"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    conn = connect(path)
    counts = {"companies": 0, "facts": 0, "missing": 0}
    try:
        for cik_s, meta in state.items():
            cik = int(cik_s)
            symbols = meta.get("symbols") or [meta["symbol"]]  # one company, several tickers
            facts_path = root / "facts" / f"{cik}.json.gz"
            sub_path = root / "submissions" / f"{cik}.json"
            if not facts_path.exists() or not sub_path.exists():
                counts["missing"] += 1
                continue
            doc = json.loads(gzip.decompress(facts_path.read_bytes()))
            sub = json.loads(sub_path.read_text(encoding="utf-8"))
            for sym in symbols:
                rows = fact_rows(sym, doc)
                with conn:
                    conn.execute("DELETE FROM sec_facts WHERE symbol = ?", (sym,))
                    conn.executemany("INSERT OR IGNORE INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                    conn.execute(
                        "INSERT OR REPLACE INTO sec_filers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        filer_row(sym, cik, sub),
                    )
                counts["companies"] += 1
                counts["facts"] += len(rows)
    finally:
        conn.close()
    return {"ok": True, **counts}


# ------------------------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class SecYear:
    """One fiscal year as the company's filings stated it by the session asked about. Money in dollars;
    a line the filings never stated is None. `sources` names the tag each line came from (or how it
    was derived); `filed` is the newest filing date among the year's lines."""

    end: str
    start: str
    revenue: float | None
    ebit: float | None
    pretax: float | None
    taxes: float | None
    interest: float | None
    da: float | None
    capex: float | None
    cfo: float | None
    wc_level: float | None
    wc_change: float | None
    cash: float | None
    debt: float | None
    finance_lease: float | None
    operating_lease: float | None
    preferred: float | None
    nci: float | None
    assets: float | None
    shares: float | None
    rsu_unvested: float | None
    options: float | None
    options_strike: float | None
    filed: str
    sources: dict[str, str] = field(default_factory=dict)
    finance_receivables_bought: float | None = None


def _facts(conn, symbol: str, as_of: str | None) -> dict[tuple[str, str, str], float]:
    """(tag, start, end) -> the value as of `as_of`: the newest filing on or before it (all, if None)."""
    # Dollars, share counts and dollars a share only: a foreign filer's euros would read as dollars.
    q = (
        "SELECT tag, start, end, value, filed FROM sec_facts "
        "WHERE symbol = ? AND unit IN ('USD', 'shares', 'USD/shares')"
    )
    args: list[Any] = [symbol]
    if as_of is not None:
        q += " AND filed <= ?"
        args.append(as_of)
    out: dict[tuple[str, str, str], tuple[float, str]] = {}
    for tag, start, end, value, filed in conn.execute(q + " ORDER BY filed", args):
        out[(tag, start, end)] = (value, filed)
    return out  # type: ignore[return-value]


def _pick(facts: dict, chain: tuple[str, ...], start: str | None, end: str) -> tuple[float, str, str] | None:
    """The first tag in `chain` with a value for the period: (value, tag, filed)."""
    for tag in chain:
        if start is None:  # an instant: the balance sheet at `end`, or the cover page soon after it
            hit = facts.get((tag, "", end))
            if hit is None and tag.startswith("dei:"):
                later = sorted(
                    (k[2], v) for k, v in facts.items() if k[0] == tag and k[1] == "" and k[2] >= end
                )
                hit = later[0][1] if later and _days(end, later[0][0]) <= 120 else None
        else:
            hit = facts.get((tag, start, end))
        if hit is not None:
            return hit[0], tag, hit[1]
    return None


def _wc(get) -> float | None:
    """Operating working capital: receivables and inventory, less payables, accrued liabilities and
    deferred revenue. None without receivables or payables (the two lines nearly every filer states)."""
    rec, pay = get("receivables"), get("payables")
    if rec is None or pay is None:
        return None
    return rec + (get("inventory") or 0.0) - pay - (get("accrued") or 0.0) - (get("deferred_revenue") or 0.0)


def years(conn, symbol: str, as_of: str | None = None) -> list[SecYear]:
    """Every fiscal year the filings state by `as_of`, oldest first. A fiscal year is an annual
    revenue duration (`ANNUAL_DAYS`) from a 10-K, 20-F or 40-F; its other lines read the same period."""
    facts = _facts(conn, symbol, as_of)
    periods = sorted(
        {
            (k[1], k[2])
            for k in facts
            if k[0] in FIELDS["revenue"][1] and k[1] and ANNUAL_DAYS[0] <= _days(k[1], k[2]) <= ANNUAL_DAYS[1]
        },
        key=lambda p: p[1],
    )
    # One period per fiscal year end: the longest-lived revenue tag's period wins a tie.
    by_end: dict[str, str] = {}
    for start, end in periods:
        by_end.setdefault(end, start)
    out: list[SecYear] = []
    prev_wc: float | None = None
    for end, start in sorted(by_end.items()):
        vals: dict[str, float | None] = {}
        src: dict[str, str] = {}
        filed: list[str] = []
        for name, (kind, chain) in FIELDS.items():
            hit = _pick(facts, chain, start if kind == "D" else None, end)
            vals[name] = None if hit is None else hit[0]
            if hit is not None:
                src[name] = hit[1]
                filed.append(hit[2])
        get = vals.get
        if vals["ebit"] is None and vals["pretax"] is not None and vals["interest"] is not None:
            vals["ebit"] = vals["pretax"] + vals["interest"]
            src["ebit"] = "derived: pretax + interest"
        if vals["da"] is None and (vals["depreciation"] is not None or vals["amortization"] is not None):
            vals["da"] = (vals["depreciation"] or 0.0) + (vals["amortization"] or 0.0)
            src["da"] = "derived: depreciation + amortization of intangibles"
        if vals["debt_noncurrent"] is not None:
            debt = vals["debt_noncurrent"] + (get("debt_current") or 0.0)
            src["debt"] = f"{src['debt_noncurrent']} + {src.get('debt_current', 'no current debt')}"
        elif vals["debt_total"] is not None:
            debt = vals["debt_total"] + (get("short_borrowings") or 0.0)
            src["debt"] = f"{src['debt_total']} + {src.get('short_borrowings', 'no short-term borrowings')}"
        else:
            debt = get("debt_current")
            if debt is not None:
                src["debt"] = src["debt_current"]
        lease = vals["finance_lease"]
        if lease is None and (
            vals["finance_lease_noncurrent"] is not None or vals["finance_lease_current"] is not None
        ):
            lease = (vals["finance_lease_noncurrent"] or 0.0) + (vals["finance_lease_current"] or 0.0)
        wc = _wc(get)
        wc_change = None
        if vals["wc_change"] is not None:
            wc_change = -vals["wc_change"]  # the tag is the increase; the cash flow is its negative
        elif wc is not None and prev_wc is not None:
            wc_change = -(wc - prev_wc)
            src["wc_change"] = "derived: change in operating working capital"
        prev_wc = wc
        cash = get("cash")
        if cash is not None and vals["securities"] is not None:
            cash += vals["securities"]
            src["cash"] = f"{src['cash']} + {src['securities']}"
        if vals["shares"] is None and vals["diluted_shares"] is not None:
            # A company with several share classes states no single total (META, V, NKE, UPS).
            vals["shares"] = vals["diluted_shares"]
            src["shares"] = "WeightedAverageNumberOfDilutedSharesOutstanding (no single total stated)"
        out.append(
            SecYear(
                end=end,
                start=start,
                revenue=get("revenue"),
                ebit=get("ebit"),
                pretax=get("pretax"),
                taxes=get("taxes"),
                interest=get("interest"),
                da=get("da"),
                capex=get("capex"),
                cfo=get("cfo"),
                wc_level=wc,
                wc_change=wc_change,
                cash=cash,
                debt=debt,
                finance_lease=lease,
                operating_lease=get("operating_lease"),
                preferred=get("preferred"),
                nci=get("nci"),
                assets=get("assets"),
                shares=get("shares"),
                rsu_unvested=get("rsu_unvested"),
                options=get("options"),
                options_strike=get("options_strike"),
                filed=max(filed) if filed else "",
                sources=src,
                finance_receivables_bought=get("finance_receivables_bought"),
            )
        )
    return out


#: The lines Dolt's statements may fill when the filings do not state them, and are compared on.
DOLT_LINES = ("revenue", "ebit", "pretax", "taxes", "interest", "da", "capex", "cash", "debt", "shares")
#: Dolt's period ends are month ends; a 52/53-week year ends up to a week before (AMD's 2025-12-27).
SAME_YEAR_DAYS = 10
#: Lines further apart than this between the filing and Dolt are recorded (the filing is used).
DISAGREE = 0.10


@dataclass(frozen=True)
class MergedYear:
    """A fiscal year from the filings, any line they lack filled from Dolt's statements for the same
    year, and the lines where the two disagree (`disagree`: line -> (filing, Dolt))."""

    year: SecYear
    disagree: dict[str, tuple[float, float]] = field(default_factory=dict)


def merged_years(conn, symbol: str, as_of: str | None = None) -> list[MergedYear]:
    """The filings' years with Dolt as the fallback and the cross-check, oldest first. A fiscal year the
    filings do not state (one their tags miss, or a company they give no years for at all: a new holding
    company, XOM on 2026-10-28) reads Dolt's year, each line sourced "Dolt"."""
    dolt = _dolt_years(conn, symbol, as_of)
    secs = years(conn, symbol, as_of)
    out: list[MergedYear] = []
    used: set[str] = set()
    for y in secs:
        d = next((x for x in dolt if abs(_days(x.end, y.end)) <= SAME_YEAR_DAYS), None)
        if d is None:
            out.append(MergedYear(y))
            continue
        used.add(d.end)
        fills: dict[str, Any] = {}
        src = dict(y.sources)
        disagree = {}
        for line in DOLT_LINES:
            mine, theirs = getattr(y, line), getattr(d, line)
            if mine is None and theirs is not None:
                fills[line] = theirs
                src[line] = "Dolt (not stated in the filing)"
            elif mine is not None and theirs is not None and max(abs(mine), abs(theirs)) > 0:
                if abs(mine - theirs) > DISAGREE * max(abs(mine), abs(theirs)):
                    disagree[line] = (mine, theirs)
        if y.wc_change is None and d.nwc_change is not None:
            fills["wc_change"] = d.nwc_change
            src["wc_change"] = "Dolt (not stated in the filing)"
        out.append(MergedYear(dataclasses.replace(y, **fills, sources=src), disagree))
    for d in dolt:
        if d.end in used or any(abs(_days(d.end, y.end)) <= SAME_YEAR_DAYS for y in secs):
            continue
        out.append(
            MergedYear(
                SecYear(
                    end=d.end,
                    start="",
                    revenue=d.revenue,
                    ebit=d.ebit,
                    pretax=d.pretax,
                    taxes=d.taxes,
                    interest=d.interest,
                    da=d.da,
                    capex=d.capex,
                    cfo=None,
                    wc_level=None,
                    wc_change=d.nwc_change,
                    cash=d.cash,
                    debt=d.debt,
                    finance_lease=None,
                    operating_lease=None,
                    preferred=None,
                    nci=None,
                    assets=None,
                    shares=d.shares,
                    rsu_unvested=None,
                    options=None,
                    options_strike=None,
                    filed="",
                    sources={line: "Dolt" for line in DOLT_LINES},
                )
            )
        )
    return sorted(out, key=lambda m: m.year.end)


def _dolt_years(conn, symbol: str, as_of: str | None) -> list[SimpleNamespace]:
    """Dolt's annual rows, partial ones included (a fallback fills one line at a time), published by
    `as_of` under the statements' own 45-day rule."""
    from . import statements

    if not _has_table(conn, "statements_annual"):
        return []
    cutoff = None if as_of is None else statements.published_by(as_of)
    out = []
    for r in conn.execute(
        f"SELECT {', '.join(statements.COLUMNS)} FROM statements_annual WHERE symbol = ? ORDER BY period_end",
        (symbol,),
    ):
        d = dict(zip(statements.COLUMNS, r, strict=True))
        if cutoff is not None and d["period_end"] > cutoff:
            continue
        lt, cur = d["debt_lt"], d["debt_current"]
        out.append(
            SimpleNamespace(
                end=d["period_end"],
                revenue=d["revenue"],
                ebit=d["ebit"],
                pretax=d["pretax"],
                taxes=d["taxes"],
                interest=d["interest"],
                da=d["da"],
                capex=d["capex"],
                cash=d["cash"],
                shares=d["shares"],
                debt=None if lt is None and cur is None else (lt or 0.0) + (cur or 0.0),
                nwc_change=d["nwc_change"],
            )
        )
    return out


def _has_table(conn, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        is not None
    )


def fiscal_years(conn, symbol: str, as_of: str | None = None) -> list[_val.FiscalYear]:
    """The filings' years in the shape `core.valuation` reads today: only years with every line the
    model needs. The fuller `SecYear` carries the rest for the model's next version."""
    out = []
    for y in years(conn, symbol, as_of):
        need = (y.revenue, y.ebit, y.da, y.capex, y.cash, y.shares)
        if any(v is None for v in need):
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
            )
        )
    return out


def latest_period(conn, symbol: str, as_of: str | None = None) -> tuple[str, str] | None:
    """(the newest period end any report states revenue for, its filing date) by `as_of`: the
    company's latest results, quarterly included. None when the filings state no revenue."""
    q = "SELECT end, MIN(filed) FROM sec_facts WHERE symbol = ? AND tag IN ({}) AND start <> ''".format(
        ",".join("?" * len(FIELDS["revenue"][1]))
    )
    args: list[Any] = [symbol, *FIELDS["revenue"][1]]
    if as_of is not None:
        q += " AND filed <= ?"
        args.append(as_of)
    row = conn.execute(q + " GROUP BY end ORDER BY end DESC LIMIT 1", args).fetchone()
    return None if row is None else (row[0], row[1])


def filer(conn, symbol: str) -> dict | None:
    row = conn.execute("SELECT * FROM sec_filers WHERE symbol = ?", (symbol,)).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM sec_filers LIMIT 0").description]
    return dict(zip(cols, row, strict=True))
