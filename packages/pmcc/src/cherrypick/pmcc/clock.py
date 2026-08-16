"""One clock for the module (ET, offset-carrying), plus the two-expiration plan arithmetic.

Every timestamp this module persists is ET and carries its offset — the same rule flies adopted
after a naive `datetime.now()` left its ledger unreadable without knowing which machine wrote it.

The expiration plan is the strategy's skeleton, so it lives here as pure date functions over
`cherrypick.core.calendar` and nothing else:

- The SHORT expiration is the soonest weekly Friday (holiday-shifted back to Thursday) whose DTE
  falls in `[short_dte_min, short_dte_max]` (defaults 6–12, targeting ~9 days).
- The LONG expiration is the weekly Friday whose DTE falls in `[long_dte_min, long_dte_max]`
  (defaults 17–25), nearest `long_dte_target` (~21), and strictly after the short.

Expirations are COMPUTED here and asserted against actual chain rows downstream, never selected
with a nearest-match helper: MEIC's 0DTE selector trap (a silent fallback to the next cycle) is the
standing lesson that a selector's output is never trusted without a post-hoc equality check. A
computed date the cache does not hold is a `no_short_chain`/`no_long_chain` refusal, never a
substitute date. Everything here takes DATES, not clocks, by construction — the stream request
derives its forward expirations from this and must only ever change value at an ET date boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    ET = pytz.timezone("America/New_York")

DTE_DEFAULTS = {
    "short_dte_min": 6,
    "short_dte_max": 12,
    "short_dte_target": 9,
    "long_dte_min": 17,
    "long_dte_max": 25,
    "long_dte_target": 21,
}


def now_et() -> datetime:
    """Timezone-aware 'now' in Eastern."""
    return datetime.now(ET)


def now_iso() -> str:
    """ET timestamp for persistence: seconds precision, offset included."""
    return now_et().isoformat(timespec="seconds")


def today_iso() -> str:
    """Today's ET date — deliberately not the local date (a late-evening Pacific run is already
    tomorrow in ET, which would file a session under the wrong day)."""
    return now_et().date().isoformat()


def minute_of_day(when: datetime) -> int:
    return when.hour * 60 + when.minute


def hhmm_to_min(value: str, default: int) -> int:
    """A config 'HH:MM' as minutes-of-day, falling back rather than crashing on junk."""
    try:
        hours, minutes = str(value).split(":")
        return int(hours) * 60 + int(minutes)
    except (TypeError, ValueError, AttributeError):
        return default


# --------------------------------------------------------------------------- expiration anchors
def weekly_expiration(day: date, weeks_ahead: int) -> date | None:
    """The weekly expiration of the calendar week `weeks_ahead` weeks after `day`'s: its Friday,
    holiday-shifted BACK (Good Friday → Thursday). None if the whole week is dark, which the NYSE
    calendar does not produce — kept as None rather than an exception so a caller can treat an
    impossible week as absent rather than a crash."""
    monday = day - timedelta(days=day.weekday()) + timedelta(days=7 * weeks_ahead)
    candidate = monday + timedelta(days=4)
    while candidate >= monday:
        if _cal.is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def candidate_expirations(today: date, weeks: int = 6) -> list[date]:
    """The next `weeks` weekly expirations strictly after `today`, this week's included when it has
    not passed. The plan below picks from these; nothing else generates dates."""
    out = []
    for ahead in range(weeks):
        exp = weekly_expiration(today, ahead)
        if exp is not None and exp > today and exp not in out:
            out.append(exp)
    return sorted(out)


def _dte_params(params: dict | None) -> dict:
    return {**DTE_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DTE_DEFAULTS}}


def expiration_plan(today: date, params: dict | None = None) -> dict | None:
    """The two computed expirations for an entry on `today`, or None when the calendar cannot
    produce a valid pair (both DTE windows empty of Fridays — rare, holiday-compressed stretches).

    Returned DTEs are CALENDAR days from `today`, which is what the yield arithmetic wants (time
    value decays over calendar days, weekends included)."""
    p = _dte_params(params)
    candidates = candidate_expirations(today)
    short = None
    for exp in candidates:
        dte = (exp - today).days
        if p["short_dte_min"] <= dte <= p["short_dte_max"]:
            short = exp
            break
    if short is None:
        return None
    long_candidates = [
        exp
        for exp in candidates
        if exp > short and p["long_dte_min"] <= (exp - today).days <= p["long_dte_max"]
    ]
    if not long_candidates:
        return None
    long_exp = min(long_candidates, key=lambda e: abs((e - today).days - p["long_dte_target"]))
    return {
        "short_expiration": short.isoformat(),
        "long_expiration": long_exp.isoformat(),
        "short_dte": (short - today).days,
        "long_dte": (long_exp - today).days,
    }


def roll_expiration(today: date, long_expiration: str, params: dict | None = None) -> dict | None:
    """Where a rolled short may land: the candidate Friday nearest `short_dte_target` from `today`
    that is on or before the held long's expiration (the short must never outlive its cover), or
    None when nothing qualifies. The current short's own expiration is a valid answer — a pure
    roll-down. DTE bounds are deliberately NOT applied here: near the long's own expiry the only
    legal landing spot may be a shorter date, and refusing it would strand the breach unmanaged."""
    p = _dte_params(params)
    limit = date.fromisoformat(long_expiration)
    eligible = [exp for exp in candidate_expirations(today) if exp <= limit]
    if not eligible:
        return None
    chosen = min(eligible, key=lambda e: abs((e - today).days - p["short_dte_target"]))
    return {"expiration": chosen.isoformat(), "dte": (chosen - today).days}
