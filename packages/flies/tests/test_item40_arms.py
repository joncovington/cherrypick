"""The `strand-stop` and `whole-fly` arms (research item 40, 2026-10-31): control, plus a fixed stop on
an uncompleted vertical, or the whole butterfly bought in one order. Invented ticks only.

Each rule is shown to fail without it: control, run on the same ticks, does the other thing.
"""

from datetime import datetime, timedelta

import pytest
from test_book import one_arm_config
from test_engine import params, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import clock, engine, fly
from cherrypick.flies import db as dbmod

NOON = datetime(2026, 7, 20, 12, 0, tzinfo=clock.ET)
SPOT = 5998.0  # centre 6000, a put spread 6000/5995


def q(bid, ask):
    """A replay quote: bid, ask and the mid the replay stamps on every leg."""
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}


def puts(p6000, p5995, p6005=None):
    out = {6000: q(*p6000), 5995: q(*p5995), 5990: q(1.0, 1.4)}
    out[6005] = q(*(p6005 or (8.6, 9.0)))
    return out


#: A cheap entry: mid credit 1.10, 1.075 after the 0.125 haircut (the 20% floor is 1.00). The natural
#: to buy it back is 1.20; half the maximum loss (3.925) puts the stop at a natural of 3.0375.
CHEAP = puts((3.6, 3.7), (2.5, 2.6))
NEAR = puts((4.6, 4.8), (1.8, 2.0))  # natural 3.00: under the stop
OVER = puts((4.9, 5.1), (1.8, 2.0))  # natural 3.30: over it
#: The completing spread cheapens (debit 0.65 against the 1.075 credit): control completes.
CHEAP_COMPLETION = puts((3.6, 3.7), (2.5, 2.6), (4.2, 4.3))


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def config(arm, **block):
    cfg = one_arm_config(entry_modes=["legged"])
    cfg["arms"] = {arm: dict(block)}
    return cfg


def stop_config(arm="strand-stop"):
    return config(arm, strand_stop={"max_loss_fraction": 0.5})


def whole_config(arm="whole-fly"):
    return config(arm, whole_fly=True)


def tick(conn, cfg, arm, seconds, put_quotes=None, **over):
    snap = snapshot(underlying_price=over.pop("spot", SPOT), **over)
    if put_quotes is not None:
        snap["puts"] = put_quotes
    with clock.pinned(NOON + timedelta(seconds=seconds)):
        return bookmod.process_snapshot(snap, cfg, conn, arm)


def rows(conn, arm):
    return [dict(r) for r in dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", arm, "SPX"))]


def settle(conn, cfg, arm, price):
    return bookmod.settle_book(conn, "2026-07-20", arm, "SPX", price, cfg)


# --------------------------------------------------------------------------- registration and settings
def test_both_arms_are_registered_and_in_no_config():
    assert "strand-stop" in engine.ARMS and "whole-fly" in engine.ARMS
    import json
    from pathlib import Path

    example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text("utf-8"))
    assert "strand-stop" not in example.get("arms", {}) and "whole-fly" not in example.get("arms", {})


def test_the_settings_are_off_unless_an_arm_sets_them_and_a_bad_multiple_raises():
    assert engine.strand_stop_settings(params()) is None
    assert engine.strand_stop_settings(params(strand_stop={})) is None
    assert engine.strand_stop_settings(params(strand_stop={"max_loss_fraction": 0.5})) == {
        "max_loss_fraction": 0.5
    }
    assert engine.whole_fly_enabled(params()) is False
    assert engine.whole_fly_enabled(params(whole_fly=True)) is True
    for bad in (0.0, 1.0, 1.5, -0.5):
        with pytest.raises(ValueError):
            engine.strand_stop_settings(params(strand_stop={"max_loss_fraction": bad}))


