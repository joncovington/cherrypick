"""The PMCC view and its rules: recognition by delta at entry or intent, linking the shorts sold against
the long, the width check, assignment exposure, the long's roll, roll candidates, precedence with the
diagonal rules, and failing closed. Synthetic names, prices and histories, as in test_wheel.py."""

from datetime import date

import pytest
from cherrypick.core import bs
from test_wheel import occ, opt_leg, rd, share_leg, tx

from cherrypick.manage import evaluate as ev
from cherrypick.manage import pmcc
from cherrypick.manage import rules as _rules

SESSION = "2026-10-13"
ENTRY = "2026-04-01"
LONG_EXP = "2028-01-21"
RATE = 0.04
LONG = occ(LONG_EXP, "C", 40)
OLD_SHORT = occ("2026-05-15", "C", 55)
SHORT = occ("2026-11-20", "C", 55)


def fill(strike, vol, close=50.0, div=0.0, day=ENTRY, exp=LONG_EXP):
    """The price a long call would have filled at, entry day, at `vol`."""
    years = (date.fromisoformat(exp) - date.fromisoformat(day)).days / 365.0
    return round(bs.price(close, strike, years, vol, "C", RATE, div), 2)


DEEP = fill(40, 0.30)  # about 0.83 delta at entry


def history(long=LONG, long_price=DEEP, short=SHORT, short_price=1.20, own_order=True):
    """Bought the long with a May short; the May short expired; the November short sold on its own."""
    rows = [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", long, 1, long_price),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", OLD_SHORT, 1, 1.50),
        rd("2026-05-15T22:00:00Z", "Expiration", "Buy to Close", OLD_SHORT, 1),
    ]
    rows.append(tx(2 if own_order else 1, "2026-09-25T15:00:00Z", "Sell to Open", short, 1, short_price))
    return rows


HIST = {"XYZ": {"closes": {ENTRY: 50.0}, "dividends": [], "splits": [], "through": "2026-10-12"}}


def snapshot(rows, legs, spot=50.0, metrics=None, hist=HIST, income=None, bands=None, extra=None):
    acct = {
        "account": "****1234",
        "designated": True,
        "underlyings": [{"underlying": "XYZ", "legs": legs}],
        "balances": {"net_liquidating_value": 100_000},
    }
    snap = {
        "session": SESSION,
        "positions": {"ok": True, "accounts": [acct]},
        "metrics": {"XYZ": {"last": spot, **(metrics or {})}},
        "transactions": {"XYZ": rows},
        "working_orders": [],
        "income": income,
        "skate_bands": bands,
        "underlying_history": hist,
    }
    snap.update(extra or {})
    return snap


def evaluate(snap, overrides=None):
    return ev.evaluate(snap, _rules.effective(overrides)[0])["accounts"][0]


def legs(long_mid=14.0, short_mid=0.60, long=LONG, short=SHORT, long_price=DEEP, short_price=1.20):
    return [opt_leg(long, 1, long_mid, long_price), opt_leg(short, -1, short_mid, short_price)]


def view_of(account):
    (v,) = account["pmcc"]["positions"]
    return v


def trade_of(account, symbol):
    return next(t for t in account["trades"] if any(leg["symbol"] == symbol for leg in t["legs"]))


def fired(trade):
    return {f["rule"]: f for f in trade["fired"]}


# --------------------------------------------------------------------------- recognition
def test_a_long_entered_deep_is_a_pmcc():
    a = evaluate(snapshot(history(), legs()))
    v = view_of(a)
    assert v["recognized"] and v["reconciled"], v["problems"]
    assert 0.80 <= v["long"]["delta_at_entry"] < 0.90
    assert v["recognized_by"].startswith(f"entered at {v['long']['delta_at_entry']:.2f} delta")
    assert v["long"]["entry_fills"][0]["close"] == 50.0 and v["long"]["entry_fills"][0]["div_yield"] == 0.0


