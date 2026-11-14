"""The rules engine: `decide` turns an arm and a snapshot into an entry or the reason there is none;
`manage` says whether an open position should be closed now; `close_position` prices the close and
returns the trade. Pure and deterministic: the same arm and snapshot give the same answer from the
backtest driver and the paper loop, which differ only in how they BUILD the snapshot.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from cherrypick.core import calendar as cal

from . import gates as gates_mod
from . import indicators as ind
from . import pricing, strikes
from .model import Chain, Decision, Entry, Leg, Snapshot, hhmm


# --------------------------------------------------------------------------- expiry and trigger
def resolve_expiry(arm: dict, snap: Snapshot) -> str | None:
    """The listed expiry the arm's rule names today, or None when none is listed."""
    rule = arm["expiry"]
    listed = sorted(e for e in snap.chains if e >= snap.session)
    today = snap.session_date
    if rule["kind"] == "dte":
        target = today + timedelta(days=rule["days"])
    elif rule["kind"] == "market_days":
        target = cal.nth_trading_day(today, rule["days"])
    else:
        target = today + timedelta(days=rule["days"])
    tol = rule.get("tolerance", 0)
    best = None
    for e in listed:
        gap = abs((date.fromisoformat(e) - target).days)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, e)
    return None if best is None else best[1]


def breakout_minute(trail, minutes: int, latest: int) -> int | None:
    """The first minute at or after the opening range's end whose spot exceeds the range's high."""
    rng = ind.opening_range(trail, minutes)
    if rng is None:
        return None
    for m, s in trail:
        if rng["done_at"] <= m <= latest and s > rng["high"]:
            return m
    return None


def entry_due(arm: dict, snap: Snapshot) -> tuple[bool, object]:
    e = arm["entry"]
    if e["kind"] == "time":
        at = hhmm(e["at"])
        return at <= snap.minute < at + e.get("window", 5), snap.minute
    latest = hhmm(e["latest"])
    b = breakout_minute([(m, s) for m, s in snap.trail if m <= snap.minute], e["minutes"], latest)
    if b is None or snap.minute > latest:
        return False, b
    if e.get("single_shot", True):
        return snap.minute == b, b
    return True, b


# --------------------------------------------------------------------------- the decision
def decide(arm: dict, snap: Snapshot, *, costs: dict | None = None) -> Decision:
    """Whether `arm` enters on `snap`. The first failing check is the reason; `checks` lists what was
    read up to it."""
    checks: list = []

    def fail(name, value=None):
        checks.append((name, False, value))
        return Decision(arm["name"], False, name, None, checks)

    def ok(name, value=None):
        checks.append((name, True, value))

    if snap.symbol != arm["symbol"]:
        return fail("symbol", snap.symbol)
    if snap.session_date.weekday() not in arm["weekdays"]:
        return fail("weekday", snap.session_date.weekday())
    ok("weekday", snap.session_date.weekday())
    due, v = entry_due(arm, snap)
    if not due:
        return fail("entry_not_due", v)
    ok("entry_due", v)

    lim = arm.get("limits", {})
    mine = snap.entries.get(arm["name"], [])
    if snap.session in mine:
        return fail("entered_today")
    opened = [p for p in snap.open_positions if p.arm == arm["name"]]
    if len(opened) >= lim.get("max_open", 1):
        return fail("max_open", len(opened))
    if lim.get("every_days") and mine:
        gap = (snap.session_date - date.fromisoformat(max(mine))).days
        if gap < lim["every_days"]:
            return fail("every_days", gap)
    ok("limits")

    expiry = resolve_expiry(arm, snap)
    if expiry is None:
        return fail("no_expiry")
    if lim.get("one_per_expiry") and any(p.expiry == expiry for p in opened):
        return fail("one_per_expiry", expiry)
    ok("expiry", expiry)

    skip = arm.get("skip_events") or {}
    last = date.fromisoformat(expiry) if skip.get("through_expiry") else snap.session_date
    for label in skip.get("labels", []):
        hit = snap.events.occurs(label, snap.session_date, last) if snap.events else None
        if hit:
            return fail(f"event:{label}", True)
        checks.append((f"event:{label}", True, "unknown" if hit is None else False))

    chain = snap.chains[expiry]
    ctx = gates_mod.Ctx(snap, arm, chain, expiry)
    for g in arm.get("gates", []):
        if gates_mod.stage_of(g) != "market":
            continue
        passed, v = gates_mod.run(g, ctx)
        if not passed:
            return fail(f"gate:{g['gate']}", v)
        ok(f"gate:{g['gate']}", v)

    chosen: dict[str, float] = {}
    legs: list[Leg] = []
    for spec in arm["legs"]:
        k = strikes.pick(spec["strike"], spec["right"], snap, chain, chosen)
        if k is None or chain.get(k, spec["right"]) is None:
            return fail(f"strike:{spec['name']}")
        chosen[spec["name"]] = k
        legs.append(Leg(spec["name"], spec["right"], spec["side"], k))
    ok("strikes", chosen)

    quotes = pricing.quotes_for(legs, chain)
    prices = pricing.structure_prices(quotes) if quotes else None
    if prices is None:
        return fail("unpriced")
    profit_mid, loss_mid = pricing.payoff_extremes(legs, prices["mid"])
    if loss_mid is None:
        return fail("undefined_risk")
    ctx.legs, ctx.quotes, ctx.prices = legs, quotes, prices
    ctx.profit_mid, ctx.loss_mid = profit_mid, loss_mid
    for g in arm.get("gates", []):
        if gates_mod.stage_of(g) != "structure":
            continue
        passed, v = gates_mod.run(g, ctx)
        if not passed:
            return fail(f"gate:{g['gate']}", v)
        ok(f"gate:{g['gate']}", v)

    qty = size(arm["size"], loss_mid)
    if qty < 1:
        return fail("size", loss_mid)
    ok("size", qty)
    sym = arm["symbol"]
    slip = {k: pricing.slippage(k, quotes, qty, sym, costs) for k in pricing.KINDS}
    profit, loss = pricing.payoff_extremes(legs, prices["mid"])
    entry = Entry(
        arm=arm["name"],
        session=snap.session,
        minute=snap.minute,
        expiry=expiry,
        legs=legs,
        quantity=qty,
        prices=prices,
        max_loss=loss,
        max_profit=profit,
        spot=snap.spot,
        slippage=slip,
        fees_open=pricing.open_fee(sym, qty, legs),
    )
    return Decision(arm["name"], True, "enter", entry, checks)


