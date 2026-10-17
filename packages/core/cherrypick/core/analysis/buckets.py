"""P&L broken down by something known when a trade was entered: one engine, one column set.

docs/strategy-analysis-plan.md, Phase 3. Every table has the same columns -- trades, sessions, net,
win rate, average net, best, worst -- so a reader learns one table, not one per dimension. Every
row carries its sample, and a row with fewer than `MIN_SESSIONS` sessions is marked `thin`: trades
entered on one session are not independent, so it is sessions, not trades, that say how much a row
can carry. A thin row is shown, never hidden, and never read as a finding.

Dimensions, each a function from a record to a bucket name or None (not bucketable, counted):

- `weekday`  the session's day of the week
- `hour`     the hour of entry, in the timezone the module stamped (ET for every module here);
             None where the schema records no entry time
- `month`    the session's month
- `event`    the major scheduled releases on the session (`core.events`), joined; "none" when the
             calendar vouches for a day with no major release, and "unknown" when it cannot speak
             for the day -- an unknown day is never counted as a quiet one

These are historical associations over the trades the window holds, not predictions, and a
breakdown of a short sample is mostly noise: the `thin` mark is the reader's guard against it.
Market-regime buckets (`core.marketregime`) join once its artifact is written on the machine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from functools import lru_cache

from cherrypick.core import events as _events

#: A row with fewer sessions than this is marked thin.
MIN_SESSIONS = 5

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _session_date(r: Mapping) -> date | None:
    try:
        return date.fromisoformat(str(r.get("session"))[:10])
    except ValueError:
        return None


def weekday(r: Mapping) -> str | None:
    d = _session_date(r)
    return _WEEKDAYS[d.weekday()] if d else None


def hour(r: Mapping) -> str | None:
    t = r.get("entry_time")
    if not t:
        return None
    try:
        return f"{datetime.fromisoformat(str(t)).hour:02d}:00"
    except ValueError:
        return None


def month(r: Mapping) -> str | None:
    d = _session_date(r)
    return d.isoformat()[:7] if d else None


@lru_cache(maxsize=4096)
def _event_bucket(day: date) -> str:
    doc = _events.day_events(day)
    if not doc["known"]:
        return "unknown"
    major = sorted({e["label"] for e in doc["events"] if e.get("major")})
    return "+".join(major) if major else "none"


def event(r: Mapping) -> str | None:
    d = _session_date(r)
    return _event_bucket(d) if d else None


DIMENSIONS: dict[str, Callable[[Mapping], str | None]] = {
    "weekday": weekday,
    "hour": hour,
    "month": month,
    "event": event,
}


def _order(dimension: str, name: str) -> tuple:
    if dimension == "weekday":
        return (_WEEKDAYS.index(name) if name in _WEEKDAYS else 99, name)
    if dimension == "event":
        return ({"none": 0, "unknown": 2}.get(name, 1), name)
    return (0, name)


def table(records: Sequence[Mapping], key: Callable[[Mapping], str | None], dimension: str = "") -> dict:
    """One breakdown: a row per bucket, and how many records had no bucket (`skipped`)."""
    groups: dict[str, list[Mapping]] = {}
    skipped = 0
    for r in records:
        if r.get("net_pnl") is None:
            continue
        name = key(r)
        if name is None:
            skipped += 1
            continue
        groups.setdefault(name, []).append(r)
    rows = []
    for name, rs in groups.items():
        nets = [r["net_pnl"] for r in rs]
        sessions = len({r.get("session") for r in rs if r.get("session")})
        rows.append(
            {
                "bucket": name,
                "trades": len(nets),
                "sessions": sessions,
                "net": round(sum(nets), 2),
                "win_rate": round(sum(1 for v in nets if v > 0) / len(nets), 4),
                "avg_net": round(sum(nets) / len(nets), 2),
                "best": round(max(nets), 2),
                "worst": round(min(nets), 2),
                "thin": sessions < MIN_SESSIONS,
            }
        )
    rows.sort(key=lambda row: _order(dimension, row["bucket"]))
    return {"rows": rows, "skipped": skipped}


def breakdowns(records: Sequence[Mapping]) -> dict:
    """Every dimension's table for one arm's records, and the thin threshold they were marked at."""
    return {
        "min_sessions": MIN_SESSIONS,
        **{name: table(records, key, name) for name, key in DIMENSIONS.items()},
    }
