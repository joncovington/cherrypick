"""scripts/flies_live_start_replay.py: live's admission rules replayed over paper control's ticks.

One fixture session. Paper control entered a 7000 put spread at 10:00 and was refused 7000 as a
duplicate on every tick after, so its ledger holds that one row. A replay starting at 10:15 must
still enter 7000 -- live's book has no 10:00 entry -- which is the drift the first model could not
produce: it only dropped paper's rows. Spot sits at 6999 until 10:40 and then 7007, seven points
through the centre toward the put fly's completing side, so the replayed spread completes there.
"""

import importlib.util
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_live_start_replay.py"
DAY = "2026-09-29"
PRINT = 7010.0
OPEN = datetime.fromisoformat(f"{DAY}T10:00:05-04:00")


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("flies_live_start_replay", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _position(conn, **row):
    base = {
        "book_id": f"{row['position_id']}-book",
        "trade_date": DAY,
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "kind": "short_vertical",
        "side": "put",
        "center": 7000.0,
        "wing_width": 5.0,
        "quantity": 1,
        "credit": 2.40,
        "net": 2.40,
        "fees": 3.44,
        "settlement_fees": 0.0,
        "underlying_at_entry": 6999.0,
        "status": "settled",
        "settlement_price": PRINT,
        "pnl": 236.56,
    }
    row = {**base, **row}
    conn.execute(
        f"INSERT INTO fly_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()


@pytest.fixture
def ledgers(managed_home):
    root = managed_home / "data" / "flies"
    root.mkdir(parents=True)
    paper = dbmod.connect(str(root / "paper_trades.db"))
    live = dbmod.connect(str(root / "live_trades.db"))
    _position(paper, position_id="paper-7000", entry_time=OPEN.isoformat())
    t = OPEN
    while t.strftime("%H:%M") < "11:00":
        spot = 6999.0 if t.strftime("%H:%M") < "10:40" else 7007.0
        outcome = "filled" if t == OPEN else "duplicate_blocked"
        paper.execute(
            "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, mode, outcome, center, spot) "
            "VALUES (?, ?, 'control', 'SPX', 'legged', ?, 7000.0, ?)",
            (t.isoformat(), DAY, outcome, spot),
        )
        paper.execute(
            "INSERT INTO fly_iterations (iteration_ts, trade_date, symbol, arm, center, underlying_price) "
            "VALUES (?, ?, 'SPX', 'control', 7000.0, ?)",
            (t.isoformat(), DAY, spot),
        )
        t += timedelta(seconds=15)
    paper.commit()
    return {"paper": paper, "live": live}


def test_a_later_start_enters_the_centre_paper_held_from_before_it(script, ledgers):
    inp = script.Inputs(DAY, DAY)
    assert [p["t"] for p in script.replay_day(inp, DAY, "10:00", 1000.0, 360, 60)["book"]] == [OPEN]
    book = script.replay_day(inp, DAY, "10:15", 1000.0, 360, 60)["book"]
    assert [(p["center"], p["side"], p["t"].strftime("%H:%M:%S")) for p in book] == [
        (7000.0, "put", "10:15:05")
    ]


def test_the_replayed_spread_completes_where_spot_crosses_and_settles_as_a_fly(script, ledgers):
    inp = script.Inputs(DAY, DAY)
    (pos,) = script.replay_day(inp, DAY, "10:15", 1000.0, 360, 60)["book"]
    assert pos["completed_at"].strftime("%H:%M:%S") == "10:40:05"
    assert pos["credit"] == pytest.approx(2.40)  # the only paper entry within 15 minutes, same distance
    limit, _ = engine.completion_limit(
        {"net": 2.40, "fees": fly.vertical_open_fee("SPX"), "quantity": 1, "symbol": "SPX"},
        {"min_floor_dollars": 1.0, "fee_buffer": 0.10},
    )
    assert pos["debit"] == pytest.approx(limit)
    fees = 2 * fly.vertical_open_fee("SPX")
    expected = (2.40 - limit + fly.fly_payoff(7000.0, 5.0, PRINT)) * 100 - fees
    assert pos["pnl"] == pytest.approx(round(expected, 2))
    assert not pos["stranded"]


def test_the_cap_refuses_a_spread_whose_own_width_less_credit_does_not_fit(script, ledgers):
    inp = script.Inputs(DAY, DAY)
    out = script.replay_day(inp, DAY, "10:15", 250.0, 360, 60)  # (5 - 2.40) x 100 = 260
    assert out["book"] == [] and out["cap_refused"] > 0


def test_validate_passes_inside_the_declared_tolerance_and_fails_outside_it(script, ledgers):
    inp = script.Inputs(DAY, DAY)
    args = Namespace(
        arm="control",
        since=DAY,
        until=DAY,
        start=["10:15"],
        cap=1000.0,
        cadence=360,
        tick_seconds=60,
        phases=4,
    )
    _position(ledgers["live"], position_id="live-1", entry_time=f"{DAY}T10:15:30-04:00")
    assert script.validate(inp, args)
    for i in range(2, 5):
        _position(
            ledgers["live"],
            position_id=f"live-{i}",
            center=7000.0 + 5 * i,
            entry_time=f"{DAY}T1{i}:00:00-04:00",
        )
    assert not script.validate(inp, args)
