"""The expected move is the report's move (declared boundary, 2026-11-06).

It was 0.85 x the front ATM straddle. The front expiry can sit up to nine days after the report, so
that straddle also carries ordinary time value and overstated the move. It now comes from two
expiries that both follow the report, which separates event variance from ordinary variance, and
falls back to the straddle number (recording why) when the split cannot be made.

Worked example used throughout: spot 100, front vol 0.80 at 3 days, back vol 0.45 at 31 days
(calendar days / 365, as `time_spread.event_variance` does):

    T_f = 3/365 = 0.0082192        T_b = 31/365 = 0.0849315
    w_f = 0.80^2 * T_f = 0.0052603 w_b = 0.45^2 * T_b = 0.0171986
    ex-event variance rate  sx^2 = (w_b - w_f) / (T_b - T_f) = 0.155625   (sx = 39.4%)
    event variance          E^2  = w_f - sx^2 * T_f = 0.0039812           (E = 6.3096% of spot)
    one-SD event move       = 100 * E = $6.31
    event expected move     = 0.85 * sqrt(2/pi) * 6.31 = $4.28   (the suite's units: 0.85 x a straddle)
    the old number, from the front straddle the same vol implies ($5.79): 0.85 * 5.79 = $4.92
"""

import argparse
import json
import sqlite3
from datetime import date, timedelta

import pytest

from cherrypick.earnings import db_paper, scanner
from cherrypick.earnings import strat_test_harness as harness
from cherrypick.earnings.strategies import directional_credit_spread, iron_condor, iron_fly

T0 = date(2026, 11, 2)  # a fixed "today"; nothing here reads the host clock
FRONT, BACK = T0 + timedelta(days=3), T0 + timedelta(days=31)
SF, SB = 0.80, 0.45
LEG_MID = 2.8934  # half the 5.7869 straddle a 0.80 vol implies at 3 days on a $100 spot
EVENT_EM = 4.2792
OLD_EM = 0.85 * 2 * LEG_MID  # 4.919


class _FixedDate(date):
    @classmethod
    def today(cls):
        return T0


# --------------------------------------------------------------------------- the pure function


def test_the_worked_example():
    em = scanner.resolve_expected_move(LEG_MID, LEG_MID, SF, SB, 100.0, 3, 31)
    assert em["expected_move_method"] == "event_variance" and em["expected_move_reason"] is None
    assert em["event_base_vol"] == pytest.approx(0.39449, abs=1e-4)
    assert em["event_move_sd_dollars"] == pytest.approx(6.3096, abs=1e-3)  # one standard deviation
    assert em["expected_move_dollars"] == pytest.approx(EVENT_EM, abs=1e-3)
    assert em["expected_move_event"] == em["expected_move_dollars"]
    assert em["expected_move_straddle"] == pytest.approx(OLD_EM)  # beside it, for the comparison
    assert em["expected_move_dollars"] < em["expected_move_straddle"]  # the time value is gone


def test_the_event_move_is_in_the_units_the_straddle_move_was():
    """With no ordinary vol (back total variance equals front's per day), the event IS the straddle:
    the two methods agree to within the sqrt(2/pi) approximation, so thresholds keep their meaning."""
    # sx -> small: front 3d at 0.80, back 31d at 0.80 * sqrt(3/31 + eps) is not needed; use sx = 0.05.
    sx, e_sd = 0.05, 0.06
    tf, tb = 3 / 365, 31 / 365
    sf = ((sx**2 * tf + e_sd**2) / tf) ** 0.5
    sb = ((sx**2 * tb + e_sd**2) / tb) ** 0.5
    straddle_mid = 0.5 * ((2 / 3.141592653589793) ** 0.5) * e_sd * 100.0  # per leg, ~ no time value
    em = scanner.resolve_expected_move(straddle_mid, straddle_mid, sf, sb, 100.0, 3, 31)
    assert em["event_move_sd_dollars"] == pytest.approx(6.0, abs=1e-6)
    assert em["expected_move_dollars"] == pytest.approx(em["expected_move_straddle"], rel=1e-9)


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ((None, SB, 3, 31), "missing_inputs"),
        ((SF, None, 3, 31), "missing_inputs"),
        ((SF, SB, None, 31), "missing_inputs"),
        ((SF, SB, 3, None), "missing_inputs"),
        ((SF, SB, 31, 31), "bad_inputs"),  # T_b not above T_f
        ((SF, SB, 31, 3), "bad_inputs"),
        ((SF, SB, 0, 31), "bad_inputs"),  # the front expires today
        ((0.80, 0.30, 10, 11), "back_variance_not_above_front"),  # sx^2 <= 0
        ((0.30, 0.45, 3, 31), "no_event_premium"),  # E^2 <= 0
    ],
)
def test_it_falls_back_to_the_straddle_and_says_why(args, reason):
    em = scanner.resolve_expected_move(LEG_MID, LEG_MID, *args[:2], 100.0, *args[2:])
    assert em["expected_move_method"] == "straddle_fallback"
    assert em["expected_move_reason"] == reason
    assert em["expected_move_dollars"] == pytest.approx(OLD_EM)
    assert em["expected_move_event"] is None and em["expected_move_straddle"] == pytest.approx(OLD_EM)


