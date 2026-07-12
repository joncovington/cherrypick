import pytest

import strategy_test_runner as runner


def test_occ_expiration_parses_real_symbols():
    assert runner._occ_expiration("PEP   260710C00145000") == "2026-07-10"
    assert runner._occ_expiration("PEP   260821C00145000") == "2026-08-21"


def test_occ_expiration_handles_short_root_symbol():
    # Root symbols are left-padded to 6 chars; a 1-char root still works
    # since the parser reads the fixed-width suffix from the right.
    assert runner._occ_expiration("F     260710C00012500") == "2026-07-10"


@pytest.mark.parametrize("order,expected", [
    ({"strategy": "iron_fly", "credit": 0.90}, 0.90),
    ({"strategy": "iron_condor", "credit": 0.80}, 0.80),
    ({"strategy": "directional_credit_spread", "credit": 0.40}, 0.40),
    ({"strategy": "atm_calendar", "debit": 3.12}, -3.12),
    ({"strategy": "double_calendar", "debit": 0.45}, -0.45),
    ({"strategy": "broken_wing_butterfly", "net_debit": 0.45}, -0.45),
    ({"strategy": "reverse_fly", "net_debit": 1.75, "max_loss": 3.0}, -1.75),
    # total_credit is not produced by any current strategy, but _per_contract_credit
    # keeps it as a general fallback for a future multi-credit-leg strategy:
    ({"strategy": "hypothetical_multi_credit", "total_credit": 1.25}, 1.25),
])
def test_per_contract_credit_covers_every_strategys_field_name(order, expected):
    """Regression test: each strategy's get_order result uses a different
    field name for its entry price -- iron_fly/iron_condor/directional use
    "credit", atm_calendar/double_calendar use "debit", and
    broken_wing_butterfly/reverse_fly use "net_debit". A naive
    `order["credit"] if "credit" in order else -order["debit"]` would
    KeyError on the net_debit strategies."""
    assert runner._per_contract_credit(order) == pytest.approx(expected)


def test_per_contract_credit_raises_on_unrecognized_shape():
    with pytest.raises(KeyError):
        runner._per_contract_credit({"strategy": "mystery_strategy"})


def test_entry_context_extracts_expected_fields():
    criteria = {
        "iv_rv_ratio": 1.1, "realized_move_dispersion_pct": 0.12,
        "skew_abs": 0.03, "winrate": 0.6, "avg_volume": 999999,
    }
    ctx = runner._entry_context(criteria, composite_score=0.76)
    assert ctx == {
        "iv_rv_ratio": 1.1, "dispersion": 0.12, "skew_abs": 0.03,
        "winrate": 0.6, "composite_score": 0.76,
    }


def test_avg_sold_iv_averages_only_short_legs():
    # iron_fly-shaped: two short legs (IV 0.40, 0.42), two long legs (IV
    # 0.35, 0.33) -- only the short (Sell to Open) legs should count.
    legs = [
        {"symbol": "SC", "action": "Sell to Open"},
        {"symbol": "SP", "action": "Sell to Open"},
        {"symbol": "LC", "action": "Buy to Open"},
        {"symbol": "LP", "action": "Buy to Open"},
    ]
    quotes = {
        "SC": {"bid": 1, "ask": 1.1, "iv": 0.40},
        "SP": {"bid": 1, "ask": 1.1, "iv": 0.42},
        "LC": {"bid": 0.5, "ask": 0.6, "iv": 0.35},
        "LP": {"bid": 0.5, "ask": 0.6, "iv": 0.33},
    }
    assert runner._avg_sold_iv(legs, quotes) == pytest.approx((0.40 + 0.42) / 2)


def test_avg_sold_iv_single_short_leg():
    legs = [
        {"symbol": "SP", "action": "Sell to Open"},
        {"symbol": "LP", "action": "Buy to Open"},
    ]
    quotes = {
        "SP": {"bid": 1, "ask": 1.1, "iv": 0.55},
        "LP": {"bid": 3, "ask": 3.2, "iv": 0.30},
    }
    assert runner._avg_sold_iv(legs, quotes) == pytest.approx(0.55)


def test_avg_sold_iv_returns_none_when_iv_missing():
    legs = [{"symbol": "SC", "action": "Sell to Open"}]
    quotes = {"SC": {"bid": 1, "ask": 1.1, "iv": None}}
    assert runner._avg_sold_iv(legs, quotes) is None


def test_avg_sold_iv_returns_none_with_no_matching_quote():
    legs = [{"symbol": "SC", "action": "Sell to Open"}]
    quotes = {}
    assert runner._avg_sold_iv(legs, quotes) is None
