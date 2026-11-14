"""Monthly expiries and entry sessions, as the study scripts compute them.

Two families, kept apart because they answer different questions:

- **From the calendar** (`monthly_expiry`, `monthlies_between`): the standard monthly of a month is
  its third Friday, or the trading day before when that Friday is an exchange holiday. Copied from
  items 30, 38, 57 (via 46), 64 and 74, which agree.
- **From a listing** (`listed_monthlies`): of the expiries a chain store lists, each month's third
  Friday, or the Thursday before it when the Friday is not listed (a holiday moved it). Copied from
  item 59 (item 74 loads it from there).

`entry_session` takes its tenor and band as required arguments: they are declared per study.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as cal


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before when that is a holiday."""
    d = third_friday(year, month)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def monthlies_between(first: date, last: date) -> list[date]:
    """Every standard monthly from `first` to `last`, both included."""
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def is_listed_monthly(e: str) -> bool:
    """The third Friday, or the Thursday before it (an exchange holiday moved the expiry)."""
    d = date.fromisoformat(e)
    return (d.weekday() == 4 and 15 <= d.day <= 21) or (d.weekday() == 3 and 14 <= d.day <= 20)


def listed_monthlies(expirations: list[str]) -> list[str]:
    """One standard monthly per month of the listed `expirations`: its Friday, or the Thursday when
    no Friday is listed."""
    chosen: dict[str, str] = {}
    for e in sorted(expirations):
        if not is_listed_monthly(e):
            continue
        prev = chosen.get(e[:7])
        if prev is None or date.fromisoformat(e).weekday() == 4:
            chosen[e[:7]] = e
    return sorted(chosen.values())


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def entry_session(sessions: list[str], expiry: str, target: int, band: tuple[int, int]) -> str | None:
    """The session nearest `target` calendar days before `expiry`, inside `band` (both ends
    included); the earlier on a tie. None when no session falls inside the band."""
    inside = [s for s in sessions if band[0] <= days_between(s, expiry) <= band[1]]
    if not inside:
        return None
    return min(inside, key=lambda s: (abs(days_between(s, expiry) - target), s))
