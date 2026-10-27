"""The stage rule, the vendor-edition decoder, and the scorer that holds one against the other."""

from __future__ import annotations

from cherrypick.technicals import editions, stage, stage_score, store
from cherrypick.technicals.stage import Stage, StageRule, classify

RULE = StageRule(windows=(2, 3, 4), laggard_windows=None, method="excess", margins=(0.01, 0.02, 0.02))


def test_all_three_windows_and_the_day_agreeing_is_confirmed():
    assert classify([0.05, 0.05, 0.05], 0.01, RULE) == Stage("leader", "confirmed")
    assert classify([-0.05, -0.05, -0.05], -0.01, RULE) == Stage("laggard", "confirmed")


def test_one_and_two_months_is_building_and_one_month_only_is_early():
    assert classify([0.05, 0.05, 0.0], 0.01, RULE) == Stage("leader", "building")
    assert classify([0.05, 0.0, 0.05], 0.01, RULE) == Stage("leader", "early"), (
        "the 3-month alone adds nothing"
    )


def test_a_day_against_the_side_takes_the_name_off_the_list():
    """BKNG: a confirmed laggard on every window, absent the one day it beat the index."""
    assert classify([-0.2, -0.2, -0.2], 0.01, RULE) is None
    assert classify([0.2, 0.2, 0.2], -0.01, RULE) is None


def test_each_window_must_clear_its_own_margin():
    assert classify([0.009, 0.05, 0.05], 0.01, RULE) is None
    assert classify([0.05, 0.019, 0.05], 0.01, RULE) == Stage("leader", "early")


def test_a_missing_reading_is_never_a_verdict():
    assert classify([0.05, None, 0.05], 0.01, RULE) is None
    assert classify([0.05, 0.05, 0.05], None, RULE) is None


def test_excess_is_the_names_return_minus_the_benchmarks_over_the_same_sessions():
    sessions = ["d0", "d1", "d2"]
    closes = {"d0": 100.0, "d2": 110.0}
    bench = {"d0": 50.0, "d1": 51.0, "d2": 52.5}
    assert round(stage.excess(closes, bench, sessions, "d2", 2), 6) == round(0.10 - 0.05, 6)
    assert stage.excess(closes, bench, sessions, "d2", 1) is None, "no close on d1"
    assert stage.excess(closes, bench, sessions, "d1", 5) is None, "not enough history"


# --------------------------------------------------------------------------- the edition decoder


def _edition(rows):
    link = '<a style="font-weight: bold; color: #{c}; text-decoration: none;" href="x?symbol={s}">{s}</a>'
    body = "".join(
        f"<tr><td>Tech</td><td>{', '.join(link.format(c=c, s=s) for s, c in r)}</td></tr>" for r in rows
    )
    return f'<table><tr><th style="x">Sector</th></tr>{body}</table><p>The three shades</p>'


def test_the_decoder_reads_every_stage_from_its_colour():
    html = _edition(
        [[("AAA", "1B5E20"), ("BBB", "2E7D32"), ("CCC", "43A047")], [("DDD", "7a0030"), ("EEE", "E5384F")]]
    )
    got = editions.decode(html)
    assert got["AAA"] == Stage("leader", "confirmed") and got["CCC"] == Stage("leader", "early")
    assert got["DDD"] == Stage("laggard", "confirmed") and got["EEE"] == Stage("laggard", "early")


def test_an_edition_describes_the_session_before_its_date():
    assert editions.session_of("2026-09-25") == "2026-09-24"
    assert editions.session_of("2026-09-21") == "2026-09-18", "a Monday edition describes Friday"


# --------------------------------------------------------------------------- the scorer


