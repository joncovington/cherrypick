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


# --------------------------------------------------------------------------- 2026-09-19: floor + delta misses
def test_request_base_width_sends_the_floor_at_rest(conn):
    """Shown to fail: with nothing escalated the default convention sends no hint, so the streamer
    subscribes its own global default and the module's base_width is a number nobody reads."""
    assert stream_window.hints_for_symbols(conn, [SYMBOL], DAY, base_width=45) == {}
    assert stream_window.hints_for_symbols(conn, [SYMBOL], DAY, base_width=45, request_base_width=True) == {
        SYMBOL: 45
    }
    # an escalation still rides above the floor
    _miss(conn, 3)
    out = stream_window.hints_for_symbols(conn, [SYMBOL], DAY, base_width=45, request_base_width=True)
    assert out == {SYMBOL: 45 + stream_window.DEFAULT_INCREMENT}


def test_a_delta_rule_refusal_beyond_spot_counts_as_a_window_miss(conn):
    """Shown to fail: the delta arms refuse before choosing legs, so they never write
    missing_leg_quotes, and the escalator was blind to them."""
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences) VALUES (?, 'debit-first-up', ?, 'entry', 'no_delta_quotes_beyond_spot', 0, '', '', 4)",
        (DAY, SYMBOL),
    )
    conn.commit()
    assert stream_window.recent_miss_occurrences(conn, DAY, SYMBOL) == 4


def test_a_ledger_created_before_last_checked_occurrences_is_migrated_on_connect(tmp_path, monkeypatch):
    """Shown to fail: the deployed ledgers were created before this column existed and nothing
    added it, so evaluate() raised on every call and both loops swallowed it -- the escalator
    never ran in production. Build the table in its original shape, reopen through db.connect,
    and the column must be there and evaluate must work."""
    import sqlite3

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE fly_stream_window (symbol TEXT PRIMARY KEY, width INTEGER NOT NULL, "
        "last_escalated_occurrences INTEGER NOT NULL DEFAULT 0, last_escalated_at TEXT, "
        "last_miss_at TEXT, updated_at TEXT)"
    )
    raw.commit()
    raw.close()
    conn = dbmod.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(fly_stream_window)")}
    assert "last_checked_occurrences" in cols
    assert stream_window.evaluate(conn, SYMBOL, DAY, base_width=45, now="2026-07-31T10:00:00-04:00") == 45


def test_legs_beyond_the_strike_window_never_widen_the_request(conn):
    """A structure placed outside the snapshot's strike window is refused as
    `legs_beyond_strike_window`, which no streamer width can fix -- so however often it recurs, the
    request stays at its base."""
    when = "2026-07-31T10:00:00-04:00"
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences, center_first, center_last, position_id, detail) "
        "VALUES (?, 'callwall', ?, 'legged', 'legs_beyond_strike_window', 0, ?, ?, 78, 6250, 6250, NULL, NULL)",
        (DAY, SYMBOL, when, when),
    )
    conn.commit()
    assert stream_window.evaluate(conn, SYMBOL, DAY, base_width=60, now=when) == 60
