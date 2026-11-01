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


# ---- the pilot: built bars against the broker's ----


def test_a_sample_belongs_to_the_bar_starting_at_or_before_it_and_only_the_rules_bars_count():
    assert s45.bar_of("09:30") == "09:30"
    assert s45.bar_of("09:44") == "09:30"
    assert s45.bar_of("09:45") == "09:45"
    assert s45.bar_of("15:59") == "15:45"
    assert s45.bar_of("16:00") is None
    assert s45.bar_of("09:29") is None


def test_five_minute_sampling_keeps_three_samples_a_bar_and_can_miss_the_extreme():
    samples = {f"09:{m:02d}": 100.0 for m in range(30, 45)}
    samples["09:37"] = 101.0  # the high, on a minute five-minute sampling never sees
    one = s45.build_bars(samples, 1)
    five = s45.build_bars(samples, 5)
    assert one == {"09:30": (100.0, 101.0, 100.0, 100.0)}
    assert five == {"09:30": (100.0, 100.0, 100.0, 100.0)}


def test_wilder_atr_waits_for_n_ranges_then_smooths():
    bars = [(10, 11, 9, 10)] * 3 + [(10, 14, 10, 12)]
    atr = s45.wilder_atr(bars, n=3)
    assert atr[:2] == [None, None]
    assert atr[2] == 2.0
    assert atr[3] == (2.0 * 2 + 4) / 3  # true range 4: high 14 over the prior close 10


def _series(n, *, high=1.0, low=1.0):
    return {
        ("2026-08-03", f"{10 + i // 4:02d}:{(i % 4) * 15:02d}"): (100, 100 + high, 100 - low, 100)
        for i in range(n)
    }


def test_a_faithful_build_passes_and_a_flat_one_fails():
    true = _series(40)
    same = s45.compare(true, dict(true))
    assert s45.verdict([same])["accepted"]
    flat = s45.compare(
        true, _series(40, high=0.5, low=0.5)
    )  # half the range: ATR ratio 0.5, every bar misses
    v = s45.verdict([flat])
    assert not v["accepted"]
    assert v["median_atr_ratio"] == 0.5 and v["miss_share"] == 1.0


def test_a_bar_without_samples_is_a_miss_not_skipped():
    true = _series(40)
    built = dict(true)
    gone = sorted(true)[20:26]  # six of the 27 judged bars
    for k in gone:
        del built[k]
    v = s45.verdict([s45.compare(true, built)])
    assert v["missing"] == 6
    assert v["miss_share"] > s45.MISS_MAX
    assert not v["accepted"]


def test_the_basis_follows_the_declaration():
    assert s45.basis({"1m": True, "5m": True}).startswith("5m")
    assert s45.basis({"1m": True, "5m": False}).startswith("1m only")
    assert s45.basis({"1m": False, "5m": False}).startswith("neither")


def test_a_candle_event_reads_as_a_new_york_bar_start_and_a_removal_or_zero_price_does_not():
    from types import SimpleNamespace

    ms = 1786109400000  # 2026-08-07 13:30 UTC
    ev = SimpleNamespace(
        event_symbol="KO{=15m,tho=true}",
        event_flags=0,
        time=ms,
        open=70.0,
        high=70.5,
        low=69.9,
        close=70.2,
        volume=1e5,
    )
    assert s45.candle_row(ev) == ("KO", "2026-08-07T09:30", 70.0, 70.5, 69.9, 70.2, 1e5)
    assert s45.candle_row(SimpleNamespace(**{**vars(ev), "event_flags": s45.REMOVE})) is None
    assert s45.candle_row(SimpleNamespace(**{**vars(ev), "low": 0.0})) is None
