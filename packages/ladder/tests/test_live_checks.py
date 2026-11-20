"""The pure half of the live checks: the split entries, which lots a split is due on, the reference
close across a split, and the positions comparison. No ledger, no broker."""

from datetime import date

import pytest

from cherrypick.ladder import live_checks

D = date(2026, 12, 1)


def entry(**kw):
    return {"fund": "tqqq", "ex_date": "2026-12-01", "to": 2, "for": 1, **kw}


# --------------------------------------------------------------------------- the split entries
def test_a_split_entry_reads_as_a_factor_and_a_fund_in_capitals():
    [s], problems = live_checks.parse_splits({"splits": [entry()]})
    assert problems == [] and (s.fund, s.ex_date, s.factor, s.ratio) == ("TQQQ", "2026-12-01", 2.0, "2:1")
    [r], _ = live_checks.parse_splits({"splits": [entry(to=1, **{"for": 20})]})
    assert r.factor == 0.05 and r.ratio == "1:20"


def test_no_splits_key_is_no_splits_and_not_a_problem():
    assert live_checks.parse_splits({}) == ([], [])
    assert live_checks.parse_splits({"splits": []}) == ([], [])


@pytest.mark.parametrize(
    "bad",
    [
        entry(fund=""),
        entry(ex_date="soon"),
        entry(ex_date=None),
        entry(to=0),
        entry(to=True),
        entry(to="2"),
        entry(**{"for": -1}),
        entry(to=1, **{"for": 1}),
        "TQQQ 2:1",
    ],
)
def test_an_entry_it_cannot_use_is_a_problem_not_a_skipped_entry(bad):
    splits, problems = live_checks.parse_splits({"splits": [bad]})
    assert splits == [] and len(problems) == 1


def test_a_repeated_split_and_a_non_list_are_problems():
    splits, problems = live_checks.parse_splits({"splits": [entry(), entry()]})
    assert len(splits) == 1 and "repeats" in problems[0]
    assert live_checks.parse_splits({"splits": {"fund": "TQQQ"}})[1]


def test_splits_come_back_in_ex_date_order():
    out, _ = live_checks.parse_splits({"splits": [entry(ex_date="2027-01-05"), entry()]})
    assert [s.ex_date for s in out] == ["2026-12-01", "2027-01-05"]


# --------------------------------------------------------------------------- which lots a split is due on
def lot(bought="2026-11-23", symbol="TQQQ", pid="a:TQQQ:2026-11-23:1"):
    return {"position_id": pid, "symbol": symbol, "buy_session": bought}


def test_a_split_is_due_on_a_lot_bought_before_it_went_ex_and_not_on_one_bought_after():
    splits, _ = live_checks.parse_splits({"splits": [entry()]})
    assert live_checks.due(splits, lot("2026-11-30"), D, set()) == splits
    assert live_checks.due(splits, lot("2026-12-01"), D, set()) == []  # bought on the ex-date: new terms
    assert live_checks.due(splits, lot("2026-12-02"), date(2026, 12, 3), set()) == []


def test_a_split_not_yet_ex_is_not_due_and_another_fund_is_not_touched():
    splits, _ = live_checks.parse_splits({"splits": [entry()]})
    assert live_checks.due(splits, lot("2026-11-23"), date(2026, 11, 30), set()) == []
    assert live_checks.due(splits, lot(symbol="SOXL"), D, set()) == []


def test_a_split_already_applied_to_a_lot_is_not_due_again():
    splits, _ = live_checks.parse_splits({"splits": [entry()]})
    done = {("a:TQQQ:2026-11-23:1", "2026-12-01")}
    assert live_checks.due(splits, lot(), D, done) == []
    assert live_checks.due(splits, lot(pid="a:TQQQ:2026-11-23:2"), D, done) == splits


# --------------------------------------------------------------------------- the reference close
def reference(raw, bid, ex="2026-12-01"):
    splits, _ = live_checks.parse_splits({"splits": [entry(ex_date=ex)]})
    return live_checks.restated_reference(
        raw, splits, "TQQQ", bid=bid, prev_session=date(2026, 11, 30), today=D
    )


def test_a_close_still_on_the_old_terms_is_restated_by_the_factor():
    assert reference(100.0, 49.0) == 50.0


def test_a_close_the_exchange_already_restated_is_left_alone():
    assert reference(50.0, 49.0) == 50.0