def size(rule: dict, loss_per_share: float) -> int:
    """Contracts: a fixed count, or the largest whole number whose maximum loss stays within the budget,
    never fewer than the rule's `minimum` (default 0: no trade when not even one fits)."""
    if "contracts" in rule:
        return int(rule["contracts"])
    per = loss_per_share * pricing.MULTIPLIER
    n = int(math.floor(rule["max_risk"] / per + 1e-9)) if per > 0 else 0
    return max(n, int(rule.get("minimum", 0)))


# --------------------------------------------------------------------------- holding
class Position:
    """An open position: the entry, plus what the exit rules read."""

    def __init__(self, entry: Entry, arm: dict):
        self.entry = entry
        self.arm = arm["name"]
        self.symbol = arm["symbol"]
        self.exit_rules = arm.get("exit", {})

    @property
    def expiry(self) -> str:
        return self.entry.expiry


def _credit_now(pos: Position, chain: Chain) -> float | None:
    """What the position's close would bring at mid (per share), None when a leg has no quote."""
    q = pricing.quotes_for(pos.entry.legs, chain)
    if q is None or not all(x["ask"] for x, _ in q):
        return None
    return sum(n * (x["bid"] + x["ask"]) / 2 for x, n in q)


def manage(pos: Position, snap: Snapshot) -> tuple[str, object] | None:
    """The exit due now, as (kind, detail), or None. Touch first, then profit target, then the time exit;
    held to settlement when the arm has none."""
    r, e = pos.exit_rules, pos.entry
    touch = r.get("touch")
    if touch:
        limit = e.spot * (1 + touch["pct"] / 100.0) if "pct" in touch else e.spot + touch["points"]
        crossed = snap.spot <= limit if (touch.get("pct", touch.get("points")) < 0) else snap.spot >= limit
        if crossed:
            return "touch", limit
    chain = snap.chains.get(e.expiry)
    if r.get("profit_target_pct") and chain is not None and e.max_profit:
        now = _credit_now(pos, chain)
        if now is not None:
            gained = e.prices["mid"] + now
            if gained >= r["profit_target_pct"] / 100.0 * e.max_profit - 1e-9:
                return "profit_target", gained
    if r.get("close_before_expiry_min") is not None and snap.session == e.expiry:
        if snap.minute >= snap.close_minute - r["close_before_expiry_min"]:
            return "time", snap.minute
    return None


def close_position(pos: Position, kind: str, snap: Snapshot, *, costs: dict | None = None) -> dict | None:
    """Close at `snap`'s quotes and return the trade, or None when the legs do not price yet. Gross is
    mid to mid; each fill kind carries its own slippage and net."""
    e = pos.entry
    chain = snap.chains.get(e.expiry)
    q = pricing.quotes_for(e.legs, chain) if chain is not None else None
    flipped = None if q is None else [(x, -n) for x, n in q]
    if flipped is None or pricing.structure_prices(flipped) is None:
        return None
    exit_credit = sum(n * (x["bid"] + x["ask"]) / 2 for x, n in q)
    slip = {k: pricing.slippage(k, q, e.quantity, pos.symbol, costs) for k in pricing.KINDS}
    fee = pricing.close_fee(pos.symbol, e.quantity, e.legs)
    return _trade(pos, kind, snap.session, snap.minute, exit_credit, slip, fee, 0.0)


def settle_position(pos: Position, settle: float, session: str | None = None) -> dict:
    """Held to the expiry's settlement print `settle`: intrinsic value, no exit slippage."""
    e = pos.entry
    value = pricing.intrinsic_value(e.legs, settle)
    zero = dict.fromkeys(pricing.KINDS, 0.0)
    return _trade(
        pos, "settlement", session or e.expiry, None, value, zero, 0.0, pricing.settlement_fee(e.legs, settle)
    )


def _trade(pos, kind, session, minute, exit_credit, exit_slip, close_fee, settlement) -> dict:
    e = pos.entry
    mult = pricing.MULTIPLIER * e.quantity
    gross = round((e.prices["mid"] + exit_credit) * mult, 2)
    fees = round(e.fees_open + close_fee, 2)
    slippage = {k: round(e.slippage[k] + exit_slip[k], 2) for k in pricing.KINDS}
    return {
        "arm": e.arm,
        "symbol": pos.symbol,
        "session": e.session,
        "entry_minute": e.minute,
        "expiry": e.expiry,
        "legs": [{"name": g.name, "right": g.right, "side": g.side, "strike": g.strike} for g in e.legs],
        "quantity": e.quantity,
        "entry_mid": e.prices["mid"],
        "exit_kind": kind,
        "exit_session": session,
        "exit_minute": minute,
        "exit_mid": round(exit_credit, 4),
        "gross": gross,
        "fees": fees,
        "settlement": round(settlement, 2),
        "slippage": slippage,
        "net": {k: round(gross - fees - settlement - slippage[k], 2) for k in pricing.KINDS},
    }