def test_a_long_entered_near_the_money_stays_a_diagonal():
    near = occ(LONG_EXP, "C", 50)
    price = fill(50, 0.30)
    a = evaluate(
        snapshot(history(long=near, long_price=price), legs(long=near, long_price=price, long_mid=9.0))
    )
    v = view_of(a)
    assert not v["recognized"] and "under 0.80): a diagonal" in v["recognized_by"]
    # The view lists it; no PMCC rule judges it, whatever its numbers.
    assert not any(r.startswith("pmcc") for r in fired(trade_of(a, SHORT)))


def test_the_intent_decides_either_way():
    near = occ(LONG_EXP, "C", 50)
    price = fill(50, 0.30)
    snap = snapshot(history(long=near, long_price=price), legs(long=near, long_price=price, long_mid=9.0))
    v = view_of(evaluate(snap, {"positions": {"XYZ": {"intent": "pmcc"}}}))
    assert v["recognized"] and v["recognized_by"] == "intent: pmcc"
    v = view_of(evaluate(snapshot(history(), legs()), {"positions": {"XYZ": {"intent": "diagonal"}}}))
    assert not v["recognized"] and v["recognized_by"] == "intent: diagonal"
    assert v["long"]["delta_at_entry"] > 0.80  # out whatever its delta


@pytest.mark.parametrize(
    "hist, why",
    [
        (None, "no close for the name"),
        ({"XYZ": {"closes": {}, "dividends": [], "splits": []}}, f"no close for {ENTRY}"),
        ({"XYZ": {"closes": {ENTRY: 50.0}, "dividends": [], "splits": ["2026-06-01"]}}, "a split since"),
    ],
)
def test_a_delta_at_entry_that_cannot_be_solved_is_unknown_and_does_not_recognise(hist, why):
    v = view_of(evaluate(snapshot(history(), legs(), hist=hist)))
    assert v["long"]["delta_at_entry"] is None and why in v["long"]["delta_at_entry_why"]
    assert not v["recognized"] and v["recognized_by"].startswith("delta at entry unknown")


def test_the_solver_carries_the_dividend_yield():
    """A long-dated call on a high-yield fund: the plan's TLT case, synthesised. At an 80.16 close the
    80 call two and a quarter years out fills at 7.05, under its no-dividend floor (80.16 less the
    discounted strike), so without the yield the model reads it as all intrinsic, a delta of 1; at a
    ~4.3% yield it is near 0.49, and no PMCC."""
    day, exp, close = "2026-10-15", "2029-01-19", 80.16
    long, short = occ(exp, "C", 80), occ("2026-11-20", "C", 85)
    rows = [
        tx(1, f"{day}T15:00:00Z", "Buy to Open", long, 1, 7.05),
        tx(1, f"{day}T15:00:00Z", "Sell to Open", short, 1, 0.30),
    ]
    cur = [opt_leg(long, 1, 7.10, 7.05), opt_leg(short, -1, 0.25, 0.30)]
    # Twelve monthly payments in the year to the entry (about 4.3% at the close), and one older.
    months = [(2025, 10), (2025, 11), (2025, 12), *((2026, m) for m in range(1, 11))]
    paid = [[f"{y}-{m:02d}-01", 0.2875] for y, m in months]
    with_yield = {"XYZ": {"closes": {day: close}, "dividends": paid, "splits": []}}
    v = view_of(evaluate(snapshot(rows, cur, spot=80.16, hist=with_yield)))
    assert v["long"]["entry_fills"][0]["div_yield"] == pytest.approx(12 * 0.2875 / close)
    assert v["long"]["delta_at_entry"] == pytest.approx(0.49, abs=0.03)
    assert not v["recognized"]
    without = {"XYZ": {"closes": {day: close}, "dividends": [], "splits": []}}
    v = view_of(evaluate(snapshot(rows, cur, spot=80.16, hist=without)))
    assert v["long"]["delta_at_entry"] > 0.95  # the guess the yield prevents


