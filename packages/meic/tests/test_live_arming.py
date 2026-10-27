"""MEIC live arming (2026-10-14): the per-day arm record and self-disarm, the entries-per-day cap,
the orphan sweep (suite-wide known orders), the tick lock and `--status` -- the flies/bwb posture."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import execution as _execution
from cherrypick.core import live as _live
from cherrypick.core import livepositions as _livepos
from test_live_scaffold import DAY, FakeBroker, _config, _entry_snapshot, _init_db

from cherrypick.meic import live_loop, paper

QUIET = {"log": lambda *_: None}


def _pin_config(monkeypatch):
    """Build the config BEFORE patching: `_config` itself calls `paper.load_base_config`, so patching
    in `_config` directly recursed without bound and took the machine's memory (2026-10-14)."""
    cfg = _config()
    monkeypatch.setattr(paper, "load_base_config", lambda: cfg)


# --------------------------------------------------------------------------- the dead-man's switch


def test_no_arm_record_disarms(monkeypatch):
    monkeypatch.setattr(live_loop, "arm_stamp_date", lambda: None)
    assert "arm stamp" in live_loop.should_disarm(_config(), 12 * 60, DAY)


def test_todays_record_before_disarm_time_keeps_the_arm(monkeypatch):
    monkeypatch.setattr(live_loop, "arm_stamp_date", lambda: DAY)
    assert live_loop.should_disarm(_config(), 12 * 60, DAY) is None
    assert "past disarm" in live_loop.should_disarm(_config(), 17 * 60, DAY)
    assert "past disarm" in live_loop.should_disarm(_config(disarm_time="15:00"), 15 * 60, DAY)


def test_a_live_tick_without_an_arm_never_reaches_the_broker(monkeypatch, capsys):
    """Shown to fail by commenting out the should_disarm block in main(): make_broker is reached."""
    _pin_config(monkeypatch)

    def boom(*_a, **_k):
        raise AssertionError("a disarmed tick built a broker")

    monkeypatch.setattr(live_loop, "make_broker", boom)
    monkeypatch.setattr(live_loop, "_build_snapshot", boom)
    assert live_loop.main(["--once", "--live"]) == 0
    assert "disarmed" in json.loads(capsys.readouterr().out)


def test_arming_needs_a_supervisor_and_writes_todays_record(monkeypatch):
    monkeypatch.setattr(live_loop, "_spawn_first_tick", lambda: None)
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda *a, **k: False)
    assert live_loop.install_task()["ok"] is False
    assert live_loop.arm_stamp_date() is None

    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda *a, **k: True)
    out = live_loop.install_task()
    assert out["ok"] is True
    today = live_loop._pl._now_et().strftime("%Y-%m-%d")
    assert live_loop.arm_stamp_date() == today
    record = json.loads(_live.arm_record_path("meic").read_text(encoding="utf-8"))
    assert record["armed_by"] == "live-meic-start"
    assert live_loop.uninstall_task()["arm_record_removed"] is True
    assert live_loop.arm_stamp_date() is None


# --------------------------------------------------------------------------- entries per day


def _place_one(db_path, cfg):
    return live_loop.run_once(cfg, _entry_snapshot(), db_path, FakeBroker(), live=True, **QUIET)


def test_the_cap_stops_a_second_entry_the_same_day(tmp_path):
    db_path = _init_db(tmp_path)
    cfg = _config()
    assert _place_one(db_path, cfg)["entry"]["entry"] == "placed"
    second = _place_one(db_path, cfg)
    assert second["entry"]["reason"] == "max_structures_per_day_reached"


def test_a_cancelled_entry_does_not_spend_the_budget(tmp_path):
    db_path = _init_db(tmp_path)
    cfg = _config()
    first = _place_one(db_path, cfg)
    paper._update_trade(first["entry"]["ic_order_id"], {"status": "cancelled"}, db_path)
    assert live_loop.entries_today(db_path, "XSP", DAY) == 0


def test_the_cap_is_configurable():
    assert live_loop.max_structures_per_day(_config()) == 1
    assert live_loop.max_structures_per_day(_config(max_structures_per_day=3)) == 3


def test_a_null_cap_leaves_sizing_to_buying_power(tmp_path):
    """null = no count cap (2026-10-15): the second attempt is judged by the entry gates, not the
    budget -- here the overlap rule refuses the same short pair, and the cap never speaks."""
    cfg = _config(max_structures_per_day=None)
    assert live_loop.max_structures_per_day(cfg) is None
    db_path = _init_db(tmp_path)
    assert _place_one(db_path, cfg)["entry"]["entry"] == "placed"
    assert _place_one(db_path, cfg)["entry"]["reason"] != "max_structures_per_day_reached"


