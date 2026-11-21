"""The ladder's order layer (issue #1337, part 2): a pure planner and an executor over an injected broker.

Nothing here builds a broker, reads the ledger or runs on a tick. The tick (`live_tick`) hands the
planner the state it read; the planner returns actions; the executor applies them through whatever
broker object it was given, or logs them in dry-run and sends nothing.

**The planner (`plan`)** is a pure function of its inputs. Per fund, in the order of `live.symbols`:

- Buys rest at the arm's levels (`rules.buy_level`, the same call paper makes, `bought_today` counting
  fills), from `live.session_start` once the reference close is known, as extended-hours day limits.
  Each is sized in whole shares at its level from `lot_dollars`. At `live.disarm_at` every resting buy
  is cancelled; the next session re-places them.
- Every open lot has one GTC extended-hours limit sell at its target (`rules.target`, with `decay`'s
  stages), placed as soon as the lot is known. A changed target or a split changes the order's terms;
  the old order is cancelled and the new one placed on the next run, once the broker no longer lists
  the old one, so two sells never rest on the same shares.
- Caps hold before every new buy and a resting buy counts as if filled: lots per fund, lots in all,
  lots per correlated group, and open plus resting dollars. The drawdown breaker (open lots' mark
  against the budget, `max_open_dollars`), the halt flag and `disarm_at` cancel resting buys and place
  none. They never touch a sell.
- Unarmed, the plan is empty. A lot past `hospital_alert_days` is an alert, never an action. A working
  order whose client id is not ours (a hand order) is never touched.

**Client ids** carry the order's terms: `ldr.b.<fund>.<yyyymmdd>.<n>.<shares>x<cents>` for a buy and
`ldr.s.<lot>.<shares>x<cents>` for a sell. The same state therefore plans the same ids, and the broker's
working list alone says what is already resting. A sell the broker ended unfilled the same day is
re-placed with an `.rN` suffix, at most three times.

**Splits** are an input: `splits[fund]` is the factor (new shares per old share) the stored lots and the
reference close have not yet been restated by. The planner restates both itself and prices every order
at the restated terms. The old orders do not match the new ids, so they are cancelled and re-placed;
nothing relies on the broker's own adjustment. The live tick restates the ledger's lots itself, once
(`live_checks`, `live_tick.apply_splits`), so it passes no factor for lots; the planner's factor stays
for a caller whose lots are not yet restated.

**Blocked funds** (`block_buys`, the positions cross-check) take no new buy and lose their resting
buys; their sells are planned as ever.

Order specs are plain dicts for `cherrypick.core.broker.build_order`.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day
from cherrypick.core.execution import order_id_of

from cherrypick.ladder import live_gates, paper_loop, rules

PREFIX = "ldr"
BUY_TIF = "Ext"  # tastytrade OrderTimeInForce.EXT: a day order that also works extended hours
SELL_TIF = "GTC Ext"  # OrderTimeInForce.GTC_EXT: good till cancelled, extended hours
INSTRUMENT_TYPE = "Equity"
MAX_SELL_ATTEMPTS = 3

_logger = logging.getLogger("ladder_live_orders")


# --------------------------------------------------------------------------- plan types
@dataclass(frozen=True)
class Action:
    kind: str  # place | cancel | keep
    side: str  # buy | sell
    symbol: str
    reason: str
    external_identifier: str | None = None
    order_id: str | None = None  # the broker's id, for a cancel or a keep
    spec: dict | None = None  # for a place
    lot: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Plan:
    armed: bool
    actions: list[Action] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    buys_blocked: list[str] = field(default_factory=list)
    breaker: dict = field(default_factory=dict)

    def of(self, kind: str) -> list[Action]:
        return [a for a in self.actions if a.kind == kind]

    @property
    def places(self) -> list[Action]:
        return self.of("place")

    @property
    def cancels(self) -> list[Action]:
        return self.of("cancel")

    @property
    def keeps(self) -> list[Action]:
        return self.of("keep")

    def as_dict(self) -> dict:
        return {
            "armed": self.armed,
            "actions": [a.as_dict() for a in self.actions if a.kind != "keep"],
            "kept": len(self.keeps),
            "alerts": self.alerts,
            "buys_blocked": self.buys_blocked,
            "breaker": self.breaker,
        }


# --------------------------------------------------------------------------- ids and specs
def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(text))


def _terms(shares: int, price: float) -> str:
    return f"{int(shares)}x{round(price * 100)}"


def _split_terms(terms: str) -> tuple[int, float]:
    shares, cents = terms.split("x")
    return int(shares), int(cents) / 100.0


def buy_id(symbol: str, session: str, n: int, shares: int, price: float) -> str:
    return f"{PREFIX}.b.{_slug(symbol)}.{session.replace('-', '')}.{n}.{_terms(shares, price)}"


def sell_prefix(lot_id: str) -> str:
    return f"{PREFIX}.s.{_slug(lot_id)}."


def sell_id(lot_id: str, shares: int, price: float) -> str:
    return f"{sell_prefix(lot_id)}{_terms(shares, price)}"


def parse_id(eid: object) -> dict | None:
    """The fields of one of our client ids, or None for any other order (a hand order)."""
    parts = str(eid or "").split(".")
    if parts[0] != PREFIX or len(parts) < 4:
        return None
    try:
        if parts[1] == "b" and len(parts) >= 6:
            shares, price = _split_terms(parts[5])
            return {"side": "buy", "symbol": parts[2], "session": parts[3], "n": int(parts[4]),
                    "shares": shares, "price": price, "terms": parts[5]}  # fmt: skip
        if parts[1] == "s":
            shares, price = _split_terms(parts[3])
            return {"side": "sell", "lot": parts[2], "shares": shares, "price": price, "terms": parts[3]}
    except ValueError:
        return None
    return None


def _spec(symbol: str, action: str, shares: int, price: float, tif: str, effect: str, eid: str) -> dict:
    return {
        "time_in_force": tif,
        "order_type": "Limit",
        "legs": [
            {"instrument_type": INSTRUMENT_TYPE, "symbol": symbol, "action": action, "quantity": shares}
        ],
        "price": round(price, 2),
        "price_effect": effect,
        "external_identifier": eid,
    }


def buy_spec(symbol: str, shares: int, price: float, eid: str) -> dict:
    return _spec(symbol, "buy to open", shares, price, BUY_TIF, "debit", eid)


def sell_spec(symbol: str, shares: int, price: float, eid: str) -> dict:
    return _spec(symbol, "sell to close", shares, price, SELL_TIF, "credit", eid)


# --------------------------------------------------------------------------- helpers
def arm_rule(config: dict) -> dict | None:
    """The live arm's params from the paper config (entry, target, drop, gain, min_shares), or None."""
    live = live_gates.live_block(config)
    return paper_loop.arms(config).get(str(live.get("arm") or "").strip())


