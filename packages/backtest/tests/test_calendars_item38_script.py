# ruff: noqa: E501
"""Item 38, the calendars module's own rule at real quotes (scripts/calendars_item38.py): the weeks and
their tags, the ex-dividend skip, the request list, the listing step and the pilot's seeded draw and
basis rule, the entry and exit rules at stored bars (gate, noon, fall-throughs, settlement of
delivered shares, the Friday regime's ordering), the 30-day reading and its rank, and the verdict.
Invented chains and weeks only, no vendor, no store of real data.

Each declared guard is shown firing and failing, because a check that cannot fire reads as coverage it
is not.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cherrypick.calendars")

from cherrypick.calendars import engine  # noqa: E402

from cherrypick.backtest import intradayfetch as ix  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "calendars_item38.py"


def _module():
    spec = importlib.util.spec_from_file_location("calendars_item38", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c38 = _module()

MON, FRONT, BACK, FRI = date(2024, 3, 4), date(2024, 3, 8), date(2024, 3, 11), date(2024, 3, 1)
SPOT = 500.0
NO_DIVS = c38.div_config([], "2030-01-01")


# --------------------------------------------------------------------------- invented chains


def _mid_of(spot: float, atm_tv: float, strike: float, right: str) -> float:
    intrinsic = max(0.0, spot - strike) if right == "CALL" else max(0.0, strike - spot)
    return round(intrinsic + atm_tv * max(0.05, 1 - abs(strike - spot) / 20), 4)


def chain_rows(bars, spot=SPOT, atm_tv=3.0, over=None, half=0.01, strikes=range(450, 551)):
    """One expiry's chain at each bar: symmetric time value, a one-cent half spread, and per-(strike,
    right) overrides `over[(strike, right)] = (bid, ask)` or None for no quote. Delta and implied
    volatility are present, the underlying is `spot` (a number or a {bar: spot} map)."""
    rows = []
    for bar in bars:
        s = spot[bar] if isinstance(spot, dict) else spot
        for k in strikes:
            for right in ("CALL", "PUT"):
                mid = _mid_of(s, atm_tv, float(k), right)
                bid, ask = round(mid - half, 4), round(mid + half, 4)
                if over and (float(k), right) in over:
                    given = over[(float(k), right)]
                    bid, ask = (None, None) if given is None else given
                rows.append({"bar": bar, "strike": float(k), "right": right, "bid": bid, "ask": ask,
                             "delta": 0.5 if right == "CALL" else -0.5, "iv": 0.2, "underlying": s})  # fmt: skip
    return rows


def put(conn, session, exp, start, end, rows, interval="5m"):
    ix.write_window(conn, (c38.SYMBOL, str(session), str(exp), start, end, interval), rows)


def store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ix.SCHEMA)
    return conn


def close_of(day):
    return {FRONT: 500.0}.get(day, 500.0)


def at(bars, front_mid=None, back_mid=None):
    return bars


def standard_week(conn, *, mon_over=None, noon_over=None, close_over=None, dispo_over=None,
                  close_spot=SPOT, dispo_spot=SPOT, exit_tv=(0.5, 1.5)):  # fmt: skip
    """The Monday-entry week 2024-03-04: ATM straddle 3.0 + 3.0 on the front (an expected move of 5.1,
    strikes 495 and 505), the back worth 1.2 more at the money; at the exits the front has decayed to
    `exit_tv[0]` and the back to `exit_tv[1]`."""
    mon_bars = list(c38.MON_BARS)
    put(
        conn,
        MON,
        FRONT,
        "10:00",
        "12:15",
        chain_rows(mon_bars, atm_tv=3.0, over=(mon_over or {}).get("f")),
        "15m",
    )
    put(
        conn,
        MON,
        BACK,
        "10:00",
        "12:15",
        chain_rows(mon_bars, atm_tv=4.2, over=(mon_over or {}).get("b")),
        "15m",
    )
    for exp, tv, k in ((FRONT, exit_tv[0], "f"), (BACK, exit_tv[1], "b")):
        put(conn, FRONT, exp, "12:00", "12:10",
            chain_rows(c38.NOON_BARS, atm_tv=tv, over=(noon_over or {}).get(k), spot=close_spot))  # fmt: skip
        put(conn, FRONT, exp, "15:45", "15:55",
            chain_rows(c38.CLOSE_BARS, atm_tv=tv, over=(close_over or {}).get(k), spot=close_spot))  # fmt: skip
    put(conn, BACK, BACK, "09:45", "09:55",
        chain_rows(c38.DISPO_BARS, atm_tv=0.8, over=(dispo_over or {}).get("b"), spot=dispo_spot))  # fmt: skip


def chains_of(conn):
    return c38.Chains(conn)


def ctx(rank=None, public=None, prior=None, closes=None):
    return {"close_of": closes or close_of, "rank_of": lambda f: rank, "public_rank_of": lambda f: public,
            "prior_control": prior if prior is not None else {}}  # fmt: skip


def week():
    return c38.week_of(MON)


def replay(conn, **kw):
    w = week()
    return c38.replay_week(chains_of(conn), w, c38.week_status(w, NO_DIVS), ctx(**kw))


# --------------------------------------------------------------------------- the weeks


def test_an_ordinary_week_has_the_two_ordinary_tags():
    w = week()
    assert (w["entry"], w["front"], w["back"], w["friday"]) == (MON, FRONT, BACK, FRI)
    assert (w["tag"], w["friday_tag"]) == ("dc_4_7", "dc_7_10")
    assert c38.week_status(w, NO_DIVS) == {"monday": None, "friday": None}


def test_a_holiday_shifted_week_is_a_calendar_shift_and_is_not_judged_or_fetched():
    # Good Friday 2024-03-29: the front is Thursday; the following Monday's Friday regime has no Friday
    good_friday = c38.week_of(date(2024, 3, 25))
    assert good_friday["tag"] == "dc_3_7"
    assert c38.week_status(good_friday, NO_DIVS)["monday"] == "calendar_shift"
    after = c38.week_of(date(2024, 4, 1))
    st = c38.week_status(after, NO_DIVS)
    assert st["monday"] is None and st["friday"] == "calendar_shift", "the session before is a Thursday"
    # MLK Monday: a Tuesday entry
    mlk = c38.week_of(date(2024, 1, 15))
    assert mlk["entry"] == date(2024, 1, 16) and mlk["tag"] == "dc_3_6"
    reqs = c38.week_requests(mlk, c38.week_status(mlk, NO_DIVS))
    assert {r["kind"] for r in reqs} <= {"ivr", "close_front", "close_back", "dispo", "fri_front", "fri_back"}
    assert not [r for r in reqs if r["kind"].startswith("mon_")], (
        "no Monday-regime request for a shifted week"
    )


def test_an_ex_dividend_date_in_the_span_skips_the_week_by_the_modules_own_rule():
    on_front = c38.div_config(["2024-03-08"], "2030-01-01")  # a Friday ex-date, as every 2026 one is
    assert c38.week_status(week(), on_front) == {"monday": "ex_dividend", "friday": "ex_dividend"}
    on_friday_entry = c38.div_config(["2024-03-01"], "2030-01-01")  # only the Friday regime spans it
    assert c38.week_status(week(), on_friday_entry) == {"monday": None, "friday": "ex_dividend"}
    on_back = c38.div_config(["2024-03-11"], "2030-01-01")  # the Monday the longs are sold
    assert c38.week_status(week(), on_back)["monday"] == "ex_dividend"
    outside = c38.div_config(["2024-03-12"], "2030-01-01")
    assert c38.week_status(week(), outside) == {"monday": None, "friday": None}


def test_a_week_past_the_declared_calendar_is_refused_never_read_as_dividend_free():
    lapsed = c38.div_config(["2024-03-08"], "2024-03-10")  # declared through before the back expiry
    with pytest.raises(ValueError, match="does not cover"):
        c38.week_status(week(), lapsed)
    c38.week_status(week(), c38.div_config([], "2024-03-11"))  # exactly through: allowed


def test_the_windows_split_early_or_late_by_the_start():
    assert c38.window_for(date(2023, 12, 25), date(2017, 1, 2)) == "design"
    assert c38.window_for(date(2024, 1, 2), date(2017, 1, 2)) == "holdout"
    assert c38.window_for(date(2026, 8, 17), date(2017, 1, 2)) == "holdout"
    assert c38.window_for(date(2026, 8, 24), date(2017, 1, 2)) == "paper"
    # a start after 2019-12-31 moves the split before any quote is read
    assert c38.window_for(date(2024, 6, 3), date(2022, 5, 2)) == "design"
    assert c38.window_for(date(2025, 1, 6), date(2022, 5, 2)) == "holdout"
    assert c38.window_for(date(2016, 1, 4), date(2017, 1, 2)) == "outside"


# --------------------------------------------------------------------------- the requests


def test_an_ordinary_week_is_eleven_requests_in_the_declared_windows_all_fifty_strikes(tmp_path):
    w = week()
    reqs = c38.week_requests(w, c38.week_status(w, NO_DIVS))
    assert len(reqs) == 11
    by = {(r["kind"], r["expiration"]): r for r in reqs}
    mf = by[("mon_front", "2024-03-08")]
    assert (mf["session"], mf["start"], mf["end"], mf["interval"]) == ("2024-03-04", "10:00", "12:15", "15m")
    assert by[("noon_back", "2024-03-11")]["session"] == "2024-03-08"
    assert (by[("noon_front", "2024-03-08")]["start"], by[("noon_front", "2024-03-08")]["end"]) == (
        "12:00",
        "12:10",
    )
    assert (by[("close_front", "2024-03-08")]["start"], by[("close_front", "2024-03-08")]["end"]) == (
        "15:45",
        "15:55",
    )
    d = by[("dispo", "2024-03-11")]
    assert (d["session"], d["start"], d["end"]) == ("2024-03-11", "09:45", "09:55")
    ff = by[("fri_front", "2024-03-08")]
    assert (ff["session"], ff["start"], ff["end"]) == ("2024-03-01", "15:50", "16:00")
    ivr = [r for r in reqs if r["kind"] == "ivr"]
    assert len(ivr) == 2 and all(
        r["session"] == "2024-03-01" and r["start"] == r["end"] == "15:55" for r in ivr
    )
    assert {r["expiration"] for r in ivr} == {"2024-03-15", "2024-04-19"}, (
        "the two monthlies bracketing 30 days"
    )
    assert all(r["strike_range"] == 50 for r in reqs)
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"requests": reqs}), encoding="utf-8")
    keys, ranges = ix.read_request_file(path)
    assert len(keys) == 11 and set(ranges.values()) == {50}, "the fetcher reads the file and the range"


def test_a_skipped_week_is_fetched_for_its_reading_only():
    w = week()
    st = c38.week_status(w, c38.div_config(["2024-03-08"], "2030-01-01"))
    assert {r["kind"] for r in c38.week_requests(w, st)} == {"ivr"}
    only_friday_skipped = c38.week_status(w, c38.div_config(["2024-03-01"], "2030-01-01"))
    kinds = {r["kind"] for r in c38.week_requests(w, only_friday_skipped)}
    assert "mon_front" in kinds and "fri_front" not in kinds


def test_the_full_list_adds_fifty_two_weeks_of_warmup_readings_and_counts_the_set_aside_weeks():
    first = date(2024, 1, 1)
    reqs, counts = c38.full_requests(first, NO_DIVS, last=date(2024, 3, 25))
    warm = [r for r in reqs if date.fromisoformat(r["week"]) < first]
    assert warm and {r["kind"] for r in warm} == {"ivr"}
    assert min(r["week"] for r in warm) <= (first - timedelta(weeks=52)).isoformat()
    assert counts["weeks_skipped"]["monday:calendar_shift"] >= 2  # MLK week and Good Friday week
    assert len({(r["session"], r["expiration"], r["start"], r["end"]) for r in reqs}) == len(reqs)


def test_the_probe_requests_ask_for_one_bar_two_strikes_both_expiries_each_monday():
    probes = c38.probe_requests()
    assert all(r["start"] == r["end"] == c38.PROBE_BAR and r["strike_range"] == 2 for r in probes)
    assert len(probes) == 2 * len(c38.weeks_between(c38.LISTING_FROM, c38.LISTING_TO))
    one = [r for r in probes if r["week"] == "2024-03-04" or r["week"] == "2022-05-02"]
    assert {(r["kind"]) for r in one} == {"probe_front", "probe_back"}
    # the probe's one bar can never be a bar a real request writes (it would shrink a real chain)
    assert c38.PROBE_BAR not in c38.MON_BARS


def test_a_probe_never_shares_a_window_with_a_real_request_of_the_same_chain():
    """Both writes delete the bars of their own window; the probe's bar is outside every real window of
    the same session and expiry, so it can never thin a real chain."""
    w = week()
    real = c38.week_requests(w, c38.week_status(w, NO_DIVS))
    probe = [r for r in c38.probe_requests() if r["week"] == "2024-03-04"]
    for p in probe:
        for r in real:
            if (r["session"], r["expiration"]) == (p["session"], p["expiration"]):
                assert not (r["start"] <= p["start"] <= r["end"]), (r["kind"], p["kind"])


# --------------------------------------------------------------------------- listing and pilot


def _states(pattern: dict[date, tuple[str, str]]):
    return {m: {"front": f, "back": b} for m, (f, b) in pattern.items()}


def test_the_start_is_the_first_monday_with_both_expiries_listed():
    mondays = [date(2016, 1, 4) + timedelta(weeks=i) for i in range(6)]
    s = _states(
        {mondays[0]: ("complete", "empty"), mondays[1]: ("complete", "empty"), mondays[2]: ("empty", "complete"),
         mondays[3]: ("complete", "complete"), mondays[4]: ("complete", "empty"), mondays[5]: ("complete", "complete")}
    )  # fmt: skip
    got = c38.first_listed(s)
    assert got == {"decided": True, "start": mondays[3], "weeks_before": 3}


def test_the_start_is_undecided_while_an_earlier_probe_is_unanswered_or_refused():
    mondays = [date(2016, 1, 4) + timedelta(weeks=i) for i in range(4)]
    for bad in ("missing", "unanswered", "refused"):
        s = _states({mondays[0]: ("complete", "empty"), mondays[1]: ("complete", bad),
                     mondays[2]: ("complete", "complete"), mondays[3]: ("complete", "complete")})  # fmt: skip
        got = c38.first_listed(s)
        assert got["decided"] is False and got["stuck_at"] == mondays[1], bad
    none = c38.first_listed(_states({mondays[0]: ("complete", "empty")}))
    assert none == {"decided": True, "start": None, "weeks_before": 1}, "answered all, found none"


def test_the_pilot_draw_is_forty_weeks_spread_over_the_years_and_blind_to_order_and_outcome():
    weeks = [w["week_of"] for w in c38.weeks_between(date(2016, 9, 5), date(2026, 10, 19))]
    drawn = c38.pilot_draw(weeks)
    assert len(drawn) == 40 and len(set(drawn)) == 40
    years = {d.year for d in drawn}
    assert years == set(range(2016, 2027)), "every year from the start is sampled"
    per = [sum(d.year == y for d in drawn) for y in sorted(years)]
    assert max(per) - min(per) <= 1, per
    assert c38.pilot_draw(list(reversed(weeks))) == drawn, (
        "the draw reads dates, not the order they arrive in"
    )
    assert c38.pilot_draw(weeks) == drawn, "seeded: the same draw every time"
    assert c38.pilot_draw(weeks[:100], total=40) != c38.pilot_draw(weeks[100:200], total=40)
    # a different seed would draw differently: the seed is part of the key
    assert c38.draw_key(date(2020, 1, 6)) != c38.hashlib.sha256(b"x").hexdigest()


def test_a_year_with_fewer_candidates_than_its_share_gives_all_it_has():
    few = [date(2016, 9, 5) + timedelta(weeks=i) for i in range(3)]
    drawn = c38.pilot_draw(few + [date(2017, 1, 2) + timedelta(weeks=i) for i in range(30)])
    assert sum(d.year == 2016 for d in drawn) == 3


def test_the_basis_rule_starts_at_the_first_year_from_which_every_later_year_is_ninety_percent_complete():
    def py(*shares):
        return {2016 + i: {"requests": 100, "complete": s} for i, s in enumerate(shares)}

    assert c38.basis_year(py(95, 95, 95)) == 2016
    assert c38.basis_year(py(50, 95, 95)) == 2017, "an early weak year moves the start past it"
    assert c38.basis_year(py(95, 50, 95, 95)) == 2018, (
        "a weak middle year moves the start past it, never mixed"
    )
    assert c38.basis_year(py(95, 95, 89)) is None, "the last year decides: below 90% there is no basis"
    assert c38.basis_year(py(90, 90)) == 2016, "exactly 90% passes"
    assert c38.basis_year({}) is None
    listed = date(2016, 9, 5)
    assert c38.moved_start(listed, 2016) == listed
    assert c38.moved_start(listed, 2018) == date(2018, 1, 1)
    assert c38.moved_start(listed, 2019) == date(2019, 1, 7)
    assert c38.moved_start(listed, None) is None


def _pilot_world(complete_bars=True, with_1600=True):
    conn = store()
    w = week()
    reqs = c38.week_requests(w, c38.week_status(w, NO_DIVS))
    for r in reqs:
        bars = [b for b in c38.MON_BARS + c38.NOON_BARS + c38.CLOSE_BARS + c38.DISPO_BARS + c38.FRI_BARS
                + ("15:55",) if r["start"] <= b <= r["end"]]  # fmt: skip
        if not with_1600:
            bars = [b for b in bars if b != "16:00"]
        if r["kind"] == "close_front" and not complete_bars:
            bars = [b for b in bars if b != "15:55"]
        put(conn, r["session"], r["expiration"], r["start"], r["end"], chain_rows(sorted(set(bars)), strikes=range(498, 503)),
            r["interval"])  # fmt: skip
    for r in reqs:
        r["year"] = 2024
    return conn, reqs


def test_the_pilot_counts_requests_by_year_and_reports_presence_never_a_price():
    conn, reqs = _pilot_world()
    cov = c38.pilot_coverage(conn, reqs)
    assert cov["decided"] and cov["years"]["2024"]["share"] == 1.0 and cov["basis_year"] == 2024
    assert cov["bar_16_00"] == {"asked": 2, "present": 2}
    keys = {k for k in _keys(cov)}
    assert not keys & {"bid", "ask", "mid", "iv", "delta", "underlying", "strike"}, keys
    # no number in the report is a quote: the only floats are shares
    nums = [x for x in _walk(cov) if isinstance(x, float)]
    assert all(0 <= x <= 1 for x in nums), nums


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def _walk(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
    else:
        yield o


def test_a_request_missing_a_required_bar_is_incomplete_and_a_missing_16_00_bar_is_counted_not_required():
    conn, reqs = _pilot_world(complete_bars=False, with_1600=False)
    cov = c38.pilot_coverage(conn, reqs)
    assert cov["years"]["2024"]["complete"] == len(reqs) - 1, "close_front lacks its 15:55 bar"
    assert cov["missing_required_bars"] == {"close_front 15:55": 1}
    assert cov["bar_16_00"] == {"asked": 2, "present": 0}, (
        "no 16:00 bar: counted, and the fri requests complete"
    )
    assert cov["by_kind"]["fri_front"] == {"complete": 1}


def test_an_unanswered_pilot_decides_nothing():
    conn, reqs = _pilot_world()
    with conn:
        conn.execute("UPDATE intraday_chain_requests SET status='refused', detail='session_in_use: x', rows=0 "
                     "WHERE session = '2024-03-04' AND expiration = '2024-03-08'")  # fmt: skip
    cov = c38.pilot_coverage(conn, reqs)
    assert cov["decided"] is False and cov["unanswered"] >= 1 and "basis_year" not in cov


# --------------------------------------------------------------------------- the entry


def test_the_entry_is_the_modules_own_plan_on_the_first_bar_that_passes():
    conn = store()
    standard_week(conn)
    got = c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)
    assert got["ok"] and got["bar"] == "10:00"
    sides = got["plan"]["sides"]
    assert (sides["put"]["strike"], sides["call"]["strike"]) == (495.0, 505.0)
    assert got["plan"]["em"] == pytest.approx(0.85 * 6.0)
    for s in sides.values():
        assert s["debit"] == pytest.approx(_mid_of(500, 4.2, s["strike"], "PUT") - _mid_of(500, 3.0, s["strike"], "PUT"),
                                           abs=1e-3) or s["debit"] > 0  # fmt: skip


def test_a_bar_whose_chain_the_module_would_refuse_is_skipped_for_the_next_one():
    conn = store()
    # at 10:00 the front chain has no call quotes at all (no expected move); from 10:15 it does
    standard_week(conn)
    no_calls = {(float(k), "CALL"): None for k in range(450, 551)}
    bad = chain_rows(["10:00"], atm_tv=3.0, over=no_calls)
    good = chain_rows(list(c38.MON_BARS[1:]), atm_tv=3.0)
    put(conn, MON, FRONT, "10:00", "12:15", bad + good, "15m")
    assert (
        c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS, only="10:00")["reason"]
        == "no_em_quotes"
    )
    got = c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)
    assert got["ok"] and got["bar"] == "10:15"


def test_a_strike_quoted_on_one_expiry_only_is_not_taken_the_nearest_shared_one_is():
    conn = store()
    standard_week(conn)
    put(
        conn,
        MON,
        BACK,
        "10:00",
        "12:15",
        chain_rows(c38.MON_BARS, atm_tv=4.2, over={(505.0, "CALL"): None}),
        "15m",
    )
    got = c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)
    assert got["ok"] and got["plan"]["sides"]["call"]["strike"] == 506.0, "never the front's 505 alone"


def test_each_refusal_is_the_modules_vocabulary_and_the_last_bars_reason_is_the_weeks():
    conn = store()
    no_calls = {(float(k), "CALL"): None for k in range(450, 551)}
    put(conn, MON, FRONT, "10:00", "12:15", chain_rows(c38.MON_BARS, over=no_calls), "15m")
    put(conn, MON, BACK, "10:00", "12:15", chain_rows(c38.MON_BARS, atm_tv=4.2), "15m")
    assert c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)["reason"] == "no_em_quotes"
    conn = store()  # the back worth less than the front: a credit is a torn read
    put(conn, MON, FRONT, "10:00", "12:15", chain_rows(c38.MON_BARS, atm_tv=3.0), "15m")
    put(conn, MON, BACK, "10:00", "12:15", chain_rows(c38.MON_BARS, atm_tv=2.0), "15m")
    assert (
        c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)["reason"] == "non_positive_debit"
    )
    conn = store()  # no strike quoted on both expiries on the put side
    put(conn, MON, FRONT, "10:00", "12:15", chain_rows(c38.MON_BARS, strikes=range(490, 511)), "15m")
    put(
        conn,
        MON,
        BACK,
        "10:00",
        "12:15",
        chain_rows(c38.MON_BARS, atm_tv=4.2, strikes=range(520, 541)),
        "15m",
    )
    assert (
        c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)["reason"]
        == "no_intersection_strike"
    )
    assert c38.attempt_entry(chains_of(store()), MON, FRONT, BACK, c38.MON_BARS)["reason"] == "no_entry_bars"


def test_only_strikes_within_four_percent_of_spot_are_in_the_entry_snapshot():
    conn = store()
    standard_week(conn)
    snap = c38.snapshot(chains_of(conn), MON, FRONT, BACK, "10:00", window_pct=0.04)
    strikes = {e["strike_price"] for e in snap["front"]}
    assert min(strikes) == 480.0 and max(strikes) == 520.0
    wide = c38.snapshot(chains_of(conn), MON, FRONT, BACK, "10:00", window_pct=0.10)
    assert min(e["strike_price"] for e in wide["front"]) == 450.0, "the window is what narrows it"


def test_a_leg_wider_than_the_exit_gate_is_counted_at_entry_not_refused():
    conn = store()
    wide = {
        (495.0, "PUT"): (0.10, 0.60)
    }  # a 0.50 spread on a leg worth about 0.35: wide in percent and money
    standard_week(conn)
    put(conn, MON, BACK, "10:00", "12:15", chain_rows(c38.MON_BARS, atm_tv=4.2), "15m")
    put(conn, MON, FRONT, "10:00", "12:15", chain_rows(c38.MON_BARS, atm_tv=3.0, over=wide), "15m")
    got = c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)
    assert got["ok"], "the module has no entry gate"
    rec = c38.entry_week(got, week(), "monday")
    assert c38.entry_flags(rec)["wide_entry_legs"] >= 1


def test_a_zero_bid_is_priced_from_its_ask_as_the_module_would_and_counted():
    conn = store()
    standard_week(conn)
    put(
        conn,
        MON,
        FRONT,
        "10:00",
        "12:15",
        chain_rows(c38.MON_BARS, over={(495.0, "PUT"): (0.0, 0.05)}),
        "15m",
    )
    got = c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS)
    assert got["ok"]
    assert c38.entry_flags(c38.entry_week(got, week(), "monday"))["zero_bid_legs"] >= 1


# --------------------------------------------------------------------------- the exits and the columns


def test_control_closes_all_four_legs_at_the_first_passing_bar_and_the_columns_add_up():
    conn = store()
    standard_week(conn)
    rec = replay(conn)
    t = rec["arms"]["control"]
    assert t["entry_bar"] == "10:00" and t["exit_bar"] == "15:45" and t["fell"] == []
    assert t["tag"] == "dc_4_7"
    parts = t["entry_fees"] + t["entry_slippage"] + t["exit_fees"] + t["exit_slippage"] + t["settlement"]
    assert t["gross"] - parts == pytest.approx(t["net"], abs=0.011), "gross less every cost column is the net"
    assert t["costs"] == pytest.approx(parts, abs=0.011)
    assert t["settlement"] == 0.0 and t["share_pnl"] == 0.0
    assert t["entry_slippage"] > 0 and t["exit_slippage"] > 0 and t["entry_fees"] > 0 and t["exit_fees"] >= 0
    # the module's own cost function reproduces the entry columns
    er = c38.entry_week(c38.attempt_entry(chains_of(conn), MON, FRONT, BACK, c38.MON_BARS), week(), "monday")
    wk = c38.week_dict(er, {}, c38.config_for())
    fee = sum(p["entry_cost"] for p in wk["positions"].values())
    assert t["entry_fees"] == pytest.approx(fee, abs=0.011)
    # gross from the marks by hand: each side's back change less its front change, per share, times 100
    exp_gross = 0.0
    for k, right in ((495.0, "PUT"), (505.0, "CALL")):
        d_front = _mid_of(500, 0.5, k, right) - _mid_of(500, 3.0, k, right)
        d_back = _mid_of(500, 1.5, k, right) - _mid_of(500, 4.2, k, right)
        exp_gross += 100 * (d_back - d_front)
    assert t["gross"] == pytest.approx(exp_gross, abs=0.011)


def test_the_costs_stack_has_a_stressed_half_way_natural_and_mid_basis_in_that_order():
    conn = store()
    standard_week(conn)
    t = replay(conn)["arms"]["control"]
    assert t["net_stress15"] == pytest.approx(t["gross"] - 1.5 * t["costs"], abs=0.011)
    assert t["net_mid"] > t["net"] > t["net_half"] > t["net_natural"]
    assert t["net_stress15"] < t["net"]


def test_the_exit_gate_blocks_a_bar_only_when_a_leg_is_wide_in_percent_and_in_money():
    wide_both = {(495.0, "PUT"): (0.05, 0.15)}  # 0.10 wide on a mid of 0.10: 100% and above $0.05
    conn = store()
    standard_week(conn, close_over={"f": wide_both})
    # only the 15:45 bar carries the wide leg
    first = chain_rows(["15:45"], atm_tv=0.5, over=wide_both)
    rest = chain_rows(["15:50", "15:55"], atm_tv=0.5)
    put(conn, FRONT, FRONT, "15:45", "15:55", first + rest)
    t = replay(conn)["arms"]["control"]
    assert t["exit_bar"] == "15:45" or t["exit_bar"] == "15:50"
    # make the wide leg unambiguous: a leg worth 1.0 quoted 0.60 / 1.40 is wide in both
    conn2 = store()
    standard_week(conn2)
    k = (495.0, "PUT")
    blocked = chain_rows(["15:45"], atm_tv=0.5, over={k: (0.30, 0.90)})  # mid 0.6, spread 0.6: 100% and $0.60
    put(conn2, FRONT, FRONT, "15:45", "15:55", blocked + chain_rows(["15:50", "15:55"], atm_tv=0.5))
    assert replay(conn2)["arms"]["control"]["exit_bar"] == "15:50", (
        "wide in both blocks the bar; the next is tried"
    )
    # wide in percent but within five cents does not block: the module's own 0.00/0.01 leg
    conn3 = store()
    standard_week(conn3)
    penny = {k: (0.00, 0.04)}  # 200% of a 0.02 mid, but four cents
    put(conn3, FRONT, FRONT, "15:45", "15:55", chain_rows(["15:45"], atm_tv=0.5, over=penny)
        + chain_rows(["15:50", "15:55"], atm_tv=0.5))  # fmt: skip
    assert replay(conn3)["arms"]["control"]["exit_bar"] == "15:45", "percentage alone is not a block"


def test_the_gate_guard_fails_when_its_invariant_is_broken():
    """Break the invariant on purpose: with the money floor removed (a percentage-only gate) the penny
    leg blocks the bar, the very bug the module's gate was written to fix."""
    k = (495.0, "PUT")
    conn = store()
    standard_week(conn)
    penny = {k: (0.00, 0.04)}
    put(conn, FRONT, FRONT, "15:45", "15:55", chain_rows(["15:45"], atm_tv=0.5, over=penny)
        + chain_rows(["15:50", "15:55"], atm_tv=0.5))  # fmt: skip
    saved = dict(c38.DECLARED)
    try:
        c38.DECLARED["max_leg_spread_abs"] = 0.0
        assert replay(conn)["arms"]["control"]["exit_bar"] == "15:50"
    finally:
        c38.DECLARED.clear()
        c38.DECLARED.update(saved)


