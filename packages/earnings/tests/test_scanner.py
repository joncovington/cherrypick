from datetime import date

import pytest

import scanner

# --- has_weekly_options -----------------------------------------------------

def test_has_weekly_options_true_when_gap_le_10_days():
    exps = [date(2026, 7, 10), date(2026, 7, 17), date(2026, 8, 21)]
    assert scanner.has_weekly_options(exps) is True


def test_has_weekly_options_false_when_all_gaps_large():
    exps = [date(2026, 7, 17), date(2026, 8, 21), date(2026, 9, 18)]
    assert scanner.has_weekly_options(exps) is False


def test_has_weekly_options_unsorted_input():
    exps = [date(2026, 8, 21), date(2026, 7, 10), date(2026, 7, 17)]
    assert scanner.has_weekly_options(exps) is True


# --- reaction_date / select_front_expiration --------------------------------

def test_reaction_date_after_market_close_is_next_day():
    assert scanner.reaction_date(date(2026, 7, 7), "After market close") == date(2026, 7, 8)


def test_reaction_date_before_market_open_is_same_day():
    assert scanner.reaction_date(date(2026, 7, 7), "Before market open") == date(2026, 7, 7)


def test_select_front_expiration_picks_nearest_on_or_after_reaction():
    expirations = [date(2026, 7, 3), date(2026, 7, 10), date(2026, 7, 17)]
    front, err = scanner.select_front_expiration(expirations, date(2026, 7, 7), "After market close")
    assert err is None
    assert front == date(2026, 7, 10)


def test_select_front_expiration_no_eligible_expiration_returns_error():
    expirations = [date(2026, 7, 3)]
    front, err = scanner.select_front_expiration(expirations, date(2026, 7, 7), "After market close")
    assert front is None
    assert "no expiration" in err


# --- is_monthly_expiration / nearest_expiration_at_least_days_after ---------

def test_is_monthly_expiration_third_friday():
    assert scanner.is_monthly_expiration(date(2026, 7, 17)) is True


def test_is_monthly_expiration_rejects_non_friday():
    assert scanner.is_monthly_expiration(date(2026, 7, 16)) is False


def test_is_monthly_expiration_rejects_friday_outside_window():
    assert scanner.is_monthly_expiration(date(2026, 7, 3)) is False


def test_nearest_expiration_at_least_days_after_monthly_only():
    expirations = [date(2026, 7, 24), date(2026, 8, 21), date(2026, 9, 4)]
    result = scanner.nearest_expiration_at_least_days_after(expirations, date(2026, 7, 10), 21, monthly_only=True)
    assert result == date(2026, 8, 21)


def test_nearest_expiration_at_least_days_after_none_when_no_candidate():
    expirations = [date(2026, 7, 12)]
    result = scanner.nearest_expiration_at_least_days_after(expirations, date(2026, 7, 10), 21)
    assert result is None


def test_select_back_expiration_falls_back_to_non_monthly():
    front = date(2026, 7, 10)
    expirations = [front, date(2026, 8, 5)]  # no monthly cycle available
    result = scanner.select_back_expiration(expirations, front, 21)
    assert result == date(2026, 8, 5)


# --- atm_entry / nearest_strike_entry ---------------------------------------

CALL_ENTRIES = [
    {"option_type": "C", "strike_price": "95"},
    {"option_type": "C", "strike_price": "100"},
    {"option_type": "C", "strike_price": "105"},
    {"option_type": "P", "strike_price": "100"},
]


def test_atm_entry_picks_closest_strike():
    entry = scanner.atm_entry(CALL_ENTRIES, "call", 101)
    assert entry["strike_price"] == "100"


def test_atm_entry_case_insensitive_full_word():
    entries = [{"option_type": "Call", "strike_price": "100"}]
    assert scanner.atm_entry(entries, "call", 100)["strike_price"] == "100"


def test_atm_entry_none_when_no_match():
    assert scanner.atm_entry(CALL_ENTRIES, "put", 200) is not None  # one put exists
    assert scanner.atm_entry([], "call", 100) is None


