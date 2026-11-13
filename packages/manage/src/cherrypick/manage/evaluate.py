"""Evaluate every position in a broker snapshot against the management ruleset.

A pure function: snapshot + rules in, evaluation out. No broker, network, clock or file is touched
here, so an evaluation can be re-run over last month's snapshots with a changed ruleset and give the
same answer twice.

Each underlying is one trade. Its net open cost decides what kind of trade it is (a net credit is a
credit trade, a net debit a debit trade), and each rule that fires names an action. The trade's
recommendation is the most decisive action fired (CLOSE before TAKE_PROFIT before ROLL_OR_CLOSE
before CLOSE_LEGS before REVIEW), ties broken by a fixed rule order, with that rule's evidence grade
and the rules that fired pointing the other way named beside it. A trade where nothing fired is a
HOLD, and carries the levels at which the rules would fire.

Money follows the suite layout: signed cash flow (a credit received is +), whole-position dollars, and
every P&L here is a mark P&L GROSS of fees -- the broker snapshot carries no fee record.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from functools import reduce
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as _calendar

from cherrypick.manage import attention as _attention
from cherrypick.manage import candidates as _candidates
from cherrypick.manage import chains as _chains
from cherrypick.manage import deltaideas as _deltaideas
from cherrypick.manage import explain as _explain
from cherrypick.manage import hedge as _hedge
from cherrypick.manage import ledger as _ledger
from cherrypick.manage import limits as _limits
from cherrypick.manage import payoff as _payoff
from cherrypick.manage import pmcc as _pmcc
from cherrypick.manage import portfolio as _portfolio
from cherrypick.manage import premium as _premium
from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules
from cherrypick.manage import shapes as _shapes
from cherrypick.manage import timespreads as _timespreads
from cherrypick.manage import tradeids as _tradeids
from cherrypick.manage import wheel as _wheel
from cherrypick.manage import zerodte as _zerodte

EVAL_VERSION = 1
NEW_YORK = ZoneInfo("America/New_York")

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
# Shorts sold against a later long: the shapes whose short can be written week by week.
TIME_SPREADS = {"diagonal", "calendar"}


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


def _band_text(low: float | None, high: float | None) -> str:
    """'340.12 / 405.30', with 'none' for a side the trade never loses that much on."""
    return " / ".join(f"{x:,.2f}" if x is not None else "none" for x in (low, high))


def _fire(rule: str, action: str, evidence: Any, reason: str, legs: list[str] | None = None) -> dict:
    return {"rule": rule, "action": action, "evidence": evidence, "reason": reason, "legs": legs}


# --------------------------------------------------------------------------- leg facts
def _broker_iv(broker_greeks: dict[str, dict] | None, symbol: Any) -> float | None:
    iv = _f(((broker_greeks or {}).get(str(symbol or "")) or {}).get("iv"))
    return iv if iv and iv > 0 else None


# `implied_vol`'s lower bound, and the extrinsic under which a mid at that bound is a real zero-vol
# reading (an in-the-money leg worth its intrinsic) rather than a mid the model cannot reach.
_IV_FLOOR = 0.0002
_NO_EXTRINSIC = 0.01


def _reprices(
    leg: dict[str, Any],
    spot: float,
    strike: float,
    years: float,
    iv: float,
    right: str,
    rate: float,
    div: float = 0.0,
) -> bool:
    """Whether the broker's IV, priced in our model at the snapshot's spot, lands inside the leg's own
    quote. The broker computes its greeks on its own underlying price, which after the close on an
    earnings night is the after-hours print: META's 705C (2026-10-21, 16:35 ET) took an IV that priced
    it at $10.40 against a 21.80/24.40 quote, and beside its partner solved from a mid it gave the
    bull call spread a negative delta. An IV that cannot price the quote at our spot is not used."""
    bid, ask = _f(leg.get("bid")), _f(leg.get("ask"))
    if bid is None or ask is None or ask < bid:
        return False
    model = pricing.model_price(spot, strike, years, iv, right, rate, div)
    return model is not None and bid - 1e-9 <= model <= ask + 1e-9


def _legs(
    raw: list[dict[str, Any]],
    session: date,
    spot: float | None,
    rate: float,
    broker_greeks: dict[str, dict] | None = None,
    div_yield: float | None = None,
) -> list[dict[str, Any]]:
    """Each leg's facts. Its IV is solved from its own mid at the name's dividend yield (`div_yield`,
    None when the snapshot has no dividend record for the name: priced at 0 and `div_source` says
    so), unless the snapshot flags the quote `wide`
    and the broker has an IV for it: a wide mid is the midpoint of a market nobody quoted seriously
    (before the open, a 0.00/1.05 call), and the IVs solved off two such mids in one spread disagreed
    about twice as much as the broker's surface IVs did (median 0.030 against 0.014 over the wide
    pairs of 2026-10-20/21; on tight quotes ours agreed better, 0.005 against 0.010). The broker's IV
    is taken only when it prices the leg inside its quote at our spot (`_reprices`). `iv_source` says
    which: `mid`, `broker`, or `unpriceable` (no vol prices the mid; the leg is left unmodelled)."""
    q = float(div_yield or 0.0)
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
            # The broker's previous close, so a surface can mark the day's P&L live (2026-11-04).
            "prev_close": _f(leg.get("prev_close")),
            "value": value,
            "cost": (value - open_pl) if value is not None and open_pl is not None else None,
            "open_pl": open_pl,
            "priced": bool(leg.get("priced")) and value is not None,
            "years": max(dte, 0) / 365.0 if dte is not None else 0.0,
            "iv": None,
            "iv_source": None,
            # The yield the leg is modelled at, and where it came from: `history` (the snapshot's
            # dividends gone ex in the year) or `none` (no record, priced at 0).
            "div_yield": q if right else None,
            "div_source": ("history" if div_yield is not None else "none") if right else None,
            "wide": bool(leg.get("wide")),
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
                broker_iv = _broker_iv(broker_greeks, leg.get("symbol")) if item["wide"] else None
                if broker_iv is not None and not _reprices(
                    leg, spot, strike, item["years"], broker_iv, right, rate, q
                ):
                    broker_iv = None
                if broker_iv is not None:
                    item["iv"], item["iv_source"] = broker_iv, "broker"
                else:
                    item["iv"] = pricing.implied_vol(spot, strike, item["years"], mid, right, rate, q)
                    item["iv_source"] = "mid" if item["iv"] else None
                    if item["iv"] and item["iv"] <= _IV_FLOOR and item["extrinsic"] > _NO_EXTRINSIC:
                        # The solver's floor, reached by a mid that still carries extrinsic: the mid
                        # sits under what the model calls the least this option can be worth. On a
                        # high-yield name with no dividend record that is the missing dividend (a
                        # long-dated call's mid under spot less the discounted strike, read as delta
                        # 1.00 against the broker's 0.55), not a vol of zero, so the leg is left
                        # unmodelled rather than given one.
                        item["iv"], item["iv_source"] = None, "unpriceable"
                if item["iv"]:
                    item["delta"] = pricing.delta(spot, strike, item["years"], item["iv"], right, rate, q)
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
    broker_greeks: dict[str, dict] | None = None,
    trade_id: str | None = None,
    roll_out: dict[str, Any] | None = None,
    div_yield: float | None = None,
    covered: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`trade_id` is the trade's stable ID (tradeids.py): its own overrides (`trades.<ID>`: intent, note,
    disable) are read over the name's (`positions.<SYM>`).

    `covered` is the shares covering this trade's short calls (`_share_cover`), when it is nothing but
    short calls the name's shares cover: a covered call, judged as one (COVERED_SKIPS do not apply).

    `chain`, when the broker history reconciled one, sets the basis: the chain's net cash across its
    opening order and every roll, not the open legs' average prices. `seen` is the earnings trades the
    previous evaluation found (`earnings_trade`). `roll_chains` is the snapshot's quoted chains, keyed
    "<UNDERLYING>|<expiry>", that a strangle's untested side is rolled along. `wheel_ctx` is the wheel
    cycles holding this trade's legs (`_wheel_context`), which the wheel rules read; `pmcc_ctx` the PMCC
    view entries whose short this trade holds (`_pmcc_context`), which the PMCC rules read.
    `broker_greeks` is the snapshot's per-leg broker greeks, whose IV a leg with a wide quote takes.
    `roll_out` is the snapshot's roll-out chains (v14), which a weekly short's roll is priced on.
    `div_yield` is the name's dividend yield (`_pmcc._yield`), which every leg is modelled at."""
    metrics = metrics or {}
    spot = _f(metrics.get("last"))
    pr = rules["pricing"]
    rate = float(pr["rate"])
    legs = _legs(raw_legs, session, spot, rate, broker_greeks, div_yield)
    override = _rules.override(rules, underlying, trade_id)
    is_covered = bool(covered and legs and all(leg["right"] == "C" and leg["quantity"] < 0 for leg in legs))
    # A short put on a name the operator wheels (intent "wheel", the name's or the trade's own), held
    # by a cycle that reconciles: assignment is the wheel's next step, so the stop and the breached
    # close give way to wheel.tested's roll, close or take the assignment (user, 2026-11-12: opt in
    # per name, so a put the operator would not take shares on keeps them).
    is_wheel_put = bool(
        override.get("intent") == "wheel"
        and legs
        and all(leg["right"] == "P" and leg["quantity"] < 0 for leg in legs)
        and any(item["cycle"]["reconciled"] for item in (wheel_ctx or {}).get("cycles") or [])
    )
    skips = COVERED_SKIPS if is_covered else WHEEL_PUT_SKIPS if is_wheel_put else frozenset()

    def off(rule_id: str) -> bool:
        return rule_id in skips or _rules.disabled(rules, underlying, rule_id, trade_id)

    trade: dict[str, Any] = {
        "trade_id": trade_id,
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
        "time_exit_skipped": None,
        "expiry_close_due": None,
        "wheel_put": is_wheel_put,
        "covered": (
            {
                "shares": covered["shares"],
                "share_cost": covered["share_cost"],
                "basis": None,
                "basis_source": None,
            }
            if is_covered
            else None
        ),
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
    # A leg that outlives the horizon with no IV would be valued there at intrinsic: no profile then.
    if (
        spot
        and expiries
        and _date(expiries[0]) >= session
        and not pricing.outliving_unpriced(options, expiries[0])
    ):
        horizon = max((_date(expiries[0]) - session).days, 0) / 365.0
        profile = pricing.payoff_profile(legs, spot, horizon, pr)
        # Whole-position dollars to the cent: summing Black-Scholes legs leaves float noise
        # (-300.00000000000364 for a $300 loss), which every reader would otherwise round itself.
        profile.update(
            horizon=expiries[0],
            max_loss_from_open=round(profile["min_value"] - cost, 2),
            max_gain_from_open=round(profile["max_value"] - cost, 2),
            downside_from_now=round(profile["min_value"] - value, 2),
            upside_from_now=round(profile["max_value"] - value, 2),
        )
        trade["profile"] = profile

    trade["shape"] = "covered call" if is_covered else _shapes.classify(options, cost)
    # A diagonal or calendar whose short was sold close to its expiry is managed on the long's clock.
    short_term = (
        _short_term_short(options, chain, rules["debit"]["time_exit"])
        if trade["shape"] in TIME_SPREADS
        else None
    )
    trade["short_term_short"] = short_term
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
                    ex["evidence"],
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
                    ex["evidence"],
                    "physically-settled legs past expiry are still listed: check for assignment or exercise",
                    [_label(leg) for leg in stale],
                )
            )
        if expiring and is_covered:
            # A covered call is held into expiry: an assignment, even after the bell, delivers shares the
            # account already holds, and the pin risk the close guards against is a question of whether
            # the shares go, never of a position the account cannot meet.
            trade["covered"]["held_into_expiry"] = True
            expiring = []
        if expiring:
            expiring_on = min(_date(leg["expiration"]) for leg in expiring)
            due = _expiry_close_due(trade, chain, expiring, expiring_on, session, ex)
            # Legs opened inside the window were put on to be held into it: closing them at the first
            # evaluation guards nothing. What the close guards against (pin and post-close exercise)
            # comes at the expiry day's close, so that day it is due; until then it waits.
            expiring = [] if due else expiring
        if expiring:
            whole = len(expiring) == len(options)
            soonest = min(leg["dte"] for leg in expiring)
            expiry_close = _fire(
                "expiry",
                CLOSE if whole else CLOSE_LEGS,
                ex["evidence"],
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
                    dr["take_profit"]["evidence"],
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
                    dr["stop"]["evidence"],
                    f"down ${-pnl:,.0f}, {-pnl / max_loss:.0%} of the ${max_loss:,.0f} it can lose "
                    f"(stop at {stop_share:.0%})",
                )
            )
        te = rules["credit"]["time_exit"]
        inside = (
            _entered_inside(trade, chain, short_expiries[0], int(te["dte"]), te, "credit.time_exit")
            if trade["nearest_short_dte"] is not None
            else None
        )
        if trade["nearest_short_dte"] is not None and not inside:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(_date(short_expiries[0]), int(te["dte"]))}
            if not off("credit.time_exit") and 0 <= trade["nearest_short_dte"] <= int(te["dte"]):
                fired.append(
                    _fire(
                        "credit.time_exit",
                        TAKE_PROFIT if pnl > 0 else CLOSE,
                        te["evidence"],
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
        tp_capture, tp_evidence, tp_shaped = _rules.for_shape(cr["take_profit"], trade["shape"], "capture")
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
                    cr["stop"]["evidence"],
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
                    tp_evidence,
                    f"{trade['capture']:.0%} of the ${credit:,.0f} credit captured (target {tp_capture:.0%}"
                    + (f", a {trade['shape']}'s own" if tp_shaped else "")
                    + ")",
                )
            )
        te = cr["time_exit"]
        inside = (
            _entered_inside(trade, chain, short_expiries[0], int(te["dte"]), te, "credit.time_exit")
            if trade["nearest_short_dte"] is not None
            else None
        )
        if trade["nearest_short_dte"] is not None and not inside:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(_date(short_expiries[0]), int(te["dte"]))}
            winner = pnl > 0
            # A covered call losing at its time exit means the stock has risen through the premium: the
            # shares hold that gain, so the call is not bought back at a loss (covered_call.assigned
            # judges it if it is in the money); a winner still rolls, for a credit only.
            covered_loser = is_covered and not winner
            if (
                not off("credit.time_exit")
                and not covered_loser
                and 0 <= trade["nearest_short_dte"] <= int(te["dte"])
            ):
                fired.append(
                    _fire(
                        "credit.time_exit",
                        ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                        te["evidence"],
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
                            br["evidence"],
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
                    eb["evidence"],
                    f"earnings {trade['earnings']['date']} fall inside the short legs' life",
                )
            )

    # ---- debit trades
    else:
        debit = basis
        db = rules["debit"]
        ret = trade["capture"]
        gain, gain_evidence, gain_shaped = _rules.for_shape(
            db["take_profit"], trade["shape"], "gain_fraction"
        )
        trade["exit_levels"]["stop"] = {"close_value": debit * (1 - float(db["stop"]["loss_fraction"]))}
        # A diagonal is worth about its width at the short's expiry, so a target past what it can make
        # there never fires (a PMCC at +75% of its debit sat above its whole call-away value): take
        # the lesser of the debit target and `share` of its modelled gain at that expiry.
        cap = db["take_profit"].get("max_gain_cap") or {}
        capped_by = None
        if (
            trade["shape"] in (cap.get("shapes") or [])
            and profile
            and debit > 0
            and profile["max_gain_from_open"] > 0
        ):
            ceiling = float(cap["share"]) * profile["max_gain_from_open"] / debit
            if ceiling < gain:
                gain, capped_by = (
                    ceiling,
                    {
                        "share": float(cap["share"]),
                        "max_gain": profile["max_gain_from_open"],
                        "horizon": profile["horizon"],
                        "modelled": True,
                    },
                )
        trade["exit_levels"]["take_profit"] = {
            "close_value": debit * (1 + gain),
            "target": gain,
            "by_shape": trade["shape"] if gain_shaped else None,
            "capped_by_max_gain": capped_by,
        }
        if not off("debit.stop") and ret is not None and ret <= -float(db["stop"]["loss_fraction"]):
            fired.append(
                _fire(
                    "debit.stop",
                    CLOSE,
                    db["stop"]["evidence"],
                    f"down {-ret:.0%} on the ${debit:,.0f} debit "
                    f"(stop at -{float(db['stop']['loss_fraction']):.0%})",
                )
            )
        if not off("debit.take_profit") and ret is not None and ret >= gain:
            fired.append(
                _fire(
                    "debit.take_profit",
                    TAKE_PROFIT,
                    gain_evidence,
                    f"up {ret:.0%} on the ${debit:,.0f} debit: take profit, roll or add "
                    f"(target +{gain:.0%}"
                    + (f", a {trade['shape']}'s own" if gain_shaped and not capped_by else "")
                    + (
                        f", {capped_by['share']:.0%} of the ${capped_by['max_gain']:,.0f} it can make by "
                        f"{capped_by['horizon']} (modelled)"
                        if capped_by
                        else ""
                    )
                    + ")",
                )
            )
        ps = db["price_stop"]
        if trade["shape"] in ps["shapes"] and profile and spot and expiries and not off("debit.price_stop"):
            share = float(ps["loss_share"])
            horizon = max((_date(expiries[0]) - session).days, 0) / 365.0
            band = pricing.price_band(legs, spot, horizon, rate, debit * (1 - share))
            if band:
                low, high = band
                trade["exit_levels"]["price_stop"] = {
                    "low": low,
                    "high": high,
                    "loss_share": share,
                    "horizon": expiries[0],
                    "modelled": True,
                }
                side = (
                    "below"
                    if low is not None and spot < low
                    else "above"
                    if high is not None and spot > high
                    else None
                )
                if side:
                    fired.append(
                        _fire(
                            "debit.price_stop",
                            REVIEW,
                            ps["evidence"],
                            f"the underlying at {spot:,.2f} is {side} the price stop "
                            f"({_band_text(low, high)}, where the trade is modelled to lose {share:.0%} of "
                            f"the debit by {expiries[0]}); close if it closes there",
                        )
                    )
        te = db["time_exit"]
        clock = "nearest short" if shorts else "nearest leg"
        if short_term:
            # The short is written week by week: the trade's clock is the long it is sold against.
            anchor = min(
                _date(leg["expiration"])
                for leg in options
                if leg["quantity"] > 0 and leg["expiration"] > short_term["expiration"]
            )
            limit, when, clock = int(te["time_spread_long_dte"]), (anchor - session).days, "the long"
        elif shorts:
            limit, when = int(te["short_leg_dte"]), trade["nearest_short_dte"]
            anchor = _date(short_expiries[0])
        else:
            limit = int(te["long_only_dte"])
            when = (_date(expiries[0]) - session).days if expiries else None
            anchor = _date(expiries[0]) if expiries else None
        inside = (
            _entered_inside(trade, chain, anchor.isoformat(), limit, te, "debit.time_exit")
            if anchor
            else None
        )
        if anchor and not inside:
            trade["exit_levels"]["time_exit"] = {"date": _exit_day(anchor, limit)}
        if not inside and not off("debit.time_exit") and when is not None and 0 <= when <= limit:
            winner = pnl > 0
            fired.append(
                _fire(
                    "debit.time_exit",
                    ROLL_OR_CLOSE if winner and te["roll_if_profitable"] else CLOSE,
                    te["evidence"],
                    f"{clock} is {when} DTE (exit at {limit}); "
                    + (
                        "profitable, so the shorts may roll out, for a net credit only"
                        if winner
                        else "losing, so close; never roll a loser"
                    ),
                )
            )

    # ---- a diagonal with little left to make at the short's expiry
    dw = rules["debit"]["diagonal_width"]
    width = _diagonal_width(options) if trade["shape"] == "diagonal" else None
    if width and cost > 0 and not off("debit.diagonal_width"):
        share = float(dw["max_share_of_width"])
        check = _diagonal_check(options, cost, width, share, float(dw["run_past_short_moves"]), spot, pr)
        trade["diagonal_check"] = check
        if check["flag"]:
            fired.append(
                _fire("debit.diagonal_width", REVIEW, dw["evidence"], _diagonal_reason(check, cost, share))
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
                    lt["evidence"],
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
                    lv["evidence"],
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
                    fo["evidence"],
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
                        ur["evidence"],
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
                    xr["evidence"],
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
                    rk["evidence"],
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
                    rp["evidence"],
                    f"roll the short {roll['strike']:g} put out to {roll['expiration']} for about "
                    f"{roll['credit']:.2f} cr (modelled) instead of closing it; "
                    "close if it will not fill for a credit",
                )
            )

    # ---- a diagonal's short structure rolls out for a credit when it is due, if the long still works
    rd = rules["roll"]["diagonal_short"]
    names = {f["rule"] for f in fired}
    due = {"credit.time_exit", "debit.time_exit", "legs.short_take_profit"} & names
    if short_term:
        # A short written week by week is due when the expiry close reaches it, not three weeks out.
        front = {_label(leg) for leg in shorts if leg["expiration"] == short_term["expiration"]}
        if any(
            f["rule"] == "expiry" and f["action"] == CLOSE_LEGS and front & set(f["legs"] or [])
            for f in fired
        ):
            due.add("expiry")
    stopped = {"debit.stop", "credit.stop"} & names
    if due and spot and not off("roll.diagonal_short"):
        roll, blocked = _diagonal_roll(
            options, session, spot, rate, rd, cost, short_term=short_term is not None, roll_out=roll_out
        )
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
                    rd["evidence"],
                    f"roll the {roll['from']} short structure out to {roll['expiration']} "
                    "at the same strikes "
                    f"for about {roll['credit']:.2f} cr "
                    f"({'modelled' if roll['modelled'] else 'quoted mid'}), "
                    "keeping the long; "
                    "only for a net credit",
                    roll["legs"],
                )
            )

    # ---- a covered call: the shares cover it, so an in-the-money call is judged by what a call-away does
    if is_covered:
        cov = trade["covered"]
        cycle_basis = next(
            (
                item["cycle"]["wheel_basis"]
                # The call's trade holds the call, not the shares: the cycle that holds both says so.
                for item in (wheel_ctx or {}).get("cycles") or []
                if (item["cycle"].get("shares") or 0) > 0
                and item["cycle"]["reconciled"]
                and item["cycle"].get("wheel_basis") is not None
            ),
            None,
        )
        if cycle_basis is not None:
            cov.update(basis=float(cycle_basis), basis_source="wheel basis")
        elif cov["share_cost"] is not None:
            cov.update(basis=float(cov["share_cost"]), basis_source="share cost")
        assigned = _covered_call_assigned(legs, cov, rules["covered_call"]["assigned"])
        if assigned and not off("covered_call.assigned"):
            fired.append(assigned)

    # ---- a wheel cycle's shorts and shares (wheel.py): ranked with every other rule by action
    if wheel_ctx:
        fired += _wheel_rules(trade, legs, wheel_ctx, session, net_liq, metrics, rules, off)

    # ---- a recognised PMCC's width, assignment exposure and long (pmcc.py): ranked by action too
    if pmcc_ctx:
        try:
            fired += _pmcc_rules(trade, legs, pmcc_ctx, session, net_liq, metrics, rules, off, broker_greeks)
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
            # The expiry close waits too when the trade comes off after the call on or before its
            # legs' expiry day: the pin and post-close exercise risk it guards against comes at that
            # day's close, after the morning the trade comes off. An earnings trade sits on a short
            # expiring days after the call, so otherwise it is always inside the window and the hold
            # could never apply. On the day after the call the report is out, nothing is held, and
            # the expiry close stands beside the earnings close.
            waits = (expiry_close,) if expiring_on is not None and after <= expiring_on else ()
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
                    rules["earnings_trade"]["evidence"],
                    f"the {et['report']} report is out: an earnings trade is closed after the call",
                )
            )

    trade["fired"] = fired
    return _decide(trade, rules, legs)


