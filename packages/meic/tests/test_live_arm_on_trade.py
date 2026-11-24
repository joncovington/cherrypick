"""The registry arm live ran, on the trade itself (`ic_trades.live_arm`), and its backfill. Offline."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import DAY, FakeBroker, _config, _entry_snapshot, _init_db  # noqa: E402

from cherrypick.meic import db, live_arm_backfill, live_loop  # noqa: E402


def _quiet(*_):
    return None


def _tick(db_path, config):
    return live_loop.run_once(config, _entry_snapshot(), db_path, FakeBroker(), live=True, log=_quiet)


def _live_arm(db_path, order_id="LIVE-XSP-ORD1"):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT live_arm, arm FROM ic_trades WHERE ic_order_id = ?", (order_id,)
        ).fetchone()
    finally:
        conn.close()


def test_a_new_live_row_records_the_arm_live_ran(tmp_path):
    db_path = _init_db(tmp_path)
    out = _tick(db_path, _config(arm="pm-200"))
    assert out["entry"]["entry"] == "placed"
    assert _live_arm(db_path) == ("pm-200", "live")


def test_an_unset_live_arm_records_null(tmp_path):
    db_path = _init_db(tmp_path)
    out = _tick(db_path, _config())
    assert out["entry"]["entry"] == "placed"
    assert _live_arm(db_path) == (None, "live")


def test_a_recovered_entry_carries_the_arm_from_its_intent(tmp_path, monkeypatch):
    """The write-ahead row is drafted with the arm, so the tick that records a lost entry has it."""
    db_path = _init_db(tmp_path)

    class Died(Exception):
        pass

    def dies(*a, **k):
        raise Died

    monkeypatch.setattr(live_loop, "_save_placed_entry", dies)
    try:
        _tick(db_path, _config(arm="pm-200"))
    except Died:
        pass
    [intent] = list(live_loop._read_intents(db_path).values())
    assert intent["row"]["live_arm"] == "pm-200"


def test_a_failure_to_read_the_arm_records_null_and_the_entry_still_places(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path)

    def broken(_config):
        raise RuntimeError("registry unreadable")

    config = _config(arm="pm-200")
    real = live_loop.paper.live_arm_name

    def only_the_row_label_fails(cfg):
        import inspect

        if inspect.stack()[1].function == "_live_arm_or_none":
            return broken(cfg)
        return real(cfg)

    monkeypatch.setattr(live_loop.paper, "live_arm_name", only_the_row_label_fails)
    out = _tick(db_path, config)
    assert out["entry"]["entry"] == "placed"
    assert _live_arm(db_path) == (None, "live")


def test_the_migration_adds_live_arm_to_an_old_ledger_without_touching_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "meic_trades.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, "
        "symbol TEXT NOT NULL, status TEXT DEFAULT 'pending', ic_order_id TEXT UNIQUE NOT NULL, "
        "put_strike REAL, call_strike REAL, underlying_price_entry REAL, net_credit REAL, wing_width REAL, "
        "arm TEXT DEFAULT 'live', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, created_at, updated_at) "
        "VALUES ('2026-07-01', 'XSP', 'OLD-1', 'x', 'x')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "_DB_PATH", path)
    conn = sqlite3.connect(path)
    added = db._migrate(conn)
    conn.close()
    assert "ic_trades.live_arm" in added
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT live_arm, arm, status, symbol FROM ic_trades").fetchone()
    conn.close()
    assert row == (None, "live", "pending", "XSP")


# --------------------------------------------------------------------------- backfill
def _seed(db_path):
    """Three live rows: one with a filled attempt under pm-200, one whose attempt ran with no
    live.arm, one with no attempt at all (it predates attempts)."""
    conn = sqlite3.connect(db_path)
    for oid in ("T-A", "T-B", "T-C"):
        conn.execute(
            "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, arm, created_at, updated_at) "
            "VALUES (?, 'XSP', ?, 'live', 'x', 'x')",
            (DAY, oid),
        )
    for oid, arm, outcome in (
        ("T-A", "pm-200", "filled"),
        ("T-A", "other", "skipped"),
        ("T-B", "live", "filled"),
    ):
        conn.execute(
            "INSERT INTO entry_attempts (trade_date, arm, symbol, outcome, ic_order_id, ts) "
            "VALUES (?, ?, 'XSP', ?, ?, 'x')",
            (DAY, arm, outcome, oid),
        )
    conn.commit()
    conn.close()


def _column(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT ic_order_id, live_arm FROM ic_trades ORDER BY id").fetchall())
    finally:
        conn.close()


def test_the_backfill_is_a_dry_run_unless_applied(tmp_path, capsys):
    db_path = _init_db(tmp_path)
    _seed(db_path)
    assert live_arm_backfill.main(["--db", db_path]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["fill_count"] == 1 and out["unmatched"] == 2
    assert _column(db_path) == {"T-A": None, "T-B": None, "T-C": None}
    assert not list(tmp_path.glob("*.bak-pre-live-arm-*"))


def test_the_backfill_fills_matched_rows_backs_up_and_is_idempotent(tmp_path):
    db_path = _init_db(tmp_path)
    _seed(db_path)
    first = live_arm_backfill.apply(db_path, "t1")
    assert first["applied"] is True and Path(first["backup"]).exists()
    assert _column(db_path) == {"T-A": "pm-200", "T-B": None, "T-C": None}
    saved = sqlite3.connect(first["backup"])
    assert saved.execute("SELECT COUNT(*) FROM ic_trades WHERE live_arm IS NOT NULL").fetchone()[0] == 0
    saved.close()
    second = live_arm_backfill.apply(db_path, "t2")
    assert second["applied"] is False and second["fill"] == []
    assert _column(db_path) == {"T-A": "pm-200", "T-B": None, "T-C": None}
    assert not list(tmp_path.glob("*.bak-pre-live-arm-t2"))


def test_the_backfill_never_overwrites_a_recorded_arm(tmp_path):
    db_path = _init_db(tmp_path)
    _seed(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE ic_trades SET live_arm = 'recorded' WHERE ic_order_id = 'T-A'")
    conn.commit()
    conn.close()
    live_arm_backfill.apply(db_path, "t1")
    assert _column(db_path)["T-A"] == "recorded"