def test_nearest_strike_entry_excludes_given_strike():
    entry = scanner.nearest_strike_entry(CALL_ENTRIES, "call", 100, exclude_strike=100.0)
    assert entry["strike_price"] == "95" or entry["strike_price"] == "105"
    assert entry["strike_price"] != "100"


# --- compute_expected_move_and_term_structure -------------------------------

def test_compute_expected_move_and_term_structure_negative_when_front_richer():
    result = scanner.compute_expected_move_and_term_structure(
        front_call_mid=7.0, front_put_mid=7.0, front_iv=0.60, back_iv=0.40, underlying_price=100.0,
    )
    assert result["term_structure"] < 0
    assert result["expected_move_dollars"] == pytest.approx(0.85 * 14.0)
    assert result["expected_move_pct"] == pytest.approx(0.85 * 14.0 / 100.0)


def test_compute_expected_move_and_term_structure_positive_when_back_richer():
    result = scanner.compute_expected_move_and_term_structure(
        front_call_mid=1.0, front_put_mid=1.0, front_iv=0.30, back_iv=0.60, underlying_price=100.0,
    )
    assert result["term_structure"] > 0


# --- _band -------------------------------------------------------------------

def test_band_pass_silent():
    near_miss, hard_fail = [], []
    scanner._band(10, 5, 2, "x", near_miss, hard_fail)
    assert near_miss == [] and hard_fail == []


def test_band_near_miss():
    near_miss, hard_fail = [], []
    scanner._band(3, 5, 2, "x", near_miss, hard_fail)
    assert near_miss == ["x"] and hard_fail == []


def test_band_hard_fail_below_near_miss():
    near_miss, hard_fail = [], []
    scanner._band(1, 5, 2, "x", near_miss, hard_fail)
    assert hard_fail == ["x_below_near_miss"]


def test_band_missing_value_is_near_miss_not_pass():
    near_miss, hard_fail = [], []
    scanner._band(None, 5, 2, "x", near_miss, hard_fail)
    assert near_miss == ["x_unknown"]
    assert hard_fail == []


# --- apply_liquidity_gates ------------------------------------------------------

def test_apply_liquidity_gates_all_pass(base_strategy_config, good_criteria):
    hard_fail, near_miss = [], []
    scanner.apply_liquidity_gates(good_criteria, base_strategy_config, hard_fail, near_miss)
    assert hard_fail == []
    assert near_miss == []


def test_apply_liquidity_gates_missing_open_interest_hard_fails(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "combined_open_interest": None}
    hard_fail, near_miss = [], []
    scanner.apply_liquidity_gates(criteria, base_strategy_config, hard_fail, near_miss)
    assert "combined_open_interest_unverified" in hard_fail


def test_apply_liquidity_gates_requires_weekly_options(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "has_weekly_options": False}
    hard_fail, near_miss = [], []
    scanner.apply_liquidity_gates(criteria, base_strategy_config, hard_fail, near_miss)
    assert "no_weekly_options" in hard_fail


# --- _shrunk_winrate / compute_composite_score ----------------------------------

def test_shrunk_winrate_full_sample_uses_raw_value():
    assert scanner._shrunk_winrate(0.85, 8, target_sample=8) == pytest.approx(0.85)


def test_shrunk_winrate_small_sample_shrinks_toward_half():
    result = scanner._shrunk_winrate(1.0, 1, target_sample=8)
    assert 0.5 < result < 1.0


def test_shrunk_winrate_none_defaults_to_half():
    assert scanner._shrunk_winrate(None, 0) == 0.5


def test_compute_composite_score_uses_term_structure():
    criteria = {"term_structure": -0.1, "iv_rv_ratio": 1.5, "winrate": 0.6}
    score = scanner.compute_composite_score(criteria, winrate_sample_size=8)
    assert score == pytest.approx(0.1 * 1.5 * 0.6)


def test_compute_composite_score_falls_back_to_skew_abs():
    criteria = {"skew_abs": 0.05, "iv_rv_ratio": 1.2, "winrate": 0.5}
    score = scanner.compute_composite_score(criteria, winrate_sample_size=8)
    assert score is not None


