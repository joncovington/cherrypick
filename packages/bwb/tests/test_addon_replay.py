"""The add-on scored as its own trade (addon_replay.py)."""

import pytest

from cherrypick.bwb import addon_replay, db

PARAMS = {"delta_trigger": 0.50, "addon_credit_floor": 0.0}
CONFIG = {"symbol": "SPX", "defaults": {"quantity": 1, "strike_increment": 5.0, "delta_trigger": 0.5}}


def tick(session, delta, quotes=(1.1, 1.3, 0.8, 0.95), at=0.0):
    sb, sa, lb, la = quotes
    return {
        "session_date": session,
        "ticked_at": at,
        "near_abs_delta": delta,
        "addon_short_bid": sb,
        "addon_short_ask": sa,
        "addon_long_bid": lb,
        "addon_long_ask": la,
    }


# --------------------------------------------------------------------------- the pure layer
def test_the_addon_arms_on_the_delta_touch_and_fires_on_the_first_credit_tick_after():
    """The loop's latch: once armed it stays armed, and fires on the first tick that prices as a
    credit -- even after delta has fallen back below the bar."""
    ticks = [
        tick("2026-09-08", 0.40, at=1),
        tick("2026-09-08", 0.55, quotes=(0.5, 0.6, 0.6, 0.7), at=2),  # armed, but a debit
        tick("2026-09-08", 0.45, quotes=(None, None, None, None), at=3),  # unpriceable: skipped
        tick("2026-09-08", 0.45, at=4),  # fires here, delta already back under 0.50
    ]
    fire = addon_replay.addon_only_fire(ticks, PARAMS)
    assert fire["ticked_at"] == 4 and fire["credit"] == pytest.approx(0.325)


def test_no_touch_no_trade():
    assert addon_replay.addon_only_fire([tick("2026-09-08", 0.49)], PARAMS) is None


def test_the_daily_comparator_sells_on_the_entry_session_only():
    ticks = [tick("2026-09-07", 0.2, at=1), tick("2026-09-08", 0.2, quotes=(2.0, 2.2, 1.0, 1.2), at=2)]
    sale = addon_replay.daily_sale(ticks, "2026-09-08", PARAMS)
    assert sale["ticked_at"] == 2 and sale["credit"] == pytest.approx(1.0)
    assert addon_replay.daily_sale(ticks, "2026-09-09", PARAMS) is None


