"""From 2026-10-05 a paper legged completion pays the live limit (engine.pays_limit)."""

import pytest
from test_book import one_arm_config
from test_engine import open_spread, params, q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly, live_orders, paper_loop

BOUNDARY = engine.COMPLETION_PRICE_FROM
BEFORE = "2026-10-02"


def _cheap(date, far_mid=2.5):
    """Completing put spread: buy 6005 (mid `far_mid`), sell 6000 (mid 1.10), both 0.20 wide."""
    return snapshot(date=date, puts={6000: q(1.0, 1.2), 6005: q(far_mid - 0.1, far_mid + 0.1)})


def _limit(position, p):
    return live_orders.tick_floor(
        live_orders.max_safe_completion_debit(
            {**position, "symbol": "SPX"}, p.get("min_floor_dollars", 0.0), p.get("fee_buffer", 0.10)
        )
    )


def test_before_the_boundary_a_completion_still_pays_its_modelled_debit():
    done, _, plan = engine.evaluate_completion(_cheap(BEFORE), open_spread(), params())
    assert done
    modelled = fly.vertical_debit(q(2.4, 2.6), q(1.0, 1.2))
    assert plan["debit"] == pytest.approx(modelled) and plan["market_debit"] == pytest.approx(modelled)


def test_from_the_boundary_a_completion_pays_the_live_limit():
    """Shown to fail without the rule: the same market now completes at the price a resting live
    order sits at -- the limit `resting_completion_spec` would submit -- not the cheaper modelled
    debit the market had run down to by this tick."""
    p, pos = params(), open_spread()
    done, reason, plan = engine.evaluate_completion(_cheap(BOUNDARY), pos, p)
    limit = _limit(pos, p)
    assert done and reason == "ok"
    assert plan["debit"] == pytest.approx(limit) and plan["limit"] == pytest.approx(limit)
    assert plan["market_debit"] == pytest.approx(fly.vertical_debit(q(2.4, 2.6), q(1.0, 1.2)))
    assert plan["debit"] > plan["market_debit"], "paying the limit is never better than the market"
    assert plan["net"] == pytest.approx(pos["net"] - limit)
    assert plan["slippage"] == pytest.approx(limit - (2.5 - 1.1))  # conceded against mid
    assert plan["floor"] == pytest.approx(
        round(
            fly.position_floor(
                {
                    "kind": "fly",
                    "side": "put",
                    "center": 6000,
                    "wing_width": 5,
                    "net": pos["net"] - limit,
                    "quantity": 1,
                    "fees": pos["fees"] + fly.vertical_open_fee("SPX", 1),
                }
            ),
            2,
        )
    )
    # One formula with live: the live spec for this position rests at exactly this price.
    snap = _cheap(BOUNDARY)
    for k, quote in snap["puts"].items():
        quote["occ_symbol"] = f"SPXW  261005P0{int(k)}000"
    spec = live_orders.resting_completion_spec(snap, {**pos, "symbol": "SPX"}, p)
    assert spec["price"] == pytest.approx(plan["debit"])


@pytest.mark.parametrize(
    "min_floor, binding_reason", [(-10.0, None), (60.0, "floor_below_minimum_after_fees")]
)
def test_the_trigger_is_the_modelled_debit_at_or_under_the_limit(min_floor, binding_reason):
    p, pos = params(min_floor_dollars=min_floor), open_spread()
    limit, binding = engine.completion_limit({**pos, "symbol": "SPX"}, p)
    # Modelled debit = far mid - 1.10 + 0.125 x (0.20 + 0.20): place it 0.02 either side of the limit.
    under = _cheap(BOUNDARY, far_mid=limit - 0.02 + 1.10 - 0.05)
    over = _cheap(BOUNDARY, far_mid=limit + 0.02 + 1.10 - 0.05)
    assert engine.evaluate_completion(under, pos, p)[0]
    done, reason, plan = engine.evaluate_completion(over, pos, p)
    assert not done and plan["gate_debit"] == pytest.approx(limit)
    expected = binding_reason or (
        "completing_debit_too_high" if binding == "price" else "floor_below_minimum_after_fees"
    )
    assert reason == expected


def test_an_arm_may_keep_the_modelled_price():
    done, _, plan = engine.evaluate_completion(
        _cheap(BOUNDARY), open_spread(), params(completion_price="modelled")
    )
    assert done and plan["debit"] == pytest.approx(plan["market_debit"])


def test_the_book_records_the_limit_paid_and_the_market_debit_as_the_counterfactual(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    config = one_arm_config(entry_modes=["legged"])
    first = bookmod.process_snapshot(
        snapshot(date=BOUNDARY, underlying_price=5998.0), config, conn, "control"
    )
    pid = next(a for a in first["actions"] if a["action"] == "credit_spread_opened")["position_id"]
    later = snapshot(date=BOUNDARY, underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    bookmod.process_snapshot(later, config, conn, "control")
    row = dict(conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (pid,)).fetchone())
    assert row["kind"] == "fly"
    assert row["debit"] == pytest.approx(row["shadow_completion_limit"]), "paper paid what live would rest at"
    assert row["net"] == pytest.approx(row["credit"] - row["debit"])
    assert row["best_completing_debit"] == pytest.approx(fly.vertical_debit(q(2.4, 2.6), q(1.0, 1.2)))


def test_the_break_is_journaled_once_book_wide(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    paper_loop._note_completion_rule(conn, {"defaults": {}})
    paper_loop._note_completion_rule(conn, {"defaults": {}})
    rows = [r for r in dbmod.measurement_breaks(conn) if r["kind"] == "completion_rule"]
    assert len(rows) == 1
    assert rows[0]["break_date"] == BOUNDARY and rows[0]["scope"] == "*"
    # An opted-out book records no break.
    conn2 = dbmod.connect(str(tmp_path / "other.db"))
    paper_loop._note_completion_rule(conn2, {"defaults": {"completion_price": "modelled"}})
    assert not [r for r in dbmod.measurement_breaks(conn2) if r["kind"] == "completion_rule"]
