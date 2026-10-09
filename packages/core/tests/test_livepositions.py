"""Broker positions against the live ledgers (`cherrypick.core.livepositions`), safeguard 2(c)."""

from __future__ import annotations

from cherrypick.core import livepositions as lp


def pos(symbol, qty, direction, under="SPX", kind="Equity Option"):
    return {
        "symbol": symbol,
        "quantity": str(qty),
        "quantity_direction": direction,
        "underlying_symbol": under,
        "instrument_type": kind,
    }


def test_parse_occ_reads_root_date_right_and_strike():
    assert lp.parse_occ("SPXW  261008P05800000") == ("SPXW", "2026-10-08", "P", 5800.0)
    assert lp.parse_occ("XSP   261009C00652500") == ("XSP", "2026-10-09", "C", 652.5)
    assert lp.parse_occ("SPY") is None
    assert lp.parse_occ("") is None


def test_broker_legs_signs_nets_and_keeps_only_traded_underlyings():
    rows = [
        pos("SPXW  261008P05800000", 2, "Short"),
        pos("SPXW  261008P05795000", 1, "Long"),
        pos("SPXW  261008P05805000", 1, "Long"),
        pos("AAPL  261016C00250000", 1, "Long", under="AAPL"),  # not a live module's name
        pos("SPY", 100, "Long", under="SPY", kind="Equity"),
        pos("SPXW  261008C05900000", 0, "Zero"),
    ]
    assert lp.broker_legs(rows, ["SPX"]) == {
        ("SPX", "2026-10-08", "P", 5800.0): -2,
        ("SPX", "2026-10-08", "P", 5795.0): 1,
        ("SPX", "2026-10-08", "P", 5805.0): 1,
    }


def test_a_matching_book_has_no_diffs_and_two_modules_on_one_account_sum():
    legs = [
        {"underlying": "SPX", "expiry": "2026-10-08", "right": "P", "strike": 5800, "qty": -1},
        {"underlying": "SPX", "expiry": "2026-10-08", "right": "P", "strike": 5800, "qty": -1},
    ]
    broker = lp.broker_legs([pos("SPXW  261008P05800000", 2, "Short")], ["SPX"])
    assert lp.compare(lp.expected_legs(legs), broker) == []


def test_unrecorded_missing_and_quantity_are_each_named():
    expected = lp.expected_legs(
        [
            {"underlying": "SPX", "expiry": "2026-10-08", "right": "P", "strike": 5800, "qty": -2},
            {"underlying": "SPX", "expiry": "2026-10-08", "right": "C", "strike": 5900, "qty": -1},
        ]
    )
    broker = lp.broker_legs(
        [pos("SPXW  261008P05800000", 1, "Short"), pos("SPXW  261008P05790000", 1, "Long")], ["SPX"]
    )
    kinds = {(d["right"], d["strike"]): d["kind"] for d in lp.compare(expected, broker)}
    assert kinds == {("P", 5790.0): "unrecorded", ("C", 5900.0): "missing", ("P", 5800.0): "quantity"}


def test_only_a_disagreement_seen_twice_is_confirmed():
    d1 = {
        "underlying": "SPX",
        "expiry": "2026-10-08",
        "right": "P",
        "strike": 5790.0,
        "expected": 0,
        "broker": 1,
        "kind": "unrecorded",
    }
    d2 = dict(d1, broker=2, kind="unrecorded")
    assert lp.confirmed([], [d1]) == []
    assert lp.confirmed([d1], [d1]) == [d1]
    assert lp.confirmed([d1], [d2]) == []  # it changed: still settling, not yet a finding


def test_drop_expired_keeps_future_and_todays_until_the_close():
    legs = {
        ("SPX", "2026-10-07", "P", 5800.0): 1,
        ("SPX", "2026-10-08", "P", 5800.0): -1,
        ("SPX", "2026-10-09", "P", 5800.0): 1,
    }
    kept, dropped = lp.drop_expired(legs, "2026-10-08", after_close=False)
    assert dropped == 1 and ("SPX", "2026-10-08", "P", 5800.0) in kept
    kept, dropped = lp.drop_expired(legs, "2026-10-08", after_close=True)
    assert dropped == 2 and list(kept) == [("SPX", "2026-10-09", "P", 5800.0)]
