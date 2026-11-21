"""The sessions ahead: what each of an arm's date-based gates will do on the next sessions.

A module hands in its arm's gates and the one question the calendar answers for them, `on(label, day)`:
True when the day carries the label, False when it does not, None when the module's calendar does not
say (an FOMC year not yet bundled, a symbol whose earnings dates are not held). This lists the next
sessions from the market calendar (`core.calendar`) with each gate that fires there, the market days
closed between them, and the gates that read a value at the time (a VIX level), which no calendar can
answer ahead.

It also gives the arm's own record on each kind of day, from the per-session nets the module hands in
(its current measurement era, as `core.compare` reads it): the sessions that carried the label, and
their net and mean only once there are `MIN_SESSIONS` of them (the changes reading's floor), so a
handful of FOMC days is counted, never read as a result. A session whose label the calendar cannot
answer is counted apart, never as an ordinary day.

Pure: no clock, no file. The module's command supplies `start`, the gates, `on` and the nets.

    gate        {"label": key `on` answers, "name": words, "effect": what it does that day,
                 "depends_on": words or None, for a gate that fires on the day but reads a value too,
                 "record": False for a gate that refuses every entry that day, whose kind of day can
                 hold no trade of the arm's to read (default True)}
    value gate  {"name": words, "effect": what it does, "depends_on": what it reads at the time}
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta

from cherrypick.core import calendar as cal
from cherrypick.core.changes import MIN_SESSIONS

#: How many sessions are listed.
SESSIONS = 10

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: Labels the shared calendar answers by rule, for a module that names its gates by these.
CALENDAR_LABELS = ("FOMC", "TripleWitching", "QuarterlyExpiry", "MonthlyExpiry", "EarlyClose")

On = Callable[[str, date], "bool | None"]


def calendar_on(label: str, day: date) -> bool | None:
    """The shared calendar's answer for its own labels (`CALENDAR_LABELS`); None for any other label,
    and for FOMC in a year whose decision days are not bundled."""
    if label == "FOMC":
        return cal.is_fomc_day(day) if cal.fomc_year_known(day.year) else None
    if label == "TripleWitching":
        return cal.is_triple_witching(day)
    if label == "QuarterlyExpiry":
        return cal.is_quarterly_expiry(day)
    if label == "MonthlyExpiry":
        return cal.is_monthly_expiry(day)
    if label == "EarlyClose":
        return cal.is_early_close(day)
    return None


def next_sessions(start: date, n: int = SESSIONS) -> list[date]:
    """The first `n` trading days on or after `start`."""
    d = start if cal.is_trading_day(start) else cal.next_trading_day(start)
    out = []
    while len(out) < n:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def _closed_between(prev: date | None, day: date) -> list[str]:
    """Weekdays the market is closed after `prev` and before `day` (a holiday), ISO."""
    if prev is None:
        return []
    out, d = [], prev + timedelta(days=1)
    while d < day:
        if d.weekday() < 5 and cal.is_holiday(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _record(label: str, on: On, nets: list[tuple[date, float]], min_sessions: int) -> dict:
    hit, rest, unknown = [], [], 0
    for day, net in nets:
        v = on(label, day)
        if v is None:
            unknown += 1
        elif v:
            hit.append(net)
        else:
            rest.append(net)
    readable = len(hit) >= min_sessions and len(rest) >= min_sessions
    out = {
        "sessions": len(hit),
        "other_sessions": len(rest),
        "unknown_sessions": unknown,
        "readable": readable,
        "to_go": max(0, min_sessions - len(hit)),
        "net": None,
        "mean": None,
        "other_mean": None,
    }
    if readable:
        out["net"] = round(sum(hit), 2)
        out["mean"] = round(sum(hit) / len(hit), 2)
        out["other_mean"] = round(sum(rest) / len(rest), 2)
    return out


def sessions_ahead(
    start: date,
    gates: Iterable[Mapping],
    on: On,
    *,
    value_gates: Iterable[Mapping] = (),
    session_nets: Iterable[tuple[str, float]] | None = None,
    n: int = SESSIONS,
    min_sessions: int = MIN_SESSIONS,
) -> dict:
    """The next `n` sessions from `start` with what each gate does there, the value gates, and the
    arm's record on each gate's kind of day (`session_nets` as (ISO session, net); None when the module
    has no record to give)."""
    gates = [dict(g) for g in gates]
    sessions, prev = [], None
    for day in next_sessions(start, n):
        fires, unknown = [], []
        for g in gates:
            v = on(g["label"], day)
            if v is None:
                unknown.append(g["label"])
            elif v:
                fires.append({k: g.get(k) for k in ("label", "name", "effect", "depends_on")})
        sessions.append(
            {
                "session": day.isoformat(),
                "weekday": WEEKDAYS[day.weekday()],
                "close": cal.session_close_hhmm(day),
                "early_close": cal.is_early_close(day),
                "closed_before": _closed_between(prev, day),
                "gates": fires,
                "unknown": unknown,
            }
        )
        prev = day
    records = None
    if session_nets is not None:
        nets = [(date.fromisoformat(s), float(v)) for s, v in session_nets]
        seen: dict[str, dict] = {}
        for g in gates:
            if g.get("record", True) and g["label"] not in seen:
                seen[g["label"]] = {
                    "label": g["label"],
                    "name": g.get("name"),
                    **_record(g["label"], on, nets, min_sessions),
                }
        records = list(seen.values())
    return {
        "start": start.isoformat(),
        "n": n,
        "min_sessions": min_sessions,
        "sessions": sessions,
        "gates": [{k: g.get(k) for k in ("label", "name", "effect", "depends_on")} for g in gates],
        "value_gates": [{k: v.get(k) for k in ("name", "effect", "depends_on")} for v in value_gates],
        "records": records,
    }


__all__ = [
    "CALENDAR_LABELS",
    "MIN_SESSIONS",
    "SESSIONS",
    "calendar_on",
    "next_sessions",
    "sessions_ahead",
]
