"""The pin study: which recorded level the close settled nearest.

The property that carries the whole result is the RTH gate. `gex_regime_history` holds overnight
rows stamped with the NEXT trade_date, and once in this history's life a session's rows crossed
midnight (2026-08-28) — so both hazards are in the fixture, wearing absurd levels, and the test is
that neither can become "the open reading". Scored against levels nobody could have read at the
open, the study would be measuring its own bug.
"""

import sqlite3
from datetime import datetime

import pytest
from cherrypick.core.clock import ET

from cherrypick.gex import pin_study

MON, TUE, WED, THU = "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"


def ts(day: str, hhmm: str) -> float:
    hour, minute = (int(x) for x in hhmm.split(":"))
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=ET).timestamp()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "gex_history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL,
           net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL,
           expiration TEXT)"""
    )
    conn.execute("CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL, recorded_at REAL, source TEXT)")

    def row(day, hhmm, spot, net, flip, cw, pw, *, stamp_day=None):
        conn.execute(
            "INSERT INTO gex_regime_history VALUES ('SPX', ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (stamp_day or day, ts(day, hhmm), spot, net, flip, cw, pw, day),
        )

    # MONDAY: an ordinary positive-regime day whose FINAL reading is what Tuesday's prior_final
    # variant must see (flip 6420, call wall 6450, put wall 6380).
    row(MON, "09:35", 6400, 5e9, 6410, 6440, 6370)
    row(MON, "15:55", 6410, 5e9, 6420, 6450, 6380)

    # The overnight hazard: recorded Monday EVENING, stamped with Tuesday's trade_date, carrying
    # absurd levels. Its ET calendar date is Monday, so the gate must drop it — hour checks alone
    # would too (19:30), so the sharper hazard is the one below.
    row(MON, "19:30", 6400, 5e9, 1111, 2222, 333, stamp_day=TUE)

    # TUESDAY: the open reading (09:31) says call wall 6500, flip 6470, put wall 6420, positive
    # regime; the close (6498) settles ON the call wall. Against Monday's prior_final levels the
    # same close is nearest the 6450 call wall too.
    row(TUE, "09:31", 6480, 6e9, 6470, 6500, 6420)
    row(TUE, "15:50", 6495, 6e9, 6472, 6500, 6425)
    # The sharper hazard, the 2026-08-28 incident's shape: recording carried a stale session date
    # across midnight, so this row is stamped Tuesday but timestamped WEDNESDAY 09:45 — inside RTH
    # HOURS, which is exactly why the hour check alone cannot catch it. Only the calendar-date
    # equality drops it. Absurd levels so any leak moves a number.
    row(WED, "09:45", 6495, 6e9, 6470, 9999, 77, stamp_day=TUE)

    # WEDNESDAY: negative regime; the close (6402) settles nearest the put wall (6400).
    row(WED, "09:45", 6430, -4e9, 6460, 6520, 6400)
    row(WED, "15:58", 6410, -4e9, 6455, 6520, 6400)

    # An expired-chain row FIRST in Wednesday's window (expiration = Monday, long dead), wearing
    # a wall nobody could trade. The forward-only rule must drop it or it becomes the open reading.
    conn.execute(
        "INSERT INTO gex_regime_history VALUES ('SPX', ?, ?, 6430, -4e9, 0, 6460, 8888, 6100, ?)",
        (WED, ts(WED, "09:31"), MON),
    )

    # THURSDAY: readings exist but no close on file yet — must be SKIPPED with the reason, and its
    # final reading must still hand forward as a later session's prior_final.
    row(THU, "09:40", 6410, 3e9, 6440, 6480, 6390)

    conn.executemany(
        "INSERT INTO daily_closes VALUES ('SPX', ?, ?, 0, 'test')",
        [(MON, 6412.0), (TUE, 6498.0), (WED, 6402.0)],
    )
    conn.commit()
    conn.close()
    return path


def by_date(result, day):
    return next(s for s in result["sessions"] if s["trade_date"] == day)


def test_the_close_is_scored_against_the_open_readings_levels(db):
    result = pin_study.pin_study(db)
    tue = by_date(result, TUE)
    assert tue["open"]["levels"] == {"call_wall": 6500, "zero_gamma": 6470, "put_wall": 6420}
    assert tue["open"]["winner"] == "call_wall"
    assert tue["open"]["regime"] == "positive"
    assert tue["open"]["distance_to_close"]["call_wall"] == pytest.approx(2.0)


def test_prior_final_uses_the_previous_sessions_last_rth_reading(db):
    tue = by_date(pin_study.pin_study(db), TUE)
    # Monday's 15:55 reading, not its 09:35 one and not the overnight hazard.
    assert tue["prior_final"]["levels"] == {"call_wall": 6450, "zero_gamma": 6420, "put_wall": 6380}
    assert tue["prior_final"]["winner"] == "call_wall"


def test_overnight_and_past_midnight_rows_cannot_become_the_open_reading(db):
    """Both hazards wear absurd levels; if either leaks in, a winner or a distance changes.

    Verified by removing the calendar-date equality from `_rth_rows`: the wrong-date row lands in
    RTH hours, becomes Tuesday's LAST reading, and poisons Wednesday's prior_final with a 77 put
    wall — the hour check alone does not catch it.
    """
    result = pin_study.pin_study(db)
    tue = by_date(result, TUE)
    assert tue["open"]["levels"]["call_wall"] == 6500  # not 2222, not 9999
    for s in result["sessions"]:
        for variant in ("open", "prior_final"):
            if variant in s:
                assert 300 < s[variant]["levels"]["put_wall"] < 9000
                assert s[variant]["levels"]["call_wall"] < 9000


def test_an_expired_chain_reading_cannot_be_the_open_reading(db):
    """core.regime's forward-only rule, applied here for the same reason: pre-2026-08-26 rows can
    carry levels computed off a chain that had already expired. Wednesday's 09:31 row uses Monday's
    expiration and an 8888 wall; the 09:45 row on the live chain must win instead."""
    wed = by_date(pin_study.pin_study(db), WED)
    assert wed["open"]["levels"]["call_wall"] == 6520  # not 8888


def test_regimes_are_counted_apart_because_pinning_is_a_positive_gamma_claim(db):
    result = pin_study.pin_study(db)
    wed = by_date(result, WED)
    assert wed["open"]["regime"] == "negative"
    assert wed["open"]["winner"] == "put_wall"
    summary = result["summary"]["open"]
    # Monday (close 6412, two points off its 6410 flip) and Tuesday (on its call wall) are both
    # positive-regime sessions; Wednesday's put-wall win must sit apart under "negative".
    assert summary["winners_by_regime"]["positive"] == {"call_wall": 1, "zero_gamma": 1}
    assert summary["winners_by_regime"]["negative"] == {"put_wall": 1}


def test_a_session_without_a_close_is_skipped_with_the_reason_never_silently(db):
    result = pin_study.pin_study(db)
    assert {"trade_date": THU, "reason": "no_close_on_file"} in result["skipped"]
    assert result["scored"] == 3  # Mon (no prior), Tue, Wed
    mon = by_date(result, MON)
    assert "prior_final" not in mon  # nothing on file before it


def test_bwb_zones_walk_the_payoff_from_the_safe_side_in():
    """A put bwb at 6400 (5/10): floor at/above 6405, tent inside, risk below 6395, flat below 6390.
    Boundary closes land where the payoff arithmetic puts them -- the near wing is worth exactly
    zero, so a close ON it is the floor, and the far wing is the last point of the risk zone."""
    K = 6400.0
    assert pin_study.bwb_zone(K + 5, K, "put") == "floor"
    assert pin_study.bwb_zone(K + 4.9, K, "put") == "profit"
    assert pin_study.bwb_zone(K, K, "put") == "profit"
    assert pin_study.bwb_zone(K - 4.9, K, "put") == "profit"
    assert pin_study.bwb_zone(K - 5, K, "put") == "risk"
    assert pin_study.bwb_zone(K - 10, K, "put") == "risk"
    assert pin_study.bwb_zone(K - 10.1, K, "put") == "max_loss"


def test_a_call_bwb_is_the_exact_mirror():
    """Same structure reflected: risk ABOVE the body. A transposed sign here would score the wall
    trade as safe on precisely the days the wall failed."""
    K = 6500.0
    assert pin_study.bwb_zone(K - 5, K, "call") == "floor"
    assert pin_study.bwb_zone(K, K, "call") == "profit"
    assert pin_study.bwb_zone(K + 5, K, "call") == "risk"
    assert pin_study.bwb_zone(K + 10.1, K, "call") == "max_loss"


def test_the_ic_wins_inside_the_walls_and_names_the_breached_side():
    assert pin_study.ic_outcome(6450, 6500, 6400) == {"outcome": "inside", "breach_points": 0.0}
    assert pin_study.ic_outcome(6512, 6500, 6400) == {"outcome": "call_breach", "breach_points": 12.0}
    assert pin_study.ic_outcome(6380, 6500, 6400) == {"outcome": "put_breach", "breach_points": 20.0}
    # A missing wall is no structure; an inverted pair could not be built and is refused, not scored.
    assert pin_study.ic_outcome(6450, None, 6400) is None
    assert pin_study.ic_outcome(6450, 6400, 6500)["outcome"] == "inverted_walls"


def test_the_iron_fly_reports_distance_buckets_because_its_credit_is_unpriceable():
    """The win condition is |close - body| < credit, and no credit is on file -- so the buckets
    hand the reader the distance and nothing else. Boundaries are inclusive on the near side."""
    assert pin_study.iron_fly_zone(6400, 6400) == "within_5"
    assert pin_study.iron_fly_zone(6405, 6400) == "within_5"
    assert pin_study.iron_fly_zone(6405.1, 6400) == "within_10"
    assert pin_study.iron_fly_zone(6410, 6400) == "within_10"
    assert pin_study.iron_fly_zone(6425, 6400) == "within_25"
    assert pin_study.iron_fly_zone(6374.9, 6400) == "beyond_25"
