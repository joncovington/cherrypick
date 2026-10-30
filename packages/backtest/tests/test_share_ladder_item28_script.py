"""Hypothesis 28's study (scripts/share_ladder_item28.py): the rebuilt fund, its validation, the
single-stock name rule, the windows and the verdict. Invented bars only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("share_ladder_item28", SCRIPTS / "share_ladder_item28.py")
s28 = importlib.util.module_from_spec(_spec)
sys.modules["share_ladder_item28"] = s28
_spec.loader.exec_module(s28)
sl = s28.sl
B = sl.Bar
RATES = [("1998-12-31", 0.0)]


def _bars(ohlc, start=date(2000, 1, 3)):
    return [B((start + timedelta(days=i)).isoformat(), *x) for i, x in enumerate(ohlc)]


def _walk(n, seed=3):
    rng, px, out = random.Random(seed), 100.0, []
    for _ in range(n):
        o = px * (1 + rng.gauss(0, 0.004))
        c = o * (1 + rng.gauss(0, 0.01))
        out.append((o, max(o, c) * 1.003, min(o, c) * 0.997, c))
        px = c
    return _bars(out)


def test_rebuild_levers_the_close_and_the_range_and_charges_the_carry():
    under = _bars([(100, 100, 100, 100), (101, 103, 99, 102)])
    rebuilt, floored = s28.rebuild(under, {}, 3, [("1998-12-31", 4.0)], fee=0.0)
    first, second = rebuilt
    assert first.c == s28.BASE and floored == 0
    carry = 2 * 4.0 / 100 / 252
    assert second.c == pytest.approx(100 * (1 + 3 * 0.02 - carry))
    assert (second.o, second.h, second.l) == pytest.approx((103, 109, 97))


def test_rebuild_takes_the_total_return_from_adjusted_closes():
    under = _bars([(100, 100, 100, 100), (100, 100, 100, 100)])
    adj = {under[0].start: 50.0, under[1].start: 50.5}  # a 1% dividend on a flat price
    rebuilt, _ = s28.rebuild(under, adj, 2, RATES, fee=0.0)
    assert rebuilt[1].c == pytest.approx(102.0)
    assert rebuilt[1].h == pytest.approx(102.0)  # widened to hold the close


def test_rebuild_holds_a_wipe_out_at_the_floor_and_counts_it():
    under = _bars([(100, 100, 100, 100), (100, 100, 60, 60)])
    rebuilt, floored = s28.rebuild(under, {}, 3, RATES, fee=0.0)
    assert floored == 1
    assert rebuilt[1].c == pytest.approx(s28.BASE * s28.FLOOR)
    _, none = s28.rebuild(_bars([(100, 100, 100, 100), (100, 100, 90, 90)]), {}, 3, RATES, fee=0.0)
    assert none == 0


def test_rebuild_starts_at_the_declared_session():
    under = _bars([(100, 100, 100, 100)] * 5)
    rebuilt, _ = s28.rebuild(under, {}, 3, RATES, start=under[2].start)
    assert [b.start for b in rebuilt] == [b.start for b in under[2:]]


def test_rate_is_the_last_curve_strictly_before_the_session():
    rates = [("2000-01-03", 5.0), ("2000-01-04", 6.0)]
    assert s28.rate_before(rates, "2000-01-04") == 5.0
    assert s28.rate_before(rates, "2000-01-05") == 6.0
    assert s28.rate_before(rates, "1999-12-31") == 5.0


def test_validation_passes_a_faithful_rebuild_and_fails_the_wrong_leverage():
    under = _walk(600)
    adj = {b.start: b.c for b in under}
    rebuilt, _ = s28.rebuild(under, adj, 3, RATES, fee=0.0)
    real = {b.start: b.c for b in rebuilt}
    ok = s28.validate(rebuilt, real)
    assert ok["ok"] and ok["corr"] == pytest.approx(1.0) and ok["mean_year_diff"] == pytest.approx(0.0)
    two_x, _ = s28.rebuild(under, adj, 2, RATES, fee=0.0)
    wrong = s28.validate(rebuilt, {b.start: b.c for b in two_x})
    assert wrong["corr"] > s28.MIN_CORR  # correlation alone cannot see the leverage
    assert not wrong["ok"] and wrong["why"] == "annual difference"


def test_validation_fails_a_noisy_fund_and_a_short_overlap():
    under = _walk(400)
    rebuilt, _ = s28.rebuild(under, {}, 3, RATES, fee=0.0)
    rng = random.Random(1)
    real, px = {}, 100.0
    for b in rebuilt:
        px *= 1 + rng.gauss(0, 0.02)
        real[b.start] = px
    noisy = s28.validate(rebuilt, real)
    assert not noisy["ok"] and noisy["why"] == "correlation"
    short = s28.validate(rebuilt, {b.start: b.c for b in rebuilt[:10]})
    assert not short["ok"] and short["why"] == "too short"


def test_validation_reads_only_sessions_since_a_leverage_change():
    under = _walk(400)
    rebuilt, _ = s28.rebuild(under, {}, 2, RATES, fee=0.0)
    three_x, _ = s28.rebuild(under, {}, 3, RATES, fee=0.0)
    cut = rebuilt[200].start
    # The real fund ran at 3x before the change and 2x after.
    real, px = {}, 100.0
    for i, b in enumerate(rebuilt):
        if i:
            src = three_x if b.start < cut else rebuilt
            px *= src[i].c / src[i - 1].c
        real[b.start] = px
    assert not s28.validate(rebuilt, real)["ok"]
    since = s28.validate(rebuilt, real, since=cut)
    assert since["ok"] and since["first"] == cut


def test_stock_in_name_reads_the_ticker_and_skips_spelled_out_names():
    assert s28.stock_in_name("GraniteShares 2x Long NVDA Daily ETF") == "NVDA"
    assert s28.stock_in_name("Direxion Daily TSLA Bull 2X ETF") == "TSLA"
    assert s28.stock_in_name("Defiance Daily Target 2X Long AVGO ETF") == "AVGO"
    assert s28.stock_in_name("T-Rex 2X Long Apple Daily Target ETF") is None
    assert s28.stock_in_name("Direxion Daily AI and Big Data Bull 2X ETF") is None
    assert s28.stock_in_name("Direxion Daily Semiconductor Bull 3X ETF") is None


def test_window_starts_after_twenty_sessions_and_drops_an_empty_one():
    rebuilt = _bars([(1, 1, 1, 1)] * 30)
    assert s28.window_from(rebuilt, "1999-01-01", "2010-12-31") == (rebuilt[20].start, "2010-12-31")
    assert s28.window_from(rebuilt, "1999-01-01", "1999-12-31") is None
    assert s28.window_from(rebuilt[:20], "1999-01-01", "2010-12-31") is None


def _per(edges, closed=40):
    return {
        f"N{i}": {"arms": {a: {"holdout": {"edge": e, "closed": closed}} for a in sl.ARMS}}
        for i, e in enumerate(edges)
    }


def test_verdict_judges_only_arms_that_passed_item_26():
    item26 = {a: "fail" for a in sl.ARMS} | {"once-3": "pass"}
    out = s28.judge(_per([1.0] * 9), item26)
    assert out["once-3"]["verdict"] == "pass"
    assert out["once-1"]["verdict"] == "reported only"


def test_verdict_fails_a_negative_or_narrow_edge_and_floors_the_count():
    item26 = {a: "pass" for a in sl.ARMS}
    assert s28.judge(_per([-1.0] * 9), item26)["once-3"]["verdict"] == "fail"
    # The mean is up, but the share of names is under half.
    assert s28.judge(_per([5.0] * 4 + [-1.0] * 5), item26)["once-3"]["verdict"] == "fail"
    assert s28.judge(_per([1.0] * 7), item26)["once-3"]["verdict"] == "no verdict"
    assert s28.judge(_per([1.0] * 9, closed=10), item26)["once-3"]["verdict"] == "no verdict"


def test_default_list_rule_fires_on_a_long_hold_or_a_deep_drawdown():
    assert not s28.off_default(s28.LONG_HOLD_DAYS, s28.MAX_DRAWDOWN_PCT)
    assert s28.off_default(s28.LONG_HOLD_DAYS + 1, 10.0)
    assert s28.off_default(100, s28.MAX_DRAWDOWN_PCT + 0.1)
