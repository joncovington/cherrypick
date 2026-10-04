"""The skew sampler: what the market charged at the points the shield replay can only model.

Telemetry, so the guards are about honesty rather than money: one sample a session, the right
strikes picked, a refusal recorded rather than a silent gap, and the year-long date kept on the
stream request after the held-long arms stop asking for it.
"""

import copy
import json
from datetime import date, datetime

from cherrypick.core import streamcache

from cherrypick.pmcc import db, paper_loop, skew, stream_request

TODAY = date(2026, 10, 5)  # Monday: the weekly short is 2026-10-16 (11 DTE)
WEEK, YEAR = "2026-10-16", "2027-09-17"
LISTING = ["2026-10-09", WEEK, "2026-10-23", "2027-06-17", "2027-06-30", YEAR, "2027-09-30", "2028-01-21"]


def _sampling(config):
    cfg = copy.deepcopy(config)
    cfg["skew_samples"] = {"enabled": True}
    return cfg


def _market(cache, symbol="TQQQ", spot=100.0, week=WEEK, year=YEAR, listing=LISTING):
    cache.spot(symbol, spot)
    streamcache.write_listed_expirations(cache.conn, symbol, listing)
    for strike, delta in ((95, 0.85), (98, 0.70), (100, 0.52), (102, 0.35)):
        cache.option(
            symbol, week, strike, bid=spot - strike + 1.0, ask=spot - strike + 1.2, delta=delta, iv=0.40
        )
    cache.option(symbol, year, 100, bid=14.0, ask=14.6, delta=0.62, iv=0.45)
    for strike, delta in ((70, -0.08), (80, -0.19), (85, -0.27), (90, -0.33)):
        cache.option(symbol, year, strike, right="P", bid=2.0, ask=2.4, delta=delta, iv=0.55)


def test_each_target_takes_the_strike_it_names(cache, config):
    _market(cache)
    dates = skew.dates(LISTING, TODAY)
    assert dates == {"week": WEEK, "year": YEAR}
    from cherrypick.pmcc import provider

    snap = provider.skew_snapshot(cache.path, "TQQQ", dates, root="TQQQ")
    rows = {r["target"]: r for r in skew.select(snap, TODAY)}
    picked = {t: (r["option_type"], r["strike"]) for t, r in rows.items()}
    assert picked == {
        "week_atm_call": ("call", 100),
        "week_call_70": ("call", 98),
        "year_atm_call": ("call", 100),
        "year_put_30": ("put", 85),  # 0.27 and 0.33 are equally near: the lower strike
        "year_put_20": ("put", 80),
        "year_put_10": ("put", 70),
    }
    assert rows["year_put_20"]["iv"] == 0.55 and rows["year_put_20"]["dte"] == 347
    assert all(r["usable"] == 1 for r in rows.values())


def test_one_sample_a_session_from_an_hour_before_the_close(cache, config, tmp_path):
    _market(cache)
    cfg = _sampling(config)
    conn = db.connect(str(tmp_path / "paper.db"))
    early = datetime(2026, 10, 5, 14, 59)
    assert paper_loop._sample_skew(cfg, conn, cache_path=cache.path, when=early, day="2026-10-05") == 0
    on_time = datetime(2026, 10, 5, 15, 0)
    assert paper_loop._sample_skew(cfg, conn, cache_path=cache.path, when=on_time, day="2026-10-05") == 6
    later = datetime(2026, 10, 5, 15, 30)
    assert paper_loop._sample_skew(cfg, conn, cache_path=cache.path, when=later, day="2026-10-05") == 0
    assert conn.execute("SELECT COUNT(*) FROM pmcc_skew_samples").fetchone()[0] == 6


def test_an_early_close_moves_the_sample_with_it(cache, config, tmp_path):
    """2026-11-27 closes at 13:00, so the sample is due from 12:00 -- not 15:00, two hours after the bell."""
    week, year = "2026-12-04", "2027-11-19"
    _market(cache, week=week, year=year, listing=["2026-11-27", week, "2026-12-11", year, "2028-01-21"])
    conn = db.connect(str(tmp_path / "paper.db"))
    cfg = _sampling(config)
    before = datetime(2026, 11, 27, 11, 59)
    assert paper_loop._sample_skew(cfg, conn, cache_path=cache.path, when=before, day="2026-11-27") == 0
    noon = datetime(2026, 11, 27, 12, 0)
    assert paper_loop._sample_skew(cfg, conn, cache_path=cache.path, when=noon, day="2026-11-27") == 6


def test_switched_off_it_samples_nothing(cache, config, tmp_path):
    _market(cache)
    conn = db.connect(str(tmp_path / "paper.db"))
    when = datetime(2026, 10, 5, 15, 30)
    assert paper_loop._sample_skew(config, conn, cache_path=cache.path, when=when, day="2026-10-05") == 0


def test_a_refused_snapshot_is_journaled_not_silent(cache, config, tmp_path):
    cache.spot("TQQQ", 100.0)  # no listing: no year-long date to sample
    conn = db.connect(str(tmp_path / "paper.db"))
    when = datetime(2026, 10, 5, 15, 30)
    assert (
        paper_loop._sample_skew(_sampling(config), conn, cache_path=cache.path, when=when, day="2026-10-05")
        == 0
    )
    reasons = [
        r["reason"] for r in conn.execute("SELECT reason FROM pmcc_decisions WHERE mode = 'skew_sample'")
    ]
    assert reasons == ["no_year_expiry_listed"]


def test_the_request_keeps_the_year_expiry_while_sampling(cache, config, tmp_path):
    """The fixture runs control alone, which never asks for a year-long date: only the sampler can."""
    _market(cache)
    db_path = str(tmp_path / "paper.db")
    conn = db.connect(db_path)
    for cfg, expected in ((config, False), (_sampling(config), True)):
        path = stream_request.write(cfg, conn, db_path, cache_path=cache.path, today=TODAY)
        dates = json.loads(path.read_text(encoding="utf-8"))["expirations"]["TQQQ"]
        assert (YEAR in dates) is expected
        assert WEEK in dates