@pytest.mark.parametrize(
    "case",
    ["short too far out", "short under the long", "shares held", "two shorts"],
)
def test_other_shapes_are_not_candidates(case):
    session = date.fromisoformat(SESSION)
    cfg = _rules.DEFAULT_RULES["pmcc"]["recognize"]
    base = legs()
    if case == "short too far out":  # the plan's 10-16 TLT: a short 61+ days out is a diagonal
        base[1] = opt_leg(occ("2027-01-15", "C", 55), -1, 1.0, 1.2)
    elif case == "short under the long":
        base[1] = opt_leg(occ("2026-11-20", "C", 35), -1, 15.0, 15.0)
    elif case == "shares held":
        base.append(share_leg(100, 50.0, 48.0))
    else:
        base.append(opt_leg(occ("2026-11-20", "C", 60), -1, 0.2, 0.4))
    assert pmcc.candidate(base, session, cfg) is None
    assert pmcc.candidate(legs(), session, cfg) is not None


def test_the_long_s_days_are_judged_when_it_was_bought():
    # A December long bought on 06-29 had 172 days left: never a PMCC's long. Bought on 04-01 (261
    # days), the same long, with 66 left today, is one, and is due a roll.
    dec = occ("2026-12-18", "C", 40)
    cur = legs(long=dec, long_mid=10.2)

    def bought(day):
        return [
            tx(1, f"{day}T15:00:00Z", "Buy to Open", dec, 1, 11.0),
            tx(2, "2026-09-25T15:00:00Z", "Sell to Open", SHORT, 1, 1.20),
        ]

    assert evaluate(snapshot(bought("2026-06-29"), cur))["pmcc"]["positions"] == []
    v = view_of(evaluate(snapshot(bought(ENTRY), cur)))
    assert v["long"]["days_at_entry"] == 261 and v["long"]["dte"] == 66 and v["long"]["roll"]


# --------------------------------------------------------------------------- linking and money
def test_a_short_sold_in_its_own_order_is_linked_and_its_premium_counted():
    a = evaluate(snapshot(history(), legs()))
    v = view_of(a)
    assert [s["leg"] for s in v["shorts"]] == ["2026-05-15 55C", "2026-11-20 55C"]
    assert [s["status"] for s in v["shorts"]] == ["expiration", "open"]
    assert v["premiums"] == pytest.approx(150.0 + 120.0)
    assert v["long_cost"] == pytest.approx(DEEP * 100)
    assert v["width"]["net_cost"] == pytest.approx(DEEP * 100 - 270.0)
    assert v["fees"] == pytest.approx(-1.1 * 3)
    # The order chains keep the November short apart; the link joins it to the long (test_pmcc_grouping).
    assert v["trade_ids"] == ["XYZ"]
    assert trade_of(a, SHORT)["pmcc"]["recognized"] is True


def test_the_width_check_and_the_p_and_l_if_assigned():
    v = view_of(evaluate(snapshot(history(), legs())))
    w = v["width"]
    net_ps = (DEEP * 100 - 270.0) / 100
    assert w["width"] == 15.0 and w["net_cost_per_share"] == pytest.approx(net_ps)
    assert w["passes"] is True and w["basis"] == "linked orders"
    assert w["pnl_if_assigned"] == pytest.approx((15.0 - net_ps) * 100)
    assert w["pnl_if_assigned_net"] == pytest.approx((15.0 - net_ps) * 100 - 3.3)


def test_skate_yield_is_a_comparison_figure_on_the_long_s_cost():
    v = view_of(evaluate(snapshot(history(), legs(short_mid=0.60))))
    days = (date.fromisoformat(SESSION) - date.fromisoformat(ENTRY)).days
    assert v["skate_yield"]["value"] == pytest.approx((270.0 - 60.0) / (DEEP * 100) * 365 / days)
    assert "comparison figure" in v["skate_yield"]["basis"]


