import argparse
import json

import pytest

import scanner
import strategy_test_runner as runner

# --- the R1 seam: leg scaling must preserve structure ratios ---------------------

BWB_TEMPLATE = [
    {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
    {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
    {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
]


def test_scaled_legs_preserves_structure_ratio():
    assert [leg["quantity"] for leg in runner._scaled_legs(BWB_TEMPLATE, 1)] == [1, 2, 1]
    assert [leg["quantity"] for leg in runner._scaled_legs(BWB_TEMPLATE, 3)] == [3, 6, 3]


def test_scaled_legs_defaults_missing_ratio_to_one():
    flat = [{"symbol": "A", "action": "Sell to Open"}, {"symbol": "B", "action": "Buy to Open"}]
    assert [leg["quantity"] for leg in runner._scaled_legs(flat, 2)] == [2, 2]


def test_bwb_round_trip_pnl_is_flat_when_quotes_do_not_move():
    """Entry credit is priced off the order's net_debit (which counts the x2 body
    twice); the close prices legs_json through compute_generic_exit_debit. If
    scaling flattens the ratio, closing on unchanged quotes buys the body back
    once and shows a phantom profit of one body price x100 per contract. With
    ratio-preserving scaling the round trip is exactly flat."""
    near, body, far = 2.00, 3.00, 1.20
    # bid == ask so the exit's conservative side-of-spread pricing is unambiguous.
    quotes = {
        "NEAR": {"bid": near, "ask": near},
        "BODY": {"bid": body, "ask": body},
        "FAR": {"bid": far, "ask": far},
    }
    order = {"strategy": "broken_wing_butterfly", "net_debit": near + far - 2 * body}
    entry_credit = runner._per_contract_credit(order) * 1  # quantity 1
    legs = runner._scaled_legs(BWB_TEMPLATE, 1)
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    assert (entry_credit - exit_debit) * 100 == pytest.approx(0.0)


def test_bwb_round_trip_scales_linearly_with_quantity():
    quotes = {
        "NEAR": {"bid": 2.00, "ask": 2.10},
        "BODY": {"bid": 2.90, "ask": 3.00},
        "FAR": {"bid": 1.20, "ask": 1.30},
    }
    legs_q1 = runner._scaled_legs(BWB_TEMPLATE, 1)
    legs_q3 = runner._scaled_legs(BWB_TEMPLATE, 3)
    d1 = scanner.compute_generic_exit_debit(legs_q1, quotes)
    d3 = scanner.compute_generic_exit_debit(legs_q3, quotes)
    assert d3 == pytest.approx(d1 * 3)


# --- the R9 seam: a position that can't be priced at close must never vanish -----

def test_run_closes_records_attempts_and_reports_stranded(tmp_path, monkeypatch):
    """First failed sweep: the skip carries close_attempts=1 and nothing is stranded
    yet. Second failed sweep: attempts=2 and the position surfaces in `stranded`, which
    the orchestrator's exit heartbeat turns into a WARNING. The position itself stays
    open — closing it blind would be worse — but it can no longer disappear silently."""
    import db_paper

    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps({
        "order_id": "STUCK-1", "strategy": "iron_fly", "symbol": "HALT",
        "expiration": "2026-08-21", "entry_credit": 2.0,
        "legs_json": json.dumps([{"symbol": "H1", "action": "Sell to Open", "quantity": 1}]),
        "profile": "strat_test:iron_fly",
    })))

    monkeypatch.setattr(runner.rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(runner.scanner, "_load_config",
                        lambda *a, **k: {"close_quote_retries": 0})
    monkeypatch.setattr(runner, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(runner.scanner, "fetch_quote_and_expirations",
                        lambda symbol: {"ok": False})
    monkeypatch.setattr(runner, "_leg_quotes_for_symbols", lambda *a, **k: None)
    # Pretend today's EOD report already exists so the sweep skips report generation.
    report = tmp_path / "eod.md"
    report.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(runner, "_eod_report_path", lambda day: report)

    first = runner.cmd_run_closes(argparse.Namespace())
    assert first["ok"] is True
    assert first["closed"] == []
    assert first["skipped"][0]["reason"] == "leg_quotes_unavailable"
    assert first["skipped"][0]["close_attempts"] == 1
    assert first["stranded"] == []

    second = runner.cmd_run_closes(argparse.Namespace())
    assert second["skipped"][0]["close_attempts"] == 2
    assert [s["order_id"] for s in second["stranded"]] == ["STUCK-1"]
    # Still open: never close a position on quotes we don't have.
    assert len(db_paper.cmd_get_open_positions(argparse.Namespace())["positions"]) == 1


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
    # total_credit is not produced by any current strategy, but _per_contract_credit
    # keeps it as a general fallback for a future multi-credit-leg strategy:
    ({"strategy": "hypothetical_multi_credit", "total_credit": 1.25}, 1.25),
])
def test_per_contract_credit_covers_every_strategys_field_name(order, expected):
    """Regression test: each strategy's get_order result uses a different
    field name for its entry price -- iron_fly/iron_condor/directional use
    "credit", atm_calendar/double_calendar use "debit", and
    broken_wing_butterfly uses "net_debit". A naive
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
