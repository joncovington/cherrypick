"""The rules engine: `decide` turns an arm and a snapshot into an entry or the reason there is none;
`manage` says whether an open position should be closed now; `close_position` prices the close and
returns the trade. Pure and deterministic: the same arm and snapshot give the same answer from the
backtest driver and the paper loop, which differ only in how they BUILD the snapshot.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as cal
from cherrypick.core import entrychecks, signals

from . import gates as gates_mod
from . import indicators as ind
from . import pricing, sizing, strikes
from .model import Chain, Decision, Entry, Leg, Snapshot, hhmm

ET = ZoneInfo("America/New_York")
OHLC = ("open", "high", "low", "close")


# --------------------------------------------------------------------------- expiry and trigger
def resolve_expiry(arm: dict, snap: Snapshot) -> str | None:
    """The listed expiry the arm's rule names today, or None when none is listed. `at_least` and
    `between` count calendar days from the session; the others aim at a date and take the listed expiry
    nearest it within `tolerance`. `monthly` keeps only the standard monthlies."""
    rule = arm["expiry"]
    listed = sorted(e for e in snap.chains if e >= snap.session)
    if rule.get("monthly"):
        listed = [e for e in listed if cal.is_monthly_expiry(date.fromisoformat(e))]
    today = snap.session_date
    if rule["kind"] in ("at_least", "between"):
        lo = rule["days"] if rule["kind"] == "at_least" else rule["min"]
        hi = None if rule["kind"] == "at_least" else rule["max"]
        inside = [
            e
            for e in listed
            if lo <= (date.fromisoformat(e) - today).days
            and (hi is None or (date.fromisoformat(e) - today).days <= hi)
        ]
        if not inside:
            return None
        return inside[-1] if rule.get("prefer") == "last" else inside[0]
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


def event_labels(spec) -> set[str]:
    """Every event label a `core.signals` spec names, nested filters included."""
    out: set[str] = set()
    if isinstance(spec, dict):
        if spec.get("kind") == "event" and isinstance(spec.get("event"), str):
            out.add(spec["event"])
        for v in spec.values():
            out |= event_labels(v)
    elif isinstance(spec, list | tuple):
        for v in spec:
            out |= event_labels(v)
    return out


def _event_reach(spec) -> int:
    """Calendar days either side of the session an event signal's dates must cover: its largest offset
    in sessions, as calendar days with room for weekends and holidays."""
    days = [0]

    def walk(x):
        if isinstance(x, dict):
            if x.get("kind") == "event":
                days.append(int(x.get("days") or 0))
            for v in x.values():
                walk(v)
        elif isinstance(x, list | tuple):
            for v in x:
                walk(v)

    walk(spec)
    return 2 * max(days) + 10


def events_for(trigger: dict, snap: Snapshot) -> dict[str, list[str]] | None:
    """The event dates a trigger's signal reads, from the snapshot's calendar over the stretch it can
    reach: `{label: [ISO dates]}`, or None when it names no event. A label whose stretch is not wholly
    known is left out, so core refuses the signal rather than read the unknown days as quiet."""
    labels = event_labels(trigger.get("signal"))
    if not labels:
        return None
    if snap.events is None:
        return {}
    day, reach = date.fromisoformat(snap.session), timedelta(days=_event_reach(trigger.get("signal")))
    out = {}
    for label in sorted(labels):
        found = snap.events.dates(label, day - reach, day + reach, snap.symbol)
        if found is not None:
            out[label] = found
    return out


def trigger_due(entry: dict, snap: Snapshot) -> tuple[bool, object]:
    """A `trigger` entry (or a `signal` exit, the same shape): `core.entrychecks.entry_due` on the trail
    cut into `bar_minutes` bars completed by the snapshot's minute, with the calendar's dates for any
    event the signal names. The value is the minute it fired when due, else the reason it is not:
    `event_dates_unknown` when the calendar cannot say whether an event falls in reach."""
    minute = snap.minute
    if entry["trigger"].get("daily"):
        # Completed sessions only, and only whole bars: a day the feed holds no open (or high, low, close)
        # for is absent, never filled in, so a missing yesterday reads `stale_bars`.
        bars = signals.daily(
            [r for r in snap.daily if r["session"] < snap.session and all(r.get(k) is not None for k in OHLC)]
        )
        if entry["trigger"].get("developing"):
            # Today's bar as it stands: the session's open, the trail's high and low so far, the spot now.
            so_far = [(m, s) for m, s in snap.trail if m <= minute]
            bars.append(entrychecks.developing_bar(snap.session, snap.day_open, so_far, snap.spot))
    else:
        # Earlier sessions first (the snapshot carries them for an arm that reads an intraday signal),
        # then today's bars completed by now: what a signal study over the whole history sees.
        width = entry.get("bar_minutes", 1)
        bars = [b for day, tr in sorted(snap.prior.items()) for b in signals.from_trail(day, tr, width)]
        bars += signals.from_trail(
            snap.session, [(m, s) for m, s in snap.trail if m <= minute], width, until=minute
        )
        if entry.get("volume"):
            bars = signals.with_volume(bars, snap.volumes.get(str(entry["volume"]).upper(), {}))
    now = datetime.fromisoformat(snap.session).replace(hour=minute // 60, minute=minute % 60, tzinfo=ET)
    try:
        due = entrychecks.entry_due(
            entry["trigger"], now=now, bars=bars, events=events_for(entry["trigger"], snap)
        )
    except signals.MissingVolume:
        return False, "no_volume"
    except ValueError as exc:
        if "no dates given for event" in str(exc):
            return False, "event_dates_unknown"
        raise
    return due.due, due.fired if due.due else due.reason


def entry_due(arm: dict, snap: Snapshot) -> tuple[bool, object]:
    e = arm["entry"]
    if e["kind"] == "trigger":
        return trigger_due(e, snap)
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

    lim = arm["limits"]  # validated: max_open and per_day stated, no default here
    mine = snap.entries.get(arm["name"], [])
    if mine.count(snap.session) >= lim["per_day"]:
        return fail("entered_today")
    opened = [p for p in snap.open_positions if p.arm == arm["name"]]
    if len(opened) >= lim["max_open"]:
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
        hit = snap.events.occurs(label, snap.session_date, last, snap.symbol) if snap.events else None
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

    legs, why = place_legs(arm, snap, chain, fired=v)
    if legs is None:
        return fail(why)
    ok("strikes", {leg.name: leg.strike for leg in legs})

    quotes = pricing.quotes_for(legs, chain)
    prices = pricing.structure_prices(quotes) if quotes else None
    if prices is None:
        return fail("unpriced")
    if not pricing.within_bounds(legs, -prices["mid"]):
        return fail("unrealistic_price", prices["mid"])
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

    qty, how = sizing.count(
        arm, loss_mid, results=snap.results.get(arm["name"], []), open_positions=snap.open_positions
    )
    if qty < 1:
        return fail(how["why"], how)
    ok("size", qty if set(how) == {"base"} else {**how, "count": qty})
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


def place_legs(arm: dict, snap: Snapshot, chain: Chain, *, fired=None) -> tuple[list[Leg] | None, str | None]:
    """The legs `arm`'s strike rules place on `chain`, in its leg order, each against the legs before
    it: (legs, None), or (None, the failing check `strike:<leg>`)."""
    chosen: dict[str, float] = {}
    legs: list[Leg] = []
    for spec in arm["legs"]:
        k = strikes.pick(spec["strike"], spec["right"], snap, chain, chosen, fired=fired)
        if k is None or chain.get(k, spec["right"]) is None:
            return None, f"strike:{spec['name']}"
        chosen[spec["name"]] = k
        legs.append(Leg(spec["name"], spec["right"], spec["side"], k))
    return legs, None


def structure(arm: dict, snap: Snapshot) -> dict:
    """The structure `arm` would take on `snap`, read as `decide` reads it but without its timing,
    limits, event skips, gates or size: the expiry its rule names, the legs its strike rules place, the
    prices at the legs' quotes, and the payoff at expiry. {"expiry", "legs", "prices", "max_profit",
    "max_loss"} per share and the `chain` it was placed on, or {"why": the check that stopped it}. For
    the Builder's payoff chart."""
    expiry = resolve_expiry(arm, snap)
    if expiry is None or expiry not in snap.chains:
        return {"why": "no_expiry"}
    chain = snap.chains[expiry]
    due, fired = entry_due(arm, snap)
    legs, why = place_legs(arm, snap, chain, fired=fired if due else None)
    if legs is None:
        return {"why": why, "expiry": expiry}
    quotes = pricing.quotes_for(legs, chain)
    prices = pricing.structure_prices(quotes) if quotes else None
    if prices is None:
        return {"why": "unpriced", "expiry": expiry, "legs": legs}
    if not pricing.within_bounds(legs, -prices["mid"]):
        return {"why": "unrealistic_price", "expiry": expiry, "legs": legs}
    profit, loss = pricing.payoff_extremes(legs, prices["mid"])
    return {"expiry": expiry, "legs": legs, "prices": prices, "max_profit": profit, "max_loss": loss,
            "chain": chain}  # fmt: skip