# --------------------------------------------------------------------------- pmcc.width
NARROW = occ("2026-11-20", "C", 45)


def narrow(short_mid=5.60):
    return snapshot(
        history(short=NARROW, short_price=6.00), legs(short=NARROW, short_price=6.00, short_mid=short_mid)
    )


def test_a_failed_width_check_is_rolled_or_closed():
    a = evaluate(narrow())
    v = view_of(a)
    assert v["width"]["passes"] is False and v["width"]["pnl_if_assigned"] < 0
    f = fired(trade_of(a, NARROW))["pmcc.width"]
    assert f["action"] == ev.ROLL_OR_CLOSE and f["confidence"] == 65
    assert f"loses ${-v['width']['pnl_if_assigned']:,.0f}" in f["reason"]
    assert "No roll priced" in f["reason"]  # the snapshot quotes nothing later


def test_a_passing_width_check_fires_nothing():
    a = evaluate(snapshot(history(), legs()))
    assert not any(r.startswith("pmcc") for r in fired(trade_of(a, SHORT)))


# --------------------------------------------------------------------------- pmcc.assignment
ITM = occ("2026-11-20", "C", 48)


def itm(short_mid, metrics=None, spot=50.0):
    return snapshot(
        history(short=ITM, short_price=3.00),
        legs(short=ITM, short_price=3.00, short_mid=short_mid),
        spot,
        metrics,
    )


def test_an_in_the_money_short_with_no_time_value_left_is_exposed():
    a = evaluate(itm(2.03))
    v = view_of(a)
    assert v["short"]["assignment"]["near_zero"] is True and v["short"]["assignment"]["exposed"] is True
    f = fired(trade_of(a, ITM))["pmcc.assignment"]
    assert f["action"] == ev.ROLL_OR_CLOSE and "0.03 of time value" in f["reason"]


def test_a_dividend_bigger_than_the_time_value_before_an_ex_date_is_exposed():
    div = {"ex_dividend_date": "2026-10-20", "dividend_rate_per_share": "0.50"}
    v = view_of(evaluate(itm(2.30, div)))
    assert v["short"]["assignment"]["dividend"] is True and "0.50 dividend" in v["short"]["assignment"]["why"]
    # The same short past its ex-date, or with more time value than the dividend, is not.
    assert view_of(evaluate(itm(2.60, div)))["short"]["assignment"]["exposed"] is False
    late = {"ex_dividend_date": "2026-12-01", "dividend_rate_per_share": "0.50"}
    assert view_of(evaluate(itm(2.30, late)))["short"]["assignment"]["exposed"] is False


def test_an_unknown_dividend_is_said_not_assumed():
    v = view_of(evaluate(itm(2.30, {"ex_dividend_date": "2026-10-20"})))
    a = v["short"]["assignment"]
    assert a["dividend"] is None and a["exposed"] is False and "amount is unknown" in a["why"]


def test_an_out_of_the_money_short_is_not_exposed():
    a = evaluate(snapshot(history(), legs()))
    assert view_of(a)["short"]["assignment"]["exposed"] is False


# --------------------------------------------------------------------------- pmcc.roll_long
def test_a_long_near_its_expiry_or_its_delta_floor_is_reviewed():
    # 100 days left, still deep: the days fire it.
    soon = occ("2027-01-21", "C", 40)
    price = fill(40, 0.30, exp="2027-01-21")
    a = evaluate(
        snapshot(history(long=soon, long_price=price), legs(long=soon, long_price=price, long_mid=10.5))
    )
    v = view_of(a)
    assert v["long"]["dte"] == 100 and v["long"]["roll"] == ["100 days left (roll at 120)"]
    assert fired(trade_of(a, SHORT))["pmcc.roll_long"]["action"] == ev.REVIEW
    # Fallen to the money (spot 41): the delta fires it.
    a = evaluate(snapshot(history(), legs(long_mid=6.0, short_mid=0.05), spot=41.0))
    v = view_of(a)
    assert v["long"]["delta"] < 0.70 and "delta (under 0.70)" in v["long"]["roll"][0]
    assert "pmcc.roll_long" in fired(trade_of(a, SHORT))
    # Neither: nothing.
    assert view_of(evaluate(snapshot(history(), legs())))["long"]["roll"] == []