def _groups(live: dict) -> dict[str, set[str]]:
    return {
        str(name): {str(m).upper() for m in members}
        for name, members in (live.get("correlated_groups") or {}).items()
        if not str(name).startswith("_") and isinstance(members, list)
    }


def restate(lot: dict, factor: float | None) -> dict:
    """A lot at today's terms across a split: shares scale by `factor`, the cost by its inverse."""
    out = {**lot, "symbol": str(lot["symbol"]).upper()}
    if factor and factor > 0 and factor != 1:
        out["shares"] = int(math.floor(int(lot["shares"]) * factor + 1e-9))
        out["cost"] = round(float(lot["cost"]) / factor, 6)
    return out


def breaker_state(lots: list[dict], marks: dict, budget: float, frac: float) -> dict:
    """The open lots' unrealized result against the budget. Fails closed: a lot with no mark trips it."""
    if not lots:
        return {"tripped": False, "unrealized": 0.0, "limit": round(-frac * budget, 2)}
    missing = sorted({lot["symbol"] for lot in lots if not marks.get(lot["symbol"])})
    unrealized = round(
        sum((float(marks.get(lot["symbol"]) or lot["cost"]) - lot["cost"]) * lot["shares"] for lot in lots), 2
    )
    limit = round(-frac * budget, 2)
    out = {"tripped": unrealized <= limit, "unrealized": unrealized, "limit": limit}
    if missing:
        out.update(tripped=True, reason="no_mark", missing=missing)
    return out


