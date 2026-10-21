"""Income trades from our own universe, sized to the account: structures, filters, caps."""

import copy
from datetime import date

import pytest

from cherrypick.manage import candidates, pricing
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
SESSION = date(2026, 10, 19)
CFG = RULES["portfolio"]["candidates"]


def _entry(spot=50.0, iv=0.30, dte=45, strikes=range(35, 66), spread=0.04, passes=True, **over):
    """One name's income reading, priced by the model so every IV solves back to `iv`."""
    years, options = dte / 365.0, []
    for k in strikes:
        for right in ("C", "P"):
            if (right == "P" and k > spot) or (right == "C" and k < spot):
                continue
            mid = pricing.price(spot, float(k), years, iv, right, 0.04)
            if mid < 0.03:
                continue
            options.append(
                {
                    "strike": float(k),
                    "right": right,
                    "option": f"X{right}{k}",
                    "bid": round(mid - spread / 2, 4),
                    "ask": round(mid + spread / 2, 4),
                    "volume": 50,
                    "open_interest": 500,
                    "passes": passes,
                }
            )
    entry = {
        "spot": spot,
        "expiry": "2026-12-03",
        "dte": dte,
        "beta": 1.0,
        "earnings_date": None,
        "stress": {"sessions": 31, "p01": -0.20, "p99": 0.20, "windows": 700},
        "options": options,
    }
    entry.update(over)
    return entry


def _book(net_liq=100_000.0, used=0.0, theta=0.0, bwd=0.0, bp_cap=0.50, underlyings=()):
    return {
        "net_liq": net_liq,
        "buying_power_used": used,
        "theta": theta,
        "bwd": bwd,
        "underlyings": list(underlyings),
        "bands": {
            "delta": {"low": -1000.0, "high": 1000.0},
            "theta": {"high": 0.003},
            "buying_power": {"high": bp_cap},
        },
    }


def _suggest(entries, book=None, trades=(), kind=None, rules=RULES, orders=None):
    income = {"session": "2026-10-16", "taken_at": "t", "complete": True, "symbols": entries}
    return candidates.suggest(
        income,
        book or _book(),
        list(trades),
        SESSION,
        rules,
        kind or {"margin_or_cash": "Cash", "type_name": "Roth IRA"},
        500.0,
        orders,
    )


def _order(status, action, symbol="SLV   261120C00055000", qty=1, left=None):
    return {
        "status": status,
        "underlying_symbol": "SLV",
        "legs": [{"symbol": symbol, "action": action, "quantity": qty, "remaining_quantity": left}],
    }


def test_a_call_still_being_sold_counts_against_the_shares_and_a_finished_order_does_not():
    held = _held(200)
    working = [_order("Live", "Sell to Open")]
    (row,) = _rows(_suggest({"SLV": _entry()}, trades=held, orders=working), "covered_call")
    assert row["units"] == 1  # 200 shares, one call already on its way
    # A filled order is in the positions already; a cancelled one never will be.
    for done in ("Filled", "Cancelled", "Rejected", "Expired"):
        (row,) = _rows(
            _suggest({"SLV": _entry()}, trades=held, orders=[_order(done, "Sell to Open")]), "covered_call"
        )
        assert row["units"] == 2
    # Partly filled: only what is left counts (the filled part is a position).
    assert candidates.pending_short_calls([_order("Live", "Sell to Open", qty=3, left=1)]) == {"SLV": 1.0}
    # Not a call sold to open: a put, a buy, a close.
    for o in (
        _order("Live", "Sell to Open", symbol="SLV   261120P00050000"),
        _order("Live", "Buy to Open"),
        _order("Live", "Buy to Close"),
    ):
        assert candidates.pending_short_calls([o]) == {}
    out = _suggest({"SLV": _entry()}, trades=_held(100), orders=working)
    assert _rows(out, "covered_call") == [] and out["excluded"].get("shares already written against") == 1


def _rows(out, kind=None):
    rows = out["fits"] + out["too_big"]
    return [r for r in rows if kind is None or r["kind"] == kind]


