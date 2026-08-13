from datetime import date

import pytest

from cherrypick.earnings import scanner

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
    result = scanner.nearest_expiration_at_least_days_after(
        expirations, date(2026, 7, 10), 21, monthly_only=True
    )
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
        front_call_mid=7.0,
        front_put_mid=7.0,
        front_iv=0.60,
        back_iv=0.40,
        underlying_price=100.0,
    )
    assert result["term_structure"] < 0
    assert result["expected_move_dollars"] == pytest.approx(0.85 * 14.0)
    assert result["expected_move_pct"] == pytest.approx(0.85 * 14.0 / 100.0)


def test_compute_expected_move_and_term_structure_positive_when_back_richer():
    result = scanner.compute_expected_move_and_term_structure(
        front_call_mid=1.0,
        front_put_mid=1.0,
        front_iv=0.30,
        back_iv=0.60,
        underlying_price=100.0,
    )
    assert result["term_structure"] > 0


# --- _soft_gate --------------------------------------------------------------


def test_soft_gate_pass_level_accepts_at_or_above_pass():
    hard_fail = []
    scanner._soft_gate(10, 5, 2, "pass", "x", hard_fail)
    assert hard_fail == []


def test_soft_gate_pass_level_rejects_below_pass():
    hard_fail = []
    scanner._soft_gate(3, 5, 2, "pass", "x", hard_fail)
    assert hard_fail == ["x_below_minimum"]


def test_soft_gate_near_miss_level_accepts_between_bands():
    hard_fail = []
    scanner._soft_gate(3, 5, 2, "near_miss", "x", hard_fail)
    assert hard_fail == []


def test_soft_gate_near_miss_level_rejects_below_near_miss():
    hard_fail = []
    scanner._soft_gate(1, 5, 2, "near_miss", "x", hard_fail)
    assert hard_fail == ["x_below_minimum"]


def test_soft_gate_off_never_rejects():
    hard_fail = []
    scanner._soft_gate(0, 5, 2, "off", "x", hard_fail)
    scanner._soft_gate(None, 5, 2, "off", "x", hard_fail)
    assert hard_fail == []


def test_soft_gate_missing_value_is_unverified_reject_unless_off():
    hard_fail = []
    scanner._soft_gate(None, 5, 2, "pass", "x", hard_fail)
    assert hard_fail == ["x_unverified"]


# --- apply_liquidity_gates (hard filters only) ----------------------------------


def test_apply_liquidity_gates_all_pass(base_strategy_config, good_criteria):
    hard_fail = []
    scanner.apply_liquidity_gates(good_criteria, base_strategy_config, hard_fail)
    assert hard_fail == []


def test_apply_liquidity_gates_missing_open_interest_hard_fails(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "combined_open_interest": None}
    hard_fail = []
    scanner.apply_liquidity_gates(criteria, base_strategy_config, hard_fail)
    assert "combined_open_interest_unverified" in hard_fail


def test_apply_liquidity_gates_requires_weekly_options(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "has_weekly_options": False}
    hard_fail = []
    scanner.apply_liquidity_gates(criteria, base_strategy_config, hard_fail)
    assert "no_weekly_options" in hard_fail


# --- apply_soft_criteria --------------------------------------------------------


def test_apply_soft_criteria_all_pass(base_strategy_config, good_criteria):
    hard_fail = []
    scanner.apply_soft_criteria(good_criteria, base_strategy_config, hard_fail)
    assert hard_fail == []


def test_apply_soft_criteria_rejects_market_cap_below_pass(base_strategy_config, good_criteria):
    # Between the near-miss floor (1B) and the pass threshold (2B): rejected at the default "pass".
    criteria = {**good_criteria, "market_cap": 1500000000}
    hard_fail = []
    scanner.apply_soft_criteria(criteria, base_strategy_config, hard_fail)
    assert "market_cap_below_minimum" in hard_fail


