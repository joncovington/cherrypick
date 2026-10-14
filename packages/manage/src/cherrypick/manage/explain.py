"""Plain-English explanation of a trade's recommendation.

The evaluation's `reason` is the rule's terse statement ("down 58% on the $253 debit (stop at -50%)").
This writes the same verdict the way a person would say it: what was paid or taken in, what the trade
is worth now, what the rule is and why it acts here, and what to do -- or, for a hold, what the trade
is waiting for. Every number comes from the trade's own fields, never from parsing the reason, and
nothing here changes a verdict: it only words the one already made.

Money in prose is whole-position dollars, rounded to the dollar, and gross of fees like the rest of
the evaluation.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def _usd(v: float | None) -> str:
    return "an unknown amount" if v is None else f"${abs(v):,.0f}"


def _day(iso: str | None) -> str:
    if not iso:
        return "an unknown date"
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def _pct(v: float | None) -> str:
    return "an unknown share" if v is None else f"{abs(v) * 100:.0f}%"


def opened(t: dict[str, Any]) -> str:
    """'You paid $1,104 for this trade, and it is now worth -$58.' -- the opening, then where it stands."""
    cost, value = t.get("cost"), t.get("value")
    if cost is None or value is None:
        return ""
    if t.get("kind") == "credit":
        now = -value
        stand = (
            f"buying it back would cost {_usd(now)}" if now > 0 else f"closing it would pay you {_usd(now)}"
        )
        return f"You took in {_usd(cost)} when you opened this, and {stand}"
    stand = f"it is worth {_usd(value)} now" if value >= 0 else f"closing it would cost {_usd(value)}"
    return f"You paid {_usd(cost)} for this, and {stand}"


def _pnl(t: dict[str, Any]) -> str:
    pnl = t.get("pnl_gross")
    if pnl is None:
        return ""
    return f"a {'gain' if pnl >= 0 else 'loss'} of {_usd(pnl)} ({_pct(t.get('capture'))})"


def _close_cash(t: dict[str, Any]) -> str:
    order = t.get("order") or {}
    net = order.get("net")
    if net is None:
        return ""
    return f"Closing at mid would {'bring in' if net >= 0 else 'cost'} about {_usd(net)}."


def _earnings(t: dict[str, Any]) -> str:
    er = t.get("earnings")
    if not er:
        return ""
    return f" Earnings are due {_day(er['date'])} ({er['days']} days), inside the trade."


def _fired(t: dict[str, Any], rule: str) -> dict[str, Any] | None:
    return next((f for f in t.get("fired") or [] if f["rule"] == rule), None)


def _hold(t: dict[str, Any]) -> str:
    levels = t.get("exit_levels") or {}
    tp, stop, te = levels.get("take_profit"), levels.get("stop"), levels.get("time_exit")
    head = f"Nothing to do today. {opened(t)}, {_pnl(t)}. No rule has been triggered."
    waits = []
    if t.get("kind") == "credit":
        if tp and tp.get("close_value") is not None:
            waits.append(f"take the profit once buying it back costs {_usd(tp['close_value'])} or less")
        if stop and stop.get("close_value") is not None:
            waits.append(f"cut it if buying it back reaches {_usd(stop['close_value'])}")
    else:
        if tp and tp.get("close_value") is not None:
            waits.append(f"take profit, roll or add if it grows to {_usd(tp['close_value'])}")
        if stop and stop.get("close_value") is not None:
            waits.append(f"cut it if it falls to {_usd(stop['close_value'])}")
    if te and te.get("date"):
        waits.append(f"close or roll it on {_day(te['date'])} whatever happens")
    tail = (" It is waiting to " + "; ".join(waits) + ".") if waits else ""
    return head + tail + _earnings(t)


def explain(t: dict[str, Any]) -> str:
    rec = t.get("recommendation") or {}
    action, rule = rec.get("action"), rec.get("rule")
    if action == "HOLD":
        return _hold(t)
    if rule == "data":
        return "Some legs of this trade could not be priced, so no rule was judged. Check it by hand."
    if action == "SETTLES":
        return (
            "These are cash-settled index options at expiry. They settle in cash on the official closing "
            "print, with no shares and no assignment risk, so there is nothing to do."
        )

    pos = f"{opened(t)}, {_pnl(t)}."
    levels = t.get("exit_levels") or {}
    if rule == "debit.stop":
        why = (
            "The rule cuts any trade bought for a debit once it has lost half of what was paid. "
            "Past that point it rarely recovers, and holding on turns a planned loss into a bigger one. "
            "Close it."
        )
    elif rule == "credit.stop":
        basis = (levels.get("stop") or {}).get("basis")
        why = (
            "The loss has grown as large as the credit you took in. The rule cuts a credit trade there, "
            "so one bad trade costs about what a good one earns. Close it."
            if basis != "max_loss"
            else "The loss has reached half of the most this trade can lose. The rule cuts it there. "
            "Close it."
        )
    elif rule == "credit.take_profit":
        why = (
            "You have kept at least half of the credit. The rule banks it: the rest of the premium is "
            "small, and earning it means carrying the full risk for weeks. Close it."
        )
    elif rule == "debit.take_profit":
        why = (
            "It has gained three quarters or more on what you paid. The rule says take the profit, roll it "
            "to a new position, or add to it, rather than let the gain ride on to expiry."
        )
    elif rule in ("credit.time_exit", "debit.time_exit"):
        dte = t.get("nearest_short_dte")
        when = f"The nearest short leg expires in {dte} days. " if dte is not None else ""
        if action == "ROLL_OR_CLOSE":
            why = (
                when + "In the last three weeks the remaining premium comes with the most risk, so the rule "
                "exits now. This trade is profitable, so you can either close it or roll it out to about 45 "
                "days."
            )
        else:
            why = (
                when + "In the last three weeks the remaining premium comes with the most risk, so the rule "
                "exits now. This trade is losing, and a loser is closed, never rolled. Close it."
            )
    elif rule == "credit.breached":
        pf = t.get("profile") or {}
        why = (
            "The market has moved through one of your short strikes. What is left to win is "
            f"{_usd(pf.get('upside_from_now'))}, against {_usd(pf.get('downside_from_now'))} still at risk. "
            "You would not open a trade on those odds today, so the reason for holding it is gone. Close it."
        )
    elif rule == "expiry":
        why = (
            "Some legs expire within days and settle into shares, not cash. Holding them into expiry risks "
            "being assigned stock, even after the closing bell. Close before then."
        )
    elif rule == "legs.short_take_profit":
        f = _fired(t, rule) or {}
        legs = ", ".join(f.get("legs") or []) or "the short legs"
        why = (
            f"Close only these legs: {legs}. The short leg has given up almost all of its premium, so buying "
            "it back is cheap and removes risk that no longer pays. Keep the rest of the trade."
        )
    elif rule == "ex_dividend":
        why = (
            "A short call is in the money ahead of an ex-dividend date, with less time value left than the "
            "dividend. Whoever owns that call is likely to exercise it early to collect the dividend. "
            "Decide whether to close or roll it before the ex-date."
        )
    elif rule == "risk.oversize":
        why = (
            "What this trade could still lose is far larger than the per-trade risk budget. Review whether "
            "the size is what you intended."
        )
    elif rule == "credit.close_before_earnings":
        why = "Earnings fall inside the short legs' life, and the rule closes credit trades before a report."
    else:
        why = rec.get("reason") or ""

    # A whole-trade close costs what the opening sentence already said; only a partial one needs its price.
    cash = f" {_close_cash(t)}" if action == "CLOSE_LEGS" else ""
    also = ""
    if rec.get("agreeing"):
        also = " The " + _names(rec["agreeing"]) + " points the same way."
    conflict = ""
    if rec.get("conflicts"):
        conflict = " The " + _names(rec["conflicts"]) + " points the other way, which lowers the confidence."
    return f"{pos} {why}{cash}{also}{conflict}{_earnings(t)}"


PLAIN = {
    "expiry": "expiry rule",
    "credit.take_profit": "credit take-profit rule",
    "credit.stop": "credit stop",
    "credit.time_exit": "21-day exit (which would roll this winner)",
    "credit.breached": "breached-strike rule",
    "credit.close_before_earnings": "close-before-earnings rule",
    "debit.stop": "50% debit stop",
    "debit.take_profit": "debit take-profit rule",
    "debit.time_exit": "time exit",
    "legs.short_take_profit": "short-leg buy-back rule",
    "ex_dividend": "ex-dividend check",
    "risk.oversize": "risk budget check",
}


def _names(rules: list[str]) -> str:
    names = [PLAIN.get(r, r) for r in rules]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
