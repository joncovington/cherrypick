"""Hypothesis 64's study (scripts/backspread_bwb_item64.py): the backspread, the spreads with a
back-month long, the 1-3-2 and the 1-3-2 calendarized, each against holding the same delta in shares.
Invented quotes only (Black-Scholes prices around a made-up forward), no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import date
from pathlib import Path

from cherrypick.core import bs

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backspread_bwb_item64.py"


def _module():
    spec = importlib.util.spec_from_file_location("backspread_bwb_item64", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _module()
ic = m.ic

F, B = "2024-03-15", "2024-04-19"
ENTRY = "2024-01-30"  # 45 days before F


def rows(forward: float, days: int, vol: float = 0.20, half_spread: float = 0.03) -> list:
    years = max(days, 1) / 365.0
    out = []
    for k in range(int(forward) - 30, int(forward) + 31):
        for r, name in (("C", "CALL"), ("P", "PUT")):
            p = bs.price(forward, k, years, vol, r, 0.0)
            out.append((float(k), name, round(max(p - half_spread, 0.0), 2), round(p + half_spread, 2)))
    return out


def chains(session=ENTRY, forward=100.0, **kw) -> dict:
    return {
        tag: ic.Chain(rows(forward, ic.days_between(session, e), **kw), session, e)
        for tag, e in (("F", F), ("B", B))
    }


def strikes() -> dict:
    c = chains()
    return m.pick_strikes(c["F"], c["B"])


def test_monthlies_are_third_fridays_or_the_session_before_a_holiday():
    assert m.monthly_expiry(2024, 3) == date(2024, 3, 15)
    assert m.monthly_expiry(2019, 4) == date(2019, 4, 18)  # Good Friday


def test_requests_ask_each_name_and_monthly_from_85_days_out():
    reqs = m.requests(names=("WMT",), first=date(2024, 1, 1), last=date(2024, 3, 31), today=date(2026, 1, 1))
    assert [r["expiration"] for r in reqs] == ["2024-01-19", "2024-02-16", "2024-03-15"]
    assert reqs[2] == {
        "symbol": "WMT",
        "expiration": "2024-03-15",
        "start": "2023-12-21",
        "through": "2024-03-15",
    }
    # An expiry not yet reached is asked only through today.
    late = m.requests(names=("WMT",), first=date(2024, 3, 1), last=date(2024, 3, 31), today=date(2024, 3, 1))
    assert late[0]["through"] == "2024-03-01"


def test_strikes_step_three_percent_of_the_forward_from_the_money():
    assert strikes() == {"K1": 100.0, "K2": 103.0, "K3": 106.0, "P1": 100.0, "P2": 97.0, "P3": 94.0}


def test_the_four_structures_have_the_declared_shapes():
    legs = m.structure_legs(strikes())
    assert legs["A"] == [("B", 100.0, "C", 2), ("B", 103.0, "C", -1)]
    assert legs["B"] == [("B", 100.0, "C", 1), ("F", 103.0, "C", -2), ("F", 106.0, "C", 2)]
    assert legs["C"] == [("F", 100.0, "P", 1), ("F", 97.0, "P", -3), ("F", 94.0, "P", 2)]
    assert legs["D"] == [
        ("B", 100.0, "P", 1),
        ("B", 97.0, "P", -1),
        ("F", 97.0, "P", -2),
        ("F", 94.0, "P", 2),
    ]
    # Every sold contract is matched by a bought one of the same type: defined risk, IRA-shaped.
    for name, ls in legs.items():
        for right in ("C", "P"):
            assert sum(n for _, _, r, n in ls if r == right) >= 0, name


def test_capital_is_what_the_broker_held_in_the_dry_runs():
    s = strikes()
    legs = m.structure_legs(s)
    assert m.capital("A", legs["A"], -9.65, s) == 965.0  # the debit
    assert m.capital("B", legs["B"], -4.17, s) == 417.0  # the debit
    # The 1-3-2 with 3-point wings loses 3 below P3: less the credit.
    assert m.capital("C", legs["C"], 0.76, s) == round((3 - 0.76) * 100, 2)
    # Calendarized: both front credit spreads at full width, less the net credit.
    assert m.capital("D", legs["D"], 0.74, s) == round((6 - 0.74) * 100, 2)
    assert m.capital("A", legs["A"], 0.10, s) is None  # a credit backspread holds nothing: not a trade


def test_the_backspread_and_the_1_3_2_never_lose_more_than_their_capital_at_expiry():
    s = strikes()
    legs = m.structure_legs(s)
    for name, credit in (("A", -9.65), ("C", 0.76)):
        cap = m.capital(name, legs[name], credit, s)
        worst = min(m.expiry_value(legs[name], x / 2) + credit for x in range(0, 400))
        assert -worst * 100 <= cap + 1e-6


def test_entry_delta_is_long_for_the_bullish_structures():
    c = chains()
    legs = m.structure_legs(strikes())
    assert m.entry_delta(c, legs["A"]) > 50  # two ATM calls less one OTM: more than half a hundred shares
    assert m.entry_delta(c, legs["C"]) > 0  # short the middle puts: long delta


def _path(marks: list[tuple[str, float]]) -> list:
    return [(s, chains(s, forward=f)) for s, f in marks]


def test_a_rally_closes_the_backspread_at_its_target_and_the_shares_take_their_part():
    legs = m.structure_legs(strikes())["A"]
    r = m.run_trade(
        "WMT", "A", legs, strikes(), chains(), _path([("2024-02-05", 100.5), ("2024-02-09", 106.0)]), F
    )
    assert r["how"] == "target" and r["exit"] == "2024-02-09"
    assert r["shares"] > 0 and r["excess"] == round(r["net"] - r["shares"], 2)
    assert r["cost"] > 0 and r["net"] == round(r["gross"] - r["cost"], 2)


def test_otherwise_everything_closes_21_days_before_the_front_expiry():
    legs = m.structure_legs(strikes())["C"]
    r = m.run_trade(
        "WMT", "C", legs, strikes(), chains(), _path([("2024-02-05", 99.0), ("2024-02-23", 99.5)]), F
    )
    assert r["how"] == "time" and r["exit"] == "2024-02-23"


def test_a_trade_with_no_priced_exit_is_skipped():
    legs = m.structure_legs(strikes())["D"]
    assert m.run_trade("WMT", "D", legs, strikes(), chains(), [], F) == {"skip": "no exit mark"}


def _row(front, symbol, structure, net, capital=500.0, excess=None, cost=5.0):
    return {"front": front, "symbol": symbol, "structure": structure, "net": net, "capital": capital,
            "excess": net if excess is None else excess, "cost": cost, "how": "time",
            "entry": front}  # fmt: skip


def test_one_observation_is_one_cycle_month_weighted_equally_across_names():
    rs = [
        _row("2024-03-15", "WMT", "A", 100.0),
        _row("2024-03-15", "KMB", "A", 0.0),
        _row("2024-04-19", "WMT", "A", 50.0),
    ]
    assert m.cycle_means(rs, lambda r: r["net"]) == {"2024-03": 50.0, "2024-04": 50.0}


def test_calendarizing_is_compared_only_where_a_name_entered_both():
    rs = [_row("2024-03-15", "WMT", "D", 100.0, 1000.0), _row("2024-03-15", "WMT", "C", 100.0, 500.0),
          _row("2024-03-15", "KMB", "D", 900.0, 1000.0)]  # fmt: skip
    assert m.diff_cycles(rs) == {"2024-03": 0.1 - 0.2}


def _months(n: int, start=(2016, 1)) -> list[str]:
    y, mo = start
    out = []
    for _ in range(n):
        out.append(f"{y}-{mo:02d}-15")
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


def _book(excess_by_structure: dict, n=70, net=60.0, cost=5.0) -> list:
    rows = []
    for i, front in enumerate(_months(n)):
        for k, pattern in excess_by_structure.items():
            e = pattern[i % len(pattern)]
            rows.append(_row(front, "WMT", k, net, excess=e, cost=cost))
    return rows


def _judged(rows, skips=None):
    sk = {k: Counter((skips or {}).get(k, {})) for k in m.STRUCTURES}
    tests = m.score(rows, sk, "2016-01-01", "2021-12-31")
    m.judge(tests)
    return tests


STEADY = [60.0, 40.0]


def test_a_steady_excess_passes():
    t = _judged(_book({k: STEADY for k in m.STRUCTURES}))
    assert t["A"]["verdict"] == "pass" and t["A"]["checks"]["holm"]


def test_a_noisy_excess_fails_the_t_bar():
    t = _judged(_book({"A": [300.0, -290.0], "B": STEADY, "C": STEADY, "D": STEADY}))
    assert t["A"]["verdict"] == "fail" and not t["A"]["checks"]["t_above_bar"]


def test_holm_can_refuse_what_the_t_bar_alone_would_pass():
    # 44 cycles of +10 and 26 of -10, interleaved: t about 2.2, p about 0.015. Enough alone, not as
    # the best of five under Holm at 5% (it needs p at or under 0.01).
    block = [10.0 if i < 22 else -10.0 for i in range(35)]
    weak = [block[(i * 8) % 35] for i in range(35)]  # the same 22 and 13, interleaved
    t = _judged(_book({"A": weak, "B": [0.0], "C": [0.0], "D": [0.0]}))
    assert 2.0 < t["A"]["t"] < 2.6 and t["A"]["checks"]["both_halves_positive"]
    assert t["A"]["checks"]["t_above_bar"] and not t["A"]["checks"]["holm"]
    assert t["A"]["verdict"] == "fail"


def test_an_excess_only_in_the_first_half_fails():
    # 70 months from 2016-01: the first 36 fall before the window's midpoint (2018-12-31).
    t = _judged(_book({"A": [80.0, 60.0] * 18 + [-5.0, -1.0] * 17, "B": STEADY, "C": STEADY, "D": STEADY}))
    assert t["A"]["verdict"] == "fail" and not t["A"]["checks"]["both_halves_positive"]


def test_a_raw_return_eaten_by_half_again_its_costs_fails():
    t = _judged(_book({k: STEADY for k in m.STRUCTURES}, net=2.0, cost=5.0))
    assert t["A"]["verdict"] == "fail" and not t["A"]["checks"]["raw_positive_at_1_5x_costs"]


def test_too_few_cycles_or_too_many_unexited_entries_is_no_verdict():
    assert _judged(_book({k: STEADY for k in m.STRUCTURES}, n=40))["A"]["verdict"] == "no verdict"
    t = _judged(_book({k: STEADY for k in m.STRUCTURES}), skips={"A": {"no exit mark": 10}})
    assert t["A"]["verdict"] == "no verdict" and t["B"]["verdict"] == "pass"


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert m.main(["study", "--end", "2022-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
