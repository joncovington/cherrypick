"""The bwb LIVE path: what it may do, what it must refuse, and what it records when.

Every guard here was broken on purpose once and watched to fail before it was trusted (the suite
rule): a green check that cannot fire reads as coverage.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cherrypick.core import home as _home
from cherrypick.core import live as _core_live

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
    id INTEGER PRIMARY KEY, position_id TEXT UNIQUE, symbol TEXT, arm TEXT, entry_session TEXT,
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
    "entry_repriced_at",
    "entry_reprice_count",
    "entry_mid_at_submit",
    "addon_order_id",
    "addon_external_id",
    "addon_fill_status",
    "addon_placed_at",
    "addon_live_floor",
    "addon_reprice_count",
    "addon_repriced_at",
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
        "INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, status, entry_credit) "
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


_ET = ZoneInfo("America/New_York")
_GATE_DAY = "2026-10-13"
_GATE_WHEN = datetime(2026, 10, 13, 10, 30, tzinfo=_ET)


def _arm_for(day: str) -> None:
    _core_live.write_arm_record("bwb", date=day, armed_by="test", at=f"{day}T09:00:00")


def _gates(cfg, designated="5WX11234", when=_GATE_WHEN):
    return broker_cli.live_gates(cfg, designated=designated, when=when)


def test_live_gates_fail_closed_and_require_a_base_book_arm():
    _arm_for(_GATE_DAY)
    assert broker_cli.live_gates({}) == ["live.enabled is false"]
    assert _gates({"live": {"enabled": False, "gate0_confirmed": "owner", "arm": "control"}})
    unmet = _gates({"live": {"enabled": True}})
    assert any("gate0_confirmed" in g for g in unmet) and any("live.arm" in g for g in unmet)
    for bad in ("wall", "advised:control", "", "Control"):
        unmet = _gates({"live": {"enabled": True, "gate0_confirmed": "owner", "arm": bad}})
        assert unmet and "not a base arm" in unmet[0], bad
    for arm in engine.ARMS:
        assert _gates({"live": {"enabled": True, "gate0_confirmed": "owner", "arm": arm}}) == []


_LIVE = {"live": {"enabled": True, "gate0_confirmed": "owner", "arm": "control"}}


def test_live_gates_are_readiness_plus_the_day_arm_record():
    """One source: every readiness refusal is a live_gates refusal, with the same words. The copy
    this replaced had no designated-account gate, and disagreed with readiness on a missing arm."""
    from cherrypick.bwb import live_loop

    _arm_for(_GATE_DAY)
    for cfg, designated in (
        (_LIVE, "5WX11234"),
        (_LIVE, None),
        ({"live": {"enabled": True}}, None),
        ({"live": {"enabled": True, "gate0_confirmed": "owner"}}, "5WX11234"),
    ):
        assert _gates(cfg, designated) == live_loop.readiness(cfg, designated=designated), cfg
    assert any("designated" in g for g in _gates(_LIVE, None))


def test_an_unarmed_session_refuses_a_live_submission():
    unarmed = _gates(_LIVE)
    assert len(unarmed) == 1 and "not armed" in unarmed[0]
    _arm_for("2026-10-12")
    assert any("not armed" in g for g in _gates(_LIVE))
    _arm_for(_GATE_DAY)
    assert _gates(_LIVE) == []
    assert any("disarm time" in g for g in _gates(_LIVE, when=datetime(2026, 10, 13, 17, 5, tzinfo=_ET)))


@pytest.fixture
def by_hand(monkeypatch):
    """`execute_trade --live` with the broker faked out: what reached `place_order`, if anything."""
    placed = []
    monkeypatch.setattr(broker_cli, "load_config", lambda *a, **k: _LIVE)
    monkeypatch.setattr(broker_cli.creds, "designated_account", lambda: "5WX11234")
    monkeypatch.setattr(broker_cli.clock, "now_et", lambda: _GATE_WHEN)
    monkeypatch.setattr(broker_cli.creds, "get_session", lambda: object())

    async def account(_session):
        return "acct"

    async def place_order(account, session, order, *, live, deploy_limit_pct):
        placed.append(live)
        return {"ok": True, "live": live}

    monkeypatch.setattr(broker_cli, "_account", account)
    monkeypatch.setattr(broker_cli._broker, "build_order", lambda spec: spec)
    monkeypatch.setattr(broker_cli._broker, "place_order", place_order)
    args = type("Args", (), {"live": True, "order": "{}"})()
    return lambda: asyncio.run(broker_cli.cmd_execute_trade(args)), placed


def test_execute_trade_live_refuses_on_an_unarmed_session(by_hand):
    run, placed = by_hand
    out = run()
    assert out["ok"] is False and any("not armed" in g for g in out["unmet_gates"])
    assert placed == []


def test_execute_trade_live_passes_the_gate_on_an_armed_session(by_hand):
    run, placed = by_hand
    _arm_for(_GATE_DAY)
    assert run() == {"ok": True, "live": True}
    assert placed == [True]


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
