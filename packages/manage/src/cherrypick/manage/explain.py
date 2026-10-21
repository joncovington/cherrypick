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

from cherrypick.manage import shapes as _shapes


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
    chain = t.get("chain") or {}
    orders = chain.get("orders") or []
    later = orders[1:]
    if later:
        # A rolled trade: say what opened it and what the rolls brought in, then the net it stands on.
        first = orders[0]
        opened_for = f"{'took in' if first['net'] >= 0 else 'paid'} {_usd(first['net'])}"
        moved = sum(o["net"] for o in later)
        n = len(later)
        times = "once" if n == 1 else "twice" if n == 2 else f"{n} times"
        rolls = f"{'taking in' if moved >= 0 else 'paying'} {_usd(moved)}"
        net_in = (
            f"so you are in for {_usd(cost)} net" if cost >= 0 else f"so you are {_usd(cost)} ahead in cash"
        )
        if value >= 0:
            stand = f"it is worth {_usd(value)} now"
        else:
            stand = f"closing it would cost {_usd(value)}"
        return (
            f"You opened this on {_day(first['date'])} and {opened_for}, then adjusted it {times}, "
            f"{rolls}, {net_in}; {stand}"
        )
    if cost < 0:
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
    word = "gain" if pnl >= 0 else "loss"
    d = t.get("directional") or {}
    if d:
        return f"a {word} of {_usd(pnl)} ({_pct(t.get('capture'))} of the {_usd(d['max_loss'])} at risk)"
    return f"a {word} of {_usd(pnl)} ({_pct(t.get('capture'))})"


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
    head = f"Nothing to do today.{_shape_note(t)} {opened(t)}, {_pnl(t)}. No rule has been triggered."
    waits = []
    d = t.get("directional") or {}
    if d:
        if stop and stop.get("close_value") is not None:
            waits.append(f"take profit once it is up {_usd(d['target'])}")
            waits.append(f"cut it if it is down {_usd(-(stop['close_value'] - t['cost']))}")
        if te and te.get("date"):
            waits.append(f"close it on {_day(te['date'])} whatever happens")
        return head + " It is waiting to " + "; ".join(waits) + "." + _earnings(t)
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


def _earnings_hold(t: dict[str, Any]) -> str:
    """An earnings trade before its call: why nothing is done today, and what is waiting."""
    et = t.get("earnings_trade") or {}
    when = f" {et['time_of_day']}" if et.get("time_of_day") else ""
    lead = et.get("lead_days")
    put_on = f", put on {lead} day{'s' if lead != 1 else ''} before it" if lead is not None else ""
    head = (
        f"This is an earnings trade: it is here for the report on {_day(et.get('report'))}{when}{put_on}. "
        f"{opened(t)}, {_pnl(t)}. Hold it through the report and close it after the call."
    )
    held = t.get("held_for_earnings") or []
    if not held:
        return head
    plain = {**PLAIN, "credit.time_exit": "21-day exit", "debit.time_exit": "time exit"}
    names = ", ".join(plain.get(f["rule"], f["rule"]) for f in held)
    return head + f" The {names} would act today; for an earnings trade it waits for the call."


def explain(t: dict[str, Any]) -> str:
    et = t.get("earnings_trade") or {}
    if not et.get("kept"):
        return _explain(t)
    return _explain(t) + (
        f" It was put on for the {_day(et.get('report'))} report, which is out. It is losing, but it has "
        "a leg in a later expiry that can recover, so it is not closed for being an earnings trade; "
        "the ordinary rules manage it from here."
    )