def test_the_stop_can_fire_at_every_credit_the_arm_enters_at_where_three_times_the_credit_could_not():
    """Credits 1.00 to 3.00 on a 5-wide. Half the maximum loss is reachable at each (the natural never
    needs to exceed the width); the corrected-away form, three times the credit, is beyond the width
    for every credit over a third of it, so it could not fire there."""
    for credit in (1.0, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0):
        threshold = credit + 0.5 * (5.0 - credit)
        assert threshold < 5.0
        under = snapshot(puts={6000: q(threshold - 0.02 + 1.0, threshold - 0.02 + 1.0), 5995: q(1.0, 1.1)})
        at = snapshot(puts={6000: q(threshold + 1.0, threshold + 1.0), 5995: q(1.0, 1.1)})
        assert engine.strand_stop_hit(under, vertical(credit), 0.5) is None, credit
        assert engine.strand_stop_hit(at, vertical(credit), 0.5) is not None, credit
        if credit > 5.0 / 3.0 + 1e-9:
            assert 3.0 * credit > 5.0  # the old form: a buy-back beyond the width, never quoted


# --------------------------------------------------------------------------- the stop
def vertical(credit, **over):
    return {
        "kind": "short_vertical", "status": "open", "side": "put", "center": 6000.0,
        "wing_width": 5.0, "net": credit, **over,
    }  # fmt: skip


def test_the_stop_fires_at_exactly_half_the_maximum_loss_and_not_below():
    """Natural 2.99 / 3.00 / 3.01 against a 1.00 credit."""
    for natural, fires in ((2.99, False), (3.00, True), (3.01, True)):
        snap = snapshot(puts={6000: q(natural + 1.0, natural + 1.0), 5995: q(1.0, 1.1)})
        assert (engine.strand_stop_hit(snap, vertical(1.0), 0.5) is not None) is fires, natural


def test_the_stop_prices_the_natural_and_adds_no_second_slippage():
    snap = snapshot(puts={6000: q(4.9, 5.1), 5995: q(1.8, 2.0)})
    hit = engine.strand_stop_hit(snap, vertical(1.0), 0.5)
    assert hit["natural"] == pytest.approx(3.3)  # the short's ask less the long's bid
    assert hit["mid"] == pytest.approx(3.1)
    assert hit["conceded"] == pytest.approx(0.2)  # a measure of mid to natural, not a cost to add


def test_a_vertical_with_no_quote_or_not_open_or_not_a_vertical_is_never_stopped():
    holed = snapshot(puts={6000: q(4.9, 5.1)})
    assert engine.strand_stop_hit(holed, vertical(1.0), 0.5) is None
    snap = snapshot(puts={6000: q(4.9, 5.1), 5995: q(1.8, 2.0)})
    assert engine.strand_stop_hit(snap, vertical(1.0), 0.5) is not None
    assert engine.strand_stop_hit(snap, vertical(1.0, status="settled"), 0.5) is None
    assert engine.strand_stop_hit(snap, vertical(1.0, kind="fly"), 0.5) is None


def test_an_uncompleted_vertical_is_bought_back_at_the_first_tick_half_its_maximum_loss_is_lost(conn):
    cfg = stop_config()
    tick(conn, cfg, "strand-stop", 0, CHEAP)
    (pos,) = rows(conn, "strand-stop")
    assert pos["credit"] == pytest.approx(1.075) and pos["status"] == "open"
    tick(conn, cfg, "strand-stop", 60, NEAR)  # natural 3.00 < 3.0375
    assert rows(conn, "strand-stop")[0]["status"] == "open"
    out = tick(conn, cfg, "strand-stop", 120, OVER)  # natural 3.30
    assert [a["action"] for a in out["actions"] if a["action"] == "strand_stopped"] == ["strand_stopped"]
    (pos,) = rows(conn, "strand-stop")
    assert pos["status"] == "settled" and pos["closed_before_expiry"] == 1
    assert pos["close_tag_source"] == "stop" and pos["close_tag_natural"] == pytest.approx(3.3)
    assert pos["exit_time"].startswith("2026-07-20T12:02:00")
    # credit - natural, whole-position dollars; the round trip's fees, none for settlement
    close_fee = fly.vertical_close_fee("SPX", 1)
    assert pos["gross_pnl"] == pytest.approx((1.075 - 3.3) * 100, abs=0.01)
    assert pos["fees"] == pytest.approx(fly.vertical_open_fee("SPX", 1) + close_fee, abs=1e-3)
    assert pos["settlement_fees"] == 0.0
    assert pos["pnl"] == pytest.approx(pos["gross_pnl"] - pos["fees"], abs=0.01)