class _Occupancy:
    """Lots and dollars held or resting, by fund, in all and by group, and what one more would do."""

    def __init__(self, live: dict):
        self.live = live
        self.groups = _groups(live)
        self.by_symbol: dict[str, int] = {}
        self.total = 0
        self.dollars = 0.0

    def add(self, symbol: str, shares: int, price: float) -> None:
        self.by_symbol[symbol] = self.by_symbol.get(symbol, 0) + 1
        self.total += 1
        self.dollars += shares * price

    def refusal(self, symbol: str, shares: int, price: float) -> str | None:
        live = self.live
        if self.by_symbol.get(symbol, 0) + 1 > int(live["max_open_lots"]):
            return "name_cap_full"
        if self.total + 1 > int(live["max_open_lots_total"]):
            return "total_cap_full"
        for members in self.groups.values():
            if symbol in members:
                held = sum(self.by_symbol.get(m, 0) for m in members)
                if held + 1 > int(live["max_open_lots_per_group"]):
                    return "group_cap_full"
        if self.dollars + shares * price > float(live["max_open_dollars"]) + 1e-9:
            return "dollar_ceiling"
        return None


# --------------------------------------------------------------------------- the planner
def plan(
    live: dict,
    rule: dict,
    *,
    when: datetime,
    armed: bool,
    halted: bool = False,
    ref_close: dict[str, float | None],
    lots: list[dict],
    working: list[dict],
    buys_today: dict[str, int] | None = None,
    marks: dict[str, float | None] | None = None,
    anchors: dict[str, float | None] | None = None,
    splits: dict[str, float] | None = None,
    ended_ids: Iterable[str] = (),
    block_buys: dict[str, str] | None = None,
    cash_limit: float | None = None,
) -> Plan:
    """What to place, cancel and keep. Pure: no clock, no broker, no ledger.

    `lots` are the open live lots ({id, symbol, shares, cost, buy_date}); `working` is the broker's
    working-order list ({order_id, external_identifier, ...}); `buys_today` counts the fund's lots
    bought this session, sold or not; `marks` is each fund's bid; `anchors` is `chain`'s last lot
    bought per fund; `ended_ids` are client ids the broker ended unfilled today. `block_buys` maps a
    fund to why it may take no new buy (the positions cross-check): its resting buys are cancelled and
    none placed, and its sells are untouched. `cash_limit` is the settled cash new buys may spend
    (`cherrypick.core.live.cash`; None in a margin account): the buys kept resting count against it
    first, and a new buy it cannot pay for waits (`settled_cash_short`). Resting buys stay."""
    out = Plan(armed=armed)
    if not armed:
        out.buys_blocked.append("unarmed")
        return out
    problems = live_gates.limit_problems(live)
    if problems:
        out.buys_blocked.append("limits_invalid: " + "; ".join(problems))
        return out

    when = when.astimezone(ET)
    today = when.date()
    now_min = minute_of_day(when)
    start_min = hhmm_to_min(live["session_start"], 8 * 60 + 15)
    end_min = hhmm_to_min(live["disarm_at"], 20 * 60)
    symbols = [str(s).upper() for s in live.get("symbols") or []]
    buys_today = {k.upper(): v for k, v in (buys_today or {}).items()}
    marks = {k.upper(): v for k, v in (marks or {}).items()}
    anchors = {k.upper(): v for k, v in (anchors or {}).items()}
    splits = {k.upper(): v for k, v in (splits or {}).items()}
    ref_close = {k.upper(): v for k, v in ref_close.items()}
    ended = set(ended_ids)
    blocked = {k.upper(): v for k, v in (block_buys or {}).items()}

    held = [restate(lot, splits.get(str(lot["symbol"]).upper())) for lot in lots]
    mine = [(w, parse_id(w.get("external_identifier"))) for w in working]
    mine = [(w, p) for w, p in mine if p is not None]

    _plan_sells(out, live, rule, held, [(w, p) for w, p in mine if p["side"] == "sell"], today, ended)

    # ----- buys
    out.breaker = breaker_state(
        held, marks, float(live["max_open_dollars"]), float(live["drawdown_breaker_frac"])
    )
    resting = [(w, p) for w, p in mine if p["side"] == "buy"]
    stop = None
    if halted:
        stop = "halted"
    elif out.breaker["tripped"]:
        stop = "drawdown_breaker"
    elif now_min >= end_min:
        stop = "after_disarm_at"
    if stop:
        out.buys_blocked.append(stop)
        for w, p in resting:
            out.actions.append(_cancel(w, p["symbol"], "buy", stop))
        return out
    if now_min < start_min:
        out.buys_blocked.append("before_session_start")
        return out

    # Desired buys, in the order of live.symbols, against the open lots and each other.
    desired: list[dict] = []
    desired_occ = _Occupancy(live)
    for lot in held:
        desired_occ.add(lot["symbol"], lot["shares"], lot["cost"])
    for symbol in symbols:
        if symbol in blocked:
            out.buys_blocked.append(f"{symbol}: {blocked[symbol]}")
            continue
        ref = ref_close.get(symbol)
        if not ref:
            out.buys_blocked.append(f"{symbol}: no_reference_close")
            continue
        factor = splits.get(symbol) or 1.0
        ref = ref / factor
        anchor = anchors.get(symbol)
        anchor = anchor / factor if anchor else None
        filled = int(buys_today.get(symbol, 0))
        for k in range(1000):
            if rule["entry"] == "once" and (filled or k):
                break
            if rule["entry"] == "chain" and k:
                break
            level = rules.buy_level(
                rule["entry"],
                ref_close=ref,
                last_buy=anchor,
                drop=float(rule["drop"]),
                bought_today=filled + k,
            )
            if level is None:
                break
            shares = rules.lot_shares(float(live["lot_dollars"]), level)
            if shares < int(rule["min_shares"]):
                out.buys_blocked.append(f"{symbol}: lot_too_small")
                break
            refused = desired_occ.refusal(symbol, shares, level)
            if refused:
                out.buys_blocked.append(f"{symbol}: {refused}")
                break
            desired_occ.add(symbol, shares, level)
            n = filled + k + 1
            desired.append(
                {
                    "symbol": symbol,
                    "n": n,
                    "level": level,
                    "shares": shares,
                    "id": buy_id(symbol, today.isoformat(), n, shares, level),
                }  # fmt: skip
            )

    want = {d["id"] for d in desired}
    # Funds whose reference is missing keep what rests; every other resting buy must be wanted.
    no_ref = {s for s in symbols if not ref_close.get(s) and s not in blocked}
    occ = _Occupancy(live)
    for lot in held:
        occ.add(lot["symbol"], lot["shares"], lot["cost"])
    seen: set[str] = set()
    cash_left = None if cash_limit is None else float(cash_limit)
    for w, p in resting:
        eid = str(w["external_identifier"])
        occ.add(p["symbol"], p["shares"], p["price"])
        if (eid in want and eid not in seen) or p["symbol"] in no_ref:
            seen.add(eid)
            out.actions.append(_keep(w, p["symbol"], "buy", "resting"))
            if cash_left is not None:
                cash_left -= float(p["shares"]) * float(p["price"])
        else:
            out.actions.append(
                _cancel(w, p["symbol"], "buy", "blocked" if p["symbol"] in blocked else "not_wanted")
            )
    for d in desired:
        if d["id"] in seen:
            continue
        if d["id"] in ended:
            out.buys_blocked.append(f"{d['symbol']}: ended_today")
            continue
        refused = occ.refusal(d["symbol"], d["shares"], d["level"])
        if refused:  # a cancel is still pending on the book; the place waits for the next run
            out.buys_blocked.append(f"{d['symbol']}: waiting_for_cancel ({refused})")
            continue
        cost = d["shares"] * d["level"]
        if cash_left is not None:
            if cost > cash_left + 1e-9:
                out.buys_blocked.append(
                    f"{d['symbol']}: settled_cash_short (needs {cost:.2f}, settled {max(cash_left, 0.0):.2f})"
                )
                continue
            cash_left -= cost
        occ.add(d["symbol"], d["shares"], d["level"])
        out.actions.append(
            Action(
                "place",
                "buy",
                d["symbol"],
                f"level {d['n']} at {d['level']:.2f}",
                d["id"],
                spec=buy_spec(d["symbol"], d["shares"], d["level"], d["id"]),
            )  # fmt: skip
        )
    return out


