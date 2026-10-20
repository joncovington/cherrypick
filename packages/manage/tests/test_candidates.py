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


def _suggest(entries, book=None, trades=(), kind=None, rules=RULES):
    income = {"session": "2026-10-16", "taken_at": "t", "complete": True, "symbols": entries}
    return candidates.suggest(
        income,
        book or _book(),
        list(trades),
        SESSION,
        rules,
        kind or {"margin_or_cash": "Cash", "type_name": "Roth IRA"},
        500.0,
    )


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
    assert abs(sp["delta"]) == pytest.approx(0.40, abs=0.04)
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
    assert abs(cc["legs"][0][0]["delta"]) == pytest.approx(0.165, abs=0.04)


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


def test_an_illiquid_leg_excludes_the_structure():
    out = _suggest({"AAA": _entry(passes=False)})
    assert out["excluded"]["a leg is illiquid or unquoted"] == 3 and not _rows(out)


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
