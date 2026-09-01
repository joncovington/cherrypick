"""The rejection trail: every gate's reason carries the number behind it, and the funnel from
screening to open position is recorded rather than inferred from what is missing."""

import pytest

from cherrypick.earnings import rank_strategies, scanner

# --------------------------------------------------------------------------- the map stays honest


def _all_apply_tiering():
    return [
        (e["name"], e["apply_tiering_fn"], e["strategy_config_fn"]) for e in rank_strategies.STRATEGY_REGISTRY
    ]


def _empty_criteria_reasons():
    """Every strategy's verdict on a criteria dict where nothing could be measured — the cheapest
    way to make every `_unverified` branch fire at once."""
    config = scanner._load_config()
    for name, apply_tiering, strategy_config_fn in _all_apply_tiering():
        yield name, apply_tiering({}, strategy_config_fn(config))["reject_reasons"]


def _out_of_range_criteria():
    """A criteria vector that is measurable but fails every numeric gate in both directions, so the
    threshold branches fire rather than the unverified ones."""
    return {
        "price": 0.01,
        "term_structure": 5.0,
        "expected_move_dollars": 0.0,
        "expected_move_pct": 0.0,
        "atm_delta_abs": 0.99,
        "front_expiration_days": 999,
        "chain_complete": True,
        "combined_open_interest": 0,
        "bid_ask_spread_pct": 5.0,
        "has_weekly_options": False,
        "skew_abs": 0.0,
        "realized_move_dispersion_pct": 5.0,
        "avg_volume": 0,
        "winrate": 0.0,
        "winrate_sample_size": 1,
        "iv_rv_ratio": 0.0,
        "market_cap": 0,
        "combined_option_volume": 0,
    }


def test_reject_reason_map_covers_every_gate():
    """The map is derived alongside the gates rather than by them, so this is what stops the two
    drifting. A reason with no entry still records — but silently losing its measurement is the
    exact failure this whole trail exists to prevent."""
    config = scanner._load_config()
    seen = set()
    for _name, reasons in _empty_criteria_reasons():
        seen.update(reasons)
    for _name, apply_tiering, strategy_config_fn in _all_apply_tiering():
        seen.update(apply_tiering(_out_of_range_criteria(), strategy_config_fn(config))["reject_reasons"])

    # Reasons produced outside apply_tiering, by the whole-symbol shortcuts in rank_strategies.
    seen.update(rank_strategies._no_options_result("iron_fly")["reject_reasons"])

    unmapped = sorted(
        r
        for r in seen
        if r not in scanner._REJECT_REASON_MAP and r not in scanner.UNMEASURABLE_REJECT_REASONS
    )
    assert not unmapped, f"reject reasons with no measurement mapping: {unmapped}"
    assert len(seen) > 15, f"expected the sweep to trip most gates, only saw {sorted(seen)}"


def test_every_mapped_criterion_is_a_real_criteria_key():
    """Guards the other direction: a map entry pointing at a criteria key nothing ever writes would
    record `measured: null` forever and look like missing data rather than a broken map."""
    known = set(_out_of_range_criteria()) | {"move_tail_veto"}
    for reason, (criterion, _threshold, _cmp) in scanner._REJECT_REASON_MAP.items():
        assert criterion in known, f"{reason} maps to unknown criterion {criterion}"


# --------------------------------------------------------------------------- explain_reject_reasons


def test_it_records_the_measurement_and_the_bar_it_missed():
    detail = scanner.explain_reject_reasons(["price_below_minimum"], {"price": 8.02}, {"min_price": 10.0})[0]
    assert detail == {
        "reason": "price_below_minimum",
        "criterion": "price",
        "measured": 8.02,
        "threshold": 10.0,
        "comparator": "<",
    }


def test_a_ceiling_gate_records_the_ceiling():
    detail = scanner.explain_reject_reasons(
        ["bid_ask_spread_too_wide"], {"bid_ask_spread_pct": 2.0}, {"max_bid_ask_spread_pct": 0.15}
    )[0]
    assert detail["measured"] == 2.0 and detail["threshold"] == 0.15
    assert detail["comparator"] == ">"


