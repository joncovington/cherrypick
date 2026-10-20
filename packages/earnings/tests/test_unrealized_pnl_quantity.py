"""A mark and a managed close are the realised close's own formula, at any quantity.

`strat_test_harness` stores `entry_credit` already multiplied by quantity and gives every leg in
`legs_json` its real contract count, so `scanner.compute_generic_exit_debit` returns a position
total too. `management.unrealized_pnl` multiplied by quantity once more from 2026-09-25: a
two-contract position marked and closed at twice its P&L. Every trade recorded until 2026-10-19
was one contract, so the guard is the multi-contract case below.
"""

from cherrypick.earnings import management, scanner


def _position(quantity: int) -> tuple[dict, list[dict], dict]:
    per_contract_credit = 1.20
    legs = [
        {"symbol": "X  261120C00100000", "action": "Sell to Open", "quantity": 1 * quantity},
        {"symbol": "X  261120C00105000", "action": "Buy to Open", "quantity": 1 * quantity},
    ]
    quotes = {
        "X  261120C00100000": {"bid": 0.95, "ask": 1.05, "mid": 1.00},
        "X  261120C00105000": {"bid": 0.35, "ask": 0.45, "mid": 0.40},
    }
    trade = {"entry_credit": per_contract_credit * quantity, "quantity": quantity}
    return trade, legs, quotes


def _harness_close(trade: dict, exit_debit: float) -> float:
    """strat_test_harness.cmd_run_closes: `pnl = (entry_credit - exit_debit) * 100`."""
    return (trade["entry_credit"] - exit_debit) * 100


def test_a_two_contract_mark_is_twice_the_one_contract_mark_and_matches_the_realised_close():
    marks = {}
    for quantity in (1, 2):
        trade, legs, quotes = _position(quantity)
        exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
        assert exit_debit is not None
        marks[quantity] = management.unrealized_pnl(trade, exit_debit)
        assert marks[quantity] == _harness_close(trade, exit_debit)
    assert round(marks[2], 6) == round(2 * marks[1], 6)


def test_the_guard_can_fail_on_the_double_multiplication():
    """The pre-fix arithmetic, multiplying by quantity a second time, must break the assertion."""
    trade, legs, quotes = _position(2)
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    doubled = (trade["entry_credit"] - exit_debit) * 100 * trade["quantity"]
    assert doubled != _harness_close(trade, exit_debit)
