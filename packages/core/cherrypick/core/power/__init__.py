"""cherrypick.core.power — is this machine on battery?

Added 2026-10-08, after a power outage ran the laptop down mid-session: the machine went dark around
10:35 ET with live trading armed and paper loops running, and nothing had said it was on battery.
`read()` asks Windows (`GetSystemPowerStatus`, kernel32 — no extra dependency); anywhere else, or if
the call fails, the reading is `known: False` and the caller treats it as "cannot tell", never as
"plugged in".
"""

from __future__ import annotations

import sys
from typing import Any

UNKNOWN: dict[str, Any] = {"known": False, "on_battery": None, "percent": None, "seconds_left": None}


def read() -> dict[str, Any]:
    """{known, on_battery, percent, seconds_left}. `percent`/`seconds_left` are None when Windows
    does not know them (a desktop with no battery, or a battery still estimating)."""
    if sys.platform != "win32":
        return dict(UNKNOWN)
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = _Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return dict(UNKNOWN)
    except Exception:  # noqa: BLE001 -- no reading is "cannot tell", never a crash
        return dict(UNKNOWN)
    return parse(status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent, status.BatteryLifeTime)


def parse(ac_line: int, battery_flag: int, percent: int, seconds_left: int) -> dict[str, Any]:
    """The raw SYSTEM_POWER_STATUS fields as a reading. Pure. AC line 0 = on battery, 1 = on AC,
    255 = unknown; battery flag 128 = no battery; percent 255 and seconds 0xFFFFFFFF = unknown."""
    if ac_line not in (0, 1):
        return dict(UNKNOWN)
    has_battery = battery_flag != 128 and battery_flag != 255
    return {
        "known": True,
        "on_battery": ac_line == 0,
        "percent": percent if has_battery and percent != 255 else None,
        "seconds_left": seconds_left if seconds_left not in (0xFFFFFFFF, -1) else None,
    }
