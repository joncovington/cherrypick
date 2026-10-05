"""The debit-first shadow ladder: rungs priced at the anchor's fill, carried by the arm's own
completion rule, settled at the same print -- and never a position."""

import pytest
from test_book import one_arm_config
from test_engine import q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly, ladder


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def _ladder_config(offsets):
    return one_arm_config(entry_modes=["debit_first"], debit_ladder={"offsets_strikes": offsets})


def _rungs(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fly_debit_ladder ORDER BY direction, k")]


def _anchor(conn):
    return dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))[0]


# The debit-first lifecycle's ticks: spot at 6000 buys the 5995/6000 call spread; later the
# 6000/6005 call spread richens and the arm sells it.
OPEN = snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2)})
COMPLETE = snapshot(calls={6000: q(3.0, 3.2), 6005: q(0.5, 0.6)})


def test_a_rung_at_k0_is_the_anchors_own_trade_from_fill_to_settlement(conn):
    """The ladder's correctness check. A rung at zero strikes out is the anchor by construction, so
    its entry debit, the tick it completes on, the credit it takes and its settled P&L must all be
    the anchor's exactly. The middle tick clears the debit but not the fee buffer: the arm must
    refuse it, and so must the rung, which is what catches a rung running a rule of its own."""
    config = _ladder_config([0])
    bookmod.process_snapshot(OPEN, config, conn, "control")
    anchor = _anchor(conn)
    up = next(r for r in _rungs(conn) if r["direction"] == "up")
    assert up["refusal"] is None
    assert (up["side"], up["center"], up["wing_width"]) == (
        anchor["side"],
        anchor["center"],
        anchor["wing_width"],
    )
    assert up["entry_debit"] == pytest.approx(anchor["debit"])
    assert up["entry_fee"] == pytest.approx(anchor["fees"])

    # Credit = D + 0.05 at zero spread: above the debit, under the 0.10 buffer.
    d = anchor["debit"]
    under_buffer = snapshot(calls={6000: q(d + 0.35, d + 0.35), 6005: q(0.30, 0.30)})
    bookmod.process_snapshot(under_buffer, config, conn, "control")
    assert _anchor(conn)["kind"] == "long_vertical"
    up = next(r for r in _rungs(conn) if r["direction"] == "up")
    assert up["complete_credit"] is None, "the rung completed on a tick the arm refused"
    assert up["best_credit"] == pytest.approx(d + 0.05)

    bookmod.process_snapshot(COMPLETE, config, conn, "control")
    anchor = _anchor(conn)
    up = next(r for r in _rungs(conn) if r["direction"] == "up")
    assert anchor["kind"] == "fly"
    assert up["first_complete_at"] == anchor["completed_at"]
    assert up["complete_credit"] - up["entry_debit"] == pytest.approx(anchor["net"])

    bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 6002.0, config)
    anchor = _anchor(conn)
    up = next(r for r in _rungs(conn) if r["direction"] == "up")
    assert up["settlement_price"] == 6002.0
    assert up["pnl"] == pytest.approx(anchor["pnl"])


def test_rungs_sit_k_strikes_out_on_the_delta_pairs_sides():
    assert ladder.rung_geometry(6000.0, "up", 3, 5) == (fly.CALL, 6015.0)
    assert ladder.rung_geometry(6000.0, "down", 3, 5) == (fly.PUT, 5985.0)


def test_an_unquoted_rung_records_its_refusal_and_no_price(conn):
    """The default grid quotes calls only out to 6010, so the up rung two strikes out (the 6005/6010
    spread centred at 6010) is priced and the one three out is not: it says why, and carries no
    debit -- never a zero."""
    config = _ladder_config([2, 3])
    bookmod.process_snapshot(
        snapshot(calls={5995: q(2.0, 2.4), 6000: q(1.0, 1.2), 6005: q(0.5, 0.6), 6010: q(0.2, 0.3)}),
        config,
        conn,
        "control",
    )
    rungs = {(r["direction"], r["k"]): r for r in _rungs(conn)}
    assert rungs[("up", 2)]["refusal"] is None and rungs[("up", 2)]["entry_debit"] > 0
    assert rungs[("up", 3)]["refusal"] in ("missing_leg_quotes", "legs_beyond_strike_window")
    assert rungs[("up", 3)]["entry_debit"] is None


def test_a_refused_rung_is_never_folded_or_settled(conn):
    config = _ladder_config([3])
    bookmod.process_snapshot(OPEN, config, conn, "control")
    bookmod.process_snapshot(COMPLETE, config, conn, "control")
    bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 6002.0, config)
    up = next(r for r in _rungs(conn) if r["direction"] == "up")
    assert up["refusal"] is not None
    assert (up["best_credit"], up["settlement_price"], up["pnl"]) == (None, None, None)


def test_a_failing_ladder_never_costs_the_anchor_fill(conn, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("telemetry down")

    monkeypatch.setattr(ladder, "stamp", boom)
    result = bookmod.process_snapshot(OPEN, _ladder_config([1]), conn, "control")
    assert [a for a in result["actions"] if a["action"] == "debit_vertical_opened"]
    assert _rungs(conn) == []


def test_no_ladder_is_stamped_unless_the_arm_declares_one(conn):
    bookmod.process_snapshot(OPEN, one_arm_config(entry_modes=["debit_first"]), conn, "control")
    assert _rungs(conn) == []
    assert ladder.offsets({"debit_ladder": {"offsets_strikes": []}}) == ()


def test_rungs_are_not_positions(conn):
    """Shadow rows only: the book holds exactly the anchor, whatever the ladder stamped."""
    bookmod.process_snapshot(OPEN, _ladder_config([0, 1, 2]), conn, "control")
    assert len(dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))) == 1
    assert len(_rungs(conn)) == 6


def test_the_fold_uses_the_arms_completion_function():
    """A rung's completion is `engine.evaluate_debit_completion` on a synthetic long vertical --
    the same call the arm makes on its own position."""
    rung = {"side": fly.CALL, "center": 6000.0, "wing_width": 5.0, "entry_debit": 1.175, "entry_fee": 2.0}
    params = engine.merged_params(_ladder_config([0]), "control")
    done, _r, plan = engine.evaluate_debit_completion(COMPLETE, ladder.as_position(rung), params)
    changed = ladder.fold(COMPLETE, params, rung, "t1")
    assert done and changed["complete_credit"] == plan["credit"]
