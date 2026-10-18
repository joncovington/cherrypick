"""The broker-chain recorder for the income screener lists: which options it asks for.

`scripts/fetch_screener_greeks.py` records, around each row of the vendor's lists, the strikes the
re-created rule will have to choose from. What it asks for is a pure function of the saved lists;
each test breaks one thing about a row and checks the request follows.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_screener_greeks.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_screener_greeks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fsg = _module()

LISTS = {
    "short-puts": {"shortPuts": [{"symbol": "AR", "expiry": "11/06/2026", "strikePrice": 35.0}]},
    "covered-calls": {"coveredCalls": [{"symbol": "AR", "expiry": "11/20/2026", "strikePrice": 45.0}]},
    "credit-spreads": {
        "creditSpreads": [
            {"symbol": "MU", "type": "Put", "expiry": "11/20/2026", "strike": {"sell": 1080.0, "buy": 990.0}},
            {
                "symbol": "AZO",
                "type": "Call",
                "expiry": "11/20/2026",
                "strike": {"sell": 2870.0, "buy": 3150.0},
            },
        ]
    },
}


def test_every_list_contributes_its_own_side_and_expiry():
    got = fsg.anchors(LISTS)
    assert got[("AR", "2026-11-06", "P")] == {35.0}
    assert got[("AR", "2026-11-20", "C")] == {45.0}
    assert got[("MU", "2026-11-20", "P")] == {1080.0, 990.0}
    assert got[("AZO", "2026-11-20", "C")] == {2870.0, 3150.0}


def test_a_row_without_a_numeric_strike_asks_for_nothing():
    bad = {"short-puts": {"shortPuts": [{"symbol": "AR", "expiry": "11/06/2026", "strikePrice": "35"}]}}
    assert fsg.anchors(bad) == {}


def test_the_window_is_the_listed_strikes_either_side_of_the_vendors():
    listed = [float(k) for k in range(20, 51)]
    assert fsg.strike_window(listed, {35.0}, width=2) == [33.0, 34.0, 35.0, 36.0, 37.0]


def test_the_window_stops_at_the_end_of_the_chain():
    assert fsg.strike_window([30.0, 31.0, 32.0], {30.0}, width=2) == [30.0, 31.0, 32.0]


def test_a_strike_the_broker_does_not_list_still_gets_its_neighbours():
    """A vendor strike missing from the broker's chain is itself a finding; record around it."""
    assert fsg.strike_window([30.0, 32.5, 35.0, 37.5], {33.0}, width=1) == [30.0, 32.5, 35.0]


def test_two_anchors_share_one_window():
    listed = [float(k) for k in range(0, 100, 10)]
    assert fsg.strike_window(listed, {10.0, 80.0}, width=1) == [0.0, 10.0, 20.0, 70.0, 80.0, 90.0]


def test_the_subscription_pacing_is_not_loosened():
    """DXLink kills a socket subscribed too fast: this script's first full run (2026-10-07, unpaced)
    was cut off with 'Your subscription rate is too high'."""
    assert fsg.BATCH <= 100
    assert fsg.SUBSCRIBE_PACE_S >= 1.0
    assert fsg.MIN_BATCH_S >= 3.0
    assert fsg.CHAIN_PAUSE_S >= 0.3
    assert fsg.RECONNECT_PAUSE_S >= 60


def test_the_wrapped_feed_error_is_reported_by_its_own_message():
    inner = RuntimeError("Fatal streamer error: Your subscription rate is too high")

    class Group(Exception):
        def __init__(self, *exceptions):
            super().__init__("unhandled errors in a TaskGroup")
            self.exceptions = exceptions

    wrapped = Group(Group(inner))
    assert fsg._innermost(wrapped) == str(inner)


def test_measure_skips_names_the_universe_already_measures_and_uses_its_spelling():
    lists = {
        "short-puts": {"shortPuts": [{"symbol": "BRK/B"}, {"symbol": "AAPL"}]},
        "covered-calls": {"coveredCalls": [{"symbol": "AAPL"}, {"symbol": "SLS"}]},
    }
    assert fsg.measure_names(lists, {"AAPL"}) == ["BRK.B", "SLS"]


