"""One clock for the module (ET, offset-carrying), plus the target-expiration plan.

Every timestamp this module persists is ET and carries its offset (the flies lesson: a naive
`datetime.now()` left a ledger unreadable without knowing which machine wrote it).

The target expiration is the PM-settled expiration nearest `dte_target` (default 7) calendar days
out. SPX lists both PM-settled weeklies (SPXW, every Friday) and an AM-settled third-Friday monthly
(the SET print) that shares its date with that week's weekly. When the nearest-to-target date IS
that AM monthly, the plan shifts to the nearest PM weekly instead — ties broken toward the LONGER
date (more premium, and it stays ahead of the ladder).

Expirations are COMPUTED here and asserted against actual chain rows downstream, never selected
with a nearest-match helper that could silently substitute a different date (the MEIC 0DTE
selector trap, the standing suite lesson): a computed date the cache does not hold is a `no_chain`
refusal, never a substitute date.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso, today_iso  # noqa: F401

DTE_DEFAULTS = {"dte_target": 7}


def _dte_params(params: dict | None) -> dict:
    return {**DTE_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DTE_DEFAULTS}}


def is_third_friday_monthly(d: date) -> bool:
    """Whether `d` is the AM-settled third-Friday monthly cycle date (the SET print)."""
    return d.weekday() == _cal.FRI and _cal.nth_weekday(d.year, d.month, _cal.FRI, 3) == d


def weekly_fridays(today: date, weeks_ahead: int = 8) -> list[date]:
    """Every PM-settled weekly expiration (Friday, holiday-shifted back to Thursday) strictly
    after `today`, for `weeks_ahead` weeks — the third-Friday MONTHLY date is excluded on purpose,
    since it settles AM and this module never trades it."""
    out: list[date] = []
    cursor = today
    for _ in range(weeks_ahead):
        friday = cursor - timedelta(days=cursor.weekday()) + timedelta(days=4)
        if friday <= today:
            friday += timedelta(days=7)
        candidate = friday
        while not _cal.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        if candidate > today and not is_third_friday_monthly(candidate):
            out.append(candidate)
        cursor = friday + timedelta(days=1)
    return sorted(set(out))


def target_expiration(today: date, params: dict | None = None) -> dict | None:
    """The target PM-settled expiration for an entry on `today`.

    Candidates are the next several weekly (PM-settled) Fridays plus, separately, whatever the
    unfiltered nearest-to-target date would have been (to detect the AM-monthly-shift case). Ties
    on distance from `dte_target` break toward the LONGER date. None only on an impossible
    calendar stretch (never observed on the real NYSE calendar)."""
    p = _dte_params(params)
    weeklies = weekly_fridays(today)
    if not weeklies:
        return None
    chosen = _nearest(weeklies, today, p["dte_target"])
    return {"expiration": chosen.isoformat(), "dte": (chosen - today).days, "pm_settled": True}


def _nearest(candidates: list[date], today: date, dte_target: int) -> date:
    """Nearest candidate to `dte_target`, ties broken toward the LONGER (later) date."""

    def key(d: date) -> tuple[int, int]:
        dte = (d - today).days
        return (abs(dte - dte_target), -dte)  # -dte: at equal distance, larger dte sorts first

    return min(candidates, key=key)
