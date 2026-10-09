"""cherrypick.core.power — is this machine on battery?

Added 2026-10-08, after a power outage ran the laptop down mid-session: the machine went dark around
10:35 ET with live trading armed and paper loops running, and nothing had said it was on battery.
`read()` asks the operating system, with no extra dependency:

- **Windows**: `GetSystemPowerStatus` (kernel32).
- **Linux**: `/sys/class/power_supply/*` -- a `Mains`/`USB` supply that is online means AC; mains
  supplies all offline means battery; with no mains entry, a `Discharging` battery means battery. A
  machine with power-supply entries but no `Battery` is not a laptop: on AC, nothing to alert.
- **macOS**: `pmset -g batt`.

A reading that cannot be taken is `known: False`, and the caller treats it as "cannot tell", never as
"plugged in" (2026-10-08: Linux and macOS added, the user's ask after the Windows-only first cut).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

UNKNOWN: dict[str, Any] = {"known": False, "on_battery": None, "percent": None, "seconds_left": None}


def read() -> dict[str, Any]:
    """{known, on_battery, percent, seconds_left}. `percent`/`seconds_left` are None when the system
    does not know them (no battery, or a battery still estimating)."""
    if sys.platform.startswith("linux"):
        return read_linux()
    if sys.platform == "darwin":
        return read_macos()
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


# ------------------------------------------------------------------------------------------------ linux
SYSFS_POWER = "/sys/class/power_supply"


def _sysfs(path: str, name: str) -> str | None:
    try:
        with open(os.path.join(path, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def read_linux(root: str = SYSFS_POWER) -> dict[str, Any]:
    """The Linux reading from sysfs. Pure over the directory (tests point `root` at a fake one)."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return dict(UNKNOWN)
    mains_online: list[bool] = []
    batteries: list[dict] = []
    for name in names:
        path = os.path.join(root, name)
        kind = (_sysfs(path, "type") or "").lower()
        if kind in ("mains", "usb", "usb_c", "usb_pd"):
            online = _int(_sysfs(path, "online"))
            if online is not None:
                mains_online.append(online == 1)
        elif kind == "battery" and _sysfs(path, "scope") != "Device":  # not a mouse or headset
            batteries.append(
                {
                    "status": (_sysfs(path, "status") or "").lower(),
                    "capacity": _int(_sysfs(path, "capacity")),
                    "energy_now": _int(_sysfs(path, "energy_now")),
                    "power_now": _int(_sysfs(path, "power_now")),
                    "charge_now": _int(_sysfs(path, "charge_now")),
                    "current_now": _int(_sysfs(path, "current_now")),
                }
            )
    if not mains_online and not batteries:
        return dict(UNKNOWN) if not names else {**UNKNOWN, "known": True, "on_battery": False}
    if mains_online:
        on_battery = not any(mains_online)
    else:
        on_battery = any(b["status"] == "discharging" for b in batteries)
    caps = [b["capacity"] for b in batteries if b["capacity"] is not None]
    secs = None
    if on_battery:
        for b in batteries:
            if b["energy_now"] and b["power_now"]:
                secs = int(b["energy_now"] / b["power_now"] * 3600)
                break
            if b["charge_now"] and b["current_now"]:
                secs = int(b["charge_now"] / b["current_now"] * 3600)
                break
    return {
        "known": True,
        "on_battery": on_battery,
        "percent": min(caps) if caps else None,
        "seconds_left": secs,
    }


# ------------------------------------------------------------------------------------------------ macos
def read_macos() -> dict[str, Any]:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return dict(UNKNOWN)
    return parse_pmset(out)


def parse_pmset(text: str) -> dict[str, Any]:
    """`pmset -g batt`: "Now drawing from 'Battery Power'" / "'AC Power'", then a battery line like
    "-InternalBattery-0 (id=...)  64%; discharging; 3:12 remaining present: true". Pure."""
    import re

    src = re.search(r"drawing from '([^']+)'", text)
    if not src:
        return dict(UNKNOWN)
    on_battery = "battery" in src.group(1).lower()
    pct = re.search(r"(\d{1,3})%", text)
    left = re.search(r"(\d+):(\d{2}) remaining", text)
    return {
        "known": True,
        "on_battery": on_battery,
        "percent": int(pct.group(1)) if pct else None,
        "seconds_left": (int(left.group(1)) * 3600 + int(left.group(2)) * 60) if left else None,
    }
