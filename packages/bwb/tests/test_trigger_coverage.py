"""The trigger-tick substrate's own integrity reading.

Why this file exists: `trigger_coverage()` reported `refusal_share = 1.00` for four consecutive
sessions (2026-08-24..27) and nobody read it. Two unrelated defects had starved the module's second
product from its first session — `greeks_for` never selected gamma, so the flip read failed; and the
tick path never set `position_symbol`, so spot was NULL — and a single ANDed `measured` flag could
only ever show that as one undifferentiated wall of refusals.

So the coverage reading now has to do two things a bare share could not: separate the halves, and
CALL a session of nothing-measured a defect rather than leaving it as a number to notice.
"""

from cherrypick.bwb import analytics, db


def _tick(conn, session, *, spot_ok, flip_ok, refusal=None):
    db.record_trigger_tick(
        conn,
        {
            "entry_session": session,
            "structure_signature": "sig",
            "symbol": "SPX",
            "ticked_at": 1.0,
            "session_date": session,
            "near_abs_delta": 0.3,
            "peak_abs_delta": 0.3,
            "spot": 7700.0 if spot_ok else None,
            "gamma_flip": 7750.0 if flip_ok else None,
            "gamma_flip_basis": "live_stream_cache" if flip_ok else None,
            "below_flip_seen": 0,
            "addon_short_bid": None,
            "addon_short_ask": None,
            "addon_long_bid": None,
            "addon_long_ask": None,
            "measured": 1 if (spot_ok and flip_ok) else 0,
            "spot_measured": 1 if spot_ok else 0,
            "flip_measured": 1 if flip_ok else 0,
            "refusal": refusal,
        },
    )


def test_the_two_halves_of_measured_are_recorded_separately(tmp_path):
    conn = db.connect(str(tmp_path / "p.db"))
    _tick(conn, "2026-08-28", spot_ok=True, flip_ok=False, refusal="flip: insufficient_gex_data")
    _tick(conn, "2026-08-28", spot_ok=False, flip_ok=True, refusal="spot: no_spot_price")
    _tick(conn, "2026-08-28", spot_ok=True, flip_ok=True)
    conn.commit()

    cov = analytics.trigger_coverage(conn, "2026-08-28")
    assert cov["ticks"] == 3 and cov["refused"] == 2
    # The distinction one flag could not draw: which input failed, and how often.
    assert cov["no_flip"] == 1
    assert cov["no_spot"] == 1
    assert cov["total_failure"] is False


def test_a_session_that_measured_nothing_is_flagged_as_a_defect(tmp_path):
    """The 2026-08-24..27 shape: ticks recorded, cohorts open, every read failed. That is a bug,
    not thin data, and the reading has to say so without a human noticing a share equals 1."""
    conn = db.connect(str(tmp_path / "p.db"))
    for _ in range(5):
        _tick(
            conn,
            "2026-08-24",
            spot_ok=False,
            flip_ok=False,
            refusal="spot: no_spot_price; flip: insufficient_gex_data",
        )
    conn.commit()

    cov = analytics.trigger_coverage(conn, "2026-08-24")
    assert cov["total_failure"] is True
    assert cov["refusal_share"] == 1.0
    assert cov["no_spot"] == 5 and cov["no_flip"] == 5
    # And it names BOTH halves, rather than the flip reason winning as it used to.
    assert cov["reasons"] == {"spot: no_spot_price; flip: insufficient_gex_data": 5}


def test_a_barren_session_is_thin_data_not_a_defect(tmp_path):
    """No ticks at all is a quiet session, which must never read as the failure above."""
    conn = db.connect(str(tmp_path / "p.db"))
    cov = analytics.trigger_coverage(conn, "2026-08-29")
    assert cov["ticks"] == 0
    assert cov["refusal_share"] is None
    assert cov["total_failure"] is False


def test_a_fully_measured_session_reports_clean(tmp_path):
    conn = db.connect(str(tmp_path / "p.db"))
    _tick(conn, "2026-08-28", spot_ok=True, flip_ok=True)
    conn.commit()
    cov = analytics.trigger_coverage(conn, "2026-08-28")
    assert cov["refused"] == 0 and cov["refusal_share"] == 0.0
    assert cov["reasons"] == {} and cov["total_failure"] is False


# --------------------------------------------------------------- the two defects, pinned directly


