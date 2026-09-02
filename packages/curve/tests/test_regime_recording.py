"""When a session's regime reading is settled, and when it must be retried.

`curve_regime` is the module's declared second product: the classification is recorded every
session, traded or not. That only holds if a refusal can still become a measurement.
"""

from cherrypick.curve import db, paper_loop

DAY = "2026-08-26"
MIDNIGHT = 0
OPEN_TICK = 9 * 60 + 31  # 09:31 ET, inside RTH
PREMARKET = 2 * 60 + 38  # 02:38 ET, the hour the 2026-09-02 defect was measured at


def _config():
    return {"defaults": {"max_quote_age_seconds": 300, "contango_max": 0.97, "hook_threshold": 1.10}}


def test_an_overnight_refusal_does_not_settle_the_session(tmp_path, cache):
    """The tick runs every 60s from midnight, so the session's FIRST attempt always lands hours
    before the open and always refuses -- correctly, since an overnight-frozen VIX must never
    masquerade as a measured reading.

    Treating that refusal as "already recorded" blocked every RTH tick behind it, and the product
    was never measured once: three sessions on file, all stamped 00:00 with a null ratio.
    """
    conn = db.connect(str(tmp_path / "paper_trades.db"))

    # 00:00 — hours before the open, so the reading refuses and is recorded as such.
    first = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=MIDNIGHT
    )
    assert first["usable"] == 0 and first["ratio"] is None

    # 09:31 — the feed is live. The stored refusal must not block this.
    cache.spot("VIX", 15.5).spot("VIX3M", 18.3)
    second = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=OPEN_TICK
    )

    assert second["usable"] == 1, "a refusal must be retried, never treated as the day's answer"
    assert second["regime"] == "contango"
    stored = db.regime_for(conn, DAY)
    assert stored["usable"] == 1 and stored["ratio"] is not None, "and it overwrites the row"


def test_a_measurement_is_final_for_the_session(tmp_path, cache):
    """Once measured, the day's basis is fixed. Re-reading on every later tick would let the
    recorded classification drift with intraday VIX, which is not what a daily regime read means.
    """
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    cache.spot("VIX", 15.5).spot("VIX3M", 18.3)
    first = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=OPEN_TICK
    )
    assert first["usable"] == 1

    cache.spot("VIX", 25.0).spot("VIX3M", 20.0)  # a later, very different tape
    again = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=OPEN_TICK + 60
    )

    assert again["ratio"] == first["ratio"], "the session's basis does not drift once established"
    assert db.regime_for(conn, DAY)["regime"] == "contango"


def test_a_premarket_quote_is_refused_on_the_clock(tmp_path, cache):
    """The defect of 2026-09-02, and the reason this gate is a clock check.

    A streamer reconnect resubscribes every symbol, DXLink answers with a snapshot of the last
    trade, and `_listen_trade` stamps `stream_trades.updated_at` with the time it RECEIVED that
    event. So at 02:38 ET the cache served yesterday's closing VIX looking seconds old, the reading
    passed the freshness gate, and the day's basis was settled on a stale quote — which the 10:28
    entry then traded through. The cache below is exactly that: a perfectly fresh-looking quote at
    an hour the index cannot print.
    """
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    cache.spot("VIX", 16.34).spot("VIX3M", 18.33)  # yesterday's closes, restamped by a reconnect

    row = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=PREMARKET
    )

    assert row["usable"] == 0, "a quote outside RTH is refused however fresh the cache claims it is"
    assert row["refusal"] == "outside_rth"
    assert row["ratio"] is None and row["vix"] is None, "and no stale value is carried onto the row"


def test_the_clock_refusal_is_still_a_recorded_row(tmp_path, cache):
    """Rule 7: the classification is recorded every session, traded or not. Refusing on the clock
    must not mean writing nothing — a day the loop never saw past the open still has to leave a row
    saying why, or an unmeasured session is indistinguishable from one that never ran.
    """
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY, now_min=MIDNIGHT)

    stored = db.regime_for(conn, DAY)
    assert stored is not None and stored["refusal"] == "outside_rth"


def test_the_clock_gate_never_blocks_an_rth_measurement(tmp_path, cache):
    """The gate refuses a time of day, not a reading. Inside RTH nothing about it applies, and the
    same premarket refusal is overwritten the moment the session opens.
    """
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    cache.spot("VIX", 16.34).spot("VIX3M", 18.33)
    paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY, now_min=PREMARKET)

    cache.spot("VIX", 15.20).spot("VIX3M", 17.73)  # the real open, a different tape
    measured = paper_loop._record_regime(
        _config(), conn, cache_path=cache.path, day=DAY, now_min=OPEN_TICK
    )

    assert measured["usable"] == 1 and measured["regime"] == "contango"
    assert measured["vix"] == 15.20, "the session's basis is the RTH reading, not the premarket one"