# --------------------------------------------------------------------------- the structures
def test_the_structures_follow_the_screener_rules():
    e = _entry()
    opts = candidates.priced_options(e, SESSION, 0.04)
    built = {s["kind"]: s for s in candidates.structures(e, opts, CFG, 0.0)}
    assert set(built) == {"short_put", "put_spread", "call_spread"}  # no covered call without shares
    ((sp, _),) = built["short_put"]["legs"]
    assert abs(sp["delta"]) == pytest.approx(0.16, abs=0.04)
    short, long_ = (o for o, _ in built["put_spread"]["legs"])
    assert short["strike"] == 50.0 and long_["strike"] < 50.0  # short the first strike out of the money
    assert abs(long_["delta"]) == pytest.approx(0.25, abs=0.04)
    short, long_ = (o for o, _ in built["call_spread"]["legs"])
    assert short["strike"] == 50.0 and long_["strike"] > 50.0


def test_a_covered_call_only_on_held_shares_and_never_more_than_they_cover():
    e = _entry()
    opts = candidates.priced_options(e, SESSION, 0.04)
    assert "covered_call" not in {s["kind"] for s in candidates.structures(e, opts, CFG, 99.0)}
    cc = next(s for s in candidates.structures(e, opts, CFG, 250.0) if s["kind"] == "covered_call")
    assert cc["max_units"] == 2
    assert abs(cc["legs"][0][0]["delta"]) == pytest.approx(0.16, abs=0.04)


def test_the_covered_call_is_written_on_its_own_expiry_when_the_reading_has_one():
    """From 2026-10-19 the reading carries the 30-45 day monthly's calls (`cc`): the covered call is
    built there, the put and the spreads on the cycle monthly, and each is checked for earnings
    against its own expiry."""
    cycle = _entry(dte=52)
    near = _entry(dte=33)
    cc = {"expiry": "2026-11-20", "dte": 33, "window": "30-45", "stress": cycle["stress"]}
    cc["options"] = [o for o in near["options"] if o["right"] == "C"]
    e = {**cycle, "cc": cc}
    out = _suggest({"SLV": e}, trades=_held(300))
    (call,) = _rows(out, "covered_call")
    assert (call["expiry"], call["dte"]) == ("2026-11-20", 33)
    (put,) = _rows(out, "short_put")
    assert (put["expiry"], put["dte"]) == ("2026-12-03", 52)
    # A report after the call's expiry and before the cycle's: the call stands, the put does not.
    late = {**e, "earnings_date": "2026-11-27"}
    out = _suggest({"SLV": late}, trades=_held(300))
    assert _rows(out, "covered_call") and not _rows(out, "short_put")
    # A reading from before the block: the call on the cycle monthly, as before.
    (old,) = _rows(_suggest({"SLV": cycle}, trades=_held(300)), "covered_call")
    assert old["dte"] == 52


def test_a_short_put_or_call_far_from_its_delta_is_not_built():
    """The Screeners lists' tolerance: a nearest strike that misses 16 delta by more than 0.05 is no
    trade (a $3 stock's nearest call at 0.43 delta, 2026-10-19). The spreads have no delta target."""
    e = _entry(strikes=[44, 49, 51, 53])  # nearest picks near 0.09 and 0.32 delta, both outside
    opts = candidates.priced_options(e, SESSION, 0.04)
    kinds = {s["kind"] for s in candidates.structures(e, opts, CFG, 300.0)}
    assert "short_put" not in kinds and "covered_call" not in kinds
    loose = {**CFG, "delta_tolerance": 1.0}
    kinds = {s["kind"] for s in candidates.structures(e, opts, loose, 300.0)}
    assert {"short_put", "covered_call"} <= kinds


def _held(shares, *calls):
    """A name's trades: the shares, and each (signed quantity, strike) call leg open against them."""
    legs = [{"quantity": float(shares), "right": None, "strike": None}]
    legs += [{"quantity": float(q), "right": "C", "strike": float(k)} for q, k in calls]
    return [{"underlying": "SLV", "legs": legs}]


def test_shares_already_written_against_are_not_offered_another_covered_call():
    # 100 shares with a call already sold against them: another would be naked, which an IRA cannot
    # carry. The name is counted as excluded, not offered at quantity 0 as if it were too big.
    out = _suggest({"SLV": _entry()}, trades=_held(100, (-1, 55)))
    assert _rows(out, "covered_call") == []
    assert out["excluded"].get("shares already written against") == 1
    # 300 shares, one written: two left.
    (row,) = _rows(_suggest({"SLV": _entry()}, trades=_held(300, (-1, 55))), "covered_call")
    assert row["units"] == 2
    (row,) = _rows(_suggest({"SLV": _entry()}, trades=_held(300)), "covered_call")
    assert row["units"] == 3


