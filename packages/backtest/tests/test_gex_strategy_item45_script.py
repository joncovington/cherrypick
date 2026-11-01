"""Hypothesis 45's inputs (scripts/gex_strategy_item45.py): the cells, the probe and the chain requests.
Invented expiry lists only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("gex_strategy_item45", SCRIPTS / "gex_strategy_item45.py")
s45 = importlib.util.module_from_spec(_spec)
sys.modules["gex_strategy_item45"] = s45
_spec.loader.exec_module(s45)


def test_cell_n_is_the_first_ten_tradable_dropped_names_by_the_declared_hash():
    # The 17 dropped names the liquidity verdict called tradable on 2026-10-30, as declared.
    tradable = "BAC WFC AXP C PG PEP DE JNJ UNH MRK PFE ABBV V XOM CVX CCJ NEE".split()
    ranked = sorted(tradable, key=lambda s: hashlib.sha256(f"item45:{s}".encode()).hexdigest())
    assert tuple(ranked[:10]) == s45.CELL_N_NAMES


def test_the_cells_do_not_share_names_and_p_follows_the_release():
    assert not set(s45.CELL_P_NAMES) & set(s45.CELL_N_NAMES)
    assert set(s45.PILOT_NAMES) <= set(s45.CELL_P_NAMES)
    assert s45.CELLS["N"][2] < date(2026, 4, 12) < s45.CELLS["P"][1]


def test_every_probe_session_trades_and_only_the_pilot_carries_the_probe():
    from cherrypick.core import calendar as cal

    assert all(cal.is_trading_day(d) for d in s45.PROBE_SESSIONS)
    pilot = s45.cell_days("pilot")
    assert all(s45.PROBE_NAME in pilot[d.isoformat()] for d in s45.PROBE_SESSIONS)
    assert s45.PROBE_NAME not in sum(s45.cell_days("P").values(), [])


def test_wide_requests_cover_expiries_through_sixty_days_only():
    exps = ["2026-05-01", "2026-05-08", "2026-06-12", "2026-06-13", "2026-07-17", "2026-08-21"]
    assert s45.wide_expiries("2026-04-14", exps) == ["2026-05-01", "2026-05-08", "2026-06-12", "2026-06-13"]
    assert s45.wide_expiries("2026-04-14", exps + ["2026-04-13"]) == s45.wide_expiries("2026-04-14", exps)


def test_the_sample_expiry_lies_past_the_gauge_so_it_never_shares_bars_with_a_wide_request():
    exps = ["2026-05-01", "2026-06-12", "2026-06-19", "2026-07-17", "2026-08-21"]
    far = s45.sample_expiry("2026-04-14", exps)
    assert far == "2026-06-19"  # 66 days: the nearest from 61 on
    assert far not in s45.wide_expiries("2026-04-14", exps)
    assert s45.sample_expiry("2026-04-14", ["2026-05-01", "2026-09-30"]) is None  # 169 days: too far


def test_requests_carry_one_strike_range_per_expiry():
    exps = ["2026-05-01", "2026-06-19"]
    reqs = s45.requests_for("KO", "2026-04-14", exps, ("1m", "5m"))
    by_exp = {}
    for r in reqs:
        by_exp.setdefault(r["expiration"], set()).add(r.get("strike_range"))
    assert all(len(v) == 1 for v in by_exp.values())
    assert [r["interval"] for r in reqs if r["expiration"] == "2026-06-19"] == ["1m", "5m"]


def test_a_name_day_without_listed_expiries_is_counted_not_guessed():
    days = {"2026-04-14": ["AAPL", "KO"], "2018-03-15": ["NEE"]}
    listed = {("AAPL", "2026-04-14"): ["2026-05-01", "2026-06-19"], ("NEE", "2018-03-15"): ["2018-03-16"]}
    built = s45.build_requests(days, listed)
    assert built["unlisted"] == 1  # KO
    assert built["no_sample_expiry"] == 1  # NEE lists nothing past 60 days
    probe = [r for r in built["requests"] if r["symbol"] == "NEE"]
    assert probe and all(r["interval"] == s45.WIDE_INTERVAL for r in probe)
    aapl_samples = [r["interval"] for r in built["requests"] if r["symbol"] == "AAPL" and "strike_range" in r]
    assert aapl_samples == [s45.SAMPLE_INTERVAL]  # not a probe session: one sampling only
