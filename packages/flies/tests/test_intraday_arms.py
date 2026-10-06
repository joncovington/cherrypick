"""The intraday agent plan's two paper arms (docs/intraday-agent-plan.md): the agent's gate applied per
tick with the rule as its fallback, and closes TAGGED at natural, never executed."""

import pytest
from cherrypick.core import fees as core_fees

from cherrypick.flies import analytics, close_tags, db, engine, intraday_advice, paper_loop

DAY, ARM, SYM = "2026-10-06", "trend-rule", "SPX"


def _q(bid, ask, sym):
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 2), "streamer_symbol": sym}


def _snapshot(spot, day_open=7700.0):
    return {
        "date": DAY,
        "symbol": SYM,
        "underlying_price": spot,
        "session": {"day_open": day_open},
        # A call vertical: short 7720, long 7725 (the protected wing at +width).
        "calls": {7720.0: _q(6.0, 6.4, ".C7720"), 7725.0: _q(3.4, 3.8, ".C7725")},
        "puts": {},
    }


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "paper.db"))
    for pid, kind, status in (
        ("v1", "short_vertical", "open"),
        ("f1", "fly", "open"),
        ("v2", "short_vertical", "settled"),
    ):
        c.execute(
            "INSERT INTO fly_positions (position_id, book_id, trade_date, arm, symbol, kind, side, center, wing_width, "
            "quantity, credit, status) VALUES (?, ?, ?, ?, ?, ?, 'call', 7720, 5, 1, 2.4, ?)",
            (pid, f"{DAY}:{ARM}:{SYM}", DAY, ARM, SYM, kind, status),
        )
    c.commit()
    return c


def test_a_close_is_priced_at_natural_pay_the_ask_take_the_bid():
    pos = {"side": "call", "center": 7720, "wing_width": 5}
    assert close_tags.natural_close(_snapshot(7724.0), pos) == {
        "natural": 3.0,
        "mid": 2.6,
    }  # 6.4 - 3.4; 6.2 - 3.6


@pytest.mark.parametrize(
    ("spot", "day_open", "wanted"),
    [
        (7723.0, 7700.0, True),  # 3 through the short, +23 committed up against a call vertical
        (7726.0, 7700.0, False),  # past the wing: the loss is already full
        (7718.0, 7690.0, False),  # not yet through the short
        (7723.0, 7710.0, False),  # +13: inside the trend band, not committed
    ],
)
def test_the_rule_closes_only_inside_the_wing_on_a_committed_day(spot, day_open, wanted):
    pos = {"side": "call", "center": 7720, "wing_width": 5}
    assert close_tags.rule_wants_close(pos, _snapshot(spot, day_open), {"regime_trend_points": 20}) is wanted


def test_a_tag_is_stamped_once_on_an_open_vertical_and_nothing_else(conn):
    first = close_tags.tag(conn, _snapshot(7723.0), ARM, {}, source="rule", now="t1")
    assert [t["position_id"] for t in first] == ["v1"]  # not the fly, not the settled vertical
    assert close_tags.tag(conn, _snapshot(7723.5), ARM, {}, source="rule", now="t2") == []  # once
    row = conn.execute("SELECT * FROM fly_positions WHERE position_id = 'v1'").fetchone()
    assert (row["status"], row["close_tag_source"], row["close_tag_natural"], row["close_tag_at"]) == (
        "open",
        "rule",
        3.0,
        "t1",
    )


def test_agent_tags_only_the_ids_it_named(conn):
    assert close_tags.tag(conn, _snapshot(7723.0), ARM, {}, source="agent", agent_ids=["nope"], now="t") == []
    assert (
        len(close_tags.tag(conn, _snapshot(7723.0), ARM, {}, source="agent", agent_ids=["v1"], now="t")) == 1
    )


def test_the_agent_arms_gate_follows_a_fresh_decision_and_falls_back_to_the_rule(monkeypatch):
    config = {
        "arms": {
            "intraday-agent": {"refuse_completion_against_trend": True, "trend_gate_source": "agent"},
            "control": {},
        }
    }
    monkeypatch.setattr(intraday_advice, "read_decision", lambda target, session, now: None)
    same, used = paper_loop.tick_config(config, "intraday-agent", DAY, 0.0)
    assert (
        used is None
        and engine.merged_params(same, "intraday-agent")["refuse_completion_against_trend"] is True
    )
    monkeypatch.setattr(
        intraday_advice,
        "read_decision",
        lambda target, session, now: {"trend_gate": "off", "close_stranded": []},
    )
    off, used = paper_loop.tick_config(config, "intraday-agent", DAY, 0.0)
    assert (
        used["trend_gate"] == "off"
        and engine.merged_params(off, "intraday-agent")["refuse_completion_against_trend"] is False
    )
    assert config["arms"]["intraday-agent"]["refuse_completion_against_trend"] is True  # never mutated
    assert paper_loop.tick_config(config, "control", DAY, 0.0) == (config, None)


def test_the_read_side_values_a_tagged_close_at_natural_and_at_2x(conn):
    close_tags.tag(conn, _snapshot(7723.0), ARM, {}, source="rule", now="t1")
    # It rode to a full loss: settled at -width + credit.
    conn.execute(
        "UPDATE fly_positions SET status = 'settled', gross_pnl = -260.0, fees = 6.88 WHERE position_id = 'v1'"
    )
    conn.execute(
        "UPDATE fly_positions SET status = 'settled', gross_pnl = 240.0, fees = 6.88 WHERE position_id = 'f1'"
    )
    conn.commit()
    out = analytics.close_tag_result(conn, ARM)
    v1 = next(c for c in out["closes"] if c["position_id"] == "v1")
    assert v1["settled_net"] == -266.88
    fees = core_fees.ic_open_fee(SYM, 1, legs=2, sell_legs=1) + core_fees.ic_close_fee(
        SYM, 1, legs=2, sell_legs=1
    )
    assert v1["closed_net"] == pytest.approx(
        (2.4 - 3.0) * 100 - fees, abs=0.01
    )  # credit less the natural debit
    worse = 3.0 + (3.0 - 2.6)  # one more spread's worth on the close
    assert v1["closed_net_2x"] == pytest.approx((2.4 - worse) * 100 - fees, abs=0.01)
    assert v1["closed_net"] > v1["closed_net_2x"] > v1["settled_net"]  # closing at -$60 beats riding to -$260
    assert out["saved"] == pytest.approx(v1["closed_net"] - v1["settled_net"])
    assert out["tagged"] == 1 and out["positions"] == 3
