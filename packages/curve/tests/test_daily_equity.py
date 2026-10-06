"""Each arm's daily marked equity: closed nets land on their close session, open positions at their
session's last usable mark, and a session with no usable mark carries the last one (and says so)."""

import pytest

from cherrypick.curve import analytics, db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "paper_trades.db"))
    for day in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"):
        c.execute("INSERT INTO curve_regime (trade_date, tick, usable) VALUES (?, ?, 1)", (day, day))
    c.commit()
    return c


def _position(conn, pid, *, entry, closed=None, net=None, credit=0.30, qty=1, arm="control"):
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": "VXX",
            "arm": arm,
            "entry_session": entry,
            "quantity": qty,
            "expiration": "2026-10-16",
            "short_strike": 20,
            "long_strike": 22,
            "entry_credit": credit,
            "entry_cost": 1.0,
            "entry_slippage": 2.0,
            "status": "closed" if closed else "open",
            "closed_session": closed,
            "gross_pnl": None if net is None else net + 5.0,
            "fees": None if net is None else 5.0,
        },
    )


def _mark(conn, pid, day, close_cost, *, usable=1):
    db.record_mark(
        conn,
        position_id=pid,
        leg_role="short_call",
        marked_at=float(day[-2:]),
        session_date=day,
        close_cost=close_cost,
        usable=usable,
    )


def test_open_positions_mark_daily_and_a_close_lands_on_its_session(conn):
    _position(conn, "a", entry="2026-09-01", closed="2026-09-03", net=12.0)
    _mark(conn, "a", "2026-09-01", 0.30)
    _mark(conn, "a", "2026-09-02", 0.70)  # against us: (0.30 - 0.70) x 100 - 3 spent = -43
    out = analytics.daily_equity(conn)["control"]
    assert out["series"] == [
        ("2026-09-01", -3.0),
        ("2026-09-02", -43.0),
        ("2026-09-03", 12.0),
        ("2026-09-04", 12.0),
    ]
    assert out["reading"]["max_drawdown"] == -43.0  # the closed-trade view would show a +12 win, nothing else


def test_a_session_without_a_usable_mark_carries_the_last_and_counts_it(conn):
    _position(conn, "b", entry="2026-09-01")
    _mark(conn, "b", "2026-09-01", 0.20)
    _mark(conn, "b", "2026-09-02", None, usable=0)
    out = analytics.daily_equity(conn)["control"]
    assert out["series"][1] == ("2026-09-02", out["series"][0][1])
    assert out["carried"] == 3  # 09-02, 09-03, 09-04 all carried 09-01's mark