def test_compute_composite_score_none_without_edge_signal():
    assert scanner.compute_composite_score({"iv_rv_ratio": 1.2}, 8) is None


def test_compute_composite_score_defaults_iv_rv_to_one():
    criteria = {"term_structure": -0.1, "winrate": 0.6}
    score = scanner.compute_composite_score(criteria, winrate_sample_size=8)
    assert score == pytest.approx(0.1 * 1.0 * 0.6)


# --- compute_generic_exit_debit ------------------------------------------------

def _quote(bid, ask):
    return {"bid": bid, "ask": ask}


def test_compute_generic_exit_debit_iron_fly_shape():
    legs = [
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "SP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "LC", "action": "Buy to Open", "quantity": 1},
        {"symbol": "LP", "action": "Buy to Open", "quantity": 1},
    ]
    quotes = {
        "SC": _quote(1.0, 1.2), "SP": _quote(1.0, 1.2),
        "LC": _quote(0.3, 0.4), "LP": _quote(0.3, 0.4),
    }
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    # buy back shorts at ask (1.2 + 1.2), sell longs at bid (0.3 + 0.3)
    assert exit_debit == pytest.approx(2.4 - 0.6)


def test_compute_generic_exit_debit_butterfly_shape_with_quantity():
    legs = [
        {"symbol": "ATM", "action": "Buy to Open", "quantity": 1},
        {"symbol": "SHORT", "action": "Sell to Open", "quantity": 2},
        {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
    ]
    quotes = {
        "ATM": _quote(2.0, 2.2),
        "SHORT": _quote(1.0, 1.1),
        "FAR": _quote(0.4, 0.5),
    }
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    # sell longs at bid (2.0 + 0.4), buy back short x2 at ask (2 * 1.1)
    assert exit_debit == pytest.approx((2 * 1.1) - 2.0 - 0.4)


def test_compute_generic_exit_debit_none_when_quote_missing():
    legs = [{"symbol": "SC", "action": "Sell to Open", "quantity": 1}]
    assert scanner.compute_generic_exit_debit(legs, {}) is None


def test_compute_generic_exit_debit_none_when_required_side_missing():
    legs = [{"symbol": "SC", "action": "Sell to Open", "quantity": 1}]
    quotes = {"SC": {"bid": 1.0, "ask": None}}
    assert scanner.compute_generic_exit_debit(legs, quotes) is None


# --- evaluate_credit_spread_exit / evaluate_debit_spread_exit -------------------

def test_evaluate_credit_spread_exit_profit_target():
    result = scanner.evaluate_credit_spread_exit(entry_credit=2.0, exit_debit=0.9, config={"profit_target_pct": 0.50})
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_credit_spread_exit_stop_loss():
    result = scanner.evaluate_credit_spread_exit(entry_credit=2.0, exit_debit=3.5, config={"stop_loss_credit_multiple": 1.5})
    assert result == {"action": "close_all", "reason": "stop_loss"}


def test_evaluate_credit_spread_exit_hold():
    result = scanner.evaluate_credit_spread_exit(entry_credit=2.0, exit_debit=1.5, config={"profit_target_pct": 0.50})
    assert result == {"action": "hold"}


def test_evaluate_debit_spread_exit_profit_target():
    # entry_credit stored negative (debit paid); exit_debit negative = nets credit on close
    result = scanner.evaluate_debit_spread_exit(entry_credit=-2.0, exit_debit=-3.0, config={"profit_target_pct": 0.25})
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_debit_spread_exit_stop_loss():
    result = scanner.evaluate_debit_spread_exit(entry_credit=-2.0, exit_debit=1.5, config={"stop_loss_pct_of_debit": 0.40})
    assert result == {"action": "close_all", "reason": "stop_loss"}


def test_evaluate_debit_spread_exit_hold():
    result = scanner.evaluate_debit_spread_exit(entry_credit=-2.0, exit_debit=-1.9, config={})
    assert result == {"action": "hold"}


# --- rank_candidates / select_positions -----------------------------------------

def test_rank_candidates_excludes_reject_and_near_miss():
    candidates = [
        {"tier": "Reject", "criteria": {"term_structure": -0.1, "iv_rv_ratio": 1.0, "winrate": 0.5}},
        {"tier": "Near Miss", "criteria": {"term_structure": -0.1, "iv_rv_ratio": 1.0, "winrate": 0.5}},
        {"tier": "Tier 1", "criteria": {"term_structure": -0.2, "iv_rv_ratio": 1.0, "winrate": 0.5}},
        {"tier": "Tier 2", "criteria": {"term_structure": -0.1, "iv_rv_ratio": 1.0, "winrate": 0.5}},
    ]
    ranked = scanner.rank_candidates(candidates, config={})
    assert len(ranked) == 2
    assert ranked[0]["tier"] == "Tier 1"  # higher |term_structure| scores higher


def test_select_positions_respects_max_concurrent():
    ranked = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
    result = scanner.select_positions(ranked, config={"max_concurrent_earnings_positions": 2})
    assert [c["symbol"] for c in result["selected"]] == ["A", "B"]
    assert result["skipped"] == [{"symbol": "C", "reason": "max_positions_reached"}]


def test_select_positions_blocks_correlated_names():
    ranked = [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "GOOG"}]
    config = {"max_concurrent_earnings_positions": 3, "correlation_block_list": [["AAPL", "MSFT"]]}
    result = scanner.select_positions(ranked, config)
    selected_symbols = [c["symbol"] for c in result["selected"]]
    assert selected_symbols == ["AAPL", "GOOG"]
    assert result["skipped"] == [{"symbol": "MSFT", "reason": "correlation_block"}]