def test_every_short_call_counts_against_the_shares_even_inside_a_spread():
    # A call spread's short (and a second expiry's short) still ties up shares: a long call is not
    # taken as cover, which the broker may not accept in an IRA.
    e = _entry()
    opts = candidates.priced_options(e, SESSION, 0.04)
    built = {s["kind"]: s for s in candidates.structures(e, opts, CFG, 300.0, 2.0)}
    assert built["covered_call"]["max_units"] == 1
    out = _suggest({"SLV": _entry()}, trades=_held(300, (-2, 55), (1, 60), (-1, 58)))
    assert _rows(out, "covered_call") == []
    # A long call alone takes nothing.
    (row,) = _rows(_suggest({"SLV": _entry()}, trades=_held(200, (1, 60))), "covered_call")
    assert row["units"] == 2


# --------------------------------------------------------------------------- the filters
def test_earnings_inside_the_trade_excludes_it_and_after_expiry_does_not():
    inside = _suggest({"AAA": _entry(earnings_date="2026-11-05")})
    assert inside["excluded"] == {"earnings inside the trade": 1} and not _rows(inside)
    after = _suggest({"AAA": _entry(earnings_date="2027-01-28")})
    assert _rows(after)
    unknown = _suggest({"AAA": _entry()})
    assert all(r["earnings_unknown"] for r in _rows(unknown))  # flagged, never called clear


def test_an_illiquid_option_excludes_a_short_put_but_a_spread_is_judged_as_one_order():
    # Every leg failing on its own: the short put goes, the spreads stay -- a 4-cent-wide leg on the
    # model's prices costs a few cents to trade as a spread, well under a tenth of its credit.
    out = _suggest({"AAA": _entry(passes=False)})
    assert out["excluded"]["a leg is illiquid or unquoted"] == 1 and not _rows(out, "short_put")
    assert _rows(out, "put_spread") and _rows(out, "call_spread")


def test_a_spread_that_costs_too_much_to_trade_is_excluded():
    # 60 cents wide a leg: mid less natural is 60 cents on a credit of about a dollar.
    out = _suggest({"AAA": _entry(spread=0.60)})
    assert not _rows(out, "put_spread") and not _rows(out, "call_spread")
    assert out["excluded"]["a leg is illiquid or unquoted"] >= 2


def test_a_spread_without_open_interest_on_a_leg_is_excluded():
    e = _entry()
    for o in e["options"]:
        o["open_interest"] = 20
    out = _suggest({"AAA": e})
    assert not _rows(out, "put_spread") and not _rows(out, "call_spread")


def test_the_credit_floor_is_tested_at_the_working_price_not_the_mid():
    row = next(r for r in _rows(_suggest({"AAA": _entry()}), "put_spread") if not r["alternative_to"])
    pu = row["per_unit"]
    assert pu["natural"] < pu["working"] < pu["credit"]
    width = abs(row["legs"][0]["strike"] - row["legs"][1]["strike"])
    # A floor the spread clears at its mid but not at its working price.
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["candidates"]["spread_min_credit"] = (pu["credit"] - 0.005) / width
    out = _suggest({"AAA": _entry()}, rules=rules)
    assert not [r for r in _rows(out, "put_spread") if not r["alternative_to"]]
    assert out["excluded"]["credit under a third of the width"] >= 1


def test_a_spread_under_a_third_of_its_width_is_not_offered():
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["candidates"]["spread_min_credit"] = 0.99
    out = _suggest({"AAA": _entry()}, rules=rules)
    assert out["excluded"]["credit under a third of the width"] == 2
    assert not _rows(out, "put_spread") and not _rows(out, "call_spread")


# --------------------------------------------------------------------------- per unit
def test_a_short_put_ties_up_its_strike_in_cash_and_reg_t_on_margin():
    e = _entry()
    cash = _rows(_suggest({"AAA": e}), "short_put")[0]
    k, credit = cash["legs"][0]["strike"], cash["per_unit"]["credit"]
    # The credit is shown to the cent; the figures use the exact mid: half a cent a share.
    assert cash["per_unit"]["buying_power"] == pytest.approx((k - credit) * 100, abs=0.5)
    margin = _rows(
        _suggest({"AAA": e}, kind={"margin_or_cash": "Margin", "type_name": "Individual"}), "short_put"
    )[0]
    otm = max(50.0 - k, 0.0)
    reg_t = max(0.20 * 50.0 - otm + credit, 0.10 * k + credit) * 100
    assert margin["per_unit"]["buying_power"] == pytest.approx(reg_t, abs=0.5)
    # An unread account kind is sized as cash-secured, never as margin.
    unknown = _rows(_suggest({"AAA": e}, kind={"margin_or_cash": None, "type_name": None}), "short_put")[0]
    assert unknown["per_unit"]["buying_power"] == cash["per_unit"]["buying_power"]


