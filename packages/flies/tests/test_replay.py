"""flies.replay: a recorded session run through the paper loop's own tick (docs/quote-history-replay-plan.md).

The guards, each broken on purpose while this was written: a replayed tick touches nothing but its
scratch ledger (no heartbeat, no log file, no advice decision, which WRITES the live loop's file); a
model-driven arm is refused; the rebuilt snapshot uses no spot from after its own instant and no GEX
profile older than its bound.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from cherrypick.core.clock import ET
from test_paper_loop import at, cache_with_chain, config  # noqa: F401 -- the fixture is used by name

from cherrypick.flies import db as dbmod
from cherrypick.flies import paper_loop, provider, replay


# --------------------------------------------------------------------------- the replayed tick
def _forbid(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a replayed tick touched the live loop's files")

    for name in ("_beat", "_log", "advice_decision", "selector_session_model"):
        monkeypatch.setattr(paper_loop, name, boom)


def test_a_replayed_tick_touches_nothing_but_its_ledger(cache_with_chain, tmp_path, monkeypatch):  # noqa: F811
    conn = dbmod.connect(str(tmp_path / "scratch.db"))
    _forbid(monkeypatch)
    out = paper_loop.run_once(
        config(),
        conn,
        cache_path=str(cache_with_chain),
        when=at(12),
        snapshot_fn=provider.build_snapshot,
        replay=True,
    )
    assert out["ok"] and conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0] == 1
    # The same tick, not as a replay, does reach them: the guard above can fail.
    with pytest.raises(AssertionError):
        paper_loop.run_once(config(), conn, cache_path=str(cache_with_chain), when=at(12, 1))


def test_settling_a_replay_touches_nothing_but_its_ledger(cache_with_chain, tmp_path, monkeypatch):  # noqa: F811
    conn = dbmod.connect(str(tmp_path / "scratch.db"))
    _forbid(monkeypatch)
    paper_loop.run_once(
        config(),
        conn,
        cache_path=str(cache_with_chain),
        when=at(12),
        snapshot_fn=provider.build_snapshot,
        replay=True,
    )
    out = paper_loop.run_settle(config(), conn, cache_path="", when=at(16, 20), price=6000.0, replay=True)
    assert out["ok"] and out["settled"] == 1


def test_model_driven_and_unknown_arms_are_refused():
    cfg = {"arms": {"control": {}, "_note": "a comment", "selector": {"selector": True}}}
    with pytest.raises(ValueError, match="model-driven"):
        replay.replay_config(
            {**cfg, "arms": {**cfg["arms"], "control": {"trend_gate_source": "agent"}}}, ["control"]
        )
    with pytest.raises(ValueError, match="unknown arm"):
        replay.replay_config(cfg, ["no-such-arm"])
    out = replay.replay_config(cfg, ["control"])
    assert out["arms"]["control"]["enabled"] is True and out["arms"]["_note"] == "a comment"


def test_a_session_before_the_recording_or_over_an_existing_ledger_is_refused(tmp_path):
    assert replay.replay_session({}, "2026-10-09", tmp_path / "x.db")["ok"] is False


# --------------------------------------------------------------------------- the rebuilt snapshot
DAY = "2026-10-15"


def _ts(hour, minute, second=0):
    return datetime(2026, 10, 15, hour, minute, second, tzinfo=ET).timestamp()


@pytest.fixture()
def history(tmp_path):
    path = tmp_path / "gex_history.db"
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL, expiration TEXT);
        CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, mid REAL,
                                 age_s REAL, delta REAL);
        CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);
        CREATE TABLE gex_profile_history (symbol TEXT, trade_date TEXT, ts REAL, strike REAL, call_oi REAL,
                                          put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL,
                                          put_gamma REAL, call_iv REAL, put_iv REAL);
        CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL, recorded_at REAL, source TEXT);
        """
    )  # fmt: skip
    # Two samples: 11:00 and 11:20. Spot trail rises to 7720 at 11:10 -- AFTER the first sample.
    for when, spot in ((_ts(9, 30), 7690.0), (_ts(10, 0), 7680.0), (_ts(11, 0), 7700.0), (_ts(11, 10), 7720.0),
                       (_ts(11, 20), 7705.0)):  # fmt: skip
        c.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (DAY, when, spot))
    c.execute("INSERT INTO gex_spot_history VALUES ('SPX', '2026-10-14', ?, 7600)", (_ts(10, 0) - 86400,))
    c.execute("INSERT INTO daily_closes VALUES ('SPX', '2026-10-14', 7650.0, 0, 'x')")
    c.execute("INSERT INTO daily_closes VALUES ('SPX', '2026-10-15', 7999.0, 0, 'x')")  # today's: never used
    for sid, when in ((1, _ts(11, 0)), (2, _ts(11, 20))):
        c.execute("INSERT INTO gex_quote_samples VALUES (?, 'SPX', ?, ?, ?)", (sid, DAY, when, DAY))
        for strike in (7550.0, 7695.0, 7700.0, 7705.0, 7900.0):
            for side in (0, 1):
                age = 200.0 if strike == 7705.0 and side == 1 else 1.0  # one stale call quote
                c.execute(
                    "INSERT INTO gex_quotes VALUES (?, ?, ?, 1.0, 1.2, 1.1, ?, 0.5)", (sid, side, strike, age)
                )
    # One profile row at 10:58 (fresh for the 11:00 sample, stale by 11:20), 30 strikes with data.
    for k in range(30):
        c.execute(
            "INSERT INTO gex_profile_history VALUES ('SPX', ?, ?, ?, 100, 120, 0, 0, 0.002, 0.003, 0, 0)",
            (DAY, _ts(10, 58), 7600.0 + 5 * k),
        )
    c.commit()
    c.close()
    return path


