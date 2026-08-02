import argparse
import json

import pytest

from cherrypick.earnings import scanner
from cherrypick.earnings import strategy_test_runner as runner

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


# --- end-to-end: entry -> close through the real save/price/close pipeline -------


def test_entry_to_close_round_trip_prices_the_bwb_ratio_correctly(tmp_path, monkeypatch):
    """The audit's headline gap: no test ever drove cmd_run_entries -> cmd_run_closes on
    one DB. This one opens a broken-wing butterfly (the 1-2-1 shape R1 flattened) through
    the real order->size->cost->save pipeline and closes it on UNCHANGED zero-spread
    quotes: the round-trip P&L must be exactly zero (the body bought back twice), entry
    costs must charge 4 contracts, and the slippage columns must be recorded."""
    from cherrypick.earnings import db_paper

    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())

    config = {"close_quote_retries": 0, "strategies": {"broken_wing_butterfly": {}}}
    monkeypatch.setattr(runner.rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(runner.rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(runner.scanner, "_load_config", lambda *a, **k: config)
    monkeypatch.setattr(runner, "_run_bounded", lambda fn, timeout, *a, **k: [])
    monkeypatch.setattr(runner, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(runner, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_write_eod_report", lambda day: None)
    monkeypatch.setattr(runner, "_write_eod_analysis", lambda day: None)

    near, body, far = 2.00, 3.00, 1.20
    order = {
        "ok": True,
        "strategy": "broken_wing_butterfly",
        "net_debit": near + far - 2 * body,  # -2.80: a 2.80 net CREDIT
        "underlying_price": 100.0,
        "expiration": "2026-08-21",
        "order": {
            "legs": [
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]
        },
    }
    monkeypatch.setitem(runner._ORDER_FNS, "broken_wing_butterfly", lambda *a: order)
    monkeypatch.setattr(
        runner.sizing,
        "compute_position_size",
        lambda *a: {"ok": True, "quantity": 1, "capital_at_risk": 220.0},
    )
    # bid == ask: zero spread -> zero slippage, and the exit's side-of-spread pricing
    # equals the entry mids, so any non-zero P&L is an accounting bug.
    quotes = {
        "NEAR": {"bid": near, "ask": near, "iv": 0.5},
        "BODY": {"bid": body, "ask": body, "iv": 0.6},
        "FAR": {"bid": far, "ask": far, "iv": 0.4},
    }
    monkeypatch.setattr(runner, "_leg_quotes_for_symbols", lambda u, syms, p: {s: quotes[s] for s in syms})
    monkeypatch.setattr(
        runner,
        "_parallel_scan",
        lambda *a, **k: [
            (
                {"symbol": "XYZ", "date": "2026-07-28", "timing": "AMC"},
                [
                    {
                        "name": "broken_wing_butterfly",
                        "accepted": True,
                        "reject_reasons": [],
                        "criteria": {},
                        "composite_score": 1.0,
                    }
                ],
                None,
            )
        ],
    )

    entered = runner.cmd_run_entries(argparse.Namespace(date="2026-07-28"))
    assert entered["ok"] is True and len(entered["opened"]) == 1
    # 4 contracts (1+2+1) x $1 commission + 4 x $0.14 pass-through, zero slippage.
    assert entered["opened"][0]["entry_cost"] == pytest.approx(4.56)

    row = db_paper.cmd_get_open_positions(argparse.Namespace())["positions"][0]
    assert [leg["quantity"] for leg in json.loads(row["legs_json"])] == [1, 2, 1]
    assert row["entry_credit"] == pytest.approx(2.80)
    assert row["entry_slippage"] == pytest.approx(0.0)

    monkeypatch.setattr(runner.scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 100.0})
    closed = runner.cmd_run_closes(argparse.Namespace())
    assert closed["closed"][0]["pnl"] == pytest.approx(0.0)
    assert closed["closed"][0]["exit_cost"] == pytest.approx(0.56)  # $0 close commission

    done = db_paper.cmd_get_open_positions(argparse.Namespace())["positions"]
    assert done == []


# --- the R8 seam: multi-day strategies are managed, not force-closed -------------


def _close_sweep_env(tmp_path, monkeypatch, quotes_by_symbol, config=None):
    """Common harness for cmd_run_closes tests: isolated DB, no network, canned quotes."""
    from cherrypick.earnings import db_paper

    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    monkeypatch.setattr(runner.rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(
        runner.scanner, "_load_config", lambda *a, **k: config or {"close_quote_retries": 0, "strategies": {}}
    )
    monkeypatch.setattr(runner, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(
        runner.scanner, "fetch_quote_and_expirations", lambda symbol: {"ok": True, "price": 100.0}
    )
    monkeypatch.setattr(
        runner,
        "_leg_quotes_for_symbols",
        lambda underlying, leg_symbols, price: {s: quotes_by_symbol[s] for s in leg_symbols},
    )
    report = tmp_path / "eod.md"
    report.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(runner, "_eod_report_path", lambda day: report)
    return db_paper


def _far_expiration(days=30):
    from datetime import date, timedelta

    return (date.today() + timedelta(days=days)).isoformat()


def _save_calendar(db_paper, strategy, entry_credit, legs, trade_legs=None):
    spec = {
        "order_id": f"{strategy}-1",
        "strategy": strategy,
        "symbol": "CAL",
        "expiration": _far_expiration(),
        "entry_credit": entry_credit,
        "legs_json": json.dumps(legs),
        "profile": f"strat_test:{strategy}",
    }
    if trade_legs:
        spec["legs"] = trade_legs
    db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps(spec)))


def test_atm_calendar_is_held_when_its_strategy_says_hold(tmp_path, monkeypatch):
    """The old sweep force-closed every position the morning after entry — a one-night
    structure nobody intends to trade. A calendar at neither profit target nor stop,
    far from expiry, must be HELD, and a hold is a decision, not a close failure."""
    legs = [
        {"symbol": "FRONT", "action": "Sell to Open", "quantity": 1},
        {"symbol": "BACK", "action": "Buy to Open", "quantity": 1},
    ]
    # exit_debit = +1.0 (buy front back) - 4.0 (sell back) = -3.0 -> nets exactly the
    # 3.00 debit paid: zero profit, zero loss.
    quotes = {"FRONT": {"bid": 1.0, "ask": 1.0}, "BACK": {"bid": 4.0, "ask": 4.0}}
    db_paper = _close_sweep_env(tmp_path, monkeypatch, quotes)
    _save_calendar(db_paper, "atm_calendar", -3.00, legs)

    result = runner.cmd_run_closes(argparse.Namespace())
    assert result["closed"] == []
    assert result["skipped"] == []
    assert [h["order_id"] for h in result["held"]] == ["atm_calendar-1"]
    assert len(db_paper.cmd_get_open_positions(argparse.Namespace())["positions"]) == 1


def test_atm_calendar_closes_on_its_own_profit_target(tmp_path, monkeypatch):
    legs = [
        {"symbol": "FRONT", "action": "Sell to Open", "quantity": 1},
        {"symbol": "BACK", "action": "Buy to Open", "quantity": 1},
    ]
    # Nets 4.00 against a 3.00 debit: profit 1.00 >= 3.00 * 0.25 -> profit_target.
    quotes = {"FRONT": {"bid": 1.0, "ask": 1.0}, "BACK": {"bid": 5.0, "ask": 5.0}}
    db_paper = _close_sweep_env(tmp_path, monkeypatch, quotes)
    _save_calendar(db_paper, "atm_calendar", -3.00, legs)

    result = runner.cmd_run_closes(argparse.Namespace())
    assert result["held"] == []
    assert result["closed"][0]["reason"] == "profit_target"
    assert db_paper.cmd_get_open_positions(argparse.Namespace())["positions"] == []


def test_overnight_strategy_still_closes_unconditionally(tmp_path, monkeypatch):
    """The five overnight strategies keep the Step 3 close-window backstop: whatever is
    open at 09:45 closes regardless of P&L — that IS their design."""
    legs = [
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "LC", "action": "Buy to Open", "quantity": 1},
    ]
    quotes = {"SC": {"bid": 2.0, "ask": 2.1}, "LC": {"bid": 0.5, "ask": 0.6}}
    db_paper = _close_sweep_env(tmp_path, monkeypatch, quotes)
    _save_calendar(db_paper, "iron_fly", 2.00, legs)

    result = runner.cmd_run_closes(argparse.Namespace())
    assert result["held"] == []
    assert result["closed"][0]["reason"] == "close_window"


def test_double_calendar_leg_stop_closes_whole_position_in_harness(tmp_path, monkeypatch):
    """A front short past its delta stop: the strategy says close_side; the harness's
    single-close paper accounting exits the whole position, with the reason suffixed so
    the simplification stays visible in the journal. trade_legs sweep closes with it."""
    legs = [
        {"symbol": "FC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "FP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "BC", "action": "Buy to Open", "quantity": 1},
        {"symbol": "BP", "action": "Buy to Open", "quantity": 1},
    ]
    trade_legs = [
        {"leg_role": "front_call", "symbol": "FC", "action": "Sell to Open", "quantity": 1},
        {"leg_role": "front_put", "symbol": "FP", "action": "Sell to Open", "quantity": 1},
        {"leg_role": "back_call", "symbol": "BC", "action": "Buy to Open", "quantity": 1},
        {"leg_role": "back_put", "symbol": "BP", "action": "Buy to Open", "quantity": 1},
    ]
    # cost_to_close = 1+1-2-2 = -2 -> nets 2.00 on a 2.00 debit: neither profit target
    # (needs 2.50) nor stop (needs <= -2.00 net). Front call delta 0.50 >= 0.45 default.
    quotes = {
        "FC": {"bid": 0.9, "ask": 1.0, "delta": 0.50},
        "FP": {"bid": 0.9, "ask": 1.0, "delta": -0.20},
        "BC": {"bid": 2.0, "ask": 2.1, "delta": 0.40},
        "BP": {"bid": 2.0, "ask": 2.1, "delta": -0.30},
    }
    db_paper = _close_sweep_env(tmp_path, monkeypatch, quotes)
    _save_calendar(db_paper, "double_calendar", -2.00, legs, trade_legs=trade_legs)

    result = runner.cmd_run_closes(argparse.Namespace())
    assert result["held"] == []
    assert result["closed"][0]["reason"] == "leg_stop_overnight_gap_close_all"
    assert db_paper.cmd_get_open_positions(argparse.Namespace())["positions"] == []
    legs_left = db_paper.cmd_get_open_legs(argparse.Namespace(order_id="double_calendar-1"))["legs"]
    assert legs_left == []


# --- the R9 seam: a position that can't be priced at close must never vanish -----


def test_run_closes_records_attempts_and_reports_stranded(tmp_path, monkeypatch):
    """First failed sweep: the skip carries close_attempts=1 and nothing is stranded
    yet. Second failed sweep: attempts=2 and the position surfaces in `stranded`, which
    the orchestrator's exit heartbeat turns into a WARNING. The position itself stays
    open — closing it blind would be worse — but it can no longer disappear silently."""
    from cherrypick.earnings import db_paper

    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    db_paper.cmd_save_trade(
        argparse.Namespace(
            data=json.dumps(
                {
                    "order_id": "STUCK-1",
                    "strategy": "iron_fly",
                    "symbol": "HALT",
                    "expiration": "2026-08-21",
                    "entry_credit": 2.0,
                    "legs_json": json.dumps([{"symbol": "H1", "action": "Sell to Open", "quantity": 1}]),
                    "profile": "strat_test:iron_fly",
                }
            )
        )
    )

    monkeypatch.setattr(runner.rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(runner.scanner, "_load_config", lambda *a, **k: {"close_quote_retries": 0})
    monkeypatch.setattr(runner, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(runner.scanner, "fetch_quote_and_expirations", lambda symbol: {"ok": False})
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


@pytest.mark.parametrize(
    "order,expected",
    [
        ({"strategy": "iron_fly", "credit": 0.90}, 0.90),
        ({"strategy": "iron_condor", "credit": 0.80}, 0.80),
        ({"strategy": "directional_credit_spread", "credit": 0.40}, 0.40),
        ({"strategy": "atm_calendar", "debit": 3.12}, -3.12),
        ({"strategy": "double_calendar", "debit": 0.45}, -0.45),
        ({"strategy": "broken_wing_butterfly", "net_debit": 0.45}, -0.45),
        # total_credit is not produced by any current strategy, but _per_contract_credit
        # keeps it as a general fallback for a future multi-credit-leg strategy:
        ({"strategy": "hypothetical_multi_credit", "total_credit": 1.25}, 1.25),
    ],
)
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
        "iv_rv_ratio": 1.1,
        "realized_move_dispersion_pct": 0.12,
        "skew_abs": 0.03,
        "winrate": 0.6,
        "avg_volume": 999999,
    }
    ctx = runner._entry_context(criteria, composite_score=0.76)
    assert ctx == {
        "iv_rv_ratio": 1.1,
        "dispersion": 0.12,
        "skew_abs": 0.03,
        "winrate": 0.6,
        "composite_score": 0.76,
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