def test_a_soft_criterion_records_whichever_bar_actually_applied():
    """A soft gate's threshold depends on that criterion's symbol_screen level, so recording the
    strict bar when the near-miss one was enforced would misstate the distance by the width of the
    band."""
    config = {
        "min_avg_volume": 1_500_000,
        "near_miss_min_avg_volume": 1_000_000,
        "_symbol_screen": {"avg_volume": "near_miss"},
    }
    detail = scanner.explain_reject_reasons(["avg_volume_below_minimum"], {"avg_volume": 900_000}, config)[0]
    assert detail["threshold"] == 1_000_000

    config["_symbol_screen"] = {"avg_volume": "pass"}
    strict = scanner.explain_reject_reasons(["avg_volume_below_minimum"], {"avg_volume": 900_000}, config)[0]
    assert strict["threshold"] == 1_500_000


def test_an_unverified_reason_has_no_threshold():
    """Nothing was compared to a bar — recording one would invent a comparison that never happened."""
    detail = scanner.explain_reject_reasons(["iv_rv_ratio_unverified"], {}, {})[0]
    assert detail["measured"] is None and detail["threshold"] is None
    assert detail["comparator"] == "unverified"


def test_a_retired_reason_still_records_as_a_bare_name():
    """`avg_volume_below_near_miss` has 1,036 rows in the ledger and no producer in the source any
    more. A reason the map has never heard of must not take the row down with it."""
    assert scanner.explain_reject_reasons(["avg_volume_below_near_miss"], {}, {}) == [
        {"reason": "avg_volume_below_near_miss"}
    ]


def test_no_reasons_explains_to_nothing():
    assert scanner.explain_reject_reasons([], {"price": 100.0}, {}) == []


# --------------------------------------------------------------------------- the recorded funnel


@pytest.fixture
def logged(monkeypatch):
    """Capture what the harness writes to scan_log."""
    from cherrypick.earnings import strat_test_harness as harness

    rows = []
    monkeypatch.setattr(
        harness.db_paper,
        "cmd_log_scan",
        lambda args: rows.append(__import__("json").loads(args.data)) or {"ok": True},
    )
    return rows


def test_a_screening_verdict_is_recorded_at_the_screen_stage(logged):
    from cherrypick.earnings import strat_test_harness as harness

    harness._log_scan_row(
        "2026-08-12",
        "SUZ",
        "iron_fly",
        "strat_test",
        stage="screen",
        outcome="rejected",
        reason="price_below_minimum",
        reject_details=[{"reason": "price_below_minimum", "measured": 8.02, "threshold": 10.0}],
    )
    row = logged[0]
    assert row["stage"] == "screen" and row["outcome"] == "rejected"
    assert row["reject_details"][0]["measured"] == 8.02


def test_an_accepted_candidate_that_never_opened_is_recorded_too(logged):
    """The gap this closes: a candidate that cleared the screen and then failed order building,
    sizing, the risk cap or quote availability left no record of having done so."""
    from cherrypick.earnings import strat_test_harness as harness

    harness._log_scan_row(
        "2026-08-12",
        "CSCO",
        "iron_fly",
        "strat_test",
        stage="execution",
        outcome="dropped",
        reason="order_build_failed: no strikes",
    )
    row = logged[0]
    assert row["stage"] == "execution" and row["outcome"] == "dropped"
    assert "order_build_failed" in row["reason"]


def test_the_prefilter_keeps_its_own_stage(logged):
    from cherrypick.earnings import strat_test_harness as harness

    harness._log_prefilter_skip("2026-08-12", "ALLO", "avg_volume 900 below near-miss floor 1000000")
    row = logged[0]
    assert row["stage"] == "prefilter" and row["strategy"] == "_prefilter"
    assert row["outcome"] == "skipped"