def _keep(w: dict, symbol: str, side: str, reason: str, lot: str | None = None) -> Action:
    return Action("keep", side, symbol, reason, str(w.get("external_identifier")), w.get("order_id"), lot=lot)


def _cancel(w: dict, symbol: str, side: str, reason: str, lot: str | None = None) -> Action:
    return Action(
        "cancel", side, symbol, reason, str(w.get("external_identifier")), w.get("order_id"), lot=lot
    )


def _plan_sells(
    out: Plan, live: dict, rule: dict, held: list[dict], resting: list, today, ended: set
) -> None:
    by_lot: dict[str, list] = {}
    for w, p in resting:
        by_lot.setdefault(p["lot"], []).append((w, p))
    known = {_slug(lot["id"]) for lot in held}
    for key, orders in by_lot.items():
        if key not in known:
            out.alerts.append({"kind": "orphan_sell", "lot": key, "order_id": orders[0][0].get("order_id")})
    for lot in held:
        age = rules.age_days(_as_date(lot["buy_date"]), today)
        if age > int(live["hospital_alert_days"]):
            out.alerts.append(
                {"kind": "hospital", "lot": lot["id"], "symbol": lot["symbol"], "age_days": age}
            )
        if lot["shares"] < 1:
            out.alerts.append(
                {"kind": "no_whole_shares_after_split", "lot": lot["id"], "symbol": lot["symbol"]}
            )
            continue
        tgt = rules.target(
            rule["target"], cost=lot["cost"], shares=lot["shares"], age=age, gain=float(rule["gain"])
        )
        terms = _terms(lot["shares"], tgt)
        orders = by_lot.get(_slug(lot["id"]), [])
        right = [o for o in orders if o[1]["terms"] == terms]
        for w, _ in right[1:]:
            out.actions.append(_cancel(w, lot["symbol"], "sell", "duplicate", lot["id"]))
        for w, _ in [o for o in orders if o[1]["terms"] != terms]:
            out.actions.append(_cancel(w, lot["symbol"], "sell", "terms_changed", lot["id"]))
        if right:
            out.actions.append(_keep(right[0][0], lot["symbol"], "sell", "resting", lot["id"]))
            continue
        if len(right) < len(orders):  # an old order is being cancelled; place on the next run
            continue
        base = sell_id(lot["id"], lot["shares"], tgt)
        tries = sum(1 for e in ended if e == base or e.startswith(base + ".r"))
        if tries >= MAX_SELL_ATTEMPTS:
            out.alerts.append({"kind": "sell_ended_repeatedly", "lot": lot["id"], "symbol": lot["symbol"]})
            continue
        eid = base if tries == 0 else f"{base}.r{tries}"
        out.actions.append(
            Action(
                "place",
                "sell",
                lot["symbol"],
                f"target {tgt:.2f}",
                eid,
                spec=sell_spec(lot["symbol"], lot["shares"], tgt, eid),
                lot=lot["id"],
            )  # fmt: skip
        )


