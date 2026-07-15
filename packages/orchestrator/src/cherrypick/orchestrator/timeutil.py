"""Time / market-calendar helpers (Eastern Time aware).

Freshness and deadline checks only make sense against the trading session, so these helpers
answer: what's the ET time now, is today a trading day, is the market open right now. Holidays
are read from the MEIC module's config when available (avoids a second hardcoded holiday list);
absent that, we fall back to a weekday check and log the degradation to the caller.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

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


def load_holidays(cfg: dict[str, Any], module_root_fn) -> set[str]:
    """Read NYSE holiday ISO dates from the MEIC module config, if reachable. Best-effort."""
    holidays: set[str] = set()
    meic = cfg.get("modules", {}).get("meic")
    if not meic:
        return holidays
    try:
        root = module_root_fn(meic)
        mc_path = Path(root) / "config.json"
        if not mc_path.exists():
            return holidays
        with mc_path.open("r", encoding="utf-8") as fh:
            mc = json.load(fh)
        for key, val in mc.items():
            if key.startswith("nyse_holidays_") and isinstance(val, list):
                holidays.update(str(d) for d in val)
    except Exception:
        return holidays
    return holidays


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