def test_apply_soft_criteria_honors_near_miss_level(base_strategy_config, good_criteria):
    config = {**base_strategy_config, "_symbol_screen": {"market_cap": "near_miss"}}
    criteria = {**good_criteria, "market_cap": 1500000000}
    hard_fail = []
    scanner.apply_soft_criteria(criteria, config, hard_fail)
    assert hard_fail == []


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
        "SC": _quote(1.0, 1.2),
        "SP": _quote(1.0, 1.2),
        "LC": _quote(0.3, 0.4),
        "LP": _quote(0.3, 0.4),
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
    result = scanner.evaluate_credit_spread_exit(
        entry_credit=2.0, exit_debit=0.9, config={"profit_target_pct": 0.50}
    )
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_credit_spread_exit_stop_loss():
    result = scanner.evaluate_credit_spread_exit(
        entry_credit=2.0, exit_debit=3.5, config={"stop_loss_credit_multiple": 1.5}
    )
    assert result == {"action": "close_all", "reason": "stop_loss"}


def test_evaluate_credit_spread_exit_hold():
    result = scanner.evaluate_credit_spread_exit(
        entry_credit=2.0, exit_debit=1.5, config={"profit_target_pct": 0.50}
    )
    assert result == {"action": "hold"}


def test_evaluate_debit_spread_exit_profit_target():
    # entry_credit stored negative (debit paid); exit_debit negative = nets credit on close
    result = scanner.evaluate_debit_spread_exit(
        entry_credit=-2.0, exit_debit=-3.0, config={"profit_target_pct": 0.25}
    )
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_debit_spread_exit_stop_loss():
    result = scanner.evaluate_debit_spread_exit(
        entry_credit=-2.0, exit_debit=1.5, config={"stop_loss_pct_of_debit": 0.40}
    )
    assert result == {"action": "close_all", "reason": "stop_loss"}


def test_evaluate_debit_spread_exit_hold():
    result = scanner.evaluate_debit_spread_exit(entry_credit=-2.0, exit_debit=-1.9, config={})
    assert result == {"action": "hold"}


# --- rank_candidates / select_positions -----------------------------------------


def test_rank_candidates_excludes_rejected():
    candidates = [
        {"accepted": False, "criteria": {"term_structure": -0.1, "iv_rv_ratio": 1.0, "winrate": 0.5}},
        {"accepted": False, "criteria": {"term_structure": -0.3, "iv_rv_ratio": 1.0, "winrate": 0.5}},
        {
            "accepted": True,
            "symbol": "A",
            "criteria": {"term_structure": -0.2, "iv_rv_ratio": 1.0, "winrate": 0.5},
        },
        {
            "accepted": True,
            "symbol": "B",
            "criteria": {"term_structure": -0.1, "iv_rv_ratio": 1.0, "winrate": 0.5},
        },
    ]
    ranked = scanner.rank_candidates(candidates, config={})
    assert len(ranked) == 2
    assert ranked[0]["symbol"] == "A"  # higher |term_structure| scores higher


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
        "ok": True,
        "symbol": "AAPL",
        "sample_size": 0,
        "winrate": None,
        "quarters": [],
        "realized_move_quarters": [],
        "skipped": [],
    }


def test_compute_winrate_skips_ambiguous_timing(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "fetch_historical_earnings_dates",
        lambda *a, **k: [{"date": date(2026, 1, 1), "timing": "During Market"}],
    )
    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result["sample_size"] == 0
    assert result["skipped"] == [{"date": "2026-01-01", "reason": "ambiguous_timing_or_no_price_data"}]


def test_compute_winrate_computes_win_when_implied_exceeds_realized(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "fetch_historical_earnings_dates",
        lambda *a, **k: [{"date": date(2026, 1, 1), "timing": "After market close"}],
    )
    monkeypatch.setattr(
        scanner,
        "pre_and_reaction_closes",
        lambda *a, **k: (
            {"date": date(2026, 1, 1), "close": 100.0},
            {"date": date(2026, 1, 2), "close": 103.0},
        ),
    )
    monkeypatch.setattr(
        scanner,
        "fetch_atm_straddle_price",
        lambda *a, **k: {"expiration": "2026-01-16", "atm_strike": 100.0, "straddle_mid": 5.0},
    )
    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result["sample_size"] == 1
    assert result["winrate"] == 1.0
    assert result["quarters"][0]["win"] is True


