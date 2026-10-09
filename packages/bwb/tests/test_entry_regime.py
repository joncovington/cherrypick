"""The vol term structure recorded at entry (`entry_regime.py`, 2026-10-09): recording only, a stale or
missing print is no number plus its reason, and the read-side split never assigns an unmeasured row
to a side."""

from __future__ import annotations

from datetime import datetime

from cherrypick.core import streamcache
from test_live_loop import DAY, _plan

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import db, entry_regime

NOW = 1_791_000_000.0


def _cache(path, prints: dict[str, tuple[float, float]]) -> str:
    """`prints` maps symbol -> (last, updated_at)."""
    conn = streamcache.connect(str(path))
    for sym, (last, updated) in prints.items():
        conn.execute(
            "INSERT INTO stream_trades(symbol, last, updated_at) VALUES (?,?,?)", (sym, last, updated)
        )
    conn.commit()
    conn.close()
    return str(path)


def test_fresh_prints_are_recorded_raw_with_their_ages(tmp_path):
    cache = _cache(
        tmp_path / "c.db", {"VIX9D": (14.2, NOW - 20), "VIX": (16.0, NOW - 5), "VIX3M": (18.1, NOW - 60)}
    )
    got = entry_regime.measure(cache, max_age_seconds=300, now_ts=NOW)
    assert (got["entry_vix9d"], got["entry_vix"], got["entry_vix3m"]) == (14.2, 16.0, 18.1)
    assert (got["entry_vix9d_age"], got["entry_vix_age"], got["entry_vix3m_age"]) == (20.0, 5.0, 60.0)
    assert got["entry_regime_reason"] is None


def test_a_stale_or_missing_print_is_no_number_and_says_which(tmp_path):
    cache = _cache(tmp_path / "c.db", {"VIX9D": (14.2, NOW - 900), "VIX": (16.0, NOW - 5)})
    got = entry_regime.measure(cache, max_age_seconds=300, now_ts=NOW)
    assert got["entry_vix9d"] is None and got["entry_vix9d_age"] == 900.0  # frozen, never the last value
    assert got["entry_vix"] == 16.0 and got["entry_vix3m"] is None
    assert got["entry_regime_reason"] == "VIX9D:stale,VIX3M:missing"


def test_measuring_never_raises(tmp_path):
    got = entry_regime.measure(str(tmp_path / "missing" / "no.db"), now_ts=NOW)
    assert got["entry_vix9d"] is None and got["entry_regime_reason"].startswith("cache:")


def test_enter_position_stores_the_reading_and_nothing_else_it_was_handed(config):
    regime = {
        **{c: None for c in entry_regime.COLUMNS},
        "entry_vix9d": 14.2,
        "entry_vix": 16.0,
        "exit_reason": "x",
    }
    conn = db.connect()
    bookmod.enter_position(
        conn, _plan(), config, "control", entry_session=DAY, advice_params=None, regime=regime
    )
    row = conn.execute("SELECT * FROM bwb_positions").fetchone()
    assert row["entry_vix9d"] == 14.2 and row["entry_vix"] == 16.0
    assert row["exit_reason"] is None  # only the regime columns pass through


def test_a_paper_tick_reads_once_and_stores_it_on_every_arm(tmp_path, monkeypatch):
    from cherrypick.bwb import engine, paper_loop, provider

    calls = []

    def fake_measure(cache_path, *, max_age_seconds, now_ts=None):
        calls.append(max_age_seconds)
        return {**{c: None for c in entry_regime.COLUMNS}, "entry_vix9d": 13.0, "entry_vix": 15.0}

    snap = {
        "ok": True,
        "spot": 7700.0,
        "expiration": "2026-09-18",
        "quote_stats": {"fresh": 4, "rejected": 0},
    }
    monkeypatch.setattr(entry_regime, "measure", fake_measure)
    monkeypatch.setattr(provider, "build_entry_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(engine, "plan_entry", lambda snapshot, params: {"ok": True, "plan": _plan()})
    monkeypatch.setattr(
        paper_loop, "advice_decision", lambda config, day: {"day": day, "params": None, "experiments": []}
    )
    cache = str(tmp_path / "cache.db")
    streamcache.connect(cache).close()
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_root": "SPXW", "defaults": {"max_quote_age_seconds": 120}}
    paper_loop.run_once(config, conn, cache_path=cache, when=datetime(2026, 9, 16, 10, 5))
    rows = conn.execute("SELECT arm, entry_vix9d, entry_vix FROM bwb_positions").fetchall()
    assert len(rows) > 1 and calls == [120]
    assert all(r["entry_vix9d"] == 13.0 and r["entry_vix"] == 15.0 for r in rows)


def test_the_gate_is_open_below_one_shut_at_or_above_and_never_guessed():
    assert entry_regime.gate_state({"entry_vix9d": 14.0, "entry_vix": 16.0}) == "open"
    assert entry_regime.gate_state({"entry_vix9d": 16.0, "entry_vix": 16.0}) == "shut"
    assert entry_regime.gate_state({"entry_vix9d": None, "entry_vix": 16.0}) == "unmeasured"
    assert entry_regime.gate_state({}) == "unmeasured"  # a row from before 2026-10-09


def test_split_cuts_each_arms_closed_results_by_the_gate(config):
    conn = db.connect()
    readings = {"2026-10-12": (14.0, 16.0), "2026-10-13": (17.0, 16.0), "2026-10-14": (None, 16.0)}
    nets = {"2026-10-12": 80.0, "2026-10-13": -400.0, "2026-10-14": 60.0}
    for day, (v9, vix) in readings.items():
        bookmod.enter_position(
            conn,
            _plan(),
            config,
            "bounce",
            entry_session=day,
            advice_params=None,
            regime={"entry_vix9d": v9, "entry_vix": vix},
        )
        conn.execute(
            "UPDATE bwb_positions SET status = 'closed', gross_pnl = ?, fees = 0 WHERE entry_session = ?",
            (nets[day], day),
        )
    conn.execute(  # an open position is not a result
        "UPDATE bwb_positions SET status = 'open' WHERE entry_session = '2026-10-14'"
    )
    got = entry_regime.split(conn)["arms"]["bounce"]
    assert got["open"]["positions"] == 1 and got["open"]["net_pnl"] == 80.0
    assert got["shut"]["positions"] == 1 and got["shut"]["net_per_trade"] == -400.0
    assert "unmeasured" not in got


def test_the_stream_request_declares_the_three_indexes_as_bare_prints(monkeypatch, tmp_path):
    from cherrypick.bwb import stream_request

    seen = {}
    monkeypatch.setattr(
        stream_request._sr, "write_request", lambda module, symbols, **kw: seen.update(kw) or tmp_path
    )
    conn = db.connect(str(tmp_path / "paper.db"))
    stream_request.write({"symbol": "SPX"}, conn, str(tmp_path / "paper.db"), cache_path="x")
    assert seen["legs"] == ["VIX9D", "VIX", "VIX3M"]
