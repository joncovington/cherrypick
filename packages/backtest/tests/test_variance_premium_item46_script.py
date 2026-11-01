"""Hypothesis 46's inputs (scripts/variance_premium_item46.py): the names, the cycles and the path requests.
Invented store keys only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "variance_premium_item46", SCRIPTS / "variance_premium_item46.py"
)
s46 = importlib.util.module_from_spec(_spec)
sys.modules["variance_premium_item46"] = s46
_spec.loader.exec_module(s46)


def test_the_groups_are_the_declared_seven_names_and_do_not_overlap():
    assert s46.GROUPS["equity_index"] == ("SPXW", "SPY", "QQQ", "IWM")
    assert s46.GROUPS["non_equity"] == ("GLD", "SLV", "TLT")
    assert len(set(s46.NAMES)) == len(s46.NAMES) == 7


def test_a_monthly_is_the_third_friday_unless_it_is_a_holiday():
    assert s46.monthly_expiry(2026, 10) == date(2026, 10, 16)
    assert s46.monthly_expiry(2022, 4) == date(2022, 4, 14)  # Good Friday 04-15: the Thursday
    assert s46.monthly_expiry(2025, 4) == date(2025, 4, 17)  # Good Friday 04-18
    assert s46.monthly_expiry(2016, 3) == date(2016, 3, 18)


def test_the_cycles_run_from_the_first_to_the_last_declared_expiry_one_a_month():
    cs = s46.cycles()
    assert cs[0] == s46.FIRST_EXPIRY and cs[-1] == s46.LAST_EXPIRY
    assert len(cs) == 128  # 2016-03 .. 2026-10
    assert len({(d.year, d.month) for d in cs}) == len(cs)
    # A window that ends before its month's monthly leaves that month out.
    assert s46.cycles(date(2026, 9, 1), date(2026, 10, 15))[-1] == date(2026, 9, 18)


def test_each_request_is_the_stores_66_day_path_to_expiry():
    r = s46.path_request("SPY", date(2026, 3, 20))
    assert r == {"symbol": "SPY", "expiration": "2026-03-20", "start": "2026-01-13", "through": "2026-03-20"}
    longest = max(s46.TENORS)
    assert (
        date(2026, 3, 20) - date.fromisoformat(r["start"])
    ).days > longest  # every tenor's entry is inside


def test_every_name_gets_every_cycle():
    reqs = s46.build_requests()
    assert len(reqs) == 7 * 128
    assert {r["symbol"] for r in reqs} == set(s46.NAMES)
    assert reqs[0]["symbol"] == "SPXW" and reqs[-1]["symbol"] == "TLT"


def test_held_counts_only_the_exact_complete_key():
    reqs = s46.build_requests(("SPY",), [date(2026, 3, 20), date(2026, 4, 17)])
    exact = {("SPY", "2026-03-20", "2026-01-13", "2026-03-20")}
    assert s46.held(reqs, exact) == 1
    shorter = {("SPY", "2026-03-20", "2026-03-02", "2026-03-20")}  # an 18-day path is not the same request
    assert s46.held(reqs, shorter) == 0


def test_the_time_split_falls_inside_the_cycles():
    cs = s46.cycles()
    before = [d for d in cs if d < s46.TIME_SPLIT]
    assert 0 < len(before) < len(cs)
