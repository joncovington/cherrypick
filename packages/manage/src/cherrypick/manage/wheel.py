"""The wheel view: each name's cycle of cash-secured put, assignment, covered calls and call-away,
linked from the broker's trade history and accounted for across every roll (docs/skate-plan.md step 5).

**Linking.** Orders are linked into chains the way `chains.py` links them, through the contracts they
open and close; a chain is a WHEEL chain when every option leg in it is one right sold to open or
bought to close (a short put and its rolls, a covered call and its rolls). A chain holding a long
option, or both rights, is some other trade and stays out. A call chain joins a wheel only when shares
are held as it opens; on margin a call sold with no shares is a naked call, not a wheel leg.

A cycle starts when a wheel put is sold to open, or shares arrive (bought, or delivered), on a name with
no cycle running. It takes in every wheel chain, share trade, delivery and dividend on that name until
it is flat (no shares, no open wheel option) with credits exceeding debits: then it is COMPLETE. A cycle
that goes flat at a loss is not complete; the next put on the name continues it, since that is what
wheeling a loser back is.

**Assignment** is matched option row to share row (`match_deliveries`): both Receive Deliver rows on
the same name (the history is the designated account's, one account, so account matches by
construction), the shares on the side the option implies (a put assigned or a call exercised brings
shares in, a call assigned or a put exercised takes them out), shares = contracts x 100, at the
strike, within ASSIGNMENT_WINDOW_DAYS, nearest first, each share row claimed once. A share delivery no
option explains, or an assignment with no share row, is FLAGGED and makes its cycle unreconciled; it
is never filled in. Options with a non-standard deliverable (after a split or merger) do not match,
which is the point.

**Reconciliation.** The open cycle of a name must replay to today's quantities: its shares, and each
option contract it touched, against the positions. A cycle that does not -- history that starts after
it did, a contract closed that the history never opened, an unmatched delivery -- is `unreconciled`,
with each reason listed, and its money is shown as far as the rows go but is not a basis anyone
should trade on. Shares held before the history begins are a lot with `basis_known: false`: no tax
basis, no wheel basis, no stock P&L, never a guessed price.

**Money** is the broker's: every cash figure is a row's own `value` (signed, a credit received is +),
never price x quantity; fees are the rows' fee fields, kept apart. Two bases, never one:

- tax basis: the price per share each lot was acquired at (an assignment at its strike, as the
  broker records the delivery), first in, first out. US tax rules fold an assigning put's premium
  into it; an IRA owes no tax on either, and the plan defines it as the assignment price, so the
  premium is kept in the wheel basis instead;
- wheel basis: what the shares held have cost once everything else in the cycle is counted:
  -(every cash flow in the cycle, option and stock, fees and dividends included) / shares held. A
  net-debit roll raises it.

Breakeven is the wheel basis with shares held, and with only puts open the put strike less the
cycle's net cash per share secured. Option and stock P&L are kept apart, realised and unrealised
apart, all gross of fees, which are their own figure; `pnl_net` is their sum with fees, and None when
any part is unknown. The skate yield is a COMPARISON figure: net P&L to date / time-weighted capital x
365 / days, capital being strike x 100 per open put and the shares' cost while held (a covered call
adds none); None under MIN_YIELD_DAYS, flagged past YIELD_CAP.

**Today's check**, for each short option a running cycle holds: the strike against the skate band for
its expiry (the snapshot's `skate_bands`, the band file's `names` block, moved to today's price as the
screen moves it; null while the snapshot carries none), tested or not (the price at or through the
strike), extrinsic value left at the mid, and any buy-to-close resting on the day's working orders.

Pure: positions, history rows and orders in, cycles out. Advice only; nothing here places an order.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

from cherrypick.manage import pricing
from cherrypick.manage.candidates import WORKING
from cherrypick.manage.chains import FEE_FIELDS

MULTIPLIER = pricing.MULTIPLIER
# An assignment's share row can land a business day or a weekend after the option row.
ASSIGNMENT_WINDOW_DAYS = 4
# Under a week, an annualised yield says more about the calendar than the trade.
MIN_YIELD_DAYS = 7
# Past this (500% a year), the yield is shown but marked: a comparison figure, never a forecast.
YIELD_CAP = 5.0
# Option deliveries that move shares; the rest (expiration, cash-settled) only close the contract.
SHARE_MOVING = {"Assignment", "Exercise"}
SHORT_SIDE = {"Sell to Open", "Buy to Close"}
EPS = 1e-9


# --------------------------------------------------------------------------- small helpers
def _num(v: Any) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _sum(values) -> float | None:
    """A sum that is unknown when any part is: None is never zero."""
    total = 0.0
    for v in values:
        if v is None:
            return None
        total += v
    return total


def _sym(value: Any) -> str:
    return " ".join(str(value or "").split())


def occ(symbol: str) -> dict[str, Any] | None:
    """'SOFI 261120P00016000' -> {expiration, right, strike}; None for anything that is not an option."""
    parts = symbol.split()
    if len(parts) != 2 or len(parts[1]) != 15 or parts[1][6] not in "CP":
        return None
    code = parts[1]
    try:
        strike = int(code[7:]) / 1000.0
        exp = date(2000 + int(code[:2]), int(code[2:4]), int(code[4:6]))
    except ValueError:
        return None
    return {"expiration": exp.isoformat(), "right": code[6], "strike": strike}


def _day(row: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(row.get("executed_at") or "")[:10])
    except ValueError:
        return None


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("executed_at") or ""), str(row.get("id") or ""))


def _signed(row: dict[str, Any]) -> float:
    """Position change: a buy adds, a sell subtracts (to open or to close alike)."""
    action = str(row.get("action") or row.get("transaction_sub_type") or "").lower()
    qty = abs(_num(row.get("quantity")) or 0.0)
    return qty if action.startswith("buy") else -qty


def _closes(row: dict[str, Any]) -> bool:
    return "close" in str(row.get("action") or "").lower()


def _fees(row: dict[str, Any]) -> float:
    return sum(_num(row.get(k)) or 0.0 for k in FEE_FIELDS)


def _is_option(row: dict[str, Any]) -> bool:
    return occ(_sym(row.get("symbol"))) is not None


def _is_share(row: dict[str, Any], underlying: str) -> bool:
    kind = str(row.get("instrument_type") or "")
    return kind == "Equity" or (not kind and _sym(row.get("symbol")).upper() == underlying)


def _is_dividend(row: dict[str, Any]) -> bool:
    return "dividend" in str(row.get("transaction_sub_type") or "").lower()


# --------------------------------------------------------------------------- assignment matching
def _share_side(option_row: dict[str, Any]) -> int:
    """+1 when the delivery brings shares in, -1 when it takes them out, 0 when it moves none."""
    sub = str(option_row.get("transaction_sub_type") or "")
    if sub not in SHARE_MOVING:
        return 0
    right = (occ(_sym(option_row.get("symbol"))) or {}).get("right")
    if sub == "Assignment":  # the short side: a put buys the shares, a call delivers them
        return 1 if right == "P" else -1
    return 1 if right == "C" else -1  # exercised long: a call buys, a put sells


def _share_price(row: dict[str, Any]) -> float | None:
    """Per share, from the broker's price, else its cash over the quantity."""
    price = _num(row.get("price"))
    if price is not None and price > 0:
        return price
    value, qty = _num(row.get("value")), _num(row.get("quantity"))
    return abs(value) / abs(qty) if value is not None and qty else None