def test_compute_winrate_realized_move_quarters_survives_missing_straddle(monkeypatch):
    """The bug this guards: a quarter with good OHLCV closes but no matching historical option
    chain used to be dropped entirely, discarding perfectly good realized-move data along with
    the missing implied side. realized_move_quarters must keep it; quarters (winrate) still
    correctly excludes it."""
    monkeypatch.setattr(
        scanner,
        "fetch_historical_earnings_dates",
        lambda *a, **k: [{"date": date(2026, 1, 1), "timing": "After market close"}],
    )
    monkeypatch.setattr(
        scanner,
        "pre_and_reaction_closes",
        lambda *a, **k: (
            {"date": date(2026, 1, 1), "close": 100.0},
            {"date": date(2026, 1, 2), "close": 103.0},
        ),
    )
    monkeypatch.setattr(scanner, "fetch_atm_straddle_price", lambda *a, **k: None)

    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=8)
    assert result["quarters"] == []
    assert result["sample_size"] == 0
    assert result["skipped"] == [{"date": "2026-01-01", "reason": "no_matching_option_chain_data"}]
    assert len(result["realized_move_quarters"]) == 1
    assert result["realized_move_quarters"][0]["realized_move"] == 3.0
    assert result["realized_move_quarters"][0]["pre_close"] == 100.0


def test_compute_winrate_realized_move_quarters_can_exceed_quarters_sample(monkeypatch):
    """realized_move_quarters fills to its own lookback_quarters target even when winrate's own
    sample stays smaller -- deep OHLCV coverage vs shallower option_chain coverage."""
    dates = [{"date": date(2026, 1, d), "timing": "After market close"} for d in (1, 3, 5)]
    monkeypatch.setattr(scanner, "fetch_historical_earnings_dates", lambda *a, **k: dates)
    monkeypatch.setattr(
        scanner,
        "pre_and_reaction_closes",
        lambda symbol, d, timing, config: ({"date": d, "close": 100.0}, {"date": d, "close": 105.0}),
    )
    # Only the first date has a matching historical option chain.
    monkeypatch.setattr(
        scanner,
        "fetch_atm_straddle_price",
        lambda symbol, pre_date, reaction_date, pre_close, config: (
            {"expiration": "2026-01-16", "atm_strike": 100.0, "straddle_mid": 5.0}
            if pre_date == date(2026, 1, 1)
            else None
        ),
    )

    result = scanner.compute_winrate("AAPL", {}, lookback_quarters=3)
    assert result["sample_size"] == 1
    assert len(result["realized_move_quarters"]) == 3


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
    assert all(r["timing_assumed"] is False for r in result)


def _timing_calendar(monkeypatch, by_date):
    """Mock fetch_dolthub_calendar with `{iso_date: [row, ...]}`, empty for any other date."""
    monkeypatch.setattr(
        scanner, "fetch_dolthub_calendar", lambda iso_date, config: list(by_date.get(iso_date, []))
    )


def test_fetch_entry_window_calendar_bmo_half_is_the_next_TRADING_day(monkeypatch):
    """Friday's BMO half must reach Monday. Using `today + 1` landed it on Saturday, a date the
    calendar has no rows for, so Friday could never see a Monday-morning reporter."""
    _timing_calendar(
        monkeypatch,
        {
            "2026-07-10": [{"symbol": "FRI_AMC", "timing": "After market close"}],
            "2026-07-11": [{"symbol": "SAT_GHOST", "timing": "Before market open"}],
            "2026-07-13": [{"symbol": "MON_BMO", "timing": "Before market open"}],
        },
    )
    result = scanner.fetch_entry_window_calendar({}, today=date(2026, 7, 10))  # a Friday
    assert [r["symbol"] for r in result] == ["FRI_AMC", "MON_BMO"]


