"""The income screens' rules: delta, strike, expiry and return choices, and the per-session score."""

from __future__ import annotations

import json
from datetime import date

from cherrypick.technicals import screeners as sc


def test_delta_is_black_scholes_and_a_put_is_the_call_less_one():
    c = sc.bs_delta("C", 100, 100, 30 / 365, 0.30)
    p = sc.bs_delta("P", 100, 100, 30 / 365, 0.30)
    assert 0.52 < c < 0.55 and abs(c - 1 - p) < 1e-12
    assert sc.bs_delta("C", 100, 100, 0, 0.3) is None and sc.bs_delta("C", 100, 100, 0.1, 0) is None


def test_the_strike_is_the_one_nearest_the_target_delta_on_its_own_iv():
    ivs = {k: 0.30 for k in (90.0, 92.0, 94.0, 96.0, 98.0, 100.0)}
    picks = {t: sc.nearest_delta_strike(ivs, "P", 100, 30, t) for t in (0.40, 0.20)}
    assert picks[0.40] == 98.0  # |delta| 0.38 at 98 against 0.53 at 100
    assert picks[0.20] < picks[0.40]  # a lower delta is further out of the money
    # Each strike's own IV counts: lift 96's IV and it is the 0.40 put instead.
    assert sc.nearest_delta_strike({**ivs, 96.0: 0.60, 98.0: 0.10}, "P", 100, 30, 0.40) == 96.0


def test_the_spread_short_is_the_first_strike_out_of_the_money():
    listed = [95.0, 97.5, 100.0, 102.5, 105.0]
    assert sc.first_otm(listed, 101.0, "P") == 100.0
    assert sc.first_otm(listed, 101.0, "C") == 102.5
    assert sc.first_otm(listed, 100.0, "P") == 100.0 and sc.first_otm(listed, 100.0, "C") == 100.0
    assert sc.first_otm(listed, 90.0, "P") is None


def test_the_expiry_is_the_nearest_to_the_target_and_a_tie_takes_the_earlier():
    today = date(2026, 10, 14)
    exps = [date(2026, 10, 14), date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20)]
    assert sc.nearest_expiry(exps, today, 30) == date(2026, 11, 13)  # 30 days
    assert sc.nearest_expiry([date(2026, 11, 10), date(2026, 11, 18)], today, 30) == date(2026, 11, 10)
    assert sc.nearest_expiry([today], today, 30) is None


def test_returns_are_simple_on_the_share_price():
    raw, ann = sc.returns(4.01, 116.88, 30)
    assert round(raw, 2) == 3.43 and round(ann, 2) == 41.74  # the vendor's EBAY row, to the cent
    assert sc.returns(1.0, 0, 30) is None


def _write(root, day, rows_sp, greeks):
    d = root / "vendor-screeners" / day
    d.mkdir(parents=True)
    bodies = {
        "short-puts": {"shortPuts": rows_sp},
        "covered-calls": {"coveredCalls": []},
        "credit-spreads": {"creditSpreads": []},
    }
    for name, body in bodies.items():
        (d / f"{name}.json").write_text(json.dumps({"body": body}), encoding="utf-8")
    g = root / "screener-greeks"
    g.mkdir(parents=True, exist_ok=True)
    (g / f"{day}.json").write_text(json.dumps({"complete": True, "symbols": greeks}), encoding="utf-8")


def _row(sym, strike):
    return {
        "symbol": sym,
        "expiry": "11/13/2026",
        "daysToExpiry": 30.0,
        "strikePrice": strike,
        "midPrice": 2.0,
        "stockPrice": 100.0,
        "rawReturn": "2.00%",
        "annualizedReturn": "24.33%",
    }


def test_a_session_scores_hits_and_names_its_misses(tmp_path):
    strikes = {str(k): {"P": {"iv": 0.30}} for k in (90, 92, 94, 96, 98, 100)}
    chain = {"spot": 100.0, "expiries": {"2026-11-13": {"listed_strikes": [], "strikes": strikes}}}
    _write(tmp_path, "2026-10-14", [_row("AAA", 98.0), _row("BBB", 94.0)], {"AAA": chain, "BBB": chain})
    s = sc.score_day(tmp_path, "2026-10-14")["short-puts"]
    assert s["strike"]["weekly"] == {"hit": 1, "n": 2}  # 11/13 is not a third Friday
    assert s["misses"] == [{"symbol": "BBB", "expiry": "2026-11-13", "vendor": 94.0, "ours": 98.0}]
    assert s["returns"] == {"hit": 2, "n": 2}


def test_a_session_without_its_greeks_is_not_scored(tmp_path):
    _write(tmp_path, "2026-10-14", [_row("AAA", 98.0)], {})
    (tmp_path / "screener-greeks" / "2026-10-14.json").unlink()
    assert sc.score_day(tmp_path, "2026-10-14") is None


def test_score_all_skips_scored_sessions_and_totals_them(tmp_path):
    strikes = {str(k): {"P": {"iv": 0.30}} for k in (90, 92, 94, 96, 98, 100)}
    chain = {"spot": 100.0, "expiries": {"2026-11-13": {"listed_strikes": [], "strikes": strikes}}}
    _write(tmp_path, "2026-10-14", [_row("AAA", 98.0)], {"AAA": chain})
    out = tmp_path / "scores"
    first = sc.score_all(root=tmp_path, out_dir=out)
    assert first["scored"] == ["2026-10-14"] and first["short-puts"]["weekly"] == "1/1 = 100.0%"
    assert sc.score_all(root=tmp_path, out_dir=out)["scored"] == []
    assert sc.score_all(root=tmp_path, out_dir=out, rescore=True)["scored"] == ["2026-10-14"]
