"""When a session's regime reading is settled, and when it must be retried.

`curve_regime` is the module's declared second product: the classification is recorded every
session, traded or not. That only holds if a refusal can still become a measurement.
"""

from cherrypick.curve import db, paper_loop

DAY = "2026-08-26"


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

    # 00:00 — nothing in the cache yet, so the reading refuses and is recorded as such.
    first = paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY)
    assert first["usable"] == 0 and first["ratio"] is None

    # 09:31 — the feed is live. The stored refusal must not block this.
    cache.spot("VIX", 15.5).spot("VIX3M", 18.3)
    second = paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY)

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
    first = paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY)
    assert first["usable"] == 1

    cache.spot("VIX", 25.0).spot("VIX3M", 20.0)  # a later, very different tape
    again = paper_loop._record_regime(_config(), conn, cache_path=cache.path, day=DAY)

    assert again["ratio"] == first["ratio"], "the session's basis does not drift once established"
    assert db.regime_for(conn, DAY)["regime"] == "contango"
