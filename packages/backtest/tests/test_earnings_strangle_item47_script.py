"""Hypothesis 47's study (scripts/earnings_strangle_item47.py): the expiry, the past moves, the selector,
the strikes and the fills. Invented chains and bars only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "earnings_strangle_item47", SCRIPTS / "earnings_strangle_item47.py"
)
s47 = importlib.util.module_from_spec(_spec)
sys.modules["earnings_strangle_item47"] = s47
_spec.loader.exec_module(s47)


def test_the_monthly_is_the_first_third_friday_at_least_fourteen_days_out():
    assert s47.monthly_expiry(date(2026, 10, 29)) == date(2026, 11, 20)  # the video's XOM trade
    assert s47.monthly_expiry(date(2026, 11, 9)) == date(2026, 12, 18)  # 11-20 is only 11 days out
    assert s47.monthly_expiry(date(2025, 3, 20)) == date(2025, 4, 17)  # Good Friday: the Thursday


def test_the_move_table_reads_close_and_extreme_and_skips_blank_timing_and_splits():
    rows = [
        (date(2024, 1, 30), "After market close"),  # entry 01-30, reaction 01-31
        (date(2024, 4, 25), None),  # blank timing: not read
        (date(2024, 7, 30), "After market close"),  # a 2:1 split between the closes
    ]
    bars = {
        date(2024, 1, 30): (100, 101, 99, 100.0),
        date(2024, 1, 31): (104, 106, 97, 103.0),
        date(2024, 7, 30): (100, 100, 100, 100.0),
        date(2024, 7, 31): (50, 51, 49, 50.0),
    }
    table = s47.move_table(rows, bars)
    assert len(table) == 1
    d, close, extreme = table[0]
    assert d == date(2024, 1, 30)
    assert abs(close - 0.03) < 1e-12 and abs(extreme - 0.06) < 1e-12  # the high, 6% over the entry close


def _moves(close_moves, extremes=None):
    extremes = extremes or close_moves
    return [
        (date(2021, 1 + 3 * i // 12, 1 + i % 28), c, x)
        for i, (c, x) in enumerate(zip(close_moves, extremes, strict=True))
    ]


def test_the_selector_needs_both_averages_beaten_and_four_earlier_reports():
    moves = _moves([0.02] * 6 + [0.05, 0.05], [0.03] * 6 + [0.08, 0.04])
    report = date(2026, 1, 1)
    sel = s47.selection(moves, report, em_pct=0.03)
    assert sel["width"] == 0.08  # the largest extreme of the last eight
    assert abs(sel["short_mean"] - 0.0275) < 1e-12 and abs(sel["long_mean"] - 0.0275) < 1e-12
    assert sel["rich"]
    assert not s47.selection(moves, report, em_pct=0.0275)["rich"]  # equal is not above
    assert not s47.selection(moves, report, em_pct=None)["rich"]
    assert s47.selection(moves[:3], report, em_pct=0.10) is None


def test_the_selector_fails_when_only_the_short_average_is_beaten():
    moves = _moves([0.10] * 8 + [0.01] * 8)  # the long mean 5.5%, the last eight 1%
    sel = s47.selection(moves, date(2026, 1, 1), em_pct=0.03)
    assert sel["short_mean"] < 0.03 < sel["long_mean"]
    assert not sel["rich"]


def test_the_expected_move_is_085_of_the_straddle_nearest_spot_with_both_sides_quoted():
    chain = {
        (100.0, "C"): (2.0, 2.2),
        (100.0, "P"): (1.9, 2.1),
        (99.0, "C"): (2.5, 2.7),
        (99.0, "P"): (0.0, 0.1),  # no bid: 99 is not a straddle strike
    }
    em = s47.expected_move(chain, 99.2)
    assert abs(em - 0.85 * 4.1 / 99.2) < 1e-12
    assert s47.expected_move({(99.0, "C"): (2.5, 2.7)}, 99.2) is None


def test_the_strikes_sit_at_or_beyond_the_width():
    strikes = [90.0, 92.5, 95.0, 97.5, 100.0, 102.5, 105.0, 107.5, 110.0]
    assert s47.strangle_strikes(strikes, 100.0, 0.05) == (95.0, 105.0)
    assert s47.strangle_strikes(strikes, 100.0, 0.06) == (92.5, 107.5)
    assert s47.strangle_strikes(strikes, 100.0, 0.20) is None


def test_the_fill_charges_slippage_and_fees_and_an_unquoted_exit_leg_pays_intrinsic_plus_its_spread():
    legs = [(95.0, "P"), (105.0, "C")]
    entry = {legs[0]: (0.90, 1.10), legs[1]: (0.90, 1.10)}  # credit $200 at mid
    calm = s47.price("XOM", legs, entry, {legs[0]: (0.30, 0.40), legs[1]: (0.30, 0.40)}, 100.0, 100.0)
    assert calm["credit"] == 200.0 and calm["exit_value"] == 70.0
    assert calm["working"]["net"] < 200.0 - 70.0  # slippage and fees come off
    assert calm["stress"]["net"] < calm["working"]["net"]
    jump = s47.price("XOM", legs, entry, {legs[0]: (0.01, 0.05)}, 110.0, 100.0)  # the call has no quote
    assert jump["exit_unquoted_legs"] == 1
    assert abs(jump["exit_value"] - (3.0 + 100.0 * (5.0 + 0.20))) < 1e-9
    assert jump["working"]["bp"] < 0


def _arm(trades, mean, t, pair_mean, pair_t, halves=(1.0, 1.0)):
    return {
        "trades": trades,
        "net": {"mean": mean, "t": t},
        "pair": {"mean": pair_mean, "t": pair_t},
        "net_by_time_half": list(halves),
        "net_by_name_half": list(halves),
        "stress_mean": mean,
        "without_best_five": mean,
    }


def test_the_verdict_follows_the_declaration():
    good = _arm(400, 5.0, 3.0, 4.0, 2.5)
    held = _arm(200, 1.0, 0.0, 1.0, 0.0)
    assert s47.verdict(good, held, unselected_mean=1.0)["verdict"] == "pass"
    assert s47.verdict(good, held, unselected_mean=6.0)["verdict"] == "fail"  # the selector adds nothing
    assert s47.verdict(_arm(400, 5.0, 3.0, 4.0, 1.5), held, 1.0)["verdict"] == "fail"  # theta, not the report
    assert s47.verdict(good, _arm(200, -1.0, 0.0, 1.0, 0.0), 1.0)["verdict"] == "fail"
    assert s47.verdict(_arm(100, 5.0, 3.0, 4.0, 2.5), held, 1.0)["verdict"] == "no verdict"
    assert s47.verdict(good, _arm(50, 1.0, 0.0, 1.0, 0.0), 1.0)["verdict"] == "no verdict"
