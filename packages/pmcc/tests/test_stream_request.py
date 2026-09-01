"""The stream request: dates-only expirations, the leg_sources SQL, and the window hints."""

import json
import pathlib
from datetime import date

from cherrypick.core import streamrequests as _sr

from cherrypick.pmcc import clock, db, stream_request, stream_window


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
    cache.spot("TQQQ", 70.60)
    # 70 listed $1 strikes between the window floor (~38.8) and spot -> need ~32 + margin 10.
    for strike in range(39, 71):
        cache.option("TQQQ", "2026-09-04", float(strike))
    # The computed need is emitted whatever it is. This used to be suppressed below `base_width`,
    # on the reasoning that "the default window covers it" — and `base_width` was a hand-copied
    # mirror of the producer's default that stopped being true when the producer was cut 60 -> 30
    # in the 2026-08-24 subscription incident. Anything needing 31-60 strikes was then silently
    # swallowed while the producer served 30. The producer already resolves `max(default, hint)`,
    # so a hint under its default is correctly ignored at the other end and suppressing here only
    # ever risked drift.
    fresh = db.connect(str(tmp_path / "paper2.db"))
    config["stream_window"] = {"base_width": 20, "margin": 10}
    hints = stream_window.hints_for_symbols(
        fresh, cache.path, ["TQQQ"], "2026-08-24", config, deep_window_pct=0.45
    )
    assert hints["TQQQ"] == {"down": 42, "up": 10}, "32 strikes of chain + 10 margin"


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


def _seed_position(conn, symbol: str, book: str, session: str = "2026-08-24") -> None:
    db.save_position(
        conn,
        {
            "position_id": f"{symbol}:{book}:{session}",
            "symbol": symbol,
            "book": book,
            "entry_session": session,
            "status": "open",
            "quantity": 1,
            "long_expiration": "2026-09-18",
            "short_expiration": "2026-09-04",
            "long_strike": 57.0,
            "short_strike": 70.0,
        },
    )
    conn.commit()


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
    assert free.get("TQQQ") == {"down": 163, "up": 10}, "a free slot must still ask for its deep window"

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
    hints = _hints(conn, cache, config, monkeypatch, books=["control", "advised:control"], max_positions=2)
    assert hints.get("TQQQ") == {"down": 163, "up": 10}


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
    assert _hints(conn, cache, config, monkeypatch).get("TQQQ") == {"down": 163, "up": 10}


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


def test_the_deep_window_is_asked_for_downward_only(cache, config, tmp_path, monkeypatch):
    """Everything the widened window exists to find sits BELOW spot. A symmetric count bought an
    identical block above it that no book here can read — the largest single waste in the suite's
    subscription budget on 2026-08-24."""
    conn = db.connect(str(tmp_path / "paper.db"))
    hint = _hints(conn, cache, config, monkeypatch, books=["control"], max_positions=1)["TQQQ"]
    assert hint["down"] > hint["up"]
    # The upward figure is the declared margin, not a share of the deep need: the ATM short is
    # covered by the producer's own default window whatever this asks for.
    assert hint["up"] == 10


# --------------------------- the advised twin has its own slot, and its own window (2026-08-27)
#
# Control filled XSP on 2026-08-24; the window-hint gate asked only "can CONTROL still enter?",
# went False, and dropped the widened window. The advised twin — a real book with its own slot,
# still trying — then recorded 658 `no_deep_itm_long` refusals across the whole of 08-25 and
# 08-26 before a lucky re-centre let it in on the 27th. An A/B whose two arms cannot enter on the
# same days is not an A/B.


def test_a_free_advised_slot_keeps_the_window_alive_after_control_fills(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _seed_position(conn, "TQQQ", "control")  # control is full; the advised twin is not

    held_by_control_only = stream_window.hints_for_symbols(
        conn,
        cache.path,
        ["TQQQ"],
        "2026-08-24",
        config,
        books=["control"],
        max_positions=1,
    )
    assert held_by_control_only == {}, "the defect: control's full slot dropped the whole window"

    with_advised = stream_window.hints_for_symbols(
        conn,
        cache.path,
        ["TQQQ"],
        "2026-08-24",
        config,
        books=["control", "advised:control"],
        max_positions=1,
    )
    assert with_advised.get("TQQQ"), "the advised twin can still enter, so it still needs the depth"


def test_entry_possible_sees_the_advised_twins_free_slot(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _seed_position(conn, "TQQQ", "control")
    assert stream_window.entry_possible(conn, "TQQQ", ["control"], 1) is False
    assert stream_window.entry_possible(conn, "TQQQ", ["control", "advised:control"], 1) is True


def test_a_symbol_every_book_holds_still_gets_no_window():
    """The saving the gate exists for has to survive the fix: once BOTH books hold it, nothing can
    be entered until one closes and the widened window is pure cost."""
    import tempfile

    conn = db.connect(str(pathlib.Path(tempfile.mkdtemp()) / "p.db"))
    _seed_position(conn, "TQQQ", "control")
    _seed_position(conn, "TQQQ", "advised:control")
    assert stream_window.entry_possible(conn, "TQQQ", ["control", "advised:control"], 1) is False


def test_the_written_request_keeps_the_window_for_a_free_advised_slot(cache, config, tmp_path, monkeypatch):
    """End-to-end through `stream_request.write`, which is where the roster was actually wrong —
    the direct `hints_for_symbols` tests above pass an explicit `books` and so cannot see it."""
    monkeypatch.setattr(stream_window, "needed_width", lambda *a, **k: 163)
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    _open_pos(conn, symbol="TQQQ", book="control")  # control full, advised twin free

    # An active advice artifact is what puts `advised:control` on the roster.
    monkeypatch.setattr(
        __import__("cherrypick.pmcc.paper_loop", fromlist=["x"]),
        "advice_decision",
        lambda cfg, day: {"params": {"tv_managed_exit": 1}, "base_book": "control"},
    )
    path = stream_request.write(config, conn, db_path, cache_path=cache.path, today=date(2026, 8, 24))
    hints = json.loads(path.read_text(encoding="utf-8"))["window_hints"]
    assert hints.get("TQQQ"), (
        "control holding TQQQ must not drop the window while the advised twin can still enter"
    )


def test_a_session_with_every_slot_held_still_records_why(cache, config, tmp_path, monkeypatch):
    """2026-08-28: pmcc showed no entry attempts at all. The loop was healthy — 241 entry
    iterations, marks 0.5 min old — and every book already held every symbol, so the entry phase
    short-circuited before recording anything. "All slots full" and "the loop never evaluated
    entry" produced the identical empty table, which is the pair this module most needs to tell
    apart."""
    from cherrypick.pmcc import paper_loop

    conn = db.connect(str(tmp_path / "paper.db"))
    for sym in config["symbols"]:
        _seed_position(conn, sym, "control")

    paper_loop._try_entries(
        config, conn, cache_path=cache.path, when=clock.now_et(), day="2026-08-28"
    )

    held = conn.execute(
        "SELECT symbol, reason, occurrences FROM pmcc_decisions WHERE reason = 'slot_held'"
    ).fetchall()
    assert {r["symbol"] for r in held} == set(config["symbols"])
    # Collapsed, so a whole session of it is one counted row per book/symbol rather than one a tick.
    paper_loop._try_entries(
        config, conn, cache_path=cache.path, when=clock.now_et(), day="2026-08-28"
    )
    again = conn.execute(
        "SELECT occurrences FROM pmcc_decisions WHERE reason = 'slot_held' LIMIT 1"
    ).fetchone()
    assert again["occurrences"] == 2
