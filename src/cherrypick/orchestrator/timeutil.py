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
    from zoneinfo import ZoneInfo

    def _tz(name: str):
        return ZoneInfo(name)
except Exception:  # pragma: no cover - fallback for older/edge environments
    import pytz

    def _tz(name: str):
        return pytz.timezone(name)


_DEFAULT_TZ = "America/New_York"
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
NEAR_OPEN = dtime(9, 15)  # start data services a bit before the bell


def now_et(tz_name: str = _DEFAULT_TZ) -> datetime:
    return datetime.now(_tz(tz_name))


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