def test_the_long_s_extrinsic_share_and_today_s_delta():
    v = view_of(evaluate(snapshot(history(), legs(long_mid=14.0))))
    assert v["long"]["extrinsic"] == pytest.approx(4.0)
    assert v["long"]["extrinsic_share"] == pytest.approx(4.0 / 14.0)
    assert 0.70 < v["long"]["delta"] < 1.0


# --------------------------------------------------------------------------- roll candidates
DEC = "2026-12-18"


def quote(strike, bid, ask, exp=DEC):
    return {"strike": strike, "right": "C", "bid": bid, "ask": ask, "option": occ(exp, "C", strike)}


def income(*options, exp=DEC):
    return {"taken_at": "2026-10-12T18:45:00Z", "symbols": {"XYZ": {"expiry": exp, "options": list(options)}}}


def test_roll_candidates_are_quoted_capped_by_delta_and_debit_and_checked():
    """The 45 short (bought back at 0.80, spot 43) fails the width at 7.62 a share. On December the 44
    and 45 are over 0.40 delta; the 46 and 47 are under it but leave too little width (an assignment
    there still loses, even with their credit); the 48 pays for the buy-back, keeps the width (8 over
    the long's 40 against 7.62), and is the most premium left, so it is the candidate; the 50 pays
    less."""
    quotes = [(44, 2.4), (45, 1.95), (46, 1.5), (47, 1.1), (48, 0.75), (50, 0.35)]
    snap = snapshot(
        history(short=NARROW, short_price=6.00),
        legs(short=NARROW, short_price=6.00, short_mid=0.80, long_mid=8.0),
        spot=43.0,
        income=income(*(quote(k, b, b + 0.1) for k, b in quotes)),
    )
    a = evaluate(snap)
    v = view_of(a)
    (c,) = v["rolls"]["candidates"]
    assert c["expiration"] == DEC and c["open"]["strike"] == 48.0 and c["net"] == pytest.approx(0.0)
    assert 0.20 < c["delta"] <= 0.40
    assert c["check"]["verdict"] == "ok" and c["legs"][1] == "STO 1 2026-12-18 48C @ 0.80 (quoted 0.75/0.85)"
    assert (
        "2026-12-18 48C at 0.25 delta, +0.00 a share, check-roll ok"
        in fired(trade_of(a, NARROW))["pmcc.width"]["reason"]
    )
    # A debit of up to a dime a share is allowed (check-roll still rejects any debit, and says so);
    # fifteen cents is not.
    snap["income"] = income(quote(48, 0.67, 0.77))
    (c,) = view_of(evaluate(snap))["rolls"]["candidates"]
    assert c["net"] == pytest.approx(-0.08) and c["check"]["verdict"] == "reject"
    snap["income"] = income(quote(48, 0.60, 0.70))
    rolls = view_of(evaluate(snap))["rolls"]
    assert rolls["candidates"] == [] and "within 0.10" in rolls["why"]