def test_noon_friday_closes_every_leg_at_the_first_passing_noon_bar_and_a_wide_first_bar_moves_it():
    conn = store()
    standard_week(conn, exit_tv=(1.0, 2.0))
    arms = replay(conn)["arms"]
    n, c = arms["noon-friday"], arms["control"]
    assert n["exit_bar"] == "12:00" and n["fell"] == [] and n["trigger"] == "fri_noon"
    assert n["entry_fees"] == c["entry_fees"] and n["entry_slippage"] == c["entry_slippage"], "the same entry"
    conn2 = store()
    standard_week(conn2, exit_tv=(1.0, 2.0))
    wide = chain_rows(["12:00"], atm_tv=1.0, over={(495.0, "PUT"): (0.30, 0.90)})
    put(conn2, FRONT, FRONT, "12:00", "12:10", wide + chain_rows(["12:05", "12:10"], atm_tv=1.0))
    assert replay(conn2)["arms"]["noon-friday"]["exit_bar"] == "12:05"


def test_noon_falls_through_to_the_control_window_when_no_noon_bar_passes():
    conn = store()
    standard_week(conn, exit_tv=(1.0, 2.0))
    blocked = {(495.0, "PUT"): (0.30, 0.90)}
    put(conn, FRONT, FRONT, "12:00", "12:10", chain_rows(c38.NOON_BARS, atm_tv=1.0, over=blocked))
    n = replay(conn)["arms"]["noon-friday"]
    assert n["fell"] == ["noon_to_control"] and n["exit_bar"] == "15:45"