def test_the_snapshot_keeps_paper_s_window_and_quote_age(history):
    s = replay.RecordedSession("SPX", DAY, history_db=history).snapshot(
        "", "SPX", when=datetime.fromtimestamp(_ts(11, 0), ET)
    )
    assert s["ok"] and s["underlying_price"] == 7700.0
    # 7550 and 7900 are outside 1.5% of spot (115 points); the 200 s-old 7705 call is past paper's 120 s.
    assert sorted(s["puts"]) == [7695.0, 7700.0, 7705.0]
    assert sorted(s["calls"]) == [7695.0, 7700.0]
    assert s["quote_stats"]["rejected"] == 1
    assert s["calls"][7700.0]["streamer_symbol"] == ".SPXW261015C7700"


def test_session_bounds_use_no_spot_from_after_the_instant(history):
    rec = replay.RecordedSession("SPX", DAY, history_db=history)
    first = rec.snapshot("", "SPX", when=datetime.fromtimestamp(_ts(11, 0), ET))["session"]
    assert (first["day_open"], first["day_high"], first["day_low"]) == (7690.0, 7700.0, 7680.0)
    assert first["prev_day_close"] == 7650.0  # strictly before the day: today's close is the answer
    later = rec.snapshot("", "SPX", when=datetime.fromtimestamp(_ts(11, 20), ET))["session"]
    assert later["day_high"] == 7720.0


def test_gex_comes_from_a_fresh_profile_or_is_refused(history):
    rec = replay.RecordedSession("SPX", DAY, history_db=history)
    fresh = rec.snapshot("", "SPX", when=datetime.fromtimestamp(_ts(11, 0), ET))
    assert fresh["gex"]["ok"] and fresh["gex_stats"]["profile_age_seconds"] == 120.0
    stale = rec.snapshot("", "SPX", when=datetime.fromtimestamp(_ts(11, 20), ET))
    assert stale["gex"]["ok"] is False and stale["gex_stats"]["refused"] == "profile_stale"


def test_an_unrecorded_instant_is_refused(history):
    rec = replay.RecordedSession("SPX", DAY, history_db=history)
    assert (
        rec.snapshot("", "SPX", when=datetime.fromtimestamp(_ts(11, 5), ET))["reason"] == "no_recorded_sample"
    )