def size(rule: dict, loss_per_share: float) -> int:
    """The rule's own count (`sizing.base`): a fixed count, or the largest whole number whose maximum
    loss stays within the budget, never fewer than the rule's `minimum`."""
    return sizing.base(rule, loss_per_share)


def risk(entry: Entry) -> float:
    """An entry's risk, whole-position dollars: what `OpenPosition.risk` holds."""
    return round(entry.max_loss * pricing.MULTIPLIER * entry.quantity, 2)


# --------------------------------------------------------------------------- holding
class Position:
    """An open position: the entry, plus what the exit rules read."""

    def __init__(self, entry: Entry, arm: dict, *, peak_pnl: float | None = None):
        self.entry = entry
        self.arm = arm["name"]
        self.symbol = arm["symbol"]
        self.exit_rules = arm.get("exit", {})
        #: The best open P&L at the mid `manage` has read (whole-position dollars), for a trailing stop.
        #: The paper ledger keeps it between ticks; the driver keeps the object.
        self.peak_pnl = peak_pnl

    @property
    def expiry(self) -> str:
        return self.entry.expiry


def _beyond_band(legs, chain: Chain) -> dict[tuple[float, str], float] | None:
    """The legs with no quote that lie beyond the strikes the chain holds on their side, each with the
    most it can be worth: the ask of the farthest strike held on that side, since an option further out
    is worth no more (`{}` when every leg is quoted). None when a leg is missing inside the band, or no
    strike on its side carries an ask: that leg has no reading at all.

    A store records a band of strikes around the spot, so on a day the market runs a far wing can
    leave it while the position is open; read as missing, no exit could fire for the rest of the day."""
    out: dict[tuple[float, str], float] = {}
    for leg in legs:
        q = chain.get(leg.strike, leg.right)
        if q is not None and q.get("ask"):
            continue
        held = [r for r in chain.rows if r["right"] == leg.right and r.get("ask") is not None]
        if not held:
            return None
        far = (
            max(held, key=lambda r: float(r["strike"]))
            if leg.right == "C"
            else min(held, key=lambda r: float(r["strike"]))
        )
        k = float(far["strike"])
        if not (leg.strike > k if leg.right == "C" else leg.strike < k):
            return None
        out[(float(leg.strike), leg.right)] = float(far["ask"])
    return out