def test_control_on_the_same_ticks_holds_the_vertical(conn):
    """Shown to fail without the rule: control sees the same 3.30 natural and does nothing."""
    cfg = one_arm_config(entry_modes=["legged"])
    for seconds, quotes in ((0, CHEAP), (60, NEAR), (120, OVER)):
        tick(conn, cfg, "control", seconds, quotes)
    (pos,) = rows(conn, "control")
    assert pos["status"] == "open" and pos["close_tag_source"] is None


def test_a_stopped_vertical_is_not_settled_again_and_the_session_pnl_is_the_close(conn):
    cfg = stop_config()
    tick(conn, cfg, "strand-stop", 0, CHEAP)
    tick(conn, cfg, "strand-stop", 120, OVER)
    (stopped,) = rows(conn, "strand-stop")
    net = []
    for price in (5900.0, 6000.0, 6100.0):  # wherever it ends, a closed position cannot move
        result = settle(conn, cfg, "strand-stop", price)
        assert result["assignment_fees"] == 0.0 and result["itm_legs"] == 0
        net.append(result["pnl"])
    assert net[0] == net[1] == net[2] == pytest.approx(stopped["pnl"], abs=0.01)
    (after,) = rows(conn, "strand-stop")
    assert after["gross_pnl"] == stopped["gross_pnl"] and after["fees"] == stopped["fees"]
    assert after["settlement_price"] is None or after["status"] == "settled"


def test_without_the_stop_flag_the_same_close_would_settle_as_a_vertical():
    """The accounting guard: `position_pnl` of a stopped row reads its net and fees only. Break the
    flag and the vertical's payoff at the settlement print is charged on top of the buy-back."""
    closed = vertical(1.075 - 3.3, quantity=1, fees=4.0, status="settled", settlement_price=5900.0)
    assert fly.position_pnl({**closed, "stopped": True}, 5900.0) == pytest.approx((1.075 - 3.3) * 100 - 4.0)
    assert fly.position_pnl(closed, 5900.0) != pytest.approx((1.075 - 3.3) * 100 - 4.0)


def test_a_completed_fly_is_not_a_strand_and_is_never_stopped(conn):
    cfg = stop_config()
    tick(conn, cfg, "strand-stop", 0, CHEAP)
    out = tick(conn, cfg, "strand-stop", 60, CHEAP_COMPLETION)
    assert [a["action"] for a in out["actions"] if a["action"] == "completed"] == ["completed"]
    for seconds in (120, 180):
        out = tick(conn, cfg, "strand-stop", seconds, OVER)  # the fly's legs run far adverse
        assert not [a for a in out["actions"] if a["action"] == "strand_stopped"]
    (pos,) = rows(conn, "strand-stop")
    assert pos["kind"] == "fly" and pos["status"] == "open" and pos["close_tag_source"] is None


def test_a_vertical_that_completes_on_the_tick_its_cost_is_over_is_a_fly_not_a_stop(conn):
    """Completion runs first. A tick that both completes and is over the stop completes."""
    cfg = stop_config()
    tick(conn, cfg, "strand-stop", 0, CHEAP)
    both = puts((4.9, 5.1), (1.8, 2.0), (5.3, 5.4))  # natural 3.30; completing debit 5.4 - 4.9 ~ 0.5
    out = tick(conn, cfg, "strand-stop", 60, both)
    actions = [a["action"] for a in out["actions"]]
    assert "completed" in actions and "strand_stopped" not in actions


def test_a_stop_frees_its_slot_but_not_its_centre(conn):
    """One position slot is free again; the duplicate rule still sees the stopped centre."""
    cfg = stop_config()
    cfg["defaults"]["max_positions"] = 1
    tick(conn, cfg, "strand-stop", 0, CHEAP)
    tick(conn, cfg, "strand-stop", 60, OVER)
    out = tick(conn, cfg, "strand-stop", 120, CHEAP)
    reasons = [a.get("reason") for a in out["actions"]]
    assert "duplicate_structure" in reasons and len(rows(conn, "strand-stop")) == 1