def match_deliveries(
    option_rows: list[dict[str, Any]], share_rows: list[dict[str, Any]]
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """(matched (option row, share row) pairs, unmatched option rows, unmatched share rows).

    Only option rows that move shares (assignment, exercise) are matched; share rows are the name's
    Receive Deliver equity rows. Each share row is claimed once, the nearest in time first.
    """
    claimed: set[int] = set()
    pairs, lonely = [], []
    for opt in sorted(option_rows, key=_key):
        side = _share_side(opt)
        if not side:
            continue
        leg = occ(_sym(opt.get("symbol"))) or {}
        contracts = abs(_num(opt.get("quantity")) or 0.0)
        when = _day(opt)
        best, gap = None, None
        for i, sh in enumerate(share_rows):
            if i in claimed:
                continue
            d = _day(sh)
            if when is None or d is None or abs((d - when).days) > ASSIGNMENT_WINDOW_DAYS:
                continue
            if (1 if _signed(sh) > 0 else -1) != side:
                continue
            if abs(abs(_num(sh.get("quantity")) or 0.0) - contracts * MULTIPLIER) > EPS:
                continue
            price, strike = _share_price(sh), leg.get("strike")
            if price is None or strike is None or abs(price - strike) > 0.005:
                continue
            g = abs((d - when).days)
            if gap is None or g < gap:
                best, gap = i, g
        if best is None:
            lonely.append(opt)
        else:
            claimed.add(best)
            pairs.append((opt, share_rows[best]))
    loose = [sh for i, sh in enumerate(share_rows) if i not in claimed]
    return pairs, lonely, loose


# --------------------------------------------------------------------------- order components
def _components(trades: list[dict[str, Any]]) -> tuple[dict[Any, int], dict[int, bool]]:
    """({order_id: component}, {component: is a wheel chain}) over option trade rows.

    Orders are linked through the contracts they touch (an order that closes a contract belongs with
    the one that opened it; a roll links the old contract to the new). A component is a wheel chain
    when every option leg in it is one right, sold to open or bought to close.
    """
    parent: dict[Any, Any] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_symbol: dict[str, list] = defaultdict(list)
    for r in trades:
        find(r["order_id"])
        by_symbol[_sym(r.get("symbol"))].append(r["order_id"])
    for orders in by_symbol.values():
        for o in orders[1:]:
            parent[find(o)] = find(orders[0])
    roots = {}
    comp_of = {}
    for o in list(parent):
        comp_of[o] = roots.setdefault(find(o), len(roots))
    rights: dict[int, set] = defaultdict(set)
    short_only: dict[int, bool] = defaultdict(lambda: True)
    for r in trades:
        c = comp_of[r["order_id"]]
        rights[c].add((occ(_sym(r.get("symbol"))) or {}).get("right"))
        if str(r.get("action") or "") not in SHORT_SIDE:
            short_only[c] = False
    wheel = {c: short_only[c] and len(rights[c]) == 1 for c in set(comp_of.values())}
    return comp_of, wheel


# --------------------------------------------------------------------------- one cycle
class _Cycle:
    def __init__(self, underlying: str, start: date):
        self.underlying = underlying
        self.start = start
        self.end: date | None = None
        self.rows: list[dict[str, Any]] = []  # cash rows: {kind, symbol, cash, fees, date}
        self.events: list[dict[str, Any]] = []
        self.positions: dict[str, float] = defaultdict(float)  # option contracts, by symbol
        self.lots: list[dict[str, Any]] = []  # open share lots, FIFO
        self.shares = 0.0
        self.stock_realised: float | None = 0.0
        self.problems: list[str] = []
        self.capital_days: float | None = 0.0
        self.last: date = start
        self.assignments = 0
        self.called_away = 0

    # ---- state
    def flat(self) -> bool:
        return abs(self.shares) < EPS and all(abs(q) < EPS for q in self.positions.values())

    def net(self) -> float | None:
        return _sum(r["cash"] for r in self.rows) if self.rows else 0.0

    def fees(self) -> float:
        return sum(r["fees"] for r in self.rows)

    def capital(self) -> float | None:
        puts = 0.0
        for sym, q in self.positions.items():
            leg = occ(sym)
            if leg and leg["right"] == "P" and q < 0:
                puts += leg["strike"] * MULTIPLIER * -q
        stock = _sum(lot["shares"] * lot["price"] if lot["price"] is not None else None for lot in self.lots)
        return None if stock is None else puts + stock

    def advance(self, day: date) -> None:
        """Accrue capital x days up to `day`, at what was deployed since the last event."""
        days = (day - self.last).days
        if days > 0:
            cap = self.capital()
            self.capital_days = (
                None if cap is None or self.capital_days is None else self.capital_days + cap * days
            )
            self.last = day

    # ---- what happens
    def option(self, row: dict[str, Any]) -> None:
        sym = _sym(row.get("symbol"))
        before = self.positions[sym]
        change = _signed(row)
        after = before + change
        if _closes(row) and (abs(before) < EPS or (before > 0) != (after > 0) and abs(after) > EPS):
            # A close the history never saw opened: the contract (or part of it) predates the window.
            self.problems.append(f"{_label(sym)} was closed but its opening predates the history")
            after = 0.0
        self.positions[sym] = after
        self._cash(row, "option", sym)

    def share(self, row: dict[str, Any], label: str) -> None:
        change = _signed(row)
        value = _num(row.get("value"))
        if change > 0:
            price = -value / change if value is not None else None
            self.lots.append({"date": str(_day(row)), "shares": change, "price": price, "source": label})
        else:
            self._sell(-change, value)
        self.shares += change
        if self.shares < -EPS:
            self.problems.append("more shares left than the history brought in")
            self.shares = 0.0
        self._cash(row, "stock", self.underlying)

    def _sell(self, qty: float, proceeds: float | None) -> None:
        cost: float | None = 0.0
        left = qty
        while left > EPS and self.lots:
            lot = self.lots[0]
            take = min(left, lot["shares"])
            cost = None if cost is None or lot["price"] is None else cost + take * lot["price"]
            lot["shares"] -= take
            left -= take
            if lot["shares"] < EPS:
                self.lots.pop(0)
        if left > EPS:
            cost = None
        gain = None if cost is None or proceeds is None else proceeds - cost
        self.stock_realised = (
            None if gain is None or self.stock_realised is None else self.stock_realised + gain
        )

    def dividend(self, row: dict[str, Any]) -> None:
        self._cash(row, "dividend", self.underlying)

    def _cash(self, row: dict[str, Any], kind: str, symbol: str) -> None:
        self.rows.append(
            {
                "kind": kind,
                "symbol": symbol,
                "cash": _num(row.get("value")),
                "fees": _fees(row),
                "date": str(_day(row)),
            }
        )


def _label(symbol: str) -> str:
    leg = occ(symbol)
    return f"{leg['expiration']} {leg['strike']:g}{leg['right']}" if leg else symbol


ACTION_CODE = {"Buy to Open": "BTO", "Sell to Open": "STO", "Buy to Close": "BTC", "Sell to Close": "STC"}


def _row_text(row: dict[str, Any], underlying: str) -> str:
    sym = _sym(row.get("symbol"))
    qty = abs(_num(row.get("quantity")) or 0.0)
    action = str(row.get("action") or "")
    what = _label(sym) if occ(sym) else f"{underlying} shares"
    price = _num(row.get("price"))
    sub = str(row.get("transaction_sub_type") or "")
    head = (
        sub.lower()
        if row.get("transaction_type") == "Receive Deliver" and sub
        else ACTION_CODE.get(action, action)
    )
    return f"{head} {qty:g} {what}" + (f" @ {price:.2f}" if price else "")


# --------------------------------------------------------------------------- one name
def _name(
    underlying: str,
    rows: list[dict[str, Any]],
    held: dict[str, float],
    session: date,
) -> dict[str, Any]:
    """Every cycle on one name, and the rows on it that belong to no cycle or could not be explained."""
    rows = sorted(rows, key=_key)
    trades = [r for r in rows if r.get("transaction_type") == "Trade" and r.get("order_id") is not None]
    option_trades = [r for r in trades if _is_option(r)]
    share_trades = [r for r in trades if _is_share(r, underlying)]
    deliveries = [r for r in rows if r.get("transaction_type") == "Receive Deliver"]
    option_deliveries = [r for r in deliveries if _is_option(r)]
    share_deliveries = [r for r in deliveries if _is_share(r, underlying)]
    dividends = [r for r in rows if _is_dividend(r)]
    comp_of, wheel = _components(option_trades)
    comp_symbols: dict[int, set] = defaultdict(set)
    for r in option_trades:
        comp_symbols[comp_of[r["order_id"]]].add(_sym(r.get("symbol")))
    comp_of_symbol = {s: c for c, syms in comp_symbols.items() for s in syms}

    pairs, lonely_options, loose_shares = match_deliveries(option_deliveries, share_deliveries)
    paired_option = {id(opt) for opt, _ in pairs}
    lonely_ids = {id(r) for r in lonely_options}
    flagged = [
        {"date": str(_day(r)), "kind": "share delivery", "row": _row_text(r, underlying)}
        for r in loose_shares
    ] + [
        {"date": str(_day(r)), "kind": "assignment without shares", "row": _row_text(r, underlying)}
        for r in lonely_options
    ]

    # Events, oldest first: an order's option rows together, a matched assignment as one event.
    events: list[tuple[tuple, str, Any]] = []
    by_order: dict[Any, list] = defaultdict(list)
    for r in option_trades:
        by_order[r["order_id"]].append(r)
    for order, legs in by_order.items():
        events.append((min(_key(r) for r in legs), "order", (order, legs)))
    for r in share_trades:
        events.append((_key(r), "shares", r))
    for r in option_deliveries:
        if id(r) in paired_option:
            continue
        events.append((_key(r), "option delivery", r))
    for opt, sh in pairs:
        events.append((min(_key(opt), _key(sh)), "assignment", (opt, sh)))
    for r in loose_shares:
        events.append((_key(r), "share delivery", r))
    for r in dividends:
        events.append((_key(r), "dividend", r))
    events.sort(key=lambda e: e[0])

    # Shares held before the history began: today's less what the history moved.
    moved = sum(_signed(r) for r in share_trades + share_deliveries)
    held_shares = held.get(underlying, 0.0)
    before = held_shares - moved
    cycles: list[_Cycle] = []
    outside: list[str] = []
    cur: _Cycle | None = None
    excluded: set[int] = {c for c, ok in wheel.items() if not ok}
    decided: set[int] = set()
    shares_now = 0.0
    if before > EPS:
        first = _day(rows[0]) if rows else session
        cur = _Cycle(underlying, first or session)
        cur.lots.append({"date": None, "shares": before, "price": None, "source": "before the history"})
        cur.shares = shares_now = before
        cur.events.append({"date": None, "what": f"{before:g} shares held before the history", "cash": None})
    elif before < -EPS:
        outside.append(f"the history moves {moved:g} shares but {held_shares:g} are held")

    def wheel_option(sym: str) -> bool | None:
        """True when the contract is a wheel leg, False when another trade's, None when the history
        never traded it (opened before the window)."""
        c = comp_of_symbol.get(sym)
        if c is None:
            return None
        return c not in excluded

    for when, kind, payload in events:
        day = date.fromisoformat(when[0][:10]) if when[0] else session
        if kind == "order":
            order, legs = payload
            c = comp_of[order]
            if c in excluded:
                continue
            if c not in decided:
                decided.add(c)
                right = (occ(_sym(legs[0].get("symbol"))) or {}).get("right")
                if right == "C" and shares_now <= EPS:
                    excluded.add(c)  # a call sold with no shares behind it is not a wheel leg
                    outside.append(
                        f"{', '.join(_label(_sym(r.get('symbol'))) for r in legs)}: a call with no shares"
                    )
                    continue
            opens_put = any(
                r.get("action") == "Sell to Open" and (occ(_sym(r.get("symbol"))) or {}).get("right") == "P"
                for r in legs
            )
            if cur is None:
                if not opens_put:
                    outside.append(
                        f"{day}: {'; '.join(_row_text(r, underlying) for r in legs)} (no cycle running)"
                    )
                    continue
                cur = _Cycle(underlying, day)
            cur.advance(day)
            for r in legs:
                cur.option(r)
            cash = _sum(_num(r.get("value")) for r in legs)
            roles = {"open" if not _closes(r) else "close" for r in legs}
            cur.events.append(
                {
                    "date": str(day),
                    "what": "roll" if roles == {"open", "close"} else roles.pop(),
                    "legs": [_row_text(r, underlying) for r in legs],
                    "cash": cash,
                    "fees": sum(_fees(r) for r in legs),
                }
            )
        elif kind in ("option delivery", "assignment"):
            opt, sh = payload if kind == "assignment" else (payload, None)
            sym = _sym(opt.get("symbol"))
            mine = wheel_option(sym)
            if sh is None and mine is False:
                continue  # another trade's contract expiring or settling: not this view's
            if cur is None and sh is None:
                if mine is not False:
                    outside.append(f"{day}: {_row_text(opt, underlying)} (no cycle running)")
                continue
            if cur is None:
                cur = _Cycle(underlying, day)
            cur.advance(day)
            if mine is False:
                cur.problems.append(f"shares delivered by {_label(sym)}, a leg of another trade on the name")
            else:
                cur.option(opt)
            if id(opt) in lonely_ids:
                cur.problems.append(
                    f"{_label(sym)} was assigned or exercised but no share delivery matches it"
                )
            event = {"date": str(day), "what": kind, "legs": [_row_text(opt, underlying)], "fees": _fees(opt)}
            if sh is not None:
                cur.share(sh, "assignment")
                shares_now = cur.shares
                event["legs"].append(_row_text(sh, underlying))
                event["fees"] += _fees(sh)
                if _signed(sh) > 0:
                    cur.assignments += 1
                else:
                    cur.called_away += 1
            event["cash"] = _sum(_num(r.get("value")) for r in ([opt, sh] if sh is not None else [opt]))
            cur.events.append(event)
        elif kind in ("shares", "share delivery"):
            r = payload
            if cur is None:
                if _signed(r) <= 0:
                    outside.append(f"{day}: {_row_text(r, underlying)} (no cycle running)")
                    shares_now += _signed(r)
                    continue
                cur = _Cycle(underlying, day)
            cur.advance(day)
            if kind == "share delivery":
                cur.problems.append(f"{day}: a share delivery no assignment or exercise explains")
            cur.share(r, kind)
            shares_now = cur.shares
            cur.events.append(
                {
                    "date": str(day),
                    "what": "bought"
                    if kind == "shares" and _signed(r) > 0
                    else ("sold" if kind == "shares" else kind),
                    "legs": [_row_text(r, underlying)],
                    "cash": _num(r.get("value")),
                    "fees": _fees(r),
                }
            )
        elif kind == "dividend":
            if cur is None or cur.shares <= EPS:
                continue
            cur.advance(day)
            cur.dividend(payload)
            cur.events.append(
                {"date": str(day), "what": "dividend", "cash": _num(payload.get("value")), "fees": 0.0}
            )

        if cur is not None and cur.flat():
            net = cur.net()
            if net is not None and net + cur.fees() > 0:
                cur.end = day
                cycles.append(cur)
                cur = None

    if cur is not None:
        cur.advance(session)
        cycles.append(cur)

    # ---- the running cycle against today's positions
    running = cycles[-1] if cycles and cycles[-1].end is None else None
    longs = any(q > 0 for s, q in held.items() if occ(s))
    unexplained = [
        s
        for s, q in held.items()
        if occ(s)
        and q < 0
        and wheel_option(s) is None
        and not longs
        and (occ(s)["right"] == "P" or held_shares > EPS)
    ]
    if unexplained and running is None:
        running = _Cycle(underlying, session)
        cycles.append(running)
    if running is not None:
        if abs(running.shares - held_shares) > EPS:
            running.problems.append(f"replays to {running.shares:g} shares, {held_shares:g} held")
        for sym, q in running.positions.items():
            if abs(q - held.get(sym, 0.0)) > EPS:
                running.problems.append(f"{_label(sym)}: replays to {q:+g}, {held.get(sym, 0.0):+g} held")
        for sym in unexplained:
            running.problems.append(f"{_label(sym)} is held but the history never traded it")
            running.positions[sym] = held[sym]
    return {"cycles": cycles, "outside": outside, "flagged": flagged}


# --------------------------------------------------------------------------- accounting
def _accounting(cy: _Cycle, held_legs: dict[str, dict], session: date) -> dict[str, Any]:
    end = cy.end or session
    cash = [r["cash"] for r in cy.rows]
    known = all(c is not None for c in cash)
    credits = sum(c for c in cash if c is not None and c > 0) if known else None
    debits = sum(c for c in cash if c is not None and c < 0) if known else None
    fees = cy.fees()
    dividends = _sum(r["cash"] for r in cy.rows if r["kind"] == "dividend")
    net_premium = _sum(r["cash"] for r in cy.rows if r["kind"] == "option")
    net_cash = _sum(cash)

    # Option P&L: contracts now flat are realised; open ones are their own cash plus today's mark.
    realised: float | None = 0.0
    unrealised: float | None = 0.0
    by_symbol: dict[str, list] = defaultdict(list)
    for r in cy.rows:
        if r["kind"] == "option":
            by_symbol[r["symbol"]].append(r["cash"])
    for sym, flows in by_symbol.items():
        flow = _sum(flows)
        if abs(cy.positions.get(sym, 0.0)) < EPS:
            realised = None if realised is None or flow is None else realised + flow
        else:
            mark = _num((held_legs.get(sym) or {}).get("value"))
            part = None if flow is None or mark is None else flow + mark
            unrealised = None if unrealised is None or part is None else unrealised + part
    for sym, q in cy.positions.items():
        if abs(q) > EPS and sym not in by_symbol:  # held, never traded in the window
            unrealised = None

    # Stock P&L: lots sold are realised (first in, first out); lots held are marked.
    basis_known = all(lot["price"] is not None for lot in cy.lots) and cy.stock_realised is not None
    cost_held = _sum(lot["shares"] * lot["price"] if lot["price"] is not None else None for lot in cy.lots)
    stock_unrealised: float | None = 0.0
    if cy.shares > EPS:
        mark = _num((held_legs.get(cy.underlying) or {}).get("value"))
        stock_unrealised = None if mark is None or cost_held is None else mark - cost_held
    tax_basis = cost_held / cy.shares if cy.shares > EPS and cost_held is not None else None

    total_in = None if net_cash is None else net_cash + fees
    wheel_basis = None
    breakeven = None
    if cy.shares > EPS:
        if basis_known and total_in is not None:
            wheel_basis = -total_in / cy.shares
            breakeven = {"price": wheel_basis, "basis": "wheel basis"}
    else:
        puts = [
            (occ(s), -q) for s, q in cy.positions.items() if q < -EPS and (occ(s) or {}).get("right") == "P"
        ]
        contracts = sum(q for _, q in puts)
        if contracts and total_in is not None:
            strike = sum(leg["strike"] * q for leg, q in puts) / contracts
            breakeven = {
                "price": strike - total_in / (contracts * MULTIPLIER),
                "basis": "put strike less net cash",
            }

    pnl_net = _sum([realised, unrealised, cy.stock_realised, stock_unrealised, dividends, fees])
    days = (end - cy.start).days
    avg_capital = cy.capital_days / days if cy.capital_days is not None and days > 0 else None
    yld = None
    if pnl_net is not None and avg_capital and days >= MIN_YIELD_DAYS:
        yld = pnl_net / avg_capital * 365.0 / days

    flat = cy.flat()
    status = "complete" if cy.end is not None else "open"
    if cy.problems:
        status = "unreconciled"
    return {
        "underlying": cy.underlying,
        "status": status,
        "reconciled": not cy.problems,
        "problems": list(dict.fromkeys(cy.problems)),
        "flat": flat,
        "start": cy.start.isoformat(),
        # Shares held before the history: the cycle began earlier than `start`, by an unknown amount.
        "start_known": all(lot["date"] is not None for lot in cy.lots),
        "end": cy.end.isoformat() if cy.end else None,
        "days": days,
        "assignments": cy.assignments,
        "called_away": cy.called_away,
        "shares": cy.shares,
        "basis_known": basis_known,
        "lots": [dict(lot) for lot in cy.lots],
        "open_options": [
            {"symbol": s, "leg": _label(s), "quantity": q}
            for s, q in sorted(cy.positions.items())
            if abs(q) > EPS
        ],
        "credits": credits,
        "debits": debits,
        "net_cash": net_cash,
        "fees": fees,
        "net_premium": net_premium,
        "dividends": dividends,
        "tax_basis": tax_basis,
        "wheel_basis": wheel_basis,
        "breakeven": breakeven,
        "option_pnl": {"realised": realised, "unrealised": unrealised},
        "stock_pnl": {"realised": cy.stock_realised, "unrealised": stock_unrealised},
        "pnl_net": pnl_net,
        "skate_yield": {
            "value": yld,
            "capped": yld is not None and abs(yld) > YIELD_CAP,
            "capital": avg_capital,
            "basis": "comparison figure: net P&L / time-weighted capital x 365 / days",
        },
        "events": cy.events,
    }


# --------------------------------------------------------------------------- today's check
def _band(bands: dict[str, Any] | None, underlying: str, right: str, expiry: str, spot: float | None):
    """The skate band for this name, side and expiry, moved to today's price; None with why when absent."""
    name = ((bands or {}).get("names") or {}).get(underlying)
    if not name:
        return None, "no band for the name in the snapshot"
    row = next(
        (r for r in name.get("rows") or [] if r.get("side") == right and r.get("expiry") == expiry), None
    )
    if not row:
        return None, "no band for this expiry"
    base = _num(name.get("spot"))
    shift = spot - base if spot and base else 0.0
    out = {"day": bands.get("day"), "shift": shift}
    for which, b in (row.get("bands") or {}).items():
        level = _num((b or {}).get("band"))
        out[which] = None if level is None else level + shift
    return out, None


def _resting(orders: list[dict[str, Any]] | None, symbol: str) -> list[dict[str, Any]]:
    out = []
    for o in orders or []:
        if o.get("status") not in WORKING:
            continue
        for leg in o.get("legs") or []:
            if _sym(leg.get("symbol")) == symbol and leg.get("action") == "Buy to Close":
                left = _num(leg.get("remaining_quantity"))
                out.append(
                    {
                        "order_id": o.get("id"),
                        "status": o.get("status"),
                        "price": _num(o.get("price")),
                        "quantity": left if left is not None else _num(leg.get("quantity")),
                        "time_in_force": o.get("time_in_force"),
                        "legs": len(o.get("legs") or []),
                    }
                )
    return out


def check(
    cycle: dict[str, Any],
    held_legs: dict[str, dict],
    spot: float | None,
    session: date,
    bands: dict[str, Any] | None,
    orders: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Each short option the cycle holds, judged against today: band, tested, extrinsic, resting close."""
    out = []
    for o in cycle["open_options"]:
        leg = occ(o["symbol"])
        if not leg or o["quantity"] >= 0:
            continue
        raw = held_legs.get(o["symbol"]) or {}
        mid = _num(raw.get("mid")) if raw.get("priced", True) else None
        strike, right = leg["strike"], leg["right"]
        intrinsic = pricing.intrinsic(spot, strike, right) if spot else None
        extrinsic = mid - intrinsic if mid is not None and intrinsic is not None else None
        band, why = _band(bands, cycle["underlying"], right, leg["expiration"], spot)
        beyond = None
        if band:
            beyond = {
                which: None
                if band.get(which) is None
                else (strike >= band[which] if right == "C" else strike <= band[which])
                for which in ("fixed", "by_expiry")
                if which in band
            }
        resting = _resting(orders, o["symbol"])
        btc = min((r["price"] for r in resting if r["price"] is not None), default=None)
        out.append(
            {
                "leg": o["leg"],
                "symbol": o["symbol"],
                "right": right,
                "strike": strike,
                "expiration": leg["expiration"],
                "dte": (date.fromisoformat(leg["expiration"]) - session).days,
                "quantity": o["quantity"],
                "spot": spot,
                "mid": mid,
                # The price it was sold at (the positions' own average for this contract), and whether
                # its market is wide: the plan's buy-to-close is $0.05 there, $0.03 otherwise.
                "open_price": _num(raw.get("open_price")),
                "wide": raw.get("wide"),
                "tested": None if spot is None else (spot >= strike if right == "C" else spot <= strike),
                "intrinsic": intrinsic,
                "extrinsic": extrinsic,
                "extrinsic_dollars": None if extrinsic is None else extrinsic * MULTIPLIER * -o["quantity"],
                "band": band,
                "band_missing": why,
                "beyond_band": beyond,
                "resting_btc": resting,
                "btc_price": btc,
                "mark_at_or_under_btc": None if btc is None or mid is None else mid <= btc + EPS,
            }
        )
    return out


# --------------------------------------------------------------------------- the account
def build(
    underlyings: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]] | None,
    session: date,
    metrics: dict[str, Any] | None = None,
    orders: list[dict[str, Any]] | None = None,
    bands: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The account's wheel view: {"cycles": [...], "flagged": [...], "outside": {...}}.

    `underlyings` is the positions' per-name legs (raw, as the snapshot holds them), `history` the
    trade history by name. A name appears when it holds a short put, shares or a short call, or its
    history holds a cycle.
    """
    history = history or {}
    metrics = metrics or {}
    legs_by_name: dict[str, dict[str, dict]] = {}
    for u in underlyings or []:
        name = str(u.get("underlying") or "").upper()
        for leg in u.get("legs") or []:
            sym = _sym(leg.get("symbol"))
            legs_by_name.setdefault(name, {})[sym.upper() if not occ(sym) else sym] = leg
    cycles, flagged, outside = [], [], {}
    for name in sorted(set(history) | set(legs_by_name)):
        held_legs = legs_by_name.get(name, {})
        held = {s: _num(leg.get("quantity")) or 0.0 for s, leg in held_legs.items()}
        found = _name(name, history.get(name) or [], held, session)
        spot = _num((metrics.get(name) or {}).get("last"))
        for cy in found["cycles"]:
            view = _accounting(cy, held_legs, session)
            view["today"] = (
                check(view, held_legs, spot, session, bands, orders) if view["end"] is None else []
            )
            cycles.append(view)
        flagged += [{"underlying": name, **f} for f in found["flagged"]]
        if found["outside"]:
            outside[name] = found["outside"]
    cycles.sort(key=lambda c: (c["end"] is not None, c["underlying"], c["start"]))
    return {
        "cycles": cycles,
        "flagged": flagged,
        "outside": outside,
        "bands_day": (bands or {}).get("day"),
        "bands_missing": None if bands else "the snapshot carries no skate bands",
    }


# --------------------------------------------------------------------------- what the rules propose
def _quoted(income_entry: dict[str, Any] | None, right: str) -> list[dict[str, Any]]:
    """The income reading's two-sided quotes for one right on its expiry, with mids."""
    out = []
    for o in (income_entry or {}).get("options") or []:
        bid, ask, k = _num(o.get("bid")), _num(o.get("ask")), _num(o.get("strike"))
        if o.get("right") != right or k is None or bid is None or ask is None or ask <= 0 or bid < 0:
            continue
        out.append({"strike": k, "bid": bid, "ask": ask, "mid": (bid + ask) / 2.0})
    return out


def _further(k: float, strike: float, right: str) -> bool:
    """`k` is at `strike` or further from the money: never a roll toward it."""
    return k >= strike - EPS if right == "C" else k <= strike + EPS


def roll_out_for(roll_out: dict[str, Any] | None, underlying: str, right: str) -> list[dict[str, Any]]:
    """The snapshot's roll-out chains (v14 `roll_out_chains`) for one name and right that quote
    anything, nearest expiry first; empty for a snapshot without them."""
    chains = (roll_out or {}).get("chains") if isinstance(roll_out, dict) else None
    out = [
        {**c, "taken_at": roll_out.get("taken_at")}
        for c in (chains or {}).values()
        if isinstance(c, dict)
        and str(c.get("underlying") or "").upper() == underlying.upper()
        and c.get("right") == right
        and c.get("expiration")
        and c.get("options")
    ]
    return sorted(out, key=lambda c: str(c["expiration"]))


def roll_candidates(
    today: dict[str, Any], income_entry: dict[str, Any] | None, chains: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The tested short's two rolls out in time, on the snapshot's quoted chains.

    - net-zero: buy the short back at today's mid and sell, on the later expiry, the strike furthest
      from the money whose mid still pays for the buy-back. A put's new strike is also capped at the
      old strike + its original premium - the buy-back (a roll may not pay for itself with risk).
    - premium gambit: the buy-back rests at the price the short was sold for (it fills only if the
      stock comes back), and the same strike, or the next one further out, is sold on the later expiry.

    `chains` are the roll-out chains read for this name and right (v14, `roll_out_for`): the next
    expiries after the short, quoted that session. The roll goes to the NEAREST of them on which the
    net-zero roll is priced, else the nearest on which anything at or beyond the strike is quoted; the
    expiries looked at are listed in `considered`. Without them (or when none quotes a strike at or
    beyond the short's) the roll is priced, as before v14, on the previous afternoon's income reading:
    listed strikes, two-sided quotes, the cycle monthly.

    Neither ever moves the strike toward the money, so a roll never closes on the other side of a
    wheel. Each is None, with why, when the chain cannot price it.
    """
    later = [c for c in chains or [] if str(c["expiration"]) > today["expiration"]]
    tried = []
    for c in later:
        entry = {"expiry": c["expiration"], "options": c["options"], "taken_at": c.get("taken_at")}
        got = _roll_on(today, entry)
        tried.append(got)
        if got.get("net_zero"):
            break
    usable = [t for t in tried if t.get("net_zero") or t.get("premium_gambit")]
    if usable:
        pick = next((t for t in usable if t.get("net_zero")), usable[0])
        pick["source"] = "roll-out chains"
        pick["considered"] = [
            {"expiration": t["expiration"], "net_zero": bool(t.get("net_zero"))} for t in tried
        ]
        return pick
    return _roll_on(today, income_entry)


def _roll_on(today: dict[str, Any], income_entry: dict[str, Any] | None) -> dict[str, Any]:
    """Both rolls on one quoted expiry (`income_entry`: its `expiry`, `options` and `taken_at`)."""
    right, strike = today["right"], today["strike"]
    out: dict[str, Any] = {"net_zero": None, "premium_gambit": None, "why": None, "expiration": None}
    expiry = (income_entry or {}).get("expiry")
    if not income_entry or not expiry:
        out["why"] = "the snapshot's income reading quotes no chain for the name"
        return out
    if str(expiry) <= today["expiration"]:
        out["why"] = f"the quoted chain's expiry ({expiry}) is not later than the short's"
        return out
    out["expiration"] = expiry
    out["quoted_at"] = income_entry.get("taken_at")
    quotes = [q for q in _quoted(income_entry, right) if _further(q["strike"], strike, right)]
    if not quotes:
        out["why"] = f"no quoted {right} at or beyond the {strike:g} strike on {expiry}"
        out["net_zero_why"] = out["premium_gambit_why"] = out["why"]
        return out
    far = (lambda q: q["strike"]) if right == "P" else (lambda q: -q["strike"])
    buyback, sold = today["mid"], today["open_price"]
    if buyback is not None:
        cap = strike + (sold - buyback) if right == "P" and sold is not None else None
        pays = [q for q in quotes if q["mid"] >= buyback - EPS and (cap is None or q["strike"] <= cap + EPS)]
        if pays:
            q = min(pays, key=far)
            out["net_zero"] = {"close_price": buyback, "open": q, "net": q["mid"] - buyback}
        else:
            edge = min(strike, cap) if cap is not None else strike
            out["net_zero_why"] = (
                f"no quoted {right} at or beyond {edge:g} on {expiry} pays the {buyback:.2f} buy-back"
            )
    else:
        out["net_zero_why"] = "the short has no mark to buy it back at"
    if sold is not None:
        q = max(quotes, key=far)  # the nearest to the old strike, from the far side
        out["premium_gambit"] = {"close_price": sold, "open": q, "net": q["mid"] - sold}
    else:
        out["premium_gambit_why"] = "the price the short was sold at is unknown"
    return out


def next_leg(
    cycle: dict[str, Any],
    bands: dict[str, Any] | None,
    income_entry: dict[str, Any] | None,
    spot: float | None,
    session: date,
    which: str,
) -> dict[str, Any]:
    """After an assignment, the covered call the skate screen would name: the call band (`which`:
    fixed or by_expiry) moved to today's price, on the quoted chain's expiry where the band has it,
    else the latest band row 30-60 days out, snapped to the first quoted strike at or above it.
    Without bands it names nothing and says so."""
    out: dict[str, Any] = {"strike": None, "band": None, "expiration": None, "why": None, "quote": None}
    name = ((bands or {}).get("names") or {}).get(cycle["underlying"])
    if not bands:
        out["why"] = "band not in the snapshot"
        return out
    if not name:
        out["why"] = "no call band for the name in the snapshot"
        return out
    rows = [r for r in name.get("rows") or [] if r.get("side") == "C"]
    want = (income_entry or {}).get("expiry")
    row = next((r for r in rows if r.get("expiry") == want), None)
    if row is None:
        window = [
            r for r in rows if 30 <= (date.fromisoformat(str(r.get("expiry"))[:10]) - session).days <= 60
        ]
        row = max(window, key=lambda r: r["expiry"], default=None)
    if row is None:
        out["why"] = "no call band 30-60 days out"
        return out
    band, why = _band(bands, cycle["underlying"], "C", row["expiry"], spot)
    level = (band or {}).get(which)
    if level is None:
        out["why"] = why or f"no {which} band"
        return out
    out.update(band=level, expiration=row["expiry"], band_kind=which)
    if want == row["expiry"]:
        above = [q for q in _quoted(income_entry, "C") if q["strike"] >= level - EPS]
        if above:
            q = min(above, key=lambda q: q["strike"])
            out.update(strike=q["strike"], quote=q)
    if out["strike"] is None:
        out["why"] = "no quoted strike on that expiry: the first listed strike at or above the band"
    basis = cycle.get("wheel_basis")
    edge = out["strike"] if out["strike"] is not None else level
    out["below_wheel_basis"] = None if basis is None else edge < basis
    return out