class _Bounded:
    """A chain with each leg beyond its band priced at one end of what it can be worth: `low` puts the
    structure at the least it can be worth (a sold far leg at its cap, a bought one at 0), `high` at the
    most. The far legs' quotes are one price, bid and ask alike, and carry no delta."""

    def __init__(self, chain: Chain, legs, far: dict, *, low: bool):
        self.expiry, self.rows, self.strikes = chain.expiry, chain.rows, chain.strikes
        self._chain = chain
        self._far = {}
        sides = {(float(leg.strike), leg.right): leg.side for leg in legs}
        for key, cap in far.items():
            v = cap if (sides[key] < 0) == low else 0.0
            self._far[key] = {"strike": key[0], "right": key[1], "bid": v, "ask": v, "bounded": True}

    def get(self, strike: float, right: str) -> dict | None:
        return self._far.get((float(strike), right)) or self._chain.get(strike, right)


def _quoted(x: dict) -> bool:
    return bool(x["ask"]) or bool(x.get("bounded"))


def _credit_now(pos: Position, chain: Chain) -> float | None:
    """What the position's close would bring at mid (per share), None when a leg has no quote or the
    mid is one the structure cannot be worth (`pricing.within_bounds`): no reading, so no exit fires on
    it and the trailing stop's best does not move."""
    q = pricing.quotes_for(pos.entry.legs, chain)
    if q is None or not all(_quoted(x) for x, _ in q):
        return None
    now = sum(n * (x["bid"] + x["ask"]) / 2 for x, n in q)
    return now if pricing.within_bounds(pos.entry.legs, now) else None


def structure_width(pos: Position, chain: Chain) -> float | None:
    """The structure's bid/ask width per share (each leg's width times its size), None when a leg has
    no two-sided quote."""
    q = pricing.quotes_for(pos.entry.legs, chain)
    if q is None or not all(_quoted(x) for x, _ in q):
        return None
    return sum(abs(n) * (x["ask"] - x["bid"]) for x, n in q)