def test_a_candidate_is_never_over_the_delta_cap_however_much_it_pays():
    """A long that has cost little leaves every strike the width: the at-the-money 50 and the 52 pay
    most but sit over 0.40 delta, so the 54 is the candidate."""
    view = {
        "spot": 50.0,
        "div_yield": 0.0,
        "long": {"strike": 40.0, "expiration": LONG_EXP},
        "short": {"expiration": "2026-11-20", "mid": 0.50},
        "width": {"net_cost_per_share": 2.0},
    }
    book = [(50, 2.2, 2.3), (52, 1.4, 1.5), (54, 0.8, 0.9), (56, 0.4, 0.5)]
    quotes = {DEC: [{"strike": k, "bid": b, "ask": a, "mid": (b + a) / 2} for k, b, a in book]}
    cfg = _rules.DEFAULT_RULES["pmcc"]["roll"]
    (c,) = pmcc.roll_candidates(view, quotes, date.fromisoformat(SESSION), RATE, cfg)["candidates"]
    assert c["open"]["strike"] == 54.0 and c["delta"] <= 0.40


def test_a_roll_is_never_past_the_long_and_none_priced_is_said():
    late = income(quote(56, 1.6, 1.7, exp="2028-06-16"), exp="2028-06-16")
    v = view_of(evaluate(narrow() | {"income": late}))
    assert v["rolls"]["candidates"] == [] and "before the long's" in v["rolls"]["why"]
    too_dear = income(quote(60, 0.10, 0.20))
    v = view_of(evaluate(narrow() | {"income": too_dear}))
    assert v["rolls"]["candidates"] == [] and "within 0.10" in v["rolls"]["why"]


# --------------------------------------------------------------------------- precedence
def test_a_stop_outranks_the_pmcc_roll():
    # The long has lost most of its value: the debit stop closes, and the width's roll is its conflict.
    snap = snapshot(
        history(short=NARROW, short_price=6.00),
        legs(short=NARROW, short_price=6.00, short_mid=1.5, long_mid=3.5),
        spot=40.0,
    )
    (t,) = evaluate(snap, {"trade_grouping": "underlying"})["trades"]
    assert {"debit.stop", "pmcc.width"} <= set(fired(t))
    rec = t["recommendation"]
    assert rec["rule"] == "debit.stop" and rec["action"] == ev.CLOSE
    assert rec["conflicts"] == ["pmcc.width"] and rec["confidence"] == 65 - 10


def test_the_diagonal_credit_roll_is_offered_in_place_of_the_pmcc_roll():
    """One chain holding the long and an in-the-money short 17 days out (rolled into in May), a dividend
    going ex first: the PMCC rules say roll or close, and roll.diagonal_short's credit roll, which also
    clears the long's breakeven, is offered in their place, as it is for any close."""
    short = occ("2026-10-30", "C", 50)
    rows = [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", LONG, 1, DEEP),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", OLD_SHORT, 1, 1.50),
        tx(2, "2026-05-14T15:00:00Z", "Buy to Close", OLD_SHORT, 1, 0.05),
        tx(2, "2026-05-14T15:00:00Z", "Sell to Open", short, 1, 3.00),
    ]
    cur = [opt_leg(LONG, 1, 15.5, DEEP), opt_leg(short, -1, 2.40, 3.00)]
    div = {"ex_dividend_date": "2026-10-20", "dividend_rate_per_share": "0.50"}
    (t,) = evaluate(snapshot(rows, cur, spot=52.0, metrics=div))["trades"]
    assert {"pmcc.assignment", "pmcc.width", "roll.diagonal_short"} <= set(fired(t))
    rec = t["recommendation"]
    assert rec["rule"] == "roll.diagonal_short" and rec["action"] == ev.ROLL_FOR_CREDIT
    assert {"pmcc.assignment", "pmcc.width"} <= set(rec["replaces"]) and rec["conflicts"] == []


# --------------------------------------------------------------------------- failing closed
def test_an_unreconciled_pmcc_gets_a_review_and_no_pmcc_rule():
    # The history starts after the long was bought: only the short's order is in it.
    rows = [tx(2, "2026-09-25T15:00:00Z", "Sell to Open", NARROW, 1, 6.00)]
    snap = snapshot(rows, legs(short=NARROW, short_price=6.00, short_mid=5.60))
    a = evaluate(snap, {"positions": {"XYZ": {"intent": "pmcc"}}})
    v = view_of(a)
    assert v["recognized"] and not v["reconciled"]
    assert "the long's opening predates the history" in v["problems"]
    assert v["width"]["basis"] == "open_legs" and v["width"]["pnl_if_assigned_net"] is None
    names = fired(trade_of(a, NARROW))
    assert names["pmcc_data"]["action"] == ev.REVIEW and names["pmcc_data"]["confidence"] is None
    assert not any(r.startswith("pmcc.") for r in names)


