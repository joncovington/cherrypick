"""A trade opened inside its own time exit skips it (user, 2026-11-06).

The 21-day exit was tested on trades opened about 45 days out. On one opened at 21 DTE or fewer it
only said close on the first day, so it does not apply there: the trade is managed by its take
profit, stop and the expiry close."""

from datetime import date

from test_evaluate import fired, leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]
EXP = "2027-03-19"
SESSION = date(2027, 3, 1)  # 18 DTE


def occ(exp: str, right: str, strike: float) -> str:
    return f"XYZ   {exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}"


def chain(on: str, legs: list[dict], role: str = "open", extra: list[dict] | None = None) -> dict:
    rows = [
        {
            "symbol": occ(x["expiration"], x["right"], x["strike"]),
            "action": "Sell to Open" if x["quantity"] < 0 else "Buy to Open",
            "quantity": abs(x["quantity"]),
            "price": x["open_price"],
        }
        for x in legs
    ]
    net = -sum(x["quantity"] * x["open_price"] * 100 for x in legs)
    orders = [{"executed_at": f"{on}T15:00:00Z", "role": role, "net": net, "fees": -2.0, "legs": rows}]
    return {"cash": net, "fees": -2.0, "orders": orders + (extra or [])}


def condor(exp=EXP):
    # Opened for 1.50; worth 1.10 now: 27% captured, no take profit, no stop.
    return [
        leg(-1, "P", 90, exp, 1.0, 0.7),
        leg(1, "P", 85, exp, 0.4, 0.3),
        leg(-1, "C", 110, exp, 1.2, 0.9),
        leg(1, "C", 115, exp, 0.3, 0.2),
    ]


def run(legs, c, session=SESSION):
    return ev.evaluate_trade("XYZ", legs, {"last": 100.0}, session, 100_000.0, RULES, c)


def test_a_condor_opened_at_18_dte_skips_the_21_day_exit():
    legs = condor()
    t = run(legs, chain("2027-03-01", legs))
    assert "credit.time_exit" not in fired(t)
    assert "time_exit" not in t["exit_levels"]
    assert t["time_exit_skipped"] == {
        "rule": "credit.time_exit",
        "expiration": EXP,
        "opened_on": "2027-03-01",
        "entry_dte": 18,
        "limit": 21,
    }
    assert t["recommendation"]["action"] == ev.HOLD
    assert "inside the 21-day time exit, so that exit does not apply" in t["explanation"]


def test_a_condor_opened_45_days_out_still_exits_at_21():
    legs = condor()
    t = run(legs, chain("2027-02-02", legs))
    assert t["time_exit_skipped"] is None
    assert "credit.time_exit" in fired(t)


def test_without_a_chain_the_exit_stands():
    # The guard shown failing: the entry date is unknown, so the old behaviour stands.
    t = run(condor(), None)
    assert t["time_exit_skipped"] is None
    assert "credit.time_exit" in fired(t)


def test_a_roll_out_counts_from_the_roll():
    # Opened at 18 DTE on an earlier expiry, then rolled out to EXP 46 days before it: the exit applies.
    first = condor("2027-02-19")
    legs = condor()
    rolled = chain("2027-02-01", legs, role="roll")["orders"]
    t = run(legs, chain("2027-02-01", first, extra=rolled))
    assert t["time_exit_skipped"] is None
    assert "credit.time_exit" in fired(t)


def test_a_debit_spread_bought_at_14_dte_skips_its_exit():
    legs = [leg(1, "C", 100, EXP, 3.0, 3.2), leg(-1, "C", 105, EXP, 1.0, 1.1)]
    t = run(legs, chain("2027-03-05", legs), session=date(2027, 3, 8))
    assert t["kind"] == "debit"
    assert "debit.time_exit" not in fired(t)
    assert t["time_exit_skipped"]["rule"] == "debit.time_exit" and t["time_exit_skipped"]["entry_dte"] == 14


def test_a_long_call_bought_at_10_dte_skips_the_14_day_exit():
    legs = [leg(1, "C", 100, EXP, 3.0, 3.1)]
    t = run(legs, chain("2027-03-09", legs), session=date(2027, 3, 10))
    assert "debit.time_exit" not in fired(t)
    assert t["time_exit_skipped"]["limit"] == 14


def test_the_skip_can_be_turned_off():
    rules, _ = _rules.effective({"credit": {"time_exit": {"skip_if_entered_inside": False}}})
    legs = condor()
    t = ev.evaluate_trade("XYZ", legs, {"last": 100.0}, SESSION, 100_000.0, rules, chain("2027-03-01", legs))
    assert "credit.time_exit" in fired(t)
