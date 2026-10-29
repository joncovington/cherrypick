"""SEC filings landed and read back (secfacts.py): which tag a line reads, what a session could know,
and the lines the 2026-10-28 comparison turned on. Synthetic `companyfacts` documents only."""

from __future__ import annotations

import gzip
import json

import pytest

from cherrypick.fundamentals import paths, secfacts


def _fact(end, val, filed, start=None, form="10-K", fp="FY"):
    e = {"end": end, "val": val, "filed": filed, "form": form, "fp": fp, "accn": f"a-{filed}"}
    if start:
        e["start"] = start
    return e


def _doc(tags: dict[str, list], taxonomy="us-gaap", unit="USD") -> dict:
    return {"facts": {taxonomy: {t: {"units": {unit: es}} for t, es in tags.items()}}}


Y24, Y25 = ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")


def _company(**over) -> dict:
    """Two fiscal years of a plain company, filed each February; `over` replaces a tag's facts."""

    def d(tag, v24, v25):
        return {tag: [_fact(Y24[1], v24, "2025-02-10", Y24[0]), _fact(Y25[1], v25, "2026-02-10", Y25[0])]}

    def i(tag, v24, v25):
        return {tag: [_fact(Y24[1], v24, "2025-02-10"), _fact(Y25[1], v25, "2026-02-10")]}

    tags = {
        **d("Revenues", 1000.0, 1100.0),
        **d("OperatingIncomeLoss", 100.0, 120.0),
        **d("InterestExpenseNonoperating", 10.0, 12.0),
        **d("DepreciationDepletionAndAmortization", 50.0, 55.0),
        **d("PaymentsToAcquirePropertyPlantAndEquipment", 60.0, 66.0),
        **d("NetCashProvidedByUsedInOperatingActivities", 140.0, 150.0),
        **i("AccountsReceivableNetCurrent", 100.0, 110.0),
        **i("AccountsPayableCurrent", 80.0, 85.0),
        **i("CashAndCashEquivalentsAtCarryingValue", 200.0, 220.0),
        **i("ShortTermInvestments", 20.0, 30.0),
        **i("LongTermDebtNoncurrent", 300.0, 280.0),
        **i("DebtCurrent", 20.0, 25.0),
        **i("CommonStockSharesOutstanding", 100.0, 98.0),
    }
    tags.update(over)
    return _doc(tags)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTALS_DATA_DIR", str(tmp_path))
    c = secfacts.connect(paths.db())
    yield c
    c.close()


def _land(conn, doc, symbol="AAA"):
    conn.executemany(
        "INSERT OR IGNORE INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?)", secfacts.fact_rows(symbol, doc)
    )


def test_a_year_reads_its_lines_and_says_which_tag_each_came_from(conn):
    _land(conn, _company())
    y = secfacts.years(conn, "AAA")[-1]
    assert (y.end, y.revenue, y.ebit, y.interest, y.da, y.capex) == (
        "2025-12-31",
        1100.0,
        120.0,
        12.0,
        55.0,
        66.0,
    )
    assert y.cash == 250.0 and y.sources["cash"].endswith("ShortTermInvestments")
    assert y.debt == 305.0  # noncurrent plus the current portion
    assert y.wc_level == 25.0 and y.wc_change == -5.0  # working capital rose 20 to 25: cash absorbed
    assert y.sources["interest"] == "InterestExpenseNonoperating" and y.filed == "2026-02-10"


def test_a_session_sees_only_what_had_been_filed(conn):
    _land(conn, _company())
    assert [y.end for y in secfacts.years(conn, "AAA", as_of="2026-01-15")] == ["2024-12-31"]
    # A restatement is its own row, read from its own filing date on.
    restated = _fact(Y24[1], 990.0, "2026-02-10", Y24[0])
    _land(conn, _company(Revenues=[*_company()["facts"]["us-gaap"]["Revenues"]["units"]["USD"], restated]))
    assert secfacts.years(conn, "AAA", as_of="2026-01-15")[0].revenue == 1000.0
    assert secfacts.years(conn, "AAA")[0].revenue == 990.0


def test_a_missing_line_falls_down_its_chain_or_is_derived(conn):
    doc = _company(
        OperatingIncomeLoss=[],  # BMY files no operating income
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=[
            _fact(Y25[1], 90.0, "2026-02-10", Y25[0])
        ],
        LongTermDebtNoncurrent=[],  # AAL moved to LongTermDebt, which includes the current portion
        LongTermDebt=[_fact(Y25[1], 290.0, "2026-02-10")],
        ShortTermBorrowings=[_fact(Y25[1], 5.0, "2026-02-10")],
    )
    _land(conn, doc)
    y = secfacts.years(conn, "AAA")[-1]
    assert y.ebit == 102.0 and y.sources["ebit"] == "derived: pretax + interest"
    assert y.debt == 295.0 and y.sources["debt"] == "LongTermDebt + ShortTermBorrowings"