# --- legs: the vendor's option against the user's monthly ------------------------------------

MONTHLIES = {"2026-11-20", "2026-12-18", "2027-01-15"}


def _is_monthly(iso):
    return iso in MONTHLIES


def _exp(iso, dte, strikes=((100.0, "C100", "P100"), (105.0, "C105", "P105"))):
    return {"expiration": iso, "dte": dte, "strikes": [list(s) for s in strikes]}


def test_the_cycle_prefers_a_monthly_45_to_60_days_out():
    exps = [_exp("2026-11-06", 30), _exp("2026-11-20", 44), _exp("2026-12-18", 50)]
    assert fsg.cycle_expiry(exps, _is_monthly)["expiration"] == "2026-12-18"


def test_the_cycle_falls_back_to_a_monthly_30_to_60_days_out():
    exps = [_exp("2026-11-06", 30), _exp("2026-11-20", 44), _exp("2026-12-18", 72)]
    assert fsg.cycle_expiry(exps, _is_monthly)["expiration"] == "2026-11-20"


def test_a_weekly_is_never_the_cycle_and_an_empty_range_is_none():
    assert fsg.cycle_expiry([_exp("2026-11-06", 45)], _is_monthly) is None
    assert fsg.cycle_expiry([_exp("2026-11-20", 29), _exp("2026-12-18", 64)], _is_monthly) is None


def test_income_names_are_the_tradable_verdicts_only():
    verdicts = {
        "AA": {"verdict": "tradable"},
        "A": {"verdict": "illiquid"},
        "BRK.B": {"verdict": "tradable"},
        "X": {},
    }
    assert fsg.income_names(verdicts) == ["AA", "BRK.B"]


def test_the_income_plan_is_the_cycle_monthly_both_sides_out_of_the_money():
    strikes = [(float(k), f"C{k}", f"P{k}") for k in range(80, 121, 5)]
    exps = [_exp("2026-11-06", 30, strikes), _exp("2026-12-18", 50, strikes)]
    plan = fsg.income_plan(exps, 101.0, _is_monthly)
    assert plan["expiry"] == "2026-12-18" and plan["dte"] == 50  # the monthly, never the nearer weekly
    puts = [leg["strike"] for leg in plan["legs"] if leg["right"] == "P"]
    calls = [leg["strike"] for leg in plan["legs"] if leg["right"] == "C"]
    assert puts and max(puts) <= 101.0 and min(puts) >= 101.0 * 0.8  # out of the money, within reach
    assert calls and min(calls) >= 101.0 and max(calls) <= 101.0 * 1.2
    assert {leg["option"] for leg in plan["legs"] if leg["strike"] == 95.0} == {"P95"}
    # No cycle monthly, or no price to centre on: no plan, never a weekly instead.
    assert fsg.income_plan([_exp("2026-11-06", 45, strikes)], 101.0, _is_monthly) is None
    assert fsg.income_plan(exps, None, _is_monthly) is None


def test_the_stress_move_is_the_names_bad_tail_over_the_holding_period():
    # A steady 1% a session up, with one 30% crash: the worst 5-session windows hold the crash.
    closes = [100.0 * 1.01**i for i in range(400)]
    closes = closes[:200] + [c * 0.7 for c in closes[200:]]
    s = fsg.stress_move(closes, 5)
    assert s["sessions"] == 5 and s["windows"] == 395
    assert s["p01"] < -0.25 < 0 < s["p99"]
    # Too little history to read a tail from is no reading, never a mild one.
    assert fsg.stress_move(closes[:100], 5) is None
    assert fsg.stress_move(closes, 0) is None


def test_a_holding_period_is_counted_in_sessions():
    assert fsg.holding_sessions(50) == 35 and fsg.holding_sessions(1) == 1