# --------------------------------------------------------------------------- the whole fly
def test_the_whole_fly_is_priced_at_the_modules_fly_debit_with_four_legs_of_fees(conn):
    cfg = whole_config()
    tick(conn, cfg, "whole-fly", 0)
    (pos,) = rows(conn, "whole-fly")
    snap = snapshot(underlying_price=SPOT)
    expected = fly.fly_debit(snap["puts"][5995], snap["puts"][6000], snap["puts"][6005], 0.125)
    assert pos["kind"] == "fly" and pos["entry_mode"] == "whole_fly" and pos["side"] == "put"
    assert pos["center"] == 6000.0 and pos["wing_width"] == 5.0
    assert pos["debit"] == pytest.approx(expected, abs=1e-4) and pos["net"] == pytest.approx(
        -expected, abs=1e-4
    )
    assert pos["fees"] == pytest.approx(fly.fly_open_fee("SPX", 1))
    assert pos["fees"] > fly.vertical_open_fee("SPX", 1) * 1.5  # four legs, not two
    assert pos["completed_at"] is None


def test_the_whole_fly_buys_where_control_sells_and_never_legs(conn):
    """Same tick, same centre and side. Control sells the vertical; the arm holds the fly and, however
    prices move, never opens a vertical and never completes."""
    tick(conn, one_arm_config(entry_modes=["legged"]), "control", 0)
    (ctl,) = rows(conn, "control")
    cfg = whole_config()
    tick(conn, cfg, "whole-fly", 0)
    for seconds, quotes in ((60, CHEAP_COMPLETION), (120, OVER), (180, CHEAP)):
        out = tick(conn, cfg, "whole-fly", seconds, quotes)
        assert not [a for a in out["actions"] if a["action"] in ("credit_spread_opened", "completed")]
    held = rows(conn, "whole-fly")
    assert ctl["kind"] == "short_vertical"
    assert {r["kind"] for r in held} == {"fly"} and held[0]["center"] == ctl["center"]
    assert held[0]["side"] == ctl["side"] and held[0]["wing_width"] == ctl["wing_width"]
    assert all(r["entry_mode"] == "whole_fly" and r["completed_at"] is None for r in held)


def test_the_whole_fly_is_held_to_settlement_at_the_fly_payoff(conn):
    cfg = whole_config()
    tick(conn, cfg, "whole-fly", 0)
    (pos,) = rows(conn, "whole-fly")
    result = settle(conn, cfg, "whole-fly", 6000.0)  # a pin: the full width
    expected = (5.0 - pos["debit"]) * 100 - pos["fees"] - fly.expire_fee(1)  # the 6005 put is in the money
    assert result["pnl"] == pytest.approx(expected, abs=0.01)
    (after,) = rows(conn, "whole-fly")
    assert after["expiry_payoff"] == pytest.approx(5.0) and after["status"] == "settled"


def test_a_fly_with_a_hole_in_its_strikes_is_refused_not_legged(conn):
    """The upper strike is unquoted: control would sell its vertical, the arm buys nothing."""
    cfg = whole_config()
    holed = {6000: q(5.0, 5.4), 5995: q(2.6, 3.0), 5990: q(1.0, 1.4)}
    out = tick(conn, cfg, "whole-fly", 0, holed)
    assert rows(conn, "whole-fly") == []
    assert {"action": "entry_skipped", "mode": "whole_fly", "reason": "whole_fly_unpriced"} in out["actions"]
    ctl = tick(conn, one_arm_config(entry_modes=["legged"]), "control", 0, holed)
    assert [a["action"] for a in ctl["actions"]] == ["credit_spread_opened"]


def test_a_crossed_fly_quote_is_refused():
    snap = snapshot(underlying_price=SPOT)
    snap["puts"][6005] = q(0.1, 0.2)  # the far wing quoted cheaper than the centre: a crossed fly
    plan = engine.evaluate_credit_spread_entry(snap, params(), [])[2]
    assert engine.evaluate_whole_fly(snap, plan, params(whole_fly=True)) == (
        False,
        "implausible_fly_quote",
        None,
    )


def test_the_whole_fly_keeps_controls_entry_gates(conn):
    """It enters on the ticks control would: the window gate that refuses control refuses the arm."""
    cfg = whole_config()
    cfg["defaults"]["entry_windows"] = [["13:00", "14:00"]]
    out = tick(conn, cfg, "whole-fly", 0)
    assert rows(conn, "whole-fly") == []
    assert any(a.get("reason") == "outside_entry_window" for a in out["actions"])
