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
    assert payload["symbols"] == ["TNA"]
    # The plan's two dates, derived from the DATE alone.
    assert payload["expirations"] == {"TNA": ["2026-09-04", "2026-09-11"]}
    assert payload["leg_sources"][0]["db"] == db_path
    assert "streamer_symbol" in payload["leg_sources"][0]["query"]


def test_open_leg_expirations_stay_requested(cache, config, tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    db.save_position(
        conn,
        {
            "position_id": "P",
            "symbol": "TNA",
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
    wanted = stream_request.wanted_expirations(conn, ["TNA"], date(2026, 8, 24))
    assert "2026-08-28" in wanted["TNA"]  # the held leg outlives the plan roll


def test_leg_sources_query_returns_open_legs_only(tmp_path):
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    db.save_position(
        conn,
        {
            "position_id": "P",
            "symbol": "TNA",
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
    cache.spot("TNA", 70.60)
    # 70 listed $1 strikes between the window floor (~38.8) and spot -> need ~32 + margin 10.
    for strike in range(39, 71):
        cache.option("TNA", "2026-09-04", float(strike))
    hints = stream_window.hints_for_symbols(
        conn, cache.path, ["TNA"], "2026-08-24", config, deep_window_pct=0.45
    )
    # 32 strikes + 10 margin = 42 < base 60 -> no hint needed (the default window covers it).
    assert hints == {}
    # A pricier symbol scenario: a smaller base width forces the hint through. Fresh state —
    # evaluate() deliberately never lets a stored width fall below what it already asked for.
    fresh = db.connect(str(tmp_path / "paper2.db"))
    config["stream_window"] = {"base_width": 20}
    hints = stream_window.hints_for_symbols(
        fresh, cache.path, ["TNA"], "2026-08-24", config, deep_window_pct=0.45
    )
    assert hints["TNA"] == 42


def test_window_escalates_on_misses_and_decays(tmp_path, config):
    conn = db.connect(str(tmp_path / "paper.db"))
    for _ in range(3):
        db.record_decision(
            conn,
            trade_date="2026-08-24",
            book="control",
            symbol="TNA",
            mode="entry",
            reason="no_deep_itm_long",
            accepted=False,
        )
    width = stream_window.evaluate(
        conn,
        "TNA",
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
        "TNA",
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
    assert union["TNA"] == ["2026-09-04", "2026-09-11"]