def test_a_short_puts_stress_loss_is_the_names_bad_move_at_expiry_less_the_credit():
    row = _rows(_suggest({"AAA": _entry()}), "short_put")[0]
    k, credit = row["legs"][0]["strike"], row["per_unit"]["credit"]
    assert row["per_unit"]["stress_loss"] == pytest.approx(
        max(k - 50.0 * 0.80, 0) * 100 - credit * 100, abs=0.5
    )


def test_no_stress_reading_is_no_size():
    out = _suggest({"AAA": _entry(stress=None)})
    sp = _rows(out, "short_put")[0]
    assert sp["units"] == 0 and sp["binding"] == "no stress reading"


# --------------------------------------------------------------------------- the caps
def test_per_trade_risk_caps_the_count_and_names_itself():
    # 3% of $60,000 is $1,800: a short put's stress loss of ~$750 fits twice.
    sp = _rows(_suggest({"AAA": _entry()}, _book(net_liq=60_000.0)), "short_put")[0]
    assert sp["units"] >= 1 and sp["binding"] == "per-trade risk"
    assert (
        sp["units"] * sp["per_unit"]["stress_loss"]
        <= 1_800.0
        < (sp["units"] + 1) * sp["per_unit"]["stress_loss"]
    )


def test_buying_power_is_held_under_the_vix_cap_and_past_it_nothing_fits():
    # 35% used: past the VIX cap of 30%, though under the 50% fallback -- so it is the VIX cap that holds.
    over = _suggest({"AAA": _entry()}, _book(used=35_000.0, bp_cap=0.30))
    assert not over["fits"] and {r["binding"] for r in over["too_big"]} == {"buying power"}


def test_a_candidate_stopped_by_buying_power_waits_sized_as_if_there_were_room():
    over = _suggest({"AAA": _entry()}, _book(used=35_000.0, bp_cap=0.30))
    # Every one stopped by buying power waits, each trade once (not again as its narrowed version).
    assert over["waiting_total"] == len(over["too_big"]) > 0
    assert len({(r["kind"], tuple(leg["strike"] for leg in r["legs"])) for r in over["waiting"]}) == len(
        over["waiting"]
    )
    assert over["bp_used_pct"] == 0.35 and over["bp_cap_pct"] == 0.30 and over["bp_warn_over"] == 0.10
    for r in over["waiting"]:
        room = r["if_room"]
        assert r["units"] == 0 and room["units"] >= 1
        # Its room size stops at a cap on the trade, never at one on how full the book is.
        assert room["binding"] not in candidates.ROOM_CAPS
        bp = r["per_unit"]["buying_power"]
        assert room["bp_after_pct"] == round((35_000.0 + room["units"] * bp) / 100_000.0, 4)


def test_a_waiting_candidate_is_graded_by_the_buying_power_it_would_leave():
    def graded(used, rules=RULES, warn=0.10):
        out = _suggest({"AAA": _entry()}, _book(used=used, bp_cap=0.30), rules=rules)
        assert out["waiting"]
        for r in out["waiting"]:
            after = r["if_room"]["bp_after_pct"]
            expect = "ok" if after <= 0.30 else "over" if after <= 0.30 + warn else "far_over"
            assert r["if_room"]["bp_level"] == expect, (after, r["if_room"])
        return {r["if_room"]["bp_level"] for r in out["waiting"]}

    # Just over the 30% cap, a small trade lands within ten points of it; far past it, nothing does.
    assert "over" in graded(30_500.0)
    assert graded(45_000.0) == {"far_over"}
    # A wider warning band moves the line.
    wide = copy.deepcopy(RULES)
    wide["portfolio"]["candidates"]["bp_warn_over"] = 0.50
    assert "far_over" not in graded(45_000.0, wide, 0.50)


def test_a_book_over_its_theta_ceiling_waits_too_and_says_so():
    full = _suggest({"AAA": _entry()}, _book(theta=300.0))
    assert full["waiting"] and all(r["if_room"]["theta_over"] for r in full["waiting"])
    assert {r["if_room"]["bp_level"] for r in full["waiting"]} == {"ok"}


