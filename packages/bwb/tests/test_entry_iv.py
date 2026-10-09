"""The expiration's implied variance recorded at entry (`entry_iv.py`, 2026-10-09): recording only,
honest about a cut-off strip, never a guess when it cannot read one."""

from __future__ import annotations

import json
import time

import pytest
from cherrypick.core import impliedvar as iv
from cherrypick.core import streamcache
from test_live_loop import DAY, _plan

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import db, entry_iv

EXP = "2026-10-16"
EXPIRES = "2026-10-16T20:00:00Z"
NOW = 1_791_000_000.0  # 2026-10-03, about 13 days before EXPIRES


def _strip(dry: bool) -> list[dict]:
    """Calls and puts around 100. With `dry`, each wing ends in a zero bid, so the strip is complete."""
    out = []
    for k in range(70, 135, 5):
        call = max(0.0, (100 - k) + 6 - 0.15 * abs(k - 100)) if k < 100 else max(0.0, 6 - 0.3 * (k - 100))
        put = max(0.0, (k - 100) + 6 - 0.15 * abs(k - 100)) if k > 100 else max(0.0, 6 - 0.3 * (100 - k))
        for kind, mid in (("call", call), ("put", put)):
            bid = round(max(0.0, mid - 0.05), 2)
            if not dry and bid == 0:
                bid = 0.05  # a wing that never runs dry: the cache's window cut it
            out.append({"strike": float(k), "option_type": kind, "bid": bid, "ask": round(mid + 0.05, 2)})
    return out


def _cache(path, quotes, *, expires=EXPIRES):
    conn = streamcache.connect(path)
    for i, q in enumerate(quotes):
        sym = f".SPXW{EXP}{q['option_type'][0]}{q['strike']}"
        occ = f"{'SPXW':<6}{EXP[2:].replace('-', '')}{q['option_type'][0].upper()}{i:08d}"
        opt = {
            "streamer_symbol": sym,
            "symbol": occ,
            "strike_price": q["strike"],
            "option_type": q["option_type"][0].upper(),
            **({"expires_at": expires} if expires else {}),
            "exercise_style": "European",
            "settlement_type": "PM",
        }
        conn.execute(
            "INSERT INTO stream_chain(streamer_symbol, expiration, underlying_symbol, data_json, updated_at)"
            " VALUES (?,?,?,?,?)",
            (sym, EXP, "SPX", json.dumps(opt), NOW),
        )
        conn.execute(
            "INSERT INTO stream_quotes(symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
            (sym, q["bid"], q["ask"], (q["bid"] + q["ask"]) / 2, NOW),
        )
    conn.commit()
    conn.close()
    return str(path)


def test_a_complete_strip_is_measured_as_the_core_arithmetic_says(tmp_path):
    quotes = _strip(dry=True)
    got = entry_iv.measure(_cache(tmp_path / "c.db", quotes), "SPX", "SPXW", EXP, rate=0.04, now_ts=NOW)
    years = iv.years_between(NOW, 1_792_180_800.0)  # EXPIRES
    want = iv.single_term(quotes, years=years, rate=0.04)
    assert want["ok"] and want["complete"]
    assert got["entry_iv_vol"] == pytest.approx(want["vol"], abs=1e-3)
    assert got["entry_iv_complete"] == 1 and got["entry_iv_reason"] is None
    assert (
        got["entry_iv_quotes"] == len(quotes)
        and got["entry_iv_missing"] == 0
        and got["entry_iv_rate"] == 0.04
    )


def test_a_cut_off_strip_is_recorded_as_a_lower_bound(tmp_path):
    got = entry_iv.measure(_cache(tmp_path / "c.db", _strip(dry=False)), "SPX", "SPXW", EXP, now_ts=NOW)
    assert got["entry_iv_vol"] is not None and got["entry_iv_complete"] == 0


def test_a_reading_that_cannot_be_taken_stores_why_and_no_number(tmp_path):
    no_expiry = entry_iv.measure(
        _cache(tmp_path / "a.db", _strip(dry=True), expires=None), "SPX", "SPXW", EXP, now_ts=NOW
    )
    assert no_expiry["entry_iv_vol"] is None and no_expiry["entry_iv_reason"] == "no_expiry_in_cache"
    stale = entry_iv.measure(
        _cache(tmp_path / "b.db", _strip(dry=True)),
        "SPX",
        "SPXW",
        EXP,
        now_ts=NOW + 3600,
        max_age_seconds=300,
    )
    assert stale["entry_iv_vol"] is None and stale["entry_iv_reason"] == "no_parity_strike"
    assert stale["entry_iv_missing"] == len(_strip(dry=True))


def test_enter_position_stores_the_reading_and_nothing_else_it_was_handed(config):
    implied = {
        **{c: None for c in entry_iv.COLUMNS},
        "entry_iv_vol": 18.5,
        "entry_iv_complete": 0,
        "exit_reason": "x",
    }
    conn = db.connect()
    bookmod.enter_position(
        conn, _plan(), config, "control", entry_session=DAY, advice_params=None, implied=implied
    )
    row = conn.execute("SELECT * FROM bwb_positions").fetchone()
    assert row["entry_iv_vol"] == 18.5 and row["entry_iv_complete"] == 0
    assert row["exit_reason"] is None  # only entry_iv_* columns pass through


def test_measuring_never_raises(tmp_path):
    got = entry_iv.measure(str(tmp_path / "missing" / "no.db"), "SPX", "SPXW", EXP, now_ts=time.time())
    assert got["entry_iv_vol"] is None and got["entry_iv_reason"]


def test_a_paper_tick_reads_each_expiration_once_and_stores_it_on_every_arm(tmp_path, monkeypatch):
    from datetime import datetime

    from cherrypick.bwb import engine, paper_loop, provider

    calls = []

    def fake_measure(cache_path, symbol, root, expiration, *, rate, max_age_seconds, now_ts=None):
        calls.append((symbol, root, expiration, rate))
        return {**{c: None for c in entry_iv.COLUMNS}, "entry_iv_vol": 17.25, "entry_iv_rate": rate}

    snap = {
        "ok": True,
        "spot": 7700.0,
        "expiration": "2026-09-18",
        "quote_stats": {"fresh": 4, "rejected": 0},
    }
    monkeypatch.setattr(entry_iv, "measure", fake_measure)
    monkeypatch.setattr(provider, "build_entry_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(engine, "plan_entry", lambda snapshot, params: {"ok": True, "plan": _plan()})
    monkeypatch.setattr(
        paper_loop, "advice_decision", lambda config, day: {"day": day, "params": None, "experiments": []}
    )
    cache = str(tmp_path / "cache.db")
    streamcache.connect(cache).close()
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_root": "SPXW", "defaults": {"risk_free_rate": 0.05}}
    paper_loop.run_once(config, conn, cache_path=cache, when=datetime(2026, 9, 16, 10, 5))
    rows = conn.execute("SELECT arm, entry_iv_vol, entry_iv_rate FROM bwb_positions").fetchall()
    assert len(rows) > 1 and calls == [("SPX", "SPXW", "2026-09-18", 0.05)]
    assert all(r["entry_iv_vol"] == 17.25 and r["entry_iv_rate"] == 0.05 for r in rows)
