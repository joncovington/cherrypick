"""Evaluate every position in a broker snapshot against the management ruleset.

A pure function: snapshot + rules in, evaluation out. No broker, network, clock or file is touched
here, so an evaluation can be re-run over last month's snapshots with a changed ruleset and give the
same answer twice.

Each underlying is one trade. Its net open cost decides what kind of trade it is (a net credit is a
credit trade, a net debit a debit trade), and each rule that fires names an action. The trade's
recommendation is the most decisive action fired (CLOSE before TAKE_PROFIT before ROLL_OR_CLOSE
before CLOSE_LEGS before REVIEW), at that rule's configured confidence, less a penalty for every rule
that fired pointing the other way. A trade where nothing fired is a HOLD, and carries the levels at
which the rules would fire.

Money follows the suite layout: signed cash flow (a credit received is +), whole-position dollars, and
every P&L here is a mark P&L GROSS of fees -- the broker snapshot carries no fee record.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from functools import reduce
from typing import Any

from cherrypick.core import calendar as _calendar

from cherrypick.manage import candidates as _candidates
from cherrypick.manage import chains as _chains
from cherrypick.manage import explain as _explain
from cherrypick.manage import hedge as _hedge
from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules
from cherrypick.manage import shapes as _shapes

EVAL_VERSION = 1

CLOSE = "CLOSE"
TAKE_PROFIT = "TAKE_PROFIT"
ROLL_OR_CLOSE = "ROLL_OR_CLOSE"
ROLL_FOR_CREDIT = "ROLL_FOR_CREDIT"
CLOSE_LEGS = "CLOSE_LEGS"
REVIEW = "REVIEW"
SETTLES = "SETTLES"
HOLD = "HOLD"

# A credit roll only fires when a rule already says to close, and is offered in that close's place.
PRIORITY = {
    ROLL_FOR_CREDIT: 0,
    CLOSE: 1,
    TAKE_PROFIT: 2,
    ROLL_OR_CLOSE: 3,
    CLOSE_LEGS: 4,
    REVIEW: 5,
    SETTLES: 6,
    HOLD: 7,
}
# Which way an action points. Two fired rules pointing different ways are a conflict.
DIRECTION = {
    CLOSE: "reduce",
    TAKE_PROFIT: "reduce",
    CLOSE_LEGS: "reduce",
    ROLL_OR_CLOSE: "roll",
    ROLL_FOR_CREDIT: "roll",
}


# --------------------------------------------------------------------------- small helpers
def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _root(leg: dict[str, Any]) -> str:
    return (str(leg.get("symbol") or "").split() or [""])[0].upper()


def _label(leg: dict[str, Any]) -> str:
    if leg.get("right") not in ("C", "P"):
        return f"{leg['quantity']:+g} {leg.get('underlying')} shares"
    strike = leg["strike"]
    strike_txt = f"{strike:g}"
    return f"{leg['quantity']:+g} {leg['expiration']} {strike_txt}{leg['right']}"


def _exit_day(expiry: date, dte: int) -> str:
    """The time exit: `dte` days before expiry, or the trading day before that when it is a holiday."""
    day = expiry - timedelta(days=dte)
    return (day if _calendar.is_trading_day(day) else _calendar.previous_trading_day(day)).isoformat()


def _fire(rule: str, action: str, confidence: Any, reason: str, legs: list[str] | None = None) -> dict:
    return {"rule": rule, "action": action, "confidence": confidence, "reason": reason, "legs": legs}


# --------------------------------------------------------------------------- leg facts
def _legs(raw: list[dict[str, Any]], session: date, spot: float | None, rate: float) -> list[dict[str, Any]]:
    out = []
    for leg in raw:
        right = leg.get("right") if leg.get("right") in ("C", "P") else None
        exp = _date(leg.get("expiration")) if right else None
        dte = (exp - session).days if exp else None
        value, open_pl = _f(leg.get("value")), _f(leg.get("open_pl"))
        mid, opened = _f(leg.get("mid")), _f(leg.get("open_price"))
        strike = _f(leg.get("strike"))
        qty = _f(leg.get("quantity")) or 0.0
        item = {
            "symbol": leg.get("symbol"),
            "underlying": leg.get("underlying"),
            "root": _root(leg),
            "quantity": qty,
            "right": right,
            "strike": strike,
            "expiration": exp.isoformat() if exp else None,
            "dte": dte,
            "open_price": opened,
            "mid": mid,
            "value": value,
            "cost": (value - open_pl) if value is not None and open_pl is not None else None,
            "open_pl": open_pl,
            "priced": bool(leg.get("priced")) and value is not None,
            "years": max(dte, 0) / 365.0 if dte is not None else 0.0,
            "iv": None,
            "delta": None,
            "intrinsic": None,
            "extrinsic": None,
            "capture": None,
        }
        if right and opened:
            # A short leg's capture is the share of its premium given up; a long leg's is its return.
            item["capture"] = (opened - mid) / opened if qty < 0 and mid is not None else None
            if qty > 0 and mid is not None:
                item["capture"] = (mid - opened) / opened
        if right and spot and strike and mid is not None:
            item["intrinsic"] = pricing.intrinsic(spot, strike, right)
            item["extrinsic"] = mid - item["intrinsic"]
            if dte and dte > 0:
                item["iv"] = pricing.implied_vol(spot, strike, item["years"], mid, right, rate)
                if item["iv"]:
                    item["delta"] = pricing.delta(spot, strike, item["years"], item["iv"], right, rate)
        item["label"] = _label(item)
        out.append(item)
    return out


def _order(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Closing order for `legs` at mid: buy back the shorts, sell the longs."""
    rows = []
    for leg in legs:
        option = leg["right"] is not None
        side = (
            ("BTC" if leg["quantity"] < 0 else "STC")
            if option
            else ("BUY" if leg["quantity"] < 0 else "SELL")
        )
        rows.append({"side": side, "quantity": abs(leg["quantity"]), "leg": _label(leg), "mid": leg["mid"]})
    values = [leg["value"] for leg in legs]
    net = sum(values) if all(v is not None for v in values) else None
    net_price = None
    if net is not None and all(leg["right"] for leg in legs):
        unit = reduce(math.gcd, [int(abs(leg["quantity"])) or 1 for leg in legs])
        net_price = abs(net) / pricing.MULTIPLIER / unit
    return {
        "legs": rows,
        # Signed cash flow of the close: + is received (cr), - is paid (db).
        "net": net,
        "net_price": net_price,
        "net_side": None if net is None else ("cr" if net >= 0 else "db"),
    }


