"""Recording additions of 2026-11-05: the quotes each leg was priced from at entry and exit, and the
term-structure inputs behind the gate's ratio. Recording only: no test here changes a decision."""

import argparse
import json
import sqlite3
from datetime import date

import pytest

from cherrypick.earnings import db, db_paper, ledger_verbs, paper_loop, scanner

LEGS = [
    {"symbol": "AAA   260821C00100000", "action": "Sell to Open", "quantity": 1, "streamer_symbol": ".A"},
    {"symbol": "AAA   260821C00110000", "action": "Buy to Open", "quantity": 1, "streamer_symbol": ".B"},
]
QUOTES = {
    "AAA   260821C00100000": {"bid": 2.0, "ask": 2.2, "age_seconds": 3.5},
    "AAA   260821C00110000": {"bid": 0.5, "ask": 0.7},
}


# --- leg quotes ----------------------------------------------------------------------------------


def test_entry_quotes_ride_on_each_leg_and_leave_the_rest_alone():
    out = scanner.with_entry_quotes(LEGS, QUOTES, "stream")
    assert out[0]["entry_bid"] == 2.0 and out[0]["entry_ask"] == 2.2
    assert out[0]["entry_quote_source"] == "stream" and out[0]["entry_quote_age_seconds"] == 3.5
    assert out[1]["entry_quote_age_seconds"] is None  # a source that does not say: None, not zero
    assert out[0]["symbol"] == LEGS[0]["symbol"] and out[0]["streamer_symbol"] == ".A"


def test_a_leg_without_a_quote_records_none_never_zero():
    out = scanner.with_entry_quotes(LEGS, {}, "rest")
    assert out[0]["entry_bid"] is None and out[0]["entry_ask"] is None
    assert out[0]["entry_quote_source"] is None


def test_the_exit_record_leaves_out_a_settled_leg_and_an_unquoted_one():
    rec = scanner.exit_quote_record(LEGS, QUOTES, "rest", skip={LEGS[1]["symbol"]})
    assert rec == [
        {"symbol": LEGS[0]["symbol"], "bid": 2.0, "ask": 2.2, "source": "rest", "age_seconds": 3.5}
    ]
    assert scanner.exit_quote_record(LEGS, {}, "rest") == []


def _paper(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    return tmp_path / "paper_trades.db"


def test_close_position_stores_the_quotes_the_exit_was_priced_from(tmp_path, monkeypatch):
    path = _paper(tmp_path, monkeypatch)
    legs = scanner.with_entry_quotes(LEGS, QUOTES, "rest")
    spec = {
        "order_id": "T1",
        "strategy": "iron_fly",
        "symbol": "AAA",
        "expiration": "2026-08-21",
        "legs_json": json.dumps(legs),
        "entry_credit": 1.5,
        "arm": "strat_test",
        "quantity": 1,
    }
    db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps(spec)))
    trade = db_paper.cmd_get_open_positions(argparse.Namespace())["positions"][0]
    exit_quotes = {
        LEGS[0]["symbol"]: {"bid": 1.0, "ask": 1.2, "mid": 1.1, "age_seconds": 0.8},
        LEGS[1]["symbol"]: {"bid": 0.2, "ask": 0.3, "mid": 0.25, "age_seconds": 1.1},
    }
    monkeypatch.setattr(paper_loop.costs, "exit_costs", lambda *a, **k: {"total_cost": 1.0, "slippage": 0.1})
    now = paper_loop.datetime(2026, 8, 20, 10, 0, tzinfo=paper_loop.ET)
    snap = {"quotes": exit_quotes, "source": "stream"}
    assert paper_loop.close_position(trade, snap, "profit_target", {}, now)["ok"]

    conn = sqlite3.connect(path)
    entry_legs, exit_legs = conn.execute("SELECT legs_json, exit_legs_json FROM trades").fetchone()
    conn.close()
    assert json.loads(entry_legs)[0]["entry_bid"] == 2.0
    recorded = json.loads(exit_legs)
    assert [(r["bid"], r["ask"], r["source"], r["age_seconds"]) for r in recorded] == [
        (1.0, 1.2, "stream", 0.8),
        (0.2, 0.3, "stream", 1.1),
    ]