def _event_ahead(rule: dict, pos: Position, snap: Snapshot) -> tuple[str, bool] | None:
    """The first listed event the position must be out before, as (label, now): the next session's,
    closed at the rule's time, or today's when the position was carried into it from an earlier one (a
    close the session before was missed: a gap in the data, a late enrolment), closed at once. Unknown
    calendar days hold, as an entry's event check passes them."""
    if not snap.events:
        return None
    today = snap.session_date
    nxt = cal.next_trading_day(today)
    for label in rule.get("labels", []):
        if snap.session > pos.entry.session and snap.events.occurs(label, today, today, pos.symbol):
            return label, True
        if snap.events.occurs(label, nxt, nxt, pos.symbol):
            return label, False
    return None


def position_delta(pos: Position, chain: Chain) -> float | None:
    """The structure's delta per unit (each leg's quoted delta times its side), None when a leg is not
    listed or carries no delta: a missing delta is no reading, never zero."""
    total = 0.0
    for leg in pos.entry.legs:
        q = chain.get(leg.strike, leg.right)
        if q is None or q.get("delta") is None:
            return None
        total += leg.side * float(q["delta"])
    return total


def _trail_dollars(trail: dict | None, e) -> tuple[float, float] | None:
    """A trailing stop as (trail, activate) in whole-position dollars: as stated, or its percent of the
    entry's max profit. None when there is none, or a percent with no max profit to measure."""
    if not trail:
        return None
    if "dollars" in trail:
        return trail["dollars"], trail.get("activate_dollars", 0)
    if not e.max_profit:
        return None
    full = e.max_profit * pricing.MULTIPLIER * e.quantity
    return trail["pct"] / 100.0 * full, trail.get("activate_pct", 0) / 100.0 * full


def manage(pos: Position, snap: Snapshot) -> tuple[str, object] | None:
    """The exit due now, as (kind, detail), or None, checked in this order: a touch, avoid events, the
    stops (credit multiple, % of debit, dollars), a trailing stop, the profit targets (% of max profit,
    dollars), a delta limit, a signal, a DTE, then minutes before expiry; held to settlement when the
    arm has none. Dollar rules read the open P&L at the mid, whole-position dollars.

    - `max_spread: x`: while the structure's bid/ask is wider than $x a share, no exit is due and the
      trailing stop's peak is not moved: the close waits for a quote it can fill at, and settles at
      expiry if none comes.
    - `avoid_events: {"labels": [...], "before_close_min": n}`: closes n minutes (default 60) before
      the close on the session before a listed event (`_event_ahead`).
    - `stop_credit_multiple: m`: a credit entry closes when its loss reaches m times the credit
      received (a debit entry has no credit to measure, and this rule never fires on one).
    - `stop_debit_pct: p`: a debit entry closes when its loss reaches p% of the debit paid (never fires
      on a credit).
    - `stop_dollars: x` / `profit_dollars: x`: the open P&L reaches -x / +x.
    - `trailing_stop: {"dollars": d, "activate_dollars": a}`: once the open P&L has reached a (default
      0), closes when it falls d below its best. In percent, `{"pct": p, "activate_pct": a}`: the same
      rule with d and a as p% and a% of the trade's max profit (whole-position dollars); a structure
      with no max profit has nothing to measure, and the rule never fires on one.
    - `delta_limit: x`: closes when the structure's delta per unit reaches x either way.
    - `close_at_dte: n`: closes at the first tick of a session n or fewer calendar days before expiry.

    A `signal` exit is a trigger of the entry's shape (`{"trigger": ..., "bar_minutes": N}`) read on the
    position's underlying: due when it fires inside its window. On the entry day only a firing after the
    entry minute counts, and core's trigger takes the day's first firing in the window, so a signal that
    first fired before the entry does not exit that day.

    A leg beyond the band of strikes the chain holds (`_beyond_band`) is read at both ends of what it
    can be worth, and an exit is due only when it is due on both readings; the trailing stop's best
    moves on the lower one."""
    chain = snap.chains.get(pos.entry.expiry)
    far = _beyond_band(pos.entry.legs, chain) if chain is not None else None
    if not far:
        return _manage(pos, snap, chain, peak=True)
    low = _manage(pos, snap, _Bounded(chain, pos.entry.legs, far, low=True), peak=True)
    high = _manage(pos, snap, _Bounded(chain, pos.entry.legs, far, low=False), peak=False)
    return low if (low is not None and high is not None) else None