# --------------------------------------------------------------------------- one trade
def evaluate_trade(
    underlying: str,
    raw_legs: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    session: date,
    net_liq: float | None,
    rules: dict[str, Any],
    chain: dict[str, Any] | None = None,
    seen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`chain`, when the broker history reconciled one, sets the basis: the chain's net cash across its
    opening order and every roll, not the open legs' average prices. `seen` is the earnings trades the
    previous evaluation found (`earnings_trade`)."""
    metrics = metrics or {}
    spot = _f(metrics.get("last"))
    pr = rules["pricing"]
    rate = float(pr["rate"])
    legs = _legs(raw_legs, session, spot, rate)
    override = (rules.get("positions") or {}).get(underlying) or {}

    def off(rule_id: str) -> bool:
        return _rules.disabled(rules, underlying, rule_id)

    trade: dict[str, Any] = {
        "underlying": underlying,
        "spot": spot,
        "intent": override.get("intent"),
        "note": override.get("note"),
        "legs": legs,
        "kind": None,
        "cost": None,
        "value": None,
        "pnl_gross": None,
        "pnl_net": None,
        "fees": None,
        "basis": None,
        "chain": _chain_view(chain) if chain else None,
        "roll": None,
        "roll_blocked": None,
        "shape": None,
        "directional": None,
        "capture": None,
        "nearest_expiry": None,
        "nearest_short_expiry": None,
        "nearest_short_dte": None,
        "profile": None,
        "earnings": None,
        "ex_dividend": None,
        "fired": [],
        "exit_levels": {},
    }

    if not legs or not all(leg["priced"] for leg in legs):
        unpriced = [_label(leg) for leg in legs if not leg["priced"]]
        trade["fired"] = [
            _fire("data", REVIEW, None, f"{len(unpriced)} leg(s) unpriced; no rule evaluated", unpriced)
        ]
        return _decide(trade, rules, legs)

    value = sum(leg["value"] for leg in legs)
    if chain:
        # The chain's net cash is what this trade has actually cost (or paid) since it opened.
        cost = -chain["cash"]
        trade.update(basis="order_chain", fees=chain["fees"])
    else:
        cost = sum(leg["cost"] for leg in legs)
        trade["basis"] = "open_legs"
    pnl = value - cost
    kind = "credit" if cost < 0 else "debit"
    basis = abs(cost)
    trade.update(
        kind=kind,
        cost=cost,
        value=value,
        pnl_gross=pnl,
        pnl_net=(pnl + trade["fees"]) if trade["fees"] is not None else None,
        # Under a dollar of net cash (a roll that paid for the whole trade) no percentage means anything.
        capture=(pnl / basis) if basis >= 1 else None,
    )
    options = [leg for leg in legs if leg["right"]]
    shorts = [leg for leg in options if leg["quantity"] < 0]
    expiries = sorted({leg["expiration"] for leg in options})
    short_expiries = sorted({leg["expiration"] for leg in shorts})
    trade["nearest_expiry"] = expiries[0] if expiries else None
    trade["nearest_short_expiry"] = short_expiries[0] if short_expiries else None
    trade["nearest_short_dte"] = (_date(short_expiries[0]) - session).days if short_expiries else None

    # ---- modelled payoff at the nearest expiry
    profile = None
    if spot and expiries and _date(expiries[0]) >= session:
        horizon = max((_date(expiries[0]) - session).days, 0) / 365.0
        profile = pricing.payoff_profile(legs, spot, horizon, pr)
        profile.update(
            horizon=expiries[0],
            max_loss_from_open=profile["min_value"] - cost,
            max_gain_from_open=profile["max_value"] - cost,
            downside_from_now=profile["min_value"] - value,
            upside_from_now=profile["max_value"] - value,
        )
        trade["profile"] = profile

    trade["shape"] = _shapes.classify(options, cost)
    # A trade opened near even money (a super bear or bull, a risk reversal) earns from direction, not
    # premium: a percentage of a tiny credit or debit measures nothing, so it is judged on what it can
    # still win and lose instead.
    dr = rules["directional"]
    directional = bool(
        dr["enabled"]
        and trade["shape"] in _shapes.RISK_JUDGED
        and profile
        and not profile["unbounded_low"]
        and -profile["max_loss_from_open"] > 0
        and abs(cost) < float(dr["max_premium_share"]) * -profile["max_loss_from_open"]
    )
    if directional:
        trade["kind"] = "directional" if trade["shape"] in _shapes.DIRECTIONAL else "risk-judged"

    fired: list[dict] = []

    # ---- expiry: cash-settled legs settle; physically-settled ones are never held into it
    ex = rules["expiry"]
    if not off("expiry"):
        cash_roots = {r.upper() for r in ex["cash_settled_roots"]}
        settled = [
            leg for leg in options if leg["dte"] is not None and leg["dte"] <= 0 and leg["root"] in cash_roots
        ]
        stale = [
            leg
            for leg in options
            if leg["dte"] is not None and leg["dte"] < 0 and leg["root"] not in cash_roots
        ]
        expiring = [
            leg
            for leg in options
            if leg["dte"] is not None
            and 0 <= leg["dte"] <= int(ex["close_by_dte"])
            and leg["root"] not in cash_roots
        ]
        if settled:
            fired.append(
                _fire(
                    "expiry",
                    SETTLES,
                    ex["confidence"],
                    "cash-settled legs at expiry settle on the official print",
                    [_label(leg) for leg in settled],
                )
            )
            if len(settled) == len(options):
                # Nothing is left to manage: every other rule would be judging a settled position.
                trade["fired"] = fired
                return _decide(trade, rules, legs)
        if stale:
            fired.append(
                _fire(
                    "expiry",
                    REVIEW,
                    ex["confidence"],
                    "physically-settled legs past expiry are still listed: check for assignment or exercise",
                    [_label(leg) for leg in stale],
                )
            )
        if expiring:
            whole = len(expiring) == len(options)
            soonest = min(leg["dte"] for leg in expiring)
            fired.append(
                _fire(
                    "expiry",
                    CLOSE if whole else CLOSE_LEGS,
                    ex["confidence"],
                    f"physically-settled legs expire in {soonest} day(s); never hold into expiry "
                    "(pin and post-close exercise risk)",
                    [_label(leg) for leg in expiring],
                )
            )

    # ---- earnings and ex-dividend facts
    er_date = _date(metrics.get("earnings_date"))
    if rules["earnings"]["enabled"] and er_date and er_date >= session and expiries:
        last_exp = _date(expiries[-1])
        if er_date <= last_exp:
            trade["earnings"] = {
                "date": er_date.isoformat(),
                "time_of_day": metrics.get("earnings_time"),
                "days": (er_date - session).days,
                "before_nearest_short_expiry": bool(short_expiries) and er_date <= _date(short_expiries[0]),
            }
    xd = _date(metrics.get("ex_dividend_date"))
    if xd and xd > session:
        trade["ex_dividend"] = {"date": xd.isoformat(), "days": (xd - session).days}
    trade["earnings_trade"] = earnings_trade(trade, metrics, session, rules, seen)

    # ---- trades judged on their risk, not their premium
    if directional:
        max_loss = -profile["max_loss_from_open"]
        max_gain = profile["max_gain_from_open"]
        tp_share = float(dr["take_profit"]["share_of_max_gain"])
        stop_share = float(dr["stop"]["share_of_max_loss"])
        # A long option's max gain can be near-unlimited; the target never asks for more than
        # `max_reward_multiple` times the risk taken.
        target = min(tp_share * max_gain, float(dr["take_profit"]["max_reward_multiple"]) * max_loss)
        trade["exit_levels"]["take_profit"] = {"close_value": cost + target}
        trade["exit_levels"]["stop"] = {"close_value": cost - stop_share * max_loss, "basis": "max_loss"}
        trade["capture"] = pnl / max_loss  # P&L as a share of the risk taken
        trade["directional"] = {"max_gain": max_gain, "max_loss": max_loss, "target": target}
        if not off("directional.take_profit") and target > 0 and pnl >= target:
            fired.append(
                _fire(
                    "directional.take_profit",
                    TAKE_PROFIT,
                    dr["take_profit"]["confidence"],
                    f"up ${pnl:,.0f}, past the ${target:,.0f} target "
                    f"(the lesser of {tp_share:.0%} of the ${max_gain:,.0f} it can make and "
                    f"{float(dr['take_profit']['max_reward_multiple']):g}x the ${max_loss:,.0f} at risk)",
                )
            )
        if not off("directional.stop") and -pnl >= stop_share * max_loss:
            fired.append(
                _fire(
                    "directional.stop",
                    CLOSE,
                    dr["stop"]["confidence"],
                    f"down ${-pnl:,.0f}, {-pnl / max_loss:.0%} of the ${max_loss:,.0f} it can lose "
                    f"(stop at {stop_share:.0%})",
                )
            )
        te = rules["credit"]["time_exit"]
        if trade["nearest_short_dte"] is not None:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(_date(short_expiries[0]), int(te["dte"]))}
            if not off("credit.time_exit") and 0 <= trade["nearest_short_dte"] <= int(te["dte"]):
                fired.append(
                    _fire(
                        "credit.time_exit",
                        TAKE_PROFIT if pnl > 0 else CLOSE,
                        te["confidence"],
                        f"nearest short is {trade['nearest_short_dte']} DTE (exit at {te['dte']}); "
                        "a directional trade is closed rather than rolled into the last weeks",
                    )
                )

    # ---- credit trades
    elif kind == "credit":
        credit = basis
        cr = rules["credit"]
        loss = -pnl
        max_loss = -profile["max_loss_from_open"] if profile else None
        stop_at = float(cr["stop"]["loss_multiple_of_credit"]) * credit
        stop_basis = "credit"
        if max_loss is not None and max_loss < stop_at * 0.999:
            stop_at = float(cr["stop"]["fallback_max_loss_fraction"]) * max_loss
            stop_basis = "max_loss"
        trade["exit_levels"]["stop"] = {"close_value": -(credit + stop_at), "basis": stop_basis}
        tp_capture = float(cr["take_profit"]["capture"])
        trade["exit_levels"]["take_profit"] = {"close_value": -(credit * (1 - tp_capture))}

        if not off("credit.stop") and loss >= stop_at:
            fired.append(
                _fire(
                    "credit.stop",
                    CLOSE,
                    cr["stop"]["confidence"],
                    f"loss ${loss:,.0f} has reached the stop (${stop_at:,.0f}, "
                    + ("the credit" if stop_basis == "credit" else "half the max loss")
                    + ")",
                )
            )
        if not off("credit.take_profit") and trade["capture"] is not None and trade["capture"] >= tp_capture:
            fired.append(
                _fire(
                    "credit.take_profit",
                    TAKE_PROFIT,
                    cr["take_profit"]["confidence"],
                    f"{trade['capture']:.0%} of the ${credit:,.0f} credit captured (target {tp_capture:.0%})",
                )
            )
        te = cr["time_exit"]
        if trade["nearest_short_dte"] is not None:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(_date(short_expiries[0]), int(te["dte"]))}
            if not off("credit.time_exit") and 0 <= trade["nearest_short_dte"] <= int(te["dte"]):
                winner = pnl > 0
                fired.append(
                    _fire(
                        "credit.time_exit",
                        ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                        te["confidence"],
                        f"nearest short is {trade['nearest_short_dte']} DTE (exit at {te['dte']}); "
                        + (
                            "profitable, so it may roll out to ~45 DTE, for a net credit only"
                            if winner
                            else "losing, so close; never roll a loser"
                        ),
                    )
                )
        br = cr["breached"]
        if not off("credit.breached") and profile and spot:
            itm = [leg for leg in shorts if (leg["intrinsic"] or 0) > 0]
            if trade["shape"].startswith("jade lizard"):
                # No upside risk by construction: a call spread trading through is not a broken thesis.
                itm = [leg for leg in itm if leg["right"] == "P"]
            downside = -profile["downside_from_now"]
            if itm and downside > 0:
                ratio = profile["upside_from_now"] / downside
                if ratio < float(br["min_reward_to_risk"]):
                    fired.append(
                        _fire(
                            "credit.breached",
                            CLOSE,
                            br["confidence"],
                            f"short strike in the money and ${profile['upside_from_now']:,.0f} "
                            "left to win against "
                            f"${downside:,.0f} left to lose ({ratio:.2f}, "
                            f"entry standard {br['min_reward_to_risk']}): "
                            "would not open this today",
                            [_label(leg) for leg in itm],
                        )
                    )
        eb = cr["close_before_earnings"]
        if (
            not off("credit.close_before_earnings")
            and trade["earnings"]
            and trade["earnings"]["before_nearest_short_expiry"]
        ):
            fired.append(
                _fire(
                    "credit.close_before_earnings",
                    CLOSE,
                    eb["confidence"],
                    f"earnings {trade['earnings']['date']} fall inside the short legs' life",
                )
            )

    # ---- debit trades
    else:
        debit = basis
        db = rules["debit"]
        ret = trade["capture"]
        trade["exit_levels"]["stop"] = {"close_value": debit * (1 - float(db["stop"]["loss_fraction"]))}
        trade["exit_levels"]["take_profit"] = {
            "close_value": debit * (1 + float(db["take_profit"]["gain_fraction"]))
        }
        if not off("debit.stop") and ret is not None and ret <= -float(db["stop"]["loss_fraction"]):
            fired.append(
                _fire(
                    "debit.stop",
                    CLOSE,
                    db["stop"]["confidence"],
                    f"down {-ret:.0%} on the ${debit:,.0f} debit "
                    f"(stop at -{float(db['stop']['loss_fraction']):.0%})",
                )
            )
        if (
            not off("debit.take_profit")
            and ret is not None
            and ret >= float(db["take_profit"]["gain_fraction"])
        ):
            fired.append(
                _fire(
                    "debit.take_profit",
                    TAKE_PROFIT,
                    db["take_profit"]["confidence"],
                    f"up {ret:.0%} on the ${debit:,.0f} debit: take profit, roll or add "
                    f"(target +{float(db['take_profit']['gain_fraction']):.0%})",
                )
            )
        te = db["time_exit"]
        if shorts:
            limit, when = int(te["short_leg_dte"]), trade["nearest_short_dte"]
            anchor = _date(short_expiries[0])
        else:
            limit = int(te["long_only_dte"])
            when = (_date(expiries[0]) - session).days if expiries else None
            anchor = _date(expiries[0]) if expiries else None
        if anchor:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(anchor, limit)}
        if not off("debit.time_exit") and when is not None and 0 <= when <= limit:
            winner = pnl > 0
            fired.append(
                _fire(
                    "debit.time_exit",
                    ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                    te["confidence"],
                    f"{'nearest short' if shorts else 'nearest leg'} is {when} DTE (exit at {limit}); "
                    + (
                        "profitable, so the shorts may roll out, for a net credit only"
                        if winner
                        else "losing, so close; never roll a loser"
                    ),
                )
            )

    # ---- short legs that have given up most of their premium
    lt = rules["legs"]["short_take_profit"]
    if not off("legs.short_take_profit"):
        done = [
            leg for leg in shorts if leg["capture"] is not None and leg["capture"] >= float(lt["capture"])
        ]
        if done and len(done) < len(options):
            wings = [
                leg
                for leg in options
                if leg["quantity"] > 0
                and any(
                    leg["expiration"] == s["expiration"]
                    and leg["right"] == s["right"]
                    and (
                        (leg["strike"] > s["strike"]) if s["right"] == "C" else (leg["strike"] < s["strike"])
                    )
                    for s in done
                )
                and not any(
                    leg["expiration"] == s["expiration"] and leg["right"] == s["right"] and s not in done
                    for s in shorts
                )
            ]
            fired.append(
                _fire(
                    "legs.short_take_profit",
                    CLOSE_LEGS,
                    lt["confidence"],
                    f"{len(done)} short leg(s) have given up "
                    f"{min(leg['capture'] for leg in done):.0%}+ of their premium; "
                    "buy them back (with the wings that only protected them)",
                    [_label(leg) for leg in done + wings],
                )
            )
            trade["leg_order"] = _order(done + wings)

    # ---- ex-dividend assignment risk on short calls
    xr = rules["ex_dividend"]
    if (
        xr["enabled"]
        and not off("ex_dividend")
        and trade["ex_dividend"]
        and trade["ex_dividend"]["days"] <= int(xr["warn_days"])
    ):
        # The broker's `dividend_rate_per_share` is the per-payment amount (KO 0.53, SPY 1.89 quarterly;
        # TLT 0.31 monthly), and its ex-date is the last DECLARED one -- so this fires once the next
        # ex-date is announced, which is when assignment risk becomes knowable anyway.
        est_div = _f(metrics.get("dividend_rate_per_share")) or None
        cash_roots = {r.upper() for r in ex["cash_settled_roots"]}
        exposed = [
            leg
            for leg in shorts
            if leg["right"] == "C"
            and leg["root"] not in cash_roots
            and (leg["intrinsic"] or 0) > 0
            and leg["expiration"] >= trade["ex_dividend"]["date"]
            and (est_div is None or (leg["extrinsic"] or 0) < est_div)
        ]
        if exposed:
            fired.append(
                _fire(
                    "ex_dividend",
                    REVIEW,
                    xr["confidence"],
                    f"in-the-money short call(s) over the {trade['ex_dividend']['date']} ex-date "
                    "with less time value than "
                    + (f"the ~${est_div:.2f} dividend" if est_div else "the dividend (amount unknown)")
                    + ": early assignment is likely",
                    [_label(leg) for leg in exposed],
                )
            )

    # ---- size against the account
    rk = rules["risk"]
    if not off("risk.oversize") and profile and net_liq:
        budget = float(rk["max_risk_pct"]) * net_liq
        downside = -profile["downside_from_now"]
        trade["risk_budget"] = budget
        if profile["unbounded_low"] or downside > budget * float(rk["oversize_multiple"]):
            fired.append(
                _fire(
                    "risk.oversize",
                    str(rk["oversize_action"]).upper(),
                    rk["confidence"],
                    (
                        "loss is unbounded on the modelled grid"
                        if profile["unbounded_low"]
                        else f"${downside:,.0f} modelled downside is {downside / budget:.1f}x "
                        f"the ${budget:,.0f} per-trade budget "
                        f"({float(rk['max_risk_pct']):.0%} of net liq)"
                    ),
                )
            )

    # ---- a naked short put told to close may roll out in time instead, for a credit only
    rp = rules["roll"]["naked_put"]
    exiting = any(f["action"] in (CLOSE, ROLL_OR_CLOSE) for f in fired)
    if exiting and spot and not off("roll.naked_put"):
        roll = _naked_put_roll(options, session, spot, rate, rp)
        # Never roll a short put through earnings: close it, and re-open after the report if still wanted.
        if (
            roll
            and rp.get("skip_through_earnings", True)
            and er_date is not None
            and session <= er_date <= _date(roll["expiration"])
        ):
            trade["roll_blocked"] = {
                "rule": "roll.naked_put",
                "reason": "earnings",
                "earnings_date": er_date.isoformat(),
                "expiration": roll["expiration"],
            }
            roll = None
        if roll and roll["credit"] >= float(rp["min_credit"]):
            trade["roll"] = roll
            fired.append(
                _fire(
                    "roll.naked_put",
                    ROLL_FOR_CREDIT,
                    rp["confidence"],
                    f"roll the short {roll['strike']:g} put out to {roll['expiration']} for about "
                    f"{roll['credit']:.2f} cr (modelled) instead of closing it; "
                    "close if it will not fill for a credit",
                )
            )

    # ---- a diagonal's short structure rolls out for a credit when it is due, if the long still works
    rd = rules["roll"]["diagonal_short"]
    names = {f["rule"] for f in fired}
    due = {"credit.time_exit", "debit.time_exit", "legs.short_take_profit"} & names
    stopped = {"debit.stop", "credit.stop"} & names
    if due and spot and not off("roll.diagonal_short"):
        roll, blocked = _diagonal_roll(options, session, spot, rate, rd, cost)
        if stopped and (roll or blocked):
            # A stopped trade is not rolled whatever else holds: its long has failed.
            blocked, roll = {"reason": "stopped", "rules": sorted(stopped)}, None
        if roll and er_date is not None and session <= er_date <= _date(roll["expiration"]):
            if rd.get("skip_through_earnings"):
                blocked, roll = {"reason": "earnings", "earnings_date": er_date.isoformat()}, None
            else:
                roll["earnings_date"] = er_date.isoformat()
        if roll and roll["credit"] < float(rd["min_credit"]):
            blocked, roll = {"reason": "credit", "credit": roll["credit"]}, None
        if blocked:
            trade["roll_blocked"] = {**blocked, "rule": "roll.diagonal_short"}
        if roll:
            trade["roll"] = roll
            fired.append(
                _fire(
                    "roll.diagonal_short",
                    ROLL_FOR_CREDIT,
                    rd["confidence"],
                    f"roll the {roll['from']} short structure out to {roll['expiration']} "
                    "at the same strikes "
                    f"for about {roll['credit']:.2f} cr (modelled), keeping the long; "
                    "only for a net credit",
                    roll["legs"],
                )
            )

    # ---- an earnings trade holds through the report and closes after the call
    et = trade["earnings_trade"]
    if et and not off("earnings_trade"):
        # Its time exit is the call, not expiry less 21 days: on a weekly short that date falls before
        # the trade was even put on, and the rule it belongs to is set aside here anyway.
        report = _date(et["report"])
        bto = str(et["time_of_day"] or "").upper() == "BTO"
        after = report if bto else _calendar.next_trading_day(report)
        trade["exit_levels"]["time_exit"] = {"date": after.isoformat(), "rule": "earnings_trade"}
        if not et["passed"]:
            held = [f for f in fired if f["rule"].split(".")[0] in HELD_FOR_EARNINGS]
            fired = [f for f in fired if f not in held]
            trade["held_for_earnings"] = held
            if held:
                trade["roll"] = None
        else:
            fired.append(
                _fire(
                    "earnings_trade",
                    CLOSE,
                    rules["earnings_trade"]["confidence"],
                    f"the {et['report']} report is out: an earnings trade is closed after the call",
                )
            )

    trade["fired"] = fired
    return _decide(trade, rules, legs)


# The rule families an earnings trade's report outranks: closing, taking profit and rolling wait for
# the call. Safety (expiry and assignment, ex-dividend), sizing and unpriced legs never wait.
HELD_FOR_EARNINGS = {"credit", "debit", "directional", "legs", "roll"}


def earnings_trade(
    trade: dict[str, Any],
    metrics: dict[str, Any],
    session: date,
    rules: dict[str, Any],
    seen: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The trade as an EARNINGS TRADE -- one put on to hold through an announcement -- or None.

    The user's practice (2026-10-18): earnings trades are put on 1 to 7 days before the call, on a
    short that expires soon after it. So a trade is one when its opening order (the order chain's
    first) came `min_days_before`..`max_days_before` days before the report, and its nearest short
    expires on or after the report and no more than `max_days_expiry_after` days later. A trade
    without an order chain has no opening date to judge and is not classified.

    The report date comes from yesterday's record of the trade (`seen`, keyed by underlying and short
    expiry, carried forward by the evaluate command) before the broker's metrics: the day after a
    report the broker may already show next quarter's date, and the trade would forget it ever was
    one. `passed` is the call having happened: a day after the report, or the report's own day when
    it came before the open (BTO)."""
    cfg = rules["earnings_trade"]
    if cfg.get("enabled") is False:
        return None
    expiry = _date(trade.get("nearest_short_expiry") or trade.get("nearest_expiry"))
    if expiry is None:
        return None
    key = f"{trade['underlying']}|{expiry.isoformat()}"
    prior = (seen or {}).get(key) or {}
    report = _date(prior.get("report")) or _date(metrics.get("earnings_date"))
    time_of_day = prior.get("time") if prior.get("report") else metrics.get("earnings_time")
    orders = (trade.get("chain") or {}).get("orders") or []
    opened = _date(orders[0].get("date")) if orders else _date(prior.get("opened"))
    if report is None or report > expiry or (expiry - report).days > int(cfg["max_days_expiry_after"]):
        return None
    if not prior:
        if opened is None:
            return None
        lead = (report - opened).days
        if not int(cfg["min_days_before"]) <= lead <= int(cfg["max_days_before"]):
            return None
    passed = session > report or (session == report and str(time_of_day or "").upper() == "BTO")
    return {
        "key": key,
        "report": report.isoformat(),
        "time_of_day": time_of_day,
        "opened": opened.isoformat() if opened else None,
        "lead_days": (report - opened).days if opened else None,
        "passed": passed,
    }


def long_breakeven(longs: list[dict[str, Any]], basis: float) -> dict[str, float]:
    """Per right, the strike a short may not cross: the long's strike moved by the trade's net debit per
    long contract (calls: lowest long call + debit; puts: highest long put - debit). A net credit moves
    nothing."""
    out: dict[str, float] = {}
    for right in ("C", "P"):
        same = [leg for leg in longs if leg["right"] == right and leg["quantity"] > 0]
        qty = sum(leg["quantity"] for leg in same)
        if not same or not qty:
            continue
        per_share = max(basis, 0.0) / pricing.MULTIPLIER / qty
        if right == "C":
            out[right] = min(leg["strike"] for leg in same) + per_share
        else:
            out[right] = max(leg["strike"] for leg in same) - per_share
    return out


def _diagonal_roll(
    options: list[dict[str, Any]],
    session: date,
    spot: float,
    rate: float,
    cfg: dict[str, Any],
    cost: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(roll, None) or (None, why not) for a diagonal's short structure.

    The structure is every leg in the nearest expiry that holds a short; the longs it is sold against
    are the legs expiring later. Priced at each leg's own IV on the new expiry (flat term structure), so
    the credit is an estimate.
    """
    shorts = [leg for leg in options if leg["quantity"] < 0]
    if not shorts:
        return None, None
    front = min(leg["expiration"] for leg in shorts)
    group = [leg for leg in options if leg["expiration"] == front]
    longs = [leg for leg in options if leg["expiration"] > front and leg["quantity"] > 0]
    if not longs or any(leg["iv"] is None or leg["mid"] is None for leg in group):
        return None, None  # not a diagonal, or the front cannot be priced
    new_exp = _monthly_on_or_after(_date(front) + timedelta(days=int(cfg["out_days"])))
    long_exp = min(_date(leg["expiration"]) for leg in longs)
    if (long_exp - new_exp).days < int(cfg["min_long_days"]):
        return None, {
            "reason": "long_too_short",
            "long_expiration": long_exp.isoformat(),
            "min_days": int(cfg["min_long_days"]),
        }
    years = (new_exp - session).days / 365.0
    new_mids = [pricing.price(spot, leg["strike"], years, leg["iv"], leg["right"], rate) for leg in group]
    unit = reduce(math.gcd, [int(abs(leg["quantity"])) or 1 for leg in group])
    net = sum(leg["quantity"] * (leg["mid"] - new) for leg, new in zip(group, new_mids, strict=True))
    credit = net / unit
    # The long's breakeven after the roll (its credit counts): a short beyond it cannot lock in a loss.
    line = long_breakeven(longs, cost - net * pricing.MULTIPLIER)
    for short in (leg for leg in group if leg["quantity"] < 0):
        edge = line.get(short["right"])
        if edge is None:
            continue
        if (short["strike"] < edge) if short["right"] == "C" else (short["strike"] > edge):
            return None, {"reason": "breakeven", "strike": short["strike"], "breakeven": round(edge, 2)}
    close_rows = [
        f"{'BTC' if leg['quantity'] < 0 else 'STC'} {abs(leg['quantity']):g} {front} "
        f"{leg['strike']:g}{leg['right']} @ {leg['mid']:.2f}"
        for leg in group
    ]
    open_rows = [
        f"{'STO' if leg['quantity'] < 0 else 'BTO'} {abs(leg['quantity']):g} {new_exp.isoformat()} "
        f"{leg['strike']:g}{leg['right']} @ ~{new:.2f} (modelled)"
        for leg, new in zip(group, new_mids, strict=True)
    ]
    return (
        {
            "kind": "diagonal_short",
            "from": front,
            "expiration": new_exp.isoformat(),
            "strike": shorts[0]["strike"],
            "credit": credit,
            "net": net * pricing.MULTIPLIER,
            "modelled": True,
            "legs": close_rows + open_rows,
        },
        None,
    )


def _monthly_on_or_after(day: date) -> date:
    """The first standard monthly expiry (third Friday, or the trading day before it) on or after `day`."""
    y, m = day.year, day.month
    while True:
        d = date(y, m, 15)
        while d.weekday() != 4:
            d += timedelta(days=1)
        if not _calendar.is_trading_day(d):
            d = _calendar.previous_trading_day(d)
        if d >= day:
            return d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _naked_put_roll(
    options: list[dict[str, Any]], session: date, spot: float, rate: float, cfg: dict[str, Any]
) -> dict[str, Any] | None:
    """Same strike, out to the next monthly at least `out_days` past today's expiry: the modelled credit.

    Only for a trade that is nothing but short puts at one strike and expiry -- a naked short put. The new
    put is priced at the current put's own IV (a flat term structure), so the credit is an estimate.
    """
    if not options or any(leg["right"] != "P" or leg["quantity"] >= 0 for leg in options):
        return None
    if len({(leg["strike"], leg["expiration"]) for leg in options}) != 1:
        return None
    leg = options[0]
    if leg["iv"] is None or leg["mid"] is None or leg["dte"] is None or leg["dte"] < 0:
        return None
    current = _date(leg["expiration"])
    new_exp = _monthly_on_or_after(current + timedelta(days=int(cfg["out_days"])))
    years = (new_exp - session).days / 365.0
    new_mid = pricing.price(spot, leg["strike"], years, leg["iv"], "P", rate)
    qty = abs(sum(x["quantity"] for x in options))
    credit = new_mid - leg["mid"]
    return {
        "strike": leg["strike"],
        "expiration": new_exp.isoformat(),
        "dte": (new_exp - session).days,
        "close_mid": leg["mid"],
        "open_mid": new_mid,
        "credit": credit,
        "net": credit * pricing.MULTIPLIER * qty,
        "modelled": True,
        "legs": [
            f"BTC {qty:g} {leg['expiration']} {leg['strike']:g}P @ {leg['mid']:.2f}",
            f"STO {qty:g} {new_exp.isoformat()} {leg['strike']:g}P @ ~{new_mid:.2f} (modelled)",
        ],
    }


def _price_levels(trade: dict[str, Any], legs: list[dict[str, Any]]) -> None:
    """Each exit level's close value as the net price a person would enter (per unit of the trade)."""
    if not legs or not all(leg["right"] for leg in legs):
        return
    unit = reduce(math.gcd, [int(abs(leg["quantity"])) or 1 for leg in legs])
    for level in trade["exit_levels"].values():
        value = level.get("close_value")
        if value is not None:
            level["net_price"] = abs(value) / pricing.MULTIPLIER / unit
            level["net_side"] = "cr" if value >= 0 else "db"


def _opening(trade: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the trade was opened for, as signed cash flow (a credit received is +) and a net price."""
    chain = trade.get("chain")
    if chain and chain["orders"]:
        # The order that opened the chain; the rolls since are on the chain itself.
        first = chain["orders"][0]
        return {"net": first["net"], "net_price": first["net_price"], "net_side": first["net_side"]}
    cost = trade.get("cost")
    if cost is None:
        return None
    out: dict[str, Any] = {"net": -cost, "net_price": None, "net_side": "cr" if cost < 0 else "db"}
    if legs and all(leg["right"] for leg in legs):
        unit = reduce(math.gcd, [int(abs(leg["quantity"])) or 1 for leg in legs])
        out["net_price"] = abs(cost) / pricing.MULTIPLIER / unit
    return out


def _decide(trade: dict[str, Any], rules: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    _price_levels(trade, legs)
    _recommend(trade, rules, legs)
    trade["opening"] = _opening(trade, legs)
    trade["explanation"] = _explain.explain(trade)
    return trade


def _recommend(trade: dict[str, Any], rules: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    fired = trade["fired"]
    if not fired:
        hold = rules["hold"]
        confidence = int(hold["confidence"])
        reason = "no rule fired; hold with the exit levels resting"
        rule = "hold"
        et = trade.get("earnings_trade")
        if et and not et["passed"] and not _rules.disabled(rules, trade["underlying"], "earnings_trade"):
            # Held FOR the report, not in spite of it: the report is the trade, so no earnings penalty.
            rule = "earnings_trade"
            confidence = int(rules["earnings_trade"]["confidence"])
            reason = f"an earnings trade: hold through the {et['report']} report and close after the call"
            held = trade.get("held_for_earnings") or []
            if held:
                reason += "; waiting for the call: " + ", ".join(
                    f"{f['rule']} ({f['action'].lower()})" for f in held
                )
        elif trade.get("earnings") and trade["earnings"]["before_nearest_short_expiry"]:
            confidence -= int(hold["earnings_penalty"])
            reason += f"; earnings {trade['earnings']['date']} fall inside the trade"
        trade["recommendation"] = {
            "action": HOLD,
            "confidence": max(0, confidence),
            "rule": rule,
            "reason": reason,
            "agreeing": [],
            "conflicts": [],
        }
        trade["order"] = None
        return trade
    ranked = sorted(fired, key=lambda f: (PRIORITY.get(f["action"], 9), -(f["confidence"] or 0)))
    chosen = ranked[0]
    direction = DIRECTION.get(chosen["action"])
    conflicts = [
        f["rule"]
        for f in fired
        if direction and DIRECTION.get(f["action"]) and DIRECTION[f["action"]] != direction
    ]
    # A credit roll is offered BECAUSE a close fired: those rules are its trigger, not its opposition.
    replaces: list[str] = []
    if chosen["action"] == ROLL_FOR_CREDIT:
        replaces = [f["rule"] for f in fired if f["action"] in (CLOSE, ROLL_OR_CLOSE, CLOSE_LEGS)]
        conflicts = [r for r in conflicts if r not in replaces]
    confidence = chosen["confidence"]
    if confidence is not None and conflicts:
        confidence = max(0, confidence - int(rules["conflict_penalty"]) * len(conflicts))
    trade["recommendation"] = {
        "action": chosen["action"],
        "confidence": confidence,
        "rule": chosen["rule"],
        "reason": chosen["reason"],
        "agreeing": [f["rule"] for f in fired if f is not chosen and f["action"] == chosen["action"]],
        "conflicts": conflicts,
        "replaces": replaces,
    }
    if chosen["action"] in (CLOSE, TAKE_PROFIT, ROLL_OR_CLOSE, ROLL_FOR_CREDIT):
        trade["order"] = _order(legs)
    elif chosen["action"] == CLOSE_LEGS:
        named = set(chosen["legs"] or [])
        trade["order"] = trade.get("leg_order") or _order([leg for leg in legs if _label(leg) in named])
    else:
        trade["order"] = None
    trade.pop("leg_order", None)
    return trade


# --------------------------------------------------------------------------- grouping legs into trades
def _covered(legs: list[dict[str, Any]]) -> bool:
    """True when no right is net short within these legs: the expiry stands on its own."""
    for right in ("C", "P"):
        if sum(_f(leg.get("quantity")) or 0.0 for leg in legs if leg.get("right") == right) < 0:
            return False
    return True


def split_trades(legs: list[dict[str, Any]], grouping: str | None) -> list[list[dict[str, Any]]]:
    """An underlying's legs as one or more trades.

    `expiry_chain` (the default) starts a trade per expiry and folds an expiry into the next later one
    whenever its shorts are not covered inside it -- a diagonal's front short belongs to the long it is
    sold against. So a weekly put spread and a monthly call spread on one name are two trades, while a
    calendar, a diagonal or a PMCC stays one. Any share leg makes the underlying one trade (it covers
    every expiry's calls). `underlying` always makes it one trade.
    """
    if grouping == "underlying" or any(leg.get("right") not in ("C", "P") for leg in legs):
        return [legs] if legs else []
    by_exp: dict[str, list[dict[str, Any]]] = {}
    for leg in legs:
        by_exp.setdefault(str(leg.get("expiration")), []).append(leg)
    trades: list[list[dict[str, Any]]] = []
    carry: list[dict[str, Any]] = []
    expiries = sorted(by_exp)
    for i, exp in enumerate(expiries):
        group = carry + by_exp[exp]
        if _covered(group) or i == len(expiries) - 1:
            trades.append(group)
            carry = []
        else:
            carry = group
    return trades


ACTION_CODE = {"Buy to Open": "BTO", "Sell to Open": "STO", "Buy to Close": "BTC", "Sell to Close": "STC"}


def _occ_label(symbol: str) -> str:
    """'GLD 261120C00410000' -> '2026-11-20 410C'; anything else unchanged."""
    parts = symbol.split()
    if len(parts) == 2 and len(parts[1]) == 15:
        code = parts[1]
        strike = int(code[7:]) / 1000
        return f"20{code[:2]}-{code[2:4]}-{code[4:6]} {strike:g}{code[6]}"
    return symbol


def _chain_view(chain: dict[str, Any]) -> dict[str, Any]:
    """The chain as the evaluation records it: each order's role, cash and legs, oldest first."""
    orders = []
    for o in chain["orders"]:
        net = o["net"]
        orders.append(
            {
                "executed_at": o["executed_at"],
                "date": o["executed_at"][:10],
                "role": o["role"],
                "net": net,
                "net_price": abs(net) / pricing.MULTIPLIER / (o.get("unit") or 1),
                "net_side": "cr" if net >= 0 else "db",
                "fees": o["fees"],
                "legs": [
                    f"{ACTION_CODE.get(leg['action'], leg['action'])} {leg['quantity']:g} "
                    f"{_occ_label(leg['symbol'])} @ {leg['price']:.2f}"
                    for leg in o["legs"]
                ],
            }
        )
    return {
        "orders": orders,
        "cash": chain["cash"],
        "fees": chain["fees"],
        "rolls": sum(1 for o in chain["orders"] if o["role"] == "roll"),
    }


def _without_excluded(
    underlying: dict[str, Any], session: date, rules: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """An underlying's legs split into those evaluated and those the ruleset excludes: legs expiring
    today (or already expired) on an underlying listed in `exclude.zero_dte_underlyings`."""
    legs = underlying.get("legs") or []
    names = {str(s).upper() for s in ((rules.get("exclude") or {}).get("zero_dte_underlyings") or [])}
    if str(underlying.get("underlying") or "").upper() not in names:
        return legs, []
    keep, drop = [], []
    for leg in legs:
        exp = _date(leg.get("expiration")) if leg.get("right") in ("C", "P") else None
        (drop if exp is not None and exp <= session else keep).append(leg)
    return keep, drop


def group_trades(
    legs: list[dict[str, Any]],
    grouping: str | None,
    history: list[dict[str, Any]] | None,
    history_ok: bool,
) -> list[tuple[list[dict[str, Any]], dict[str, Any] | None]]:
    """An underlying's legs as trades, each with its reconciled order chain or None.

    `order_chain` (the default) groups by the broker's own chains when the history reconciles them;
    any leg it cannot explain falls back to `expiry_chain`, priced from the open legs.
    """
    if grouping != "order_chain" or not history_ok or history is None:
        fallback = "expiry_chain" if grouping == "order_chain" else grouping
        return [(g, None) for g in split_trades(legs, fallback)]
    built = _chains.build(legs, history)
    out: list[tuple[list[dict[str, Any]], dict[str, Any] | None]] = []

    def norm(leg: dict[str, Any]) -> str:
        return " ".join(str(leg.get("symbol") or "").split())

    for chain in built["chains"]:
        if chain["reconciled"]:
            members = [leg for leg in legs if norm(leg) in chain["symbols"]]
            if members:
                out.append((members, chain))
    loose = [leg for leg in legs if norm(leg) in set(built["unchained"])]
    out += [(g, None) for g in split_trades(loose, "expiry_chain")]
    return out


# --------------------------------------------------------------------------- the whole snapshot
def evaluate(
    snapshot: dict[str, Any], rules: dict[str, Any], warnings: list[str] | None = None
) -> dict[str, Any]:
    session = _date(snapshot.get("session"))
    if session is None:
        raise ValueError("snapshot has no session date")
    positions = snapshot.get("positions") or {}
    metrics = snapshot.get("metrics") or {}
    # Trade history arrived with snapshot v2; without it (or when its fetch failed) every trade falls back
    # to the open legs' prices, and says so.
    history = snapshot.get("transactions") or {}
    history_ok = "transactions" in snapshot and not snapshot.get("transactions_error")
    accounts = []
    for account in positions.get("accounts") or []:
        balances = account.get("balances") or {}
        net_liq = _f(balances.get("net_liquidating_value"))
        trades = []
        excluded = []
        for u in account.get("underlyings") or []:
            legs, dropped = _without_excluded(u, session, rules)
            if dropped:
                excluded.append(
                    {
                        "underlying": u["underlying"],
                        "legs": len(dropped),
                        "reason": "0DTE excluded by the ruleset",
                    }
                )
            if not legs:
                continue
            groups = group_trades(legs, rules.get("trade_grouping"), history.get(u["underlying"]), history_ok)
            for i, (group, chain) in enumerate(groups):
                trade = evaluate_trade(
                    u["underlying"],
                    group,
                    metrics.get(u["underlying"]),
                    session,
                    net_liq,
                    rules,
                    chain,
                    snapshot.get("earnings_trades_seen"),
                )
                trade["trade_id"] = u["underlying"] if len(groups) == 1 else f"{u['underlying']}#{i + 1}"
                trades.append(trade)
        trades.sort(key=lambda t: (PRIORITY.get(t["recommendation"]["action"], 9), -abs(t["pnl_gross"] or 0)))
        counts: dict[str, int] = {}
        for t in trades:
            counts[t["recommendation"]["action"]] = counts.get(t["recommendation"]["action"], 0) + 1
        book = _portfolio.reading(
            trades, metrics, snapshot.get("benchmark"), balances, account.get("account_type"), rules
        )
        book["hedges"] = _hedge.suggest(book, snapshot.get("hedge_chain"), metrics, session, rules)
        book["candidates"] = _candidates.suggest(
            snapshot.get("income"),
            book,
            trades,
            session,
            rules,
            account.get("account_type"),
            book.get("benchmark_spot"),
        )
        accounts.append(
            {
                "account": account.get("account"),
                "designated": account.get("designated"),
                "error": account.get("error"),
                "net_liq": net_liq,
                "maintenance": _f(balances.get("maintenance_requirement")),
                "buying_power": _f(balances.get("derivative_buying_power")),
                "risk_budget": (float(rules["risk"]["max_risk_pct"]) * net_liq) if net_liq else None,
                "pnl_gross": sum(t["pnl_gross"] for t in trades if t["pnl_gross"] is not None),
                "counts": counts,
                "portfolio": book,
                "trades": trades,
                "excluded": excluded,
            }
        )
    return {
        "eval_version": EVAL_VERSION,
        "session": session.isoformat(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "ok": bool(positions.get("ok")),
        "error": positions.get("error"),
        "metrics_error": snapshot.get("metrics_error"),
        "transactions_error": snapshot.get("transactions_error")
        if "transactions" in snapshot
        else "no trade history in this snapshot (taken before chains were read)",
        "marks": positions.get("marks"),
        "ruleset": rules.get("ruleset"),
        "rules": rules,
        "rule_warnings": list(warnings or []),
        "accounts": accounts,
    }
