"""The study kit (#1068): pinned to the scripts it was copied from, and held to properties the copies
were never tested on. Synthetic chains only; no store is read."""

from __future__ import annotations

import importlib.util
import random
from datetime import date
from pathlib import Path

import pytest
from cherrypick.core import bs

from cherrypick.backtest.study import calendars as kc
from cherrypick.backtest.study import chains as kch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


i59 = _script("iwm_condor_item59")
i64 = _script("backspread_bwb_item64")
i74 = _script("super_bull_item74")

SESSION, EXPIRY = "2024-02-09", "2024-03-15"


def rows(forward, days, vol=0.25, half=0.02, skew=0.0, drop=0.0, noise=0.0, seed=0, spell=("CALL", "PUT")):
    """A Black-76 chain at r = 0 around `forward`, with a put skew, a share of one-sided quotes, and
    `noise` dollars of random mid error per quote (so strikes disagree on parity, as real ones do)."""
    rng = random.Random(seed)
    years = max(days, 1) / 365.0
    out = []
    for k in range(int(forward * 0.6), int(forward * 1.4) + 1):
        for r, name in zip(("C", "P"), spell, strict=True):
            v = vol + skew * (forward - k) / forward
            p = bs.price(forward, k, years, max(v, 0.05), r, 0.0) + rng.uniform(-noise, noise)
            bid = round(max(p - half, 0.0), 2)
            if rng.random() < drop:
                bid = 0.0  # one-sided
            out.append((float(k), name, bid, round(max(p + half, 0.01), 2)))
    return out


CASES = [
    dict(forward=100.0, vol=0.25),
    dict(forward=203.4, vol=0.18, skew=0.3),
    dict(forward=57.7, vol=0.6, half=0.05, drop=0.2, seed=3),
    dict(forward=412.0, vol=0.12, half=0.1, drop=0.4, seed=9),
    dict(forward=150.0, vol=0.3, half=0.05, noise=0.08, seed=5),
    dict(forward=88.8, vol=0.45, half=0.03, noise=0.15, drop=0.1, seed=11),
]


# --- pinned to the copies ------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_the_kit_chain_reads_every_case_as_item_59s_chain_does(case):
    r = rows(days=kc.days_between(SESSION, EXPIRY), **case)
    kit, old = kch.Chain(r, SESSION, EXPIRY), i59.Chain(r, SESSION, EXPIRY)
    assert kit.forward == old.forward
    for right in ("P", "C"):
        assert kit.otm(right) == old.otm(right)
        assert [kit.delta(q) for q in kit.otm(right)] == [old.delta(q) for q in old.otm(right)]
        for target in (0.05, 0.16, 0.25, 0.5):
            assert kit.nearest(right, target) == old.nearest(right, target)
        far = old.nearest(right, 0.16)
        if far:
            assert kit.nearest(right, 0.05, beyond=far["strike"]) == old.nearest(
                right, 0.05, beyond=far["strike"]
            )


@pytest.mark.parametrize("case", CASES)
def test_the_kit_prices_every_quote_as_item_59_does_in_the_money_too(case):
    # In-the-money quotes near intrinsic imply a volatility near zero: item 59's floor drops them.
    r = rows(days=kc.days_between(SESSION, EXPIRY), **case)
    kit, old = kch.Chain(r, SESSION, EXPIRY), i59.Chain(r, SESSION, EXPIRY)
    for right in ("P", "C"):
        for k, q in old.by[right].items():
            assert kit.delta(kit.by[right][k]) == old.delta(q)


def test_a_parity_seed_tie_takes_the_lower_strike_as_item_59_does():
    # Strikes 98 and 102 both have call and put mids equally close: the lower is the seed. With only
    # two two-sided strikes, the median is over both either way, so a third far strike breaks the tie.
    q = [
        (98.0, "CALL", 3.0, 3.2),
        (98.0, "PUT", 2.0, 2.2),
        (102.0, "CALL", 2.0, 2.2),
        (102.0, "PUT", 3.0, 3.2),
        (90.0, "CALL", 10.4, 10.6),
        (90.0, "PUT", 0.4, 0.6),
        (110.0, "CALL", 0.1, 0.3),
        (110.0, "PUT", 10.9, 11.1),
    ]
    assert kch.Chain(q, SESSION, EXPIRY).forward == i59.Chain(q, SESSION, EXPIRY).forward