@pytest.mark.parametrize("mod", [db_paper, db])
def test_an_old_row_reads_null_and_is_never_backfilled(tmp_path, monkeypatch, mod):
    """A ledger built before the columns existed gains them on connect, with NULL in every old row."""
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "ledger.db")
    mod.cmd_init_db(argparse.Namespace())
    conn = sqlite3.connect(tmp_path / "ledger.db")
    conn.execute("ALTER TABLE trades DROP COLUMN exit_legs_json")
    for col in ("front_iv", "back_iv", "front_dte", "back_dte", "front_expiration", "back_expiration"):
        conn.execute(f"ALTER TABLE entry_reviews DROP COLUMN {col}")
    conn.execute(
        "INSERT INTO trades (order_id, strategy, symbol, expiration, legs_json, opened_at, closed_at) "
        "VALUES ('OLD', 'iron_fly', 'AAA', '2026-01-16', '[]', 1.0, 2.0)"
    )
    conn.execute("INSERT INTO entry_reviews (scan_date, symbol) VALUES ('2026-01-02', 'AAA')")
    conn.commit()
    conn.close()

    c = mod._conn()
    try:
        assert c.execute("SELECT exit_legs_json FROM trades WHERE order_id='OLD'").fetchone()[0] is None
        row = c.execute("SELECT * FROM entry_reviews").fetchone()
        for col in ("front_iv", "back_iv", "front_dte", "back_dte", "front_expiration", "back_expiration"):
            assert row[col] is None
    finally:
        c.close()


@pytest.mark.parametrize("mod", [db_paper, db])
def test_save_close_stores_exit_legs_json_on_both_ledgers(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "ledger.db")
    mod.cmd_init_db(argparse.Namespace())
    spec = {
        "order_id": "T1",
        "symbol": "AAA",
        "expiration": "2026-08-21",
        "legs_json": json.dumps(LEGS),
        "entry_credit": 1.0,
    }
    mod.cmd_save_trade(argparse.Namespace(data=json.dumps(spec)))
    rec = scanner.exit_quote_record(LEGS, QUOTES, "rest")
    mod.cmd_save_close(
        argparse.Namespace(
            data=json.dumps({"order_id": "T1", "exit_debit": 0.5, "pnl": 50.0, "exit_legs_json": rec})
        )
    )
    c = mod._conn()
    try:
        stored = c.execute("SELECT exit_legs_json FROM trades WHERE order_id='T1'").fetchone()[0]
    finally:
        c.close()
    assert json.loads(stored) == rec


# --- term-structure inputs -----------------------------------------------------------------------


def test_the_ratio_is_unchanged_and_the_inputs_come_back_with_it():
    ts = scanner.compute_expected_move_and_term_structure(
        3.0, 3.0, 0.60, 0.40, 100.0, date(2026, 8, 21), date(2026, 9, 18), today=date(2026, 8, 14)
    )
    assert ts["term_structure"] == pytest.approx((0.40 - 0.60) / 0.40)  # the ratio the gate measures
    assert (ts["front_iv"], ts["back_iv"], ts["front_dte"], ts["back_dte"]) == (0.60, 0.40, 7, 35)
    assert (ts["front_expiration"], ts["back_expiration"]) == ("2026-08-21", "2026-09-18")
    # What a replay computes from them: the per-day slope, a different number from the ratio.
    slope = (ts["back_iv"] - ts["front_iv"]) / (ts["back_dte"] - ts["front_dte"])
    assert slope == pytest.approx(-0.2 / 28)


def test_without_expirations_the_dte_inputs_are_none():
    ts = scanner.compute_expected_move_and_term_structure(3.0, 3.0, 0.6, 0.4, 100.0)
    assert ts["front_dte"] is None and ts["back_dte"] is None and ts["front_expiration"] is None