# The rule families an earnings trade's report outranks: closing, taking profit and rolling wait for
# the call. Safety (expiry and assignment, ex-dividend), sizing and unpriced legs never wait, except
# the expiry close when the trade comes off after the call on or before its legs' expiry day.
HELD_FOR_EARNINGS = {"credit", "debit", "directional", "legs", "roll", "wheel"}

# A report before the open: the broker spells it BTO, most calendars BMO.
BEFORE_OPEN = {"BTO", "BMO"}


def pre_open(generated_at: Any, session: date, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Set when the snapshot was taken on the session's own day before 09:30 New York time: its spot
    prices and marks are the last session's, and an option expiring today has no quote yet, so the
    expiring legs are left unpriced and a name's delta reads off yesterday's close. `taken_at` is the
    New York wall clock; `expiring_unpriced` the trades holding a leg that expires today with no mark.
    None for a snapshot taken after the open, on another day, or with no readable time."""
    try:
        taken = datetime.strptime(str(generated_at), "%Y-%m-%dT%H:%M:%S%z").astimezone(NEW_YORK)
    except ValueError:
        try:
            taken = datetime.fromisoformat(str(generated_at)).astimezone(NEW_YORK)
        except ValueError:
            return None
    if taken.tzinfo is None or taken.date() != session or taken.time() >= time(9, 30):
        return None
    today = session.isoformat()
    unpriced = sorted(
        {
            t.get("trade_id") or t["underlying"]
            for a in accounts
            for t in a.get("trades") or []
            for leg in t.get("legs") or []
            if leg.get("expiration") == today and leg.get("mark") is None
        }
    )
    return {"taken_at": taken.strftime("%H:%M"), "expiring_unpriced": unpriced}


def _ny(stamp: Any) -> datetime | None:
    for parse in (
        lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
        lambda s: datetime.fromisoformat(s),
    ):
        try:
            t = parse(str(stamp))
        except (TypeError, ValueError):
            continue
        return t.astimezone(NEW_YORK) if t.tzinfo is not None else None
    return None


def early(snapshot: dict[str, Any], session: date, rules: dict[str, Any]) -> dict[str, Any] | None:
    """Set when the marks this evaluation judged were taken on the session's day between the open
    (09:30) and `early.verdicts_from` New York time (10:00; user, 2026-11-04): the opening half hour's
    quotes are wide and jumpy, so its verdicts are not to be acted on yet. The verdicts are still
    worked out and written; `early` tells every surface to show prices, P&L and needs-a-look and hold
    the verdicts back, and the orchestrator not to post the list. The time is the re-mark's when the
    snapshot was moved to newer quotes, else the snapshot's. None at or after it, or before the open
    (that is `pre_open`'s)."""
    remark = snapshot.get("remark") or {}
    taken = _ny(remark.get("at")) or _ny(snapshot.get("generated_at"))
    hh, mm = (int(x) for x in str((rules.get("early") or {}).get("verdicts_from") or "10:00").split(":"))
    until = time(hh, mm)
    if taken is None or taken.date() != session or not time(9, 30) <= taken.time() < until:
        return None
    return {"taken_at": taken.strftime("%H:%M"), "verdicts_from": until.strftime("%H:%M")}


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
        q = float(untested.get("div_yield") or 0.0)
        iv = pricing.implied_vol(spot, k, untested["years"], mid, untested["right"], rate, q)
        if not iv:
            continue
        delta = pricing.delta(spot, k, untested["years"], iv, untested["right"], rate, q)
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
    new_mid = pricing.model_price(
        spot, new_strike, old["years"], iv, old["right"], rate, float(old.get("div_yield") or 0.0)
    )
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


def _diagonal_check(
    options: list[dict[str, Any]],
    cost: float,
    width: float,
    share: float,
    moves: float,
    spot: float | None,
    pr: dict[str, Any],
) -> dict[str, Any]:
    """Whether a two-leg diagonal bought for `cost` has enough left to make, judged at the short's expiry.

    Modelled (both legs at their own IV, the long with its time left): the best case must beat the net
    by the margin `share` of the width allows, 1/share - 1 (a third at 75%), and the spread one expected
    move (`moves` of them, the short's own IV) past the short strike must still be worth the net. With
    the long at intrinsic its best case is the width and the first test is the width test itself, so a
    long expiring soon after the short is judged as before; a long with years left keeps time value the
    width ignores. Each leg is priced at the name's dividend yield, as every leg in the evaluation is
    (`pricing.leg_value_at`). Without a spot, or a leg no vol prices, it falls back to the width."""
    long = next(leg for leg in options if leg["quantity"] > 0)
    short = next(leg for leg in options if leg["quantity"] < 0)
    if not spot or not long.get("iv") or not short.get("iv") or short["years"] <= 0:
        return {"basis": "width", "width": width, "flag": cost > share * width, "loses": cost >= width}
    rate = float(pr["rate"])
    horizon = short["years"]

    def value(s: float) -> float:
        return sum(pricing.leg_value_at(leg, s, leg["years"] - horizon, rate) for leg in (long, short))

    steps = 2000  # half to one and a half times the spot, 0.05% apart, as the price stop's band
    best = max(value(spot * (0.5 + i / steps)) for i in range(steps + 1))
    best = max(best, value(short["strike"]))  # the peak sits at the short strike; never miss it on the grid
    move = spot * float(short["iv"]) * math.sqrt(horizon) * moves
    run_at = short["strike"] + move if short["right"] == "C" else short["strike"] - move
    run_value = value(run_at)
    thin = best < cost / share
    run_loses = run_value < cost
    return {
        "basis": "modelled",
        "width": width,
        "horizon": short["expiration"],
        "div_yield": long.get("div_yield"),
        "best": round(best, 2),
        "run_at": round(run_at, 2),
        "run_value": round(run_value, 2),
        "thin": thin,
        "run_loses": run_loses,
        "loses": best < cost,
        "flag": thin or run_loses,
    }


def _diagonal_reason(check: dict[str, Any], cost: float, share: float) -> str:
    width = check["width"]
    if check["basis"] == "width":
        return (
            f"${cost:,.0f} net debit is {cost / width:.0%} of the ${width:,.0f} between the strikes "
            f"(at most {share:.0%}; no IV to model the long): "
            + (
                "a deep move through the short loses money once the long's time value runs off"
                if check["loses"]
                else "a deep move through the short leaves little to make"
            )
        )
    parts = []
    if check["loses"]:
        parts.append(
            f"modelled at the short's expiry ({check['horizon']}) its best case is ${check['best']:,.0f}, "
            f"under the ${cost:,.0f} net debit: it loses money wherever the stock ends up"
        )
    elif check["thin"]:
        parts.append(
            f"modelled at the short's expiry ({check['horizon']}) its best case is ${check['best']:,.0f}, "
            f"{check['best'] / cost - 1:.0%} over the ${cost:,.0f} net debit "
            f"(at least {1 / share - 1:.0%}): little left to make"
        )
    if check["run_loses"]:
        parts.append(
            f"one expected move past the short, at {check['run_at']:,.2f}, it is worth "
            f"${check['run_value']:,.0f}, under the net: a run through the short loses money"
        )
    return "; ".join(parts)


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
    *,
    short_term: bool = False,
    roll_out: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(roll, None) or (None, why not) for a diagonal's (or calendar's) short structure.

    The structure is every leg in the nearest expiry that holds a short; the longs it is sold against
    are the legs expiring later. Priced at each leg's own IV on the new expiry (flat term structure), so
    the credit is an estimate. A `short_term` short (sold week by week) rolls to the first expiry the
    snapshot's roll-out chains quote at least `short_term_out_days` later, else the first monthly that
    far out, and takes a leg's quoted mid where that chain has its strike.
    """
    shorts = [leg for leg in options if leg["quantity"] < 0]
    if not shorts:
        return None, None
    front = min(leg["expiration"] for leg in shorts)
    group = [leg for leg in options if leg["expiration"] == front]
    longs = [leg for leg in options if leg["expiration"] > front and leg["quantity"] > 0]
    if not longs or any(leg["iv"] is None or leg["mid"] is None for leg in group):
        return None, None  # not a diagonal, or the front cannot be priced
    out_days = int(cfg["short_term_out_days" if short_term else "out_days"])
    min_long = int(cfg["short_term_min_long_days" if short_term else "min_long_days"])
    earliest = _date(front) + timedelta(days=out_days)
    quoted = _quoted_rolls(roll_out, group, earliest) if short_term else {}
    new_exp = min(quoted) if quoted else _monthly_on_or_after(earliest)
    long_exp = min(_date(leg["expiration"]) for leg in longs)
    if (long_exp - new_exp).days < min_long:
        return None, {
            "reason": "long_too_short",
            "long_expiration": long_exp.isoformat(),
            "min_days": min_long,
        }
    years = (new_exp - session).days / 365.0
    on_chain = quoted.get(new_exp) or {}
    new_mids = [
        on_chain[(leg["right"], leg["strike"])]
        if (leg["right"], leg["strike"]) in on_chain
        else pricing.model_price(
            spot, leg["strike"], years, leg["iv"], leg["right"], rate, float(leg.get("div_yield") or 0.0)
        )
        for leg in group
    ]
    modelled = any((leg["right"], leg["strike"]) not in on_chain for leg in group)
    unit = reduce(math.gcd, [int(abs(leg["quantity"])) or 1 for leg in group])
    net = sum(leg["quantity"] * (leg["mid"] - new) for leg, new in zip(group, new_mids, strict=True))
    credit = net / unit
    # The long's breakeven after the roll (its credit counts): a short beyond it cannot lock in a loss.
    line = long_breakeven(longs, cost - net * pricing.MULTIPLIER)
    # A short at its own long's strike (a calendar) is met, if assigned, by that long at the same
    # strike: the strikes net to nothing and the trade can lose no more than its debit, which is its
    # worst case anyway. The breakeven line is a diagonal's guard, and on a calendar it blocked every
    # roll (user, 2026-11-06).
    same_strike = {(leg["right"], leg["strike"]) for leg in longs}
    for short in (leg for leg in group if leg["quantity"] < 0):
        if (short["right"], short["strike"]) in same_strike:
            continue
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
        + (
            f"{leg['strike']:g}{leg['right']} @ {new:.2f} (quoted mid)"
            if (leg["right"], leg["strike"]) in on_chain
            else f"{leg['strike']:g}{leg['right']} @ ~{new:.2f} (modelled)"
        )
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
            "modelled": modelled,
            "legs": close_rows + open_rows,
        },
        None,
    )


def _quoted_rolls(
    roll_out: dict[str, Any] | None, group: list[dict[str, Any]], earliest: date
) -> dict[date, dict[tuple[str, float], float]]:
    """{expiry: {(right, strike): mid}} for each expiry on or after `earliest` that the snapshot's
    roll-out chains (v14) quote for the short structure's name, two-sided quotes only. An expiry
    there is a listed one, so a weekly roll never lands on a date the name does not trade."""
    out: dict[date, dict[tuple[str, float], float]] = {}
    name = str(group[0].get("underlying") or "")
    for right in sorted({leg["right"] for leg in group}):
        for chain in _wheel.roll_out_for(roll_out, name, right):
            exp = _date(chain["expiration"])
            if exp is None or exp < earliest:
                continue
            book = out.setdefault(exp, {})
            for o in chain["options"]:
                k, bid, ask = _f(o.get("strike")), _f(o.get("bid")), _f(o.get("ask"))
                if k is None or bid is None or ask is None or bid < 0 or ask <= 0 or ask < bid:
                    continue
                book.setdefault((right, k), (bid + ask) / 2.0)
    return out


def _opened_on(chain: dict[str, Any] | None, symbols: set[str], action: str) -> date | None:
    """The latest day an order in the chain did `action` ("sell to open", "buy to open") on any of
    `symbols`, so a roll counts from the roll; None without a chain or such an order."""
    days = [
        _date(str(order.get("executed_at") or "")[:10])
        for order in (chain or {}).get("orders") or []
        for row in order.get("legs") or []
        if " ".join(str(row.get("symbol") or "").split()) in symbols
        and action in str(row.get("action") or "").lower()
    ]
    return max((d for d in days if d), default=None)


# The rules a covered call is never judged by: the shares cover what each guards against. A stop or a
# breached-strike close would buy the call back at a loss the shares have already gained, and the
# call's modelled loss is unbounded only without them.
COVERED_SKIPS = frozenset({"credit.stop", "credit.breached", "risk.oversize"})
# The rules a wheel put (intent "wheel") is never judged by: a stop or a breached close would buy back,
# at a loss, a put the operator means to be assigned on. The sizing review stays as the guard.
WHEEL_PUT_SKIPS = frozenset({"credit.stop", "credit.breached"})


def _covered_call_assigned(
    legs: list[dict[str, Any]], cov: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any] | None:
    """A covered call in the money: what an assignment does against the basis. At or over it, the
    call-away is the trade working (review: let it go, or roll up and out for a credit); under it, a
    call-away loses (roll or close); with no basis known, a review saying so. None when no call is in
    the money."""
    itm = [leg for leg in legs if (leg["intrinsic"] or 0) > 0]
    if not itm:
        return None
    labels = [_label(leg) for leg in itm]
    basis = cov.get("basis")
    if basis is None:
        return _fire(
            "covered_call.assigned",
            REVIEW,
            cfg["evidence"],
            "the covered call is in the money and the shares' basis is unknown (no reconciled order chain "
            "or wheel cycle): check whether a call-away gains before rolling or buying it back",
            labels,
        )
    low = min(leg["strike"] for leg in itm)
    per_share = low - basis
    contracts = sum(-leg["quantity"] for leg in itm)
    dollars = per_share * pricing.MULTIPLIER * contracts
    where = f"the {low:g} strike against the {basis:.2f} {cov['basis_source']}"
    if per_share >= 0:
        return _fire(
            "covered_call.assigned",
            REVIEW,
            cfg["evidence"],
            f"the covered call is in the money: assigned, the shares go at {where}, "
            f"${dollars:,.0f} over it. Let them be called away, or roll up and out for a net credit",
            labels,
        )
    return _fire(
        "covered_call.assigned",
        ROLL_OR_CLOSE,
        cfg["evidence"],
        f"the covered call is in the money under the basis: assigned, the shares go at {where}, "
        f"${-dollars:,.0f} under it. Roll up and out for a net credit, or buy it back",
        labels,
    )


def _expiry_close_due(
    trade: dict[str, Any],
    chain: dict[str, Any] | None,
    expiring: list[dict[str, Any]],
    expiring_on: date,
    session: date,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """When every expiring leg was opened `close_by_dte` days or fewer before its expiry (read off the
    order chain), the expiry close is due on the expiry day, not at the first evaluation: a weekly put
    on at 2 DTE was otherwise told to close the day it opened. Recorded on the trade as
    `expiry_close_due` while it waits; None when the close fires now, on the expiry day, or with no
    chain to say when the legs were opened."""
    if not cfg.get("wait_if_entered_inside") or session >= expiring_on:
        return None
    limit = int(cfg["close_by_dte"])
    entries = []
    for leg in expiring:
        opened = _opened_on(chain, {_leg_symbol(leg)}, "to open")
        if opened is None:
            return None
        entries.append((_date(leg["expiration"]) - opened).days)
    if max(entries) > limit:
        return None
    trade["expiry_close_due"] = {
        "date": expiring_on.isoformat(),
        "entry_dte": max(entries),
        "limit": limit,
    }
    return trade["expiry_close_due"]


def _entered_inside(
    trade: dict[str, Any],
    chain: dict[str, Any] | None,
    expiry: str,
    limit: int,
    cfg: dict[str, Any],
    rule: str,
) -> dict[str, Any] | None:
    """When the legs in `expiry` (the time exit's clock) were opened `limit` days or fewer before it,
    the trade was entered inside its own time exit, which then never applies (user, 2026-11-06): the
    21-day exit was tested on trades opened about 45 days out, and on one opened inside it, it only
    says close on the first day. Recorded on the trade as `time_exit_skipped`; None when the exit
    stands, including when no order chain says when the legs were opened."""
    if not cfg.get("skip_if_entered_inside"):
        return None
    symbols = {_leg_symbol(leg) for leg in trade["legs"] if leg["right"] and leg["expiration"] == expiry}
    opened = _opened_on(chain, symbols, "to open")
    if opened is None:
        return None
    days = (_date(expiry) - opened).days
    if days > limit:
        return None
    trade["time_exit_skipped"] = {
        "rule": rule,
        "expiration": expiry,
        "opened_on": opened.isoformat(),
        "entry_dte": days,
        "limit": limit,
    }
    return trade["time_exit_skipped"]


def _short_term_short(
    options: list[dict[str, Any]], chain: dict[str, Any] | None, cfg: dict[str, Any]
) -> dict[str, Any] | None:
    """A time spread's nearest short when it was sold `short_term_short_days` or fewer before its
    expiry: {expiration, sold_on, days}. Read off the order chain (the latest order that sold one of
    its legs to open, so a roll counts from the roll); None without a chain, without a long expiring
    after the short, or when the short was sold further out."""
    shorts = [leg for leg in options if leg["quantity"] < 0]
    if not chain or not shorts:
        return None
    front = min(leg["expiration"] for leg in shorts)
    if not any(leg["quantity"] > 0 and leg["expiration"] > front for leg in options):
        return None
    symbols = {_leg_symbol(leg) for leg in shorts if leg["expiration"] == front}
    sold_on = _opened_on(chain, symbols, "sell to open")
    if sold_on is None:
        return None
    days = (_date(front) - sold_on).days
    if days > int(cfg["short_term_short_days"]):
        return None
    return {"expiration": front, "sold_on": sold_on.isoformat(), "days": days}


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
    new_mid = pricing.model_price(
        spot, leg["strike"], years, leg["iv"], "P", rate, float(leg.get("div_yield") or 0.0)
    )
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
            cov = trade.get("covered") or {}
            called_away_gains = (
                row["right"] == "C" and cov.get("basis") is not None and leg["strike"] >= cov["basis"]
            )
            # A covered call at or over the basis is the cycle working: covered_call.assigned reviews
            # it, and the wheel's repair roll does not apply.
            if row["tested"] and not called_away_gains and not off("wheel.tested"):
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
                    wr["take_profit"]["evidence"],
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
            for tleg, row, cands in tested:
                choice = "roll out in time or close. "
                if row["right"] == "P" and trade.get("wheel_put"):
                    # A name the operator wheels: assignment is the next step, not a failure.
                    shares = -tleg["quantity"] * pricing.MULTIPLIER
                    be = (cycle.get("breakeven") or {}).get("price")
                    choice = (
                        "roll out in time for a credit, close, or take the assignment "
                        f"({shares:g} shares at {tleg['strike']:g}, the cash already secures them"
                        + (f"; wheel breakeven {be:.2f}" if be is not None else "")
                        + "). "
                    )
                parts.append(
                    f"the {row['leg']} is tested ({row['spot']:g} at or through the strike): "
                    + choice
                    + (
                        f"Rolls to {cands['expiration']} at "
                        + (
                            "this session's roll-out chains"
                            if cands.get("source")
                            # The income reading is taken in the afternoon; which afternoon it was
                            # is its own `quoted_at`, never assumed to be the day before.
                            else "the income reading's quotes"
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
                    wr["tested"]["evidence"],
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
                        f"{q['mid']:.2f} at the income reading's mid"
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
                        wr["next_leg"]["evidence"],
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
    broker_greeks: dict[str, dict] | None = None,
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
                broker_greeks,
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
                    cfg["width"]["evidence"],
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
                    cfg["assignment"]["evidence"],
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
                    cfg["roll_long"]["evidence"],
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


def _rule_rank(rule: str) -> int:
    """A rule's place in the fixed tie-break order; a data review or an unknown rule sorts last."""
    try:
        return _rules.RULE_ORDER.index(rule)
    except ValueError:
        return len(_rules.RULE_ORDER)


def _recommend(trade: dict[str, Any], rules: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, Any]:
    fired = trade["fired"]
    if not fired:
        # A hold is no rule, so it has no grade: nothing fired.
        evidence = None
        reason = "no rule fired; hold with the exit levels resting"
        rule = "hold"
        et = trade.get("earnings_trade")
        if (
            et
            and not et["passed"]
            and not _rules.disabled(rules, trade["underlying"], "earnings_trade", trade.get("trade_id"))
        ):
            # Held FOR the report, not in spite of it: the earnings-trade rule is what holds it.
            rule = "earnings_trade"
            evidence = rules["earnings_trade"]["evidence"]
            reason = f"an earnings trade: hold through the {et['report']} report and close after the call"
            held = trade.get("held_for_earnings") or []
            if held:
                reason += "; waiting for the call: " + ", ".join(
                    f"{f['rule']} ({f['action'].lower()})" for f in held
                )
        elif trade.get("earnings") and trade["earnings"]["before_nearest_short_expiry"]:
            reason += f"; earnings {trade['earnings']['date']} fall inside the trade"
        trade["recommendation"] = {
            "action": HOLD,
            "evidence": evidence,
            "rule": rule,
            "reason": reason,
            "agreeing": [],
            "conflicts": [],
        }
        trade["order"] = None
        return trade
    # The most decisive action first; between rules naming the same action, the fixed rule order
    # (rules.RULE_ORDER: safety, sizing, stops, targets, time, rolls), never a grade.
    ranked = sorted(fired, key=lambda f: (PRIORITY.get(f["action"], 9), _rule_rank(f["rule"])))
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
    # A rule pointing the other way is named in `conflicts`; it does not lower anything.
    trade["recommendation"] = {
        "action": chosen["action"],
        "evidence": chosen["evidence"],
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


NO_ACCOUNT_HISTORY = "the snapshot carries no trade history for this account"

# The per-account fields a snapshot carries for an account other than the broker tool's default one
# (`accounts_history[<account_key>]`, written by the orchestrator's managesnap). The default account's
# stay at the top level, as every snapshot before named broker logins had them.
_ACCOUNT_FIELDS = (
    "transactions",
    "transactions_error",
    "account_transactions",
    "account_transactions_error",
    "working_orders",
    "working_orders_error",
)


def _is_history_account(account: dict[str, Any], snapshot: dict[str, Any], n_accounts: int) -> bool:
    """Is this the account the snapshot's top-level history belongs to? `history_account` names it
    when the snapshot says (the account key); a snapshot that does not reads it the way every one
    before named logins did: the designated account, or the only one."""
    named = snapshot.get("history_account")
    if "history_account" in snapshot:
        return bool(named) and account.get("account_key") == named
    return bool(account.get("designated") or n_accounts == 1)


def _has_own_history(account: dict[str, Any], snapshot: dict[str, Any], n_accounts: int) -> bool:
    key = account.get("account_key")
    return _is_history_account(account, snapshot, n_accounts) or (
        bool(key) and key in (snapshot.get("accounts_history") or {})
    )


def account_snapshot(snapshot: dict[str, Any], account: dict[str, Any], n_accounts: int) -> dict[str, Any]:
    """The snapshot as one account's views read it: its own trade history, account-wide history and
    day's orders in the top-level fields every reader already uses. The history account reads the
    snapshot as it is; another account with its own entry in `accounts_history` reads that; any other
    has none of the three, so nothing of one account's history is ever read for another."""
    if _is_history_account(account, snapshot, n_accounts):
        return snapshot
    own = (snapshot.get("accounts_history") or {}).get(account.get("account_key") or "")
    view = {k: v for k, v in snapshot.items() if k not in _ACCOUNT_FIELDS}
    if isinstance(own, dict):
        view.update({k: own[k] for k in _ACCOUNT_FIELDS if k in own})
    return view


def _wheel_view(
    account: dict[str, Any],
    n_accounts: int,
    history: dict[str, Any],
    history_ok: bool,
    session: date,
    metrics: dict[str, Any],
    snapshot: dict[str, Any],
    trades: list[dict[str, Any]],
    cash_roots: list[str] | None = None,
) -> dict[str, Any]:
    """The account's wheel cycles (`wheel.py`), each naming the evaluated trades that hold its legs.

    The view reads this account's own trade history and day's orders (`account_snapshot`); an account
    the snapshot carries none for gets no view and says why. Cash-settled index names
    (`expiry.cash_settled_roots`) are left out: no shares are ever delivered there.
    """
    if not _has_own_history(account, snapshot, n_accounts):
        return {"cycles": [], "error": NO_ACCOUNT_HISTORY}
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
            cash_roots=set(cash_roots or ()),
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
    view's shape: one long, one short of the same size at or above its strike expiring first, no
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
    """Join a short call that a long call on the name covers into one trade with it: the cases the PMCC
    link (`_join_pmcc`) leaves split, a short sold in an order of its own against a long call it is
    not linked to (expiring with it, covering more contracts than it, or a link that does not
    reconcile).

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


# The intent that takes a name off the candidates list without making it a wheel: a put the operator
# would not take shares on, so it keeps its stop and its breached close.
NO_WHEEL = "no_wheel"


def _is_intent_candidate(trade: dict[str, Any]) -> bool:
    """A trade the wheel could hold whose name (and the trade itself) has no intent yet: short puts
    alone, nothing else. A spread, a condor or anything with a long option is never a wheel."""
    legs = trade.get("legs") or []
    return bool(
        trade.get("intent") is None
        and legs
        and all(leg.get("right") == "P" and (leg.get("quantity") or 0) < 0 for leg in legs)
    )


def _with_intent(rules: dict[str, Any], underlying: str, intent: str) -> dict[str, Any]:
    """The ruleset with one name's intent set, everything else as it was."""
    out = dict(rules)
    positions = dict(rules.get("positions") or {})
    positions[underlying] = {**(positions.get(underlying) or {}), "intent": intent}
    out["positions"] = positions
    return out


def _verdict(trade: dict[str, Any]) -> dict[str, Any]:
    rec = trade.get("recommendation") or {}
    return {"action": rec.get("action"), "rule": rec.get("rule"), "reason": rec.get("reason")}


def _intent_candidate(
    trade: dict[str, Any], as_wheel: dict[str, Any], wheel_view: dict[str, Any], trade_id: str | None
) -> dict[str, Any]:
    """One row of the wheel tab's "no intent yet": the put, the cash it ties up, what an assignment
    would cost a share, any report inside it, the name's wheel history, and the verdict today beside
    the verdict marked as a wheel. Nothing here sets an intent; the console stages it for Config."""
    legs = trade["legs"]
    underlying = trade["underlying"]
    contracts = sum(-leg["quantity"] for leg in legs)
    cash = sum(leg["strike"] * -leg["quantity"] for leg in legs) * pricing.MULTIPLIER
    credit = -(trade.get("cost") or 0.0)
    strikes = sorted({leg["strike"] for leg in legs})
    shares = contracts * pricing.MULTIPLIER
    er = trade.get("earnings") or {}
    cycles = [c for c in wheel_view.get("cycles") or [] if c.get("underlying") == underlying]
    now, wheeled = _verdict(trade), _verdict(as_wheel)
    return {
        "trade_id": trade_id,
        "underlying": underlying,
        "legs": [leg["label"] for leg in legs],
        "contracts": contracts,
        "strikes": strikes,
        "expiration": trade.get("nearest_short_expiry"),
        "dte": trade.get("nearest_short_dte"),
        "cash_secured": cash,
        # One strike: what the shares would cost once the premium kept is counted.
        "net_if_assigned": (strikes[0] - credit / shares) if len(strikes) == 1 and shares else None,
        "earnings": er.get("date") if er.get("before_nearest_short_expiry") else None,
        "history": {
            "cycles_complete": sum(1 for c in cycles if c.get("status") == "complete"),
            "assignments": sum(int(c.get("assignments") or 0) for c in cycles),
        },
        "wheel_path": bool(as_wheel.get("wheel_put")),
        "now": now,
        "as_wheel": wheeled,
        "changes": (now["action"], now["rule"]) != (wheeled["action"], wheeled["rule"]),
        "key": f"positions.{underlying}.intent",
    }


def _share_cover(
    groups: list[tuple[list[dict[str, Any]], dict[str, Any] | None]],
) -> tuple[dict[str, Any] | None, set[int]]:
    """The shares that cover a name's short calls, and which groups they cover.

    A group made only of short calls sits beside the shares in a chain of its own, and alone it read as
    a naked call: stopped at a loss when the stock rose, its loss called unbounded. When the shares
    held cover every such short call (100 a contract, all of them; partial cover covers none), each is
    a covered call. `share_cost` is the shares' own reconciled order chain's cost a share, else None
    (never the positions' open price); the wheel basis, where a cycle holds the shares, is read in
    `evaluate_trade` instead."""
    shares = sum(_f(leg.get("quantity")) or 0.0 for g, _ in groups for leg in g if not leg.get("right"))
    calls = [
        i
        for i, (g, _) in enumerate(groups)
        if g and all(leg.get("right") == "C" and (_f(leg.get("quantity")) or 0.0) < 0 for leg in g)
    ]
    contracts = sum(-(_f(leg.get("quantity")) or 0.0) for i in calls for leg in groups[i][0])
    if not calls or shares <= 0 or shares < pricing.MULTIPLIER * contracts:
        return None, set()
    share_groups = [(g, c) for g, c in groups if g and all(not leg.get("right") for leg in g)]
    cost = None
    if len(share_groups) == 1:
        g, chain = share_groups[0]
        held = sum(_f(leg.get("quantity")) or 0.0 for leg in g)
        if chain and chain.get("reconciled") and held > 0:
            cost = -float(chain["cash"]) / held
    return {"shares": shares, "contracts": contracts, "share_cost": cost}, set(calls)


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
    """The account's PMCCs (`pmcc.py`), recognised or not. Like the wheel view, it needs this account's
    own history to link them, and a failure costs the view: every trade is still judged, by its
    diagonal rules alone. Only this account's legs and history are read, so a long in one account and
    a short in another are never one diagonal."""
    if not _has_own_history(account, snapshot, n_accounts):
        return {"positions": [], "error": NO_ACCOUNT_HISTORY}
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
    whole = snapshot
    n_accounts = len(positions.get("accounts") or [])
    # Stable trade IDs, against the store as this snapshot carried it (tradeids.py).
    ids = _tradeids.Assigner(snapshot.get("trade_ids"))
    accounts = []
    for account in positions.get("accounts") or []:
        # Every view below reads this account's own history and orders, never another account's
        # (`account_snapshot`); the shared readings -- metrics, chains, greeks -- are the same for all.
        snapshot = account_snapshot(whole, account, n_accounts)
        own_history = _has_own_history(account, snapshot, n_accounts)
        # Trade history arrived with snapshot v2; without it (or when its fetch failed) every trade falls
        # back to the open legs' prices, and says so.
        history = snapshot.get("transactions") or {}
        history_ok = "transactions" in snapshot and not snapshot.get("transactions_error")
        balances = account.get("balances") or {}
        net_liq = _f(balances.get("net_liquidating_value"))
        trades = []
        excluded = []
        dropped_legs: list[dict[str, Any]] = []
        grouping_errors: list[str] = []
        intent_candidates: list[dict[str, Any]] = []
        # The wheel view first: its cycles are a pure reading of the history, and the wheel rules judge
        # each trade holding a cycle's legs alongside every other rule.
        wheel_view = _wheel_view(
            account,
            n_accounts,
            history,
            history_ok,
            session,
            metrics,
            snapshot,
            trades,
            rules["expiry"]["cash_settled_roots"],
        )
        pmcc_view = _pmcc_view(
            account,
            n_accounts,
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
                dropped_legs.extend(dropped)
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
            cover, covered_groups = _share_cover(groups)
            for gi, (group, chain) in enumerate(groups):
                # The ID first: a trade's own overrides (`trades.<ID>`) are read while it is judged.
                ident = ids.assign(u["underlying"], group, chain)

                args: dict[str, Any] = {
                    "underlying": u["underlying"],
                    "raw_legs": group,
                    "metrics": metrics.get(u["underlying"]),
                    "session": session,
                    "net_liq": net_liq,
                    "chain": chain,
                    "seen": snapshot.get("earnings_trades_seen"),
                    "roll_chains": snapshot.get("roll_chains"),
                    "wheel_ctx": _wheel_context(wheel_view, group, snapshot, u["underlying"]),
                    "pmcc_ctx": _pmcc_context(pmcc_view, group, account),
                    "broker_greeks": snapshot.get("broker_greeks"),
                    "trade_id": ident["trade_id"],
                    "roll_out": snapshot.get("roll_out_chains"),
                    "div_yield": _pmcc._yield(
                        (snapshot.get("underlying_history") or {}).get(u["underlying"]),
                        session.isoformat(),
                        _f((metrics.get(u["underlying"]) or {}).get("last")),
                    ),
                    "covered": cover if gi in covered_groups else None,
                }
                trade = evaluate_trade(rules=rules, **args)
                if _is_intent_candidate(trade):
                    # What marking the name as a wheel would change today: the same trade judged
                    # again with the intent, so the console shows both verdicts and never guesses.
                    as_wheel = evaluate_trade(rules=_with_intent(rules, u["underlying"], "wheel"), **args)
                    intent_candidates.append(
                        _intent_candidate(trade, as_wheel, wheel_view, ident["trade_id"])
                    )
                trade["trade_id"] = ident["trade_id"]
                trade["trade_id_provisional"] = ident["provisional"]
                trade["merged_ids"] = ident["merged"]
                trade["trade_ident"] = ident
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
            snapshot.get("spot_vol_history"),
        )
        book["hedges"] = _hedge.suggest(book, snapshot.get("hedge_chain"), metrics, session, rules)
        # The direction card's ideas: what drives delta and the rolls that move it (deltaideas.py).
        book["delta_ideas"] = _deltaideas.suggest(
            book, trades, snapshot, metrics, session, rules, pmcc=pmcc_view
        )
        book["candidates"] = _candidates.suggest(
            snapshot.get("income"),
            book,
            trades,
            session,
            rules,
            account.get("account_type"),
            book.get("benchmark_spot"),
            # This account's own day list (`account_snapshot`).
            snapshot.get("working_orders"),
            # What the Screeners trades are judged to fit against (`candidates.screen_fit`).
            available_bp=_f(balances.get("derivative_buying_power")),
        )
        # The time-spread scanner's rows sized to this account (timespreads.suggest); no tested edge.
        book["time_spreads"] = _timespreads.suggest(
            snapshot.get("time_spreads"),
            book,
            trades,
            session,
            rules,
            account.get("account_type"),
            book.get("benchmark_spot"),
        )
        # The pace reads this account's own account-wide history; an account the snapshot carries none
        # for gets no pace -- another account's would be someone else's trades.
        premium = (
            _premium.reading(
                snapshot.get("account_transactions") if "account_transactions" in snapshot else None,
                snapshot.get("account_transactions_error"),
                session,
                book,
                net_liq,
                rules,
            )
            if own_history
            else None
        )
        # Every short option held against its strike, and what expires soon (`attention.py`).
        attention = _attention.reading(account.get("underlyings"), metrics, session, rules)
        # Each held name's P&L across its price, at expiry and on dates before it (`payoff.py`).
        payoff = _payoff.reading(trades, metrics, snapshot, session, rules)
        # Your limits beside the book, and the day's loss against its alert (`limits.py`).
        limits = _limits.reading(book, trades, account.get("underlyings"), account.get("account_type"), rules)
        # The wheel's names, each with a cycle; the wheel tab shows them and no other.
        wheel_view["names"] = sorted({c["underlying"] for c in wheel_view["cycles"]})
        # What each wheel name has earned over the whole history (`ledger.py`), on the pace's account rule.
        ledger = (
            _ledger.reading(
                snapshot.get("account_transactions") if "account_transactions" in snapshot else None,
                snapshot.get("account_transactions_error"),
                session,
                rules,
                scope=wheel_view["names"],
                # Only the wheel's own contracts: another trade on a wheel name (a PMCC) is not the wheel's.
                contracts=sorted({c for cy in wheel_view["cycles"] for c in cy.get("contracts") or []}),
            )
            if own_history
            else None
        )
        # The legs left out above, split into the loops' and the hand's (`zerodte.py`); the same
        # account rule as the pace, since both read the designated account's history.
        zero_dte_names = {
            str(s).upper() for s in ((rules.get("exclude") or {}).get("zero_dte_underlyings") or [])
        }
        zero_dte = (
            _zerodte.reading(
                session=session,
                names=zero_dte_names,
                held=dropped_legs,
                transactions=snapshot.get("account_transactions")
                if "account_transactions" in snapshot
                else None,
                transactions_error=snapshot.get("account_transactions_error"),
                known=snapshot.get("live_known_orders") if "live_known_orders" in snapshot else None,
                known_error=snapshot.get("live_known_orders_error"),
            )
            if zero_dte_names and own_history
            else None
        )
        _link_wheel(wheel_view, trades)
        _link_pmcc(pmcc_view, trades)
        wheel_view["intent_candidates"] = intent_candidates
        accounts.append(
            {
                "account": account.get("account"),
                # Which broker login it sits on and its key there (absent on a snapshot from before
                # named logins: main, keyed by its masked number).
                "login": account.get("login") or "main",
                "login_label": account.get("login_label"),
                "account_key": account.get("account_key")
                or f"main-{str(account.get('account') or '****')[-4:]}",
                "role": account.get("role"),
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
                "premium": premium,
                "ledger": ledger,
                "attention": attention,
                "payoff": payoff,
                "limits": limits,
                "trades": trades,
                "excluded": excluded,
                "zero_dte": zero_dte,
            }
        )
    return {
        "eval_version": EVAL_VERSION,
        "session": session.isoformat(),
        # The whole snapshot's, never the last account's view (`account_snapshot`) left in `snapshot`.
        "snapshot_generated_at": whole.get("generated_at"),
        "ok": bool(positions.get("ok")),
        "error": positions.get("error"),
        "metrics_error": whole.get("metrics_error"),
        "transactions_error": whole.get("transactions_error")
        if "transactions" in whole
        else "no trade history in this snapshot (taken before chains were read)",
        "marks": positions.get("marks"),
        "pre_open": pre_open(whole.get("generated_at"), session, accounts),
        "early": early(whole, session, rules),
        "ruleset": rules.get("ruleset"),
        "rules": rules,
        "rule_warnings": list(warnings or []),
        "accounts": accounts,
        # The logins with a producer of their own, carried for `stream_request` (absent before them).
        "stream_instances": list(whole.get("stream_instances") or []),
    }