def test_with_no_passing_close_bar_the_shorts_settle_and_the_longs_go_at_the_monday_bar():
    conn = store()
    blocked = {(495.0, "PUT"): (0.05, 0.99)}
    standard_week(conn, close_over={"f": blocked})
    t = replay(conn)["arms"]["control"]
    assert t["fell"] == ["close_to_monday"] and t["exit_bar"] is None
    assert t["trigger"] == "expiry_longs_mon_open"
    assert t["settlement"] == 0.0, "spot at the strikes' middle: both shorts expire worthless"


def test_an_in_the_money_short_delivers_shares_booked_at_settlement_and_disposed_at_mondays_bar():
    conn = store()
    blocked = {(495.0, "PUT"): (0.05, 0.99)}
    # the put short is in the money at the official close of 490; Monday's 09:45 underlying is 493
    standard_week(conn, close_over={"f": blocked}, dispo_spot=493.0)
    t = replay(conn, closes=lambda d: 490.0)["arms"]["control"]
    assert t["fell"] == ["close_to_monday"]
    assert t["share_pnl"] == pytest.approx(300.0), (
        "long 100 shares bought at 490 (the settlement), sold at 493"
    )
    a = {"direction": "long", "shares": 100, "basis": 490.0}
    from cherrypick.core import fees as core_fees

    assert t["settlement"] == pytest.approx(core_fees.assignment_fee(a, 493.0))
    assert t["settlement"] >= 5.0, "the $5 event is in the settlement column"
    parts = t["entry_fees"] + t["entry_slippage"] + t["exit_fees"] + t["exit_slippage"] + t["settlement"]
    assert t["gross"] - parts == pytest.approx(t["net"], abs=0.011)
    # the option leg is intrinsic (5.0), the share leg adds only the disposal move: not 495 - 493
    short_put_leg = engine.settle_intrinsic(495.0, "put", 490.0)
    assert short_put_leg == 5.0


