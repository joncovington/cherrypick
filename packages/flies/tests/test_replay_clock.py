"""A replayed tick stamps the instant it stands for, never the hour the replay ran.

The engine decides on the snapshot's own `now_min`, but until 2026-10-12 the book stamped the wall
clock beside that decision: a replayed entry carried today's entry_time, its completion latency
measured the replay's runtime, and the cadence clock (keyed on `entry_time_min`) paced a historical
session against the time of day the replay happened to run. `process_snapshot(now=...)`,
`settle_book(now=...)` and `paper_loop.run_once(when=...)` pin every write to the tick's instant;
with no `now` the wall clock stamps exactly as before.
"""

from datetime import datetime, timedelta

import pytest
from test_engine import BASE_CONFIG, q, snapshot
from test_paper_loop import at, cache_with_chain, config  # noqa: F401 - the fixture is used by name

from cherrypick.flies import book as bookmod
from cherrypick.flies import clock, paper_loop
from cherrypick.flies import db as dbmod

ENTRY_AT = datetime(2026, 7, 20, 10, 31, 0, tzinfo=clock.ET)
COMPLETE_AT = ENTRY_AT + timedelta(minutes=47)
SETTLE_AT = datetime(2026, 7, 20, 16, 20, 0, tzinfo=clock.ET)


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def legged_config():
    return {"defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]}, "arms": {"control": {}}}


def _replay_entry_and_completion(conn):
    cfg = legged_config()
    first = bookmod.process_snapshot(
        snapshot(underlying_price=5998.0, now_min=10 * 60 + 31), cfg, conn, "control", now=ENTRY_AT
    )
    later = snapshot(
        underlying_price=6004.0, now_min=11 * 60 + 18, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)}
    )
    second = bookmod.process_snapshot(later, cfg, conn, "control", now=COMPLETE_AT)
    return first, second


def test_a_replayed_tick_stamps_the_snapshots_instant_not_the_wall_clock(conn):
    first, second = _replay_entry_and_completion(conn)
    assert any(a["action"] == "credit_spread_opened" for a in first["actions"])
    assert any(a["action"] == "completed" for a in second["actions"])

    row = dbmod.book_positions(conn, first["book_id"])[0]
    assert row["entry_time"] == "2026-07-20T10:31:00-04:00"
    assert row["completed_at"] == "2026-07-20T11:18:00-04:00"
    assert row["completion_latency_min"] == 47.0
    assert bookmod._to_position(row)["entry_time_min"] == 10 * 60 + 31  # the cadence clock
    assert row["created_at"] == "2026-07-20T10:31:00-04:00"
    assert row["updated_at"] == "2026-07-20T11:18:00-04:00"
    assert row["position_id"] == "FLY-control-SPX-20260720103100000000"

    # The journals the tick writes carry the same instant.
    iterations = [r[0] for r in conn.execute("SELECT iteration_ts FROM fly_iterations ORDER BY id")]
    assert iterations == ["2026-07-20T10:31:00-04:00", "2026-07-20T11:18:00-04:00"]
    attempts = {r[0] for r in conn.execute("SELECT ts FROM fly_entry_attempts")}
    assert attempts <= {"2026-07-20T10:31:00-04:00", "2026-07-20T11:18:00-04:00"} and attempts
    seen = {r[0] for r in conn.execute("SELECT first_seen FROM fly_decisions")}
    assert seen <= {"2026-07-20T10:31:00-04:00", "2026-07-20T11:18:00-04:00"} and seen

    bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 6001.0, legged_config(), now=SETTLE_AT)
    settled = dbmod.book_positions(conn, first["book_id"])[0]
    assert settled["status"] == "settled"
    assert settled["exit_time"] == "2026-07-20T16:20:00-04:00"


def test_without_now_the_tick_stamps_the_wall_clock_as_before(conn):
    before = clock.now_et().replace(microsecond=0)
    result = bookmod.process_snapshot(snapshot(underlying_price=5998.0), legged_config(), conn, "control")
    after = clock.now_et()
    row = dbmod.book_positions(conn, result["book_id"])[0]
    stamped = datetime.fromisoformat(row["entry_time"])
    assert before <= stamped <= after
    assert row["created_at"] == row["entry_time"]


def test_the_pin_does_not_outlive_the_tick(conn):
    _replay_entry_and_completion(conn)
    assert clock.stamp_iso() != "2026-07-20T11:18:00-04:00"
    assert abs((clock.stamp_et() - clock.now_et()).total_seconds()) < 5


def test_a_naive_instant_is_refused_rather_than_read_as_eastern(conn):
    with pytest.raises(ValueError):
        bookmod.process_snapshot(
            snapshot(), legged_config(), conn, "control", now=datetime(2026, 7, 20, 10, 31)
        )


def test_the_paper_loop_stamps_the_when_it_was_handed(cache_with_chain, conn):  # noqa: F811
    paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12))
    times = {r[0] for r in conn.execute("SELECT entry_time FROM fly_positions")}
    assert times == {"2026-07-20T12:00:00-04:00"}
    snaps = {r[0] for r in conn.execute("SELECT iteration_ts FROM fly_snapshots")}
    assert snaps == {"2026-07-20T12:00:00-04:00"}