def _attempts(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT trade_date, arm, outcome, block_detail, ic_order_id FROM entry_attempts ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_every_live_tick_journals_its_entry_decision(tmp_path):
    """An armed day with no trade yet must still be a session in the live ledger (2026-10-23):
    the console resolves "latest session" from these rows, and with none it named the last
    trade's day under today."""
    db_path = _init_db(tmp_path)
    cfg = _config()
    placed = _place_one(db_path, cfg)
    _place_one(db_path, cfg)
    rows = _attempts(db_path)
    assert [r[2] for r in rows] == ["filled", "gate_blocked"]
    assert all(r[0] == DAY for r in rows)
    assert rows[0][4] == placed["entry"]["ic_order_id"]
    assert rows[1][3] == "max_structures_per_day_reached"


def test_a_blocked_tick_journals_why(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker(working=[{"order_id": "STRAY", "underlying_symbol": "XSP"}])
    live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, **QUIET)
    assert [(r[2], r[3]) for r in _attempts(db_path)] == [
        ("gate_blocked", "entry_blocked: orphaned_orders (1)")
    ]


def test_a_dry_run_tick_journals_nothing(tmp_path):
    db_path = _init_db(tmp_path)
    live_loop.run_once(_config(), _entry_snapshot(), db_path, FakeBroker(), live=False, **QUIET)
    assert _attempts(db_path) == []


def test_an_unreadable_ledger_spends_the_whole_budget(tmp_path):
    assert live_loop.entries_today(str(tmp_path / "missing.db"), "XSP", DAY) >= 10**6


# --------------------------------------------------------------------------- the orphan sweep


def test_an_unknown_working_order_blocks_entries_but_not_management(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker(working=[{"order_id": "STRAY", "underlying_symbol": "XSP"}])
    summary = live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, **QUIET)
    assert summary["orphans"] == 1
    assert summary["entry"]["blocked"] == ["orphaned_orders (1)"]
    assert broker.placed == []
    assert [o["order_id"] for o in live_loop.read_orphans()] == ["STRAY"]


def test_a_sweep_that_cannot_look_blocks_entries(tmp_path):
    db_path = _init_db(tmp_path)

    class Blind(FakeBroker):
        def working_orders(self):
            raise RuntimeError("broker down")

    summary = live_loop.run_once(_config(), _entry_snapshot(), db_path, Blind(), live=True, **QUIET)
    assert summary["orphans"] is None and summary["entry"]["blocked"] == ["orphan_sweep_failed"]


def test_the_ledgers_own_orders_and_other_loops_orders_are_not_orphans(tmp_path):
    db_path = _init_db(tmp_path)
    placed = _place_one(db_path, _config())
    own = placed["entry"]["order_id"]
    _execution.publish_known_orders("flies", ["FLY-1"], now_iso="t")
    broker = FakeBroker(
        working=[
            {"order_id": own, "underlying_symbol": "XSP"},
            {"order_id": "FLY-1", "underlying_symbol": "XSP"},
        ]
    )
    assert live_loop._sweep_orphans(broker, db_path, "XSP", lambda *_: None) == 0
    # and MEIC published its own ids for flies and bwb to read
    assert own in _execution.other_modules_known_orders("flies")


def test_another_symbols_order_is_not_ours(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker(working=[{"order_id": "MANUAL", "underlying_symbol": "AAPL"}])
    assert live_loop._sweep_orphans(broker, db_path, "XSP", lambda *_: None) == 0


# --------------------------------------------------------------------------- lock and status


def test_a_held_lock_skips_the_tick(monkeypatch, capsys):
    _pin_config(monkeypatch)
    monkeypatch.setattr(live_loop._looplock, "acquire", lambda *a, **k: False)
    monkeypatch.setattr(live_loop, "readiness", lambda *a, **k: [])
    monkeypatch.setattr(live_loop, "_designated_account", lambda: "1234")  # CI has no keyring backend
    assert live_loop.main(["--once"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == "another live tick is running"


def _corrupt_live_ledger():
    from cherrypick.meic import paths

    path = paths.live_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a sqlite database, it is a torn copy" * 20)
    return str(path)


def test_an_unreadable_ledger_is_never_a_clean_day(monkeypatch):
    """It read as empty: settled, nothing open, nothing overdue -- the watchdog saw a flat book."""
    _pin_config(monkeypatch)
    _corrupt_live_ledger()
    status = live_loop.run_status(_config(), designated="1234")
    assert status["ledger_unreadable"] and "unreadable" in status["ledger_unreadable"]
    assert status["session_settled"] is False and status["open_positions"] is None


def test_settle_overdue_reports_an_unreadable_ledger_instead_of_nothing_to_do(monkeypatch):
    db_path = _corrupt_live_ledger()
    out = live_loop.settle_overdue(_config(), db_path, "2026-10-27", close_fn=lambda *a: 6000.0)
    assert out["ok"] is False and "unreadable" in out["error"]


def test_a_readable_ledger_reports_no_problem(monkeypatch):
    _pin_config(monkeypatch)
    assert live_loop.run_status(_config(), designated="1234")["ledger_unreadable"] is None


def test_status_carries_what_the_watchdog_and_the_arm_command_read(monkeypatch):
    _pin_config(monkeypatch)
    status = live_loop.run_status(_config(), designated="1234")
    for key in (
        "armed_for",
        "orphaned_orders",
        "broker_held",
        "broker_health",
        "overdue_settlement",
        "session_settled",
        "open_positions",
        "max_structures_per_day",
        "entries_today",
        "unmet_gates",
        "halt_flag",
        "supervisor_fresh",
    ):
        assert key in status, key
    assert status["ok"] is True and status["armed_for"] is None


def test_status_reports_a_past_session_left_open(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path)
    _place_one(db_path, _config())  # DAY's pending entry, never confirmed
    assert live_loop.overdue_settlement(db_path, "2099-01-01") == [
        {"session": DAY, "positions": 0, "pending_entries": 1}
    ]
    assert live_loop.overdue_settlement(db_path, DAY) == []


# --------------------------------------------------------------------------- other live loops' legs


def _flies_holds_583p(qty):
    _livepos.publish_open_legs(
        "flies", [{"underlying": "XSP", "expiry": DAY, "right": "P", "strike": 583, "qty": qty}], now_iso="t"
    )


def test_an_entry_opposite_another_loops_leg_is_refused(tmp_path):
    """flies long the 583 put this IC would sell: the broker would net them flat while both ledgers
    held a leg. Refused before the broker sees it (2026-10-15)."""
    _flies_holds_583p(+1)
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    summary = live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, **QUIET)
    assert summary["entry"]["reason"].startswith("cross_module_sign_conflict")
    assert broker.placed == []


def test_the_same_side_as_another_loop_is_allowed(tmp_path):
    _flies_holds_583p(-1)
    assert _place_one(_init_db(tmp_path), _config())["entry"]["entry"] == "placed"


def test_meic_publishes_what_it_holds_and_has_working(tmp_path):
    _place_one(_init_db(tmp_path), _config())  # a pending entry: working, not yet filled
    legs = _livepos.other_modules_legs("flies")
    assert legs[("XSP", DAY, "P", 583.0)] == -1 and legs[("XSP", DAY, "C", 598.0)] == -1


def test_the_menu_carries_the_live_arms_own_delta_band(monkeypatch):
    """2026-10-23: at VIX 20 the default band was 0.14 while live's arm asked for 0.16, the menu held
    only 0.14 candidates, and `_select_candidates` left live nothing to enter all window. Shown to fail
    by passing `[delta_target]` alone in `_build_snapshot`."""
    cfg = _config()
    cfg["short_delta_target"] = 0.16
    pl = live_loop._pl
    monkeypatch.setattr(pl, "_fetch_vix", lambda: 20.0)
    monkeypatch.setattr(pl, "_delta_target", lambda *_: 0.14)
    monkeypatch.setattr(pl, "_run_json", lambda *a, **k: {})
    monkeypatch.setattr(pl, "_fetch_overview", lambda _s: (7635.0, 0.5, 0.5))
    monkeypatch.setattr(pl, "_fetch_atr", lambda *_: None)
    monkeypatch.setattr(pl, "_fetch_intraday_range_pct", lambda _s: None)

    def menu(_symbol, _last, widths, delta_targets, default_delta, _today, **_kw):
        cands = [
            {"wing_width": w, "short_delta": d, "is_default_delta": d == default_delta}
            for d in delta_targets
            for w in widths
        ]
        return cands, {}, None

    monkeypatch.setattr(pl, "_build_candidates", menu)
    snapshot, err = live_loop._build_snapshot(cfg, "SPX")
    assert err is None
    params = paper.live_params(cfg)
    assert paper._select_candidates(snapshot["candidates"], params, "SPX")


@pytest.fixture(autouse=True)
def _clean_known_orders():
    yield
    for f in [*_execution._known_orders_dir().glob("*.json"), *_livepos._open_legs_dir().glob("*.json")]:
        f.unlink()