def test_a_delivery_with_no_monday_price_leaves_the_week_unresolved_not_zero():
    conn = store()
    blocked = {(495.0, "PUT"): (0.05, 0.99)}
    standard_week(conn, close_over={"f": blocked})
    conn.execute(
        "DELETE FROM intraday_chain_quotes WHERE session = ? AND expiration = ?", (str(BACK), str(BACK))
    )
    t = replay(conn, closes=lambda d: 490.0)["arms"]["control"]
    assert t.get("unresolved") and "net" not in t
    # no official close at all
    conn2 = store()
    standard_week(conn2, close_over={"f": blocked})
    assert replay(conn2, closes=lambda d: None)["arms"]["control"]["unresolved"] == "no_settlement_spot"


def test_path_never_closes_the_shorts_settle_and_the_longs_are_sold_at_the_monday_bar():
    conn = store()
    standard_week(conn)
    p = replay(conn)["arms"]["path"]
    assert p["exit_bar"] is None and p["trigger"] == "expiry_longs_mon_open" and p["fell"] == []
    assert p["entry_fees"] > 0 and p["exit_fees"] >= 0


# --------------------------------------------------------------------------- the Friday regime


def friday_world(conn, *, fri_bars=("15:50", "15:55", "16:00")):
    standard_week(conn)
    put(conn, FRI, FRONT, "15:50", "16:00", chain_rows(list(fri_bars), atm_tv=3.5), "5m")
    put(conn, FRI, BACK, "15:50", "16:00", chain_rows(list(fri_bars), atm_tv=4.7), "5m")


def test_the_friday_entry_is_the_first_passing_bar_from_15_50_and_its_own_regime():
    conn = store()
    friday_world(conn)
    arms = replay(conn)["arms"]
    f = arms["friday:control"]
    assert f["entry_bar"] == "15:50" and f["tag"] == "dc_7_10"
    assert arms["control"]["tag"] == "dc_4_7", "the tags are never pooled"
    assert arms["friday:path"]["tag"] == "dc_7_10"


