"""Item 99's script (earnings_double_calendar_item99.py): invented chains and quotes, no store read."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from cherrypick.core import fees

from cherrypick.backtest import intradayfetch
from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "earnings_double_calendar_item99",
    Path(__file__).resolve().parents[1] / "scripts" / "earnings_double_calendar_item99.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


# --------------------------------------------------------------------------- the declaration


def test_the_rules_are_declared():
    assert kit_rule.check_rule(m.RULE) and m.RULE["delta"] is False
    assert m.RULE["splits"] == ["2024-01-01"] and m.RULE["t_bar"] == m.EVENT_RULE["t_bar"] == 2.4
    assert m.RULE["min_n"] == 150 and m.RULE["holdout_min_n"] == 100 and m.RULE["tail"]["seed"] == 99
    assert m.EVENT_RULE["halves"] < m.RULE["splits"][0] < m.EVENT_RULE["confirmation_from"]
    assert m.EVENT_RULE["stress"]["slippage_cap_frac_of_mid"] is None
    assert m.ARMS == {"k050": 0.5, "k075": 0.75, "k100": 1.0}
    assert set(m.NEIGHBOURS.values()) == {0.25, 1.25}


def test_the_chain_samples_smokes_then_fetches_and_studies_on_two_workers():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch", "study"]
    fetches = [s for s in m.CHAIN if s["kind"] == "fetch-intraday-chains"]
    assert len(fetches) == 2 and all(s["task_args"]["workers"] <= 2 for s in fetches)
    assert fetches[0]["task_args"]["requests"].endswith("intraday_sample.json")
    assert fetches[1]["task_args"]["requests"].endswith("intraday_requests.json")


def test_the_parts_are_cut_by_entry_session():
    assert m.part_of("2023-12-29") == "design"
    assert m.part_of("2024-01-02") == "holdout" and m.part_of("2026-07-14") == "holdout"
    assert m.part_of("2026-07-15") == "confirmation"


# --------------------------------------------------------------------------- dates and requests

EVENT = {"symbol": "ABC", "report_date": "2024-04-23", "timing": "after_close", "entry_session": "2024-04-23"}


def test_the_expiries_are_the_modules():
    d = m.trade_dates(EVENT)
    assert d["entry"] == date(2024, 4, 23) and d["reaction"] == date(2024, 4, 24)
    assert d["next"] == date(2024, 4, 25)
    assert d["front"] == date(2024, 4, 26)  # the Friday on or after the reaction date
    assert d["back"] == date(2024, 5, 17)  # the third Friday at least 21 days after the front
    assert (d["back"] - d["front"]).days >= 21


def test_an_event_reads_six_requests_and_a_control_four():
    d = m.trade_dates(EVENT)
    ev = m.requests_for("ABC", d, with_next=True)
    ctl = m.requests_for("ABC", d, with_next=False)
    assert len(ev) == 6 and len(ctl) == 4
    entry = [r for r in ev if r["kind"] == "entry"]
    assert {r["expiration"] for r in entry} == {"2024-04-26", "2024-05-17"}
    assert all(r["start"] == "15:35" and r["end"] == "15:50" and r["required"] == ["15:40"] for r in entry)
    reaction = [r for r in ev if r["kind"] == "reaction"]
    assert all(r["session"] == "2024-04-24" and r["required"] == ["10:00"] for r in reaction)
    assert all(r["year"] == 2024 and r["strike_range"] == 30 and r["interval"] == "5m" for r in ev)


def test_the_exit_waits_at_most_to_ten_thirty():
    assert m.exit_bars() == ["10:00", "10:05", "10:10", "10:15", "10:20", "10:25", "10:30"]


# --------------------------------------------------------------------------- strikes


def test_a_strike_is_the_nearest_on_its_own_side_within_a_quarter_em():
    listed = [95.0, 97.5, 100.0, 102.5, 105.0]
    assert m.pick_strike(listed, 102.6, 100.0, 4.0, "C", 0.25) == (102.5, None)
    assert m.pick_strike(listed, 97.4, 100.0, 4.0, "P", 0.25) == (97.5, None)
    # the nearest call strike is 2.5 from a 100.0 target... but at-the-money is not on the call's side
    assert m.pick_strike(listed, 100.4, 100.0, 4.0, "C", 0.25) == (None, "strike_off_target")
    # widely spaced strikes: the nearest is more than a quarter EM away, so the arm is dropped
    assert m.pick_strike([90.0, 110.0], 104.0, 100.0, 4.0, "C", 0.25) == (None, "strike_off_target")
    assert m.pick_strike([90.0, 95.0], 104.0, 100.0, 4.0, "C", 0.25) == (None, "no_strike")


def test_the_atm_strike_needs_both_fronts_and_the_back_call_with_vols():
    q = {"bid": 1.0, "ask": 1.1, "iv": 0.5}
    front = {("C", 100.0): q, ("P", 100.0): q, ("C", 101.0): q}
    back = {("C", 100.0): q, ("C", 101.0): q}
    assert m.atm_strike(front, back, 100.8) == 100.0  # 101 has no front put
    assert m.atm_strike(front, {("C", 100.0): {**q, "iv": None}}, 100.0) is None


def test_the_spread_limit_is_a_share_of_mid():
    assert m.spread_ok({"bid": 1.0, "ask": 1.1}, 0.10)
    assert not m.spread_ok({"bid": 1.0, "ask": 1.2}, 0.10)
    assert not m.spread_ok({"bid": 0.0, "ask": 0.05}, 0.10)


def test_an_unquoted_leg_closes_against_the_position():
    entry = {"bid": 1.0, "ask": 1.4}
    assert m.adverse_exit(m.SHORT, "C", 100.0, 103.0, entry) == pytest.approx(3.4)
    assert m.adverse_exit(m.LONG, "C", 100.0, 103.0, entry) == pytest.approx(2.6)
    assert m.adverse_exit(m.LONG, "P", 100.0, 103.0, entry) == 0.0


# --------------------------------------------------------------------------- pricing


def _q(bid, ask, iv=0.5, delta=0.0):
    return {"bid": bid, "ask": ask, "iv": iv, "delta": delta, "underlying": None}


LEGS = [(m.SHORT, "front", "C", 105.0), (m.LONG, "back", "C", 105.0), (m.SHORT, "front", "P", 95.0),
        (m.LONG, "back", "P", 95.0)]  # fmt: skip
IN = [_q(1.0, 1.2), _q(2.0, 2.2), _q(1.0, 1.2), _q(2.0, 2.2)]
OUT = [{"quote": _q(0.1, 0.2)}, {"quote": _q(1.5, 1.7)}, {"quote": _q(0.1, 0.2)}, {"quote": _q(1.5, 1.7)}]


def test_mid_pricing_is_the_debit_and_the_spreads_value_with_fees():
    p = m.price("ABC", LEGS, IN, OUT, fill="mid")
    open_fee = fees.ic_open_fee("ABC", 1, legs=4, sell_legs=2, ndigits=4)
    close_fee = fees.ic_close_fee("ABC", 1, legs=4, sell_legs=2, ndigits=4)
    assert p["paid"] == pytest.approx(200.0 + open_fee)
    assert p["net"] == pytest.approx(290.0 - close_fee - 200.0 - open_fee)


def test_the_verdict_fill_sits_between_mid_and_natural_and_the_stress_below_it():
    mid = m.price("ABC", LEGS, IN, OUT, fill="mid")["net"]
    verdict = m.price("ABC", LEGS, IN, OUT)["net"]
    natural = m.price("ABC", LEGS, IN, OUT, fill="natural")["net"]
    stress = m.price("ABC", LEGS, IN, OUT, stress=m.EVENT_RULE["stress"])["net"]
    assert natural < verdict < mid and stress < verdict


def test_a_credit_is_not_a_calendar():
    upside_down = [_q(2.0, 2.2), _q(1.0, 1.2), _q(2.0, 2.2), _q(1.0, 1.2)]
    assert m.price("ABC", LEGS, upside_down, OUT) == {"skip": "no_debit"}


def test_an_adverse_leg_is_priced_as_given_and_not_slipped_again():
    out = [*OUT[:3], {"adverse": 1.0}]
    a = m.price("ABC", LEGS, IN, out, fill="mid")
    b = m.price("ABC", LEGS, IN, OUT, fill="mid")
    assert a["net"] == pytest.approx(b["net"] - 60.0)  # the long put sold at 1.0, not its 1.6 mid


# --------------------------------------------------------------------------- one window, end to end


class FakeBars:
    """Invented chains: strikes 90..110 by 1, front and back, at the entry bar and the exit bars."""

    def __init__(self, exit_spot=101.0, missing=frozenset()):
        self.exit_spot, self.missing = exit_spot, missing

    def chain(self, symbol, session, expiry, bar):
        front = expiry == date(2024, 4, 26)
        entry = session == date(2024, 4, 23)
        spot = 100.0 if entry else self.exit_spot
        out = {}
        for k in range(90, 111):
            for r in ("C", "P"):
                if (r, float(k), expiry, bar) in self.missing:
                    continue
                intr = max(spot - k, 0) if r == "C" else max(k - spot, 0)
                tv = (2.0 if front else 3.0) if entry else (0.1 if front else 2.4)
                mid_ = intr + tv * max(0.2, 1 - abs(k - spot) / 15)
                w = 0.04 * mid_
                out[(r, float(k))] = _q(
                    round(mid_ - w / 2, 4), round(mid_ + w / 2, 4), 0.9 if front else 0.45
                )
        return out

    def spot(self, symbol, session, bar, *expiries):
        return 100.0 if session == date(2024, 4, 23) else self.exit_spot


def test_one_window_places_its_strikes_at_k_em_and_prices_the_exit():
    d = m.trade_dates(EVENT)
    bars = FakeBars()
    st, why = m.setup(bars, "ABC", d)
    assert why is None and st["em"]["expected_move_method"] == "event_variance"
    em = st["em"]["expected_move_dollars"]
    t = m.window_trade(bars, "ABC", d, 0.5, st, readings=False)
    assert "skip" not in t
    assert t["call_k"] == round(100 + 0.5 * em) and t["put_k"] == round(100 - 0.5 * em)
    assert t["adverse_legs"] == 0 and t["move_em"] == pytest.approx(1.0 / em)
    assert t["natural_ret"] < t["ret"] < t["mid_ret"] and t["stress_ret"] < t["ret"]


def test_a_leg_unquoted_at_ten_takes_the_next_bar_then_the_adverse_close():
    d = m.trade_dates(EVENT)
    st, _ = m.setup(FakeBars(), "ABC", d)
    em = st["em"]["expected_move_dollars"]
    k = float(round(100 + 0.5 * em))
    late = FakeBars(missing={("C", k, date(2024, 4, 26), "10:00")})
    assert m.window_trade(late, "ABC", d, 0.5, st)["adverse_legs"] == 0
    gone = FakeBars(missing={("C", k, date(2024, 4, 26), b) for b in m.exit_bars()})
    assert m.window_trade(gone, "ABC", d, 0.5, st)["adverse_legs"] == 1


def test_a_strike_the_spread_limit_removes_drops_the_arm():
    d = m.trade_dates(EVENT)
    st, _ = m.setup(FakeBars(), "ABC", d)
    st = {**st, "listed": {"C": [], "P": st["listed"]["P"]}}
    assert m.window_trade(FakeBars(), "ABC", d, 0.5, st) == {"skip": "no_strike"}


# --------------------------------------------------------------------------- the rule


def test_holm_steps_down():
    assert m.holm({"a": 0.01, "b": 0.02, "c": 0.04}, 0.05) == {"a": True, "b": True, "c": True}
    assert m.holm({"a": 0.01, "b": 0.03, "c": 0.04}, 0.05) == {"a": True, "b": False, "c": False}
    assert m.holm({"a": 0.02, "b": 0.03, "c": 0.04}, 0.05) == {"a": False, "b": False, "c": False}


def _part(trades=200, mean=0.05, t=3.0, p=0.001, halves=(0.04, 0.06), names=(0.05, 0.05), stress=0.01,
          best=0.03, net=10.0):  # fmt: skip
    return {"trades": trades, "pairs": trades, "mean_ret": mean, "mean_net": net,
            "pair": {"mean": mean, "t": t, "p": p}, "time_halves": list(halves), "name_halves": list(names),
            "stress_mean": stress, "without_best": best}  # fmt: skip


def test_a_design_that_fails_leaves_the_later_parts_unread():
    v = m.judge_event({"design": _part(stress=-0.01), "holdout": _part(), "confirmation": _part()}, True)
    assert v["verdict"] == m.FAIL and v["holdout"] == m.NOT_READ and v["confirmation"] == m.NOT_READ


def test_holm_alone_can_fail_a_design():
    v = m.judge_event({"design": _part(), "holdout": _part(), "confirmation": _part()}, False)
    assert v["verdict"] == m.FAIL and not v["checks"]["pair_holm"]


def test_a_pass_needs_the_confirmation_positive():
    parts = {"design": _part(), "holdout": _part(trades=120), "confirmation": _part(trades=30, net=-1.0)}
    assert m.judge_event(parts, True)["verdict"] == m.FAIL
    parts["confirmation"] = _part(trades=30, net=1.0)
    assert m.judge_event(parts, True)["verdict"] == m.PASS


def test_too_few_trades_is_no_verdict():
    assert (
        m.judge_event({"design": _part(trades=149), "holdout": _part(), "confirmation": _part()}, True)[
            "verdict"
        ]
        == m.NO_VERDICT
    )
    assert (
        m.judge_event({"design": _part(), "holdout": _part(trades=99), "confirmation": _part()}, True)[
            "verdict"
        ]
        == m.NO_VERDICT
    )


def test_a_year_under_the_coverage_floor_is_not_judged():
    cov = {
        "years": {
            "2020": {"share_complete": 0.79},
            "2021": {"share_complete": 0.8},
            "2022": {"share_complete": None},
        }
    }
    assert m.judged_years(cov, 0.80) == {2021}


def test_the_move_buckets():
    assert (
        m.move_bucket(0.3) == "under_0.5"
        and m.move_bucket(-0.7) == "0.5_to_1"
        and m.move_bucket(1.0) == "over_1"
    )


# --------------------------------------------------------------------------- the store's answers


def _store():
    conn = sqlite3.connect(":memory:")
    conn.executescript(intradayfetch.SCHEMA)
    return conn


REQ = {"symbol": "ABC", "session": "2024-04-23", "expiration": "2024-04-26", "start": "15:35", "end": "15:50",
       "interval": "5m", "required": ["15:40"]}  # fmt: skip


def _answer(conn, status, detail=None):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(intraday_chain_requests)")]
    row = {
        **{k: REQ[k] for k in ("symbol", "session", "expiration", "start", "end", "interval")},
        "status": status,
        "detail": detail,
        "fetched_at": 0.0,
    }
    row = {k: v for k, v in row.items() if k in cols}
    names = ", ".join('"' + k + '"' for k in row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO intraday_chain_requests ({names}) VALUES ({marks})", tuple(row.values()))


def test_a_request_is_complete_with_a_priced_quote_at_its_bar_and_no_underlying():
    conn = _store()
    assert m.request_state(conn, REQ) == "unanswered"
    _answer(conn, "complete")
    assert m.request_state(conn, REQ) == "incomplete"
    conn.execute(
        "INSERT INTO intraday_chain_quotes VALUES "
        "('ABC','2024-04-23','2024-04-26','15:40',100,'C',1.0,1.1,0.5,0.9,NULL)"
    )
    assert m.request_state(conn, REQ) == "complete"


def test_an_empty_answer_and_a_refusal_are_told_apart():
    conn = _store()
    _answer(conn, "empty")
    assert m.request_state(conn, REQ) == "empty"
    conn = _store()
    _answer(conn, "refused", "session_in_use: another login")
    assert m.request_state(conn, REQ) == "unanswered"


def test_the_parity_forward_stands_for_spot():
    chain = {("C", 100.0): _q(2.9, 3.1), ("P", 100.0): _q(1.9, 2.1), ("C", 105.0): _q(0.9, 1.1)}
    assert m.parity_spot(chain) == pytest.approx(101.0)