def test_an_assigned_short_breaks_the_link():
    rows = history()
    rows.insert(2, rd("2026-05-15T22:00:00Z", "Assignment", "Buy to Close", OLD_SHORT, 1))
    rows.pop(3)  # the assignment replaces the expiry
    v = view_of(evaluate(snapshot(rows, legs())))
    assert not v["reconciled"] and any("assigned or exercised" in p for p in v["problems"])


def test_each_pmcc_rule_can_be_turned_off():
    for overrides in (
        {"positions": {"XYZ": {"disable": ["pmcc.width"]}}},
        {"pmcc": {"width": {"enabled": False}}},
    ):
        assert "pmcc.width" not in fired(trade_of(evaluate(narrow(), overrides), NARROW))
    off = {"pmcc": {"assignment": {"enabled": False}}}
    assert "pmcc.assignment" not in fired(trade_of(evaluate(itm(2.03), off), ITM))
    off = {"pmcc": {"roll_long": {"enabled": False}}}
    a = evaluate(snapshot(history(), legs(long_mid=6.0, short_mid=0.05), spot=41.0), off)
    assert "pmcc.roll_long" not in fired(trade_of(a, SHORT))


def test_rule_ids_are_known_to_overrides():
    for rule in ("pmcc.width", "pmcc.assignment", "pmcc.roll_long"):
        assert rule in _rules.RULE_IDS
    _, warnings = _rules.effective({"positions": {"XYZ": {"disable": ["pmcc.width"], "intent": "pmcc"}}})
    assert warnings == []


# --------------------------------------------------------------------------- the band and containment
def test_the_short_against_today_s_call_band():
    v = view_of(evaluate(snapshot(history(), legs())))
    assert v["short"]["band"] is None and v["short"]["band_missing"] == "band not in the snapshot"
    bands = {
        "day": "2026-10-12",
        "names": {
            "XYZ": {
                "spot": 49.0,
                "rows": [
                    {
                        "side": "C",
                        "expiry": "2026-11-20",
                        "bands": {"fixed": {"band": 54.5}, "by_expiry": {"band": 53.0}},
                    }
                ],
            }
        },
    }
    v = view_of(evaluate(snapshot(history(), legs(), bands=bands)))
    # Moved a dollar to today's 50: 55.5 fixed, 54 by expiry; the 55 short is inside one, past the other.
    assert v["short"]["beyond_band"] == {"fixed": False, "by_expiry": True}


def test_a_pmcc_view_that_fails_costs_the_view_never_the_evaluation(monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("a history row layout never seen")

    monkeypatch.setattr(ev._pmcc, "build", boom)
    a = evaluate(narrow())
    assert a["trades"] and a["pmcc"]["positions"] == [] and "PMCC view failed" in a["pmcc"]["error"]
    assert not any(r.startswith("pmcc") for t in a["trades"] for r in fired(t))


def test_pmcc_rules_that_fail_cost_the_rules_never_the_trade(monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("unexpected")

    monkeypatch.setattr(ev, "_pmcc_rules", boom)
    a = evaluate(narrow())
    t = trade_of(a, NARROW)
    assert t["recommendation"]["action"] and "PMCC rules failed" in t["pmcc"]["error"]


def test_no_history_means_no_pmcc_view():
    snap = narrow()
    snap["transactions_error"] = "HTTP 503"
    a = evaluate(snap)
    assert a["pmcc"]["positions"] == [] and a["pmcc"]["error"]