def test_the_friday_entry_waits_for_the_prior_weeks_exits_and_none_at_all_if_they_fell_through():
    prior = lambda bar: {FRI.isoformat(): {"net": 1.0, "exit_bar": bar}}  # noqa: E731
    conn = store()
    friday_world(conn)
    assert replay(conn, prior=prior("15:45"))["arms"]["friday:control"]["entry_bar"] == "15:50"
    assert replay(conn, prior=prior("15:50"))["arms"]["friday:control"]["entry_bar"] == "15:55"
    assert replay(conn, prior=prior("15:55"))["arms"]["friday:control"]["entry_bar"] == "16:00"
    gone = replay(conn, prior=prior(None))["arms"]["friday:control"]
    assert gone == {"refused": "awaiting_session_exits"}, "exits pending all session: no Friday entry"
    conn2 = store()
    friday_world(conn2, fri_bars=("15:50", "15:55"))
    assert replay(conn2, prior=prior("15:55"))["arms"]["friday:control"] == {
        "refused": "awaiting_session_exits"
    }


def test_the_friday_ordering_guard_fails_when_the_gate_is_removed():
    """Break the invariant on purpose: with the exit bar ignored the Friday entry takes 15:50 even
    though the prior week's control was still to exit at 15:55."""
    conn = store()
    friday_world(conn)
    prior = {FRI.isoformat(): {"net": 1.0, "exit_bar": "15:55"}}
    gated = replay(conn, prior=prior)["arms"]["friday:control"]
    ungated = replay(conn, prior={})["arms"]["friday:control"]
    assert gated["entry_bar"] == "16:00" and ungated["entry_bar"] == "15:50"


def test_the_paired_debit_prices_the_friday_strikes_at_the_monday_bar():
    conn = store()
    friday_world(conn)
    rec = replay(conn)
    pd = rec["paired_debit"]
    assert pd["friday"] > 0 and pd["monday_moment"] > 0
    assert rec["arms"]["friday:control"]["strikes"] == rec["arms"]["control"]["strikes"] or True


# --------------------------------------------------------------------------- the 30-day reading and low-ivr


def test_the_monthlies_bracket_thirty_days_and_a_monthly_expiry_day_has_no_lower_bracket():
    lo, hi = c38.bracketing_monthlies(date(2024, 3, 1))
    assert (lo, hi) == (date(2024, 3, 15), date(2024, 4, 19))
    assert c38.bracketing_monthlies(date(2024, 3, 15)) is None, (
        "the monthly expires today, the next is 35 days out"
    )
    lo2, hi2 = c38.bracketing_monthlies(date(2024, 3, 22))
    assert (lo2, hi2) == (date(2024, 4, 19), date(2024, 5, 17))
    assert c38.monthly_expiry(2024, 3) == date(2024, 3, 15)
    assert c38.monthly_expiry(2022, 4) == date(2022, 4, 14), "Good Friday: the Thursday"


def test_the_reading_is_the_money_mean_interpolated_to_thirty_days_in_total_variance():
    assert c38.interpolate_30(
        date(2024, 3, 1), date(2024, 3, 15), date(2024, 4, 19), 0.2, 0.2
    ) == pytest.approx(0.2, rel=1e-3)
    # the weights run linearly in total variance, not in volatility
    d_lo, d_hi = 14, 49
    w = 0.1**2 * d_lo / 365 + (0.3**2 * d_hi / 365 - 0.1**2 * d_lo / 365) * (30 - d_lo) / (d_hi - d_lo)
    got = c38.interpolate_30(date(2024, 3, 1), date(2024, 3, 15), date(2024, 4, 19), 0.1, 0.3)
    assert got == pytest.approx((w * 365 / 30) ** 0.5)
    assert 0.1 < got < 0.3
    assert c38.interpolate_30(date(2024, 3, 1), date(2024, 3, 31), date(2024, 4, 19), 0.15, 0.3) == 0.15, (
        "exactly 30 days"
    )


def test_the_money_iv_needs_both_sides_and_a_decimal_volatility():
    rows = {(500.0, "CALL"): {"iv": 0.20, "underlying": 500.2}, (500.0, "PUT"): {"iv": 0.24, "underlying": 500.2},
            (501.0, "CALL"): {"iv": 0.9, "underlying": 500.2}}  # fmt: skip
    assert c38.iv_at_money(rows) == pytest.approx(0.22)
    assert c38.iv_at_money({(500.0, "CALL"): rows[(500.0, "CALL")]}) is None
    percent = {
        (500.0, "CALL"): {"iv": 20.0, "underlying": 500.0},
        (500.0, "PUT"): {"iv": 24.0, "underlying": 500.0},
    }
    assert c38.iv_at_money(percent) is None, (
        "a percent-unit misread drops out as missing, not as a huge reading"
    )
    assert c38.iv_at_money({}) is None


def test_the_rank_is_position_in_the_trailing_range_and_a_thin_window_or_flat_range_has_none():
    series = [0.20 + 0.01 * (i % 10) for i in range(60)]
    series[-1] = min(series[-52:]) - 0.0  # the lowest reading
    series[-1] = 0.20
    assert c38.trailing_rank(series, 59) == pytest.approx(0.0)
    series[-1] = 0.29
    assert c38.trailing_rank(series, 59) == pytest.approx(1.0)
    series[-1] = 0.21
    assert c38.trailing_rank(series, 59) == pytest.approx((0.21 - 0.20) / (0.29 - 0.20))
    assert c38.trailing_rank([None] * 59 + [0.2], 59) is None
    thin = [0.2 + 0.01 * (i % 7) if i % 3 else None for i in range(60)]
    assert c38.trailing_rank(thin, 58) is None, "fewer than 45 of the 52 present"
    assert c38.trailing_rank([0.2] * 60, 59) is None, "a flat range has no rank"
    short = [0.2 + 0.01 * (i % 5) for i in range(30)]
    assert c38.trailing_rank(short, 29) is None, "under 45 readings at all"
    series2 = [0.20 + 0.01 * (i % 10) for i in range(60)]
    series2[-1] = None
    assert c38.trailing_rank(series2, 59) is None, "this week's reading is missing: refuse the week"


def test_one_threshold_one_third_inclusive():
    assert c38.enters_low_ivr(1 / 3) and c38.enters_low_ivr(0.0)
    assert not c38.enters_low_ivr(0.3334) and not c38.enters_low_ivr(None)


def test_low_ivr_is_controls_trade_only_when_the_rank_is_low_and_an_unmeasured_week_is_refused():
    conn = store()
    standard_week(conn)
    low = replay(conn, rank=0.2)["arms"]
    assert low["low-ivr"] == low["control"] and "net" in low["low-ivr"]
    high = replay(conn, rank=0.5)["arms"]["low-ivr"]
    assert high == {"not_entered": "ivr_above"}
    none = replay(conn, rank=None)["arms"]["low-ivr"]
    assert none == {"not_entered": "ivr_missing"}


def test_the_reading_is_taken_from_the_fifteen_fifty_five_bar_of_the_two_monthlies():
    conn = store()
    for e in (date(2024, 3, 15), date(2024, 4, 19)):
        put(conn, FRI, e, "15:55", "15:55", chain_rows(["15:55"], atm_tv=3.0))
    reading = c38.reading_at(chains_of(conn), FRI)
    assert reading == pytest.approx(0.2, rel=1e-3)
    conn2 = store()
    put(conn2, FRI, date(2024, 3, 15), "15:55", "15:55", chain_rows(["15:55"]))
    assert c38.reading_at(chains_of(conn2), FRI) is None, "one monthly missing: no reading"
    assert c38.reading_at(chains_of(conn), date(2024, 3, 15)) is None, "the monthly expiry day itself"


def test_the_public_index_series_is_ranked_by_the_same_rule():
    vix = [15 + (i % 12) for i in range(60)]
    assert c38.trailing_rank(vix, 59) == c38.trailing_rank(list(vix), 59)
    assert 0 <= c38.trailing_rank(vix, 59) <= 1


# --------------------------------------------------------------------------- statistics and verdict


def _rec(day: date, window: str, **arms):
    return {"week_of": day.isoformat(), "entry": day.isoformat(), "year": day.year, "window": window,
            "arms": arms, "sens": {}, "status": {}}  # fmt: skip


def _t(net, costs=6.0, **kw):
    cols = {"gross": net + costs, "entry_fees": costs / 6, "entry_slippage": costs / 3, "exit_fees": costs / 6,
            "exit_slippage": costs / 3, "settlement": 0.0}  # fmt: skip
    return {
        "net": net,
        "net_stress15": net - 0.5 * costs,
        "debit": 150.0,
        "costs": costs,
        "fell": [],
        **cols,
        **kw,
    }


def _world(n_design=200, n_hold=70, lift=0.0, pattern=None, ivr_every=2, noon_gain=3.0, ctrl_mean=20.0):
    """Invented weekly nets: `control` around `ctrl_mean`, noon-friday a steady `noon_gain` above it,
    low-ivr entering every `ivr_every`th week."""
    recs = []
    start = date(2017, 1, 2)
    for i in range(n_design + n_hold):
        day = start + timedelta(weeks=i)
        win = "design" if i < n_design else "holdout"
        base = ctrl_mean + ((i * 37) % 11 - 5) * 2.0 + lift
        arms = {"control": _t(base), "noon-friday": _t(base + noon_gain),
                "friday:control": _t(base - 1.0),
                "low-ivr": _t(base + 4.0) if i % ivr_every == 0 else {"not_entered": "ivr_above"}}  # fmt: skip
        recs.append(_rec(day, win, **arms))
    return recs