def test_the_tick_path_resolves_spot_the_same_way_the_marks_path_does(cache, config):
    """`bwb_legs` has no symbol column, so `build_mark_snapshot` reads `position_symbol` off the
    leg. The marks path set it and the tick path did not, so every tick recorded a NULL spot for
    four sessions while `bwb_marks` beside it was healthy. Both now go through one helper."""
    from cherrypick.bwb import paper_loop, provider

    cache.spot("SPX", 7700.0)
    sym = cache.option("SPX", "2026-08-28", 7600.0, root="SPXW", bid=1.0, ask=1.2)
    conn = db.connect(str(cache.path) + ".ledger.db")
    position = {"position_id": "p1", "symbol": "SPX"}
    db.save_position(
        conn,
        {
            "position_id": "p1",
            "symbol": "SPX",
            "book": "control",
            "entry_session": "2026-08-28",
            "structure_signature": "sig",
            "expiration": "2026-08-28",
            "status": "open",
            "quantity": 1,
            "body_strike": 7600.0,
            "near_strike": 7650.0,
            "far_strike": 7500.0,
            "below_flip_seen": 0,
        },
    )
    db.save_leg(
        conn,
        {
            "position_id": "p1",
            "leg_role": "near_long",
            "streamer_symbol": sym,
            "occ_symbol": "x",
            "expiration": "2026-08-28",
            "strike": 7600.0,
            "option_type": "P",
            "action": "BUY",
            "quantity": 1,
            "status": "open",
        },
    )
    conn.commit()

    legs = paper_loop._legs_with_symbol(conn, position)
    assert legs[0]["position_symbol"] == "SPX", "the underlying must ride on the leg"
    snap = provider.build_mark_snapshot(cache.path, legs)
    assert snap["spot"] == 7700.0, "a leg without position_symbol resolves no spot at all"

    # And the bug's signature: raw legs cannot resolve spot, which is what made the two paths differ.
    raw = db.open_legs_for(conn, "p1")
    assert provider.build_mark_snapshot(cache.path, raw)["spot"] is None


def test_the_gamma_flip_read_needs_gamma_in_the_cache_reader(cache):
    """The other half: `greeks_for` never selected gamma, so `compute_gex` skipped every strike and
    reported `insufficient_gex_data` while gamma and OI were both cached."""
    from cherrypick.bwb import provider

    cache.spot("SPX", 7700.0)
    # A real zero crossing: puts dominate below spot, calls above, so net GEX changes sign between
    # them. A flat surface is `ok` with no flip, which would pass this test without exercising it.
    for strike, call_oi, put_oi in ((7650.0, 100, 900), (7700.0, 500, 500), (7750.0, 900, 100)):
        cache.option(
            "SPX",
            "2026-08-28",
            strike,
            right="C",
            root="SPXW",
            bid=1.0,
            ask=1.2,
            delta=0.4,
            gamma=0.0021,
            iv=0.2,
            oi=call_oi,
        )
        cache.option(
            "SPX",
            "2026-08-28",
            strike,
            right="P",
            root="SPXW",
            bid=1.0,
            ask=1.2,
            delta=-0.4,
            gamma=0.0021,
            iv=0.2,
            oi=put_oi,
        )

    reading = provider.gamma_flip_reading(cache.path, "SPX", "2026-08-28", "SPXW", max_age_seconds=300)
    assert reading["ok"] is True, reading.get("reason")
    assert reading["gamma_flip"] is not None
    assert reading["basis"] == provider.GAMMA_FLIP_BASIS


# ------------------------------------------------- the add-on could never price (2026-08-27)
#
# The flip book armed all four of its positions the moment the gamma flip became measurable, and
# then sat unable to price a single add-on. `_addon_snapshot` resolved `root = symbol`, so every
# lookup asked the cache for SPX-rooted contracts while SPX's weeklies are listed as SPXW —
# `not_root_listed`, on every tick, for every armed position. Every other snapshot in the module
# already resolved `config.get("occ_root") or symbol`; this was the one that did not.
#
# It hid because an armed position that cannot price produces a `hold`, and holds are not recorded.
# "Waiting for a credit" and "cannot read the chain at all" looked identical in the ledger.


def test_the_addon_snapshot_uses_the_occ_root_not_the_symbol(cache, config, tmp_path):
    from cherrypick.bwb import paper_loop
    from cherrypick.bwb.clock import now_et

    cache.spot("SPX", 7700.0)
    # Listed the way SPX weeklies actually are: root SPXW, underlying SPX.
    for strike in (7645.0, 7650.0, 7655.0):
        cache.option("SPX", "2026-08-28", strike, right="P", root="SPXW", bid=1.0, ask=1.2)
    position = {"expiration": "2026-08-28", "far_strike": 7650.0, "symbol": "SPX"}

    wrong = paper_loop._addon_snapshot(cache.path, "SPX", position, now_et(), 86400, root="SPX")
    assert wrong["ok"] is False
    assert wrong["reason"] == "not_root_listed", "the defect: SPX-rooted lookup finds no SPXW chain"

    right = paper_loop._addon_snapshot(cache.path, "SPX", position, now_et(), 86400, root="SPXW")
    assert right["ok"] is True, "the OCC root is what the chain is listed under"


def test_the_addon_root_defaults_to_the_symbol_only_when_none_is_given(cache, config):
    """The fallback stays, so a symbol whose root IS its ticker keeps working."""
    from cherrypick.bwb import paper_loop
    from cherrypick.bwb.clock import now_et

    cache.spot("TNA", 70.0)
    for strike in (66.0, 67.0, 68.0):
        cache.option("TNA", "2026-08-28", strike, right="P", bid=1.0, ask=1.2)
    position = {"expiration": "2026-08-28", "far_strike": 67.0, "symbol": "TNA"}
    assert paper_loop._addon_snapshot(cache.path, "TNA", position, now_et(), 86400)["ok"] is True