def test_fetch_entry_window_calendar_admits_blank_timing_only_for_covered_symbols(monkeypatch):
    """A missing `when` on the scan date is read as AMC, but only for symbols this morning's
    forward scan measured -- that set is what keeps the ~8s-per-symbol scan inside its window."""
    _timing_calendar(
        monkeypatch,
        {
            "2026-07-07": [
                {"symbol": "COVERED", "timing": None},
                {"symbol": "ALSO_COVERED", "timing": ""},
                {"symbol": "UNCOVERED", "timing": None},
            ],
        },
    )
    result = scanner.fetch_entry_window_calendar(
        {}, today=date(2026, 7, 7), assume_amc_for={"COVERED", "ALSO_COVERED"}
    )
    assert [r["symbol"] for r in result] == ["COVERED", "ALSO_COVERED"]
    # Normalized, not left blank: reaction_date/select_front_expiration branch on the exact string,
    # and a blank one would pick a front expiration that expires before the event's move.
    assert all(r["timing"] == scanner.TIMING_AMC for r in result)
    assert all(r["timing_assumed"] is True for r in result)


def test_fetch_entry_window_calendar_blank_timing_on_the_next_session_is_not_admitted(monkeypatch):
    """Only the scan date's blanks are assumed. A blank on the next session could be that day's
    AMC, and entering it tonight would hold it through an exit check that fires before the event."""
    _timing_calendar(
        monkeypatch,
        {
            "2026-07-08": [{"symbol": "COVERED_TOMORROW", "timing": None}],
        },
    )
    result = scanner.fetch_entry_window_calendar(
        {}, today=date(2026, 7, 7), assume_amc_for={"COVERED_TOMORROW"}
    )
    assert result == []


def test_fetch_entry_window_calendar_without_a_covered_set_falls_back_to_exact_timing(monkeypatch):
    """No snapshot (or a stale one) degrades to the old exact-timing behavior -- never to an
    unbounded scan of every unannotated row on the calendar."""
    _timing_calendar(
        monkeypatch,
        {
            "2026-07-07": [
                {"symbol": "BLANK", "timing": None},
                {"symbol": "ANNOTATED", "timing": "After market close"},
            ],
        },
    )
    result = scanner.fetch_entry_window_calendar({}, today=date(2026, 7, 7))
    assert [r["symbol"] for r in result] == ["ANNOTATED"]


# --- compute_historical_move_stats (mocked DB layer, via compute_winrate) --------


def _mock_winrate_quarters(monkeypatch, pre_closes, reaction_closes):
    """3 quarters' worth of fetch_historical_earnings_dates/pre_and_reaction_closes/
    fetch_atm_straddle_price mocks, so compute_winrate (and therefore
    compute_historical_move_stats) sees `len(pre_closes)` quarters with the given
    pre/reaction closes. Straddle mid is irrelevant to move-stats math, kept fixed."""
    dates = [{"date": date(2026, 1, i + 1), "timing": "After market close"} for i in range(len(pre_closes))]
    monkeypatch.setattr(scanner, "fetch_historical_earnings_dates", lambda *a, **k: dates)

    closes = iter(zip(pre_closes, reaction_closes, strict=True))

    def fake_closes(*a, **k):
        pre, reaction = next(closes)
        return {"date": date(2026, 1, 1), "close": pre}, {"date": date(2026, 1, 2), "close": reaction}

    monkeypatch.setattr(scanner, "pre_and_reaction_closes", fake_closes)
    monkeypatch.setattr(
        scanner,
        "fetch_atm_straddle_price",
        lambda *a, **k: {"expiration": "2026-01-16", "atm_strike": 100.0, "straddle_mid": 5.0},
    )


def test_compute_historical_move_stats_computes_mean_dispersion_max(monkeypatch):
    # Realized moves as % of pre_close: 4%, 4%, 20% -- mean 9.33%, the 20% quarter is >= 2x that.
    _mock_winrate_quarters(
        monkeypatch, pre_closes=[100.0, 100.0, 100.0], reaction_closes=[104.0, 96.0, 120.0]
    )
    result = scanner.compute_historical_move_stats("AAPL", {"move_tail_multiple": 2.0}, lookback_quarters=8)
    assert result["ok"] is True
    assert result["sample_size"] == 3
    assert result["avg_actual_move_pct"] == pytest.approx((0.04 + 0.04 + 0.20) / 3)
    assert result["max_actual_move_pct"] == pytest.approx(0.20)
    assert result["move_dispersion_pct"] > 0
    assert result["tail_quarters"] == 1
    assert result["move_tail_veto"] is True


