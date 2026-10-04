"""One clock for the module (ET, offset-carrying), plus the two-expiration plan arithmetic.

Every timestamp this module persists is ET and carries its offset — the same rule flies adopted
after a naive `datetime.now()` left its ledger unreadable without knowing which machine wrote it.

The expiration plan is the strategy's skeleton, so it lives here as pure date functions over
`cherrypick.core.calendar` and nothing else:

- The SHORT expiration is the soonest weekly Friday (holiday-shifted back to Thursday) whose DTE
  falls in `[short_dte_min, short_dte_max]` (defaults 5–9, targeting ~7 days — since the 2026-08-23
  redesign to a pure-ATM weekly short).
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

from datetime import date, timedelta

from cherrypick.core import calendar as _cal

# ET and the "what does now mean" primitives live in cherrypick.core.clock: four modules had written
# the same functions and ~10 more sites re-derived the zone inline, which is how two of them come to
# disagree about what date a session belongs to. The arithmetic BELOW is this module's own.
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso, today_iso  # noqa: F401

DTE_DEFAULTS = {
    # Width matters: weekly Fridays are 7 days apart, and the window has to be at least that wide
    # (measured against the two Fridays bracketing any weekday) or some weekdays land in the gap
    # between them and refuse `no_expiration_plan` outright. [5, 11] is width 6, centered near the
    # ~7-day target, and was verified against every weekday (Mon-Fri) before landing.
    "short_dte_min": 5,
    "short_dte_max": 11,
    "short_dte_target": 7,
    "long_dte_min": 17,
    "long_dte_max": 25,
    "long_dte_target": 21,
}


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


# --------------------------------------------------------------------------- the held-long plan
# A held-long arm (`lifecycle: held_long`) buys a long of ~1 year and sells a weekly short against it
# every week. The weekly short is chosen exactly as above; the long cannot be: ~1-year expirations
# are LISTED months (monthlies, quarterlies, January LEAPs) that differ per symbol, so it is picked
# from the broker's own listing (`streamcache.stream_expirations`) within a declared DTE band. A
# listing is a fact about which dates exist, not a nearest-match of quotes, and a date the listing
# lacks is a refusal (`no_leap_listed`), never a substitute.
LEAP_DEFAULTS = {"leap_dte_min": 240, "leap_dte_max": 540, "leap_dte_target": 360}


def standard_monthly(year: int, month: int) -> date:
    """The month's standard expiration: its third Friday, moved back a day when that is a holiday
    (2027-06-18, Juneteenth observed, expires Thursday 06-17)."""
    first = date(year, month, 1)
    friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    while not _cal.is_trading_day(friday):
        friday -= timedelta(days=1)
    return friday


def leap_expiration(listed: list[str], today: date, params: dict | None = None) -> dict | None:
    """The listed expiration whose DTE falls in `[leap_dte_min, leap_dte_max]` nearest
    `leap_dte_target`, a tie taking the nearer date (less capital). None when the listing has none.

    **Standard monthlies first.** A ~1-year horizon lists two kinds of date: the standard monthly
    (third Friday) and end-of-quarter or end-of-month expirations. Probed 2026-10-04, only the
    monthlies list deep strikes: SLV's 2027-09-17 goes down to 5 where its 2027-09-30 stops at 39
    (a ~0.85-delta call at a 54.74 spot, outside the 0.90-0.95 band, so a refusal every session);
    QQQ's to 285 against 525. The nearest-to-360 rule alone chose the quarterly. So the pick is made
    among the standard monthlies in the band, and falls back to any listed date only when none is."""
    p = {**LEAP_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in LEAP_DEFAULTS}}
    in_band: list[tuple[date, int]] = []
    for value in listed:
        try:
            exp = date.fromisoformat(str(value))
        except ValueError:
            continue
        dte = (exp - today).days
        if p["leap_dte_min"] <= dte <= p["leap_dte_max"]:
            in_band.append((exp, dte))
    monthlies = [(e, d) for e, d in in_band if e == standard_monthly(e.year, e.month)]
    best: tuple[date, int] | None = None
    for exp, dte in monthlies or in_band:
        gap = abs(dte - p["leap_dte_target"])
        if (
            best is None
            or gap < abs(best[1] - p["leap_dte_target"])
            or (gap == abs(best[1] - p["leap_dte_target"]) and dte < best[1])
        ):
            best = (exp, dte)
    if best is None:
        return None
    return {"long_expiration": best[0].isoformat(), "long_dte": best[1]}


def short_expiration(today: date, params: dict | None = None, *, cap: str | None = None) -> dict | None:
    """The weekly short's expiration: the soonest Friday in `[short_dte_min, short_dte_max]`, never
    after `cap` (the long's own expiry -- a short must never outlive its cover). The rule entry uses,
    so a roll lands where an entry on the same day would."""
    p = _dte_params(params)
    for exp in candidate_expirations(today):
        dte = (exp - today).days
        if cap is not None and exp.isoformat() > cap:
            return None
        if p["short_dte_min"] <= dte <= p["short_dte_max"]:
            return {"short_expiration": exp.isoformat(), "short_dte": dte}
    return None


def held_long_plan(today: date, listed: list[str], params: dict | None = None) -> dict | None:
    """A held-long entry's two expirations, or None: the weekly short as `short_expiration` picks
    it, and the long from the listing as `leap_expiration` picks it."""
    leap = leap_expiration(listed, today, params)
    if leap is None:
        return None
    short = short_expiration(today, params, cap=leap["long_expiration"])
    if short is None:
        return None
    return {**short, **leap}


def session_close_min(day: date) -> int:
    """The session's close in minutes after midnight ET: 16:00, or 13:00 on an early close. Every
    time anchored to the bell (the roll, the roll deadline, the settlement pass) is measured from
    this, never from a constant -- a Friday roll at 15:00 on 2026-11-27 would land two hours after
    the market closed."""
    return hhmm_to_min(_cal.session_close_hhmm(day), 16 * 60)