def test_the_straddle_method_is_the_old_definition_and_is_labelled_so():
    em = scanner.resolve_expected_move(LEG_MID, LEG_MID, SF, SB, 100.0, 3, 31, "straddle")
    assert em["expected_move_dollars"] == pytest.approx(OLD_EM)
    assert (em["expected_move_method"], em["expected_move_reason"]) == (
        "straddle_fallback",
        "method_straddle_configured",
    )


def test_an_unknown_method_in_config_is_refused_not_guessed():
    assert scanner.expected_move_method({}) == "event_variance"
    assert scanner.expected_move_method({"expected_move_method": "straddle"}) == "straddle"
    with pytest.raises(ValueError):
        scanner.expected_move_method({"expected_move_method": "event"})


def test_the_screen_result_carries_the_move_beside_the_term_inputs():
    ts = scanner.compute_expected_move_and_term_structure(
        LEG_MID, LEG_MID, SF, SB, 100.0, FRONT, BACK, today=T0
    )
    assert ts["expected_move_dollars"] == pytest.approx(EVENT_EM, abs=1e-3)
    assert ts["expected_move_pct"] == pytest.approx(EVENT_EM / 100.0, abs=1e-5)
    assert ts["expected_move_method"] == "event_variance"
    assert ts["expected_move_straddle"] == pytest.approx(OLD_EM)
    assert (ts["front_dte"], ts["back_dte"]) == (3, 31)
    legacy = scanner.compute_expected_move_and_term_structure(
        LEG_MID, LEG_MID, SF, SB, 100.0, FRONT, BACK, today=T0, method="straddle"
    )
    assert legacy["expected_move_dollars"] == pytest.approx(OLD_EM)


# --------------------------------------------------------------------------- the consumers


def _chain_stub(monkeypatch, entries=None, *, back_iv=SB):
    monkeypatch.setattr(scanner, "et_today", lambda: T0)
    monkeypatch.setattr(
        scanner,
        "fetch_quote_and_expirations",
        lambda s: {"ok": True, "price": 100.0, "expirations": [FRONT, BACK]},
    )
    monkeypatch.setattr(scanner, "select_front_expiration", lambda *a, **k: (FRONT, None))
    call = {"mid": LEG_MID, "iv": SF, "delta": 0.5, "bid": LEG_MID - 0.05, "ask": LEG_MID + 0.05}
    put = {"mid": LEG_MID, "iv": SF, "bid": LEG_MID - 0.05, "ask": LEG_MID + 0.05}
    monkeypatch.setattr(
        scanner,
        "fetch_front_back_atm_entries",
        lambda s, front, back, p: {
            "ok": True,
            "front_call": call,
            "front_put": put,
            "back_call": {**call, "iv": SF if front == back else back_iv},
        },
    )
    monkeypatch.setattr(scanner, "fetch_liquidity_criteria", lambda *a: {})
    monkeypatch.setattr(scanner, "compute_winrate", lambda *a: {"ok": False})
    monkeypatch.setattr(scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": False})
    if entries is not None:
        monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "chain": {str(FRONT): entries}})


def _entry(option_type, strike, mid):
    return {
        "symbol": f"X{option_type}{int(strike)}",
        "option_type": option_type,
        "strike_price": float(strike),
        "mid": mid,
    }


def _grid():
    out = []
    for k in range(80, 121):
        d = abs(k - 100)
        out += [_entry("C", k, round(4.0 / (1 + d), 2)), _entry("P", k, round(4.0 / (1 + d), 2))]
    return out


def test_the_floor_gate_reads_the_event_move(monkeypatch, base_strategy_config, good_criteria):
    """Straddle move $4.92 clears a $4.50 floor; the event move $4.28 does not."""
    _chain_stub(monkeypatch)
    monkeypatch.setattr(iron_fly, "date", _FixedDate)
    config = {"strategies": {"iron_fly": {"min_expected_move_dollars": 4.50}}}
    crit = iron_fly.fetch_price_and_term_structure("AAA", FRONT, "After market close", config)["criteria"]
    assert crit["expected_move_dollars"] == pytest.approx(EVENT_EM, abs=1e-3)
    assert crit["expected_move_method"] == "event_variance"
    assert crit["expected_move_straddle"] == pytest.approx(OLD_EM)
    full = {**good_criteria, **crit}
    cfg = {**base_strategy_config, "min_expected_move_dollars": 4.50}
    assert "expected_move_below_minimum" in iron_fly.apply_tiering(full, cfg)["reject_reasons"]
    # The study's explicit old definition leaves the same name above the floor.
    old = iron_fly.fetch_price_and_term_structure(
        "AAA", FRONT, "After market close", {**config, "expected_move_method": "straddle"}
    )["criteria"]
    assert old["expected_move_dollars"] == pytest.approx(OLD_EM)
    assert "expected_move_below_minimum" not in iron_fly.apply_tiering({**full, **old}, cfg)["reject_reasons"]


