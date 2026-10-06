"""Orders built through `core.broker.build_order`, serialized by the INSTALLED tastytrade SDK.

Every other broker test injects fakes. This one does not, because the risk it pins lives in the
SDK: tastytrade 13 dropped `price_effect` and reads credit/debit from the price's SIGN, and a
`NewOrder(price=1.50, price_effect=Debit)` built the 12 way serializes as a CREDIT under 13 -- no
error, the opposite side of the trade. `build_order` signs the price itself (credit positive, debit
negative) and never passes `price_effect` on, so its orders are identical on 12.4.3 and 13.2.3; that
was checked against the broker's own dry run on 2026-10-05, and this pins it on every SDK CI
installs. Core's CI job installs the `broker` extra, so these run against the real SDK.
"""

import json
import warnings

import pytest

from cherrypick.core import broker, execution  # both import the SDK lazily

tastytrade = pytest.importorskip("tastytrade")

LEG = {"instrument_type": "Equity Option", "symbol": "SPY   261016P00660000", "quantity": 1}


def _sent(spec: dict) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # NewOrder is deprecated in 13, not removed
        order = broker.build_order(spec)
    return json.loads(order.model_dump_json(by_alias=True, exclude_none=True))


@pytest.mark.parametrize(
    ("effect", "action", "expected"),
    [
        ("credit", "sell to open", "Credit"),
        ("Credit", "sell to open", "Credit"),
        ("debit", "buy to open", "Debit"),
        ("Debit", "buy to open", "Debit"),
        ("debit", "buy to close", "Debit"),
        ("credit", "sell to close", "Credit"),
    ],
)
def test_an_order_serializes_the_side_its_spec_asks_for(effect, action, expected):
    sent = _sent({"legs": [{**LEG, "action": action}], "price": "1.50", "price_effect": effect})
    assert (sent["price"], sent["price-effect"]) == ("1.50", expected)


def test_the_spec_s_effect_wins_over_a_price_s_own_sign():
    """A spec price may arrive signed either way; the declared effect decides the side."""
    sent = _sent({"legs": [{**LEG, "action": "sell to open"}], "price": "-0.40", "price_effect": "credit"})
    assert (sent["price"], sent["price-effect"]) == ("0.40", "Credit")
    sent = _sent({"legs": [{**LEG, "action": "buy to open"}], "price": "0.40", "price_effect": "debit"})
    assert (sent["price"], sent["price-effect"]) == ("0.40", "Debit")


def test_a_multi_leg_credit_spread_keeps_every_leg_and_its_side():
    legs = [
        {**LEG, "action": "sell to open"},
        {**LEG, "symbol": "SPY   261016P00655000", "action": "buy to open"},
    ]
    sent = _sent({"legs": legs, "price": "0.05", "price_effect": "credit"})
    assert [leg["action"] for leg in sent["legs"]] == ["Sell to Open", "Buy to Open"]
    assert sent["price-effect"] == "Credit"


def test_the_broker_s_debit_price_parses_signed_the_way_the_ledgers_read_it():
    from tastytrade.order import PlacedOrder

    placed = PlacedOrder.model_validate(
        {
            "id": 1,
            "account-number": "5WX00000",
            "time-in-force": "Day",
            "order-type": "Limit",
            "size": 1,
            "underlying-symbol": "SPY",
            "underlying-instrument-type": "Equity",
            "status": "Filled",
            "cancellable": False,
            "editable": False,
            "edited": False,
            "updated-at": "2026-10-05T12:00:00Z",
            "legs": [],
            "price": "1.50",
            "price-effect": "Debit",
        }
    )
    assert str(placed.price) == "-1.50"
    assert broker._serialize_placed_order(1, placed)["price"] == "-1.50"


def test_a_dry_run_order_without_an_id_yields_no_order_id_not_a_crash():
    """13's dry run returns an `UnplacedOrder` with no `id` (12 sent `id = -1`)."""
    from tastytrade import order as sdk_order

    if not hasattr(sdk_order, "UnplacedOrder"):
        pytest.skip("this SDK still returns id -1 on a dry run")
    unplaced = sdk_order.UnplacedOrder.model_validate(
        {
            "account-number": "5WX00000",
            "time-in-force": "Day",
            "order-type": "Limit",
            "size": 1,
            "underlying-symbol": "SPY",
            "underlying-instrument-type": "Equity",
            "status": "Received",
            "cancellable": True,
            "editable": True,
            "edited": False,
            "updated-at": "2026-10-05T12:00:00Z",
            "legs": [],
            "price": "0.05",
            "price-effect": "Credit",
        }
    )
    result = {"ok": True, "dry_run": True, "response": {"order": broker.serialize(unplaced)}}
    assert execution.order_id_of(result) is None
    assert broker._serialize_placed_order(None, unplaced)["price"] == "0.05"
