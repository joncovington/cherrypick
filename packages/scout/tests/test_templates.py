
from cherrypick.scout.analytics import templates as _templates


def _chain(spot=100.0, strikes=None):
    """A synthetic one-expiration chain with monotone deltas and two-sided quotes."""
    strikes = strikes or [80, 85, 90, 95, 100, 105, 110, 115, 120]
    chain = []
    for strike in strikes:
        # A crude but monotone delta model around spot.
        call_delta = max(0.02, min(0.98, 0.5 + (spot - strike) / 40))
        for option_type, delta in (("C", call_delta), ("P", -(1 - call_delta))):
            mid = max(0.10, 5.0 - abs(strike - spot) * 0.2)
            chain.append(
                {
                    "symbol": f"X {option_type}{strike}",
                    "strike": float(strike),
                    "expiration": "2026-09-18",
                    "option_type": option_type,
                    "quote": {"bid": mid - 0.05, "ask": mid + 0.05, "mid": mid, "mark": mid},
                    "greeks": {"delta": delta, "gamma": 0.01, "theta": -0.02, "vega": 0.05, "iv": 0.3},
                }
            )
    return chain


def test_every_template_builds_on_a_healthy_chain():
    chain = _chain()
    for name in _templates.TEMPLATES:
        legs = _templates.build(name, chain, 100.0)
        assert legs, f"{name} failed to build"
        for leg in legs:
            assert leg["kind"] in ("call", "put", "stock")
            assert isinstance(leg["quantity"], int)


def test_put_vertical_credit_sells_higher_delta_and_buys_lower():
    legs = _templates.build("put_vertical_credit", _chain(), 100.0)
    short = next(lg for lg in legs if lg["quantity"] < 0)
    long_ = next(lg for lg in legs if lg["quantity"] > 0)
    assert short["kind"] == long_["kind"] == "put"
    assert abs(short["delta"]) > abs(long_["delta"])  # sell ~50d, buy ~25d
    assert short["strike"] > long_["strike"]


def test_iron_condor_orders_and_separates_its_wings():
    legs = _templates.build("iron_condor", _chain(), 100.0)
    kinds = [(lg["kind"], lg["quantity"] < 0) for lg in legs]
    assert kinds.count(("put", True)) == 1 and kinds.count(("call", True)) == 1
    short_call = next(lg for lg in legs if lg["kind"] == "call" and lg["quantity"] < 0)
    short_put = next(lg for lg in legs if lg["kind"] == "put" and lg["quantity"] < 0)
    assert short_call["strike"] > short_put["strike"]


def test_covered_call_holds_stock_and_a_conservative_call():
    legs = _templates.build("covered_call", _chain(), 100.0)
    assert legs[0]["kind"] == "stock" and legs[0]["quantity"] == 1
    call = legs[1]
    assert call["quantity"] == -1
    assert abs(call["delta"]) < 0.30  # the 15-20 delta conservative guidance


def test_build_returns_none_when_the_chain_cannot_support_the_shape():
    thin = _chain(strikes=[100])  # one strike: verticals/condors can't exist
    assert _templates.build("put_vertical_credit", thin, 100.0) is None
    assert _templates.build("iron_condor", thin, 100.0) is None
    assert _templates.build("long_call", thin, 100.0) is not None  # single legs still fine


def test_flip_mirrors_a_call_vertical_into_a_put_vertical():
    chain = _chain()
    call_vertical = _templates.build("call_vertical_credit", chain, 100.0)
    flipped = _templates.flip(call_vertical, chain, 100.0)
    assert flipped is not None
    assert all(lg["kind"] == "put" for lg in flipped)
    # Quantities preserved leg-for-leg; strikes reflected to the other side of spot.
    assert sorted(lg["quantity"] for lg in flipped) == sorted(lg["quantity"] for lg in call_vertical)


def test_flip_refuses_stock_legs():
    chain = _chain()
    covered = _templates.build("covered_call", chain, 100.0)
    assert _templates.flip(covered, chain, 100.0) is None


def test_adjust_width_moves_the_long_leg_outward_and_back():
    chain = _chain()
    vertical = _templates.build("put_vertical_credit", chain, 100.0)
    short = next(lg for lg in vertical if lg["quantity"] < 0)
    long_before = next(lg for lg in vertical if lg["quantity"] > 0)

    wider = _templates.adjust_width(vertical, chain, +1)
    assert wider is not None
    long_after = next(lg for lg in wider if lg["quantity"] > 0)
    assert abs(long_after["strike"] - short["strike"]) > abs(long_before["strike"] - short["strike"])

    narrower = _templates.adjust_width(wider, chain, -1)
    long_back = next(lg for lg in narrower if lg["quantity"] > 0)
    assert long_back["strike"] == long_before["strike"]


def test_adjust_width_refuses_to_collapse_onto_the_short_strike():
    chain = _chain(strikes=[95, 100, 105])
    vertical = [
        {"kind": "put", "strike": 100.0, "quantity": -1, "price": 2.0},
        {"kind": "put", "strike": 95.0, "quantity": 1, "price": 1.0},
    ]
    assert _templates.adjust_width(vertical, chain, -1) is None  # 95 -> 100 would collapse