def test_the_income_metrics_are_the_recorded_fields_and_absent_ones_are_null():
    rows = {"AA": {"earnings_date": "2026-10-22", "iv_index": 0.49, "beta": 2.1, "liquidity_rank": 0.03}}
    m = fsg.income_metrics(rows, "AA")
    assert m["earnings_date"] == "2026-10-22" and m["beta"] == 2.1 and "liquidity_rank" not in m
    assert all(v is None for v in fsg.income_metrics(rows, "ZZ").values())


def test_the_option_symbol_is_the_brokers_padded_occ():
    assert fsg.option_symbol("VPG", "2026-11-20", "C", 105.0) == "VPG   261120C00105000"
    assert fsg.option_symbol("BRK.B", "2026-11-20", "P", 7.5) == "BRK/B 261120P00007500"


def test_a_credit_spread_has_a_short_and_a_long_leg():
    row = {"symbol": "MU", "type": "Put", "expiry": "11/20/2026", "strike": {"sell": 1080.0, "buy": 990.0}}
    legs = fsg.row_legs("credit-spreads", row)
    assert [(leg["role"], leg["right"], leg["strike"]) for leg in legs] == [
        ("short", "P", 1080.0),
        ("long", "P", 990.0),
    ]


def test_plan_skips_held_names_and_takes_the_nearest_monthly_strike():
    lists = {
        "short-puts": {
            "shortPuts": [
                {"symbol": "AAA", "expiry": "11/06/2026", "strikePrice": 103.0},
                {"symbol": "ILQ", "expiry": "11/06/2026", "strikePrice": 10.0},
            ]
        }
    }
    chains = {"AAA": [_exp("2026-11-06", 30), _exp("2026-12-18", 50)]}
    plan = fsg.plan_legs(lists, chains, {"ILQ"}, _is_monthly)
    assert [r["symbol"] for r in plan] == ["AAA"]
    m = plan[0]["monthly"][0]
    assert (m["expiry"], m["strike"], m["option"]) == ("2026-12-18", 105.0, "P105")
    assert plan[0]["vendor"][0]["option"] == "AAA   261106P00103000"


def test_a_name_without_a_cycle_monthly_keeps_its_vendor_leg_only():
    lists = {"short-puts": {"shortPuts": [{"symbol": "AAA", "expiry": "11/06/2026", "strikePrice": 103.0}]}}
    assert fsg.plan_legs(lists, {}, set(), _is_monthly)[0]["monthly"] is None


BARS = {"max_spread_pct": 0.10, "max_spread_abs": 0.05, "min_open_interest": 100, "min_volume": 10}
GOOD = {"bid": 1.00, "ask": 1.05, "open_interest": 500, "volume": 50}


def test_the_chosen_bars_are_the_ones_the_readings_picked():
    # Changing them changes what every recommendation means: do it with the readings, not by feel.
    assert fsg.LEG_BARS == BARS


def test_a_row_says_whether_its_monthly_passes_too():
    assert fsg.monthly_passes([GOOD], BARS) is True
    assert fsg.monthly_passes([GOOD, {**GOOD, "open_interest": 5}], BARS) is False
    assert fsg.monthly_passes(None, BARS) is None and fsg.monthly_passes([GOOD], None) is None


def test_apply_bars_judges_each_leg_and_the_row():
    rows = [
        {"vendor": [{"quote": {**GOOD, "open_interest": 3}}], "monthly": [{"quote": GOOD}]},
        {"vendor": [{"quote": GOOD}], "monthly": None},
        {"vendor": [{}], "monthly": None},
    ]
    fsg.apply_bars(rows, BARS)
    assert [r["recommendation"] for r in rows] == ["monthly", "vendor", "none"]
    assert rows[0]["vendor"][0]["passes"] is False and rows[0]["monthly"][0]["passes"] is True
    assert rows[0]["monthly_passes"] is True and rows[1]["monthly_passes"] is None
    assert rows[2]["vendor"][0]["passes"] is None  # unquoted: no verdict, not a failure


def test_no_recommendation_until_the_bars_are_chosen():
    assert fsg.recommend([GOOD], [GOOD], None) is None


def test_a_liquid_vendor_leg_is_recommended():
    assert fsg.recommend([GOOD], [GOOD], BARS) == "vendor"


