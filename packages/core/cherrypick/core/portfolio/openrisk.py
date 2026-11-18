"""Open risk held at once: how much defined max loss a set of arms has open at the same time.

The portfolio reading says how arms' P&L moves together. This says how much they can lose together
at any one moment: each trade's defined maximum loss, whole-position dollars, held from its entry to
its exit, summed across every chosen arm. Several 0DTE arms open in one afternoon can each look small
and add up to a large worst case.

- **Per session, the peak** of that sum during the session (Eastern time), with the arms behind it.
  A position held overnight counts from the open of every session it spans.
- **The distribution of those daily peaks** over the sessions anything was open: median, 95th
  percentile, max; and each against `account` when one is given.
- **Unmeasured trades are named, never zero.** A trade with no recorded max loss (an undefined-risk
  shape, or a ledger that predates the column) is counted per arm under `unmeasured`, and left out
  of the sums, so a reading with any is a floor, and says so.
- **Times.** A ledger record's `entry_time` / `exit_time` (ISO, offset-aware); a backtest trade's
  session and minute of day (`entry_minute`, `exit_minute`; a settled trade has no exit minute and
  is held to the close). Where a source records no time, the trade is held for its whole sessions
  (09:30 to 16:00 ET), and is counted under `whole_session`.

Pure functions over records; nothing here reads a ledger or a file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET

OPEN = time(9, 30)
CLOSE = time(16, 0)
WORST_DAYS = 5


def _at(day: str, t: time) -> datetime:
    return datetime.combine(date.fromisoformat(day[:10]), t, tzinfo=ET)


def _parse(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d.replace(tzinfo=ET) if d.tzinfo is None else d.astimezone(ET)


def _minute(day: str, minute) -> datetime | None:
    try:
        m = int(minute)
    except (TypeError, ValueError):
        return None
    return datetime.combine(date.fromisoformat(day[:10]), time(0), tzinfo=ET) + timedelta(minutes=m)


def _risk(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None


def ledger_interval(record: Mapping) -> dict:
    """A ledger record (`core.ledgers`) as {start, end, risk, whole_session}: `capital` is its max loss."""
    start, end = _parse(record.get("entry_time")), _parse(record.get("exit_time"))
    whole = start is None or end is None
    close_day = record.get("session")
    open_day = record.get("entry_session") or close_day
    if start is None and open_day:
        start = _at(open_day, OPEN)
    if end is None and close_day:
        end = _at(close_day, CLOSE)
    return {"start": start, "end": end, "risk": _risk(record.get("capital")), "whole_session": whole}


def backtest_interval(trade: Mapping) -> dict:
    """An engine trade (`automations.engine`) as {start, end, risk, whole_session}: `risk` is its max loss."""
    entry_day = trade.get("session")
    exit_day = trade.get("exit_session") or entry_day
    start = _minute(entry_day, trade.get("entry_minute")) if entry_day else None
    end = _minute(exit_day, trade.get("exit_minute")) if exit_day else None
    whole = start is None
    if start is None and entry_day:
        start = _at(entry_day, OPEN)
    if end is None and exit_day:
        end = _at(exit_day, CLOSE)  # settled at the expiry's close
    return {"start": start, "end": end, "risk": _risk(trade.get("risk")), "whole_session": whole}


def _pct(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    i = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[i]


def open_risk(intervals: Mapping[str, Sequence[Mapping]], *, account: float | None = None) -> dict:
    """The open-risk reading of `intervals` ({arm key: [{start, end, risk, whole_session}]}).

    Per session the peak of the summed max loss open at once, the arms behind that peak, and the
    distribution of those peaks over the sessions anything was open. A trade closing at the same
    instant another opens is closed first."""
    events: list[tuple[datetime, int, str, float]] = []
    unmeasured: dict[str, int] = {}
    measured: dict[str, int] = {}
    whole: dict[str, int] = {}
    for key, rows in intervals.items():
        for iv in rows:
            if iv.get("start") is None or iv.get("end") is None:
                unmeasured[key] = unmeasured.get(key, 0) + 1
                continue
            if iv.get("risk") is None:
                unmeasured[key] = unmeasured.get(key, 0) + 1
                continue
            measured[key] = measured.get(key, 0) + 1
            if iv.get("whole_session"):
                whole[key] = whole.get(key, 0) + 1
            end = max(iv["end"], iv["start"])
            events.append((iv["start"], 1, key, float(iv["risk"])))
            events.append((end, 0, key, float(iv["risk"])))
    events.sort(key=lambda e: (e[0], e[1]))
    days: list[dict] = []
    if events:
        first, last = events[0][0].date(), events[-1][0].date()
        level: dict[str, float] = {}
        i = 0
        d = first
        while d <= last:
            # Events before this session's open (a weekend, a holiday) settle into the carried level.
            while i < len(events) and events[i][0].date() < d:
                _apply(level, events[i])
                i += 1
            if _cal.is_trading_day(d):
                peak_total = sum(level.values())
                peak_arms = dict(level)
                while i < len(events) and events[i][0].date() == d:
                    _apply(level, events[i])
                    i += 1
                    total = sum(level.values())
                    if total > peak_total:
                        peak_total, peak_arms = total, dict(level)
                if peak_total > 0:
                    days.append(
                        {
                            "session": d.isoformat(),
                            "peak": round(peak_total, 2),
                            "by_arm": {k: round(v, 2) for k, v in sorted(peak_arms.items()) if v > 0},
                        }
                    )
            d += timedelta(days=1)
    peaks = sorted(x["peak"] for x in days)
    stats = {"median": _pct(peaks, 0.5), "p95": _pct(peaks, 0.95), "max": peaks[-1] if peaks else 0.0}
    worst = sorted(days, key=lambda x: (-x["peak"], x["session"]))[:WORST_DAYS]
    return {
        "sessions": len(days),
        "peaks": {k: round(v, 2) for k, v in stats.items()},
        "account": account,
        "of_account": (
            {k: round(v / account, 4) for k, v in stats.items()} if account and account > 0 else None
        ),
        "worst_sessions": worst,
        "measured": measured,
        "unmeasured": unmeasured,
        "whole_session": whole,
        "floor": bool(unmeasured),
    }


def _apply(level: dict[str, float], event: tuple[datetime, int, str, float]) -> None:
    _, opening, key, risk = event
    level[key] = level.get(key, 0.0) + (risk if opening else -risk)
    if abs(level[key]) < 1e-9:
        level.pop(key)
