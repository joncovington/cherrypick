from datetime import date, datetime

import pytest
from conftest import black_chain

from cherrypick.weeklyput import clock, db, engine, paper_loop, stream_request

ENTRY, EXPIRY = date(2026, 11, 6), date(2026, 11, 13)
YEARS = clock.years_to(ENTRY, EXPIRY)
EXP = EXPIRY.isoformat()


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=clock.ET)


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "ledger.db"))


@pytest.fixture
def stocked(cache):
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", EXP, "SPXW", black_chain(6800.0, 0.16, YEARS, range(6000, 7001, 5)))
    cache.chain("SPY", EXP, "SPY", black_chain(680.0, 0.16, YEARS, range(600, 701)))
    return cache


def tick(config, conn, cache, when):
    return paper_loop.run_once(config, conn, cache_path=cache.path, when=when)


def test_sells_one_ten_delta_put_per_name_in_the_window(config, conn, stocked):
    out = tick(config, conn, stocked, at(ENTRY, 15, 46))
    assert out["phase"] == "entry" and out["cycle"] == EXP
    assert out["entries"] == {"unhedged:SPX": "entered", "unhedged:SPY": "entered"}
    rows = db.open_positions(conn)
    assert {r["symbol"] for r in rows} == {"SPX", "SPY"}
    for r in rows:
        assert abs(r["entry_delta"]) == pytest.approx(0.10, abs=0.01)
        assert r["entry_value"] > 0  # a credit is +
        assert r["settlement_style"] == ("cash" if r["symbol"] == "SPX" else "physical")
    # A second tick in the window does not sell again.
    again = tick(config, conn, stocked, at(ENTRY, 15, 47))
    assert again["entries"] == {}
    assert db.open_position_count(conn) == 2


def test_nothing_before_the_window_and_nothing_on_other_days(config, conn, stocked):
    assert tick(config, conn, stocked, at(ENTRY, 15, 30))["phase"] == "before_window"
    assert tick(config, conn, stocked, at(date(2026, 11, 10), 15, 46))["cycle"] is None
    assert db.open_position_count(conn) == 0


def test_nothing_fills_after_the_window(config, conn, cache):
    """The guard: a name the window could not enter is missed, and a later tick never sells it,
    even once its quotes are good. (Break `record_misses`/`_miss` to sell instead and this fails.)"""
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    tick(config, conn, cache, at(ENTRY, 15, 46))  # no chain yet: refused, pending
    assert {e["outcome"] for e in db.entries(conn)} == {"pending"}
    cache.chain("SPX", EXP, "SPXW", black_chain(6800.0, 0.16, YEARS, range(6000, 7001, 5)))
    cache.chain("SPY", EXP, "SPY", black_chain(680.0, 0.16, YEARS, range(600, 701)))
    out = tick(config, conn, cache, at(ENTRY, 15, 56))
    assert out["phase"] == "after_window"
    assert set(out["entries"].values()) == {"missed"}
    assert db.open_position_count(conn) == 0
    assert {e["refusal"] for e in db.entries(conn)} == {"no_chain"}


def test_the_last_window_tick_records_a_miss(config, conn, cache):
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    out = tick(config, conn, cache, at(ENTRY, 15, 55))
    assert set(out["entries"].values()) == {"missed"}


def test_unknown_settlement_is_refused(config, conn, stocked):
    config["names"]["SPY"]["settlement"] = ""
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    assert db.entry(conn, "unhedged", "SPY", EXP)["refusal"] == "unknown_settlement"
    assert db.entry(conn, "unhedged", "SPX", EXP)["outcome"] == "entered"