def test_each_failing_input_sends_the_trade_to_the_monthly():
    for broken in ({"ask": 1.60}, {"open_interest": 20}, {"volume": 2}, {"bid": 0.0}):
        assert fsg.recommend([{**GOOD, **broken}], [GOOD], BARS) == "monthly", broken


def test_a_spread_passes_only_when_both_legs_do_and_no_monthly_is_none():
    assert fsg.recommend([GOOD, {**GOOD, "volume": 0}], [GOOD, GOOD], BARS) == "monthly"
    assert fsg.recommend([{**GOOD, "volume": 0}], None, BARS) == "none"


def test_a_cheap_option_passes_on_the_absolute_width():
    """$0.05 wide on a $0.20 option is 25% of mid but one nickel: the OR rule lets it through."""
    assert fsg.leg_passes({"bid": 0.18, "ask": 0.23, "open_interest": 500, "volume": 50}, BARS)


def test_a_rate_limited_run_is_incomplete_but_a_dead_symbol_is_not():
    # 2026-10-08: a 429 stamped the day complete, so no later run ever repaired it.
    limited = {
        "AAPL": {"error": None},
        "MSFT": {"error": fsg.RATE_LIMITED},
        "NVDA": {"error": fsg.RATE_LIMITED},
    }
    assert fsg.rate_limit_problem(limited) == "broker rate limit (429): 2 symbol(s) not fetched"
    delisted = {"AAPL": {"error": None}, "XYZ": {"error": "chain: no such symbol"}}
    assert fsg.rate_limit_problem(delisted) is None