def test_d_and_a_is_the_filing_s_own_line_not_content_amortisation(conn):
    """NFLX's case: the filing's D&A is small; content amortisation is never one of the tags read."""
    doc = _company(
        DepreciationDepletionAndAmortization=[_fact(Y25[1], 0.33, "2026-02-10", Y25[0])],
        CostOfServicesAmortization=[_fact(Y25[1], 16.4, "2026-02-10", Y25[0])],
    )
    _land(conn, doc)
    assert secfacts.years(conn, "AAA")[-1].da == 0.33


def test_money_is_read_in_dollars_only(conn):
    doc = _company()
    doc["facts"]["us-gaap"]["Revenues"]["units"]["EUR"] = [_fact(Y25[1], 9e9, "2026-02-10", Y25[0])]
    _land(conn, doc)
    assert secfacts.years(conn, "AAA")[-1].revenue == 1100.0


def test_a_line_the_filing_lacks_comes_from_dolt_and_a_disagreement_is_kept(conn):
    from cherrypick.fundamentals import statements

    conn.executescript(statements.SCHEMA)
    _land(conn, _company(PaymentsToAcquirePropertyPlantAndEquipment=[]))
    # Dolt's year ends on the month end; the filing's on a 52/53-week Saturday would match too.
    conn.execute(
        "INSERT INTO statements_annual (symbol, period_end, revenue, ebit, da, capex, cash, shares, debt_lt) "
        "VALUES ('AAA', '2025-12-31', 1100, 120, 900, 70, 250, 98, 305)"
    )
    m = secfacts.merged_years(conn, "AAA")[-1]
    assert m.year.capex == 70 and m.year.sources["capex"].startswith("Dolt")
    assert m.year.da == 55.0 and m.disagree["da"] == (55.0, 900.0)  # the filing wins; the gap is kept


def test_a_year_the_filings_do_not_state_comes_from_dolt_whole(conn):
    """The filings may state fewer years than Dolt (tags a company changed, a short XBRL history):
    each year they lack is Dolt's, so the model still sees five years."""
    from cherrypick.fundamentals import statements

    conn.executescript(statements.SCHEMA)
    _land(conn, _company())  # 2024 and 2025
    for end in ("2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"):
        conn.execute(
            "INSERT INTO statements_annual (symbol, period_end, revenue, ebit, da, capex, cash, shares) "
            "VALUES ('AAA', ?, 900, 90, 45, 45, 200, 100)",
            (end,),
        )
    ms = secfacts.merged_years(conn, "AAA")
    assert [m.year.end for m in ms] == ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    assert ms[0].year.sources["revenue"] == "Dolt" and ms[-1].year.sources["revenue"] == "Revenues"
    assert ms[3].year.revenue == 1000.0  # 2024 is in both: the filing's figure


def test_quarterly_and_other_forms_never_make_a_fiscal_year(conn):
    doc = _company()
    doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _fact("2026-03-31", 280.0, "2026-04-30", "2026-01-01", form="10-Q", fp="Q1")
    )
    doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _fact("2026-06-30", 9e9, "2026-07-01", "2025-07-01", form="8-K")
    )
    _land(conn, doc)
    assert [y.end for y in secfacts.years(conn, "AAA")] == ["2024-12-31", "2025-12-31"]


def test_the_model_s_shape_keeps_only_whole_years(conn):
    _land(conn, _company(CommonStockSharesOutstanding=[_fact(Y25[1], 98.0, "2026-02-10")]))
    fys = secfacts.fiscal_years(conn, "AAA")
    assert [f.end for f in fys] == ["2025-12-31"]  # 2024 has no share count
    assert fys[0].nwc_change == -5.0 and fys[0].debt == 305.0


def test_the_filer_row_knows_a_foreign_filer_and_the_newest_report():
    sub = {
        "name": "Foreign Co",
        "sic": "3674",
        "sicDescription": "Semiconductors",
        "fiscalYearEnd": "1231",
        "filings": {
            "recent": {
                "form": ["6-K", "20-F", "6-K"],
                "reportDate": ["", "2025-12-31", ""],
                "filingDate": ["2026-05-01", "2026-04-15", "2026-03-01"],
                "accessionNumber": ["x", "y", "z"],
            }
        },
    }
    row = secfacts.filer_row("TSM", 1046179, sub)
    assert row[6] == 1 and row[7:10] == ("20-F", "2025-12-31", "2026-04-15")
    sub["filings"]["recent"]["form"][0] = "10-K"
    assert secfacts.filer_row("TSM", 1046179, sub)[6] == 0


def test_land_reads_the_fetched_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTALS_DATA_DIR", str(tmp_path / "t"))
    root = tmp_path / "sec"
    (root / "facts").mkdir(parents=True)
    (root / "submissions").mkdir()
    (root / "facts" / "1.json.gz").write_bytes(gzip.compress(json.dumps(_company()).encode()))
    (root / "submissions" / "1.json").write_text(json.dumps({"name": "A", "filings": {"recent": {}}}))
    (root / "fetched.json").write_text(json.dumps({"1": {"symbol": "AAA"}, "2": {"symbol": "BBB"}}))
    out = secfacts.land(root=root)
    assert out["ok"] and out["companies"] == 1 and out["missing"] == 1
    c = secfacts.connect(paths.db())
    assert secfacts.filer(c, "AAA")["cik"] == 1 and len(secfacts.years(c, "AAA")) == 2
