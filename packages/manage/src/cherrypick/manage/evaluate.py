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
from cherrypick.manage import pmcc as _pmcc
from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules
from cherrypick.manage import shapes as _shapes
from cherrypick.manage import wheel as _wheel

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
    roll_chains: dict[str, Any] | None = None,
    wheel_ctx: dict[str, Any] | None = None,
    pmcc_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`chain`, when the broker history reconciled one, sets the basis: the chain's net cash across its
    opening order and every roll, not the open legs' average prices. `seen` is the earnings trades the
    previous evaluation found (`earnings_trade`). `roll_chains` is the snapshot's quoted chains, keyed
    "<UNDERLYING>|<expiry>", that a strangle's untested side is rolled along. `wheel_ctx` is the wheel
    cycles holding this trade's legs (`_wheel_context`), which the wheel rules read; `pmcc_ctx` the PMCC
    view entries whose short this trade holds (`_pmcc_context`), which the PMCC rules read."""
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
        "long_vertical": None,
        "fly_off": None,
        "untested_roll": None,
        "wheel": None,
        "pmcc": None,
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
        and _shapes.risk_judged(trade["shape"])
        and profile
        and not profile["unbounded_low"]
        and -profile["max_loss_from_open"] > 0
        and abs(cost) < float(dr["max_premium_share"]) * -profile["max_loss_from_open"]
    )
    if directional:
        trade["kind"] = "directional" if trade["shape"] in _shapes.DIRECTIONAL else "risk-judged"

    fired: list[dict] = []
    # The expiry close and the first day its legs expire: an earnings trade closed after its call
    # before that day lets it wait.
    expiry_close: dict | None = None
    expiring_on: date | None = None

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
            expiring_on = min(_date(leg["expiration"]) for leg in expiring)
            expiry_close = _fire(
                "expiry",
                CLOSE if whole else CLOSE_LEGS,
                ex["confidence"],
                f"physically-settled legs expire in {soonest} day(s); never hold into expiry "
                "(pin and post-close exercise risk)",
                [_label(leg) for leg in expiring],
            )
            fired.append(expiry_close)

    # ---- earnings and ex-dividend facts
    er_date = _date(metrics.get("earnings_date"))
    # A report before today's open is already out: no longer inside the trade.
    upcoming = er_date is not None and (
        er_date > session or (er_date == session and not before_open(metrics.get("earnings_time")))
    )
    if rules["earnings"]["enabled"] and upcoming and expiries:
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
    # A name excused from the rule (`positions.<SYM>.disable`) is not an earnings trade at all: its
    # ordinary exits stand, so the evaluation must not say it is holding for the call. Readers (the
    # console's chip, the carried-forward `seen` record) take this field as the verdict.
    trade["earnings_trade"] = (
        None if off("earnings_trade") else earnings_trade(trade, metrics, session, rules, seen)
    )

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
        tp_capture, tp_confidence, tp_shaped = _rules.for_shape(cr["take_profit"], trade["shape"], "capture")
        trade["exit_levels"]["take_profit"] = {
            "close_value": -(credit * (1 - tp_capture)),
            "target": tp_capture,
            "by_shape": trade["shape"] if tp_shaped else None,
        }

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
                    tp_confidence,
                    f"{trade['capture']:.0%} of the ${credit:,.0f} credit captured (target {tp_capture:.0%}"
                    + (f", a {trade['shape']}'s own" if tp_shaped else "")
                    + ")",
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
            risk_side = _shapes.RISK_SIDE.get(trade["shape"])
            if risk_side:
                # A lizard has no risk on its spread's side by construction: a short trading through
                # there is not a broken thesis.
                itm = [leg for leg in itm if leg["right"] == risk_side]
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
        gain, gain_confidence, gain_shaped = _rules.for_shape(
            db["take_profit"], trade["shape"], "gain_fraction"
        )
        trade["exit_levels"]["stop"] = {"close_value": debit * (1 - float(db["stop"]["loss_fraction"]))}
        trade["exit_levels"]["take_profit"] = {
            "close_value": debit * (1 + gain),
            "target": gain,
            "by_shape": trade["shape"] if gain_shaped else None,
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
        if not off("debit.take_profit") and ret is not None and ret >= gain:
            fired.append(
                _fire(
                    "debit.take_profit",
                    TAKE_PROFIT,
                    gain_confidence,
                    f"up {ret:.0%} on the ${debit:,.0f} debit: take profit, roll or add "
                    f"(target +{gain:.0%}" + (f", a {trade['shape']}'s own" if gain_shaped else "") + ")",
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

    # ---- a diagonal held for most of the distance between its strikes has little left to make
    dw = rules["debit"]["diagonal_width"]
    width = _diagonal_width(options) if trade["shape"] == "diagonal" else None
    if width and cost > 0 and not off("debit.diagonal_width"):
        share = float(dw["max_share_of_width"])
        if cost > share * width:
            fired.append(
                _fire(
                    "debit.diagonal_width",
                    REVIEW,
                    dw["confidence"],
                    f"${cost:,.0f} net debit is {cost / width:.0%} of the ${width:,.0f} between the strikes "
                    f"(at most {share:.0%}): "
                    + (
                        "a deep move through the short loses money once the long's time value runs off"
                        if cost >= width
                        else "a deep move through the short leaves little to make"
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
            trade.setdefault("leg_orders", {})["legs.short_take_profit"] = _order(done + wings)

    # ---- a butterfly's long vertical near its full value is sold, keeping the short side
    lv = rules["legs"]["long_vertical"]
    # Inside the expiry window the whole trade closes: a short spread is never kept into expiry.
    expiring = any(leg["dte"] is not None and leg["dte"] <= int(ex["close_by_dte"]) for leg in options)
    split = (
        _long_vertical(options, trade["shape"]) if not off("legs.long_vertical") and not expiring else None
    )
    if split:
        sold, rest = split
        worth = sum(leg["value"] for leg in sold)
        width = abs(sold[0]["strike"] - sold[1]["strike"]) * sold[0]["quantity"] * pricing.MULTIPLIER
        share = float(lv["share_of_width"])
        if width > 0 and worth >= share * width:
            rest_cost = -sum(leg["value"] for leg in rest)
            rest_width = (
                abs(rest[0]["strike"] - rest[1]["strike"]) * abs(rest[0]["quantity"]) * pricing.MULTIPLIER
            )
            trade["long_vertical"] = {
                "legs": [_label(leg) for leg in sold],
                "value": worth,
                "width": width,
                "rest": [_label(leg) for leg in rest],
                "rest_cost": rest_cost,
                "rest_width": rest_width,
            }
            fired.append(
                _fire(
                    "legs.long_vertical",
                    CLOSE_LEGS,
                    lv["confidence"],
                    f"the long spread is worth ${worth:,.0f}, {worth / width:.0%} of its ${width:,.0f} width "
                    f"(sell at {share:.0%}); sell it and keep the short spread, "
                    f"${rest_cost:,.0f} to buy back, ${rest_width:,.0f} wide",
                    [_label(leg) for leg in sold],
                )
            )
            trade.setdefault("leg_orders", {})["legs.long_vertical"] = _order(sold)

    # ---- a broken-wing butterfly taken in for a credit flies off its risk while that costs less
    fo = rules["roll"]["fly_off"]
    if (
        trade["shape"] == "broken-wing butterfly"
        and cost < 0
        and spot
        and not expiring
        and not off("roll.fly_off")
    ):
        fly = _fly_off(options, spot, rate)
        if fly and fly["debit"] <= float(fo["max_share_of_credit"]) * -cost:
            fly["kept"] = -cost - fly["debit"]
            trade["fly_off"] = fly
            fired.append(
                _fire(
                    "roll.fly_off",
                    REVIEW,
                    fo["confidence"],
                    f"buy the {fly['new_strike']:g}{fly['right']} and sell the "
                    f"{fly['old_strike']:g}{fly['right']} for about ${fly['debit']:,.0f} (modelled) against "
                    f"the ${-cost:,.0f} taken in: a plain butterfly that keeps ${fly['kept']:,.0f} "
                    "and cannot lose",
                    fly["legs"],
                )
            )

    # ---- a tested strangle or straddle rolls its untested short toward the money, for a credit
    ur = rules["roll"]["untested_side"]
    if (
        trade["shape"] in ("short strangle", "short straddle")
        and spot
        and not expiring
        and not off("roll.untested_side")
    ):
        roll = _untested_roll(options, spot, rate, ur, (roll_chains or {}), underlying)
        if roll:
            trade["untested_roll"] = roll
            if not roll.get("blocked"):
                # The untested short has often decayed past the buy-back rule's capture by now. Buying it
                # back alone leaves the tested short on its own, so the roll -- the same buy-back with a
                # new short sold closer -- is offered in its place, the buy-back its fallback order. A
                # stop or any other close still outranks it: this is repair, never a rescue.
                untested = f"{roll['from_strike']:g}{roll['right']}"
                buyback = next(
                    (
                        f
                        for f in fired
                        if f["rule"] == "legs.short_take_profit"
                        and any(str(x).endswith(f" {untested}") for x in f["legs"] or [])
                    ),
                    None,
                )
                closing = any(f["action"] in (CLOSE, ROLL_OR_CLOSE) for f in fired)
                instead = bool(buyback) and not closing
                if instead:
                    trade.setdefault("leg_orders", {})["roll.untested_side"] = trade["leg_orders"][
                        "legs.short_take_profit"
                    ]
                fired.append(
                    _fire(
                        "roll.untested_side",
                        ROLL_FOR_CREDIT if instead else REVIEW,
                        ur["confidence"],
                        f"the {roll['tested_strike']:g}{roll['tested_right']} is tested "
                        f"({abs(roll['tested_delta']):.2f} delta): roll the untested "
                        f"{roll['from_strike']:g}{roll['right']} to the {roll['to_strike']:g}{roll['right']} "
                        f"({abs(roll['to_delta']):.2f} delta) for {roll['credit']:.2f} cr at the quoted mids",
                        roll["legs"],
                    )
                )

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
                        # :g, not :.0%: a 2.5% budget must not read as 2%.
                        f"({float(rk['max_risk_pct']) * 100:g}% of net liq)"
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

    # ---- a wheel cycle's shorts and shares (wheel.py): ranked with every other rule by action
    if wheel_ctx:
        fired += _wheel_rules(trade, legs, wheel_ctx, session, net_liq, metrics, rules, off)

    # ---- a recognised PMCC's width, assignment exposure and long (pmcc.py): ranked by action too
    if pmcc_ctx:
        try:
            fired += _pmcc_rules(trade, legs, pmcc_ctx, session, net_liq, metrics, rules, off)
        except Exception as exc:  # noqa: BLE001 -- the PMCC view is a reading; the evaluation is the product
            trade["pmcc"] = {"error": f"PMCC rules failed: {type(exc).__name__}: {exc}"[:300]}

    # ---- an earnings trade holds through the report and closes after the call
    et = trade["earnings_trade"]
    if et and not off("earnings_trade"):
        # Its time exit is the call, not expiry less 21 days: on a weekly short that date falls before
        # the trade was even put on, and the rule it belongs to is set aside here anyway.
        report = _date(et["report"])
        after = report if before_open(et["time_of_day"]) else _calendar.next_trading_day(report)
        trade["exit_levels"]["time_exit"] = {"date": after.isoformat(), "rule": "earnings_trade"}
        if not et["passed"]:
            # The expiry close waits too when the trade comes off after the call before its legs'
            # expiry day: the pin and post-close exercise risk it guards against is never reached. An
            # earnings trade sits on a short expiring days after the call, so otherwise it is always
            # inside the window and the hold could never apply. Closed on expiry day itself, it stands.
            waits = (expiry_close,) if expiring_on is not None and after < expiring_on else ()
            held = [f for f in fired if f["rule"].split(".")[0] in HELD_FOR_EARNINGS or f in waits]
            fired = [f for f in fired if f not in held]
            trade["held_for_earnings"] = held
            if held:
                trade["roll"] = None
        elif len(expiries) > 1 and pnl < 0:
            # A loser with a leg in a later expiry (a diagonal, a calendar) can recover after the call,
            # so it is not closed for being an earnings trade: the ordinary rules manage it from here.
            et["kept"] = True
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
# the call. Safety (expiry and assignment, ex-dividend), sizing and unpriced legs never wait, except
# the expiry close when the trade comes off after the call before its legs expire.
HELD_FOR_EARNINGS = {"credit", "debit", "directional", "legs", "roll", "wheel"}

# A report before the open: the broker spells it BTO, most calendars BMO.
BEFORE_OPEN = {"BTO", "BMO"}


def before_open(time_of_day: Any) -> bool:
    """A report before the open is closed on its own day; after the close, or untimed, the day after."""
    return str(time_of_day or "").upper() in BEFORE_OPEN


def earnings_trade(
    trade: dict[str, Any],
    metrics: dict[str, Any],
    session: date,
    rules: dict[str, Any],
    seen: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The trade as an EARNINGS TRADE -- one put on to hold through an announcement -- or None.

    The user's practice (2026-10-18): earnings trades are put on 1 to 7 days before the call, on a
    short that expires soon after it, or on the report's own day before an after-the-close call. So
    a trade is one when its opening order (the order chain's first) came
    `min_days_before`..`max_days_before` days before the report, and its nearest short
    expires on or after the report and no more than `max_days_expiry_after` days later. A trade
    without an order chain has no opening date to judge and is not classified.

    The report date is the broker's while it falls inside the trade (from its opening to its short's
    expiry), so a rescheduled report moves the trade with it. Otherwise it is yesterday's record of
    the trade (`seen`, keyed by underlying and short expiry, carried forward by the evaluate command):
    the day after a report the broker may already show next quarter's date, and the trade would
    forget it ever was one. A report on the short's expiry day counts only before the open; after
    the close, the short has expired before the call. `passed` is the call having happened: a day
    after the report, or the report's own day when it came before the open (BTO). After the call a
    winner closes; a loser with a leg in a later expiry is `kept` for the ordinary rules."""
    cfg = rules["earnings_trade"]
    if cfg.get("enabled") is False:
        return None
    expiry = _date(trade.get("nearest_short_expiry") or trade.get("nearest_expiry"))
    if expiry is None:
        return None
    key = f"{trade['underlying']}|{expiry.isoformat()}"
    prior = (seen or {}).get(key) or {}
    orders = (trade.get("chain") or {}).get("orders") or []
    opened = _date(orders[0].get("date")) if orders else _date(prior.get("opened"))
    broker = _date(metrics.get("earnings_date"))
    inside = broker is not None and broker <= expiry and (opened is None or broker >= opened)
    report = broker if inside or not prior.get("report") else _date(prior.get("report"))
    # The broker's time for this report wins (a corrected time is taken); without one, yesterday's
    # for the same report: a time can arrive a day late, and an untimed report is held a day past a
    # before-the-open call.
    time_of_day = metrics.get("earnings_time") if broker == report else None
    if not time_of_day and _date(prior.get("report")) == report:
        time_of_day = prior.get("time")
    if report is None or report > expiry or (expiry - report).days > int(cfg["max_days_expiry_after"]):
        return None
    if report == expiry and not before_open(time_of_day):
        return None
    if not prior:
        if opened is None:
            return None
        lead = (report - opened).days
        if not int(cfg["min_days_before"]) <= lead <= int(cfg["max_days_before"]):
            return None
        # Opened on the report's own day, it went on before an after-the-close call; a before-the-open
        # call had already happened, so the trade was not put on for it.
        if lead == 0 and before_open(time_of_day):
            return None
    passed = session > report or (session == report and before_open(time_of_day))
    return {
        "key": key,
        "report": report.isoformat(),
        "time_of_day": time_of_day,
        "opened": opened.isoformat() if opened else None,
        "kept": False,
        "lead_days": (report - opened).days if opened else None,
        "passed": passed,
    }