def test_the_kit_listed_monthlies_and_entry_session_are_item_59s():
    listed = [d.isoformat() for d in (date(2016, 1, 1) + __import__("datetime").timedelta(days=i)
              for i in range(0, 3700)) if d.weekday() in (3, 4)]  # fmt: skip
    assert kc.listed_monthlies(listed) == i59.monthlies(listed)
    sessions = [s for s in listed if s < "2024-03-15"]
    for e in ("2024-03-15", "2022-04-14", "2020-12-18"):
        assert kc.entry_session(sessions, e, 45, (42, 48)) == i59.entry_session(sessions, e, 45, (42, 48))


def test_the_kit_calendar_monthlies_are_items_64_and_74s():
    first, last = date(2016, 1, 1), date(2026, 12, 31)
    assert kc.monthlies_between(first, last) == i64.monthlies(first, last) == i74.monthlies(first, last)


# --- properties ----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES[:2])
def test_parity_recovers_the_forward(case):
    chain = kch.Chain(rows(days=35, **case), SESSION, EXPIRY)
    assert abs(chain.forward - case["forward"]) < 0.05


def test_deltas_fall_away_from_the_money_on_both_sides():
    chain = kch.Chain(rows(100.0, 35, skew=0.3), SESSION, EXPIRY)
    for right in ("P", "C"):
        ds = [abs(d) for d in (chain.delta(q) for q in chain.otm(right)) if d is not None]
        assert len(ds) > 10 and all(a >= b for a, b in zip(ds, ds[1:], strict=False))


def test_the_store_and_the_short_spellings_of_a_right_read_the_same():
    a = kch.Chain(rows(100.0, 35, spell=("CALL", "PUT")), SESSION, EXPIRY)
    b = kch.Chain(rows(100.0, 35, spell=("c", "p")), SESSION, EXPIRY)
    assert a.forward == b.forward and a.nearest("P", 0.16) == b.nearest("P", 0.16)


def test_a_chain_with_no_forward_or_only_one_sided_quotes_offers_nothing():
    puts_only = kch.Chain([(float(k), "PUT", 1.0, 1.1) for k in range(80, 100)], SESSION, EXPIRY)
    assert puts_only.forward is None and puts_only.otm("P") == [] and puts_only.nearest("C", 0.16) is None
    dead = kch.Chain(rows(100.0, 35, drop=1.0), SESSION, EXPIRY)
    assert dead.forward is None and dead.nearest("P", 0.16) is None


def test_nearest_beyond_is_strictly_further_out():
    chain = kch.Chain(rows(100.0, 35), SESSION, EXPIRY)
    short = chain.nearest("P", 0.16)
    wing = chain.nearest("P", 0.16, beyond=short["strike"])
    assert wing["strike"] < short["strike"]
    call = chain.nearest("C", 0.16)
    assert chain.nearest("C", 0.16, beyond=call["strike"])["strike"] > call["strike"]


def test_a_holiday_friday_moves_the_monthly_to_the_thursday():
    # 2022-04-15 was Good Friday: the April monthly expired on the Thursday.
    assert kc.monthly_expiry(2022, 4) == date(2022, 4, 14)
    assert kc.listed_monthlies(["2022-04-14", "2022-04-22"]) == ["2022-04-14"]
    assert kc.listed_monthlies(["2022-04-14", "2022-04-15"]) == ["2022-04-15"]  # the Friday wins if listed


def test_an_entry_outside_its_band_is_none_and_a_tie_takes_the_earlier():
    assert kc.entry_session(["2024-03-01"], "2024-03-15", 45, (42, 48)) is None
    assert kc.entry_session(["2024-01-30", "2024-02-01"], "2024-03-15", 44, (42, 48)) == "2024-01-30"
