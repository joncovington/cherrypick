"""Item 78's script (index_put_spread_item78.py): invented chains only, the store never read."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from cherrypick.core import bs, fees

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "index_put_spread_item78", Path(__file__).resolve().parents[1] / "scripts" / "index_put_spread_item78.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ENTRY, EXPIRY = "2024-06-07", "2024-06-14"


def chain(fwd: float, session: str, expiry: str = EXPIRY, vol: float = 0.20, half: float = 0.01) -> Chain:
    """A two-sided Black-76 chain at r = 0, strikes 1 apart from 70 to 130, a cent either side."""
    years = max((Chain([], session, expiry).years), 1 / 365)
    rows = []
    for k in range(70, 131):
        for r in ("C", "P"):
            p = bs.price(fwd, k, years, vol, r, 0.0) if session != expiry else bs.intrinsic(fwd, k, r)
            p = max(p, 0.0)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(p - half, 0.0), p + half))
    return Chain(rows, session, expiry)


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)


def test_the_chain_plans_then_studies_with_no_fetch():
    assert [s["step"] for s in m.CHAIN] == ["plan", "study"]


def test_entry_is_seven_days_before_or_the_next_session():
    assert m.entry_day("2024-06-14") == "2024-06-07"
    # 2024-07-04 is a holiday: 7 days before 2024-07-11 moves to the 5th.
    assert m.entry_day("2024-07-11") == "2024-07-05"


def test_the_spread_buys_a_lower_put_and_holds_its_width_less_the_credit():
    c0, c1 = chain(100.0, ENTRY), chain(101.0, EXPIRY)
    t = m.spread_trade("SPY", c0, c1, 0.05)
    short, long = t["strikes"]
    assert long < short < 100.0
    gaps = {q["strike"]: abs(abs(c0.delta(q)) - 0.10) for q in c0.otm("P") if c0.delta(q) is not None}
    assert gaps[short] == min(gaps.values())  # the put nearest 10 delta
    assert t["bp"] == pytest.approx(100.0 * (short - long) - t["credit"], abs=1e-3)
    assert t["payoff"] == 0.0  # both expire out of the money
    assert t["net"] == pytest.approx(t["credit"] - t["costs"], abs=1e-3)
    assert t["costs"] == pytest.approx(
        fees.slippage_dollars([c0.by["P"][short], c0.by["P"][long]], 1, {})
        + fees.ic_open_fee("SPY", 1, legs=2, sell_legs=1, ndigits=4),
        abs=1e-3,
    )
    assert 0 < t["delta"] < 10  # a short put spread is long a little delta


def test_a_breach_loses_at_most_the_width_and_the_index_settles_in_cash():
    c0, c1 = chain(100.0, ENTRY), chain(80.0, EXPIRY)
    t = m.spread_trade("SPXW", c0, c1, 0.03)
    short, long = t["strikes"]
    assert t["payoff"] == pytest.approx(100.0 * (short - long))
    assert -t["net"] <= t["bp"] + t["costs"] + 1e-6
    assert t["costs"] >= fees.ic_expire_fee(2)


def test_the_naked_put_reported_beside_is_on_item_77s_margin():
    c0, c1 = chain(100.0, ENTRY), chain(101.0, EXPIRY)
    t = m.spread_trade("SPY", c0, c1, None)
    [short] = t["strikes"]
    assert t["bp"] == pytest.approx(
        max(0.20 * 100 * 100.0 - 100 * (100.0 - short), 0.10 * 100 * short) + t["credit"], abs=1e-3
    )


def test_a_missing_leg_or_forward_is_a_skip_never_a_trade():
    c0 = chain(100.0, ENTRY)
    assert m.spread_trade("SPY", c0, None, 0.05) == {"skip": "no expiry forward"}
    # The chain stops at 70: a 0.1% delta long leg below a 10-delta short has no quote.
    far = chain(100.0, ENTRY, vol=0.05)
    assert "skip" in m.spread_trade("SPY", far, chain(100.0, EXPIRY), 0.001)


def test_a_cycle_is_the_equal_weight_mean_of_each_names_return_on_its_buying_power():
    a = {"symbol": "SPY", "entry": ENTRY, "expiry": EXPIRY, "net": 10.0, "bp": 100.0, "delta": 0.0,
         "f0": 100.0, "f1": 100.0, "days": 7}  # fmt: skip
    b = {**a, "symbol": "QQQ", "net": -30.0, "bp": 1000.0}
    [row] = m.cycle_rows([a, b], lambda s: 0.05)
    assert row["net"] == pytest.approx((0.10 - 0.03) / 2)
    assert row["bp"] == 1.0 and row["names"] == 2 and row["date"] == ENTRY


def test_the_requests_are_item_57s_each_once(tmp_path):
    r = {"symbol": "SPY", "expiration": EXPIRY, "start": "2024-06-04", "through": EXPIRY}
    later = {**r, "expiration": "2024-06-21", "through": "2024-06-21"}
    (tmp_path / "path_requests.json").write_text(json.dumps({"requests": [r]}), encoding="utf-8")
    (tmp_path / "path_requests-forward.json").write_text(
        json.dumps({"requests": [r, later]}), encoding="utf-8"
    )
    assert m.requests(tmp_path) == [r, later]
    with pytest.raises(SystemExit):
        m.requests(tmp_path / "none")
