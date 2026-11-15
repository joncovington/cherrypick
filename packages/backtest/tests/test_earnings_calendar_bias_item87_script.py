"""Item 87's script (earnings_calendar_bias_item87.py): invented chains and bars only, no store read."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as cal

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "earnings_calendar_bias_item87",
    Path(__file__).resolve().parents[1] / "scripts" / "earnings_calendar_bias_item87.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_rules_are_declared_and_the_paths_are_item_86s():
    assert kit_rule.check_rule(m.RULE) and m.RULE["delta"] is True and m.RULE["t_bar"] >= 2.39
    assert m.RULE["splits"] == m.m86.RULE["splits"]
    assert m.EVENT_RULE["beats_neutral"] and not m.EVENT_RULE["quiet_beats_unselected"]
    assert m.ARMS == ("trend", "skew", "prior")
    assert [s["step"] for s in m.CHAIN] == ["plan", "study"]


def _bars(end: date, closes: list[float]) -> dict:
    """`closes` oldest first, ending at `end`."""
    out, d = {}, end
    for c in reversed(closes):
        out[d] = (c, c, c, c)
        d = cal.previous_trading_day(d)
    return out


def test_trend_reads_the_close_against_its_fifty_session_mean():
    end = date(2024, 4, 23)
    assert m.trend_lean(_bars(end, [100.0] * 49 + [110.0]), end) == m.BULL
    assert m.trend_lean(_bars(end, [100.0] * 49 + [90.0]), end) == m.BEAR
    assert m.trend_lean(_bars(end, [100.0] * 30), end) is None


def test_skew_is_contrarian_against_the_names_own_median():
    earlier = [-0.05, -0.06, -0.04, -0.05]
    assert m.skew_lean(-0.10, earlier) == m.BULL  # more put-rich than usual: bullish
    assert m.skew_lean(0.00, earlier) == m.BEAR
    assert m.skew_lean(-0.10, earlier[:3]) is None


def test_prior_reactions_take_the_majority_of_the_last_four_and_skip_a_tie():
    assert m.prior_lean([-0.1, 0.02, 0.03, -0.01, 0.04]) == m.BULL
    assert m.prior_lean([0.02, -0.03, 0.01, -0.01]) is None
    assert m.prior_lean([0.02, 0.03]) is None


def _chain(session: str, expiry: str, calls: dict, puts: dict) -> Chain:
    rows = [(k, "CALL", b, a) for k, (b, a) in calls.items()] + [
        (k, "PUT", b, a) for k, (b, a) in puts.items()
    ]
    return Chain(rows, session, expiry)


def test_a_bullish_lean_buys_the_call_calendar_half_an_expected_move_above_spot():
    calls = {95.0: (6.4, 6.6), 100.0: (2.9, 3.1), 105.0: (1.0, 1.1), 110.0: (0.3, 0.4)}
    puts = {95.0: (0.9, 1.0), 100.0: (2.9, 3.1), 105.0: (6.0, 6.2), 110.0: (10.5, 10.7)}
    f = _chain("2024-04-23", "2024-05-17", calls, puts)
    b = _chain(
        "2024-04-23",
        "2024-08-16",
        {k: (x + 3, y + 3) for k, (x, y) in calls.items()},
        {k: (x + 3, y + 3) for k, (x, y) in puts.items()},
    )
    assert abs(m.expected_move(f, 100.0) - 0.85 * 6.0) < 1e-9
    assert m.lean_strike(f, b, m.BULL, 100.0) == ("C", 105.0)  # 100 + 2.55, nearest listed
    assert m.lean_strike(f, b, m.BEAR, 100.0) == ("P", 95.0)


def test_signed_reaction_moves_read_only_earlier_reports():
    report = date(2024, 4, 24)
    e31 = m.m86.e31
    rows = [(date(2024, 1, 24), e31.AFTER_CLOSE), (report, e31.AFTER_CLOSE)]
    e = e31.entry_session(date(2024, 1, 24), "after_close")
    r = e31.reaction_session(date(2024, 1, 24), "after_close")
    bars = {e: (0, 0, 0, 100.0), r: (0, 0, 0, 104.0)}
    moves = m.reaction_moves(rows, report, bars)
    assert len(moves) == 1 and abs(moves[0] - 0.04) < 1e-12