def _explain(t: dict[str, Any]) -> str:
    rec = t.get("recommendation") or {}
    action, rule = rec.get("action"), rec.get("rule")
    if action == "HOLD" and rule == "earnings_trade":
        return _earnings_hold(t)
    if action == "HOLD":
        return _hold(t)
    if rule == "data":
        return "Some legs of this trade could not be priced, so no rule was judged. Check it by hand."
    if action == "SETTLES":
        return (
            "These are cash-settled index options at expiry. They settle in cash on the official closing "
            "print, with no shares and no assignment risk, so there is nothing to do."
        )

    pos = f"{_shape_note(t).strip()} {opened(t)}, {_pnl(t)}.".strip()
    levels = t.get("exit_levels") or {}
    if rule == "directional.take_profit":
        why = (
            "It has made half of what it can, or as much as it risked. A directional trade is closed there: "
            "the rest needs the move to keep going, with the whole gain at stake. Close it."
        )
    elif rule == "directional.stop":
        why = (
            "It has lost half of what it can lose. The move it was betting on has gone the other way, so "
            "the rule cuts it there rather than ride it to the maximum loss. Close it."
        )
    elif rule == "debit.stop":
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
        tp = levels.get("take_profit") or {}
        why = (
            f"You have kept at least {_share(tp.get('target', 0.5))} of the credit. The rule banks it: the "
            "rest of the premium is small, and earning it means carrying the full risk for weeks."
            f"{_target_note(tp)} Close it."
        )
    elif rule == "debit.take_profit":
        tp = levels.get("take_profit") or {}
        why = (
            f"It has gained {_share(tp.get('target', 0.75))} or more on what you paid. The rule says take "
            "the profit, roll it to a new position, or add to it, rather than let the gain ride on to "
            f"expiry.{_target_note(tp)}"
        )
    elif rule == "debit.diagonal_width":
        why = (
            "The net you have in this diagonal is most of the distance between its strikes, or more. If the "
            "stock runs well through the short, both legs head toward their intrinsic value and the trade "
            "toward that distance, so there is little left to make, and a net past it loses once the long's "
            "time value runs off. A long with years left keeps some of that time value, which softens this. "
            "Review it: rolling the short out for a credit lowers the net, and a trade like this is opened "
            "for under three quarters of the width."
        )
    elif rule in ("credit.time_exit", "debit.time_exit"):
        dte = t.get("nearest_short_dte")
        when = f"The nearest short leg expires in {dte} days. " if dte is not None else ""
        if action == "ROLL_OR_CLOSE":
            why = (
                when + "In the last three weeks the remaining premium comes with the most risk, so the rule "
                "exits now. This trade is profitable, so you can either close it or roll it out to about 45 "
                "days, but only for a net credit. If the roll would cost money, close it instead."
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
    elif rule == "legs.long_vertical":
        lv = t.get("long_vertical") or {}
        why = (
            f"Sell only the long spread: {', '.join(lv.get('legs') or [])}. It is worth "
            f"{_usd(lv.get('value'))}, nearly all of its {_usd(lv.get('width'))} width, and holding it gives "
            "that back if the stock comes back through the body. What is left is a short spread "
            f"({', '.join(lv.get('rest') or [])}): {_usd(lv.get('rest_cost'))} to buy back, "
            f"{_usd(lv.get('rest_width'))} wide. Hold it for its credit, close it too, or add the other "
            "side to make it a condor."
        )
    elif rule == "legs.short_take_profit":
        f = _fired(t, rule) or {}
        legs = ", ".join(f.get("legs") or []) or "the short legs"
        why = (
            f"Close only these legs: {legs}. The short leg has given up almost all of its premium, so buying "
            "it back is cheap and removes risk that no longer pays. Keep the rest of the trade."
        )
    elif rule == "roll.naked_put":
        r = t.get("roll") or {}
        closers = _names(rec.get("replaces") or [])
        strike, when = f"{r.get('strike', 0):g}", _day(r.get("expiration"))
        per_share, total = f"{r.get('credit', 0):.2f}", _usd(r.get("net"))
        why = (
            f"The rules say to close this naked short put ({closers}). Rolling it out in time can be the "
            f"better exit: buy it back and sell the same {strike} put for {when}, about {per_share} a share "
            f"more than the buy-back (roughly {total} in credit, an estimate from today's volatility). That "
            "keeps you paid for the risk while giving the stock more time. Only send it for a net credit; "
            "if it will not fill for one, close the put instead."
        )
    elif rule == "roll.untested_side":
        r = t.get("untested_roll") or {}
        right = "put" if r.get("right") == "P" else "call"
        tested = "call" if right == "put" else "put"
        way = "up" if right == "put" else "down"
        why = (
            f"The stock has moved toward your {r.get('tested_strike', 0):g} {tested}, and the "
            f"{r.get('from_strike', 0):g} {right} on the other side has little left to give. Roll that "
            f"untested {right} {way} to the {r.get('to_strike', 0):g}"
        )
        why += (
            f" in the same expiry, for about {r.get('credit', 0):.2f} a share ({_usd(r.get('net'))}) at "
            "today's quoted mids. It takes in more credit and cuts the trade's lean toward the tested side, "
            "but it is repair, not a cure: if the move keeps going the tested side still loses. Only send "
            "it for a net credit."
        )
        if "legs.short_take_profit" in (rec.get("replaces") or []):
            why += (
                f" The {right} has given up most of its premium, so it is due to be bought back anyway; "
                "rolling it, rather than only buying it back, keeps a short on both sides. If the roll will "
                f"not fill for a credit, just buy the {right} back."
            )
    elif rule == "roll.fly_off":
        fo = t.get("fly_off") or {}
        new, old = f"{fo.get('new_strike', 0):g}", f"{fo.get('old_strike', 0):g}"
        why = (
            f"The wide wing can be flown off: buy the {new} and sell the {old} for about "
            f"{_usd(fo.get('debit'))} (an estimate from today's volatility), less than the "
            f"{_usd(t.get('cost'))} you took in. That leaves a plain butterfly opened for a net credit of "
            f"about {_usd(fo.get('kept'))}: it cannot lose (fees aside) and still pays most if the stock "
            f"finishes at the body. Re-quote it first, and check the {new} strike is listed."
        )
    elif rule == "roll.diagonal_short":
        r = t.get("roll") or {}
        due = _names(rec.get("replaces") or [])
        front, when = _day(r.get("from")), _day(r.get("expiration"))
        per_unit, total = f"{r.get('credit', 0):.2f}", _usd(r.get("net"))
        why = (
            f"The short side of this diagonal is due ({due}). Rather than close the trade, roll the short "
            f"structure out: close the {front} legs and sell the same strikes for {when}, about {per_unit} "
            f"a unit (roughly {total} in credit, an estimate from today's volatility). The long keeps "
            "working underneath, and the new short stays beyond its breakeven. Only send it for a net "
            "credit; if it will not fill for one, close the short side instead."
        )
        if r.get("earnings_date"):
            why += (
                f" Earnings on {_day(r['earnings_date'])} fall inside the new short's life: the long covers "
                "it, but expect a bigger move."
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
    elif rule == "earnings_trade":
        et = t.get("earnings_trade") or {}
        why = (
            f"This is an earnings trade, put on for the report on {_day(et.get('report'))}. The report is "
            "out, and an earnings trade is closed after the call rather than held for what is left. Close it."
        )
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
    rb = t.get("roll_blocked") or {}
    if rb.get("rule") == "roll.naked_put" and rb.get("reason") == "earnings":
        blocked = (
            f" Rolling it out instead is not an option: a roll to {_day(rb.get('expiration'))} would carry "
            f"the put through earnings on {_day(rb.get('earnings_date'))}. Consider re-opening it after "
            "the report."
        )
        return f"{pos} {why}{cash}{also}{conflict}{blocked}"
    return f"{pos} {why}{cash}{also}{conflict}{_diagonal_blocked(rb)}{_earnings(t)}"


def _diagonal_blocked(rb: dict[str, Any]) -> str:
    """Why the short side of a diagonal was not rolled, when it was due."""
    if rb.get("rule") != "roll.diagonal_short":
        return ""
    reason = rb.get("reason")
    if reason == "stopped":
        return (
            " Rolling the short side is not on the table: the trade has hit its stop, and selling new shorts "
            "against a long that has failed is repairing a loser."
        )
    if reason == "long_too_short":
        return (
            " Rolling the short side would leave too little life in the long, which expires "
            f"{_day(rb.get('long_expiration'))}: it should outlast the new short by {rb.get('min_days', 45)} "
            "days or more. "
            "Close, or roll the long out first."
        )
    if reason == "breakeven":
        return (
            f" Rolling the short at the same strike would leave it at {rb.get('strike'):g}, inside the "
            "long's "
            f"breakeven of {rb.get('breakeven'):.2f}: an assignment there would lock in a loss."
        )
    if reason == "credit":
        return (
            f" A same-strike roll would take in only about {rb.get('credit', 0):.2f}, under the minimum, so "
            "it is not worth doing."
        )
    if reason == "earnings":
        return f" Rolling would carry the short side through earnings on {_day(rb.get('earnings_date'))}."
    return ""


PLAIN = {
    "expiry": "expiry rule",
    "credit.take_profit": "credit take-profit rule",
    "credit.stop": "credit stop",
    "credit.time_exit": "21-day exit (which would roll this winner)",
    "credit.breached": "breached-strike rule",
    "credit.close_before_earnings": "close-before-earnings rule",
    "debit.stop": "50% debit stop",
    "debit.take_profit": "debit take-profit rule",
    "debit.diagonal_width": "diagonal width check",
    "debit.time_exit": "time exit",
    "legs.short_take_profit": "short-leg buy-back rule",
    "legs.long_vertical": "long-spread sale",
    "roll.naked_put": "naked-put credit roll",
    "roll.diagonal_short": "diagonal short roll",
    "roll.fly_off": "broken-wing fly-off",
    "roll.untested_side": "untested-side roll",
    "ex_dividend": "ex-dividend check",
    "risk.oversize": "risk budget check",
    "earnings_trade": "earnings-trade rule",
    "directional.take_profit": "directional take-profit",
    "directional.stop": "directional stop",
}


def _share(v: float) -> str:
    """0.5 -> 'half', 0.25 -> 'a quarter', 0.75 -> 'three quarters', anything else a percentage."""
    words = {0.25: "a quarter", 0.5: "half", 0.75: "three quarters"}
    return next((w for k, w in words.items() if abs(v - k) < 1e-9), _pct(v))


# Why a shape's own profit target differs from its rule's, for the shapes the default sets apart.
TARGET_NOTES = {
    "short straddle": "one of its short strikes is nearly always in the money, so it rarely keeps more",
    "iron butterfly": "one of its short strikes is nearly always in the money, so it rarely keeps more",
    "big lizard": "its short put and call share a strike, so one is nearly always in the money",
    "reverse big lizard": "its short call and put share a strike, so one is nearly always in the money",
    "calendar": "it trades only the time value between its two expiries, which cannot run far either way",
    "call zebra": "it starts with no time value, so the gain is the stock's move, taken early",
    "put zebra": "it starts with no time value, so the gain is the stock's move, taken early",
}


def _target_note(level: dict[str, Any]) -> str:
    """' A short straddle's target is its own: ...' when the trade's shape has its own target."""
    shape = level.get("by_shape")
    if not shape:
        return ""
    why = TARGET_NOTES.get(shape)
    return f" A {shape}'s target is its own" + (f": {why}." if why else ".")


def _names(rules: list[str]) -> str:
    names = [PLAIN.get(r, r) for r in rules]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


SHAPE_NOTES = {
    "super bear": (
        "This is a super bear: a long put spread below a short call spread, opened near even money. It earns "
        "from the stock falling, so it is judged on what it can win and lose, not on its small premium."
    ),
    "super bull": (
        "This is a super bull: a long call spread above a short put spread, opened near even money. It earns "
        "from the stock rising, so it is judged on what it can win and lose, not on its small premium."
    ),
    "risk reversal (bear)": (
        "This is a bearish risk reversal: long puts paid for by short calls. It earns from the stock "
        "falling, "
        "so it is judged on what it can win and lose, not on its small premium."
    ),
    "risk reversal (bull)": (
        "This is a bullish risk reversal: long calls paid for by short puts. It earns from the stock rising, "
        "so it is judged on what it can win and lose, not on its small premium."
    ),
    "broken-wing butterfly": (
        "This is a broken-wing butterfly: it pays most if the stock finishes at the short strike, and its "
        "risk sits beyond the wider wing. Bought cheaply against that tail, it is judged on what it can win "
        "and lose, not on its small premium."
    ),
    "jade lizard": (
        "This is a jade lizard: taken in for at least the call spread's width, so it has no upside risk; "
        "all of its risk is the short put."
    ),
    "jade lizard (put wing)": (
        "This is a jade lizard with a put wing: taken in for at least the call spread's width, so it has no "
        "upside risk; its risk is the put spread below."
    ),
    "big lizard": (
        "This is a big lizard: a short put at the short call's strike, under a call spread, taken in for at "
        "least the call spread's width, so it has no upside risk; all of its risk is the short put."
    ),
    "reverse jade lizard": (
        "This is a reverse jade lizard: taken in for at least the put spread's width, so it has no downside "
        "risk; all of its risk is the short call."
    ),
    "reverse jade lizard (call wing)": (
        "This is a reverse jade lizard with a call wing: taken in for at least the put spread's width, so "
        "it has no downside risk; its risk is the call spread above."
    ),
    "reverse big lizard": (
        "This is a reverse big lizard: a short call at the short put's strike, over a put spread, taken in "
        "for at least the put spread's width, so it has no downside risk; all of its risk is the short call."
    ),
    "short straddle": (
        "This is a short straddle: a short put and a short call at one strike. One side is nearly always in "
        "the money, and its risk is open on both sides."
    ),
    "short strangle": (
        "This is a short strangle: a short put below a short call. It earns while the stock stays between "
        "them, and its risk is open on both sides."
    ),
    "broken-heart butterfly": (
        "This is a broken-heart butterfly: a long spread nearer the money paid for by a wider short spread "
        "further out. It pays most if the stock finishes between the two, and its risk sits beyond the "
        "short spread, so bought cheaply against that tail it is judged on what it can win and lose."
    ),
    "call zebra": (
        "This is a call zebra: two long calls against one short call above them, a stock substitute "
        "whose risk is what was paid."
    ),
    "put zebra": (
        "This is a put zebra: two long puts against one short put below them, a short-stock substitute "
        "whose risk is what was paid."
    ),
    "call front-ratio": (
        "This is a call front-ratio: one long call against two short calls further out. The second short "
        "is naked, so its risk is above the short strike."
    ),
    "put front-ratio": (
        "This is a put front-ratio: one long put against two short puts further out. The second short is "
        "naked, so its risk is below the short strike."
    ),
}


def _ratio_note(t: dict[str, Any]) -> str | None:
    """A ratio butterfly's note, by form: its name is its quantities, or `ratio time spread`."""
    shape = t.get("shape") or ""
    if shape == "ratio time spread":
        what = "a ratio time spread: long-short-long at three strikes, two expiries, wings matching the body"
    elif _shapes.is_ratio(shape):
        what = f"a {shape} ratio butterfly: long-short-long at three strikes, the wings matching the body"
    else:
        return None
    judged = (
        " Its premium is small against what it can lose beyond the heavier wing, so it is judged on what it "
        "can win and lose, not on its premium."
        if t.get("kind") == "risk-judged"
        else ""
    )
    return f"This is {what}.{judged}"


def _shape_note(t: dict[str, Any]) -> str:
    note = SHAPE_NOTES.get(t.get("shape") or "") or _ratio_note(t)
    return f" {note}" if note else ""