def test_the_scorer_counts_side_stage_extra_and_missed(monkeypatch):
    days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
    conn = store.connect()
    rows = []
    for i, d in enumerate(days):
        rows += [("SPY", d, 100, 100, 100, 100.0, 1)]
        rows += [("UP", d, 1, 1, 1, 100.0 * 1.05**i, 1)]  # beats SPY every day
        rows += [("DOWN", d, 1, 1, 1, 100.0 * 0.95**i, 1)]  # trails every day
        rows += [("FLAT", d, 1, 1, 1, 100.0, 1)]
    store.upsert_bars(conn, rows)
    conn.commit()
    vendor = {
        "2026-09-24": {
            "UP": Stage("leader", "confirmed"),
            "DOWN": Stage("laggard", "building"),
            "FLAT": Stage("leader", "early"),
        }
    }
    monkeypatch.setattr(editions, "load", lambda: vendor)
    excess_rule = StageRule(
        windows=(1, 2, 3), laggard_windows=None, method="excess", margins=(0.01, 0.01, 0.01)
    )
    result = stage_score.score(excess_rule, conn)
    assert result["totals"] == {"listed": 3, "side": 2, "stage": 1, "extra": 0, "missed": 1}
    assert result["sessions"]["2026-09-24"]["counts"] == {"ours": [1, 1], "vendor": [2, 1]}
    assert (
        result["halves"]["fit"]["totals"]["listed"] == 3
        and result["halves"]["later"]["totals"]["editions"] == 0
    )
    later = stage_score.score(excess_rule, conn, split="2026-09-23")["halves"]
    assert later["later"]["totals"]["side"] == 2 and later["fit"]["totals"]["editions"] == 0


# --------------------------------------------------------------------------- the channel rule

CHANNEL = StageRule(windows=(2, 3, 4), laggard_windows=(1, 2, 4))


def test_the_live_rule_is_the_channel_and_the_excess_rule_stays_named():
    assert stage.DEFAULT_RULE is stage.CHANNEL_RULE and stage.CHANNEL_RULE.method == "channel"
    assert stage.CHANNEL_RULE.windows == (20, 41, 62) and stage.CHANNEL_RULE.side_windows("laggard") == (
        14,
        30,
        62,
    )
    assert stage.EXCESS_RULE.windows == (10, 30, 63) and stage.EXCESS_RULE.name == "fit-2026-09-27"
    assert stage.RULES == {"channel": stage.CHANNEL_RULE, "excess": stage.EXCESS_RULE}


def test_a_leader_is_above_every_ratio_of_each_prior_window():
    # oldest first; the last is today. Above all 4 prior: confirmed.
    assert stage.classify_channel([1.0, 1.1, 1.2, 1.3, 1.4], CHANNEL) == Stage("leader", "confirmed")
    # Above the prior 2 and 3, not the 4th back (1.5): building.
    assert stage.classify_channel([1.5, 1.1, 1.2, 1.3, 1.4], CHANNEL) == Stage("leader", "building")
    # Above the prior 2 only: early.
    assert stage.classify_channel([1.5, 1.5, 1.2, 1.3, 1.4], CHANNEL) == Stage("leader", "early")


def test_every_ratio_in_the_window_counts_not_only_the_one_n_back():
    """The channel, not a point comparison: 3 back is lower than today, but 2 back was higher."""
    got = stage.classify_channel([1.0, 1.0, 1.0, 1.5, 1.4], CHANNEL)
    assert got is None or got.side != "leader"


def test_a_laggard_reads_its_own_windows():
    # Below the one prior (1-session channel) and the prior 2, not all 4 (0.8 four back): building.
    assert stage.classify_channel([0.8, 1.0, 1.0, 1.0, 0.9], CHANNEL) == Stage("laggard", "building")
    assert stage.classify_channel([1.0, 1.0, 1.0, 0.95, 0.9], CHANNEL) == Stage("laggard", "confirmed")


def test_a_missing_ratio_or_short_history_is_never_a_channel_verdict():
    assert stage.classify_channel([1.0, None, 1.2, 1.3, 1.4], CHANNEL) is None
    assert stage.classify_channel([1.1, 1.2, 1.3, 1.4], CHANNEL) is None


def test_stages_on_reads_the_ratio_to_the_benchmark_on_its_calendar():
    days = [f"d{k}" for k in range(6)]
    bench = {d: 100.0 * 1.01**k for k, d in enumerate(days)}
    up = {d: 50.0 * 1.03**k for k, d in enumerate(days)}  # rises faster than the benchmark
    flat = {d: 50.0 for d in days}  # falls against a rising benchmark
    got = stage.stages_on("d5", {"UP": up, "FLAT": flat}, bench, CHANNEL)
    assert got == {"UP": Stage("leader", "confirmed"), "FLAT": Stage("laggard", "confirmed")}
    assert stage.series([up[d] for d in days], [bench[d] for d in days], CHANNEL)[5] == got["UP"]
    assert stage.stages_on("d3", {"UP": up}, bench, CHANNEL) == {}, "not enough history"
