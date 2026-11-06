"""The time-spread scanner's pure parts (docs/time-spread-scanner-plan.md).

A calendar or diagonal sells a front expiry against a later one; the scanner looks for names whose
front week is priced rich to the back month. The hypothesis behind it -- a front-week premium the
stock does not then earn -- is UNTESTED (item 54), so rows are ordered by the front/back ratio and say
so, never ranked as if it were an edge.

This module reads nothing and writes nothing: `scripts/fetch_time_spreads.py` makes the broker reads
and hands the plain rows here.

Step 1, the term-structure read: from each name's market metrics, the front IV (the expiry
`front_days` out nearest the middle of that range), the back IV (a standard monthly `back_days` out
nearest the middle, else any expiry there), their ratio, and whether the name passes the prefilter.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from cherrypick.core import calendar as _calendar

RULE: dict[str, Any] = {
    "front_days": [5, 16],
    "back_days": [30, 75],
    "min_ratio": 1.15,
    "min_iv_rank": 0.30,
    # A stock's market cap floor; a fund has none and is kept.
    "min_market_cap": 20e9,
    "shortlist": 20,
}

HYPOTHESIS = "ordered by the front/back IV ratio: a hypothesis, untested (item 54)"


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def is_monthly(day: date) -> bool:
    """A standard monthly expiry: the third Friday, or the trading day before it when that is a holiday."""
    friday = date(day.year, day.month, 15)
    while friday.weekday() != 4:
        friday += timedelta(days=1)
    if not _calendar.is_trading_day(friday):
        friday = _calendar.previous_trading_day(friday)
    return day == friday


def pick(expiries: list[tuple[date, float]], session: date, days: list[int], monthly: bool) -> dict | None:
    """The expiry inside `days` nearest the middle of the range, a standard monthly first when
    `monthly`: {expiration, dte, iv}; None when none lists an IV there."""
    lo, hi = int(days[0]), int(days[1])
    inside = [(d, iv) for d, iv in expiries if iv and lo <= (d - session).days <= hi]
    if monthly and any(is_monthly(d) for d, _ in inside):
        inside = [(d, iv) for d, iv in inside if is_monthly(d)]
    if not inside:
        return None
    mid = (lo + hi) / 2
    d, iv = min(inside, key=lambda e: (abs((e[0] - session).days - mid), e[0]))
    return {"expiration": d.isoformat(), "dte": (d - session).days, "iv": iv}


def term_row(m: dict[str, Any], session: date, rule: dict[str, Any] = RULE) -> dict[str, Any]:
    """One name's term-structure row from its metrics (plain values: `symbol`, `iv_rank` a decimal,
    `iv_30d` and `hv_30d` in percent as the broker states them, `market_cap` dollars, `earnings_date`,
    `expiries` [(date or ISO string, decimal IV)]), judged against the prefilter: `passes`, and `why`
    it does not."""
    expiries = []
    for d, iv in m.get("expiries") or []:
        day, vol = _date(d), _f(iv)
        if day and vol and vol > 0:
            expiries.append((day, vol))
    front = pick(expiries, session, rule["front_days"], monthly=False)
    back = pick(expiries, session, rule["back_days"], monthly=True)
    ratio = round(front["iv"] / back["iv"], 4) if front and back else None
    cap, ivr = _f(m.get("market_cap")), _f(m.get("iv_rank"))
    er = _date(m.get("earnings_date"))
    why = []
    if not front or not back:
        why.append("no front or back expiry with an IV")
    elif ratio < float(rule["min_ratio"]):
        why.append(f"front/back ratio under {float(rule['min_ratio']):g}")
    if ivr is None or ivr < float(rule["min_iv_rank"]):
        why.append(f"IV rank under {float(rule['min_iv_rank']):.0%}")
    if cap and cap < float(rule["min_market_cap"]):
        why.append(f"market cap under ${float(rule['min_market_cap']) / 1e9:g}B")
    if er and back and session <= er <= _date(back["expiration"]):
        why.append("earnings before the back expiry")
    return {
        "symbol": str(m.get("symbol") or "").upper(),
        "front": front,
        "back": back,
        "ratio": ratio,
        "iv_rank": ivr,
        "iv_30d": _f(m.get("iv_30d")),
        "hv_30d": _f(m.get("hv_30d")),
        "market_cap": cap,
        "fund": not cap,
        "earnings_date": er.isoformat() if er else None,
        "sector": m.get("sector"),
        "passes": not why,
        "why": why,
    }


def shortlist(rows: list[dict[str, Any]], rule: dict[str, Any] = RULE) -> list[str]:
    """The passing names with the highest ratios, at most `shortlist` of them, best first (by name on
    a tie). The order is the untested hypothesis (`HYPOTHESIS`), not an edge."""
    passing = [r for r in rows if r["passes"]]
    passing.sort(key=lambda r: (-r["ratio"], r["symbol"]))
    return [r["symbol"] for r in passing[: int(rule["shortlist"])]]
