"""Item 94's script (reversion_put_item94.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from cherrypick.core import bs

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "reversion_put_item94", Path(__file__).resolve().parents[1] / "scripts" / "reversion_put_item94.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    # Fails until the declaration fills every value of RULE.
    kit_rule.check_rule(m.RULE)


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


def test_the_declaration_is_what_the_plan_states():
    # A change to any of these is a change to the declared test: it needs a logged correction.
    assert m.RULE["splits"] == ["2025-07-01"] and m.RULE["delta"] is True and m.RULE["t_bar"] == 2.0
    assert (m.RULE["min_n"], m.RULE["holdout_min_n"]) == (40, 12)
    assert m.TIMING_RULE == {
        "t_bar": 2.0,
        "min_entries": 100,
        "holdout_min_entries": 30,
        "cluster": "expiry",
        "split": "2025-07-01",
    }
    assert m.SPEC["setup"] == "reversion" and m.SPEC["dollar_floor"] == 300e6
    assert (m.SPEC["first_entry"], m.SPEC["last_expiry"]) == ("2021-01-04", "2026-11-20")
    assert m.SPEC["dte"] == (30, 45) and m.SPEC["baselines"] == 5
    assert (m.SPEC["short_delta"], m.SPEC["reported_delta"]) == (0.25, 0.16)
    assert m.ARMS == ("put25",) and not set(m.ARMS) & set(m.REPORTED)


# ------------------------------------------------------------------------------------------------
# Calendar and exclusions.


def test_the_expiry_is_the_latest_monthly_30_to_45_days_out():
    assert m.expiry_for("2025-02-10") == "2025-03-21"  # 39 days; April's is 67
    assert m.expiry_for("2025-02-20") is None  # March's is 29 days out, April's 56
    assert m.expiry_for("2025-04-04") == "2025-05-16"  # 42 days


def test_a_report_or_split_inside_the_trade_refuses_it():
    none = lambda a, b: False  # noqa: E731
    assert m.why_not("2025-02-10", none, []) == ("2025-03-21", None)
    assert m.why_not("2025-02-10", none, ["2025-03-21"])[1] == "earnings report inside the trade"
    assert m.why_not("2025-02-10", none, ["2025-02-07", "2025-03-24"])[0] == "2025-03-21"
    assert m.why_not("2025-02-10", lambda a, b: True, [])[1] == "suspected unadjusted split inside the trade"
    assert m.why_not("2026-10-12", none, []) == ("2026-11-20", None)  # the last expiry is in the window
    assert m.why_not("2026-11-09", none, [])[1] == "expiry after the window"  # 2026-12-18


def test_signal_requests_come_first_and_each_once():
    rows = [
        {"kind": "baseline", "symbol": "AAA", "expiry": "2025-03-21", "entry": "2025-01-02"},
        {"kind": "signal", "symbol": "BBB", "expiry": "2025-04-17", "entry": "2025-03-10"},
        {"kind": "baseline", "symbol": "BBB", "expiry": "2025-04-17", "entry": "2025-03-10"},
    ]
    got = m.path_requests(rows)
    assert got[0] == {
        "symbol": "BBB",
        "expiration": "2025-04-17",
        "start": "2025-03-10",
        "through": "2025-03-10",
    }
    assert len(got) == 2


# ------------------------------------------------------------------------------------------------
# One put, on an invented chain priced from a model.

EXPIRY = "2025-03-21"


def chain(session: str, spot: float, vol: float = 0.30, spread: float = 0.04) -> Chain:
    years = max((date.fromisoformat(EXPIRY) - date.fromisoformat(session)).days, 1) / 365.0
    rows = []
    for k in range(50, 151, 1):
        for r in ("C", "P"):
            px = bs.price(spot, float(k), years, vol, r, 0.0)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(px - spread / 2, 0.0), px + spread / 2))
    return Chain(rows, session, EXPIRY)


def test_an_expired_worthless_put_keeps_its_credit_less_costs():
    c = chain("2025-02-10", 100.0)
    t = m.put_trade("AAA", EXPIRY, c, 110.0, 0.25)
    assert 90 < t["strike"] < 100 and not t["assigned"] and t["settlement"] == 0
    assert t["net"] == round(t["credit"] - t["fees"] - t["slippage"], 4)
    assert t["bp"] == 100 * t["strike"] and t["delta"] > 0  # a short put is long the stock


def test_an_assigned_put_pays_its_intrinsic_and_the_assignment_fee():
    c = chain("2025-02-10", 100.0)
    t = m.put_trade("AAA", EXPIRY, c, 80.0, 0.25)
    assert t["assigned"] and t["settlement"] > 0
    owed = 100 * (t["strike"] - 80.0)
    assert abs(t["gross"] - (t["credit"] - owed)) < 1e-6
    assert t["net"] < 0


def test_a_missing_input_is_a_counted_skip():
    c = chain("2025-02-10", 100.0)
    assert m.put_trade("AAA", EXPIRY, None, 100.0, 0.25) == {"skip": "no entry chain"}
    assert m.put_trade("AAA", EXPIRY, c, None, 0.25) == {"skip": "no expiry close"}


def test_the_reported_delta_sits_further_out():
    c = chain("2025-02-10", 100.0)
    assert (
        m.put_trade("AAA", EXPIRY, c, 100.0, 0.16)["strike"]
        < m.put_trade("AAA", EXPIRY, c, 100.0, 0.25)["strike"]
    )


def test_the_spread_bar_reads_percent_or_a_nickel():
    assert m.tight({"bid": 1.00, "ask": 1.05})
    assert m.tight({"bid": 2.00, "ask": 2.18})
    assert not m.tight({"bid": 0.50, "ask": 0.70})


# ------------------------------------------------------------------------------------------------
# The rows and the two rules.


def entry(expiry: str, entry_day: str, net: float, bp: float = 9000.0) -> dict:
    return {
        "expiry": expiry,
        "entry": entry_day,
        "net": net,
        "bp": bp,
        "days": 35,
        "delta": 25.0,
        "f0": 100.0,
        "f1": 100.0,
        "assigned": False,
        "tight": True,
        "credit": 150.0,
        "slippage": 2.0,
    }


def test_entries_on_one_expiry_pool_into_one_row():
    rows = m.cycle_rows(
        [
            entry("2025-03-21", "2025-02-12", 50.0),
            entry("2025-03-21", "2025-02-10", -20.0),
            entry("2025-04-17", "2025-03-10", 5.0),
        ],
        lambda d: 0.04,
    )
    assert [r["date"] for r in rows] == ["2025-02-10", "2025-03-10"]
    assert rows[0]["net"] == 30.0 and rows[0]["bp"] == 18000.0 and rows[0]["entries"] == 2


def test_the_timing_difference_has_the_right_sign_and_floors():
    sig = [entry(f"2025-{1 + i % 12:02d}-17", "2025-01-02", 100.0 + (i % 7)) for i in range(120)]
    base = [entry(f"2025-{1 + i % 12:02d}-17", "2025-01-02", -50.0 + (i % 5)) for i in range(400)]
    d = m.clustered_difference(sig, base)
    assert d["difference"] > 0 and d["t"] > 2 and d["clusters"] == 12
    v = m.timing_verdict(d, d, rule_design_passed=False)
    assert v["design"]["verdict"] == ck.PASS and v["holdout"]["verdict"] == kit_rule.NOT_READ
    few = m.clustered_difference(sig[:50], base)
    assert m.timing_verdict(few, few, True)["verdict"] == ck.NO_VERDICT


def test_judge_needs_both_rules_and_reports_beside():
    import random

    rng = random.Random(1)
    months = [f"{y}-{mo:02d}" for y in (2021, 2022, 2023, 2024, 2025, 2026) for mo in range(1, 13)]
    entries = {a: [] for a in (*m.ARMS, *m.REPORTED)}
    for ym in months:
        exp, day = f"{ym}-20", f"{ym}-01"
        for _ in range(3):
            entries["put25"].append(entry(exp, day, rng.gauss(-10, 40)))
            entries["put16"].append(entry(exp, day, rng.gauss(-10, 40)))
        for _ in range(15):
            entries["base25"].append(entry(exp, day, rng.gauss(-10, 40)))
            entries["base16"].append(entry(exp, day, rng.gauss(-10, 40)))
    entries["put25-tight"] = list(entries["put25"])
    block = m.judge(entries, lambda d: 0.03)
    assert set(block["verdict"]) == {"put25"}
    assert block["verdict"]["put25"] in (ck.FAIL, ck.NO_VERDICT)
    assert block["arms"]["put25"]["parts"][0]["n"] > 40
    assert set(block["reported"]["beside"]) == {*m.ARMS, *m.REPORTED}