def _as_date(value):
    return (
        value
        if hasattr(value, "year") and not isinstance(value, datetime)
        else datetime.fromisoformat(str(value)).date()
    )


# --------------------------------------------------------------------------- the executor
class Executor:
    """Applies a plan through an injected broker (the `core.execution.Broker` interface). In dry-run it
    logs the orders it would send and calls the broker for nothing."""

    def __init__(self, broker, *, dry_run: bool = False, log: Callable[[str], None] | None = None):
        self.broker = broker
        self.dry_run = dry_run
        self._log = log or _logger.info

    def apply(self, the_plan: Plan) -> list[dict]:
        results: list[dict] = []
        if not the_plan.armed:
            return results
        for a in the_plan.cancels:
            results.append(self._cancel(a))
        held_off = False
        for a in the_plan.places:
            if held_off:
                results.append({"action": a.as_dict(), "ok": False, "skipped": "broker_hold"})
                continue
            r = self._place(a)
            results.append(r)
            held_off = bool(r.get("uncertain"))
        return results

    def _cancel(self, a: Action) -> dict:
        if self.dry_run:
            self._log(f"dry-run: would cancel {a.side} {a.symbol} {a.external_identifier} ({a.reason})")
            return {"action": a.as_dict(), "ok": True, "dry_run": True}
        res = self.broker.cancel(a.order_id)
        self._log(f"cancel {a.side} {a.symbol} {a.external_identifier} ({a.reason}): ok={res.get('ok')}")
        return {"action": a.as_dict(), "ok": bool(res.get("ok")), "error": res.get("error")}

    def _place(self, a: Action) -> dict:
        leg = a.spec["legs"][0]
        what = (
            f"{leg['action']} {leg['quantity']} {a.symbol} at {a.spec['price']:.2f} {a.spec['time_in_force']}"
        )
        if self.dry_run:
            self._log(f"dry-run: would place {what} id={a.external_identifier}")
            return {"action": a.as_dict(), "ok": True, "dry_run": True}
        res = self.broker.place(a.spec, True)
        oid = order_id_of(res)
        self._log(f"placed {what} id={a.external_identifier}: ok={res.get('ok')} order={oid}")
        out = {"action": a.as_dict(), "ok": bool(res.get("ok")) and oid is not None, "order_id": oid}
        if not res.get("ok"):
            out["error"] = res.get("error")
        if res.get("unmet_gates"):
            out["unmet_gates"] = list(res["unmet_gates"])
        if res.get("uncertain"):
            out["uncertain"] = True
        return out
