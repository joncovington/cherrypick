"""One clock for the module (ET, offset-carrying), plus the target-expiration plan.

Every timestamp this module persists is ET and carries its offset (the flies lesson: a naive
`datetime.now()` left a ledger unreadable without knowing which machine wrote it).

The target expiration is the NEXT PM-settled weekly Friday strictly after today: Monday through
Thursday enter for that week's Friday, Friday enters for the following one (1-7 DTE). This is the
rule the ledger has always recorded. The code once DESCRIBED a different one -- the weekly nearest
`dte_target` (7), ties to the longer date -- but a week-walk defect only ever offered the very next
Friday, so "nearest" never had a second candidate; it also excluded the third-Friday date outright,
which left the Friday before a monthly week with no plan at all (2026-09-11). Both were corrected on
2026-09-12 and the rule was stated as it actually ran, so the ladder stays comparable across the
fix. `dte_target` and `_nearest` stay for the backlog experiment (a minimum-DTE floor / nearest
selection), which is a measurement break when it comes and is not enabled here.

SPX lists both PM-settled weeklies (root SPXW, every Friday) and an AM-settled third-Friday monthly
(root SPX, the SET print) that shares its DATE with that week's weekly. The module trades the PM
weekly on that date too: what excludes the AM series is the OCC root the provider filters the chain
on (`occ_root`, SPXW), never the date.

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
    """Whether `d` is the third-Friday date the AM-settled SPX monthly (the SET print) shares with
    that week's PM-settled SPXW weekly. Informational: the plan still lands on this date, on the
    weekly; the AM series is kept out by the provider's OCC-root filter, not by the calendar."""
    return d.weekday() == _cal.FRI and _cal.nth_weekday(d.year, d.month, _cal.FRI, 3) == d


def weekly_fridays(today: date, weeks_ahead: int = 8) -> list[date]:
    """Every PM-settled weekly expiration date (Friday, holiday-shifted back to Thursday) strictly
    after `today`, for `weeks_ahead` weeks. The third-Friday date is included: the PM weekly is
    listed on it beside the AM monthly, and the root filter downstream separates the two."""
    # Walk Fridays by week index from the first Friday strictly after today. The previous cursor
    # stepped to the Saturday after each Friday, and a Saturday's "this week's Friday" is the day
    # before it -- the same Friday again -- so the walk never left week one: at most one date, and
    # NONE when that date was the (then excluded) monthly. 2026-09-11, a Friday with the 18th the
    # monthly: target None, every book refused `no_expiration_plan`, and it would have stayed that
    # way all of the following week.
    out: list[date] = []
    first = today - timedelta(days=today.weekday()) + timedelta(days=4)
    if first <= today:
        first += timedelta(days=7)
    for week in range(weeks_ahead):
        friday = first + timedelta(days=7 * week)
        candidate = friday
        while not _cal.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        if candidate > today:
            out.append(candidate)
    return sorted(set(out))


def target_expiration(today: date, params: dict | None = None) -> dict | None:
    """The target PM-settled weekly expiration for an entry on `today`: the next weekly Friday
    strictly after today (the third-Friday date included, traded on its PM weekly). Monday through
    Thursday land on that week's Friday; Friday lands on the following one. `am_monthly_date` says
    the chosen date also carries the AM monthly, so a reader can tell those weeks apart. None only
    on an impossible calendar stretch (never observed on the real NYSE calendar).

    `params["dte_target"]` is accepted and currently unused -- reserved for the backlog experiment
    (`_nearest` selection with a floor), which is a declared measurement break when it comes."""
    _dte_params(params)  # validates the shape; the value is reserved, see above
    weeklies = weekly_fridays(today)
    if not weeklies:
        return None
    chosen = weeklies[0]
    return {
        "expiration": chosen.isoformat(),
        "dte": (chosen - today).days,
        "pm_settled": True,
        "am_monthly_date": is_third_friday_monthly(chosen),
    }


def _nearest(candidates: list[date], today: date, dte_target: int) -> date:
    """Nearest candidate to `dte_target`, ties broken toward the LONGER (later) date."""

    def key(d: date) -> tuple[int, int]:
        dte = (d - today).days
        return (abs(dte - dte_target), -dte)  # -dte: at equal distance, larger dte sorts first

    return min(candidates, key=key)
