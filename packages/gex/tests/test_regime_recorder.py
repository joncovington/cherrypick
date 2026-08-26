"""The market-regime sampler's guards, each verified by breaking the invariant on purpose during
development and watching the test report the right thing (the suite rule: a guard has to be shown
to fail).

Covers: the frozen-quote refusal (a stale print becomes a refusal row, never a value), the
missing-quote refusal, the RTH gate, the DB-side throttle, the daily-close harvest, the
declaration-coverage guard (driven off ``regime.READINGS`` itself, so a new reading without its
subscription fails here the moment it is declared), and the dropped-readings stale-checkout guard.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from cherrypick.core import streamcache
from cherrypick.core.clock import ET

from cherrypick.gex import regime, stream_request

# A Tuesday, mid-session, not a holiday.
RTH_NOW = datetime(2026, 8, 18, 13, 0, 0, tzinfo=ET)


@pytest.fixture()
def cfg(tmp_path):
    return {
        "symbols": ["SPX"],
        "stream_cache_db": tmp_path / "stream_cache.db",
        "history_db_path": tmp_path / "gex_history.db",
    }


def seed_cache(cfg, quotes: dict[str, tuple[float, float]], summary_rows=()):
    """Create a real-DDL stream cache holding `quotes` = {symbol: (last, updated_at)}."""
    conn = streamcache.connect(cfg["stream_cache_db"])
    try:
        for sym, (last, updated) in quotes.items():
            conn.execute(
                "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)",
                (sym, last, updated),
            )
        for sym, trade_date, day_close in summary_rows:
            conn.execute(
                "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_close, updated_at) "
                "VALUES (?,?,?,?)",
                (sym, trade_date, day_close, RTH_NOW.timestamp()),
            )
        conn.commit()
    finally:
        conn.close()


def history_rows(cfg, sql, params=()):
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def all_fresh_quotes(now_ts):
    return {sym: (100.0 + i, now_ts - 5) for i, sym in enumerate(sorted(set(regime.READINGS.values())))}


def test_fresh_quotes_write_usable_rows(cfg):
    now_ts = RTH_NOW.timestamp()
    seed_cache(cfg, all_fresh_quotes(now_ts))
    out = regime.sample(cfg, now=RTH_NOW)
    assert out["status"] == "sampled"
    assert out["written"] == len(regime.READINGS)
    assert out["usable"] == len(regime.READINGS)
    rows = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vix'")
    assert len(rows) == 1
    assert rows[0]["usable"] == 1
    assert rows[0]["value"] is not None
    assert rows[0]["basis_ts"] == pytest.approx(now_ts - 5)
    assert rows[0]["symbol"] == "VIX"


def test_frozen_quote_is_refused_not_recorded(cfg):
    """THE frozen-quote guard: a print older than the age gate lands as usable=0 with the reason
    and NO value — never the stale number (the flat-line failure the spot trail already fixed)."""
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    quotes["VIX"] = (55.5, now_ts - regime.MAX_QUOTE_AGE_SECONDS - 60)  # frozen
    seed_cache(cfg, quotes)
    out = regime.sample(cfg, now=RTH_NOW)
    assert out["usable"] == len(regime.READINGS) - 1
    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vix'")[0]
    assert row["usable"] == 0
    assert row["reason"] == "stale_quote"
    assert row["value"] is None
    assert row["basis_ts"] is not None  # the evidence of HOW stale is kept


def test_missing_quote_is_refused(cfg):
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    del quotes["VVIX"]
    seed_cache(cfg, quotes)
    regime.sample(cfg, now=RTH_NOW)
    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vvix'")[0]
    assert row["usable"] == 0
    assert row["reason"] == "no_quote"


def test_outside_rth_writes_nothing(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    evening = RTH_NOW.replace(hour=19)
    assert regime.sample(cfg, now=evening) == {"status": "closed", "written": 0, "usable": 0}
    saturday = datetime(2026, 8, 22, 13, 0, 0, tzinfo=ET)
    assert regime.sample(cfg, now=saturday)["status"] == "closed"
    # And no table rows at all — outside-RTH silence is the legible gap, not a refusal row.
    conn = sqlite3.connect(cfg["history_db_path"]) if cfg["history_db_path"].exists() else None
    if conn is not None:
        try:
            regime.ensure_tables(conn)
            assert conn.execute("SELECT COUNT(*) FROM market_regime_history").fetchone()[0] == 0
        finally:
            conn.close()


def test_throttled_within_interval(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    assert regime.sample(cfg, now=RTH_NOW)["status"] == "sampled"
    soon = RTH_NOW.replace(second=30)
    assert regime.sample(cfg, now=soon)["status"] == "throttled"
    minute_later = RTH_NOW.replace(minute=1)
    assert regime.sample(cfg, now=minute_later)["status"] == "sampled"


def test_daily_closes_harvest_is_append_only(cfg):
    now_ts = RTH_NOW.timestamp()
    seed_cache(
        cfg,
        all_fresh_quotes(now_ts),
        summary_rows=[("XLK", "2026-08-17", 231.5), ("SPY", "2026-08-17", 645.2)],
    )
    regime.sample(cfg, now=RTH_NOW)
    rows = history_rows(cfg, "SELECT * FROM daily_closes ORDER BY symbol")
    assert [(r["symbol"], r["trade_date"], r["close"]) for r in rows] == [
        ("SPY", "2026-08-17", 645.2),
        ("XLK", "2026-08-17", 231.5),
    ]
    # A second harvest (next sample) must not duplicate or rewrite.
    regime.sample(cfg, now=RTH_NOW.replace(minute=2))
    assert len(history_rows(cfg, "SELECT * FROM daily_closes")) == 2


def test_every_reading_symbol_is_declared_to_the_streamer(cfg, managed_home):
    """Declaration coverage, driven off READINGS itself: a reading added without a subscription
    fails here the moment it is declared, with no hand-kept list to forget."""
    path = stream_request.write(cfg["symbols"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = set(payload["symbols"]) | set(payload["legs"])
    for reading, symbol in regime.READINGS.items():
        assert symbol in declared, f"reading '{reading}' ({symbol}) has no stream subscription"
    # And the legs carry a bounded history request so daily_closes backfills on day one.
    for leg in payload["legs"]:
        assert payload["history_days"].get(leg) == 270


def test_dropped_readings_flags_a_stale_checkout(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    regime.sample(cfg, now=RTH_NOW)
    conn = sqlite3.connect(cfg["history_db_path"])
    try:
        # A newer checkout recorded a reading this code does not declare, yesterday.
        conn.execute(
            "INSERT INTO market_regime_history "
            "(trade_date, ts, reading, symbol, value, basis_ts, usable, reason) "
            # A deliberately fictional reading: this fixture stands for "a newer checkout recorded
            # something this code does not declare", so it must never collide with a real reading.
            # It used to be 'skew', which stopped working the day SKEW was admitted (2026-08-24) —
            # the guard correctly caught its own fixture.
            "VALUES ('2026-08-17', ?, 'not_a_real_reading', 'ZZZZ', 141.0, ?, 1, NULL)",
            (RTH_NOW.timestamp() - 86400, RTH_NOW.timestamp() - 86400),
        )
        conn.commit()
        assert regime.dropped_readings(conn, today="2026-08-18") == {"not_a_real_reading"}
        # Nothing recorded before today -> nothing to flag.
        conn.execute("DELETE FROM market_regime_history WHERE trade_date < '2026-08-18'")
        conn.commit()
        assert regime.dropped_readings(conn, today="2026-08-18") == set()
    finally:
        conn.close()


# --- futures readings: resolved, never assembled -------------------------------------------


def _write_map(home, refreshed, vx=("/VXU26:XCBF", "/VXV26:XCBF"), zn=("/ZNZ26:XCBT",)):
    p = home / "state" / "futures_contracts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "refreshed_at": refreshed,
                "contracts": {
                    "VX": [{"streamer_symbol": s, "expiration": "2026-09-16"} for s in vx],
                    "ZN": [{"streamer_symbol": s, "expiration": "2026-12-21"} for s in zn],
                },
            }
        ),
        encoding="utf-8",
    )
    return p


def test_futures_symbols_resolve_from_the_map(managed_home):
    _write_map(managed_home, RTH_NOW.isoformat())
    assert regime.futures_symbols(RTH_NOW) == {
        "vx1": "/VXU26:XCBF",
        "vx2": "/VXV26:XCBF",
        "zn1": "/ZNZ26:XCBT",
    }


def test_a_stale_map_yields_no_futures_readings(managed_home):
    """Futures roll. A stale map names a contract that has expired or gone illiquid, so it is
    refused outright: dropping the readings leaves a legible gap, while sampling a rolled-off
    contract leaves a plausible-looking series that is quietly wrong."""
    old = RTH_NOW.replace(day=1, month=7)
    _write_map(managed_home, old.isoformat())
    assert regime.futures_symbols(RTH_NOW) == {}


def test_a_missing_or_unreadable_map_is_empty_never_an_error(managed_home):
    assert regime.futures_symbols(RTH_NOW) == {}  # absent
    p = managed_home / "state" / "futures_contracts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert regime.futures_symbols(RTH_NOW) == {}


def test_futures_are_sampled_with_the_contract_on_the_row(cfg, managed_home):
    """The reading name is stable across a roll; the row's SYMBOL carries the contract actually
    sampled, so a roll is visible in the data and no row is ever a blended constant-maturity."""
    _write_map(managed_home, RTH_NOW.isoformat())
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    quotes["/VXU26:XCBF"] = (17.55, now_ts - 5)
    quotes["/VXV26:XCBF"] = (19.20, now_ts - 5)
    quotes["/ZNZ26:XCBT"] = (108.20, now_ts - 5)
    seed_cache(cfg, quotes)

    regime.sample(cfg, now=RTH_NOW)
    rows = history_rows(
        cfg, "SELECT reading, symbol, value, usable FROM market_regime_history WHERE reading LIKE 'vx%' OR reading = 'zn1'"
    )
    got = {r["reading"]: (r["symbol"], r["value"], r["usable"]) for r in rows}
    assert got["vx1"] == ("/VXU26:XCBF", 17.55, 1)
    assert got["vx2"] == ("/VXV26:XCBF", 19.20, 1)
    assert got["zn1"] == ("/ZNZ26:XCBT", 108.20, 1)


def test_futures_legs_are_declared_with_their_exchange_suffix_intact(cfg, managed_home):
    """`/VXU26:XCBF` is what the instruments endpoint returned and what DXLink expects — the probe
    that guessed `:XCFE` saw nothing and would have read as 'not entitled'. The declaration must
    not upper-case or otherwise clean these."""
    # `regime_legs` has no injectable clock — it declares against real now — so the map must be
    # freshly stamped here rather than at the fixture's frozen RTH_NOW.
    _write_map(managed_home, datetime.now(ET).isoformat())
    legs = stream_request.regime_legs(cfg["symbols"])
    assert "/VXU26:XCBF" in legs and "/ZNZ26:XCBT" in legs


# --- Tier 2: chain math ---------------------------------------------------------------------


class _Snap:
    """The subset of GexSnapshot chain_readings reads."""

    def __init__(self, spot, entries, greeks, oi):
        self.spot, self.chain_entries, self.greeks, self.oi = spot, entries, greeks, oi
        self.source, self.expiration = "stream_cache", "2026-09-18"


def _chain(strikes, *, iv=0.20, delta_by_strike=None, oi_by_strike=None, price=1.0):
    entries, greeks, oi = [], {}, {}
    for k in strikes:
        for side in ("C", "P"):
            sym = f".X{k}{side}"
            entries.append({"streamer_symbol": sym, "strike_price": float(k), "option_type": side})
            greeks[sym] = {
                "iv": iv,
                "price": price,
                "delta": (delta_by_strike or {}).get((k, side)),
            }
            oi[sym] = (oi_by_strike or {}).get((k, side), 10)
    return entries, greeks, oi


def test_chain_readings_compute_the_measures_they_can():
    entries, greeks, oi = _chain([95, 100, 105], iv=0.25, price=2.0)
    out = regime.chain_readings(_Snap(100.0, entries, greeks, oi))
    assert out["atm_iv"] == pytest.approx(0.25)
    # Expected move goes through the suite's ONE straddle formula, so it cannot disagree with the
    # modules that trade on it.
    assert out["expected_move"] == pytest.approx(0.85 * (2.0 + 2.0))
    assert out["put_call_oi_ratio"] == pytest.approx(1.0)


def test_risk_reversal_needs_a_strike_actually_near_25_delta():
    """An expiring chain has no 25-delta strike, and inventing one from the closest available
    would report a number for a thing that does not exist. Measured 2026-08-24: SPX's nearest
    expiration was 0DTE and this was correctly absent."""
    entries, greeks, oi = _chain(
        [95, 105], delta_by_strike={(95, "P"): -0.99, (105, "C"): 0.01}
    )
    assert "risk_reversal_25d" not in regime.chain_readings(_Snap(100.0, entries, greeks, oi))

    entries, greeks, oi = _chain(
        [95, 105], delta_by_strike={(95, "P"): -0.26, (105, "C"): 0.24}, iv=0.30
    )
    got = regime.chain_readings(_Snap(100.0, entries, greeks, oi))
    assert got["risk_reversal_25d"] == pytest.approx(0.0)  # equal IVs -> no skew


def test_gamma_concentration_is_windowed_near_spot_not_whole_chain():
    """flies measured its whole-chain version degenerate 60/60: one strike's share of a
    109-strike surface is always small. Pinning is a property of a cluster near the money."""
    # UNIFORM open interest across a wide surface is the discriminating case, and the one flies
    # actually hit: windowed, the top 3 of 10 near-spot strikes is ~0.3; measured over all 199 it
    # is ~0.015 and every session reads "thin" forever.
    wide = list(range(1, 200))
    entries, greeks, oi = _chain(wide, oi_by_strike={(k, s): 10 for k in wide for s in ("C", "P")})
    out = regime.chain_readings(_Snap(100.0, entries, greeks, oi))
    assert out["gamma_concentration"] == pytest.approx(3 / 10)


def test_chain_readings_refuse_without_spot_or_chain():
    assert regime.chain_readings(_Snap(None, *_chain([100]))) == {}
    assert regime.chain_readings(_Snap(100.0, [], {}, {})) == {}


def test_a_declared_intermittent_reading_is_refused_with_its_own_reason(cfg):
    """SKEW's feed prints in bursts and is silent between: 30 usable samples of 1,105 over
    2026-08-24..26, against VIX's 363 prints at a 60-second median over the same window.

    It is still REFUSED — a burst-feed quote is as stale as any other and the value must not be
    recorded — but the reason distinguishes an expected silence from a feed that broke today. That
    distinction is the whole point: a 97% refusal rate with no declaration reads as a recorder fault
    and gets re-investigated from scratch, which is exactly what happened."""
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    quotes["SKEW"] = (143.27, now_ts - regime.MAX_QUOTE_AGE_SECONDS - 60)
    seed_cache(cfg, quotes)

    out = regime.sample(cfg, now=RTH_NOW)

    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'skew'")[0]
    assert row["usable"] == 0, "a burst-feed quote is still refused"
    assert row["value"] is None, "the stale number must never be recorded"
    assert row["basis_ts"] is not None, "the evidence of HOW stale is kept"
    assert row["reason"] == "intermittent_feed"
    assert out["expected_unusable"] == 1


def test_an_undeclared_reading_stale_by_the_same_amount_is_not_excused(cfg):
    """The declaration has to be per-reading, not a general softening of the age gate. VIX going
    quiet for the same interval is a real fault and must keep saying so."""
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    quotes["VIX"] = (15.29, now_ts - regime.MAX_QUOTE_AGE_SECONDS - 60)
    seed_cache(cfg, quotes)

    out = regime.sample(cfg, now=RTH_NOW)

    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vix'")[0]
    assert row["reason"] == "stale_quote"
    assert out["expected_unusable"] == 0


def test_an_intermittent_reading_that_does_print_is_recorded_normally(cfg):
    """Declared intermittent is not declared dead. When SKEW prints inside the age gate the value
    lands like any other — which is also why it stays in READINGS rather than being retired: if the
    feed ever sustains, the series fills with no code change."""
    now_ts = RTH_NOW.timestamp()
    seed_cache(cfg, all_fresh_quotes(now_ts))

    out = regime.sample(cfg, now=RTH_NOW)

    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'skew'")[0]
    assert row["usable"] == 1 and row["value"] is not None
    assert out["expected_unusable"] == 0


def test_the_intermittent_set_only_names_readings_that_exist():
    """A declaration naming a reading that was renamed or removed is a comment pretending to be a
    guard — it would silently stop applying."""
    assert regime.INTERMITTENT_INTRADAY <= set(regime.READINGS)