# --- compute_winrate (mocked DB layer) -------------------------------------------

def test_compute_winrate_no_earnings_dates(monkeypatch):
    monkeypatch.setattr(scanner, "fetch_historical_earnings_dates", lambda *a, **k: [])
    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result == {
        "ok": True, "symbol": "AAPL", "sample_size": 0, "winrate": None,
        "quarters": [], "skipped": [],
    }


def test_compute_winrate_skips_ambiguous_timing(monkeypatch):
    monkeypatch.setattr(
        scanner, "fetch_historical_earnings_dates",
        lambda *a, **k: [{"date": date(2026, 1, 1), "timing": "During Market"}],
    )
    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result["sample_size"] == 0
    assert result["skipped"] == [{"date": "2026-01-01", "reason": "ambiguous_timing_or_no_price_data"}]


def test_compute_winrate_computes_win_when_implied_exceeds_realized(monkeypatch):
    monkeypatch.setattr(
        scanner, "fetch_historical_earnings_dates",
        lambda *a, **k: [{"date": date(2026, 1, 1), "timing": "After market close"}],
    )
    monkeypatch.setattr(
        scanner, "pre_and_reaction_closes",
        lambda *a, **k: ({"date": date(2026, 1, 1), "close": 100.0}, {"date": date(2026, 1, 2), "close": 103.0}),
    )
    monkeypatch.setattr(
        scanner, "fetch_atm_straddle_price",
        lambda *a, **k: {"expiration": "2026-01-16", "atm_strike": 100.0, "straddle_mid": 5.0},
    )
    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result["sample_size"] == 1
    assert result["winrate"] == 1.0
    assert result["quarters"][0]["win"] is True


# --- fetch_entry_window_calendar (mocked DB layer) -------------------------------

def test_fetch_entry_window_calendar_merges_today_amc_and_tomorrow_bmo(monkeypatch):
    def fake_calendar(iso_date, config):
        if iso_date == "2026-07-07":
            return [
                {"symbol": "AMC_TODAY", "timing": "After market close"},
                {"symbol": "BMO_TODAY", "timing": "Before market open"},
            ]
        return [
            {"symbol": "BMO_TOMORROW", "timing": "Before market open"},
            {"symbol": "AMC_TOMORROW", "timing": "After market close"},
        ]
    monkeypatch.setattr(scanner, "fetch_dolthub_calendar", fake_calendar)
    result = scanner.fetch_entry_window_calendar({}, today=date(2026, 7, 7))
    symbols = [r["symbol"] for r in result]
    assert symbols == ["AMC_TODAY", "BMO_TOMORROW"]
