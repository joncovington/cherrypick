"""Tests for the debit-first offset re-cut (analytics.debit_first_by_offset)."""

import pytest

from cherrypick.flies import analytics
from cherrypick.flies import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def debit_first(
    conn,
    pid,
    *,
    day="2026-09-21",
    arm="debit-first-atm",
    symbol="SPX",
    offset=0.0,
    delta=0.5,
    debit=2.50,
    completed=True,
    pnl=40.0,
    credit=2.90,
    post_best_credit=None,
    entry_mode="debit_first",
):
    dbmod.save_position(
        conn,
        {
            "position_id": pid,
            "book_id": f"{day}:{arm}:{symbol}",
            "trade_date": day,
            "arm": arm,
            "entry_mode": entry_mode,
            "symbol": symbol,
            "kind": "fly" if completed else "long_vertical",
            "side": "put",
            "center": 6000.0,
            "wing_width": 5.0,
            "quantity": 1,
            "debit": debit,
            "credit": credit if completed else None,
            "fees": 5.0,
            "gross_pnl": pnl + 5.0,
            "pnl": pnl,
            "status": "settled",
            "entry_time": f"{day}T11:00:00",
            "completed_at": f"{day}T12:00:00" if completed else None,
            "entry_center_offset_value": offset,
            "entry_center_delta": delta,
            "post_best_completing_credit": post_best_credit,
        },
    )


def cells(out):
    return {c["bucket"]: c for c in out["cells"]}


def seed(conn):
    debit_first(conn, "A1", offset=1.2, delta=0.52, pnl=45.0)
    debit_first(conn, "A2", day="2026-09-22", offset=-2.0, delta=-0.47, completed=False, pnl=-255.0)
    debit_first(conn, "A3", day="2026-09-23", offset=6.0, delta=0.41, pnl=30.0, post_best_credit=3.40)
    debit_first(
        conn, "U1", arm="debit-first-up", offset=31.0, delta=0.16, debit=0.9, completed=False, pnl=-95.0
    )
    debit_first(conn, "D1", arm="debit-first-down", offset=-34.5, delta=-0.13, debit=0.8, pnl=120.0)
    debit_first(conn, "N1", arm="debit-first", offset=None, delta=None, pnl=10.0)
    debit_first(conn, "X1", symbol="XSP", offset=3.0, delta=0.30, debit=0.5, pnl=8.0)


def test_strikes_and_delta_cuts_over_the_same_rows_sum_to_the_same_net(conn):
    seed(conn)
    by_strikes = analytics.debit_first_by_offset(conn, unit="strikes")
    by_delta = analytics.debit_first_by_offset(conn, unit="delta")
    expected = 45.0 - 255.0 + 30.0 - 95.0 + 120.0 + 10.0 + 8.0
    for out in (by_strikes, by_delta):
        assert sum(c["net_pnl"] for c in out["cells"]) == pytest.approx(expected)
        assert sum(c["trades"] for c in out["cells"]) == 7
        assert out["total"]["net_pnl"] == pytest.approx(expected)


def test_strikes_are_counted_on_each_symbols_own_spacing_and_by_distance_not_direction(conn):
    """SPX offsets divide by 5 and XSP by 1; up and down offsets of the same size land together."""
    seed(conn)
    strikes = cells(analytics.debit_first_by_offset(conn, unit="strikes"))
    assert strikes["0"]["trades"] == 2  # +1.2 and -2.0 points on SPX
    assert strikes["1"]["trades"] == 1  # 6.0 points on SPX
    assert strikes["3"]["trades"] == 1  # 3.0 points on XSP's 1-point chain
    assert strikes["6"]["trades"] == 1 and strikes["7"]["trades"] == 1  # 31 up, 34.5 down
    assert strikes["unknown"]["trades"] == 1
    order = [c["bucket"] for c in analytics.debit_first_by_offset(conn, unit="strikes")["cells"]]
    assert order == ["0", "1", "3", "6", "7", "unknown"]


def test_a_cell_carries_completion_debit_miss_cost_left_on_table_and_a_thin_stamp(conn):
    seed(conn)
    zero = cells(analytics.debit_first_by_offset(conn, unit="strikes"))["0"]
    assert zero["sessions"] == 2 and zero["thin"] is True
    assert zero["completed"] == 1 and zero["completion_rate"] == 0.5
    assert zero["avg_debit"] == pytest.approx(2.50)
    assert zero["misses"] == 1 and zero["bounded_miss_cost"] == pytest.approx(-255.0)

    delta = cells(analytics.debit_first_by_offset(conn, unit="delta"))
    assert delta["0.35..0.45"]["left_on_table"] == {"tracked": 1, "dollars": pytest.approx(50.0)}
    assert delta[">=0.45"]["trades"] == 2  # |-0.47| and 0.52
    assert delta["0.1..0.15"]["arms"] == {"debit-first-down": 1}


def test_only_settled_unvoided_debit_first_rows_are_read(conn):
    seed(conn)
    debit_first(conn, "L1", entry_mode="legged", pnl=999.0)
    debit_first(conn, "V1", pnl=999.0)
    conn.execute("UPDATE fly_positions SET void_reason = 'test' WHERE position_id = 'V1'")
    conn.commit()
    out = analytics.debit_first_by_offset(conn, unit="strikes", symbol="SPX")
    assert out["total"]["trades"] == 6  # the XSP row is scoped out too


def test_an_unknown_unit_is_refused(conn):
    with pytest.raises(ValueError):
        analytics.debit_first_by_offset(conn, unit="points")
