"""#1084: the stores' contract against the code, with no store present (CI)."""

from __future__ import annotations

import pytest

from cherrypick.backtest import storecontract as sc
from cherrypick.backtest.study import chains

CONTRACT = sc.load()


@pytest.mark.parametrize("store", sorted(sc.DDL))
def test_each_stores_ddl_in_code_creates_the_contracts_columns(store):
    # A column added in code without the contract regenerated (or the other way) fails here.
    want = {t: v["columns"] for t, v in CONTRACT[store].items()}
    assert sc.ddl_columns(sc.DDL[store]) == want


def test_the_contract_records_both_spellings_of_a_right():
    # The trap items 47 and 54 fell into, kept in view: the chain stores and the open-interest store
    # spell a right differently.
    assert sc.rights("eod_chains.db") == ("CALL", "PUT")
    assert sc.rights("intraday_chains.db") == ("CALL", "PUT")
    assert sc.rights("open_interest.db") == ("C", "P")


@pytest.mark.parametrize("store", ["eod_chains.db", "intraday_chains.db", "open_interest.db"])
def test_the_kit_chain_reads_every_right_spelling_the_stores_use(store):
    call, put = sc.rights(store)
    rows = [(100.0, call, 2.0, 2.2), (100.0, put, 2.0, 2.2), (95.0, put, 0.5, 0.6), (105.0, call, 0.5, 0.6)]
    c = chains.Chain(rows, "2024-02-09", "2024-03-15")
    assert set(c.by["C"]) == {100.0, 105.0} and set(c.by["P"]) == {95.0, 100.0}


def test_differences_name_a_moved_spelling_and_a_new_column():
    actual = {
        "open_interest.db": {
            **CONTRACT["open_interest.db"],
            "oi_contracts": {
                "columns": CONTRACT["open_interest.db"]["oi_contracts"]["columns"] + [["x", "TEXT"]],
                "codes": {"right": ["CALL", "PUT"]},
            },
        }
    }
    diffs = sc.differences({"open_interest.db": CONTRACT["open_interest.db"]}, actual)
    assert "open_interest.db.oi_contracts: columns differ" in diffs
    assert any("codes ['CALL', 'PUT'] against the contract's ['C', 'P']" in d for d in diffs)
