"""The Friday entry regime (docs/friday-entry-arm.md).

Four guards, each verified by breaking it on purpose during development — the suite's rule that a
guard has to be shown to fail:

* the regime is OFF unless configured, so landing the code changes nothing;
* `friday:path` still HOLDS, which a raw `book == "path"` comparison silently broke — the one book
  whose entire job is never to close;
* the exit gate reads "every book that intends to close has done so", never "the book is flat",
  because `path` never closes and a flat-gate would deadlock the entry on every Friday forever;
* the Friday structure tag is distinct from the Monday one, so the two populations cannot pool.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from cherrypick.calendars import clock, db, engine, management, paper_loop

ET = clock.ET


def _friday_cfg(**over):
    cfg = {
        "symbols": ["SPY"],
        "books": {"control": {"enabled": True}, "path": {"enabled": True}},
        "friday_entry": {"enabled": True, "window_start": "15:50", "window_end": "16:00"},
    }
    cfg.update(over)
    return cfg


# --- base_book: the policy identity behind a prefixed name -------------------------


def test_base_book_strips_any_prefix_stack():
    assert engine.base_book("control") == "control"
    assert engine.base_book("path") == "path"
    assert engine.base_book("advised:control") == "control"
    assert engine.base_book("friday:path") == "path"
    assert engine.base_book("friday:control") == "control"
    assert engine.base_book("advised:friday:control") == "control"


def test_friday_path_holds_like_path():
    """The break this guards: `management.decide` compared the raw name, so `friday:path` — the
    book whose whole job is never to close — was treated as a closing book."""
    d = management.evaluate(
        {"book": "friday:path", "side": "put", "status": "open"},
        {"book": "friday:path"},
        now=datetime(2026, 8, 28, 15, 50, tzinfo=ET),
        combined_value=1.0,
        combined_debit=2.0,
        spot=760.0,
    )
    assert d.action == "hold" and d.reason == "path_holds"


# --- the plan: one entry per week, tagged apart from the Monday regime -------------


def test_friday_plan_fires_only_on_the_session_before_entry():
    assert clock.friday_entry_plan(date(2026, 8, 28)) is not None  # the Friday before
    assert clock.friday_entry_plan(date(2026, 8, 25)) is None  # a Tuesday
    assert clock.friday_entry_plan(date(2026, 8, 31)) is None  # the entry session itself


def test_friday_plan_trades_the_monday_contracts_under_a_distinct_tag():
    monday = clock.week_plan(date(2026, 8, 31))
    friday = clock.friday_entry_plan(date(2026, 8, 28))
    # Identical contracts — that is the point of the regime.
    assert friday["front_expiration"] == monday["front_expiration"]
    assert friday["back_expiration"] == monday["back_expiration"]
    assert friday["week_of"] == monday["week_of"]
    # ...entered a session earlier, under a tag that can never pool with the Monday one.
    assert friday["entry_session"] == "2026-08-28"
    assert friday["structure"] != monday["structure"]
    assert friday["structure"].startswith("dc_7_")


def test_friday_books_are_the_base_roster_prefixed_and_carry_no_advised_twin():
    assert paper_loop.friday_books(_friday_cfg()) == ["friday:control", "friday:path"]
    disabled = _friday_cfg(books={"control": {"enabled": True}, "path": {"enabled": False}})
    assert paper_loop.friday_books(disabled) == ["friday:control"]


# --- the exit gate ------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    return db.connect(str(tmp_path / "cal.db"))


def _pos(conn, position_id, book, front, status="open"):
    db.save_position(
        conn,
        {
            "position_id": position_id,
            "week_of": "2026-08-24",
            "entry_session": "2026-08-24",
            "book": book,
            "side": "put",
            "symbol": "SPY",
            "structure": "dc_4_7",
            "front_expiration": front,
            "back_expiration": "2026-08-31",
            "strike": 750.0,
            "quantity": 1,
            "entry_time": "2026-08-24T10:00:00-04:00",
            "entry_debit": 1.5,
            "status": status,
        },
    )


def test_exit_gate_ignores_path_which_never_closes(conn):
    """THE deadlock guard. `path` holds to settlement by design, so a gate waiting for the book to
    go flat is satisfied on no Friday ever and the entry silently never fires — a deadlock that
    would present as a skipped week and be misdiagnosed as a feed problem."""
    _pos(conn, "w:path:put", "path", "2026-08-28")
    _pos(conn, "w:friday:path:put", "friday:path", "2026-08-28")
    assert db.pending_closing_exits(conn, "2026-08-28") == []


def test_exit_gate_blocks_while_a_closing_book_is_still_open(conn):
    _pos(conn, "w:control:put", "control", "2026-08-28")
    pending = db.pending_closing_exits(conn, "2026-08-28")
    assert [p["book"] for p in pending] == ["control"]


def test_exit_gate_clears_once_the_closing_book_has_closed(conn):
    _pos(conn, "w:control:put", "control", "2026-08-28", status="closed")
    _pos(conn, "w:path:put", "path", "2026-08-28")
    assert db.pending_closing_exits(conn, "2026-08-28") == []


def test_exit_gate_ignores_other_expirations(conn):
    _pos(conn, "w:control:put", "control", "2026-09-04")
    assert db.pending_closing_exits(conn, "2026-08-28") == []


# --- the phase: off by default, gated, and ordered ----------------------------------


def test_regime_is_off_unless_configured(conn, tmp_path):
    cfg = _friday_cfg()
    cfg["friday_entry"]["enabled"] = False
    n = paper_loop._maybe_friday_entry(
        cfg, conn, cache_path=str(tmp_path / "none.db"),
        when=datetime(2026, 8, 28, 15, 55, tzinfo=ET), now_min=15 * 60 + 55,
    )
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM dc_entry_attempts").fetchone()[0] == 0


def test_outside_the_window_does_nothing(conn, tmp_path):
    n = paper_loop._maybe_friday_entry(
        _friday_cfg(), conn, cache_path=str(tmp_path / "none.db"),
        when=datetime(2026, 8, 28, 15, 40, tzinfo=ET), now_min=15 * 60 + 40,
    )
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM dc_entry_attempts").fetchone()[0] == 0


def test_pending_exits_block_the_entry_and_are_journaled(conn, tmp_path):
    """Ordering by state: inside the window, but the session's exits have not finished."""
    _pos(conn, "w:control:put", "control", "2026-08-28")
    n = paper_loop._maybe_friday_entry(
        _friday_cfg(), conn, cache_path=str(tmp_path / "none.db"),
        when=datetime(2026, 8, 28, 15, 52, tzinfo=ET), now_min=15 * 60 + 52,
    )
    assert n == 0
    row = conn.execute("SELECT outcome, block_detail FROM dc_entry_attempts").fetchone()
    assert row["outcome"] == "awaiting_session_exits"
    assert "1 position" in row["block_detail"]


def test_monday_skip_journal_ignores_the_friday_regimes_rows(conn):
    """Once both regimes can write rows for one `week_of`, a bare positions-for-week check would
    read the Friday arm's entry as the Monday arm having traded, and genuinely skipped Mondays
    would stop being journaled."""
    _pos(conn, "2026-08-31:friday:control:put", "friday:control", "2026-09-04")
    conn.execute("UPDATE dc_positions SET week_of = '2026-08-31'")
    assert db.positions_for_week(conn, "2026-08-31") != []
    assert paper_loop._monday_regime_positions(conn, "2026-08-31") == []
