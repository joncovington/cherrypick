"""Item 90's script (spy_call_calendar_item90.py): invented prices only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from cherrypick.core import fees

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "spy_call_calendar_item90",
    Path(__file__).resolve().parents[1] / "scripts" / "spy_call_calendar_item90.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

D = date.fromisoformat


def q(bid, ask):
    return {"bid": bid, "ask": ask}


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["delta"] is True and m.RULE["splits"] == ["2019-01-01"]


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


# --------------------------------------------------------------------------- the dates


def test_the_short_is_the_monthly_nearest_the_target_and_the_earlier_on_a_tie():
    months = [D("2024-03-15"), D("2024-04-19"), D("2024-05-17")]
    assert m.short_expiry(D("2024-02-16"), months, 60) == D("2024-04-19")  # 63 days beats 28
    tie = [D("2024-01-11"), D("2024-01-21")]  # 10 and 20 days out, both 5 from a 15-day target
    assert m.short_expiry(D("2024-01-01"), tie, 15) == D("2024-01-11")


def test_entries_are_fridays_that_trade_with_the_next_monthly_long():
    spec = {**m.SPEC, "first_entry": "2024-03-01", "data_end": "2024-07-31"}
    ts = m.trade_dates(spec)
    assert ts and all(t["entry"].weekday() == 4 for t in ts)
    assert D("2024-03-29") not in [t["entry"] for t in ts]  # Good Friday: no session, no entry
    for t in ts:
        assert t["short"] <= D("2024-07-31")
        assert 28 <= (t["long"] - t["short"]).days <= 35  # the next monthly
        assert abs((t["short"] - t["entry"]).days - 60) <= 18


def test_the_requests_cover_every_trades_legs_once_per_monthly_oldest_first():
    spec = {**m.SPEC, "first_entry": "2024-01-02", "data_end": "2024-09-30"}
    req = m.requests(spec)
    exps = [r["expiration"] for r in req]
    assert exps == sorted(set(exps))
    by = {r["expiration"]: r for r in req}
    for t in m.all_trade_dates(spec):
        for leg in (t["short"], t["long"]):
            r = by[leg.isoformat()]
            assert r["start"] <= t["entry"].isoformat() and t["short"].isoformat() <= r["through"]


# --------------------------------------------------------------------------- one trade


T = {"entry": D("2024-01-05"), "short": D("2024-01-12"), "long": D("2024-02-16")}
DAYS = [D("2024-01-05"), D("2024-01-08"), D("2024-01-09"), D("2024-01-10"), D("2024-01-11"), D("2024-01-12")]


def path_of(spreads, short_bid=5.0):
    """A path whose spread mid is each value of `spreads` (short mid 5.05, long mid 5.05 + spread)."""
    out = {}
    for day, s in zip(DAYS, spreads, strict=True):
        out[day] = (
            (q(short_bid, short_bid + 0.1), q(short_bid + s, short_bid + s + 0.1))
            if s is not None
            else (None, None)
        )
    return out


def test_the_target_exits_at_the_first_close_at_or_above_the_mark():
    res = m.price_trade("SPY", "C", 100.0, path_of([2.0, 2.5, 2.7, 3.0, 3.0, 3.0]), {}, T, target_frac=0.30)
    assert res["why"] == "target" and res["exit"] == D("2024-01-09")
    open_cost = fees.slippage_dollars([q(5.0, 5.1), q(7.0, 7.1)], 1, {}, symbol="SPY") + fees.ic_open_fee(
        "SPY", 1, legs=2, sell_legs=1, ndigits=4
    )
    close_cost = fees.slippage_dollars([q(5.0, 5.1), q(7.7, 7.8)], 1, {}, symbol="SPY") + fees.ic_close_fee(
        "SPY", 1, legs=2, sell_legs=1, ndigits=4
    )
    assert res["paid"] == pytest.approx(200.0 + open_cost)
    assert res["net"] == pytest.approx(270.0 - close_cost - res["paid"])


def test_an_unquoted_session_never_triggers_and_the_next_quoted_one_can():
    res = m.price_trade("SPY", "C", 100.0, path_of([2.0, None, 2.7, 2.0, 2.0, 2.0]), {}, T, target_frac=0.30)
    assert res["why"] == "target" and res["exit"] == D("2024-01-09")


def test_a_stop_when_declared_and_none_by_default():
    p = path_of([2.0, 0.9, 0.9, 0.9, 0.9, 0.9])
    assert m.price_trade("SPY", "C", 100.0, p, {}, T, target_frac=0.30, stop_frac=0.5)["why"] == "stop"
    assert m.price_trade("SPY", "C", 100.0, p, {D("2024-01-12"): 99.0}, T, target_frac=0.30)["why"] == "time"


def test_the_time_exit_settles_the_short_on_spys_close():
    p = path_of([2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
    p[D("2024-01-12")] = (None, q(3.0, 3.2))  # the expiring short is not quoted two-sided
    otm = m.price_trade("SPY", "C", 100.0, p, {D("2024-01-12"): 99.0}, T, target_frac=0.30)
    itm = m.price_trade("SPY", "C", 100.0, p, {D("2024-01-12"): 101.5}, T, target_frac=0.30)
    slip = fees.slippage_dollars([q(3.0, 3.2)], 1, {}, symbol="SPY")
    assert otm["why"] == itm["why"] == "time"
    assert otm["net"] == pytest.approx(
        310.0 - slip - fees.ic_close_fee("SPY", 1, legs=1, sell_legs=1, ndigits=4) - otm["paid"]
    )
    assert itm["net"] == pytest.approx(
        310.0 - 150.0 - slip - fees.ic_close_fee("SPY", 1, legs=2, sell_legs=1, ndigits=4) - itm["paid"]
    )
    assert m.price_trade("SPY", "C", 100.0, p, {}, T, target_frac=0.30) == {"skip": "expiry_unpriced"}


def test_the_day_before_variant_sells_both_legs_at_their_mids_the_session_before():
    res = m.price_trade("SPY", "C", 100.0, path_of([2.0] * 6), {}, T, target_frac=0.30, exit_day_before=True)
    assert res["why"] == "time" and res["exit"] == D("2024-01-11")


def test_no_entry_quote_or_no_debit_is_skipped():
    assert m.price_trade("SPY", "C", 100.0, path_of([None] + [2.0] * 5), {}, T, target_frac=0.3) == {
        "skip": "entry_unquoted"
    }
    assert m.price_trade(
        "SPY", "C", 100.0, path_of([-0.5] + [2.0] * 5, short_bid=5.0), {}, T, target_frac=0.3
    ) == {"skip": "no_debit"}


def test_stress_charges_more_than_the_suites_costs():
    p = path_of([2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
    base = m.price_trade("SPY", "C", 100.0, p, {D("2024-01-12"): 99.0}, T, target_frac=0.30)
    hard = m.price_trade("SPY", "C", 100.0, p, {D("2024-01-12"): 99.0}, T, target_frac=0.30, stress=m.STRESS)
    assert hard["costs"] > base["costs"] and hard["net"] < base["net"]


# --------------------------------------------------------------------------- the chain pieces


def chain(session, expiry, fwd, vol_scale):
    """A tiny chain around `fwd` whose call and put mids satisfy r = 0 parity."""
    rows = []
    for k in (95.0, 100.0, 105.0):
        put = vol_scale + max(0.0, k - fwd)
        call = put + fwd - k
        rows += [
            (k, "CALL", round(call - 0.05, 2), round(call + 0.05, 2)),
            (k, "PUT", round(put - 0.05, 2), round(put + 0.05, 2)),
        ]
    return Chain(rows, session, expiry)


def test_the_strike_is_nearest_the_short_forward_and_the_delta_signs_by_moneyness():
    # Out of the money the longer call carries more delta, so the calendar is long; in the money, short.
    s = chain("2024-01-05", "2024-03-15", 99.0, 2.0)
    lg = chain("2024-01-05", "2024-04-19", 99.0, 3.0)
    assert m.atm_strike(s, lg, "C") == 100.0
    assert m.position_delta(s, lg, "C", 100.0) > 0
    s, lg = chain("2024-01-05", "2024-03-15", 101.0, 2.0), chain("2024-01-05", "2024-04-19", 101.0, 3.0)
    assert m.position_delta(s, lg, "C", 100.0) < 0


# --------------------------------------------------------------------------- the rules


def test_the_block_bootstrap_reads_the_sign_and_needs_a_block():
    kw = {"block": 3, "resamples": 200, "seed": 1}
    assert m.block_bootstrap_p([1.0] * 10, **kw) == 0.0
    assert m.block_bootstrap_p([-1.0] * 10, **kw) == 1.0
    assert m.block_bootstrap_p([1.0, 2.0], **kw) is None


def rows(start_year, n, net):
    return [
        {
            "date": f"{start_year + i // 50}-{1 + (i % 50) // 5:02d}-{1 + i % 5 * 5:02d}",
            "net": net,
            "bp": 500.0,
            "days": 40,
            "rate": 0.0,
            "financed": 0.0,
        }
        for i in range(n)
    ]


def test_a_holdout_is_read_only_after_the_design_passes():
    rule = {**m.OVERLAP_RULE, "resamples": 200}
    bad = m.judge_overlap(rows(2016, 30, -1.0) + rows(2020, 30, 1.0), ["2019-01-01"], rule)
    assert [p["verdict"] for p in bad["parts"]] == [m.FAIL, m.NOT_READ] and bad["verdict"] == m.FAIL
    good = m.judge_overlap(rows(2016, 30, 1.0) + rows(2020, 30, 1.0), ["2019-01-01"], rule)
    assert good["verdict"] == m.PASS


def test_the_arm_passes_only_when_both_rules_pass():
    assert m.both(m.PASS, m.PASS) == m.PASS
    assert m.both(m.PASS, m.FAIL) == m.FAIL
    assert m.both(m.NO_VERDICT, m.PASS) == m.NO_VERDICT
