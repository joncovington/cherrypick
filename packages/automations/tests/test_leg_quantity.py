"""A leg's `side` is its contracts, signed: a 1-2-1 fly entered as Buy 1 / Sell 2 / Buy 1 is the same
trade, to the cent, as the fly built from four one-contract legs. And each leg's name is its own."""

from __future__ import annotations

from market import CALENDAR
from test_paper_loop import at, world

from cherrypick.automations import arms, config, engine, feed, pricing

BODY = {"rule": "prev_close", "points": 0.01, "round": "nearest"}


def _fly(base: dict, legs: list[dict]) -> dict:
    return {**base, "name": "fly", "legs": legs, "gates": [], "size": {"contracts": 1}}


def _two(base):
    """The body sold as one leg of two contracts."""
    return _fly(base, [
        {"name": "L2", "right": "P", "side": -2, "strike": BODY},
        {"name": "L1", "right": "P", "side": 1, "strike": {"rule": "from_leg", "leg": "L2", "points": 10}},
        {"name": "L3", "right": "P", "side": 1, "strike": {"rule": "from_leg", "leg": "L2", "points": -10}},
    ])  # fmt: skip


def _four(base):
    """The same fly with the body as two one-contract legs on one strike."""
    return _fly(base, [
        {"name": "s1", "right": "P", "side": -1, "strike": BODY},
        {"name": "s2", "right": "P", "side": -1, "strike": {"rule": "same_as", "leg": "s1"}},
        {"name": "l1", "right": "P", "side": 1, "strike": {"rule": "from_leg", "leg": "s1", "points": 10}},
        {"name": "l3", "right": "P", "side": 1, "strike": {"rule": "from_leg", "leg": "s1", "points": -10}},
    ])  # fmt: skip


def _snap(w, arm):
    spec = config.symbols(w.config, {arm["name"]: arm})["SPX"]
    snap, info = feed.snapshot_for(w.cache_path, w.conn, spec, "SPX", at(10, 0), config.defaults(w.config),
                                   [arm], events=CALENDAR)  # fmt: skip
    assert snap is not None, info
    return snap


def test_a_leg_of_two_contracts_is_the_same_trade_as_two_legs_of_one(tmp_path):
    w = world(tmp_path, keep=())
    base = {**w.arms["iron-fly-1000"], "enabled": True}
    two, four = _two(base), _four(base)
    assert arms.validate(two) == [] and arms.validate(four) == []
    snap = _snap(w, two)
    a, b = engine.decide(two, snap, costs=w.config), engine.decide(four, snap, costs=w.config)
    assert a.enter and b.enter, (a.reason, b.reason)
    ea, eb = a.entry, b.entry
    assert sorted((g.strike, g.side) for g in ea.legs) == [(5990.0, 1), (6000.0, -2), (6010.0, 1)]
    assert ea.prices == eb.prices
    assert (ea.max_profit, ea.max_loss) == (eb.max_profit, eb.max_loss)
    assert ea.fees_open == eb.fees_open and ea.fees_open > 0
    assert ea.slippage == eb.slippage
    assert pricing.contracts(ea.legs) == pricing.contracts(eb.legs) == 4
    pa, pb = engine.Position(ea, two), engine.Position(eb, four)
    ca, cb = (
        engine.close_position(pa, "time", snap, costs=w.config),
        engine.close_position(pb, "time", snap, costs=w.config),
    )
    assert ca is not None and cb is not None
    for k in ("risk", "entry_mid", "exit_mid", "gross", "fees", "settlement", "slippage", "net"):
        assert ca[k] == cb[k], k


def test_at_settlement_the_body_is_one_symbol_charged_once(tmp_path):
    """The broker charges settlement per symbol in the money: the two-contract leg is one symbol, where
    two one-contract legs on the same strike would be counted twice. The intrinsic value is the same."""
    w = world(tmp_path, keep=())
    base = {**w.arms["iron-fly-1000"], "enabled": True}
    two, four = _two(base), _four(base)
    snap = _snap(w, two)
    ea, eb = engine.decide(two, snap, costs=w.config).entry, engine.decide(four, snap, costs=w.config).entry
    settle = 5995.0  # the body and the upper wing finish in the money
    assert pricing.intrinsic_value(ea.legs, settle) == pricing.intrinsic_value(eb.legs, settle)
    assert pricing.settlement_fee(ea.legs, settle) < pricing.settlement_fee(eb.legs, settle)


def test_a_side_is_a_whole_number_of_contracts_never_zero_and_never_past_the_cap():
    base = arms.ARMS["iron-fly-1000"]
    for bad in (0, True, 1.5, arms.MAX_LEG_QUANTITY + 1, -arms.MAX_LEG_QUANTITY - 1, "1"):
        legs = [{**base["legs"][0], "side": bad}, *base["legs"][1:]]
        assert any("side" in p for p in arms.validate({**base, "legs": legs})), bad
    for good in (-arms.MAX_LEG_QUANTITY, -2, 2, arms.MAX_LEG_QUANTITY):
        legs = [{**base["legs"][0], "side": good}, *base["legs"][1:]]
        assert arms.validate({**base, "legs": legs}) == [], good


def test_two_legs_may_not_share_a_name():
    base = arms.ARMS["iron-fly-1000"]
    legs = [*base["legs"], {**base["legs"][2], "name": "short_put"}]
    problems = arms.validate({**base, "legs": legs})
    assert any(".name: 'short_put' is already a leg" in p for p in problems)


def test_no_shipped_automation_repeats_a_leg_name_or_holds_more_than_one_contract_a_leg():
    for name, arm in arms.ARMS.items():
        names = [g["name"] for g in arm["legs"]]
        assert len(names) == len(set(names)), name
        assert all(abs(g["side"]) == 1 for g in arm["legs"]), name
