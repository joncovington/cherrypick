"""stream_window.py: auto-escalation/decay of a symbol's requested streamer ATM-window width,
driven by real missing_leg_quotes occurrences in the fly_decisions journal."""

from __future__ import annotations

import pytest

from cherrypick.flies import db as dbmod
from cherrypick.flies import stream_window

SYMBOL = "XSP"
DAY = "2026-07-31"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(str(tmp_path / "flies.db"))


def _miss(conn, occurrences, *, arm="gex", mode="entry", when="2026-07-31T10:00:00-04:00"):
    """Seed a fly_decisions row the way record_decision's collapsing logic would: one row whose
    occurrences count is exactly `occurrences` (simulating N consecutive identical refusals)."""
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences, center_first, center_last, position_id, detail) "
        "VALUES (?, ?, ?, ?, 'missing_leg_quotes', 0, ?, ?, ?, NULL, NULL, NULL, NULL)",
        (DAY, arm, SYMBOL, mode, when, when, occurrences),
    )
    conn.commit()


def test_no_misses_returns_base_width_and_does_not_escalate(conn):
    width = stream_window.evaluate(conn, SYMBOL, DAY, base_width=60, now="2026-07-31T10:00:00-04:00")
    assert width == 60


def test_below_threshold_does_not_escalate(conn):
    _miss(conn, 2)  # threshold is 3
    width = stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, miss_threshold=3, now="2026-07-31T10:01:00-04:00"
    )
    assert width == 60


def test_crossing_threshold_escalates_by_one_increment(conn):
    _miss(conn, 3)
    width = stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:01:00-04:00"
    )
    assert width == 90


def test_escalation_is_capped_at_max_width(conn):
    _miss(conn, 300)
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        max_width=150,
        miss_threshold=3,
        now="2026-07-31T10:01:00-04:00",
    )
    assert width == 150


def test_repeated_calls_with_no_new_misses_do_not_re_escalate(conn):
    _miss(conn, 3)
    now = "2026-07-31T10:01:00-04:00"
    first = stream_window.evaluate(conn, SYMBOL, DAY, base_width=60, miss_threshold=3, now=now)
    # Same occurrences count as before -- not a NEW miss, must not escalate a second time.
    second = stream_window.evaluate(conn, SYMBOL, DAY, base_width=60, miss_threshold=3, now=now)
    assert first == 90
    assert second == 90


def test_further_misses_escalate_again(conn):
    _miss(conn, 3)
    stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:01:00-04:00"
    )
    # occurrences keeps growing on the same collapsed row -- crosses the next threshold multiple.
    conn.execute("UPDATE fly_decisions SET occurrences = 6 WHERE symbol = ?", (SYMBOL,))
    conn.commit()
    width = stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:02:00-04:00"
    )
    assert width == 120


def test_decays_one_increment_after_a_quiet_period(conn):
    _miss(conn, 3)
    stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:00:00-04:00"
    )
    # No new misses, and 61 quiet minutes have passed -> step back down one increment.
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        decay_after_minutes=60,
        now="2026-07-31T11:01:00-04:00",
    )
    assert width == 60


def test_never_decays_below_base_width(conn):
    _miss(conn, 3)
    stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:00:00-04:00"
    )
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        decay_after_minutes=60,
        now="2026-07-31T11:01:00-04:00",
    )
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        decay_after_minutes=60,
        now="2026-07-31T12:02:00-04:00",
    )
    assert width == 60


def test_does_not_decay_before_the_quiet_window_elapses(conn):
    _miss(conn, 3)
    stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:00:00-04:00"
    )
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        decay_after_minutes=60,
        now="2026-07-31T10:30:00-04:00",
    )
    assert width == 90


def test_a_new_miss_resets_the_decay_clock(conn):
    _miss(conn, 3)
    stream_window.evaluate(
        conn, SYMBOL, DAY, base_width=60, increment=30, miss_threshold=3, now="2026-07-31T10:00:00-04:00"
    )
    # A fresh (but sub-threshold) miss at 10:50 -- not enough to escalate again, but must reset decay.
    conn.execute("UPDATE fly_decisions SET occurrences = 4 WHERE symbol = ?", (SYMBOL,))
    conn.commit()
    stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        miss_threshold=3,
        decay_after_minutes=60,
        now="2026-07-31T10:50:00-04:00",
    )
    # 61 minutes after the ORIGINAL escalation, but only 11 after the fresh miss -- must not decay yet.
    width = stream_window.evaluate(
        conn,
        SYMBOL,
        DAY,
        base_width=60,
        increment=30,
        decay_after_minutes=60,
        now="2026-07-31T11:01:00-04:00",
    )
    assert width == 90


def test_raising_base_width_in_config_floors_a_lower_persisted_width(conn):
    # No escalation has ever happened, but the operator raised the configured default above whatever
    # (nonexistent) state exists -- effective width must reflect the new floor immediately.
    width = stream_window.evaluate(conn, SYMBOL, DAY, base_width=90, now="2026-07-31T10:00:00-04:00")
    assert width == 90


def test_recent_miss_occurrences_takes_max_across_arms_not_sum(conn):
    _miss(conn, 5, arm="gex")
    _miss(conn, 3, arm="control")
    assert stream_window.recent_miss_occurrences(conn, DAY, SYMBOL) == 5


def test_recent_miss_occurrences_ignores_other_reasons_and_symbols(conn):
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences) VALUES (?, 'gex', ?, 'entry', 'no_spot_price', 0, '', '', 9)",
        (DAY, SYMBOL),
    )
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences) VALUES (?, 'gex', 'QQQ', 'entry', 'missing_leg_quotes', 0, '', '', 9)",
        (DAY,),
    )
    conn.commit()
    assert stream_window.recent_miss_occurrences(conn, DAY, SYMBOL) == 0
