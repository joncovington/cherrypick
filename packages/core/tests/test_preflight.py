"""core.preflight: dry-run review of trades under consideration. It must never be able to place one."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cherrypick.core import preflight
from cherrypick.core.preflight import __main__ as cli

PKG = Path(preflight.__file__).parent

#: The shape of a real dry-run result (SPY Feb 580/560P, 2026-11-21), account changed.
ACCEPTED = {
    "ok": True,
    "dry_run": True,
    "account_number": "5WX12345",
    "response": {
        "buying_power_effect": {
            "change_in_margin_requirement": "0.0",
            "change_in_buying_power": "-163.26",
            "current_buying_power": "4524.0",
            "new_buying_power": "4360.74",
            "is_spread": True,
            "effect": "Debit",
        },
        "fee_calculation": {
            "regulatory_fees": "-0.06",
            "clearing_fees": "-0.2",
            "commission": "-2.0",
            "total_fees": "-2.26",
        },
        "warnings": [
            {
                "code": "tif.next_valid_session",
                "message": "Your order will begin working during next valid session.",
            }
        ],
        "errors": None,
    },
}
REFUSED = {
    "ok": False,
    "error": "TastytradeError: margin_check_failed: Your account does not have sufficient buying power "
    "available for this order. account-number=5WX12345\n",
}


class FakeBroker:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def place(self, spec, live):
        self.calls.append((spec, live))
        return self.result


def _trade(**kw):
    t = {
        "name": "SPY Feb 580/560P",
        "price": 1.61,
        "price_effect": "debit",
        "legs": [
            {"symbol": ".SPY270219P580", "action": "buy to open"},
            {"symbol": "SPY   270219P00560000", "action": "sell to open"},
        ],
    }
    t.update(kw)
    return t


def test_every_submission_is_a_literal_dry_run():
    """The guard: in this package's source, every `.place(...)` passes `live=False` as a literal,
    and nothing names `dry_run=False` or `live=True`. Shown failing by editing review() to
    `live=True` (and to `live=flag`) before it was committed."""
    places = 0
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("place", "place_order", "replace", "replace_order"):
                    places += 1
                    live = [k for k in node.keywords if k.arg == "live"]
                    assert len(live) == 1 and isinstance(live[0].value, ast.Constant), (
                        f"{path.name}: {ast.unparse(node)}"
                    )
                    assert live[0].value.value is False, f"{path.name}: {ast.unparse(node)}"
                    assert not node.args[1:], f"{path.name}: positional live in {ast.unparse(node)}"
            if isinstance(node, ast.keyword) and node.arg in ("dry_run", "live"):
                if isinstance(node.value, ast.Constant):
                    assert node.value.value is not (node.arg == "live"), f"{path.name}: {ast.unparse(node)}"
    assert places >= 1


def test_review_submits_dry_run_and_builds_occ_legs():
    broker = FakeBroker(ACCEPTED)
    (row,) = preflight.review([_trade()], broker)
    ((spec, live),) = broker.calls
    assert live is False
    assert spec["price"] == 1.61 and spec["price_effect"] == "debit" and spec["order_type"] == "Limit"
    assert [leg["symbol"] for leg in spec["legs"]] == ["SPY   270219P00580000", "SPY   270219P00560000"]
    assert {leg["instrument_type"] for leg in spec["legs"]} == {"Equity Option"}
    assert row["accepted"] and row["dry_run"]
    assert row["bpr"] == pytest.approx(163.26)
    assert row["new_buying_power"] == pytest.approx(4360.74)
    assert row["fees"] == pytest.approx(2.26)
    assert row["account"] == "****2345"
    assert row["warnings"] == [
        "tif.next_valid_session: Your order will begin working during next valid session."
    ]


def test_a_refusal_is_reported_with_the_account_masked():
    (row,) = preflight.review([_trade()], FakeBroker(REFUSED))
    assert row["accepted"] is False
    assert "margin_check_failed" in row["refusal"]
    assert "5WX12345" not in row["refusal"]


def test_a_trade_that_cannot_be_built_never_reaches_the_broker():
    broker = FakeBroker(ACCEPTED)
    rows = preflight.review(
        [
            _trade(name="bad action", legs=[{"symbol": ".SPY270219P580", "action": "buy"}]),
            _trade(name="no price", price=None),
            _trade(),
        ],
        broker,
    )
    assert [r["accepted"] for r in rows] == [False, False, True]
    assert rows[0]["refusal"].startswith("not built")
    assert len(broker.calls) == 1


def test_a_live_result_stops_the_review():
    with pytest.raises(RuntimeError):
        preflight.review([_trade()], FakeBroker({"ok": True, "dry_run": False}))


def test_shares_are_equity_legs():
    assert preflight.order_leg("spy", "buy to open", 100) == {
        "instrument_type": "Equity",
        "symbol": "SPY",
        "action": "buy to open",
        "quantity": 100,
    }


def test_the_cli_trade_syntax():
    t = cli.parse_trade(["cal", "2.92d", "-.SPY270115P600", "+2.SPY270219P600"])
    assert t["price"] == 2.92 and t["price_effect"] == "debit"
    assert t["legs"] == [
        {"symbol": ".SPY270115P600", "action": "sell to open", "quantity": 1},
        {"symbol": ".SPY270219P600", "action": "buy to open", "quantity": 2},
    ]
    assert cli.parse_trade(["x", "0.80c", "+SPY"], closing=True)["legs"][0]["action"] == "buy to close"
    with pytest.raises(ValueError):
        cli.parse_trade(["x", "1.61", "+SPY"])


def test_a_sell_leg_survives_argparse(monkeypatch):
    """`-.SPY...` would be read as an unknown option; main() must hand it to the trade intact."""
    seen = {}

    def fake_review(trades, _broker):
        seen["trades"] = trades
        return [{"name": t["name"], "accepted": True} for t in trades]

    monkeypatch.setattr(cli.preflight, "make_broker", lambda **_k: object())
    monkeypatch.setattr(cli.preflight, "review", fake_review)
    argv = [
        "--keyring-service",
        "svc",
        "--trade",
        "s",
        "1.61d",
        "+.SPY270219P580",
        "-.SPY270219P560",
        "--json",
    ]
    assert cli.main(argv) == 0
    (trade,) = seen["trades"]
    assert [leg["action"] for leg in trade["legs"]] == ["buy to open", "sell to open"]
    assert trade["legs"][1]["symbol"] == ".SPY270219P560"


def test_the_adapter_is_the_read_only_one(monkeypatch):
    """`make_broker` hands back account_alerts' adapter, whose live gates are never met."""
    broker = preflight.make_broker(keyring_service="cherrypick-test-service")
    assert broker._live_gates()  # non-empty: a live submit would be refused before the broker
    with pytest.raises(ValueError):
        preflight.make_broker()