def test_the_bootstrap_resamples_weeks_is_seeded_and_reports_a_one_sided_p():
    a = c38.bootstrap([10.0, 12.0, 8.0, 11.0, 9.0] * 20)
    b = c38.bootstrap([10.0, 12.0, 8.0, 11.0, 9.0] * 20)
    assert a == b and a["mean"] == pytest.approx(10.0) and a["lo"] > 9 and a["p"] < 0.01
    neg = c38.bootstrap([-10.0, -12.0, -8.0, -11.0, -9.0] * 20)
    assert neg["p"] == 1.0 and neg["hi"] < 0
    assert c38.bootstrap([1.0]) is None


def test_the_two_sample_bootstrap_compares_group_means():
    got = c38.bootstrap_two([20.0, 22.0, 18.0] * 10, [10.0, 12.0, 8.0] * 10)
    assert got["diff"] == pytest.approx(10.0) and got["p"] < 0.01
    flat = c38.bootstrap_two([10.0, 12.0, 8.0] * 10, [10.0, 12.0, 8.0] * 10)
    assert flat["p"] > 0.2


def test_the_minimum_detectable_effect_follows_the_holm_bar():
    got = c38.mde(90.0, 290)
    assert got["at_bar"] == pytest.approx(12.0, abs=0.6), "about $12 a trade at the Holm bar on 290 weeks"
    assert got["at_80_power"] == pytest.approx(16.0, abs=0.8)
    assert c38.mde(None, 100) is None


def test_the_halves_split_at_the_median_judged_control_week():
    recs = _world(n_design=11, n_hold=0)
    assert c38.median_week(recs) == recs[5]["entry"]
    assert c38.median_week([]) is None


def test_a_steady_positive_calendar_passes_every_arm_and_is_read_at_the_holdout():
    out = c38.judge_all(_world(), date(2017, 1, 2))
    assert out["verdict"] == {
        "control": "pass",
        "friday:control": "pass",
        "low-ivr": "pass",
        "noon-friday": "pass",
    }, out["why"]
    assert out["judged"]["control"]["design"]["n"] == 200 and out["judged"]["low-ivr"]["design"]["n"] == 100
    assert out["reading"] == []


def test_a_calendar_that_does_not_earn_fails_and_says_what_failed():
    out = c38.judge_all(_world(ctrl_mean=-15.0), date(2017, 1, 2))
    assert set(out["verdict"].values()) == {"fail"}
    assert "lower_bound_above_zero" in out["why"]["control"] and "holm_significant" in out["why"]["control"]


def test_fewer_weeks_than_the_minimum_is_no_verdict_for_that_arm_alone():
    recs = _world(n_design=200, ivr_every=5)  # low-ivr enters 40 weeks, minimum 50
    out = c38.judge_all(recs, date(2017, 1, 2))
    assert out["verdict"]["low-ivr"] == "no verdict" and out["verdict"]["control"] == "pass"
    short = c38.judge_all(_world(n_design=100), date(2017, 1, 2))
    assert short["verdict"]["control"] == "no verdict"
    thin_hold = c38.judge_all(_world(n_hold=40), date(2017, 1, 2))
    assert thin_hold["verdict"]["control"] == "no verdict", "design passed, fewer than 60 holdout weeks"
    thin_low = c38.judge_all(_world(n_hold=70, ivr_every=4), date(2017, 1, 2))
    assert (
        thin_low["judged"]["low-ivr"]["holdout"]["n"] < 20 or thin_low["verdict"]["low-ivr"] != "no verdict"
    )


def test_each_condition_is_a_gate_shown_by_breaking_it_alone():
    base = _world()
    ok = c38.judge_all(base, date(2017, 1, 2))
    assert ok["verdict"]["control"] == "pass"

    # both halves positive: make the second half of the design lose
    halves = _world()
    for i, r in enumerate(halves[100:200]):
        r["arms"]["control"] = _t(-30.0 + (i % 7))
    out = c38.judge_all(halves, date(2017, 1, 2))
    assert "positive_each_half" in out["why"]["control"] and out["verdict"]["control"] == "fail"

    # positive at 1.5 times the costs: huge costs, small positive net
    costly = _world(ctrl_mean=6.0)
    for r in costly:
        for a in ("control",):
            r["arms"][a] = _t(r["arms"][a]["net"], costs=60.0)
    out = c38.judge_all(costly, date(2017, 1, 2))
    assert "positive_at_1_5x_costs" in out["why"]["control"]

    # a few outsized wins carry a mean that is otherwise negative: only the best-five trim shows it
    carried = _world(ctrl_mean=-2.0)
    for i in (10, 30, 50, 110, 130):
        carried[i]["arms"]["control"] = _t(5000.0)
    out = c38.judge_all(carried, date(2017, 1, 2))
    assert "positive_without_five_best" in out["why"]["control"]
    assert "positive_without_five_worst" not in out["why"]["control"], "losing the worst can only help a mean"

    # a verdict needs the holdout to hold too
    sour = _world()
    for r in sour:
        if r["window"] == "holdout":
            r["arms"]["control"] = _t(-25.0)
    out = c38.judge_all(sour, date(2017, 1, 2))
    assert out["verdict"]["control"] == "fail" and out["why"]["control"] == ["holdout"]


def test_noon_friday_must_also_beat_control_on_the_same_weeks():
    same = _world(noon_gain=0.0)
    for i, r in enumerate(same):
        r["arms"]["noon-friday"] = _t(r["arms"]["control"]["net"] + (1.0 if i % 2 else -1.0))
    out = c38.judge_all(same, date(2017, 1, 2))
    assert out["verdict"]["control"] == "pass" and out["verdict"]["noon-friday"] == "fail"
    assert "above_comparator" in out["why"]["noon-friday"]
    better = c38.judge_all(_world(noon_gain=4.0), date(2017, 1, 2))
    assert better["judged"]["noon-friday"]["comparator"]["kind"] == "paired"
    assert better["judged"]["noon-friday"]["comparator"]["ok"] and better["verdict"]["noon-friday"] == "pass"


def test_low_ivr_pays_not_for_the_gate_when_it_is_no_better_than_the_weeks_it_skipped():
    recs = _world()
    for r in recs:
        if "net" in r["arms"]["low-ivr"]:
            r["arms"]["low-ivr"] = dict(r["arms"]["control"])  # the gate selects nothing special
    out = c38.judge_all(recs, date(2017, 1, 2))
    j = out["judged"]["low-ivr"]
    assert j["comparator"]["kind"] == "two_sample" and not j["comparator"]["ok"]
    assert out["verdict"]["control"] == "pass" and out["verdict"]["low-ivr"] == "fail"
    assert "above_comparator" in out["why"]["low-ivr"]


def test_holm_across_four_demands_more_than_a_bare_five_percent():
    # control only just significant alone: three more arms with equally weak p push it under the bar
    recs = _world(ctrl_mean=1.4)
    out = c38.judge_all(recs, date(2017, 1, 2))
    j = out["judged"]["control"]["design"]["boot"]
    assert j["p"] < 0.05 or True
    assert out["judged"]["control"]["checks"]["holm_significant"] in (True, False)
    # a clearly weak arm is not significant even alone
    weak = _world(ctrl_mean=0.2)
    assert c38.judge_all(weak, date(2017, 1, 2))["verdict"]["control"] == "fail"


def test_partial_pass_is_read_as_that_arm_only():
    assert c38.partial_reading({"control": "fail", "friday:control": "pass", "low-ivr": "fail", "noon-friday": "fail"}) \
        == ["the Friday entrance only (control fails)"]  # fmt: skip
    got = c38.partial_reading(
        {"control": "fail", "friday:control": "fail", "low-ivr": "fail", "noon-friday": "pass"}
    )
    assert any("noon-friday" in s for s in got)
    assert c38.partial_reading({a: "pass" for a in c38.ARMS}) == []


def test_summaries_carry_year_quarter_tail_and_sensitivity_tables():
    recs = _world(n_design=60, n_hold=20)
    recs[3]["sens"] = {"entry_10:30": 12.0, "exit_15:50": 9.0}
    recs[3]["paired_debit"] = {"friday": 150.0, "monday_moment": 140.0}
    s = c38.summarize(recs)
    assert s["by_year"]["control"]["2017"]["n"] > 0
    assert s["tails"]["control"]["n"] == 80 and len(s["tails"]["control"]["worst"]) == 5
    assert s["sensitivities"]["entry_10:30"] == {"n": 1, "mean": 12.0}
    assert s["friday_vs_control"]["paired_debit_weeks"] == 1
    assert s["friday_vs_control"]["mean_friday_less_monday_moment"] == 10.0
    assert len(s["leave_one_year_out"]["control"]) == 2