# --------------------------------------------------------------------------- which day it records
def _store(tmp_path, monkeypatch, today, saved=(), recorded=()):
    """A store with lists saved for `saved` days and complete greeks files for `recorded` ones."""
    import json

    for day in saved:
        d = tmp_path / "vendor-screeners" / day
        d.mkdir(parents=True)
        (d / "short-puts.json").write_text(json.dumps({"body": LISTS["short-puts"]}), encoding="utf-8")
    for day in recorded:
        g = tmp_path / "screener-greeks"
        g.mkdir(parents=True, exist_ok=True)
        (g / f"{day}.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    monkeypatch.setattr(fsg, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(fsg, "today_et", lambda: today)


def test_a_late_capture_is_waited_for_not_mistaken_for_yesterdays(tmp_path, monkeypatch, capsys):
    """2026-10-15: today's lists landed at 22:46 ET; the 19:00 run took the newest on disk (10-14,
    already recorded), exited 0, and the session was lost. Now it names today, and waits."""
    _store(tmp_path, monkeypatch, "2026-10-15", saved=["2026-10-14"], recorded=["2026-10-14"])
    assert fsg.main([]) == 0
    assert "no saved screener lists for 2026-10-15 yet" in capsys.readouterr().out


def test_the_final_run_fails_when_todays_lists_never_came(tmp_path, monkeypatch, capsys):
    _store(tmp_path, monkeypatch, "2026-10-15", saved=["2026-10-14"], recorded=["2026-10-14"])
    assert fsg.main(["--final"]) == 1
    assert "2026-10-15" in capsys.readouterr().err


def test_the_final_run_leaves_a_recorded_day_alone(tmp_path, monkeypatch, capsys):
    """The evening run already recorded today: the late run is a no-op, never a second fetch."""
    _store(tmp_path, monkeypatch, "2026-10-15", saved=["2026-10-15"], recorded=["2026-10-15"])
    assert fsg.main(["--final"]) == 0
    assert "already exists" in capsys.readouterr().out


def test_a_recorded_day_without_its_candidates_records_them_alone(tmp_path, monkeypatch, capsys):
    """The rows' file is left as it is; only the missing candidates are fetched (no broker here, so
    the attempt stops at the credential -- what matters is that it was attempted, not skipped)."""
    _store(tmp_path, monkeypatch, "2026-10-15", saved=["2026-10-15"], recorded=["2026-10-15"])
    monkeypatch.setattr(fsg, "candidate_plan", lambda day: {"AAA": {"edge": "sb", "rights": ["P"]}})
    before = (tmp_path / "screener-greeks" / "2026-10-15.json").read_text(encoding="utf-8")

    class Stop(Exception):
        pass

    import cherrypick.core.auth as auth

    def refuse(*a, **k):
        raise Stop()

    monkeypatch.setattr(auth, "CredentialStore", refuse)
    try:
        fsg.main(["--final"])
    except Stop:
        pass
    assert "recording the spread candidates alone" in capsys.readouterr().out
    assert (tmp_path / "screener-greeks" / "2026-10-15.json").read_text(encoding="utf-8") == before
    # With the candidates recorded too, the day is done.
    c = tmp_path / "screener-greeks" / "candidates"
    c.mkdir()
    (c / "2026-10-15.json").write_text('{"complete": true}', encoding="utf-8")
    assert fsg.main(["--final"]) == 0
    assert "already exists; not overwritten" in capsys.readouterr().out


# --------------------------------------------------------------------------- spread candidates
def test_candidates_are_every_rank_1_name_with_options_on_the_side_its_read_implies(tmp_path, monkeypatch):
    import json

    d = tmp_path / "vendor-securities"
    d.mkdir()
    rows = [
        {"symbol": "BULL", "hasOptions": True, "liquidityRank": 1, "directionalEdge": "bp"},
        {"symbol": "BEAR", "hasOptions": True, "liquidityRank": 1, "directionalEdge": "sbe"},
        {"symbol": "FLAT", "hasOptions": True, "liquidityRank": 1, "directionalEdge": "mx"},
        {"symbol": "THIN", "hasOptions": True, "liquidityRank": 2, "directionalEdge": "sb"},
        {"symbol": "NOOP", "hasOptions": False, "liquidityRank": 1, "directionalEdge": "sb"},
    ]
    (d / "2026-10-16.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    (d / "2026-10-20.json").write_text(json.dumps({"rows": []}), encoding="utf-8")  # after the day: unused
    monkeypatch.setattr(fsg, "store_dir", lambda: tmp_path)
    plan = fsg.candidate_plan("2026-10-19")
    assert {s: p["rights"] for s, p in plan.items()} == {"BULL": ["P"], "BEAR": ["C"], "FLAT": ["P", "C"]}
    assert plan["BULL"]["securities"] == "2026-10-16"
    assert fsg.candidate_plan("2026-10-01") == {}


def test_candidates_follow_the_pointer_to_the_public_list_planned_on_both_sides(tmp_path, monkeypatch):
    """With the vendor off, `technicals securities` names the public list current; it carries no
    direction read, so a rank-1 name is planned on both sides."""
    import json

    for folder, sym in (("vendor-securities", "VEND"), ("securities", "PUBL")):
        (tmp_path / folder).mkdir()
        row = {"symbol": sym, "hasOptions": True, "liquidityRank": 1}
        (tmp_path / folder / "2026-10-16.json").write_text(json.dumps({"rows": [row]}), encoding="utf-8")
    pointer = {"source": "public", "day": "2026-10-16"}
    (tmp_path / "securities" / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(fsg, "store_dir", lambda: tmp_path)
    plan = fsg.candidate_plan("2026-10-16")
    assert plan == {
        "PUBL": {"edge": None, "rights": ["P", "C"], "securities": "2026-10-16", "source": "public"}
    }


def test_the_candidate_expiry_is_the_nearest_45_days_and_never_an_expired_one():
    exps = [("2026-10-16", 0), ("2026-11-20", 35), ("2026-11-27", 42), ("2026-12-18", 63)]
    assert fsg.nearest_dte(exps, 45) == ("2026-11-27", 42)
    assert fsg.nearest_dte([("2026-11-25", 40), ("2026-12-05", 50)], 45) == ("2026-11-25", 40)
    assert fsg.nearest_dte([("2026-10-16", 0)], 45) is None


def test_candidate_strikes_run_from_the_money_outward_and_thin_past_the_first_few():
    listed = [float(k) for k in range(60, 141)]  # $1 strikes, 60-140
    puts = fsg.candidate_strikes(listed, 100.0, "P")
    calls = fsg.candidate_strikes(listed, 100.0, "C")
    # The short leg's strike and its nearest neighbours, consecutive; at most 16; out to 20%.
    assert puts[-4:] == [97.0, 98.0, 99.0, 100.0] and len(puts) == 16 and min(puts) == 80.0
    assert calls[:4] == [100.0, 101.0, 102.0, 103.0] and len(calls) == 16 and max(calls) == 120.0
    # Few strikes in reach: all of them, none past 20%.
    assert fsg.candidate_strikes([90.0, 95.0, 100.0, 105.0, 125.0], 101.0, "C") == [105.0]
    assert fsg.candidate_strikes([70.0, 85.0, 95.0, 100.0], 101.0, "P") == [85.0, 95.0, 100.0]


# ------------------------------------------------------------------------------------------------
# The earnings calendar's suggested spreads (2026-10-17): target strikes onto listed ones, priced.

CHAIN = [[k, f"C{k:g}", f"P{k:g}"] for k in (90.0, 92.5, 95.0, 97.5, 100.0, 102.5, 105.0, 107.5, 110.0)]


def test_a_credit_put_spread_sells_at_the_money_and_buys_just_outside_the_move():
    """The long is the first listed strike at or below the target, never above it: 95.1 takes 95,
    94.9 takes 92.5 though 95 is nearer."""
    sug = {"kind": "credit", "right": "P", "short": 100.3, "long": 95.1}
    assert fsg.snap_spread(sug, CHAIN) == ([95.0, 100.0], ["P95", "P100"])
    sug = {"kind": "credit", "right": "P", "short": 100.3, "long": 94.9}
    assert fsg.snap_spread(sug, CHAIN) == ([92.5, 100.0], ["P92.5", "P100"])


def test_a_credit_call_spread_buys_the_first_strike_at_or_above_the_move():
    sug = {"kind": "credit", "right": "C", "short": 99.0, "long": 105.0}
    assert fsg.snap_spread(sug, CHAIN) == ([105.0, 100.0], ["C105", "C100"])


def test_a_debit_spread_takes_the_nearest_strikes_and_at_least_one_apart():
    sug = {"kind": "debit", "right": "C", "long": 100.4, "short": 106.0}
    assert fsg.snap_spread(sug, CHAIN) == ([100.0, 105.0], ["C100", "C105"])
    # A move smaller than a strike still buys one and sells the next one out.
    sug = {"kind": "debit", "right": "P", "long": 100.0, "short": 99.9}
    assert fsg.snap_spread(sug, CHAIN) == ([100.0, 97.5], ["P100", "P97.5"])


def test_a_spread_the_chain_cannot_hold_is_none():
    assert fsg.snap_spread({"kind": "credit", "right": "P", "short": 90.0, "long": 80.0}, CHAIN) is None
    assert fsg.snap_spread({"kind": "debit", "right": "C", "long": 110.0, "short": 115.0}, CHAIN) is None
    assert fsg.snap_spread({"kind": "credit", "right": "P", "short": 100.0, "long": 95.0}, []) is None


def test_a_spread_is_priced_at_mid_and_at_the_natural_fill():
    long_q, short_q = {"bid": 1.00, "ask": 1.20}, {"bid": 3.00, "ask": 3.20}
    credit = fsg.price_spread("credit", long_q, short_q, 7.5)
    assert credit == {"mid": 2.0, "natural": 1.8, "share_of_width": 0.2667, "max_loss": 550.0}
    debit = fsg.price_spread("debit", short_q, long_q, 5.0)  # long the dearer leg
    assert debit == {"mid": 2.0, "natural": 2.2, "share_of_width": 0.4, "max_loss": 200.0}
    assert fsg.price_spread("credit", {}, short_q, 7.5)["mid"] is None


def test_the_legs_are_judged_by_the_same_bars_as_the_lists():
    good = {"bid": 2.00, "ask": 2.10, "open_interest": 500, "volume": 50}
    thin = {"bid": 0.50, "ask": 0.90, "open_interest": 3, "volume": 0}
    rows = [
        {
            "kind": "credit",
            "width": 7.5,
            "legs": [
                {"role": "long", "quote": dict(good)},
                {"role": "short", "quote": dict(good, bid=4.0, ask=4.2)},
            ],
        },
        {
            "kind": "credit",
            "width": 7.5,
            "legs": [{"role": "long", "quote": thin}, {"role": "short", "quote": good}],
        },
        {"kind": "credit", "width": 7.5, "legs": [{"role": "long"}, {"role": "short", "quote": good}]},
        {"legs": None},
    ]
    fsg.judge_earnings_legs(rows, fsg.LEG_BARS)
    assert [r.get("passes") for r in rows] == [True, False, None, None]
    assert rows[0]["mid"] == 2.05 and rows[1]["legs"][0]["passes"] is False


def test_the_calendar_rows_priced_are_the_strong_ones_due_this_week(tmp_path, monkeypatch):
    import json
    from datetime import date

    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path))
    sug = {"kind": "credit", "right": "P", "short": 100.0, "long": 95.0, "expiry": "2026-11-20"}
    base = {"liquidity_rank": 1, "optionable": True, "region": "US", "suggestion": sug}
    rows = [
        dict(base, symbol="NOW", date="2026-10-20"),
        dict(base, symbol="LATER", date="2026-11-05"),  # past the week
        dict(base, symbol="THIN", date="2026-10-20", liquidity_rank=3),
        dict(base, symbol="NOEXP", date="2026-10-20", suggestion=dict(sug, expiry=None)),
        dict(base, symbol="NOSUG", date="2026-10-20", suggestion=None),
        dict(base, symbol="ADR", date="2026-10-20", region="intl"),
    ]
    folder = tmp_path / "earnings-calendar"
    folder.mkdir()
    (folder / "2026-10-16.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    (folder / "2026-10-21.json").write_text(json.dumps({"rows": []}), encoding="utf-8")  # a later day
    day, picked = fsg.earnings_rows(date(2026, 10, 19))
    assert day == "2026-10-16" and [r["symbol"] for r in picked] == ["NOW"]


def test_marks_fall_due_on_the_reaction_day_and_the_session_after(tmp_path):
    """NFLX reports Tue 10-20 after the close: entered on the 20th's last reading, marked on the 21st
    (react) and the 22nd (day2). A spread first priced only after its entry session has no record."""
    import json
    from datetime import date

    legs = [{"role": "long", "strike": 75.0, "option": "L"}, {"role": "short", "strike": 70.0, "option": "S"}]
    spread = {"kind": "credit", "right": "C", "expiry": "2026-11-20", "width": 5.0, "legs": legs}
    nflx = dict(
        spread, symbol="NFLX", date="2026-10-20", entry_session="2026-10-20", reaction_day="2026-10-21"
    )
    late = dict(
        spread, symbol="LATE", date="2026-10-19", entry_session="2026-10-19", reaction_day="2026-10-20"
    )
    unknown = dict(spread, symbol="UNK", date="2026-10-20", entry_session=None, reaction_day="2026-10-21")

    def reading(day, hhmm, rows):
        p = tmp_path / "earnings-legs" / day / f"{hhmm}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"calendar_day": "2026-10-19", "rows": rows}), encoding="utf-8")

    reading("2026-10-20", "1130", [dict(nflx, legs=[dict(legs[0], option="EARLY"), legs[1]])])
    reading("2026-10-20", "1430", [nflx, late, unknown])  # LATE's entry session was the 19th
    react = fsg.marks_due(tmp_path, date(2026, 10, 21))
    assert [(m["symbol"], m["which"], m["entry_reading"]) for m in react] == [
        ("NFLX", "react", "2026-10-20/1430.json")
    ]
    assert react[0]["legs"][0]["option"] == "L"  # the session's LAST reading, not its first
    assert [(m["symbol"], m["which"]) for m in fsg.marks_due(tmp_path, date(2026, 10, 22))] == [
        ("NFLX", "day2")
    ]
    assert fsg.marks_due(tmp_path, date(2026, 10, 23)) == []