def test_a_strategy_criteria_dict_carries_the_term_inputs(monkeypatch):
    from cherrypick.earnings.strategies import iron_fly

    today = date.today()
    front, back = today.replace(year=today.year + 1), today.replace(year=today.year + 2)
    call = {"mid": 3.0, "iv": 0.6, "delta": 0.5, "bid": 2.9, "ask": 3.1}
    put = {"mid": 3.0, "iv": 0.6, "bid": 2.9, "ask": 3.1}
    monkeypatch.setattr(
        scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 100.0, "expirations": []}
    )
    monkeypatch.setattr(scanner, "select_front_expiration", lambda *a: (front, None))
    monkeypatch.setattr(scanner, "select_back_expiration", lambda *a: back)
    monkeypatch.setattr(
        scanner,
        "fetch_front_back_atm_entries",
        lambda *a: {"ok": True, "front_call": call, "front_put": put, "back_call": {**call, "iv": 0.4}},
    )
    monkeypatch.setattr(scanner, "fetch_liquidity_criteria", lambda *a: {})
    monkeypatch.setattr(scanner, "compute_winrate", lambda *a: {"ok": False})
    out = iron_fly.fetch_price_and_term_structure("AAA", front, "After market close", {})
    crit = out["criteria"]
    assert (crit["front_iv"], crit["back_iv"]) == (0.6, 0.4)
    assert crit["front_dte"] == (front - today).days and crit["back_dte"] == (back - today).days
    assert crit["front_expiration"] == str(front) and crit["back_expiration"] == str(back)


def test_the_entry_review_row_stores_the_term_inputs(tmp_path, monkeypatch):
    path = _paper(tmp_path, monkeypatch)
    criteria = {
        "term_structure": -0.5,
        "front_iv": 0.6,
        "back_iv": 0.4,
        "front_dte": 7,
        "back_dte": 35,
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-09-18",
    }
    spec = scanner.build_entry_review_spec(
        "2026-08-14", "AAA", "After market close", criteria, None, False, "x"
    )
    assert db_paper.cmd_save_entry_review(argparse.Namespace(data=json.dumps(spec)))["ok"]
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT front_iv, back_iv, front_dte, back_dte, front_expiration, back_expiration FROM entry_reviews"
    ).fetchone()
    conn.close()
    assert row == (0.6, 0.4, 7, 35, "2026-08-21", "2026-09-18")


def test_the_review_columns_and_values_stay_the_same_length():
    spec = {"scan_date": "2026-08-14", "symbol": "AAA"}
    assert len(ledger_verbs.entry_review_values(spec)) == len(ledger_verbs._ENTRY_REVIEW_COLUMNS)


# --- winrate backtest ----------------------------------------------------------------------------


def _closes(pre, reaction):
    return ({"date": date(2026, 1, 1), "close": pre}, {"date": date(2026, 1, 2), "close": reaction})


def _winrate(monkeypatch, dates, closes_fn, straddle_fn):
    monkeypatch.setattr(scanner, "fetch_historical_earnings_dates", lambda *a, **k: dates)
    monkeypatch.setattr(scanner, "pre_and_reaction_closes", closes_fn)
    monkeypatch.setattr(scanner, "fetch_atm_straddle_price", straddle_fn)
    return scanner.compute_winrate("AAA", {}, lookback_quarters=8)


def _straddle(mid):
    return lambda *a, **k: {"expiration": "2026-01-16", "atm_strike": 100.0, "straddle_mid": mid}


def test_the_winrate_scores_the_expected_move_not_the_full_straddle(monkeypatch):
    """A $5.00 straddle against a $4.50 move: the full straddle would call it a win; the expected
    move (0.85 x 5.00 = 4.25) is below the move, so it is a loss."""
    rows = [{"date": date(2026, 1, 1), "timing": "After market close"}]
    result = _winrate(monkeypatch, rows, lambda *a, **k: _closes(100.0, 104.5), _straddle(5.0))
    q = result["quarters"][0]
    assert q["implied_move"] == pytest.approx(4.25)
    assert q["win"] is False and result["winrate"] == 0.0


def test_an_unknown_report_time_counts_when_both_readings_agree(monkeypatch):
    rows = [{"date": date(2026, 1, 1), "timing": None}]
    result = _winrate(monkeypatch, rows, lambda *a, **k: _closes(100.0, 101.0), _straddle(5.0))
    assert result["sample_size"] == 1 and result["winrate"] == 1.0
    assert result["skip_counts"]["timing_unknown"] == 0
    # The realised-move sample is never filled from a quarter whose timing is a guess.
    assert result["realized_move_quarters"] == []