def test_the_condor_places_its_short_strikes_at_the_event_move(monkeypatch):
    """100 +/- 4.28 lands on 104 and 96; the straddle move 4.92 would have put them on 105 and 95."""
    _chain_stub(monkeypatch, _grid())
    config = {"strategies": {"iron_fly": {}, "iron_condor": {}}}
    out = iron_condor.fetch_iron_condor_order("AAA", FRONT, "After market close", config)
    assert out["ok"], out
    assert (out["short_call_strike"], out["short_put_strike"]) == (104.0, 96.0)
    assert out["expected_move"] == pytest.approx(EVENT_EM, abs=1e-3)
    assert out["expected_move_method"] == "event_variance"
    old = iron_condor.fetch_iron_condor_order(
        "AAA", FRONT, "After market close", {**config, "expected_move_method": "straddle"}
    )
    assert (old["short_call_strike"], old["short_put_strike"]) == (105.0, 95.0)


def test_the_credit_spread_anchors_its_short_strike_off_the_event_move(monkeypatch):
    _chain_stub(monkeypatch, _grid())
    monkeypatch.setattr(
        scanner, "select_side", lambda *a: {"ok": True, "side": "put", "skew": 0.1, "put_iv": 0.5}
    )
    config = {"strategies": {"directional_credit_spread": {}}}
    out = directional_credit_spread.fetch_directional_credit_spread_order(
        "AAA", FRONT, "After market close", config
    )
    assert out["expected_move"] == pytest.approx(EVENT_EM, abs=1e-3), out
    assert out["expected_move_method"] == "event_variance"


def test_no_back_expiry_falls_back_and_says_so(monkeypatch):
    _chain_stub(monkeypatch, _grid())
    monkeypatch.setattr(
        scanner,
        "fetch_quote_and_expirations",
        lambda s: {"ok": True, "price": 100.0, "expirations": [FRONT]},
    )
    out = iron_condor.fetch_iron_condor_order(
        "AAA", FRONT, "After market close", {"strategies": {"iron_condor": {}}}
    )
    assert out["expected_move"] == pytest.approx(OLD_EM)
    assert (out["expected_move_method"], out["expected_move_reason"]) == (
        "straddle_fallback",
        "no_back_expiration",
    )


# --------------------------------------------------------------------------- what is recorded


def test_the_method_is_recorded_on_the_review_row_and_the_entry_context(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    ts = scanner.compute_expected_move_and_term_structure(
        LEG_MID, LEG_MID, SF, SB, 100.0, FRONT, BACK, today=T0
    )
    criteria = {
        "price": 100.0,
        **scanner.move_inputs(ts),
        "expected_move_dollars": ts["expected_move_dollars"],
    }
    spec = scanner.build_entry_review_spec(
        "2026-11-02", "AAA", "After market close", criteria, None, False, "x"
    )
    assert db_paper.cmd_save_entry_review(argparse.Namespace(data=json.dumps(spec)))["ok"]
    conn = sqlite3.connect(tmp_path / "paper_trades.db")
    row = conn.execute(
        "SELECT expected_move, expected_move_method, expected_move_reason, expected_move_straddle,"
        " expected_move_event, criteria_json FROM entry_reviews"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(EVENT_EM, abs=1e-3) and row[1] == "event_variance" and row[2] is None
    assert row[3] == pytest.approx(OLD_EM) and row[4] == pytest.approx(EVENT_EM, abs=1e-3)
    assert json.loads(row[5])["event_move_sd_dollars"] == pytest.approx(6.3096, abs=1e-3)
    ctx = harness._entry_context(criteria, 0.5)
    assert ctx["expected_move_method"] == "event_variance"
    assert ctx["expected_move_straddle"] == pytest.approx(OLD_EM)
    assert ctx["expected_move_event"] == pytest.approx(EVENT_EM, abs=1e-3)


def test_a_fallback_row_records_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    ts = scanner.compute_expected_move_and_term_structure(LEG_MID, LEG_MID, SF, SB, 100.0)  # no expiries
    spec = scanner.build_entry_review_spec(
        "2026-11-02", "AAA", "After market close", {"price": 100.0, **ts}, None, False, "x"
    )
    assert db_paper.cmd_save_entry_review(argparse.Namespace(data=json.dumps(spec)))["ok"]
    conn = sqlite3.connect(tmp_path / "paper_trades.db")
    row = conn.execute(
        "SELECT expected_move_method, expected_move_reason, expected_move_event FROM entry_reviews"
    ).fetchone()
    conn.close()
    assert row == ("straddle_fallback", "missing_inputs", None)
