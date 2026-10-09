"""Power watch: tell every channel while this machine runs on battery (decided 2026-10-08).

The 2026-10-08 power outage ran the laptop down with live trading armed; it went dark around 10:35
ET and nothing had said it was on battery. `run.py power-watch` (the `power-watch` job, every 60 s,
every day) reads the power status and, while on battery, notifies ALL channels -- the moment it
switches, then every `repeat_minutes` (15) -- as a WARNING that turns CRITICAL at `critical_percent`
(20%) or `critical_minutes` (30) of estimated time left. When AC returns it says so once, with how long
it ran on battery. Offline and local: it reads the OS power status (Windows, Linux sysfs, macOS pmset) and one state file.

`decide` is the whole rule, pure, so it is tested with a fake clock; `run` is the wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cherrypick.core import power as _power

from . import config as cfgmod
from .util import atomic_write_json, read_json

# Every channel the notifier knows: a push channel without a webhook set is skipped by the notifier
# itself ("not set"), so naming them all costs nothing and reaches whatever is configured.
ALL_CHANNELS = ["log", "desktop", "discord", "slack"]
STATE_NAME = "power_watch.json"
DEFAULTS = {"enabled": True, "repeat_minutes": 15, "critical_percent": 20, "critical_minutes": 30}


def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    pw = cfg.get("power_watch") or {}
    return {k: pw.get(k, v) for k, v in DEFAULTS.items()}


def _minutes(a: str, b: datetime) -> float:
    return (b - datetime.fromisoformat(a)).total_seconds() / 60.0


def decide(
    state: dict[str, Any],
    reading: dict[str, Any],
    now: datetime,
    rules: dict[str, Any],
    *,
    live_armed: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(the alert to send now or None, the new state). Pure.

    state: {on_battery_since, last_alert_at} -- empty when on AC. A reading that is not `known`
    changes nothing and sends nothing: "cannot tell" is never treated as plugged in or unplugged."""
    if not reading.get("known"):
        return None, state
    since = state.get("on_battery_since")
    if not reading.get("on_battery"):
        if since is None:
            return None, {}
        ran = _minutes(since, now)
        return (
            {
                "level": "INFO",
                "title": "Power restored",
                "message": f"Back on AC power after {ran:.0f} min on battery (since {since[11:16]}).",
            },
            {},
        )
    since = since or now.isoformat()
    last = state.get("last_alert_at")
    if last is not None and _minutes(last, now) < float(rules["repeat_minutes"]):
        return None, {"on_battery_since": since, "last_alert_at": last}
    pct, secs = reading.get("percent"), reading.get("seconds_left")
    critical = (pct is not None and pct <= rules["critical_percent"]) or (
        secs is not None and secs / 60.0 <= rules["critical_minutes"]
    )
    parts = [f"On battery since {since[11:16]} ({_minutes(since, now):.0f} min)"]
    parts.append(f"charge {pct}%" if pct is not None else "charge unknown")
    if secs is not None:
        parts.append(f"about {secs // 60} min left")
    if live_armed:
        parts.append("LIVE TRADING IS ARMED")
    return (
        {
            "level": "CRITICAL" if critical else "WARNING",
            "title": "On battery power" + (" -- low" if critical else ""),
            "message": "; ".join(parts) + ". Plug in, or the suite stops when the battery does.",
        },
        {"on_battery_since": since, "last_alert_at": now.isoformat()},
    )


def _live_armed(cfg: dict[str, Any], today: str) -> bool:
    try:
        from .supervisor import read_arm_records

        return any((rec or {}).get("date") == today for rec in read_arm_records(cfg).values())
    except Exception:  # noqa: BLE001 -- the alert goes out either way
        return False


def run(cfg: dict[str, Any], *, reading: dict[str, Any] | None = None, now: datetime | None = None) -> dict:
    rules = settings(cfg)
    if not rules["enabled"]:
        return {"ok": True, "skipped": "disabled in config (power_watch.enabled)"}
    reading = reading if reading is not None else _power.read()
    now = now or datetime.now(timezone.utc).astimezone()
    path = cfgmod.state_file(STATE_NAME)
    state = read_json(path, default={}) or {}
    alert, new_state = decide(state, reading, now, rules, live_armed=_live_armed(cfg, now.date().isoformat()))
    if new_state != state:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, new_state)
    if alert:
        from cherrypick.notify.notifier import Notifier

        Notifier({**(cfg.get("notify") or {}), "channels": ALL_CHANNELS}).notify(
            alert["level"], "power_watch", alert["title"], alert["message"]
        )
    return {"ok": True, "reading": reading, "alert": alert, "state": new_state}