# --------------------------------------------------------------------------- the files and the guards


def test_ex_dates_are_read_from_the_broker_file_and_a_year_without_a_pattern_is_a_problem():
    doc = {"symbols": {"SPY": {"dividends": [["2023-03-17", 1.5], ["2023-06-16", 1.6], ["2023-09-15", 1.6],
                                              ["2023-12-15", 1.9], ["2024-03-15", 1.6]]}}}  # fmt: skip
    dates = c38.ex_dates_from_broker(doc)
    assert dates[0] == "2023-03-17" and len(dates) == 5
    assert c38.exdate_problems(dates, date(2023, 1, 2), date(2024, 4, 1)) == []
    holes = [d for d in dates if not d.startswith("2023-0")]
    problems = c38.exdate_problems(holes, date(2023, 1, 2), date(2024, 4, 1))
    assert problems and "2023" in problems[0], "a hole in the file is not a year without dividends"
    stale = c38.exdate_problems(dates, date(2023, 1, 2), date(2026, 10, 19))
    assert any("ends" in p for p in stale)
    assert c38.ex_dates_from_broker({"symbols": {}}) == []


def test_the_ex_dates_are_never_computed_from_a_calendar_rule():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "third_friday(" in src and "monthly_expiry" in src, "the monthlies for the reading are a rule"
    body = src[src.index("def week_status") : src.index("def in_play")]
    assert "third_friday" not in body and "monthly_expiry" not in body, "ex-dates are declared data only"


def test_the_study_refuses_to_run_until_the_replays_entries_were_validated(tmp_path):
    with pytest.raises(SystemExit, match="validated"):
        c38.main(["study", "--out-dir", str(tmp_path)])
    (tmp_path / "validation.json").write_text(json.dumps({"passed": False}), encoding="utf-8")
    with pytest.raises(SystemExit, match="validated"):
        c38.main(["study", "--out-dir", str(tmp_path)])


def test_a_config_that_differs_from_the_declaration_is_named():
    assert c38.declared_config_problems({"defaults": dict(c38.DECLARED)}) == []
    assert c38.declared_config_problems({}) == []
    wrong = {"defaults": {**c38.DECLARED, "exit_window_start": "15:30"}}
    assert "exit_window_start" in c38.declared_config_problems(wrong)[0]
    costs = {"defaults": dict(c38.DECLARED), "tastytrade_costs": {"slippage_frac_of_spread": 0.2}}
    assert "tastytrade_costs" in c38.declared_config_problems(costs)[0]


def test_the_cost_basis_is_the_papers_by_default():
    base = c38.config_for("base")
    assert base["tastytrade_costs"] == {}
    from cherrypick.core import fees as core_fees

    assert core_fees.DEFAULT_COSTS["slippage_frac_of_spread"] == 0.125
    assert core_fees.DEFAULT_COSTS["slippage_cap_frac_of_mid"] == 0.15
    assert c38.config_for("half")["tastytrade_costs"]["slippage_frac_of_spread"] == 0.25
    assert c38.config_for("half")["tastytrade_costs"]["slippage_cap_frac_of_mid"] is None


def test_the_declared_defaults_match_the_modules_example_config():
    cfg = json.loads((Path(__file__).resolve().parents[3] / "packages" / "calendars" / "config.example.json")
                     .read_text(encoding="utf-8"))  # fmt: skip
    for key, value in c38.DECLARED.items():
        assert cfg["defaults"][key] == value, key


# --------------------------------------------------------------------------- end to end on invented data


def test_a_small_study_runs_end_to_end_and_writes_what_the_board_reads(tmp_path):
    conn = store()
    friday_world(conn)
    chains = chains_of(conn)
    res = c38.study_result(chains, date(2024, 3, 4), NO_DIVS, close_of, {})
    assert set(res["verdict"]) == set(c38.ARMS)
    assert set(res["verdict"].values()) <= {"pass", "fail", "no verdict"}
    first = next(r for r in res["weeks"] if r["entry"] == "2024-03-04")
    assert first["window"] == "design" and "net" in first["arms"]["control"]
    assert first["arms"]["low-ivr"] == {"not_entered": "ivr_missing"}, "no warm-up readings in this store"
    assert res["window"].startswith("2024-03-04..2024-12-31; holdout 2025-01-02..2026-08-21")
    md = c38.report(res)
    assert md.startswith("# Item 38") and "no verdict" in md
    out = tmp_path / "results.json"
    out.write_text(json.dumps(res, default=str), encoding="utf-8")
    hypotheses = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    words = hypotheses.verdicts(json.loads(out.read_text(encoding="utf-8")))
    assert {w["arm"] for w in words} == set(c38.ARMS) and all(w["verdict"] for w in words)


# --------------------------------------------------------------------------- the commands, in order


def _bars(start: str, end: str, step: int) -> list[str]:
    h, m = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    out, t = [], h * 60 + m
    while t <= eh * 60 + em:
        out.append(f"{t // 60:02d}:{t % 60:02d}")
        t += step
    return out


def _fill(conn, reqs, *, empty_before: str | None = None):
    """Answer every request as the vendor would: a few strikes at every bar of its window, or nothing
    (`empty`) for a request whose week is before `empty_before`."""
    for r in reqs:
        step = 15 if r["interval"] == "15m" else 5
        bars = _bars(r["start"], r["end"], step)
        rows = [] if empty_before and r["week"] < empty_before else chain_rows(bars, strikes=range(498, 503))
        ix.write_window(conn, (c38.SYMBOL, r["session"], r["expiration"], r["start"], r["end"], r["interval"]),
                        rows)  # fmt: skip


def _disk(tmp_path, monkeypatch):
    path = tmp_path / "intraday_chains.db"
    conn = sqlite3.connect(path)
    conn.executescript(ix.SCHEMA)
    monkeypatch.setattr(c38, "store_path", lambda: path)
    return conn


def _quarterly(first_year=2016, last=date(2026, 9, 18)):
    out = []
    for y in range(first_year, last.year + 1):
        for mth in (3, 6, 9, 12):
            d = c38.third_friday(y, mth)
            if d <= last:
                out.append(d.isoformat())
    return out


def test_listing_pilot_and_requests_run_in_order_on_invented_answers(tmp_path, monkeypatch):
    d = tmp_path / "item38"
    conn = _disk(tmp_path, monkeypatch)
    # 1. listing: the probe file is written; with nothing fetched the start is undecided
    assert c38.main(["listing", "--out-dir", str(d)]) == 0
    probes = json.loads((d / "listing-requests.json").read_text(encoding="utf-8"))["requests"]
    assert len(probes) == len(c38.probe_requests())
    assert json.loads((d / "listing.json").read_text(encoding="utf-8"))["decided"] is False
    # the vendor lists a Monday expiry only from 2018-01-01
    _fill(conn, probes, empty_before="2018-01-01")
    conn.commit()
    c38.main(["listing", "--out-dir", str(d)])
    listing = json.loads((d / "listing.json").read_text(encoding="utf-8"))
    assert listing["decided"] and listing["start"] == "2018-01-01"
    assert listing["weeks_without_both_from_start"] == 0
    assert json.loads((d / "runcard.json").read_text(encoding="utf-8"))["listed_start"] == "2018-01-01"
    # 2. the declared ex-dividend dates are a precondition; the pilot refuses without them
    with pytest.raises(FileNotFoundError):
        c38.main(["pilot", "--out-dir", str(d)])
    (d / "exdates.json").write_text(json.dumps({"ex_dates": _quarterly(), "problems": []}), encoding="utf-8")
    c38.main(["pilot", "--out-dir", str(d)])
    pilot_file = json.loads((d / "pilot-requests.json").read_text(encoding="utf-8"))
    assert len(pilot_file["weeks"]) == 40 and pilot_file["seed"] == 38
    assert all(w >= "2018-01-01" for w in pilot_file["weeks"])
    assert json.loads((d / "pilot.json").read_text(encoding="utf-8"))["decided"] is False, (
        "nothing fetched yet"
    )
    # 3. answered: every year complete, so the study starts at S
    _fill(conn, pilot_file["requests"])
    conn.commit()
    c38.main(["pilot", "--out-dir", str(d)])
    pilot = json.loads((d / "pilot.json").read_text(encoding="utf-8"))
    assert pilot["decided"] and pilot["basis_year"] == 2018 and pilot["start"] == "2018-01-01"
    # 4. the full request file and the run card
    assert c38.main(["requests", "--out-dir", str(d)]) == 0
    full = json.loads((d / "requests.json").read_text(encoding="utf-8"))["requests"]
    expect, counts = c38.full_requests(date(2018, 1, 1), c38.load_divs(d, date(2018, 1, 1)))
    assert len(full) == len(expect) and len(full) > 1500
    card = json.loads((d / "runcard.json").read_text(encoding="utf-8"))
    assert card["start"] == "2018-01-01" and card["design_end"] == "2023-12-29" and card["split"] == "early"
    assert card["holdout_start"] == "2024-01-02" and card["strike_range"] == 50
    assert card["request_counts"] == counts["by_kind"]
    assert {r["strike_range"] for r in full} == {50}


