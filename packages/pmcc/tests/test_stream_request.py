"""The stream request: dates-only expirations, the leg_sources SQL, and the window hints."""

import json
from datetime import date

from cherrypick.core import streamrequests as _sr

from cherrypick.pmcc import db, stream_request, stream_window


def test_request_payload_shape(cache, config, tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    path = stream_request.write(config, conn, db_path, cache_path=cache.path, today=date(2026, 8, 24))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == ["TQQQ"]
    # The plan's two dates, derived from the DATE alone.
    assert payload["expirations"] == {"TQQQ": ["2026-09-04", "2026-09-11"]}
    assert payload["leg_sources"][0]["db"] == db_path
    assert "streamer_symbol" in payload["leg_sources"][0]["query"]


def test_open_leg_expirations_stay_requested(cache, config, tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    db.save_position(
        conn,
        {
            "position_id": "P",
            "symbol": "TQQQ",
            "book": "control",
            "entry_session": "2026-08-17",
            "long_expiration": "2026-08-28",
            "long_strike": 50.0,
            "short_expiration": "2026-08-21",
            "short_strike": 67.0,
            "status": "open",
        },
    )
    db.save_leg(
        conn,
        {
            "position_id": "P",
            "leg_role": "long_call",
            "occ_symbol": "X",
            "streamer_symbol": ".X",
            "expiration": "2026-08-28",
            "strike": 50.0,
            "option_type": "call",
            "action": "Buy to Open",
            "status": "open",
        },
    )
    wanted = stream_request.wanted_expirations(conn, ["TQQQ"], date(2026, 8, 24))
    assert "2026-08-28" in wanted["TQQQ"]  # the held leg outlives the plan roll


def test_leg_sources_query_returns_open_legs_only(tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    db.save_position(
        conn,
        {
            "position_id": "P",
            "symbol": "TQQQ",
            "book": "control",
            "entry_session": "2026-08-17",
            "long_expiration": "2026-08-28",
            "long_strike": 50.0,
            "short_expiration": "2026-08-21",
            "short_strike": 67.0,
            "status": "open",
        },
    )
    for role, status in (("long_call", "open"), ("short_call_1", "closed")):
        db.save_leg(
            conn,
            {
                "position_id": "P",
                "leg_role": role,
                "occ_symbol": "X",
                "streamer_symbol": f".{role}",
                "expiration": "2026-08-28",
                "strike": 50.0,
                "option_type": "call",
                "action": "Buy to Open",
                "status": status,
            },
        )
    query = (
        "SELECT l.streamer_symbol FROM pmcc_legs l JOIN pmcc_positions p "
        "ON p.position_id = l.position_id WHERE l.status = 'open' AND p.status != 'closed'"
    )
    rows = [r[0] for r in conn.execute(query)]
    assert rows == [".long_call"]


def test_window_hint_computed_from_deep_chain(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cache.spot("TQQQ", 70.60)
    # 70 listed $1 strikes between the window floor (~38.8) and spot -> need ~32 + margin 10.
    for strike in range(39, 71):
        cache.option("TQQQ", "2026-09-04", float(strike))
    hints = stream_window.hints_for_symbols(
        conn, cache.path, ["TQQQ"], "2026-08-24", config, deep_window_pct=0.45
    )
    # 32 strikes + 10 margin = 42 < base 60 -> no hint needed (the default window covers it).
    assert hints == {}
    # A pricier symbol scenario: a smaller base width forces the hint through. Fresh state —
    # evaluate() deliberately never lets a stored width fall below what it already asked for.
    fresh = db.connect(str(tmp_path / "paper2.db"))
    config["stream_window"] = {"base_width": 20}
    hints = stream_window.hints_for_symbols(
        fresh, cache.path, ["TQQQ"], "2026-08-24", config, deep_window_pct=0.45
    )
    assert hints["TQQQ"] == 42


def test_window_escalates_on_misses_and_decays(tmp_path, config):
    conn = db.connect(str(tmp_path / "paper.db"))
    for _ in range(3):
        db.record_decision(
            conn,
            trade_date="2026-08-24",
            book="control",
            symbol="TQQQ",
            mode="entry",
            reason="no_deep_itm_long",
            accepted=False,
        )
    width = stream_window.evaluate(
        conn,
        "TQQQ",
        "2026-08-24",
        base_width=60,
        increment=30,
        miss_threshold=3,
        now="2026-08-24T11:00:00",
    )
    assert width == 90
    # Quiet for over the decay window: one increment comes back off.
    width = stream_window.evaluate(
        conn,
        "TQQQ",
        "2026-08-24",
        base_width=60,
        increment=30,
        miss_threshold=3,
        decay_after_minutes=60,
        now="2026-08-24T13:00:00",
    )
    assert width == 60


def test_union_read_sees_the_request(cache, config, tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    stream_request.write(config, conn, db_path, cache_path=cache.path, today=date(2026, 8, 24))
    union = _sr.union_expirations(today=date(2026, 8, 24))
    assert union["TQQQ"] == ["2026-09-04", "2026-09-11"]


# --- the widened window is entry-only ------------------------------------------------------
# `needed_width` is stubbed so these exercise the SLOT gate rather than the fixture chain's depth:
# whether a hint is produced at all is what is under test, not how wide the chain says it must be.


def _open_pos(conn, symbol="TQQQ", book="control", pid="HELD"):
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": symbol,
            "book": book,
            "entry_session": "2026-08-17",
            "long_expiration": "2026-09-11",
            "long_strike": 50.0,
            "short_expiration": "2026-09-04",
            "short_strike": 67.0,
            "status": "open",
        },
    )


def _hints(conn, cache, config, monkeypatch, **kw):
    monkeypatch.setattr(stream_window, "needed_width", lambda *a, **k: 163)
    return stream_window.hints_for_symbols(
        conn, cache.path, ["TQQQ"], "2026-08-24", config, deep_window_pct=0.20, **kw
    )


def test_hint_is_dropped_while_every_slot_is_held(cache, config, tmp_path, monkeypatch):
    """The widened window exists to find the deep long AT ENTRY. Once the slot is held nothing can
    be entered until it closes — one to two WEEKS for a hold-to-expiration cycle — and the open
    position's marks come from `leg_sources`, never from this window. Measured 2026-08-24: the held
    symbol's window was 84% of the suite's updating option quotes."""
    conn = db.connect(str(tmp_path / "paper.db"))
    free = _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1)
    assert free.get("TQQQ") == 163, "a free slot must still ask for its deep window"

    _open_pos(conn)
    held = _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1)
    assert "TQQQ" not in held, "a held slot must not keep paying for an unusable window"


def test_hint_returns_as_soon_as_the_slot_frees(cache, config, tmp_path, monkeypatch):
    """It returns on slot-free rather than on entry-attempt, so the quotes have a subscription poll
    or two to arrive before the module wants them."""
    conn = db.connect(str(tmp_path / "paper.db"))
    _open_pos(conn)
    assert "TQQQ" not in _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1)
    conn.execute("UPDATE pmcc_positions SET status = 'closed'")
    conn.commit()
    assert _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1).get("TQQQ")


