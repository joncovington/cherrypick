"""The bwb LIVE path: what it may do, what it must refuse, and what it records when.

Every guard here was broken on purpose once and watched to fail before it was trusted (the suite
rule): a green check that cannot fire reads as coverage.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from cherrypick.core import home as _home

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import broker_cli, db, engine

# --------------------------------------------------------------------------- ledger


def test_live_ledger_is_a_separate_file_and_never_the_env_override(managed_home, monkeypatch):
    """Paper readers resolve `paper_trades.db` by name, so a live fill can only reach a paper
    surface by being written into the paper file. It cannot be: the live path is its own file and
    ignores `BWB_DB_PATH`, the override that points paper elsewhere."""
    monkeypatch.setenv("BWB_DB_PATH", str(managed_home / "elsewhere.db"))
    assert db.live_db_path() == os.path.join(str(_home.home()), "data", "bwb", "live_trades.db")
    assert db.live_db_path() != db.default_db_path()
    assert os.path.basename(db.live_db_path()) == "live_trades.db"


_PRE_LIVE_SCHEMA = """
CREATE TABLE bwb_positions (
    id INTEGER PRIMARY KEY, position_id TEXT UNIQUE, symbol TEXT, book TEXT, entry_session TEXT,
    status TEXT, entry_credit REAL, fees REAL, gross_pnl REAL, quantity INTEGER
);
"""


_LIVE_COLUMNS = (
    "entry_order_id",
    "entry_external_id",
    "entry_fill_status",
    "entry_limit",
    "entry_placed_at",
    "entry_live_floor",
    "entry_reprice_count",
    "entry_mid_at_submit",
    "addon_order_id",
    "addon_external_id",
    "addon_fill_status",
    "addon_placed_at",
    "addon_live_floor",
    "addon_reprice_count",
    "addon_mid_at_submit",
    "addon_attempts",
    "pending_addon_json",
    "entry_fee_estimate",
    "addon_fee_estimate",
    "fees_source",
    "modeled_fees",
    "modeled_gross_pnl",
    "reconciled_at",
    "settlement_source",
)


def test_live_columns_migrate_additively_onto_a_pre_live_ledger(managed_home):
    """A ledger created before the live scaffold gains every live column on connect, with the
    existing rows untouched and NULL in the new columns (the LedgerStore additive rule)."""
    path = managed_home / "old.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.executescript(_PRE_LIVE_SCHEMA)
    raw.execute(
        "INSERT INTO bwb_positions (position_id, symbol, book, entry_session, status, entry_credit) "
        "VALUES ('SPX:control:2026-09-01', 'SPX', 'control', '2026-09-01', 'open', 0.9)"
    )
    raw.commit()
    raw.close()
    conn = db.connect(str(path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
    # Named here rather than read off the map, so dropping a column from the map is caught.
    for name in _LIVE_COLUMNS:
        assert name in cols, name
        assert name in db._ADDED_COLUMNS["bwb_positions"], name
    row = conn.execute("SELECT * FROM bwb_positions").fetchone()
    assert row["entry_credit"] == 0.9 and row["entry_fill_status"] is None and row["addon_order_id"] is None


# --------------------------------------------------------------------------- gates


def test_live_gates_fail_closed_and_require_a_base_book_arm():
    assert broker_cli.live_gates({}) == ["live.enabled is false"]
    assert broker_cli.live_gates({"live": {"enabled": False, "gate0_confirmed": "jon", "arm": "control"}})
    unmet = broker_cli.live_gates({"live": {"enabled": True}})
    assert any("gate0_confirmed" in g for g in unmet) and any("live.arm" in g for g in unmet)
    for bad in ("wall", "advised:control", "", "Control"):
        unmet = broker_cli.live_gates({"live": {"enabled": True, "gate0_confirmed": "jon", "arm": bad}})
        assert unmet and "not a base book" in unmet[0], bad
    for arm in engine.BOOKS:
        assert broker_cli.live_gates({"live": {"enabled": True, "gate0_confirmed": "jon", "arm": arm}}) == []


# --------------------------------------------------------------------------- readers


def _plan(expiration="2026-09-25", body=7600.0, credit=0.9):
    near, far = body + 5, body - 10
    from conftest import occ, streamer_sym

    def leg(role, strike, action):
        return {
            "leg_role": role,
            "occ_symbol": occ("SPXW", expiration, strike),
            "streamer_symbol": streamer_sym("SPXW", expiration, strike),
            "expiration": expiration,
            "strike": strike,
            "option_type": "put",
            "action": action,
            "bid": 1.0,
            "ask": 1.2,
            "mid": 1.1,
            "iv": 0.2,
            "delta": -0.3,
        }

    return {
        "symbol": "SPX",
        "spot": 7700.0,
        "expiration": expiration,
        "dte": 7,
        "atm_strike": 7700.0,
        "expected_move": 100.0,
        "body_strike": body,
        "near_strike": near,
        "far_strike": far,
        "body_mid": 1.5,
        "near_mid": 1.9,
        "far_mid": 0.2,
        "credit": credit,
        "narrow_width": 5.0,
        "wide_width": 10.0,
        "max_loss_up": 0.0,
        "max_loss_down": 10.0 - credit,
        "max_loss": 10.0 - credit,
        "legs": [
            leg("near_long", near, "Buy to Open"),
            leg("body_short_1", body, "Sell to Open"),
            leg("body_short_2", body, "Sell to Open"),
            leg("far_long", far, "Buy to Open"),
        ],
    }


@pytest.fixture
def live_conn(managed_home):
    conn = db.connect(db.live_db_path())
    yield conn
    conn.close()


def test_enter_position_default_is_unchanged_and_the_live_override_is_additive(live_conn, config):
    paper = bookmod.enter_position(
        live_conn, _plan(), config, "control", entry_session="2026-09-18", advice_params=None
    )
    assert paper["position_id"] == "SPX:control:2026-09-18"
    row = db.open_position_for(live_conn, "SPX", "control", "2026-09-18")
    assert row["entry_fill_status"] is None and row["entry_order_id"] is None
    live = bookmod.enter_position(
        live_conn,
        _plan(),
        config,
        "delta",
        entry_session="2026-09-18",
        advice_params=None,
        position_id_override="SPX:delta:2026-09-18:1",
        extra={"entry_order_id": "OID-1", "entry_fill_status": "pending", "entry_limit": 0.85},
    )
    assert live["position_id"] == "SPX:delta:2026-09-18:1"
    row = live_conn.execute(
        "SELECT * FROM bwb_positions WHERE position_id = ?", (live["position_id"],)
    ).fetchone()
    assert (
        row["entry_order_id"] == "OID-1"
        and row["entry_fill_status"] == "pending"
        and row["entry_limit"] == 0.85
    )
    assert row["entry_credit"] == 0.9  # the modeled credit until the broker says what filled


def test_live_readers_count_only_what_was_established(live_conn, config):
    kw = dict(entry_session="2026-09-18", advice_params=None)
    bookmod.enter_position(
        live_conn,
        _plan(),
        config,
        "control",
        position_id_override="SPX:control:2026-09-18:1",
        extra={"entry_order_id": "A", "entry_fill_status": "pending"},
        **kw,
    )
    bookmod.enter_position(
        live_conn,
        _plan(),
        config,
        "control",
        position_id_override="SPX:control:2026-09-18:2",
        extra={"entry_order_id": "B", "entry_fill_status": "pending", "addon_order_id": "B2"},
        **kw,
    )
    assert [p["entry_order_id"] for p in db.pending_entries(live_conn)] == ["A", "B"]
    assert sorted(db.known_order_ids(live_conn)) == ["A", "B", "B2"]
    assert db.established_today(live_conn, "control", "2026-09-18") == 2
    db.save_position(live_conn, {"position_id": "SPX:control:2026-09-18:1", "status": "cancelled"})
    assert db.established_today(live_conn, "control", "2026-09-18") == 1  # a cancelled entry spends nothing
    assert db.established_today(live_conn, "delta", "2026-09-18") == 0
    assert db.pending_addons(live_conn) == []
    db.save_position(live_conn, {"position_id": "SPX:control:2026-09-18:2", "addon_fill_status": "pending"})
    assert [p["addon_order_id"] for p in db.pending_addons(live_conn)] == ["B2"]


def test_settled_net_and_marked_loss_readers(live_conn, config):
    kw = dict(entry_session="2026-09-18", advice_params=None)
    bookmod.enter_position(live_conn, _plan(), config, "control", **kw)
    pid = "SPX:control:2026-09-18"
    assert db.settled_net_for_session(live_conn, "2026-09-25") == 0.0
    assert db.open_marked_loss(live_conn) is None  # no usable mark yet: the breaker must not read "fine"
    for role in ("near_long", "body_short_1", "body_short_2", "far_long"):
        db.record_mark(
            live_conn,
            position_id=pid,
            leg_role=role,
            marked_at=1.0,
            session_date="2026-09-18",
            close_cost=3.4,
            usable=1,
            quote_age_s=1.0,
        )
    assert db.open_marked_loss(live_conn) == pytest.approx((3.4 - 0.9) * 100)
    # one unpriceable leg on the latest tick -> the position cannot be priced -> None, not "fine"
    for role in ("near_long", "body_short_1", "body_short_2"):
        db.record_mark(
            live_conn,
            position_id=pid,
            leg_role=role,
            marked_at=2.0,
            session_date="2026-09-18",
            close_cost=None,
            usable=1,
        )
    db.record_mark(
        live_conn,
        position_id=pid,
        leg_role="far_long",
        marked_at=2.0,
        session_date="2026-09-18",
        usable=0,
        refusal="missing_leg_quotes",
    )
    assert db.open_marked_loss(live_conn) is None
    db.save_position(
        live_conn,
        {
            "position_id": pid,
            "status": "closed",
            "closed_session": "2026-09-25",
            "gross_pnl": -420.0,
            "fees": 36.89,
        },
    )
    assert db.settled_net_for_session(live_conn, "2026-09-25") == pytest.approx(-456.89)
    assert db.open_marked_loss(live_conn) == 0.0
