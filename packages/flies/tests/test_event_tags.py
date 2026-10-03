"""The `event` regime tag: the day's scheduled releases on every entry and completion."""

from datetime import date

from test_engine import params, snapshot

from cherrypick.flies import analytics, cli, engine, provider
from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod

NFP_DAY = {
    "date": "2026-10-02",
    "known": True,
    "missing": [],
    "events": [
        {"label": "NFP", "name": "Employment Situation", "time_et": "08:30", "major": True, "source": "FRED"}
    ],
}


def test_the_regime_tag_carries_the_days_releases():
    tag = engine.classify_regime(snapshot(events=NFP_DAY, now_min=10 * 60 + 30), params())
    assert (tag["event_bucket"], tag["event_value"], tag["event_labels"]) == ("after", 120.0, "NFP 08:30")


def test_a_snapshot_without_events_is_unknown_not_quiet():
    tag = engine.classify_regime(snapshot(), params())
    assert (tag["event_bucket"], tag["event_value"], tag["event_labels"]) == ("unknown", None, None)


def test_an_unreadable_calendar_is_unknown_and_never_refuses_the_snapshot(monkeypatch):
    from cherrypick.core import events

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(events, "day_events", boom)
    provider._EVENTS_CACHE.clear()
    doc = provider._day_events(date(2026, 10, 2))
    assert doc["known"] is False and doc["missing"] == ["error: OSError"]


def test_a_calendar_file_landing_mid_session_refreshes_the_days_events(tmp_path, monkeypatch):
    """The cache key is every file day_events reads (core's `source_paths`): Michigan's note landing
    after the loop started must show up, not wait for tomorrow (review of #21)."""
    import os

    from cherrypick.core import events

    monkeypatch.setattr(events, "calendar_dir", lambda: tmp_path)
    reads = []
    monkeypatch.setattr(events, "day_events", lambda day, root=None: reads.append(day) or {"events": []})
    provider._EVENTS_CACHE.clear()
    provider._day_events(date(2026, 10, 9))
    provider._day_events(date(2026, 10, 9))
    assert len(reads) == 1  # cached
    events.umich_path(tmp_path).write_text("{}", encoding="utf-8")
    os.utime(events.umich_path(tmp_path), (1, 1))
    provider._day_events(date(2026, 10, 9))
    assert len(reads) == 2  # a new source file is a new read


def test_entry_rows_record_the_tag_through_the_regime_columns(tmp_path):
    cols = bookmod.regime_columns("entry", snapshot(events=NFP_DAY, now_min=11 * 60), params())
    assert cols["entry_event_bucket"] == "after" and cols["entry_event_labels"] == "NFP 08:30"
    conn = dbmod.connect(str(tmp_path / "p.db"))
    names = {r["name"] for r in conn.execute("PRAGMA table_info(fly_positions)")}
    assert {f"{p}_event_{k}" for p in ("entry", "completion") for k in ("bucket", "value", "labels")} <= names


def _row(conn, pid, day, entry, completed=None):
    dbmod.save_position(
        conn,
        {
            "position_id": pid,
            "book_id": f"{day}:control:SPX",
            "trade_date": day,
            "arm": "control",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "fly" if completed else "short_vertical",
            "side": "put",
            "center": 7500.0,
            "wing_width": 5,
            "status": "settled",
            "entry_time": f"{day}T{entry}:00-04:00",
            "completed_at": f"{day}T{completed}:00-04:00" if completed else None,
        },
    )


def test_backfill_stamps_known_days_leaves_unknown_ones_blank_and_dry_runs_by_default(tmp_path, monkeypatch):
    from cherrypick.core import events

    conn = dbmod.connect(str(tmp_path / "p.db"))
    _row(conn, "A", "2026-10-02", "10:00", completed="10:40")
    _row(conn, "B", "2026-10-05", "10:00")

    def fake(day, root=None):
        return NFP_DAY if day.isoformat() == "2026-10-02" else {"known": False, "events": []}

    monkeypatch.setattr(events, "day_events", fake)
    dry = cli.backfill_events(conn, write=False)
    assert dry["stamped"] == 1 and dry["unknown_day"] == 1
    assert (
        conn.execute("SELECT entry_event_bucket FROM fly_positions WHERE position_id = 'A'").fetchone()[0]
        is None
    )

    cli.backfill_events(conn, write=True)
    a = conn.execute("SELECT * FROM fly_positions WHERE position_id = 'A'").fetchone()
    assert (a["entry_event_bucket"], a["entry_event_value"]) == ("after", 90.0)
    assert (a["completion_event_bucket"], a["completion_event_value"]) == ("after", 130.0)
    b = conn.execute("SELECT entry_event_bucket FROM fly_positions WHERE position_id = 'B'").fetchone()
    assert b[0] is None, "a day the calendar cannot speak for is not recorded, never stamped unknown"
    assert cli.backfill_events(conn, write=True)["rows"] == 1, "stamped rows are not revisited"


def test_the_event_dimension_is_cut_like_the_others():
    assert analytics.REGIME_DIMENSIONS["event"] == ("entry_event_bucket", "entry_event_value")


def test_restamp_retags_rows_already_stamped_when_the_calendar_is_corrected(tmp_path, monkeypatch):
    from cherrypick.core import events

    conn = dbmod.connect(str(tmp_path / "p.db"))
    _row(conn, "A", "2026-10-02", "10:00")
    monkeypatch.setattr(events, "day_events", lambda day, root=None: NFP_DAY)
    cli.backfill_events(conn, write=True)
    quiet = {**NFP_DAY, "events": []}
    monkeypatch.setattr(events, "day_events", lambda day, root=None: quiet)
    assert cli.backfill_events(conn, write=True)["rows"] == 0, "a plain run leaves stamped rows alone"
    cli.backfill_events(conn, write=True, restamp=True)
    assert (
        conn.execute("SELECT entry_event_bucket FROM fly_positions WHERE position_id = 'A'").fetchone()[0]
        == "none"
    )