LONG_VERTICAL_SHAPES = frozenset({"butterfly", "broken-wing butterfly", "broken-heart butterfly"})


def _long_vertical(
    options: list[dict[str, Any]], shape: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """A butterfly's long vertical and the short spread left without it, or None for any other shape.

    Read from the money outward (calls up, puts down), a butterfly is long the first strike and short the
    body; its long vertical is the first long with as many of the body as it has, and what is left is a
    short spread from the body to the far wing. A broken-heart butterfly's two verticals are its first two
    and last two legs. The body is split by copying its leg at the vertical's quantity, valued at its mid."""
    if shape not in LONG_VERTICAL_SHAPES:
        return None
    outward = sorted(options, key=lambda leg: leg["strike"], reverse=options[0]["right"] == "P")
    if shape == "broken-heart butterfly":
        return outward[:2], outward[2:]
    near, body, far = outward
    qty = near["quantity"]

    def part(leg: dict[str, Any], q: float) -> dict[str, Any]:
        out = {**leg, "quantity": q, "value": q * leg["mid"] * pricing.MULTIPLIER}
        out["label"] = _label(out)
        return out

    return [near, part(body, -qty)], [part(body, body["quantity"] + qty), far]


def _untested_roll(
    options: list[dict[str, Any]],
    spot: float,
    rate: float,
    cfg: dict[str, Any],
    roll_chains: dict[str, Any],
    underlying: str,
) -> dict[str, Any] | None:
    """The untested short of a tested strangle or straddle rolled toward the money, or None when it is
    not tested. Tested: one short at `tested_delta` or more while the other is under `target_delta`.
    The new strike is the listed one, in the same expiry, nearest `target_delta`, never further out than
    the old one and never past the tested strike (a strangle may become a straddle, no more). Priced at
    the snapshot's quoted mids, each strike's delta at its own IV. A tested trade with no usable chain,
    strike or credit comes back `blocked`.

    An inverted strangle always ranks below closing or rolling out in time without the inversion (the
    user's rule, 2026-10-19), so a roll that would invert -- every in-expiry roll of a straddle's
    untested side -- is never proposed: it comes back `blocked: "inverts"`, the best such strike kept
    under `inversion` as the last alternative, after the close."""
    put = next(leg for leg in options if leg["right"] == "P")
    call = next(leg for leg in options if leg["right"] == "C")
    if put["delta"] is None or call["delta"] is None or put["mid"] is None or call["mid"] is None:
        return None
    tested, untested = (put, call) if abs(put["delta"]) > abs(call["delta"]) else (call, put)
    if abs(tested["delta"]) < float(cfg["tested_delta"]) or abs(untested["delta"]) >= float(
        cfg["target_delta"]
    ):
        return None
    head = {
        "tested_strike": tested["strike"],
        "tested_right": tested["right"],
        "tested_delta": tested["delta"],
        "from_strike": untested["strike"],
        "right": untested["right"],
        "expiration": untested["expiration"],
    }
    chain = roll_chains.get(f"{underlying.upper()}|{untested['expiration']}")
    if not chain or not chain.get("options"):
        return {**head, "blocked": "no_chain", "error": (chain or {}).get("error")}
    target = float(cfg["target_delta"])
    candidates, inversions = [], []
    for o in chain["options"]:
        mid = _f(o.get("mid"))
        if o.get("right") != untested["right"] or not mid or mid <= 0:
            continue
        k = float(o["strike"])
        toward = k > untested["strike"] if untested["right"] == "P" else k < untested["strike"]
        if not toward:
            continue
        iv = pricing.implied_vol(spot, k, untested["years"], mid, untested["right"], rate)
        if not iv:
            continue
        delta = pricing.delta(spot, k, untested["years"], iv, untested["right"], rate)
        past = k > tested["strike"] if untested["right"] == "P" else k < tested["strike"]
        (inversions if past else candidates).append((abs(abs(delta) - target), k, mid, delta))
    qty = abs(untested["quantity"])
    if not candidates:
        if not inversions:
            return {**head, "blocked": "no_strike"}
        _, k, mid, delta = min(inversions)
        return {
            **head,
            "blocked": "inverts",
            "inversion": {
                "to_strike": k,
                "to_delta": delta,
                "to_mid": mid,
                "credit": mid - untested["mid"],
                "ranked_below": ["close", "roll out in time without inverting"],
            },
        }
    _, k, mid, delta = min(candidates)
    credit = mid - untested["mid"]
    roll = {
        **head,
        "to_strike": k,
        "to_delta": delta,
        "to_mid": mid,
        "credit": credit,
        "net": credit * qty * pricing.MULTIPLIER,
        "legs": [
            f"BTC {qty:g} {untested['expiration']} {untested['strike']:g}{untested['right']} "
            f"@ {untested['mid']:.2f}",
            f"STO {qty:g} {untested['expiration']} {k:g}{untested['right']} @ {mid:.2f}",
        ],
    }
    if credit < float(cfg["min_credit"]):
        return {**roll, "blocked": "credit"}
    return roll


def _fly_off(options: list[dict[str, Any]], spot: float, rate: float) -> dict[str, Any] | None:
    """The trade that makes a broken-wing butterfly a plain one: buy the wing at the narrow side's width
    beyond the body and sell the wide wing. The new wing is priced at an IV read linearly off the body's
    and the old wing's (it lies between them), so its debit is modelled. None when a leg has no IV."""
    low, body, high = sorted(options, key=lambda leg: leg["strike"])
    below, above = body["strike"] - low["strike"], high["strike"] - body["strike"]
    old = high if above > below else low
    new_strike = body["strike"] + below if old is high else body["strike"] - above
    if body["iv"] is None or old["iv"] is None or old["mid"] is None or old["dte"] is None:
        return None
    t = (new_strike - body["strike"]) / (old["strike"] - body["strike"])
    iv = body["iv"] + t * (old["iv"] - body["iv"])
    new_mid = pricing.price(spot, new_strike, old["years"], iv, old["right"], rate)
    qty = old["quantity"]
    per_unit = new_mid - old["mid"]
    exp = old["expiration"]
    return {
        "right": old["right"],
        "old_strike": old["strike"],
        "new_strike": new_strike,
        "new_mid": new_mid,
        "debit": per_unit * qty * pricing.MULTIPLIER,
        "debit_price": per_unit,
        "modelled": True,
        "legs": [
            f"BTO {qty:g} {exp} {new_strike:g}{old['right']} @ ~{new_mid:.2f} (modelled)",
            f"STC {qty:g} {exp} {old['strike']:g}{old['right']} @ {old['mid']:.2f}",
        ],
    }


def _diagonal_width(options: list[dict[str, Any]]) -> float | None:
    """A two-leg diagonal's strike distance in whole-position dollars: one long of one right sold
    against one short of the same size expiring first, the short further out of the money (a PMCC, or
    its put mirror). None for any other diagonal."""
    longs = [leg for leg in options if leg["quantity"] > 0]
    shorts = [leg for leg in options if leg["quantity"] < 0]
    if len(longs) != 1 or len(shorts) != 1:
        return None
    long, short = longs[0], shorts[0]
    if long["right"] != short["right"] or long["quantity"] != -short["quantity"]:
        return None
    if short["expiration"] >= long["expiration"]:
        return None
    gap = short["strike"] - long["strike"] if long["right"] == "C" else long["strike"] - short["strike"]
    return gap * long["quantity"] * pricing.MULTIPLIER if gap > 0 else None


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


def _wheel_check(
    trade: dict[str, Any],
    leg: dict[str, Any],
    roll: dict[str, Any],
    expiration: str,
    session: date,
    net_liq: float | None,
    metrics: dict[str, Any],
    rules: dict[str, Any],
    shares: float,
) -> dict[str, Any]:
    """One wheel roll candidate as check-roll judges it, and the order a person would enter."""
    from cherrypick.manage import rollcheck as _rollcheck  # rollcheck reads this module's helpers

    q = leg["quantity"]
    new = roll["open"]
    text = [
        f"BTC {abs(q):g} {leg['expiration']} {leg['strike']:g}{leg['right']} @ {roll['close_price']:.2f}",
        f"STO {abs(q):g} {expiration} {new['strike']:g}{leg['right']} @ {new['mid']:.2f} "
        f"(quoted {new['bid']:.2f}/{new['ask']:.2f})",
    ]
    out = {**roll, "expiration": expiration, "legs": text, "check": None, "check_error": None}
    if not trade.get("spot"):
        out["check_error"] = "no price for the underlying"
        return out
    try:
        out["check"] = _rollcheck.check(
            trade,
            [
                {
                    **{k: leg[k] for k in ("expiration", "strike", "right")},
                    "quantity": q,
                    "price": roll["close_price"],
                }
            ],
            [
                {
                    "quantity": q,
                    "expiration": expiration,
                    "strike": new["strike"],
                    "right": leg["right"],
                    "price": new["mid"],
                }
            ],
            session=session,
            spot=trade["spot"],
            rules=rules,
            net_liq=net_liq,
            earnings_date=metrics.get("earnings_date"),
            shares=shares,
        )
    except ValueError as exc:
        out["check_error"] = str(exc)
    return out


def _roll_text(kind: str, r: dict[str, Any] | None, why: str | None) -> str:
    if not r:
        return f"{kind}: none ({why or 'not priced'})"
    verdict = (r.get("check") or {}).get("verdict") or f"unchecked ({r.get('check_error')})"
    return f"{kind}: {'; '.join(r['legs'])}, {r['net']:+.2f} a share, check-roll {verdict}"


def _wheel_rules(
    trade: dict[str, Any],
    legs: list[dict[str, Any]],
    ctx: dict[str, Any],
    session: date,
    net_liq: float | None,
    metrics: dict[str, Any],
    rules: dict[str, Any],
    off,
) -> list[dict[str, Any]]:
    """The wheel rules on one trade: buy a short back at its buy-to-close price, roll or close a tested
    short, and name the covered call once an assignment has delivered shares.

    They fire into the trade's own list and are ranked with every other rule by action (PRIORITY), so
    a stop, a breached-strike close or the expiry close still outranks the wheel's roll, and a credit
    roll (roll.naked_put) is still offered in a close's place. A cycle that does not reconcile gets no
    wheel rule at all, only a review saying so: its money and quantities are not to be acted on.
    """
    wr = rules["wheel"]
    by_symbol = {_leg_symbol(leg): leg for leg in legs}
    fired: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for item in ctx["cycles"]:
        cycle = item["cycle"]
        note: dict[str, Any] = {
            "underlying": cycle["underlying"],
            "start": cycle["start"],
            "status": cycle["status"],
        }
        notes.append(note)
        if not cycle["reconciled"]:
            note["problems"] = cycle["problems"]
            fired.append(
                _fire(
                    "wheel_data",
                    REVIEW,
                    None,
                    f"the wheel cycle from {cycle['start']} does not reconcile ("
                    + "; ".join(cycle["problems"][:3])
                    + "): no wheel rule judged; check it by hand",
                )
            )
            continue
        note.update(take_profit=[], rolls=[], next_leg=None)
        taking, tested = [], []
        for row in item["today"]:
            leg = by_symbol.get(row["symbol"])
            if leg is None or leg["quantity"] >= 0:
                continue
            tp = wr["take_profit"]
            price, source = row.get("btc_price"), "the resting buy-to-close"
            if price is None:
                wide = bool(row.get("wide"))
                price = float(tp["btc_price_illiquid"] if wide else tp["btc_price"])
                source = "the default for a wide market" if wide else "the default"
            hit = row["mid"] is not None and row["mid"] <= price + 1e-9
            note["take_profit"].append(
                {"leg": row["leg"], "mid": row["mid"], "btc_price": price, "source": source, "hit": hit}
            )
            if hit and not off("wheel.take_profit"):
                taking.append((leg, row, price, source))
            if row["tested"] and not off("wheel.tested"):
                cands = _wheel.roll_candidates(
                    row,
                    ctx.get("income"),
                    _wheel.roll_out_for(ctx.get("roll_out"), cycle["underlying"], row["right"]),
                )
                shares = cycle["shares"] if row["right"] == "C" else 0.0
                for kind in ("net_zero", "premium_gambit"):
                    if cands.get(kind):
                        cands[kind] = _wheel_check(
                            trade,
                            leg,
                            cands[kind],
                            cands["expiration"],
                            session,
                            net_liq,
                            metrics,
                            rules,
                            shares,
                        )
                note["rolls"].append({"leg": row["leg"], **cands})
                tested.append((leg, row, cands))
        if taking:
            fired.append(
                _fire(
                    "wheel.take_profit",
                    TAKE_PROFIT,
                    wr["take_profit"]["confidence"],
                    "; ".join(
                        f"the {row['leg']} is marked {row['mid']:.2f}, at or under its {price:.2f} "
                        f"buy-to-close ({source}): buy it back, nothing is left to earn"
                        for _, row, price, source in taking
                    ),
                    [leg["label"] for leg, *_ in taking],
                )
            )
            trade.setdefault("leg_orders", {})["wheel.take_profit"] = _order([leg for leg, *_ in taking])
        if tested:
            parts = []
            for _, row, cands in tested:
                parts.append(
                    f"the {row['leg']} is tested ({row['spot']:g} at or through the strike): "
                    "roll out in time or close. "
                    + (
                        f"Rolls to {cands['expiration']} at "
                        + (
                            "this session's quotes"
                            if cands.get("source")
                            else "the previous afternoon's quotes"
                        )
                        + " -- "
                        + _roll_text("net-zero", cands.get("net_zero"), cands.get("net_zero_why"))
                        + "; "
                        + _roll_text(
                            "premium gambit", cands.get("premium_gambit"), cands.get("premium_gambit_why")
                        )
                        if cands.get("expiration")
                        else f"No roll priced: {cands.get('why')}"
                    )
                )
            fired.append(
                _fire(
                    "wheel.tested",
                    ROLL_OR_CLOSE,
                    wr["tested"]["confidence"],
                    " | ".join(parts),
                    [leg["label"] for leg, *_ in tested],
                )
            )
            trade.setdefault("leg_orders", {})["wheel.tested"] = _order([leg for leg, *_ in tested])
        if item["holds_shares"] and cycle["assignments"] >= 1 and not off("wheel.next_leg"):
            written = sum(
                -o["quantity"]
                for o in cycle["open_options"]
                if o["quantity"] < 0 and (_wheel.occ(o["symbol"]) or {}).get("right") == "C"
            )
            free = cycle["shares"] - pricing.MULTIPLIER * written
            if free >= pricing.MULTIPLIER:
                nl = _wheel.next_leg(
                    cycle,
                    ctx.get("bands"),
                    ctx.get("income"),
                    trade["spot"],
                    session,
                    str(wr["next_leg"]["band"]),
                )
                nl["contracts"] = int(free // pricing.MULTIPLIER)
                note["next_leg"] = nl
                if nl["strike"] is not None:
                    q = nl["quote"]
                    what = (
                        f"sell {nl['contracts']} {nl['expiration']} {nl['strike']:g}C, the first quoted "
                        f"strike at or above the {nl['band_kind']} call band ({nl['band']:.2f}), "
                        f"{q['mid']:.2f} at the previous afternoon's mid"
                    )
                elif nl["band"] is not None:
                    what = (
                        f"sell {nl['contracts']} {nl['expiration']} call(s) at the first listed strike at or "
                        f"above the {nl['band_kind']} call band ({nl['band']:.2f}); "
                        "no quote for that expiry in the snapshot"
                    )
                else:
                    what = f"the covered call's strike comes from the skate call band: {nl['why']}"
                if nl.get("below_wheel_basis"):
                    what += (
                        f"; it sits under the {cycle['wheel_basis']:.2f} wheel basis, "
                        "so a call-away there loses"
                    )
                fired.append(
                    _fire(
                        "wheel.next_leg",
                        REVIEW,
                        wr["next_leg"]["confidence"],
                        f"{free:g} shares delivered by assignment are not written against: {what}",
                    )
                )
    trade["wheel"] = notes
    return fired


def _pmcc_rules(
    trade: dict[str, Any],
    legs: list[dict[str, Any]],
    ctx: dict[str, Any],
    session: date,
    net_liq: float | None,
    metrics: dict[str, Any],
    rules: dict[str, Any],
    off,
) -> list[dict[str, Any]]:
    """The PMCC rules on the trade holding a recognised PMCC's short: roll or close it when the width
    check fails or the short is exposed to early assignment (with the short's roll candidates, each
    judged by check-roll against the long and the short together), and review a roll of the long.

    Ranked with every other rule by action (PRIORITY): a stop or the expiry close outranks them, and
    the diagonal's credit roll (roll.diagonal_short), when it fires, is offered in their place as it
    is for any close. An unrecognised candidate gets nothing (its diagonal rules stand), and a PMCC
    whose orders do not reconcile gets only a review saying so.
    """
    cfg = rules["pmcc"]
    fired: list[dict[str, Any]] = []
    mine = {_leg_symbol(leg): leg for leg in legs}
    for view in ctx["views"]:
        # The trade carries the verdict's inputs; the whole entry is the account's `pmcc` view.
        trade["pmcc"] = {
            "underlying": view["underlying"],
            "long": view["long"]["leg"],
            "short": view["short"]["leg"],
            "recognized": view["recognized"],
            "recognized_by": view["recognized_by"],
            "reconciled": view["reconciled"],
            "width_passes": view["width"]["passes"],
            "pnl_if_assigned": view["width"]["pnl_if_assigned"],
            "assignment_exposed": view["short"]["assignment"]["exposed"],
            "roll_long": view["long"]["roll"],
        }
        if not view["recognized"]:
            continue
        if not view["reconciled"]:
            fired.append(
                _fire(
                    "pmcc_data",
                    REVIEW,
                    None,
                    "the PMCC's orders do not reconcile ("
                    + "; ".join(view["problems"][:3])
                    + "): no PMCC rule judged; check it by hand",
                )
            )
            continue
        long, short, width = view["long"], view["short"], view["width"]
        short_leg = mine.get(short["symbol"])
        both = [leg for leg in (mine.get(long["symbol"]), short_leg) if leg is not None]
        rolls = view.get("rolls") or {}
        if rolls.get("candidates"):
            # check-roll judges the long and the short together, wherever the evaluation grouped them.
            pair = _legs(
                [ctx["raw"][long["symbol"]], ctx["raw"][short["symbol"]]],
                session,
                view["spot"],
                float(rules["pricing"]["rate"]),
            )
            whole = {"legs": pair, "cost": width["net_cost"], "shape": "diagonal", "spot": view["spot"]}
            rolls["candidates"] = [
                _wheel_check(whole, pair[1], c, c["expiration"], session, net_liq, metrics, rules, 0.0)
                for c in rolls["candidates"]
            ]
        rolls_text = (
            "Rolls at the snapshot's quotes -- "
            + "; ".join(
                f"{c['expiration']} {c['open']['strike']:g}C at {c['delta']:.2f} delta, "
                f"{c['net']:+.2f} a share, check-roll {(c.get('check') or {}).get('verdict') or 'unchecked'}"
                for c in rolls["candidates"]
            )
            if rolls.get("candidates")
            else f"No roll priced: {rolls.get('why') or 'not needed'}"
        )
        if width["passes"] is False and not off("pmcc.width"):
            fired.append(
                _fire(
                    "pmcc.width",
                    ROLL_OR_CLOSE,
                    cfg["width"]["confidence"],
                    f"the {short['leg']} is {width['width']:g} over the {long['leg']}, but the long has cost "
                    f"{width['net_cost_per_share']:.2f} a share net of every premium: an assignment there "
                    f"loses ${-width['pnl_if_assigned']:,.0f}. Roll the short up and out, or close. "
                    + rolls_text,
                    [leg["label"] for leg in both],
                )
            )
            if both:
                trade.setdefault("leg_orders", {})["pmcc.width"] = _order(both)
        exposure = short["assignment"]
        if exposure["exposed"] and not off("pmcc.assignment"):
            fired.append(
                _fire(
                    "pmcc.assignment",
                    ROLL_OR_CLOSE,
                    cfg["assignment"]["confidence"],
                    f"the {short['leg']} is {exposure['why']}: early assignment is likely. Roll it out "
                    f"(or up and out), or buy it back. {rolls_text}",
                    [short_leg["label"]] if short_leg else None,
                )
            )
            if short_leg:
                trade.setdefault("leg_orders", {})["pmcc.assignment"] = _order([short_leg])
        if long["roll"] and not off("pmcc.roll_long"):
            fired.append(
                _fire(
                    "pmcc.roll_long",
                    REVIEW,
                    cfg["roll_long"]["confidence"],
                    f"the long {long['leg']} is at "
                    + " and ".join(long["roll"])
                    + ": review rolling it to a "
                    f"later expiry at a deeper strike (the method enters at "
                    f"{float(cfg['recognize']['entry_min_delta']):.2f} delta or more)",
                )
            )
    return fired


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
        # A roll's fallback is the close it replaces: the whole trade, or the legs that rule named.
        trade["order"] = (trade.get("leg_orders") or {}).get(chosen["rule"]) or _order(legs)
    elif chosen["action"] == CLOSE_LEGS:
        named = set(chosen["legs"] or [])
        trade["order"] = (trade.get("leg_orders") or {}).get(chosen["rule"]) or _order(
            [leg for leg in legs if _label(leg) in named]
        )
    else:
        trade["order"] = None
    trade.pop("leg_orders", None)
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


def _wheel_view(
    account: dict[str, Any],
    n_accounts: int,
    history: dict[str, Any],
    history_ok: bool,
    session: date,
    metrics: dict[str, Any],
    snapshot: dict[str, Any],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """The account's wheel cycles (`wheel.py`), each naming the evaluated trades that hold its legs.

    The trade history and the day's orders are the broker tool's designated account's, so only that
    account (or the only one) gets a view; the others say why they have none.
    """
    if not (account.get("designated") or n_accounts == 1):
        return {"cycles": [], "error": "the trade history is the designated account's"}
    if not history_ok:
        return {"cycles": [], "error": "no trade history in this snapshot"}
    # The view reads history rows this account has never produced (no assignment has been seen on it),
    # so a layout it does not expect is a missing view, never a missing evaluation: every trade's
    # action is judged without it.
    try:
        view = _wheel.build(
            account.get("underlyings") or [],
            history,
            session,
            metrics,
            snapshot.get("working_orders"),
            snapshot.get("skate_bands"),
        )
    except Exception as exc:  # noqa: BLE001 -- the wheel view is a reading; the evaluation is the product
        return {"cycles": [], "error": f"wheel view failed: {type(exc).__name__}: {exc}"[:300]}
    view["error"] = None
    _link_wheel(view, trades)
    return view


def _wheel_symbols(cycle: dict[str, Any]) -> list[str]:
    """The position symbols a running cycle holds: its open options, and its shares by ticker."""
    if cycle.get("end") is not None:
        return []
    return [o["symbol"] for o in cycle["open_options"]] + ([cycle["underlying"]] if cycle["shares"] else [])


def _leg_symbol(leg: dict[str, Any]) -> str:
    sym = " ".join(str(leg.get("symbol") or "").split())
    return sym if leg.get("right") else sym.upper()


def _link_wheel(view: dict[str, Any], trades: list[dict[str, Any]]) -> None:
    """Name, on each cycle, the evaluated trades that hold its legs."""
    held_by: dict[str, set[str]] = {}
    for t in trades:
        for leg in t["legs"]:
            held_by.setdefault(_leg_symbol(leg), set()).add(t.get("trade_id"))
    for cycle in view.get("cycles") or []:
        cycle["trade_ids"] = sorted({tid for s in _wheel_symbols(cycle) for tid in held_by.get(s, ()) if tid})


def _wheel_context(
    view: dict[str, Any] | None, group: list[dict[str, Any]], snapshot: dict[str, Any], underlying: str
) -> dict[str, Any] | None:
    """What the wheel rules need for one trade: each running cycle holding any of its legs, the
    cycle's day-check rows for the shorts in this trade, and whether the trade holds its shares."""
    if not view or not view.get("cycles"):
        return None
    mine = {_leg_symbol(leg) for leg in group}
    found = []
    for cycle in view["cycles"]:
        held = set(_wheel_symbols(cycle)) & mine
        if not held:
            continue
        found.append(
            {
                "cycle": cycle,
                "today": [row for row in cycle.get("today") or [] if row["symbol"] in held],
                "holds_shares": cycle["underlying"] in held,
            }
        )
    if not found:
        return None
    income = snapshot.get("income") or {}
    entry = (income.get("symbols") or {}).get(underlying) if not income.get("error") else None
    return {
        "cycles": found,
        "income": {**entry, "taken_at": income.get("taken_at")} if entry else None,
        "bands": snapshot.get("skate_bands"),
        "roll_out": snapshot.get("roll_out_chains"),
    }


def _alone(groups: list[tuple[list[dict[str, Any]], Any]], symbol: str) -> int | None:
    """The index of the group holding exactly `symbol` and nothing else, or None."""
    return next((i for i, (g, _) in enumerate(groups) if {_leg_symbol(leg) for leg in g} == {symbol}), None)


def _join_pmcc(
    groups: list[tuple[list[dict[str, Any]], dict[str, Any] | None]],
    view: dict[str, Any] | None,
    underlying: str,
    history: list[dict[str, Any]] | None,
    rules: dict[str, Any],
) -> tuple[list[tuple[list[dict[str, Any]], dict[str, Any] | None]], set[int]]:
    """Join a long call and the short call it covers into one trade when the order chains keep them
    apart, and return which groups were joined.

    A short call sold in an order of its own (after the last one expired) is a chain of its own, so
    `group_trades` makes it a naked call beside a lone long call. The rule is the narrowest that
    mends that: the two are joined only when the PMCC view has linked them and they RECONCILE (the
    view's shape: one long, one short of the same size at or above its strike within 60 days, no
    shares; its orders replaying to exactly those two legs), recognised as a PMCC or not, since
    coverage is a fact about the legs and not about intent; and only when each sits alone in its own
    group, so no other trade's legs move. The joined trade's basis is every order the view linked,
    the expired shorts' premiums included, as the view's own net cost counts them. Anything else (a
    short further out, an unreconciled link, a group holding other legs) keeps the chains' grouping.
    """
    if rules.get("trade_grouping") != "order_chain" or not view or view.get("error") or not history:
        return groups, set()
    joined: set[int] = set()
    for entry in view.get("positions") or []:
        if entry["underlying"].upper() != underlying.upper() or not entry["reconciled"]:
            continue
        long_sym, short_sym = entry["long"]["symbol"], entry["short"]["symbol"]
        gl, gs = _alone(groups, long_sym), _alone(groups, short_sym)
        if gl is None or gs is None or gl == gs:
            continue
        chain = _chains.from_orders(entry["orders"], history, [long_sym, short_sym])
        chain["grouped_by"] = "pmcc link"
        merged = (groups[gl][0] + groups[gs][0], chain)
        keep, drop = min(gl, gs), max(gl, gs)
        groups = [merged if i == keep else g for i, g in enumerate(groups) if i != drop]
        joined = {i if i < drop else i - 1 for i in joined} | {keep}
        entry["grouping"] = "joined: the long and its short were two order chains"
    return groups, joined


def _join_covered(
    groups: list[tuple[list[dict[str, Any]], dict[str, Any] | None]],
    history: list[dict[str, Any]] | None,
    rules: dict[str, Any],
) -> list[tuple[list[dict[str, Any]], dict[str, Any] | None]]:
    """Join a short call that a long call on the name covers into one trade with it, whatever the
    short's days left: the case the PMCC link (`_join_pmcc`, 60 days at most) leaves split, a short
    sold in an order of its own against a long call it is not linked to.

    The rule, as narrow as the PMCC link's: on `order_chain` grouping, among the name's groups, there
    is exactly one holding a single long call and nothing else and exactly one holding a single short
    call and nothing else; both are reconciled order chains (a fallback group, priced from its open
    legs, never moves); the long expires on or after the short, at or below its strike, in at least its
    quantity; and the name holds no shares (shares covering a call are the wheel's). Then the two are
    one trade (`grouped_by: "covered call"`), its basis the two chains' orders together. Unlike the
    PMCC link, the shorts that expired earlier in orders of their own are not in that basis: nothing
    linked them. Anything ambiguous (two lone longs or shorts that could pair), uncovered or partly
    covered keeps the chains' grouping.
    """
    if rules.get("trade_grouping") != "order_chain" or not history:
        return groups
    if any(leg.get("right") not in ("C", "P") for g, _ in groups for leg in g):
        return groups

    def lone(sign: int) -> list[int]:
        return [
            i
            for i, (g, chain) in enumerate(groups)
            if len(g) == 1
            and g[0].get("right") == "C"
            and (_f(g[0].get("quantity")) or 0.0) * sign > 0
            and chain is not None
            and chain.get("reconciled")
        ]

    longs, shorts = lone(1), lone(-1)
    if len(longs) != 1 or len(shorts) != 1:
        return groups
    (gl,), (gs,) = longs, shorts
    long, short = groups[gl][0][0], groups[gs][0][0]
    lk, sk = _f(long.get("strike")), _f(short.get("strike"))
    le, se = str(long.get("expiration") or ""), str(short.get("expiration") or "")
    covers = (
        lk is not None
        and sk is not None
        and le
        and se
        and le >= se
        and lk <= sk
        and _f(long.get("quantity")) >= -_f(short.get("quantity"))
    )
    if not covers:
        return groups
    orders = [o["order_id"] for _, c in (groups[gl], groups[gs]) for o in c["orders"]]
    chain = _chains.from_orders(orders, history, [_leg_symbol(long), _leg_symbol(short)])
    chain["grouped_by"] = "covered call"
    keep, drop = min(gl, gs), max(gl, gs)
    merged = (groups[gl][0] + groups[gs][0], chain)
    return [merged if i == keep else g for i, g in enumerate(groups) if i != drop]


def _pmcc_view(
    account: dict[str, Any],
    n_accounts: int,
    history: dict[str, Any],
    history_ok: bool,
    session: date,
    metrics: dict[str, Any],
    snapshot: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    """The account's PMCCs (`pmcc.py`), recognised or not. Like the wheel view, only the designated
    account (or the only one) has the history to link them, and a failure costs the view: every trade
    is still judged, by its diagonal rules alone."""
    if not (account.get("designated") or n_accounts == 1):
        return {"positions": [], "error": "the trade history is the designated account's"}
    if not history_ok:
        return {"positions": [], "error": "no trade history in this snapshot"}
    try:
        view = _pmcc.build(account.get("underlyings") or [], history, session, metrics, snapshot, rules)
    except Exception as exc:  # noqa: BLE001 -- the PMCC view is a reading; the evaluation is the product
        return {"positions": [], "error": f"PMCC view failed: {type(exc).__name__}: {exc}"[:300]}
    view["error"] = None
    return view


def _pmcc_context(view: dict[str, Any] | None, group: list[dict[str, Any]], account: dict[str, Any]):
    """The PMCC entries whose short this trade holds, and the raw legs check-roll prices them from."""
    mine = {_leg_symbol(leg) for leg in group}
    found = [v for v in (view or {}).get("positions") or [] if v["short"]["symbol"] in mine]
    if not found:
        return None
    raw = {
        _leg_symbol(leg): leg
        for u in account.get("underlyings") or []
        for leg in u.get("legs") or []
        if leg.get("right") in ("C", "P")
    }
    return {"views": found, "raw": raw}


def _link_pmcc(view: dict[str, Any], trades: list[dict[str, Any]]) -> None:
    """Name, on each PMCC, the evaluated trades holding its long and its short: one trade, unless the
    link did not reconcile and the chains kept them apart."""
    held_by: dict[str, set[str]] = {}
    for t in trades:
        for leg in t["legs"]:
            held_by.setdefault(_leg_symbol(leg), set()).add(t.get("trade_id"))
    for v in view.get("positions") or []:
        v["trade_ids"] = sorted(
            {tid for s in (v["long"]["symbol"], v["short"]["symbol"]) for tid in held_by.get(s, ()) if tid}
        )


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
        grouping_errors: list[str] = []
        # The wheel view first: its cycles are a pure reading of the history, and the wheel rules judge
        # each trade holding a cycle's legs alongside every other rule.
        wheel_view = _wheel_view(
            account,
            len(positions.get("accounts") or []),
            history,
            history_ok,
            session,
            metrics,
            snapshot,
            trades,
        )
        pmcc_view = _pmcc_view(
            account,
            len(positions.get("accounts") or []),
            history,
            history_ok,
            session,
            metrics,
            snapshot,
            rules,
        )
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
            try:
                groups, _ = _join_pmcc(
                    groups, pmcc_view, u["underlying"], history.get(u["underlying"]), rules
                )
            except Exception as exc:  # noqa: BLE001 -- a failed join leaves the chains' own grouping
                pmcc_view["grouping_error"] = f"PMCC grouping failed: {type(exc).__name__}: {exc}"[:300]
            try:
                groups = _join_covered(groups, history.get(u["underlying"]), rules)
            except Exception as exc:  # noqa: BLE001 -- a failed join leaves the chains' own grouping
                grouping_errors.append(
                    f"{u['underlying']}: covered-call grouping failed: {type(exc).__name__}: {exc}"[:300]
                )
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
                    snapshot.get("roll_chains"),
                    _wheel_context(wheel_view, group, snapshot, u["underlying"]),
                    _pmcc_context(pmcc_view, group, account),
                )
                trade["trade_id"] = u["underlying"] if len(groups) == 1 else f"{u['underlying']}#{i + 1}"
                # How the legs became one trade: the broker's chains, unless a join made two into one.
                trade["grouped_by"] = (chain or {}).get("grouped_by")
                trades.append(trade)
        trades.sort(key=lambda t: (PRIORITY.get(t["recommendation"]["action"], 9), -abs(t["pnl_gross"] or 0)))
        counts: dict[str, int] = {}
        for t in trades:
            counts[t["recommendation"]["action"]] = counts.get(t["recommendation"]["action"], 0) + 1
        book = _portfolio.reading(
            trades,
            metrics,
            snapshot.get("benchmark"),
            balances,
            account.get("account_type"),
            rules,
            snapshot.get("broker_greeks"),
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
            # The broker tool's day list is for its designated account, the one manage evaluates.
            snapshot.get("working_orders"),
        )
        _link_wheel(wheel_view, trades)
        _link_pmcc(pmcc_view, trades)
        accounts.append(
            {
                "account": account.get("account"),
                "wheel": wheel_view,
                "pmcc": pmcc_view,
                "grouping_errors": grouping_errors,
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