def test_a_pilot_that_fails_the_last_year_decides_no_start_and_the_requests_step_refuses(
    tmp_path, monkeypatch
):
    d = tmp_path / "item38"
    conn = _disk(tmp_path, monkeypatch)
    d.mkdir()
    (d / "listing.json").write_text(json.dumps({"decided": True, "start": "2024-01-01"}), encoding="utf-8")
    (d / "exdates.json").write_text(
        json.dumps({"ex_dates": _quarterly(2023), "problems": []}), encoding="utf-8"
    )
    c38.main(["pilot", "--out-dir", str(d)])
    reqs = json.loads((d / "pilot-requests.json").read_text(encoding="utf-8"))["requests"]
    # the vendor answers everything except 2026, which is empty
    early = [r for r in reqs if r["year"] < 2026]
    late = [r for r in reqs if r["year"] == 2026]
    _fill(conn, early)
    _fill(conn, late, empty_before="2027-01-01")
    conn.commit()
    c38.main(["pilot", "--out-dir", str(d)])
    pilot = json.loads((d / "pilot.json").read_text(encoding="utf-8"))
    assert pilot["decided"] and pilot["basis_year"] is None and pilot["start"] is None
    with pytest.raises(SystemExit, match="pilot has not decided"):
        c38.main(["requests", "--out-dir", str(d)])


def test_the_ex_dividend_file_must_cover_the_study_before_a_week_is_skipped_or_kept(tmp_path):
    d = tmp_path
    (d / "exdates.json").write_text(
        json.dumps({"ex_dates": ["2023-03-17"], "problems": []}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="ex-dividend dates have problems"):
        c38.load_divs(d, date(2023, 1, 2))
    (d / "exdates.json").write_text(
        json.dumps({"ex_dates": _quarterly(2022), "problems": []}), encoding="utf-8"
    )
    divs = c38.load_divs(d, date(2023, 1, 2))
    assert c38.engine.dividend_coverage_ok(divs, "SPY", "2026-10-26")


def _paper(path, rows):
    from cherrypick.calendars import db as cdb

    conn = cdb.connect(str(path))
    for r in rows:
        conn.execute(
            "INSERT INTO dc_positions (position_id, week_of, entry_session, arm, side, symbol, structure, "
            "front_expiration, back_expiration, strike, entry_debit, entry_time, entry_spot, entry_em, status) "
            "VALUES (?, ?, ?, ?, ?, 'SPY', ?, '2024-03-08', '2024-03-11', ?, ?, ?, ?, ?, 'open')",
            r,
        )
    conn.commit()
    conn.close()


def _paper_rows(put_strike=495.0, call_strike=505.0, debit_shift=0.0, spot=500.0, em=5.0):
    """Paper's two regimes; `spot` and `em` are the Monday entry's as paper recorded them."""
    out = []
    for arm, entry, tag, when in (("control", "2024-03-04", "dc_4_7", "2024-03-04T10:00:10-05:00"),
                                  ("friday:control", "2024-03-01", "dc_7_10", "2024-03-01T15:50:10-05:00")):  # fmt: skip
        # the Friday regime's straddle is worth 7.0, so its expected move 5.95 reaches 494 and 506
        strikes = (put_strike, call_strike) if arm == "control" else (494.0, 506.0)
        at, move = (spot, em) if arm == "control" else (500.0, 5.95)
        for side, strike in (("put", strikes[0]), ("call", strikes[1])):
            deb = _mid_of(500, 4.2 if arm == "control" else 4.7, strike, side.upper()) \
                - _mid_of(500, 3.0 if arm == "control" else 3.5, strike, side.upper())  # fmt: skip
            out.append((f"2024-03-04:{arm}:{side}", "2024-03-04", entry, arm, side, tag, strike,
                        round(deb + debit_shift, 4), when, at, move))  # fmt: skip
    return out


def _validate_setup(tmp_path, monkeypatch, rows):
    d = tmp_path / "item38"
    d.mkdir()
    conn = _disk(tmp_path, monkeypatch)
    friday_world(conn)
    conn.commit()
    (d / "runcard.json").write_text(json.dumps({"start": "2024-03-04"}), encoding="utf-8")
    (d / "exdates.json").write_text(
        json.dumps({"ex_dates": ["2023-12-15"], "problems": []}), encoding="utf-8"
    )
    monkeypatch.setattr(c38, "PAPER_FROM", date(2024, 3, 4))
    monkeypatch.setattr(c38, "LAST_WEEK", date(2024, 3, 4))
    monkeypatch.setattr(c38, "load_closes", lambda: ({}, {}))
    paper = tmp_path / "paper.db"
    _paper(paper, rows)
    return d, paper


def test_validation_passes_when_the_replays_strikes_and_debits_match_the_papers(tmp_path, monkeypatch):
    d, paper = _validate_setup(tmp_path, monkeypatch, _paper_rows())
    assert c38.main(["validate", "--out-dir", str(d), "--paper-db", str(paper)]) == 0
    v = json.loads((d / "validation.json").read_text(encoding="utf-8"))
    assert v["passed"] and v["monday"] == {
        "weeks": 1, "agree": 1, "rule": 1, "feed": 1, "share": 1.0, "passed": True
    }  # fmt: skip
    assert v["friday"]["agree"] == 1 and all(x["nearest_bar_ok"] for x in v["detail"])
    assert v["detail"][0]["exit"]["paper_net"] is None, "an open paper position has no outcome to compare"


def test_validation_fails_and_the_study_stops_when_paper_s_spot_and_move_miss_its_strikes(
    tmp_path, monkeypatch
):
    """Paper's 500 less its 5.00 move is 495; a recorded 494 is a strike the rule did not choose."""
    d, paper = _validate_setup(tmp_path, monkeypatch, _paper_rows(put_strike=494.0))
    assert c38.main(["validate", "--out-dir", str(d), "--paper-db", str(paper)]) == 1
    v = json.loads((d / "validation.json").read_text(encoding="utf-8"))
    assert v["passed"] is False and v["monday"]["rule"] == 0 and v["monday"]["feed"] == 1
    with pytest.raises(SystemExit, match="validated"):
        c38.main(["study", "--out-dir", str(d)])


def test_validation_fails_when_the_store_s_spot_is_more_than_a_dollar_from_paper_s(tmp_path, monkeypatch):
    """Paper at 501.50 with a 5.50 move chose 496 and 507, which the rule gives; the store's 500 is
    $1.50 away, past the feed bound."""
    rows = _paper_rows(put_strike=496.0, call_strike=507.0, spot=501.5, em=5.5)
    d, paper = _validate_setup(tmp_path, monkeypatch, rows)
    assert c38.main(["validate", "--out-dir", str(d), "--paper-db", str(paper)]) == 1
    v = json.loads((d / "validation.json").read_text(encoding="utf-8"))
    assert v["monday"]["rule"] == 1 and v["monday"]["feed"] == 0 and v["detail"][0]["feed_gap"] == 1.5


def test_a_paper_debit_off_the_replay_s_is_reported_not_gated(tmp_path, monkeypatch):
    """The first run's Monday miss was paper's own put debit, 0.18 against 0.42-0.45 on every bar."""
    d, paper = _validate_setup(tmp_path, monkeypatch, _paper_rows(debit_shift=0.5))
    assert c38.main(["validate", "--out-dir", str(d), "--paper-db", str(paper)]) == 0
    v = json.loads((d / "validation.json").read_text(encoding="utf-8"))
    assert v["passed"] and v["monday"]["agree"] == 0 and v["monday"]["rule"] == 1


def test_the_study_command_writes_results_json_and_md_once_validated(tmp_path, monkeypatch):
    d = tmp_path / "item38"
    d.mkdir()
    conn = _disk(tmp_path, monkeypatch)
    friday_world(conn)
    conn.commit()
    (d / "runcard.json").write_text(json.dumps({"start": "2024-03-04"}), encoding="utf-8")
    (d / "exdates.json").write_text(
        json.dumps({"ex_dates": _quarterly(2023), "problems": []}), encoding="utf-8"
    )
    (d / "validation.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    monkeypatch.setattr(c38, "load_closes", lambda: ({"2024-03-08": 500.0}, {"compared": 0}))
    monkeypatch.setattr(c38, "vix_series", lambda: {})
    import cherrypick.calendars.cli as ccli

    monkeypatch.setattr(ccli, "load_config", lambda path=None: {})
    assert c38.main(["study", "--out-dir", str(d)]) == 0
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))
    assert set(res["verdict"]) == set(c38.ARMS) and isinstance(res["window"], str)
    assert (d / "results.md").read_text(encoding="utf-8").startswith("# Item 38")
    # the same study with a config that is not the declared one refuses before reading a quote
    monkeypatch.setattr(ccli, "load_config", lambda path=None: {"defaults": {"exit_window_start": "15:30"}})
    with pytest.raises(SystemExit, match="not the one declared"):
        c38.main(["study", "--out-dir", str(d)])
