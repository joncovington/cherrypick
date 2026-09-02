"""One clock for the module (ET, offset-carrying), plus the monthly-cycle expiration plan.

Every timestamp this module persists is ET and carries its offset (the flies lesson: a naive
`datetime.now()` left a ledger unreadable without knowing which machine wrote it).

VXX's own option cycle is standard monthly (third-Friday-anchored, like most single-name/ETF
chains) — no weekly VIX-complex quirks to model, unlike VIX options' Wednesday expirations. The
target expiration is the monthly-cycle Friday nearest `dte_target` calendar days out, inside
`[dte_min, dte_max]`.

Expirations are COMPUTED here and asserted against actual chain rows downstream, never selected
with a nearest-match helper that could silently substitute a different date (the MEIC 0DTE
selector trap, the standing suite lesson): a computed date the cache does not hold is a
`no_chain` refusal, never a substitute date.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso, today_iso  # noqa: F401

DTE_DEFAULTS = {
    "dte_min": 25,
    "dte_max": 50,
    "dte_target": 35,
}


def _dte_params(params: dict | None) -> dict:
    return {**DTE_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DTE_DEFAULTS}}


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(weeks=2)


def monthly_expirations(today: date, months_ahead: int = 4) -> list[date]:
    """The next `months_ahead` monthly-cycle expirations (third Friday, holiday-shifted back to
    Thursday) strictly after `today`."""
    out: list[date] = []
    year, month = today.year, today.month
    for _ in range(months_ahead + 1):
        exp = _third_friday(year, month)
        while not _cal.is_trading_day(exp):
            exp -= timedelta(days=1)
        if exp > today:
            out.append(exp)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return sorted(out)


def target_expiration(today: date, params: dict | None = None) -> dict | None:
    """The target monthly expiration for an entry on `today`, or None when no candidate falls
    inside `[dte_min, dte_max]`.

    That None is **not** the rare holiday-compressed edge this docstring claimed until 2026-08-26.
    Consecutive monthlies sit 28-35 days apart, so just after one rolls off the next is under
    `dte_min` while the one behind it is over `dte_max`, and nothing qualifies. On the original
    25-50 band that was 42 of 251 trading days in 2026 -- 17%, every month, worst run seven
    consecutive sessions -- and it is why this module had never opened a position: it was registered
    into one of those runs. The band is config, and `tests/test_expiration_window.py` fails if the
    deployed one reopens a gap.
    """
    p = _dte_params(params)
    candidates = [
        exp for exp in monthly_expirations(today) if p["dte_min"] <= (exp - today).days <= p["dte_max"]
    ]
    if not candidates:
        return None
    chosen = min(candidates, key=lambda e: abs((e - today).days - p["dte_target"]))
    return {"expiration": chosen.isoformat(), "dte": (chosen - today).days}
