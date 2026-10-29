"""The annual statements landing (statements.py): the row assembly, its signs and the publication lag."""

from __future__ import annotations

from datetime import date

from cherrypick.fundamentals import statements

# One joined Dolt row in QUERY's column order: CVS FY2025 as Dolt states it (2026-10-27), in dollars.
DOLT_CVS_2025 = (
    "CVS",
    date(2025, 12, 31),
    402_067_000_000,  # sales
    4_660_000_000,  # income after D&A (EBIT)
    10_331_000_000,  # D&A
    2_136_000_000,  # pretax
    408_000_000,  # taxes
    3_119_000_000,  # interest
    1_768_000_000,  # net income
    10_639_000_000,  # CFO
    -2_832_000_000,  # property and equipment (an outflow)
    -1_474_000_000,  # net change from assets (cash absorbed)
    60_502_000_000,  # long-term debt
    4_068_000_000,  # current portion
    0,  # notes payable
    10_598_000_000,  # cash
    1_269_400_000,  # shares outstanding
    75_382_000_000,  # total equity
)


def test_the_row_flips_capex_to_cash_out_and_sums_the_current_debt():
    row = dict(zip(statements.COLUMNS, statements.row_from_dolt(DOLT_CVS_2025), strict=True))
    assert row["symbol"] == "CVS" and row["period_end"] == "2025-12-31"
    assert row["capex"] == 2_832_000_000.0
    assert row["nwc_change"] == -1_474_000_000.0  # the cash-flow sign, kept
    assert row["debt_current"] == 4_068_000_000.0
    assert row["shares"] == 1_269_400_000.0


def test_a_fiscal_year_carries_total_debt_and_the_dcf_signs():
    (y,) = statements.years_from_rows([statements.row_from_dolt(DOLT_CVS_2025)])
    assert y.debt == 64_570_000_000.0
    assert y.capex == 2_832_000_000.0 and y.nwc_change == -1_474_000_000.0
    assert y.ebit == 4_660_000_000.0 and y.interest == 3_119_000_000.0


def test_a_year_counts_only_after_the_publication_lag():
    row = statements.row_from_dolt(DOLT_CVS_2025)
    assert statements.years_from_rows([row], as_of="2026-01-31") == []  # 31 days after the year end
    assert len(statements.years_from_rows([row], as_of="2026-02-14")) == 1  # 45 days after
    assert statements.published_by("2026-02-14") == "2025-12-31"


def test_a_year_missing_a_line_the_dcf_needs_is_left_out_and_the_rest_are_sorted():
    newer = list(DOLT_CVS_2025)
    newer[1] = date(2026, 12, 31)
    holed = list(DOLT_CVS_2025)
    holed[1] = date(2024, 12, 31)
    holed[4] = None  # no D&A
    rows = [statements.row_from_dolt(tuple(r)) for r in (newer, DOLT_CVS_2025, holed)]
    years = statements.years_from_rows(rows)
    assert [y.end for y in years] == ["2025-12-31", "2026-12-31"]
