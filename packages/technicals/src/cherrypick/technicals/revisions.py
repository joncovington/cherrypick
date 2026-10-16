"""Analyst revisions: the vendor's board method, as a pure rule over per-firm rating events.

**The rule, as the week-3 deck states it.** A name earns an Analyst Revision tag from an upgrade or a
downgrade, an initiation that starts bullish or bearish, or a price-target change of 5% or more.
A reiteration with an unchanged target never counts, and a rating that merely sits at Buy is not a
signal (six in ten S&P 500 ratings are Buy).

**The window, fitted on the saved editions (2026-10-15).** Every tag matched to an event fell on the
session the edition describes: 33 of 33, lag zero. So a tag is that session's events only, never a
trailing window. Scored on 13 boards (`score-revisions`): 32 of 37 tags agree, and 1 of 77 untagged
rows is an extra (LIN, a Barclays initiation at Overweight). The five misses are the source's: four
events Yahoo lacks (Bernstein, Erste, two single notes) and DOCS, whose Evercore target Yahoo has as
40 -> 35 where the vendor's "+14%" is 35 -> 40. The 2026-10-08 edition carries no revision tag on any
row (the method looks absent that day) and is reported apart, never scored.

**Direction when a session mixes.** A rating change (up, down, an initiation with a side) outranks a
target change, and within a rank the largest target move decides. One case fits it (MU, 2026-09-23:
Citigroup +13% against Wells Fargo -8% was tagged Bullish); it is a choice, not a measurement.

**Not reproduced: which firm the tag names.** The vendor names one event (META's Citizens +15% over
Raymond James +32% the same day) by no rule found -- not the first, the last or the largest.

Events come from `scripts/fetch_analyst_revisions.py` (Yahoo's per-firm history), read-only here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core.clock import ET

from . import paths

PT_MIN = 0.05

# Rating words by side. Anything else (Hold, Neutral, Equal-Weight, Market Perform, Peer Perform,
# Sector Weight, In-Line, ...) is neutral: an initiation there does not count.
BULLISH_GRADES = frozenset(
    {
        "buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "add",
        "market outperform", "sector outperform", "moderate buy", "conviction buy", "top pick",
        "long-term buy", "speculative buy", "outperformer",
    }
)  # fmt: skip
BEARISH_GRADES = frozenset(
    {
        "sell", "strong sell", "underperform", "underweight", "reduce", "negative",
        "market underperform", "sector underperform", "moderate sell", "underperformer",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Event:
    symbol: str
    graded_at: datetime  # UTC
    firm: str
    from_grade: str
    to_grade: str
    action: str  # up, down, init, main, reit
    pt_prior: float | None
    pt_current: float | None


@dataclass(frozen=True)
class Tag:
    direction: int  # +1 bullish, -1 bearish
    events: tuple[Event, ...]  # every qualifying event that session, in time order


def grade_side(grade: str | None) -> int:
    g = (grade or "").strip().lower()
    return 1 if g in BULLISH_GRADES else -1 if g in BEARISH_GRADES else 0


def pt_change(e: Event) -> float | None:
    """The target's relative move, or None where either side is missing (Yahoo writes 0)."""
    if not e.pt_prior or not e.pt_current or e.pt_prior <= 0 or e.pt_current <= 0:
        return None
    return e.pt_current / e.pt_prior - 1


def score(e: Event, pt_min: float = PT_MIN) -> int:
    """+1 / -1 for an event that earns the tag, 0 for one that does not."""
    if e.action == "up":
        return 1
    if e.action == "down":
        return -1
    if e.action == "init":
        return grade_side(e.to_grade)
    move = pt_change(e)
    if move is not None and abs(move) >= pt_min:
        return 1 if move > 0 else -1
    return 0


def session_of(graded_at: datetime) -> str:
    """The trading session an event belongs to: its New York date, rolled forward off a weekend or
    holiday. Every matched tag was a pre-close event, so where an after-close note lands is untested."""
    d = graded_at.astimezone(ET).date()
    return d.isoformat() if _calendar.is_trading_day(d) else _calendar.next_trading_day(d).isoformat()


def _rank(e: Event) -> tuple[int, float]:
    return (0 if e.action in ("up", "down", "init") else 1, -abs(pt_change(e) or 0.0))


def tags(events: list[Event], session: str, pt_min: float = PT_MIN) -> dict[str, Tag]:
    """{symbol: Tag} for the session: every symbol with at least one qualifying event that day."""
    by_symbol: dict[str, list[Event]] = {}
    for e in events:
        if session_of(e.graded_at) == session and score(e, pt_min):
            by_symbol.setdefault(e.symbol, []).append(e)
    out = {}
    for sym, evs in by_symbol.items():
        lead = min(evs, key=_rank)
        out[sym] = Tag(score(lead, pt_min), tuple(sorted(evs, key=lambda e: e.graded_at)))
    return out


def score_boards(events: list[Event], boards: dict) -> dict:
    """Our tags against the vendor's on every board row. `boards` is `editions.load_boards()`.

    Only board rows are scored: the vendor prints its top names, so a name we tag that is not on the
    board says nothing. A session whose board carries no revision tag on any row is reported under
    `method_absent` and not counted, since the method, not the names, is what is missing then.
    A tag we miss is `not_in_source` when the source holds no event at all for that name that day
    (a coverage gap, not a rule error)."""
    out = {
        "sessions": 0,
        "tagged": 0,
        "agree": 0,
        "opposite": [],
        "missed": [],
        "not_in_source": [],
        "untagged": 0,
        "extra": [],
        "method_absent": [],
    }
    for session, rows in sorted(boards.items()):
        if not any(r.revision for r in rows.values()):
            out["method_absent"].append(session)
            continue
        out["sessions"] += 1
        ours = tags(events, session)
        that_day = {e.symbol for e in events if session_of(e.graded_at) == session}
        for sym, row in sorted(rows.items()):
            got = ours.get(sym)
            if row.revision:
                out["tagged"] += 1
                case = f"{session} {sym}: {row.revision_detail}"
                if got and got.direction == row.revision:
                    out["agree"] += 1
                elif got:
                    out["opposite"].append(case)
                elif sym in that_day:
                    out["missed"].append(case)
                else:
                    out["not_in_source"].append(case)
            else:
                out["untagged"] += 1
                if got:
                    lead = got.events[0]
                    out["extra"].append(f"{session} {sym}: {lead.firm} {lead.action} {lead.to_grade}".strip())
    return out


def load(path: Path | None = None, since: date | None = None) -> list[Event]:
    """Every stored event (from `since`, if given), read-only. No file is an empty list."""
    path = path or paths.analyst_revisions()
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        query = (
            "SELECT symbol, graded_at, firm, from_grade, to_grade, action, pt_prior, pt_current "
            "FROM revision_events"
        )
        args: tuple = ()
        if since is not None:
            query += " WHERE graded_at >= ?"
            args = (since.isoformat(),)
        rows = conn.execute(query, args).fetchall()
    finally:
        conn.close()
    return [
        Event(s, datetime.fromisoformat(g).astimezone(UTC), f, fg or "", tg or "", a, pp, pc)
        for s, g, f, fg, tg, a, pp, pc in rows
    ]