def test_compute_historical_move_stats_no_tail_when_moves_are_consistent(monkeypatch):
    _mock_winrate_quarters(monkeypatch, pre_closes=[100.0, 100.0], reaction_closes=[104.0, 103.0])
    result = scanner.compute_historical_move_stats("AAPL", {}, lookback_quarters=8)
    assert result["tail_quarters"] == 0
    assert result["move_tail_veto"] is False


def test_compute_historical_move_stats_insufficient_sample(monkeypatch):
    monkeypatch.setattr(scanner, "fetch_historical_earnings_dates", lambda *a, **k: [])
    result = scanner.compute_historical_move_stats("AAPL", {}, lookback_quarters=8)
    assert result["ok"] is False
    assert result["sample_size"] == 0


# --- compute_spread_quality --------------------------------------------------------


def test_compute_spread_quality_computes_net_combo_spread():
    legs = [{"bid": 1.90, "ask": 2.10, "mid": 2.00}, {"bid": 0.95, "ask": 1.05, "mid": 1.00}]
    result = scanner.compute_spread_quality(legs)
    # total width 0.30, total mid 3.00 -> 10%
    assert result["net_combo_spread_pct"] == pytest.approx(0.10)


def test_compute_spread_quality_skips_incomplete_legs():
    # First leg has no bid -- skipped entirely, not treated as zero-cost; only the second
    # leg's 0.10 width / 1.00 mid feeds the result.
    legs = [{"bid": None, "ask": 2.10, "mid": 2.00}, {"bid": 0.95, "ask": 1.05, "mid": 1.00}]
    result = scanner.compute_spread_quality(legs)
    assert result["net_combo_spread_pct"] == pytest.approx(0.10)


def test_compute_spread_quality_no_usable_legs_returns_none():
    result = scanner.compute_spread_quality([{"bid": None, "ask": None, "mid": None}])
    assert result["net_combo_spread_pct"] is None


# --- apply_move_tail_gate ----------------------------------------------------------


def test_apply_move_tail_gate_off_by_default_never_rejects():
    hard_fail: list = []
    scanner.apply_move_tail_gate({"move_tail_veto": True}, {}, hard_fail)
    assert hard_fail == []


def test_apply_move_tail_gate_veto_rejects_when_flagged():
    hard_fail: list = []
    config = {"_symbol_screen": {"move_tail": "veto"}}
    scanner.apply_move_tail_gate({"move_tail_veto": True}, config, hard_fail)
    assert hard_fail == ["move_tail_veto"]


def test_apply_move_tail_gate_veto_level_but_not_flagged_passes():
    hard_fail: list = []
    config = {"_symbol_screen": {"move_tail": "veto"}}
    scanner.apply_move_tail_gate({"move_tail_veto": False}, config, hard_fail)
    assert hard_fail == []


# --- apply_common_signals -----------------------------------------------------------


def test_apply_common_signals_prefers_tastytrade_iv_rv_over_dolt():
    criteria = {"tastytrade_iv_rv_ratio": 1.6}
    scanner.apply_common_signals(criteria, 2_000_000, 1.1, 0.6, 8)
    assert criteria["iv_rv_ratio"] == 1.6
    assert criteria["iv_rv_source"] == "tastytrade"


def test_apply_common_signals_falls_back_to_dolt_when_tastytrade_missing():
    criteria: dict = {}
    scanner.apply_common_signals(criteria, 2_000_000, 1.1, 0.6, 8)
    assert criteria["iv_rv_ratio"] == 1.1
    assert criteria["iv_rv_source"] == "dolt"


def test_apply_common_signals_no_source_when_both_missing():
    criteria: dict = {}
    scanner.apply_common_signals(criteria, 2_000_000, None, 0.6, 8)
    assert criteria["iv_rv_ratio"] is None
    assert criteria["iv_rv_source"] is None


