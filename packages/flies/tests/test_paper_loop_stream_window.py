"""The resident paper loop re-declares its stream request every tick, so the window escalator can act
mid-session. Until 2026-09-24 it was evaluated once per process: callwall refused 69 times on
missing_leg_quotes from 13:30 ET and the request did not widen until the next process start, after
the close."""

from __future__ import annotations

import json

from cherrypick.flies import db as dbmod
from cherrypick.flies import paper_loop as pl
from cherrypick.flies import provider, stream_request


def _miss(conn, day, occurrences):
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
        "last_seen, occurrences, center_first, center_last, position_id, detail) "
        "VALUES (?, 'callwall', 'SPX', 'legged', 'missing_leg_quotes', 0, ?, ?, ?, NULL, NULL, NULL, NULL)",
        (day, f"{day}T13:30:43-04:00", f"{day}T13:30:58-04:00", occurrences),
    )
    conn.commit()


def test_a_resident_session_widens_on_misses_recorded_after_it_started(tmp_path, monkeypatch):
    cfg_path = tmp_path / "flies.json"
    cfg_path.write_text(
        json.dumps({"symbols": ["SPX"], "stream_window": {"base_width": 45, "request_base_width": True}}),
        encoding="utf-8",
    )
    db_path = str(tmp_path / "paper.db")
    day = provider.now_et().date().isoformat()

    declared: list[dict | None] = []
    monkeypatch.setattr(
        stream_request, "register", lambda config, window_hints=None, **kw: declared.append(window_hints)
    )

    ticks = iter([True, True, False])
    monkeypatch.setattr(pl, "in_session", lambda _m: next(ticks))
    monkeypatch.setattr(pl.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fake_run_once(config, conn, **kw):
        # The first tick records three window misses -- exactly one escalation step's worth.
        calls["n"] += 1
        if calls["n"] == 1:
            _miss(conn, day, 3)
        return {}

    monkeypatch.setattr(pl, "run_once", fake_run_once)

    assert pl.main(["--config", str(cfg_path), "--db", db_path, "--interval", "15"]) == 0

    assert calls["n"] == 2
    # startup, then one declaration per tick; the tick after the misses asks for the wider window
    assert declared[0] == {"SPX": 45}
    assert declared[-1] == {"SPX": 75}
    conn = dbmod.connect(db_path)
    assert conn.execute("SELECT width FROM fly_stream_window WHERE symbol = 'SPX'").fetchone()["width"] == 75
