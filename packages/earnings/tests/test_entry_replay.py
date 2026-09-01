"""Phase 3 of the control-book plan: scoring a proposed entry screen over recorded outcomes.

The rule this module exists to carry, and the one most likely to erode: a name that was never traded
has no return. Widening the control lifts that only for names inside the new bar; everything outside
stays counts-and-symbols-only, forever. So `counterfactual` carries no P&L key AT ALL rather than a
null or a flag — a shape that cannot grow one by accident is the only version of that rule which
survives someone adding a field in a hurry.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from cherrypick.earnings import entry_replay as er

CONFIG = {
    "symbol_screen": {"winrate": "off", "iv_rv_ratio": "off", "market_cap": "off"},
    "strategy_defaults": {
        "min_avg_volume": 1_500_000, "near_miss_min_avg_volume": 1_000_000,
        "min_winrate": 0.5, "near_miss_min_winrate": 0.4,
        "min_iv_rv_ratio": 1.25, "near_miss_min_iv_rv_ratio": 1.0,
        "min_market_cap": 2_000_000_000, "near_miss_min_market_cap": 1_000_000_000,
        "min_combined_option_volume": 500, "near_miss_min_combined_option_volume": 200,
    },
}
STRICT = {c: "pass" for c in er.SOFT_CRITERIA}
WIDE = {c: "off" for c in er.SOFT_CRITERIA}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE entry_reviews (scan_date TEXT, symbol TEXT, selected INTEGER,"
        " reason TEXT, criteria_json TEXT);"
        "CREATE TABLE trades (order_id TEXT, symbol TEXT, strategy TEXT, opened_at REAL,"
        " pnl REAL, entry_cost REAL, exit_cost REAL, status TEXT);"
        "CREATE TABLE measurement_breaks (break_date TEXT, key TEXT, old_value TEXT, new_value TEXT);"
    )
    return c


def _criteria(**over):
    base = {"avg_volume": 5_000_000, "winrate": 0.7, "iv_rv_ratio": 1.5,
            "market_cap": 50_000_000_000, "combined_option_volume": 5000}
    base.update(over)
    return json.dumps(base)


def _review(conn, scan_date, symbol, selected, criteria=None, reason=""):
    conn.execute("INSERT INTO entry_reviews VALUES (?,?,?,?,?)",
                 (scan_date, symbol, int(selected), reason, criteria or _criteria()))
    conn.commit()


def _trade(conn, symbol, day, pnl, *, order_id="T1", status="closed"):
    epoch = time.mktime(time.strptime(f"{day} 15:45", "%Y-%m-%d %H:%M"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)",
                 (order_id, symbol, "iron_fly", epoch, pnl, 2.0, 1.0, status))
    conn.commit()


# --------------------------------------------------------------------------- the honesty rule


def test_a_name_that_was_never_traded_has_no_return(conn):
    """THE rule. `screen_report --what-if` refuses P&L for untraded candidates and this must too."""
    _review(conn, "2026-08-20", "AAA", selected=False, criteria=_criteria(avg_volume=100))

    out = er.replay(conn, WIDE, config=CONFIG)

    assert out["counterfactual"]["candidates"] == 1
    assert "net_pnl" not in out["counterfactual"], "a counterfactual return is a number nobody measured"
    assert not any("pnl" in k for k in out["counterfactual"]), out["counterfactual"].keys()


def test_an_admitted_name_that_WAS_traded_reports_its_real_return(conn):
    """The other half: widening the control lifts the restriction for names inside the new bar."""
    _review(conn, "2026-08-20", "AAA", selected=True)
    _trade(conn, "AAA", "2026-08-20", pnl=100.0)

    out = er.replay(conn, WIDE, config=CONFIG)

    assert out["measured"]["candidates"] == 1
    assert out["measured"]["closed"] == 1
    assert out["measured"]["net_pnl"] == 97.0  # 100 - 2 entry - 1 exit
    assert out["counterfactual"]["candidates"] == 0


def test_measured_and_counterfactual_never_mix(conn):
    """One report that pools measured returns with counterfactual counts is worse than two."""
    _review(conn, "2026-08-20", "TRADED", selected=True)
    _trade(conn, "TRADED", "2026-08-20", pnl=100.0)
    _review(conn, "2026-08-20", "NEVER", selected=False, criteria=_criteria(avg_volume=100))

    out = er.replay(conn, WIDE, config=CONFIG)

    assert out["measured"]["symbols"] == ["TRADED"]
    assert out["counterfactual"]["symbols"] == ["NEVER"]


# --------------------------------------------------------------------------- the join


def test_outcomes_join_through_epoch_seconds_not_sqlite_date(conn):
    """`trades.opened_at` is epoch seconds and a scan_date is a local calendar day. `date(x)` on the
    raw column returns NULL and the join silently finds nothing — the first version of this module
    matched 0 of 20 selected reviews before the conversion was added."""
    _review(conn, "2026-08-20", "AAA", selected=True)
    _trade(conn, "AAA", "2026-08-20", pnl=50.0)

    assert er.replay(conn, WIDE, config=CONFIG)["measured"]["trades"] == 1


def test_one_review_can_carry_several_trades(conn):
    """The scan is per symbol; each admitted strategy opens its own position."""
    _review(conn, "2026-08-20", "AAA", selected=True)
    _trade(conn, "AAA", "2026-08-20", pnl=10.0, order_id="T1")
    _trade(conn, "AAA", "2026-08-20", pnl=20.0, order_id="T2")

    out = er.replay(conn, WIDE, config=CONFIG)
    assert out["measured"]["candidates"] == 1 and out["measured"]["trades"] == 2


# --------------------------------------------------------------------------- the universe


def test_a_row_refused_outside_the_screen_is_not_claimable_by_any_proposal(conn):
    """A hard filter, a tier exclusion, a timeout — none of these is a screen opinion, so no screen
    change reaches them. Admitting them would invent a candidate."""
    _review(conn, "2026-08-20", "AAA", selected=False, reason="bid_ask_spread_too_wide")

    out = er.replay(conn, WIDE, config=CONFIG)

    assert out["universe"]["not_replayable"] == 1
    assert out["measured"]["candidates"] == 0 and out["counterfactual"]["candidates"] == 0


# --------------------------------------------------------------------------- levels over time


def test_the_screen_in_force_is_read_per_date_not_from_todays_config(conn):
    """The bug this caught. `symbol_screen_edge_gates_off` turned three gates from pass to off on
    2026-08-25, so a row scanned on 08-20 and refused by market cap was refused BY THE SCREEN —
    while today's levels say market cap is off, which would classify it as refused OUTSIDE the
    screen and quietly drop it from the universe."""
    conn.execute("INSERT INTO measurement_breaks VALUES (?,?,?,?)",
                 ("2026-08-25", er.SCREEN_BREAK_KEY,
                  "winrate=pass,iv_rv_ratio=pass,market_cap=pass",
                  "winrate=off,iv_rv_ratio=off,market_cap=off"))
    conn.commit()
    # Below the market-cap bar: screen-refused in July, not screened at all today.
    _review(conn, "2026-07-20", "AAA", selected=False, criteria=_criteria(market_cap=5_000_000))

    out = er.replay(conn, WIDE, config=CONFIG)

    assert out["universe"]["not_replayable"] == 0, "the July screen refused it; it IS replayable"
    assert out["counterfactual"]["candidates"] == 1
    assert out["screen_history"][0]["levels"]["market_cap"] == "pass"


def test_screen_history_is_empty_without_a_recorded_break(conn):
    out = er.replay(conn, WIDE, config=CONFIG)
    assert out["screen_history"] == []


# --------------------------------------------------------------------------- validation


def test_every_replay_carries_its_own_validation(conn):
    """calendars' exit-policy replay validates against the real books on every run. A replay nobody
    checks is a model of itself, and the failure it hides makes every answer wrong invisibly."""
    _review(conn, "2026-08-20", "AAA", selected=True)
    out = er.replay(conn, WIDE, config=CONFIG)
    assert "validation" in out and "reproduces" in out["validation"]


def test_validation_names_a_row_the_replay_cannot_reproduce(conn):
    """On the live ledger this surfaces five 2026-07-20..23 rows whose iv_rv_ratio sat between 1.00
    and 1.20 — below today's min of 1.25, above the near-miss bar. Levels are recoverable from
    measurement_breaks; the thresholds behind them are not journalled anywhere."""
    _review(conn, "2026-07-20", "AAA", selected=True, criteria=_criteria(iv_rv_ratio=1.10))

    checked = er.validate(conn, config={**CONFIG, "symbol_screen": STRICT})

    assert checked["ok"] is False
    assert checked["disagreements"][0]["soft_failures"] == ["iv_rv_ratio_below_minimum"]


def test_validation_reports_the_date_from_which_answers_are_verified(conn):
    """More useful than a failure count: it says which answers to trust, not only that some are not."""
    _review(conn, "2026-07-20", "BAD", selected=True, criteria=_criteria(iv_rv_ratio=1.10))
    _review(conn, "2026-08-20", "GOOD", selected=True)

    checked = er.validate(conn, config={**CONFIG, "symbol_screen": STRICT})

    assert checked["verified_from"] == "2026-08-20"


# --------------------------------------------------------------------------- fidelity


def test_the_criteria_list_mirrors_the_scanner():
    """A criterion that became configurable in the scanner and not here would be replayed at a bar
    nobody set. This module calls the scanner's own gate precisely so the two cannot drift."""
    from cherrypick.earnings import scanner

    assert er.SOFT_CRITERIA == scanner._SOFT_CRITERIA
