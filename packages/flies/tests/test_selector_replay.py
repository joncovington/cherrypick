"""The selector's walk-forward replay: out of sample by construction, scored on the stamped regime,
and its baseline is replay-gates' to the cent."""

import pytest

from cherrypick.flies import db as dbmod
from cherrypick.flies import replay_gates, selector_replay


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def _row(conn, pid, day, pnl, *, arm="control", kind="fly", vol="normal", trend="up_from_open", t="11:00:00"):
    dbmod.save_position(
        conn,
        {
            "position_id": pid,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "legged" if arm == "control" else "debit_first",
            "symbol": "SPX",
            "kind": kind,
            "side": "put",
            "center": 6000.0,
            "wing_width": 5.0,
            "quantity": 1,
            "net": 1.0,
            "fees": 6.0,
            "pnl": pnl,
            "status": "settled",
            "entry_time": f"{day}T{t}-04:00",
            "entry_vol_bucket": vol,
            "entry_trend_bucket": trend,
        },
    )


DAYS = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 8, 9, 10)]


def test_take_skip_learns_a_negative_cell_and_skips_it_only_afterwards(conn):
    """Five sessions of a losing up-trend cell, then a sixth and seventh. The first five are taken
    (the cell is thin until five sessions sit BEFORE the day), the sixth onward is skipped. A
    replay that lost the stamped regime -- scoring every row in an empty cell -- would skip nothing."""
    for i, d in enumerate(DAYS):
        _row(conn, f"U{i}", d, -100.0, kind="short_vertical")
        _row(conn, f"F{i}", d, 40.0, trend="flat")
    out = selector_replay.run(conn, start="2026-09-01", history_start="2026-09-01")
    ts = out["take_skip"]["selector"]
    assert ts["kept"] == 14 - 2
    assert ts["net_pnl"] == pytest.approx(5 * -100.0 + 7 * 40.0)


def test_the_take_skip_baseline_is_replay_gates_base(conn):
    for i, d in enumerate(DAYS):
        _row(conn, f"U{i}", d, -100.0 + i, kind="short_vertical")
    out = selector_replay.run(conn, start="2026-09-01", history_start="2026-09-01")
    rg = replay_gates.summarize(*(2 * [replay_gates.load_rows(conn, start="2026-09-01")]))
    assert out["take_skip"]["control"]["net_pnl"] == rg["net_pnl"]
    assert out["take_skip"]["control"]["entries"] == rg["entries"]


def test_a_fold_never_sees_its_own_session(conn):
    """The first session has no history, so its entries are the default's whatever they did."""
    _row(conn, "A", DAYS[0], -500.0, kind="short_vertical")
    out = selector_replay.run(conn, start="2026-09-01", history_start="2026-09-01")
    assert out["take_skip"]["selector"]["kept"] == 1


def test_two_structure_ticks_pair_entries_on_one_tick_only():
    a = [{"position_id": "a1", "trade_date": "2026-09-21", "entry_time": "2026-09-21T11:00:00-04:00"}]
    b = [
        {"position_id": "b1", "trade_date": "2026-09-21", "entry_time": "2026-09-21T11:00:04-04:00"},
        {"position_id": "b2", "trade_date": "2026-09-21", "entry_time": "2026-09-21T11:30:00-04:00"},
    ]
    groups = selector_replay.ticks(a, b)
    assert [[r["position_id"] for r in g] for g in groups] == [["a1", "b1"], ["b2"]]


def test_the_vol_floor_gate_fails_open_on_a_missing_ratio():
    rows = [
        {"trade_date": "d", "kind": "fly", "pnl": 10.0, "entry_vol_value": 0.001},
        {"trade_date": "d", "kind": "fly", "pnl": 20.0, "entry_vol_value": None},
        {"trade_date": "d", "kind": "fly", "pnl": 30.0, "entry_vol_value": 0.003},
    ]
    assert selector_replay.vol_floor_gate(rows)["net_pnl"] == 50.0