def test_an_unknown_report_time_is_skipped_and_counted_when_the_readings_disagree(monkeypatch):
    rows = [{"date": date(2026, 1, 1), "timing": None}]

    def closes(symbol, d, timing, config):
        # As AMC the move is small (a win); as BMO it is large (a loss).
        return _closes(100.0, 101.0) if timing == scanner.TIMING_AMC else _closes(100.0, 110.0)

    result = _winrate(monkeypatch, rows, closes, _straddle(5.0))
    assert result["sample_size"] == 0
    assert result["skip_counts"]["timing_unknown"] == 1
    assert result["skipped"] == [{"date": "2026-01-01", "reason": "timing_unknown"}]


def test_a_data_gap_is_skipped_as_a_gap_and_counted(monkeypatch):
    rows = [
        {"date": date(2026, 1, 1), "timing": "After market close"},
        {"date": date(2026, 1, 2), "timing": "Before market open"},
    ]

    def closes(symbol, d, timing, config):
        return None if d == date(2026, 1, 1) else _closes(100.0, 101.0)

    result = _winrate(monkeypatch, rows, closes, _straddle(5.0))
    assert result["sample_size"] == 1
    assert result["skip_counts"] == {"gap": 1, "no_matching_option_chain_data": 0, "timing_unknown": 0}
    assert result["skipped"][0] == {"date": "2026-01-01", "reason": "gap"}


def _ohlcv(monkeypatch, rows):
    """`_close_on` served from a {date: close} map."""
    monkeypatch.setattr(
        scanner,
        "_close_on",
        lambda symbol, day, config: {"date": day, "close": rows[day]} if day in rows else None,
    )


def test_the_closes_must_be_the_sessions_either_side_of_the_report(monkeypatch):
    _ohlcv(monkeypatch, {date(2026, 1, 8): 100.0, date(2026, 1, 9): 103.0, date(2026, 1, 7): 99.0})
    pre, reaction = scanner.pre_and_reaction_closes("AAA", date(2026, 1, 8), scanner.TIMING_AMC, {})
    assert (pre["date"], reaction["date"]) == (date(2026, 1, 8), date(2026, 1, 9))
    pre, reaction = scanner.pre_and_reaction_closes("AAA", date(2026, 1, 8), scanner.TIMING_BMO, {})
    assert (pre["date"], reaction["date"]) == (date(2026, 1, 7), date(2026, 1, 8))


def test_a_close_nine_days_away_is_never_taken_for_the_session_next_to_the_report(monkeypatch):
    """The old lookup walked up to ten days for the nearest close, so a report with no row for its
    own session scored against a price from over a week away. Now the quarter is a gap."""
    _ohlcv(monkeypatch, {date(2025, 12, 30): 100.0, date(2026, 1, 20): 130.0})
    assert scanner.pre_and_reaction_closes("AAA", date(2026, 1, 8), scanner.TIMING_AMC, {}) is None
    assert scanner.pre_and_reaction_closes("AAA", date(2026, 1, 8), scanner.TIMING_BMO, {}) is None


def test_a_report_dated_on_a_weekend_is_a_gap(monkeypatch):
    _ohlcv(monkeypatch, {date(2026, 1, 9): 100.0, date(2026, 1, 12): 101.0, date(2026, 1, 10): 100.0})
    assert scanner.pre_and_reaction_closes("AAA", date(2026, 1, 10), scanner.TIMING_AMC, {}) is None


def test_the_backtest_reports_its_skips_in_the_criteria():
    criteria = {}
    scanner.apply_common_signals(criteria, 1e6, 1.0, 0.5, 4, None, {"gap": 2, "timing_unknown": 1})
    assert criteria["winrate_sample_size"] == 4
    assert criteria["winrate_skips"] == {"gap": 2, "timing_unknown": 1}


def test_no_strategy_hard_codes_the_straddle_factor():
    """One factor, in core.structures. A literal 0.85 beside a straddle would drift from it."""
    from pathlib import Path

    from cherrypick.earnings import strategies

    for path in Path(strategies.__file__).parent.glob("*.py"):
        assert "0.85 * (" not in path.read_text(encoding="utf-8"), path.name