def test_a_candidate_stopped_by_its_own_size_does_not_wait():
    # Per-trade risk and notional are about the trade, not the book: room would not change them.
    small = _suggest({"AAA": _entry()}, _book(net_liq=8_000.0))
    stopped = {r["binding"] for r in small["too_big"]}
    assert stopped and not stopped & candidates.ROOM_CAPS
    assert small["waiting_total"] == 0


def test_notional_per_name_holds_a_cash_secured_put_to_a_quarter_of_net_liq():
    # $8,000 account: a 40-ish strike secures ~$4,000, over the 25% ($2,000) notional cap.
    sp = _rows(_suggest({"AAA": _entry()}, _book(net_liq=8_000.0)), "short_put")[0]
    assert sp["units"] == 0 and sp["binding"] in ("notional per name", "per-trade risk")


def test_the_theta_ceiling_and_name_concentration_cap_the_count():
    full = _suggest({"AAA": _entry()}, _book(theta=300.0))  # already at the 0.30% ceiling
    assert {r["binding"] for r in full["too_big"]} == {"theta ceiling"}
    held = [{"underlying": "AAA", "theta": 75.0}]  # a quarter of the $300 ceiling, used already
    conc = _suggest({"AAA": _entry()}, _book(underlyings=held))
    assert "concentration" in {r["binding"] for r in conc["too_big"]}


def test_a_candidate_on_an_expiry_already_over_its_share_waits():
    # The entry expires 2026-12-03; the book's trades there can already lose 25% of $100,000.
    book = _book()
    book["ladder"] = {"expiries": [{"expiration": "2026-12-03", "trades": ["X"], "downside": -25_000.0}]}
    out = _suggest({"AAA": _entry()}, book)
    assert not out["fits"]
    assert {r["binding"] for r in out["too_big"]} == {"expiry concentration"}
    assert out["waiting"] and all(r["if_room"]["units"] >= 1 for r in out["waiting"])
    book["ladder"]["expiries"][0]["downside"] = -5_000.0  # 5%: room again
    assert _suggest({"AAA": _entry()}, book)["fits"]


def test_theta_about_to_expire_does_not_fill_the_ceiling():
    # $300 of the book's theta is a leg expiring tomorrow: what the band reads is the $10 left, so a
    # candidate is not stopped by the ceiling, and a name's expiring theta is no concentration.
    book = _book(theta=300.0, underlyings=[{"underlying": "AAA", "theta": 290.0, "theta_ex_front": 0.0}])
    book["theta_ex_front"] = 10.0
    out = _suggest({"AAA": _entry()}, book)
    assert not {"theta ceiling", "concentration"} & {r["binding"] for r in out["too_big"]}
    assert not any((r.get("if_room") or {}).get("theta_over") for r in out["waiting"])


def test_an_empty_book_is_not_blocked_by_concentration():
    out = _suggest({"AAA": _entry()}, _book(net_liq=50_000.0))
    assert out["fits"] and "concentration" not in {r["binding"] for r in out["too_big"]}


def test_delta_band_blocks_a_trade_that_pushes_the_book_further_out():
    book = _book()
    book["bands"]["delta"] = {"low": -5.0, "high": 5.0}
    book["bwd"] = 4.0  # a short put adds long delta: 1 unit carries it out
    sp = _rows(_suggest({"AAA": _entry()}, book), "short_put")[0]
    assert sp["units"] == 0 and sp["binding"] == "delta band"
    book["bwd"] = -40.0  # far short: the same put moves it back toward the band, so it is allowed
    assert _rows(_suggest({"AAA": _entry()}, book), "short_put")[0]["units"] >= 1


# --------------------------------------------------------------------------- what does not fit
def test_a_spread_too_wide_for_the_account_is_offered_narrowed_on_the_same_short():
    # $4,000: per-trade cap $120. The rule's spread (long at 0.25 delta, a few points wide) is over it.
    out = _suggest({"AAA": _entry(strikes=[x / 2 for x in range(70, 131)])}, _book(net_liq=4_000.0))
    wide = next(r for r in out["too_big"] if r["kind"] == "put_spread" and r["alternative_to"] is None)
    assert wide["binding"] == "per-trade risk" and wide["per_unit"]["max_loss"] > 120.0
    alt = wide["alternative"]
    assert alt["alternative_to"] == "put_spread" and alt["units"] >= 1
    assert alt["legs"][0]["strike"] == wide["legs"][0]["strike"]  # the same short
    assert alt["per_unit"]["max_loss"] <= 120.0 and alt["per_unit"]["credit"] >= 0.331 * (
        alt["legs"][0]["strike"] - alt["legs"][1]["strike"]
    )
    assert alt in out["fits"]


