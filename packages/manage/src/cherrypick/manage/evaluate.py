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

from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules

EVAL_VERSION = 1

CLOSE = "CLOSE"
TAKE_PROFIT = "TAKE_PROFIT"
ROLL_OR_CLOSE = "ROLL_OR_CLOSE"
CLOSE_LEGS = "CLOSE_LEGS"
REVIEW = "REVIEW"
SETTLES = "SETTLES"
HOLD = "HOLD"

PRIORITY = {CLOSE: 1, TAKE_PROFIT: 2, ROLL_OR_CLOSE: 3, CLOSE_LEGS: 4, REVIEW: 5, SETTLES: 6, HOLD: 7}
# Which way an action points. Two fired rules pointing different ways are a conflict.
DIRECTION = {CLOSE: "reduce", TAKE_PROFIT: "reduce", CLOSE_LEGS: "reduce", ROLL_OR_CLOSE: "roll"}


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
) -> dict[str, Any]:
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

    cost = sum(leg["cost"] for leg in legs)
    value = sum(leg["value"] for leg in legs)
    pnl = value - cost
    kind = "credit" if cost < 0 else "debit"
    basis = abs(cost)
    trade.update(
        kind=kind,
        cost=cost,
        value=value,
        pnl_gross=pnl,
        capture=(pnl / basis) if basis > 0 else None,
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
            downside_from_now=profile["min_value"] - value,
            upside_from_now=profile["max_value"] - value,
        )
        trade["profile"] = profile

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

    # ---- credit trades
    if kind == "credit":
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
            trade["exit_levels"]["time_exit"] = {
                "date": (_date(short_expiries[0]) - timedelta(days=int(te["dte"]))).isoformat()
            }
            if not off("credit.time_exit") and 0 <= trade["nearest_short_dte"] <= int(te["dte"]):
                winner = pnl > 0
                fired.append(
                    _fire(
                        "credit.time_exit",
                        ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                        te["confidence"],
                        f"nearest short is {trade['nearest_short_dte']} DTE (exit at {te['dte']}); "
                        + (
                            "profitable, so it may roll out to ~45 DTE"
                            if winner
                            else "losing, so close; never roll a loser"
                        ),
                    )
                )
        br = cr["breached"]
        if not off("credit.breached") and profile and spot:
            itm = [leg for leg in shorts if (leg["intrinsic"] or 0) > 0]
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
            trade["exit_levels"]["time_exit"] = {"date": (anchor - timedelta(days=limit)).isoformat()}
        if not off("debit.time_exit") and when is not None and 0 <= when <= limit:
            winner = pnl > 0
            fired.append(
                _fire(
                    "debit.time_exit",
                    ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                    te["confidence"],
                    f"{'nearest short' if shorts else 'nearest leg'} is {when} DTE (exit at {limit}); "
                    + (
                        "profitable, so the shorts may roll out"
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

    trade["fired"] = fired
    return _decide(trade, rules, legs)


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


def _decide(trade: dict[str, Any], rules: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    _price_levels(trade, legs)
    fired = trade["fired"]
    if not fired:
        hold = rules["hold"]
        confidence = int(hold["confidence"])
        reason = "no rule fired; hold with the exit levels resting"
        if trade.get("earnings") and trade["earnings"]["before_nearest_short_expiry"]:
            confidence -= int(hold["earnings_penalty"])
            reason += f"; earnings {trade['earnings']['date']} fall inside the trade"
        trade["recommendation"] = {
            "action": HOLD,
            "confidence": max(0, confidence),
            "rule": "hold",
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
    }
    if chosen["action"] in (CLOSE, TAKE_PROFIT, ROLL_OR_CLOSE):
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


# --------------------------------------------------------------------------- the whole snapshot
def evaluate(
    snapshot: dict[str, Any], rules: dict[str, Any], warnings: list[str] | None = None
) -> dict[str, Any]:
    session = _date(snapshot.get("session"))
    if session is None:
        raise ValueError("snapshot has no session date")
    positions = snapshot.get("positions") or {}
    metrics = snapshot.get("metrics") or {}
    accounts = []
    for account in positions.get("accounts") or []:
        balances = account.get("balances") or {}
        net_liq = _f(balances.get("net_liquidating_value"))
        trades = []
        for u in account.get("underlyings") or []:
            groups = split_trades(u.get("legs") or [], rules.get("trade_grouping"))
            for i, group in enumerate(groups):
                trade = evaluate_trade(
                    u["underlying"], group, metrics.get(u["underlying"]), session, net_liq, rules
                )
                trade["trade_id"] = u["underlying"] if len(groups) == 1 else f"{u['underlying']}#{i + 1}"
                trades.append(trade)
        trades.sort(key=lambda t: (PRIORITY.get(t["recommendation"]["action"], 9), -abs(t["pnl_gross"] or 0)))
        counts: dict[str, int] = {}
        for t in trades:
            counts[t["recommendation"]["action"]] = counts.get(t["recommendation"]["action"], 0) + 1
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
                "trades": trades,
            }
        )
    return {
        "eval_version": EVAL_VERSION,
        "session": session.isoformat(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "ok": bool(positions.get("ok")),
        "error": positions.get("error"),
        "metrics_error": snapshot.get("metrics_error"),
        "marks": positions.get("marks"),
        "ruleset": rules.get("ruleset"),
        "rules": rules,
        "rule_warnings": list(warnings or []),
        "accounts": accounts,
    }