def test_a_second_free_book_keeps_the_window_alive(cache, config, tmp_path, monkeypatch):
    """The gate is ANY book able to enter, matching the entry phase's own test — one book holding
    must not drop a window another book could still use."""
    conn = db.connect(str(tmp_path / "paper.db"))
    _open_pos(conn, book="control")
    hints = _hints(
        conn, cache, config, monkeypatch, books=["control", "advised:control"], max_positions=2
    )
    assert hints.get("TQQQ") == 163


def test_max_positions_cap_closes_the_window_too(cache, config, tmp_path, monkeypatch):
    """A free (symbol, book) slot is not enough on its own — the book's own cap is the other half
    of the entry phase's condition, so the window must respect it as well."""
    conn = db.connect(str(tmp_path / "paper.db"))
    _open_pos(conn, symbol="XSP", pid="OTHER")  # control holds its one allowed position, elsewhere
    hints = _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1)
    assert "TQQQ" not in hints


def test_no_roster_keeps_the_old_unconditional_behaviour(cache, config, tmp_path, monkeypatch):
    """Callers that pass no roster (older call sites, direct use) must be unaffected."""
    conn = db.connect(str(tmp_path / "paper.db"))
    _open_pos(conn)
    assert _hints(conn, cache, config, monkeypatch).get("TQQQ") == 163


# --- the deep window is per SYMBOL -----------------------------------------------------------


def test_deep_window_pct_resolves_per_symbol_then_falls_back(config):
    from cherrypick.pmcc import provider

    cfg = {
        "defaults": {
            "deep_window_pct": 0.20,
            "deep_window_pct_by_symbol": {"XSP": 0.06},
        }
    }
    # Measured 2026-08-24: TQQQ's 85-90 delta call sat ~15-19% ITM, XSP's ~4%. One shared bound
    # therefore buys XSP roughly five times the strikes it can use.
    assert provider.deep_window_pct_for(cfg, "XSP") == 0.06
    assert provider.deep_window_pct_for(cfg, "TQQQ") == 0.20  # falls back to the shared default
    assert provider.deep_window_pct_for(cfg, "tqqq") == 0.20  # case-insensitive


def test_deep_window_pct_unchanged_when_no_map_is_declared(config):
    """Additive: a config declaring no per-symbol map behaves exactly as before."""
    from cherrypick.pmcc import provider

    assert provider.deep_window_pct_for({"defaults": {"deep_window_pct": 0.20}}, "XSP") == 0.20
    assert provider.deep_window_pct_for({}, "XSP") == provider.DEFAULT_DEEP_WINDOW_PCT


def test_window_hints_use_each_symbols_own_bound(cache, tmp_path, monkeypatch):
    """The hint is the SUBSCRIPTION cost, so it must narrow with the per-symbol bound — otherwise
    the snapshot reads a tighter window while the producer still pays for the wide one."""
    from cherrypick.pmcc import provider, stream_window

    conn = db.connect(str(tmp_path / "paper.db"))
    seen: dict[str, float] = {}

    def fake_needed(cache_path, symbol, *, deep_window_pct, margin):
        seen[symbol] = deep_window_pct
        return 163

    monkeypatch.setattr(stream_window, "needed_width", fake_needed)
    cfg = {
        "defaults": {"deep_window_pct": 0.20, "deep_window_pct_by_symbol": {"XSP": 0.06}},
        "books": {"control": {"enabled": True}},
    }
    stream_window.hints_for_symbols(
        conn, cache.path, ["TQQQ", "XSP"], "2026-08-24", cfg, books=["control"], max_positions=1
    )
    assert seen == {"TQQQ": 0.20, "XSP": 0.06}
    assert provider.deep_window_pct_for(cfg, "XSP") == 0.06
