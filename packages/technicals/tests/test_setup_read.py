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


def test_a_good_friday_monthly_on_thursday_the_14th_is_found():
    # Good Friday 2022 fell on the 15th, the third Friday, so April's monthly was Thursday the 14th.
    assert setup_read.cycle_monthly(date(2022, 2, 25)) == ("2022-04-14", 48)


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


STRIKES = [float(k) for k in range(80, 121, 5)]  # 80, 85, ... 120


def _ctx(iv=None, board=None, strikes=STRIKES):
    return {
        "cfg": CFG,
        "iv": {"XYZ": iv or {}},
        "board": {"XYZ": board} if board else {},
        "skate": None,
        "holds": {"pullback": 8},
        "chains": {"XYZ": {"2026-11-20": strikes}} if strikes else {},
    }


IV_RICH = {"iv_index": 0.40, "iv_rank": 70, "iv_percentile": 80, "hv_30": 25.0, "earnings_date": "2026-12-10"}
IV_CHEAP = {**IV_RICH, "iv_rank": 10, "iv_percentile": 15}


def test_rich_vol_on_a_long_leans_to_a_put_credit_spread_past_support():
    r = setup_read.read(_row(), _ctx(IV_RICH))
    assert r["volatility"]["regime"] == "rich" and r["volatility"]["iv_hv"] == pytest.approx(1.6)
    assert r["lean"]["structure"] == "put credit spread"
    assert r["lean"]["legs"] == ["sell the 95 put, past support 95.00 (-5.0%)"]
    below = setup_read.read(_row(support={"value": 93.4}), _ctx(IV_RICH))
    assert below["lean"]["legs"] == ["sell the 90 put, past support 93.40 (-6.6%)"]


def test_cheap_vol_on_a_long_leans_to_a_call_debit_spread_toward_the_target():
    r = setup_read.read(_row(), _ctx(IV_CHEAP))
    assert r["lean"]["structure"] == "call debit spread"
    # Sold at the last listed strike short of the target, so the target is the spread's full value.
    assert r["lean"]["legs"] == ["buy the 100 call, sell the 105 call, toward target 106.00 (+6.0%)"]
    assert r["lean"]["expiry"] == "2026-11-20"


def test_a_target_inside_one_strike_names_the_narrowest_listed_spread_and_says_so():
    # The GM read of 2026-10-30: close 87.40, target 87.86, strikes every 2.50. "Sell at 87.86" was
    # no strike at all.
    gm = [75.0, 77.5, 80.0, 82.5, 85.0, 87.5, 90.0, 92.5, 95.0]
    row = _row(last_close=87.4, target=87.86, support={"value": 83.42})
    r = setup_read.read(row, _ctx(IV_CHEAP, strikes=gm))
    assert r["lean"]["legs"][0] == "buy the 87.5 call, sell the 90 call, toward target 87.86 (+0.5%)"
    assert any("target 87.86 sits inside one strike" in c for c in r["lean"]["cautions"])
    rich = setup_read.read(row, _ctx({**IV_RICH, "earnings_date": None}, strikes=gm))
    assert rich["lean"]["legs"] == ["sell the 82.5 put, past support 83.42 (-4.5%)"]


def test_a_short_debit_spread_mirrors_the_long():
    row = _row(side="short", target=93.0, resistance={"value": 104.0})
    r = setup_read.read(row, _ctx(IV_CHEAP))
    assert r["lean"]["legs"] == ["buy the 100 put, sell the 95 put, toward target 93.00 (-7.0%)"]
    sold = setup_read.read(row, _ctx({**IV_RICH, "earnings_date": None}))
    assert sold["lean"]["legs"] == ["sell the 105 call, past resistance 104.00 (+4.0%)"]


def test_without_a_chain_listing_a_leg_says_it_is_a_price():
    r = setup_read.read(_row(), _ctx(IV_CHEAP, strikes=None))
    assert r["lean"]["legs"] == [
        "buy near the money, sell at target 106.00 (+6.0%) (no listed strikes on file: a price, not a strike)"
    ]
    assert r["lean"]["expiry"] is None


def test_chain_strikes_reads_the_newest_listing_on_or_before_the_day(tmp_path):
    def put(sub, day, doc):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        (tmp_path / sub / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")

    xyz = [{"expiration": "2026-11-20", "strikes": [[105, "c", "p"], [100, "c", "p"]]}]
    put("universe/chains", "2026-10-29", {"XYZ": xyz})
    put("universe/chains", "2026-10-31", {"XYZ": [{"expiration": "2026-11-20", "strikes": [[1, "c", "p"]]}]})
    put(
        "screener-greeks/chains",
        "2026-10-30",
        {"ABC": [{"expiration": "2026-11-20", "strikes": [[50, "c", "p"]]}]},
    )
    got = setup_read.chain_strikes("2026-10-30", tmp_path)
    assert got == {"XYZ": {"2026-11-20": [100.0, 105.0]}, "ABC": {"2026-11-20": [50.0]}}


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
    # 100 + 12.39: the last listed strike short of +1σ is 110.
    assert r["lean"]["legs"][0].startswith("buy the 100 call, sell the 110 call, toward +1σ 112.39 (+12.4%)")


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
