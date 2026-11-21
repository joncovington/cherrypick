"""An automation's date-based gates, in words, for the sessions ahead (`core.ahead`).

What `engine` does on a dated kind of day: the weekdays an automation enters on, the event labels its
entry skips (`skip_events`), and the events its exits close ahead of (`avoid_events`, on the session
before), answered by the automation's own event calendar (`events.EventCalendar`), so a label it holds
no data for reads unknown here as it does at entry. Its filter gates read a value at the time, so they
are listed in the Builder's words (`words.GATES`) and answered by no calendar.
"""

from __future__ import annotations

import re
from datetime import date

from cherrypick.core import ahead as core_ahead
from cherrypick.core import calendar as cal

from . import events, words

DAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays")
OFF_WEEKDAY = "OffWeekday"
BEFORE = "Before:"


def _event_name(label: str) -> str:
    return events.ABOUT.get(label, (label, ""))[0]


def date_gates(arm: dict) -> list[dict]:
    """The automation's gates that fire on a dated kind of day."""
    out = []
    days = arm.get("weekdays")
    if days is not None and sorted(days) != [0, 1, 2, 3, 4]:
        named = ", ".join(DAY_NAMES[d] for d in sorted(days) if 0 <= d < 5) or "no day"
        out.append(
            {
                "label": OFF_WEEKDAY,
                "name": "Weekdays",
                "effect": f"No entry: it enters on {named} only.",
                "depends_on": None,
                "record": False,
            }
        )
    skip = arm.get("skip_events") or {}
    for label in skip.get("labels", []):
        out.append(
            {
                "label": label,
                "name": _event_name(label),
                "effect": "No entry.",
                "depends_on": (
                    "no entry on an earlier session either, where the trade would expire on or after it"
                    if skip.get("through_expiry")
                    else None
                ),
                "record": False,
            }
        )
    avoid = (arm.get("exit") or {}).get("avoid_events")
    if avoid:
        minutes = avoid.get("before_close_min", 60)
        for label in avoid.get("labels", []):
            out.append(
                {
                    "label": BEFORE + label,
                    "name": f"Before {_event_name(label)}",
                    "effect": f"Open positions close {minutes} minutes before the close.",
                    "depends_on": None,
                }
            )
    return out


def _fill(sentence: str, gate: dict) -> str:
    lo, hi = gate.get("min"), gate.get("max")
    if lo is not None and hi is not None:
        rng = words.RANGE_WORDS["both"].format(min=lo, max=hi)
    elif lo is not None:
        rng = words.RANGE_WORDS["min"].format(min=lo)
    elif hi is not None:
        rng = words.RANGE_WORDS["max"].format(max=hi)
    else:
        rng = "any"
    return re.sub(
        r"\{([\w.]+)\}",
        lambda m: rng if m.group(1) == words.RANGE else str(gate.get(m.group(1), "")),
        sentence,
    )


def _phrase(about: str | None) -> str | None:
    """A Builder sentence as the phrase after "depends on": no capital (unless an acronym), no stop."""
    if not about:
        return None
    s = about.rstrip(".")
    return s if s[:2].isupper() else s[:1].lower() + s[1:]


def value_gates(arm: dict) -> list[dict]:
    """The automation's filter gates: each reads a value at the time."""
    out = []
    for g in arm.get("gates", []):
        w = words.GATES.get(g.get("gate"), {})
        out.append(
            {
                "name": w.get("label", g.get("gate")),
                "effect": f"Enters only when {_fill(w['sentence'], g)}." if "sentence" in w else None,
                "depends_on": _phrase(w.get("about")) or "the reading at the time",
            }
        )
    return out


def on_for(arm: dict, calendar: events.EventCalendar):
    """`on(label, day)` over the automation's own calendar and symbol."""
    days = arm.get("weekdays")
    symbol = arm.get("symbol")

    def on(label: str, day: date) -> bool | None:
        if label == OFF_WEEKDAY:
            return day.weekday() not in (days or [])
        if label.startswith(BEFORE):
            nxt = cal.next_trading_day(day)
            return calendar.occurs(label[len(BEFORE) :], nxt, nxt, symbol)
        return calendar.occurs(label, day, day, symbol)

    return on


def ahead(arm: dict, calendar: events.EventCalendar, start: date, session_nets=None) -> dict:
    """`core.ahead.sessions_ahead` over this automation's gates and calendar."""
    return core_ahead.sessions_ahead(
        start,
        date_gates(arm),
        on_for(arm, calendar),
        value_gates=value_gates(arm),
        session_nets=session_nets,
    )