def test_with_no_bid_the_close_across_a_split_is_unknown():
    assert reference(100.0, None) is None
    assert reference(100.0, 0) is None


def test_a_close_with_no_split_in_between_is_untouched_whatever_the_bid():
    assert reference(100.0, 49.0, ex="2026-11-30") == 100.0  # went ex before the close it follows
    assert reference(100.0, None, ex="2026-12-02") == 100.0  # not yet ex
    assert reference(None, 49.0) is None


def test_a_reverse_split_close_is_restated_upward():
    splits, _ = live_checks.parse_splits({"splits": [entry(to=1, **{"for": 10})]})
    got = live_checks.restated_reference(
        4.0, splits, "TQQQ", bid=39.0, prev_session=date(2026, 11, 30), today=D
    )
    assert round(got, 6) == 40.0


# --------------------------------------------------------------------------- a whole-number ratio
@pytest.mark.parametrize(
    "ledger, held, expected",
    [(10, 20, "2:1"), (10, 30, "3:1"), (10, 5, "1:2"), (30, 3, "1:10"), (10, 10, None), (10, 15, None),
     (0, 20, None), (10, 0, None), (10, 21, None)],
)  # fmt: skip
def test_split_like_names_a_whole_number_factor_only(ledger, held, expected):
    assert live_checks.split_like(ledger, held) == expected


# --------------------------------------------------------------------------- the comparison
def eq(symbol, qty):
    return {"symbol": symbol, "instrument_type": "Equity", "underlying_symbol": symbol, "quantity": qty}


def test_matching_shares_pass_with_nothing_blocked():
    out = live_checks.cross_check({"TQQQ": 10}, [eq("TQQQ", 10)], ["TQQQ", "SOXL"])
    assert out["readable"] and out["blocked"] == {}
    assert out["funds"]["TQQQ"]["status"] == "match" and out["funds"]["SOXL"]["status"] == "match"


def test_a_shortfall_blocks_that_fund_only():
    out = live_checks.cross_check({"TQQQ": 10, "SOXL": 4}, [eq("TQQQ", 7), eq("SOXL", 4)], ["TQQQ", "SOXL"])
    assert list(out["blocked"]) == ["TQQQ"] and "shortfall" in out["blocked"]["TQQQ"]


def test_a_surplus_is_reported_and_blocks_nothing():
    out = live_checks.cross_check({"TQQQ": 10}, [eq("TQQQ", 15)], ["TQQQ"])
    assert out["funds"]["TQQQ"]["status"] == "surplus" and out["blocked"] == {}


def test_a_whole_number_ratio_is_a_suspected_split_and_blocks_either_way():
    up = live_checks.cross_check({"TQQQ": 10}, [eq("TQQQ", 20)], ["TQQQ"])
    down = live_checks.cross_check({"TQQQ": 10}, [eq("TQQQ", 5)], ["TQQQ"])
    assert up["funds"]["TQQQ"]["suspected_split"] == "2:1" and "suspected_split" in up["blocked"]["TQQQ"]
    assert down["funds"]["TQQQ"]["suspected_split"] == "1:2" and "suspected_split" in down["blocked"]["TQQQ"]


def test_options_and_other_instruments_are_not_shares():
    rows = [
        {"symbol": "TQQQ  261218C00050000", "instrument_type": "Equity Option", "underlying_symbol": "TQQQ",
         "quantity": 3.0},
        {"symbol": "TQQQ", "instrument_type": "Future", "underlying_symbol": "TQQQ", "quantity": 99.0},
        eq("TQQQ", 10),
    ]  # fmt: skip
    assert live_checks.equity_shares(rows, "TQQQ") == 10
    assert live_checks.cross_check({"TQQQ": 10}, rows, ["TQQQ"])["blocked"] == {}


def test_an_unreadable_read_blocks_every_fund_and_a_held_fund_outside_the_list():
    out = live_checks.cross_check({"OLD": 3}, None, ["TQQQ", "SOXL"])
    assert out["readable"] is False
    assert set(out["blocked"]) == {"TQQQ", "SOXL", "OLD"}
    assert set(out["blocked"].values()) == {"positions_unreadable"}


def test_an_empty_list_is_a_read_that_says_the_account_holds_nothing():
    out = live_checks.cross_check({"TQQQ": 10}, [], ["TQQQ"])
    assert out["readable"] and "shortfall" in out["blocked"]["TQQQ"]