def test_apply_common_signals_computes_implied_vs_avg_actual():
    criteria = {"expected_move_pct": 0.08}
    move_stats = {
        "ok": True,
        "avg_actual_move_pct": 0.04,
        "move_dispersion_pct": 0.01,
        "max_actual_move_pct": 0.06,
        "move_tail_veto": False,
    }
    scanner.apply_common_signals(criteria, 1_000_000, None, 0.5, 4, move_stats)
    assert criteria["implied_vs_avg_actual"] == pytest.approx(2.0)
    assert criteria["avg_actual_move_pct"] == 0.04
    assert criteria["move_tail_veto"] is False


def test_apply_common_signals_skips_move_stats_when_not_ok():
    criteria: dict = {}
    scanner.apply_common_signals(criteria, 1_000_000, None, 0.5, 4, {"ok": False})
    assert "avg_actual_move_pct" not in criteria


# --- richest_criteria / build_entry_review_spec -------------------------------------


def test_richest_criteria_picks_the_largest_dict():
    results = [
        {"name": "iron_fly", "criteria": {"a": 1}, "composite_score": 0.1},
        {"name": "double_calendar", "criteria": {"a": 1, "b": 2, "c": 3}, "composite_score": 0.2},
    ]
    crit, name, score = scanner.richest_criteria(results)
    assert crit == {"a": 1, "b": 2, "c": 3}
    assert name == "double_calendar"
    assert score == 0.2


def test_richest_criteria_empty_results():
    crit, name, score = scanner.richest_criteria([])
    assert crit == {}
    assert name is None
    assert score is None


def test_build_entry_review_spec_shape():
    criteria = {
        "price": 150.0,
        "avg_volume": 2_000_000,
        "winrate": 0.6,
        "winrate_sample_size": 8,
        "iv_rv_ratio": 1.4,
        "iv_rv_source": "tastytrade",
        "term_structure": -0.05,
        "expected_move_pct": 0.06,
        "net_combo_spread_pct": 0.03,
        "avg_actual_move_pct": 0.04,
        "implied_vs_avg_actual": 1.5,
        "move_tail_veto": False,
        "iv_rank": 0.7,
        "iv_percentile": 0.65,
    }
    spec = scanner.build_entry_review_spec(
        "2026-08-07",
        "AAPL",
        "After market close",
        criteria,
        "iron_fly",
        True,
        "opened iron_fly",
        composite_score=0.42,
        logged_at=123.0,
    )
    assert spec["scan_date"] == "2026-08-07"
    assert spec["symbol"] == "AAPL"
    assert spec["strategy"] == "iron_fly"
    assert spec["volume"] == 2_000_000
    assert spec["iv_rv_source"] == "tastytrade"
    assert spec["best_tier"] == "accepted"
    assert spec["selected"] is True
    assert spec["composite_score"] == 0.42
    assert spec["logged_at"] == 123.0
    assert spec["criteria_json"] == criteria


def test_build_entry_review_spec_rejected_best_tier():
    spec = scanner.build_entry_review_spec(
        "2026-08-07", "MSFT", "Before market open", {}, None, False, "no edge"
    )
    assert spec["best_tier"] == "rejected"
    assert spec["selected"] is False


def test_fetch_liquid_symbols_returns_set_on_success(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "symbols": ["AAPL", "MSFT"]})
    assert scanner.fetch_liquid_symbols() == {"AAPL", "MSFT"}


def test_fetch_liquid_symbols_none_on_failure(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": False, "error": "no session"})
    assert scanner.fetch_liquid_symbols() is None


def test_fetch_liquid_symbols_none_on_empty_watchlist(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "symbols": []})
    assert scanner.fetch_liquid_symbols() is None


def test_fetch_watch_universe_returns_set_on_success(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "symbols": ["AAPL", "NET"]})
    assert scanner.fetch_watch_universe() == {"AAPL", "NET"}


def test_fetch_watch_universe_none_on_failure(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": False, "error": "no session"})
    assert scanner.fetch_watch_universe() is None


def test_fetch_watch_universe_none_on_empty_union(monkeypatch):
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "symbols": []})
    assert scanner.fetch_watch_universe() is None