def test_settles_on_the_posted_close_and_waits_without_one(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    spx = next(p for p in db.open_positions(conn) if p["symbol"] == "SPX")
    # Expiry evening: no close posted yet (and a last trade is not a close): waits.
    stocked.last("SPX", 6000.0)
    tick(config, conn, stocked, at(EXPIRY, 16, 30))
    assert db.position(conn, spx["position_id"])["status"] == "open"
    # Monday: the expiry's close arrives as the next session's prior close.
    stocked.summary("SPX", "2026-11-16", prev_day_close=6900.0)
    tick(config, conn, stocked, at(date(2026, 11, 16), 9, 31))
    done = db.position(conn, spx["position_id"])
    assert done["status"] == "closed" and done["exit_reason"] == "expired"
    assert done["settle_price"] == 6900.0 and done["settle_source"] == "stream_summary"
    assert done["net_pnl"] == pytest.approx(done["entry_value"] - done["fees"] - done["slippage"], abs=1e-3)
    # SPY's close never came: still open, journaled.
    assert db.position(conn, f"unhedged:SPY:{EXP}")["status"] == "open"


def test_an_in_the_money_etf_put_is_assigned(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    spy = db.position(conn, f"unhedged:SPY:{EXP}")
    stocked.summary("SPY", "2026-11-16", prev_day_close=spy["strike"] - 10)
    tick(config, conn, stocked, at(date(2026, 11, 16), 9, 31))
    done = db.position(conn, spy["position_id"])
    assert done["exit_reason"] == "assigned"
    assert done["exit_value"] == pytest.approx(-1000.0)
    assert done["settlement"] > 0


def test_marks_and_flags_assignment_exposure(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    spy = db.position(conn, f"unhedged:SPY:{EXP}")
    k = spy["strike"]
    stocked.last("SPY", k - 5)
    stocked.quote(spy["streamer_symbol"], 4.98, 5.02)  # all intrinsic
    tick(config, conn, stocked, at(date(2026, 11, 12), 11, 0))
    pos = db.position(conn, spy["position_id"])
    assert pos["assignment_exposed_at"] is not None
    assert pos["worst_mark_value"] == pytest.approx(-500.0)
    marks = db.marks(conn, spy["position_id"])
    assert marks[-1]["usable"] == 1 and marks[-1]["extrinsic"] == pytest.approx(0.0, abs=1e-9)


def test_manual_settle_at_the_official_print(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    out = paper_loop.run_settle(conn, day=EXP, symbol="SPX", price=6850.0)
    assert len(out["settled"]) == 1
    assert db.position(conn, f"unhedged:SPX:{EXP}")["settle_source"] == "official"


def test_stream_request_is_narrow(config, conn, stocked):
    # A week before the entry session: nothing but the names.
    quiet = stream_request.wanted(conn, config, stocked.path, date(2026, 11, 2))
    assert quiet == {"expirations": {}, "legs": []}
    # The session before entry: the expiry, no band legs yet.
    eve = stream_request.wanted(conn, config, stocked.path, date(2026, 11, 5))
    assert eve["expirations"] == {"SPX": [EXP], "SPY": [EXP]} and eve["legs"] == []
    # Entry session: the puts 1.5% to 12% under spot, puts only.
    day = stream_request.wanted(conn, config, stocked.path, ENTRY)
    tag = EXP[2:].replace("-", "") + "P"
    assert day["legs"] and all(tag in leg for leg in day["legs"])
    strikes = [float(leg.split("P")[-1]) for leg in day["legs"] if leg.startswith(".SPXW")]
    assert min(strikes) >= 6800 * 0.88 and max(strikes) <= 6800 * 0.985


def test_cycles_measure_the_equal_weight_mean(config, conn, stocked):
    from cherrypick.weeklyput import analytics

    tick(config, conn, stocked, at(ENTRY, 15, 46))
    stocked.summary("SPX", "2026-11-16", prev_day_close=6900.0)
    stocked.summary("SPY", "2026-11-16", prev_day_close=690.0)
    tick(config, conn, stocked, at(date(2026, 11, 16), 9, 31))
    (cyc,) = analytics.cycles(conn)
    assert cyc["finished"]
    nets = [n["net_bp"] for n in cyc["names"].values()]
    assert cyc["cycle_net_bp"] == pytest.approx(sum(nets) / 2, abs=1e-4)
    s = analytics.summary(conn)["arms"]["unhedged"]
    assert s["cycles"] == 1 and s["money"]["net_pnl"] == pytest.approx(cyc["net_pnl"], abs=0.01)


def test_run_status_is_files_only(config, conn, stocked):
    st = paper_loop.run_status(config, conn, cache_path=stocked.path)
    assert st["ok"] and st["arms"] == ["unhedged"] and st["names"] == ["SPX", "SPY"]


def test_engine_styles_cover_the_config():
    assert set(paper_loop.STYLES) == {engine.CASH, engine.PHYSICAL}