def test_the_list_ranks_by_theta_per_dollar_of_buying_power():
    out = _suggest({"AAA": _entry(), "BBB": _entry(iv=0.60)}, _book(net_liq=200_000.0))
    ranks = [r["theta_per_bp"] for r in out["fits"]]
    assert ranks == sorted(ranks, reverse=True)


def test_the_switch_and_a_missing_reading():
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["candidates"]["enabled"] = False
    assert _suggest({"AAA": _entry()}, rules=rules) is None
    missing = candidates.suggest({"error": "no income reading yet"}, _book(), [], SESSION, RULES, None, 500.0)
    assert missing["error"] == "no income reading yet" and missing["fits"] == []


# --------------------------------------------------------------------------- the Screeners fit
def _fit(entries, available, kind=None, trades=()):
    income = {"session": "2026-10-16", "taken_at": "t", "complete": True, "symbols": entries}
    kind = kind or {"margin_or_cash": "Cash", "type_name": "Roth IRA"}
    out = candidates.suggest(
        income, _book(), list(trades), SESSION, RULES, kind, 500.0, None, available_bp=available
    )
    return out["screen_fit"]


def test_the_screen_fit_judges_each_near_16_delta_put_against_the_buying_power_available():
    fit = _fit({"AAA": _entry()}, available=4_000.0)["AAA"]["short_put"]
    assert fit, "a strike within the lists' delta tolerance"
    for strike, f in fit.items():
        # A cash account secures the strike less the premium; a $40-something strike is ~$4,000.
        assert f["bp"] == pytest.approx(float(strike) * 100, rel=0.05)
        assert f["fits"] is (f["bp"] <= 4_000.0)
    assert {f["fits"] for f in _fit({"AAA": _entry()}, available=100_000.0)["AAA"]["short_put"].values()} == {
        True
    }
    assert {f["fits"] for f in _fit({"AAA": _entry()}, available=100.0)["AAA"]["short_put"].values()} == {
        False
    }


def test_a_margin_account_needs_far_less_than_the_strike_and_no_reading_is_no_verdict():
    margin = _fit({"AAA": _entry()}, 1_500.0, kind={"margin_or_cash": "Margin", "type_name": "Individual"})
    cash = _fit({"AAA": _entry()}, 1_500.0)
    for strike in margin["AAA"]["short_put"]:
        assert margin["AAA"]["short_put"][strike]["bp"] < cash["AAA"]["short_put"][strike]["bp"] / 2
    unknown = _fit({"AAA": _entry()}, None)["AAA"]["short_put"]
    assert {f["fits"] for f in unknown.values()} == {None}


def test_a_candidate_on_a_name_already_held_on_its_expiry_is_badged_and_still_sized():
    # AAA holds a call spread on the cycle monthly the candidates use (2026-12-03); BBB holds one a
    # month earlier. Only AAA's candidates carry the badge, and the badge changes no size.
    def spread(und, exp):
        legs = [
            {"quantity": -1.0, "right": "C", "strike": 60.0, "expiration": exp},
            {"quantity": 1.0, "right": "C", "strike": 65.0, "expiration": exp},
        ]
        return {"underlying": und, "legs": legs}

    held = [spread("AAA", "2026-12-03"), spread("BBB", "2026-11-05")]
    out = _suggest({"AAA": _entry(), "BBB": _entry()}, trades=held)
    rows = out["fits"] + out["too_big"]
    assert {r["underlying"] for r in rows if r["held_same_expiry"]} == {"AAA"}
    assert all(r["held_same_expiry"] is False for r in rows if r["underlying"] == "BBB")
    bare = _suggest({"AAA": _entry(), "BBB": _entry()})
    units = lambda o: sorted((r["underlying"], r["kind"], r["units"]) for r in o["fits"])  # noqa: E731
    assert units(bare) == units(out)


def test_the_screen_fit_is_judged_even_with_earnings_in_the_trade_and_counts_free_shares():
    # The lists show earnings themselves; the fit is only whether the account can carry it.
    earn = _entry(earnings_date="2026-11-05")
    fit = _fit({"SLV": earn}, 100_000.0, trades=_held(250, (-1, 60)))["SLV"]
    assert fit["short_put"] and fit["covered_call"] == 1  # 250 shares, 100 written against
    assert _fit({"SLV": _entry()}, 100_000.0)["SLV"]["covered_call"] == 0