def test_a_scored_trade_adds_up_and_charges_the_settlement_fee_per_itm_leg():
    sale = {
        "session_date": "2026-09-08",
        "credit": 4.0,
        "quotes": [{"bid": 4.9, "ask": 5.1}, {"bid": 0.9, "ask": 1.1}],
    }
    # Settles between the strikes: the short is 3 in the money, the long expires.
    t = addon_replay.score(sale, 7600.0, 7590.0, 7597.0, 1, CONFIG, expiration="2026-09-11")
    assert t["entry_cash"] == 400.0 and t["exit_cash"] == -300.0 and t["gross"] == 100.0
    assert t["settlement_fees"] == 5.0
    assert t["net"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"] - t["settlement_fees"])
    assert t["max_loss"] == 600.0 and t["bp_days"] == 600.0 * 3


def test_a_trade_without_a_print_is_left_unscored():
    sale = {
        "session_date": "2026-09-08",
        "credit": 4.0,
        "quotes": [{"bid": 4.9, "ask": 5.1}, {"bid": 0.9, "ask": 1.1}],
    }
    t = addon_replay.score(sale, 7600.0, 7590.0, None, 1, CONFIG)
    assert t["net"] is None and t["gross"] is None


def test_samples_are_counted_by_settlement_friday_not_by_fire():
    trades = [
        {
            "expiration": "2026-09-11",
            "net": 100.0,
            "max_loss": 600.0,
            "gross": 110.0,
            "fees": 5.0,
            "slippage": 5.0,
            "settlement_fees": 0.0,
            "bp_days": 1800.0,
        },
        {
            "expiration": "2026-09-11",
            "net": -50.0,
            "max_loss": 600.0,
            "gross": -40.0,
            "fees": 5.0,
            "slippage": 5.0,
            "settlement_fees": 0.0,
            "bp_days": 1800.0,
        },
    ]
    s = addon_replay.summarize(trades)
    assert s["settled"] == 2 and s["fridays"] == 1 and s["worst_friday"] == 50.0


# --------------------------------------------------------------------------- the ledger layer
def _position(
    conn, pid, arm, *, fired=True, armed="2026-09-09T11:00", close=(3.0, 0.0), kinds=("itm", "expired")
):
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": "SPX",
            "arm": arm,
            "entry_session": "2026-09-08",
            "structure_signature": "sig",
            "expiration": "2026-09-11",
            "status": "closed",
            "quantity": 1,
            "body_strike": 7605.0,
            "near_strike": 7610.0,
            "far_strike": 7595.0,
            "below_flip_seen": 0,
            "armed_at": armed if fired else None,
            "addon_fired_at": armed if fired else None,
            "addon_short_strike": 7600.0,
            "addon_long_strike": 7590.0,
            "addon_cost": 2.3,
            "addon_slippage": 0.8,
            "settlement_spot": 7597.0,
        },
    )
    if fired:
        for role, action, strike, mid, value, kind in (
            ("addon_short", "Sell to Open", 7600.0, 5.0, close[0], kinds[0]),
            ("addon_long", "Buy to Open", 7590.0, 1.0, close[1], kinds[1]),
        ):
            db.save_leg(
                conn,
                {
                    "position_id": pid,
                    "leg_role": role,
                    "occ_symbol": role,
                    "streamer_symbol": role,
                    "expiration": "2026-09-11",
                    "strike": strike,
                    "option_type": "put",
                    "action": action,
                    "quantity": 1,
                    "entry_mid": mid,
                    "close_value": value,
                    "close_kind": kind,
                    "status": "settled",
                },
            )


def test_the_real_addon_is_scored_from_its_own_legs_without_the_fly(tmp_path):
    conn = db.connect(str(tmp_path / "p.db"))
    _position(conn, "d1", "delta")
    conn.commit()
    (t,) = addon_replay.real_addon_trades(conn, "delta")
    # 5.00 - 1.00 = 4.00 credit; the short settles 3 in the money.
    assert t["entry_cash"] == 400.0 and t["exit_cash"] == -300.0 and t["gross"] == 100.0
    assert (t["fees"], t["slippage"], t["settlement_fees"]) == (2.3, 0.8, 5.0)
    assert t["net"] == pytest.approx(100.0 - 2.3 - 0.8 - 5.0)


def test_validation_catches_a_trigger_the_ticks_do_not_reproduce(tmp_path):
    """The replayed arming must land on the session the real arm armed; a mismatch is reported."""
    conn = db.connect(str(tmp_path / "p.db"))
    _position(conn, "d1", "delta", armed="2026-09-10T11:00")
    for at, session, delta in ((1.0, "2026-09-08", 0.3), (2.0, "2026-09-09", 0.55)):
        db.record_trigger_tick(
            conn,
            {
                "entry_session": "2026-09-08",
                "structure_signature": "sig",
                "symbol": "SPX",
                "ticked_at": at,
                "session_date": session,
                "near_abs_delta": delta,
                "measured": 1,
                "spot_measured": 1,
                "flip_measured": 1,
                "below_flip_seen": 0,
            },
        )
    conn.commit()
    v = addon_replay.run(conn, CONFIG)["validation"]
    assert v["ok"] is False
    assert v["mismatches"] == [
        {"entry_session": "2026-09-08", "real_armed": "2026-09-10", "replay_armed": "2026-09-09", "ok": False}
    ]
