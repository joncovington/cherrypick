"""The read behind a setups-watchlist row (setup_read.py).

What these pin is what would mislead quietly: a regime called rich on one reading of two, a lean that
sells premium through a report, a counter-trend setup flagged as conflicting with its own design, and
a lean on a losing rule shown without its record.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cherrypick.technicals import setup_read

CFG = setup_read.settings(None)
DAY = date(2026, 10, 16)


def test_the_config_example_states_the_defaults():
    example = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
    assert setup_read.settings(example["setup_read"]) == setup_read.DEFAULTS


def test_the_regime_needs_rank_and_percentile_to_agree():
    assert setup_read.regime(60, 70, CFG) == "rich"
    assert setup_read.regime(60, 40, CFG) == "middling"  # one reading of two is not enough
    assert setup_read.regime(20, 25, CFG) == "cheap"
    assert setup_read.regime(20, 40, CFG) == "middling"
    assert setup_read.regime(None, 70, CFG) == "unread"


def test_the_cycle_monthly_is_the_users():
    assert setup_read.cycle_monthly(date(2026, 10, 6)) == ("2026-11-20", 45)
    assert setup_read.cycle_monthly(DAY) == ("2026-11-20", 35)  # 18 Dec is 63 days out


def test_atr_is_wilders_average_true_range():
    highs, lows, closes = [11.0] * 20, [9.0] * 20, [10.0] * 20
    assert setup_read.atr(highs, lows, closes) == pytest.approx(2.0)
    assert setup_read.atr(highs[:5], lows[:5], closes[:5]) is None


def _row(**over):
    row = {
        "symbol": "XYZ",
        "session": "2026-10-16",
        "setup": "pullback",
        "family": "pullback",
        "side": "long",
        "tested": None,
        "status": "open",
        "last_close": 100.0,
        "trend_1m": 2,
        "trend_6m": 3,
        "trend_agrees": True,
        "target": 106.0,
        "support": {"value": 95.0, "pct": -5.0},
        "resistance": {"value": 108.0, "pct": 8.0},
        "atr14": 2.0,
        "rule": "Enter on the dip.",
        "change": None,
    }
    return {**row, **over}


def _ctx(iv=None, board=None):
    return {
        "cfg": CFG,
        "iv": {"XYZ": iv or {}},
        "board": {"XYZ": board} if board else {},
        "skate": None,
        "holds": {"pullback": 8},
    }


IV_RICH = {"iv_index": 0.40, "iv_rank": 70, "iv_percentile": 80, "hv_30": 25.0, "earnings_date": "2026-12-10"}
IV_CHEAP = {**IV_RICH, "iv_rank": 10, "iv_percentile": 15}


def test_rich_vol_on_a_long_leans_to_a_put_credit_spread_past_support():
    r = setup_read.read(_row(), _ctx(IV_RICH))
    assert r["volatility"]["regime"] == "rich" and r["volatility"]["iv_hv"] == pytest.approx(1.6)
    assert r["lean"]["structure"] == "put credit spread"
    assert any("past support 95.00" in leg for leg in r["lean"]["legs"])


def test_cheap_vol_on_a_long_leans_to_a_call_debit_spread_toward_the_target():
    r = setup_read.read(_row(), _ctx(IV_CHEAP))
    assert r["lean"]["structure"] == "call debit spread"
    assert r["lean"]["legs"] == ["buy near the money, sell at 106.00 (+6.0%)"]


def test_a_report_inside_the_cycle_never_sells_premium():
    r = setup_read.read(_row(), _ctx({**IV_RICH, "earnings_date": "2026-11-05"}))
    assert r["volatility"]["earnings_in_cycle"] is True
    assert r["lean"]["structure"] == "call debit spread"
    assert any("earnings 2026-11-05" in c for c in r["lean"]["cautions"])


def test_a_losing_rule_carries_its_record_in_the_lean():
    r = setup_read.read(_row(), _ctx(IV_CHEAP))
    assert r["record"]["verdict"] == "worse than random"
    assert any("worse than random" in c for c in r["lean"]["cautions"])
    tested = setup_read.read(_row(setup="reversion", family="reversion", tested="mr-300m"), _ctx(IV_CHEAP))
    assert tested["record"]["edge_r"] > 0 and not any("tested" in c for c in tested["lean"]["cautions"])


def test_mean_reversion_is_counter_trend_by_design_and_only_the_board_votes():
    falling = _row(family="reversion", setup="reversion", trend_1m=-3, trend_6m=-2, trend_agrees=False)
    unread = setup_read.read(falling, _ctx(IV_CHEAP))["direction"]
    assert unread["counter_trend"] and unread["verdict"] == "unread"
    agrees = setup_read.read(falling, _ctx(IV_CHEAP, board={"side": "bullish", "count": 3, "tags": []}))[
        "direction"
    ]
    assert agrees["verdict"] == "agrees"


def test_a_trend_long_against_both_trends_and_the_board_conflicts():
    against = _row(family="trend", setup="trend", trend_1m=-2, trend_6m=-1, trend_agrees=False)
    r = setup_read.read(against, _ctx(IV_CHEAP, board={"side": "bearish", "count": 2, "tags": []}))
    assert r["direction"]["verdict"] == "conflicts"


def test_no_target_and_no_level_falls_back_to_the_expected_move():
    r = setup_read.read(_row(target=None, resistance=None), _ctx(IV_CHEAP))
    move = r["volatility"]["expected_move"]
    assert move["pct"] == pytest.approx(40 * (35 / 365) ** 0.5, abs=0.01)
    assert r["lean"]["legs"][0].startswith("buy near the money, sell near +1σ")


def test_levels_are_stated_in_atrs_and_sigmas():
    r = setup_read.read(_row(), _ctx(IV_CHEAP))
    sup = r["levels"]["support"]
    assert sup["atr"] == pytest.approx(-2.5)
    assert sup["sigma"] == pytest.approx(-5 / r["volatility"]["expected_move"]["dollars"], abs=0.01)


def test_no_iv_reading_leaves_the_structure_undecided():
    r = setup_read.read(_row(), _ctx(None))
    assert r["volatility"]["regime"] == "unread" and r["lean"]["structure"] is None


def test_hold_spans_count_sessions_between_entry_and_exit():
    doc = {
        "bars": {"date": ["d0", "d1", "d2", "d3"]},
        "setups": [
            {
                "id": "trend",
                "trades": [{"entry_date": "d0", "exit_date": "d3"}, {"entry_date": "d1", "exit_date": None}],
            }
        ],
        "tested": [],
    }
    assert setup_read.hold_spans(doc) == {"trend": [3]}
    assert setup_read.medians({"trend": [3, 9, 5]}) == {"trend": 5}
