"""MEIC live settlement on the official print (2026-10-15): today's posted close or nothing, the
catch-up of a past session at that session's close, and settling a day by hand."""

from __future__ import annotations

import json

from test_live_scaffold import DAY, FakeBroker, _config, _entry_snapshot, _init_db, _open_trade_row

from cherrypick.meic import live_loop, paper

QUIET = {"log": lambda *_: None}


def _held_xsp(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("XSP"), db_path)  # 583/598 XSP, 5-wide, 1.07 credit
    return db_path


def _bell(spot=590.0):
    snap = _entry_snapshot(symbol="XSP", now_et="16:01", candidates=[])
    snap["underlying_price"] = spot
    snap["leg_quotes"] = {}
    return snap


def _tick(db_path, settle_price, spot=590.0):
    return live_loop.run_once(
        _config(symbol="XSP"),
        _bell(spot),
        db_path,
        FakeBroker(),
        live=True,
        settle_price=settle_price,
        **QUIET,
    )


def test_a_provisional_print_settles_nothing(tmp_path):
    """tastytrade's intraday last is not a close: the IC stays open and the next tick asks again.
    Shown to fail by dropping the is_official_source check: the IC expires at the tick's spot."""
    db_path = _held_xsp(tmp_path)
    summary = _tick(db_path, lambda _s: (590.1, "tastytrade_last_provisional"))
    assert summary["awaiting_official_close"] == 1 and summary["expired"] == 0
    assert live_loop._trade_row("OPEN1", db_path)["status"] == "open"


def test_no_print_at_all_settles_nothing(tmp_path):
    db_path = _held_xsp(tmp_path)
    assert _tick(db_path, lambda _s: (None, "no_source_available"))["expired"] == 0
    assert live_loop._trade_row("OPEN1", db_path)["status"] == "open"


def test_the_official_close_not_the_ticks_spot_sets_the_settlement(tmp_path):
    """The tick read 590 (both sides worthless); the posted close was 600, two points through the
    598 call. The IC settles at 600, not at what the tick happened to see."""
    db_path = _held_xsp(tmp_path)
    summary = _tick(db_path, lambda _s: (600.0, "tastytrade_close"), spot=590.0)
    assert summary["expired"] == 1 and summary["settle_price"] == 600.0
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["status"] == "expired" and row["settle_underlying"] == 600.0
    assert row["call_settle_value"] == 2.0 and row["put_settle_value"] == 0.0


def test_the_catch_up_settles_a_past_session_at_its_own_close(tmp_path):
    db_path = _held_xsp(tmp_path)
    asked = []

    def close_fn(symbol, session):
        asked.append((symbol, session))
        return 585.0

    out = live_loop.settle_overdue(_config(symbol="XSP"), db_path, "2099-01-01", close_fn=close_fn, **QUIET)
    assert asked == [("XSP", DAY)]
    assert out["settled"][0]["session"] == DAY and out["settled"][0]["price"] == 585.0
    assert live_loop._trade_row("OPEN1", db_path)["status"] == "expired"
    assert live_loop.overdue_settlement(db_path, "2099-01-01") == []


def test_the_catch_up_leaves_a_session_with_no_posted_close(tmp_path):
    db_path = _held_xsp(tmp_path)
    out = live_loop.settle_overdue(
        _config(symbol="XSP"), db_path, "2099-01-01", close_fn=lambda *_: None, **QUIET
    )
    assert out["settled"] == [] and out["left"][0]["reason"] == "no_official_close"
    assert live_loop._trade_row("OPEN1", db_path)["status"] == "open"


def test_a_session_settled_by_hand(tmp_path, monkeypatch, capsys):
    db_path = _held_xsp(tmp_path)
    cfg = _config(symbol="XSP")
    monkeypatch.setattr(paper, "load_base_config", lambda: cfg)
    monkeypatch.setattr(live_loop._paths, "live_db_path", lambda: db_path)
    assert live_loop.main(["--settle", "--date", DAY, "--price", "590"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["settled"][0]["symbol"] == "XSP" and out["settled"][0]["expired"] == 1
    assert live_loop._trade_row("OPEN1", db_path)["status"] == "expired"
