"""Time / market-calendar helpers (Eastern Time aware).

Freshness and deadline checks only make sense against the trading session, so these helpers
answer: what's the ET time now, is today a trading day, is the market open right now. Holidays
come from `cherrypick.core.calendar`, the suite's one calendar — never a second hardcoded list here;
if that lookup fails we degrade to a weekday-only check rather than failing the caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from datetime import time as dtime

try:  # stdlib first (no third-party dep)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


def _tz(name: str):
    """Resolve an IANA tz. Prefer stdlib ``zoneinfo`` — which needs a tz database; the ``tzdata``
    dependency supplies one on Windows (which ships none). ``ZoneInfo`` imports fine but raises
    ``ZoneInfoNotFoundError`` at call time when no database is present, so the ``pytz`` fallback must be
    at call time, not import time (the earlier import-time guard was dead code on a db-less Windows)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    import pytz

    return pytz.timezone(name)


_DEFAULT_TZ = "America/New_York"
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
NEAR_OPEN = dtime(9, 15)  # start data services a bit before the bell


def now_et(tz_name: str = _DEFAULT_TZ) -> datetime:
    return datetime.now(_tz(tz_name))


def et_from_epoch(epoch: float, tz_name: str = _DEFAULT_TZ) -> datetime:
    """A specific instant (unix epoch seconds) rendered in market time — for callers that already
    hold a `now` timestamp (e.g. for testability) and need its ET wall-clock, without a second live
    `now_et()` call that a test can't control."""
    return datetime.fromtimestamp(epoch, _tz(tz_name))


def to_local_hhmm(hhmm: str, tz_name: str = _DEFAULT_TZ) -> str:
    """Convert a wall-clock ``HH:MM`` expressed in ``tz_name`` (the market timezone) to the host's
    local ``HH:MM``.

    Daily OS-scheduler triggers (Windows ``schtasks /ST``, POSIX cron) fire on the machine's local
    time, but the suite expresses entry/exit/digest times in the market timezone (config
    ``timezone``). Without this, a "15:45" ET entry registered on a non-ET host fires at 15:45
    *local* — e.g. 17:45 ET on a Mountain-time box, after the close.

    The offset is resolved against today's date so DST is handled for the common case. Caveat: on a
    host whose DST rules differ from the market's (e.g. Arizona, which never observes DST) the baked
    local time drifts by an hour across a market DST transition until ``install`` is re-run; hosts
    that share US DST (ET/CT/MT/PT) stay correct year-round.
    """
    hh, mm = (int(x) for x in hhmm.split(":"))
    market = _tz(tz_name)
    today = datetime.now(market).date()
    naive = datetime(today.year, today.month, today.day, hh, mm)
    if hasattr(market, "localize"):  # pytz fallback needs localize(), not tzinfo=
        aware = market.localize(naive)
    else:  # zoneinfo
        aware = naive.replace(tzinfo=market)
    return aware.astimezone().strftime("%H:%M")


def load_holidays(years: Iterable[int] | None = None) -> set[str]:
    """NYSE holiday ISO dates from the shared calendar (`cherrypick.core.calendar`).

    This used to scan MEIC's config for `nyse_holidays_<year>` list keys. Those lists were retired
    when the calendar moved into `cherrypick.core`, so the scan matched nothing and **every caller
    has been running with an empty holiday set ever since**. `doctor` reported it plainly as
    `holidays_loaded=0` and it read as a known gap rather than a live fault.

    It is not cosmetic. `is_trading_day()` is what consumes this, so an empty set makes a market
    holiday look like an ordinary session: the watchdog then expects fresh module data on a day the
    market never opened, and raises staleness findings against every module for it.

    Defaults to this year and next, so a check on 31 December still knows about 1 January. Kept
    best-effort — this sits on the watchdog's path, and a calendar hiccup should degrade to the
    old empty-set behaviour rather than fail a tick.
    """
    try:
        from cherrypick.core import calendar as _cal

        wanted = list(years) if years is not None else [now_et().year, now_et().year + 1]
        holidays: set[str] = set()
        for year in wanted:
            holidays.update(str(d) for d in _cal.nyse_holidays(int(year)))
        return holidays
    except Exception:
        return set()


def is_trading_day(dt: datetime | None = None, holidays: set[str] | None = None) -> bool:
    dt = dt or now_et()
    if dt.weekday() >= 5:  # Sat/Sun
        return False
    if holidays and dt.strftime("%Y-%m-%d") in holidays:
        return False
    return True


def is_market_hours(dt: datetime | None = None, holidays: set[str] | None = None) -> bool:
    dt = dt or now_et()
    if not is_trading_day(dt, holidays):
        return False
    return MARKET_OPEN <= dt.time() <= MARKET_CLOSE


def is_session_window(dt: datetime | None = None, holidays: set[str] | None = None) -> bool:
    """True from just before the open through the close on a trading day (for service liveness)."""
    dt = dt or now_et()
    if not is_trading_day(dt, holidays):
        return False
    return NEAR_OPEN <= dt.time() <= MARKET_CLOSE