def _manage(pos: Position, snap: Snapshot, chain, *, peak: bool) -> tuple[str, object] | None:
    """`manage` on one reading of the chain; `peak` says whether it may move the trailing stop's best."""
    r, e = pos.exit_rules, pos.entry
    touch = r.get("touch")
    if touch:
        limit = e.spot * (1 + touch["pct"] / 100.0) if "pct" in touch else e.spot + touch["points"]
        crossed = snap.spot <= limit if (touch.get("pct", touch.get("points")) < 0) else snap.spot >= limit
        if crossed:
            return "touch", limit
    if r.get("max_spread") is not None and chain is not None:
        width = structure_width(pos, chain)
        if width is not None and width > r["max_spread"] + 1e-9:
            return None
    now = _credit_now(pos, chain) if chain is not None else None
    credit = e.prices["mid"]
    pnl = None if now is None else round((credit + now) * pricing.MULTIPLIER * e.quantity, 2)
    if peak and pnl is not None and (pos.peak_pnl is None or pnl > pos.peak_pnl):
        pos.peak_pnl = pnl
    avoid = r.get("avoid_events")
    if avoid:
        ahead = _event_ahead(avoid, pos, snap)
        if ahead and (ahead[1] or snap.minute >= snap.close_minute - avoid.get("before_close_min", 60)):
            return "event", ahead[0]
    if r.get("stop_credit_multiple") and now is not None and credit > 0:
        loss = -(credit + now)
        if loss >= r["stop_credit_multiple"] * credit - 1e-9:
            return "stop", loss
    if r.get("stop_debit_pct") and now is not None and credit < 0:
        loss = -(credit + now)
        if loss >= r["stop_debit_pct"] / 100.0 * -credit - 1e-9:
            return "stop", loss
    if r.get("stop_dollars") and pnl is not None and pnl <= -r["stop_dollars"] + 1e-9:
        return "stop", pnl
    trail = _trail_dollars(r.get("trailing_stop"), e)
    if trail and pnl is not None and pos.peak_pnl is not None and pos.peak_pnl >= trail[1] - 1e-9:
        if pnl <= pos.peak_pnl - trail[0] + 1e-9:
            return "trailing_stop", pnl
    if r.get("profit_target_pct") and now is not None and e.max_profit:
        gained = credit + now
        if gained >= r["profit_target_pct"] / 100.0 * e.max_profit - 1e-9:
            return "profit_target", gained
    if r.get("profit_dollars") and pnl is not None and pnl >= r["profit_dollars"] - 1e-9:
        return "profit_target", pnl
    if r.get("delta_limit") is not None and chain is not None:
        delta = position_delta(pos, chain)
        if delta is not None and abs(delta) >= r["delta_limit"] - 1e-12:
            return "delta", round(delta, 4)
    if r.get("signal"):
        due, fired = trigger_due(r["signal"], snap)
        if due and (snap.session > e.session or fired > e.minute):
            return "signal", fired
    if r.get("close_at_dte") is not None:
        dte = (date.fromisoformat(e.expiry) - date.fromisoformat(snap.session)).days
        if dte <= r["close_at_dte"] and snap.session > e.session:
            return "dte", dte
    if r.get("close_before_expiry_min") is not None and snap.session == e.expiry:
        if snap.minute >= snap.close_minute - r["close_before_expiry_min"]:
            return "time", snap.minute
    return None


def close_position(pos: Position, kind: str, snap: Snapshot, *, costs: dict | None = None) -> dict | None:
    """Close at `snap`'s quotes and return the trade, or None when the legs do not price yet or their
    mid is one the structure cannot be worth (`pricing.within_bounds`); either way the close is tried
    again on the next tick, and settles at expiry if none comes. Gross is mid to mid; each fill kind
    carries its own slippage and net. A leg beyond the chain's band (`_beyond_band`) closes at the end
    of its range that costs most: a sold far leg bought back at its cap, a bought one sold for 0."""
    e = pos.entry
    chain = snap.chains.get(e.expiry)
    far = _beyond_band(e.legs, chain) if chain is not None else None
    if far:
        chain = _Bounded(chain, e.legs, far, low=True)
    q = pricing.quotes_for(e.legs, chain) if chain is not None else None
    real = None if q is None else [(x, -n) for x, n in q if not x.get("bounded")]
    if real is None or (real and pricing.structure_prices(real) is None):
        return None
    exit_credit = sum(n * (x["bid"] + x["ask"]) / 2 for x, n in q)
    if not pricing.within_bounds(e.legs, exit_credit):
        return None
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
        "risk": risk(e),
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
