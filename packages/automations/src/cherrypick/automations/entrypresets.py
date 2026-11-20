"""The Builder's entry presets: a named entry and a few settings in, the automation's `entry` out, and
any number of filters stacked on it, each one or two ordinary `gates`. Pure.

A preset is a starting point, never a tested edge: what it writes is the engine's own entry kinds
(`time`, `orb`, `trigger` over a `core.signals` spec) and its own gates, which the person edits
afterwards. One entry fires; every filter must pass. `tests/test_entrypresets.py` loads every entry
alone and with every filter through `config.load_arms`.

Entries (pick one):

    daily_time       a fixed time each session
    near_close       a fixed time just before the close
    rsi_pullback     a short-term oversold close above the long average (RSI(2) under 10 over the
                     200-day), read on today's bar as it stands inside a window before the close
    down_days        a run of down closes above the long average, read the same way
    rsi_overbought   a short-term overbought close below the long average
    ma_cross         a fast average crossing a slow one on intraday bars
    orb              a break of the opening range
    gap_fill         an opening gap filled by the given share
    after_event      a set number of sessions after a calendar event
    custom           a fixed time to start from, every field then the person's

Filters (stack any): vix_band, iv_rank, iv_over_rv, calm_open, quiet_day, uptrend.

The settings' values are plain (a number, a "HH:MM" time, a name), so a request carries nothing a
form could not type.
"""

from __future__ import annotations

from . import events

#: The last minute an end-of-day entry can be due: the session's last full minute.
LAST_MINUTE = "15:59"

EVENT_LABELS = [*events.RULE_LABELS, *events.STORE_LABELS]

#: Every setting's type, default and bounds, for the form.
SETTINGS: dict[str, dict] = {
    "at": {"type": "time", "default": "10:00", "label": "enter at · ET"},
    "window": {"type": "int", "min": 1, "max": 60, "default": 5, "label": "minutes to enter"},
    "rsi_n": {"type": "int", "min": 2, "max": 50, "default": 2, "label": "RSI length"},
    "below": {"type": "number", "min": 0, "max": 100, "default": 10, "label": "closes below"},
    "above": {"type": "number", "min": 0, "max": 100, "default": 90, "label": "closes above"},
    "trend_n": {"type": "int", "min": 2, "max": 400, "default": 200, "label": "trend · SMA days"},
    "days": {"type": "int", "min": 1, "max": 10, "default": 3, "label": "down closes in a row"},
    "fast": {"type": "int", "min": 2, "max": 100, "default": 9, "label": "fast EMA"},
    "slow": {"type": "int", "min": 3, "max": 200, "default": 21, "label": "slow EMA"},
    "direction": {"type": "enum", "options": ["above", "below"], "default": "above", "label": "crosses"},
    "bar_minutes": {"type": "int", "min": 1, "max": 60, "default": 5, "label": "bar · minutes"},
    "from": {"type": "time", "default": "09:45", "label": "from · ET"},
    "until": {"type": "time", "default": "15:30", "label": "until · ET"},
    "minutes": {"type": "int", "min": 1, "max": 120, "default": 30, "label": "range · minutes"},
    "latest": {"type": "time", "default": "12:00", "label": "no later than · ET"},
    "min_pct": {"type": "number", "min": 0, "max": 10, "default": 0.3, "label": "gap at least · %"},
    "event": {"type": "enum", "options": EVENT_LABELS, "default": "FOMC", "label": "event"},
    "sessions": {"type": "int", "min": 1, "max": 10, "default": 1, "label": "sessions after"},
    # filters
    "vix_min": {"type": "number", "min": 0, "max": 100, "default": 15, "label": "at least"},
    "vix_max": {"type": "number", "min": 0, "max": 100, "default": 30, "label": "at most"},
    "rank_min": {"type": "number", "min": 0, "max": 100, "default": 30, "label": "rank at least"},
    "lookback": {"type": "int", "min": 20, "max": 1000, "default": 252, "label": "over · sessions"},
    "edge_min": {"type": "number", "min": -50, "max": 50, "default": 2, "label": "at least · vol points"},
    "rv_n": {"type": "int", "min": 5, "max": 252, "default": 20, "label": "realized over · sessions"},
    "gap_max": {"type": "number", "min": 0, "max": 10, "default": 0.5, "label": "gap within · %"},
    "vix_up_max": {"type": "number", "min": -100, "max": 100, "default": 5, "label": "VIX up at most · %"},
    "move_max": {"type": "number", "min": 0, "max": 10, "default": 0.3, "label": "move within · %"},
    "ma_n": {"type": "int", "min": 2, "max": 400, "default": 200, "label": "SMA days"},
}


#: The strategies a preset suits, as the Builder's strategy line can match them: each a strategy
#: (`strategies.STRATEGIES`) and any settings it must have (sold, on puts). The Builder marks a
#: preset "suits" when the chosen strategy matches one; `suits` says it in words.
SHORT_PUT = [
    {"strategy": "option", "direction": "short", "right": "put"},
    {"strategy": "vertical", "direction": "short", "right": "put"},
]
SHORT_CALL = [
    {"strategy": "option", "direction": "short", "right": "call"},
    {"strategy": "vertical", "direction": "short", "right": "call"},
]
SPREADS = [{"strategy": "vertical"}]
RANGE_BOUND = [
    {"strategy": "iron_condor", "direction": "short"},
    {"strategy": "iron_fly", "direction": "short"},
    {"strategy": "strangle", "direction": "short"},
    {"strategy": "straddle", "direction": "short"},
    {"strategy": "butterfly", "direction": "long"},
    {"strategy": "broken_wing", "direction": "long"},
]
SELLING = [
    {"strategy": "option", "direction": "short"},
    {"strategy": "vertical", "direction": "short"},
    {"strategy": "strangle", "direction": "short"},
    {"strategy": "straddle", "direction": "short"},
    {"strategy": "iron_condor", "direction": "short"},
    {"strategy": "iron_fly", "direction": "short"},
    {"strategy": "jade_lizard"},
]


def _p(label: str, group: str, about: str, settings: list[str], suits: str = "", **more) -> dict:
    return {"label": label, "group": group, "about": about, "settings": settings, "suits": suits, **more}


ENTRIES: dict[str, dict] = {
    "daily_time": _p(
        "Daily at a time",
        "Time",
        "Enters at the same minute each session the weekdays allow.",
        ["at", "window"],
        "any structure",
    ),
    "near_close": _p(
        "Near the close",
        "Time",
        "Enters a few minutes before the close, on the day's settled move.",
        ["at", "window"],
        "overnight and short-dated trades",
        defaults={"at": "15:55"},
    ),
    "rsi_pullback": _p(
        "Pullback in an uptrend",
        "Trend and mean reversion",
        "A short-term oversold close while the longer trend is up: the day the 2-day RSI drops under 10 "
        "above the 200-day average, read on today's bar in the minutes before the close.",
        ["rsi_n", "below", "trend_n", "at"],
        "short puts, put spreads",
        defaults={"at": "15:50"},
        fits=SHORT_PUT,
    ),
    "down_days": _p(
        "Down days in an uptrend",
        "Trend and mean reversion",
        "Several down closes in a row while the close is above its long average.",
        ["days", "trend_n", "at"],
        "short puts, put spreads",
        defaults={"at": "15:50"},
        fits=SHORT_PUT,
    ),
    "rsi_overbought": _p(
        "Overbought in a downtrend",
        "Trend and mean reversion",
        "A short-term overbought close while the longer trend is down: the mirror of the pullback.",
        ["rsi_n", "above", "trend_n", "at"],
        "short calls, call spreads",
        defaults={"at": "15:50"},
        fits=SHORT_CALL,
    ),
    "ma_cross": _p(
        "Moving-average cross",
        "Trend and mean reversion",
        "A fast average crossing a slow one on intraday bars, inside a window.",
        ["fast", "slow", "direction", "bar_minutes", "from", "until"],
        "spreads on the side of the cross",
        fits=SPREADS,
    ),
    "orb": _p(
        "Opening-range breakout",
        "Intraday",
        "The price leaving the first minutes' range, once, no later than a set time.",
        ["minutes", "direction", "latest"],
        "spreads on the side of the break",
        fits=SPREADS,
    ),
    "gap_fill": _p(
        "Gap fill",
        "Intraday",
        "An opening gap of at least a set size closing back to the prior close.",
        ["min_pct", "bar_minutes", "from", "until"],
        "range-bound structures",
        fits=RANGE_BOUND,
        defaults={"from": "09:35", "until": "12:00"},
    ),
    "after_event": _p(
        "After an event",
        "Events",
        "A set number of sessions after a calendar event, at a set time: the volatility after it.",
        ["event", "sessions", "at"],
        "premium selling after the event",
        fits=SELLING,
    ),
    "custom": _p(
        "Custom",
        "Custom",
        "A fixed time to start from: any entry kind, signal and gates from here.",
        ["at", "window"],
    ),
}

FILTERS: dict[str, dict] = {
    "vix_band": _p(
        "VIX within a band",
        "Volatility",
        "VIX now between two levels: not too quiet, not a panic.",
        ["vix_min", "vix_max"],
    ),
    "iv_rank": _p(
        "Implied vol rank elevated",
        "Volatility",
        "The underlying's vol index ranked high against its own last year.",
        ["rank_min", "lookback"],
    ),
    "iv_over_rv": _p(
        "Implied over realized",
        "Volatility",
        "Options price more movement than the underlying has shown lately.",
        ["edge_min", "rv_n"],
    ),
    "calm_open": _p(
        "Calm open", "The open", "A small opening gap and VIX not jumping.", ["gap_max", "vix_up_max"]
    ),
    "quiet_day": _p(
        "Quiet so far today", "The open", "The price near the prior close at the entry.", ["move_max"]
    ),
    "uptrend": _p("In an uptrend", "Trend", "The price above its long simple average.", ["ma_n"]),
}


class Refused(ValueError):
    """A preset or a setting the presets cannot build from."""


def _minute(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _time_ok(v: object) -> bool:
    if not isinstance(v, str) or len(v) != 5 or v[2] != ":" or not (v[:2] + v[3:]).isdigit():
        return False
    return int(v[:2]) < 24 and int(v[3:]) < 60


def _settings(table: dict, kind: str, name: str, given: dict) -> dict:
    p = table.get(name)
    if p is None:
        raise Refused(f"{kind}: one of {', '.join(table)}")
    out = {}
    for key in p["settings"]:
        spec = SETTINGS[key]
        v = given.get(key, p.get("defaults", {}).get(key, spec["default"]))
        if spec["type"] == "enum" and v not in spec["options"]:
            raise Refused(f"{key}: one of {', '.join(spec['options'])}")
        if spec["type"] == "time":
            if not _time_ok(v):
                raise Refused(f"{key}: a time HH:MM")
            if not "09:30" <= v <= LAST_MINUTE:
                raise Refused(f"{key}: inside the session, 09:30 to {LAST_MINUTE}")
        if spec["type"] in ("int", "number"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise Refused(f"{key}: a number")
            if spec["type"] == "int" and int(v) != v:
                raise Refused(f"{key}: a whole number")
            if v < spec["min"] or v > spec["max"]:
                raise Refused(f"{key}: {spec['min']} to {spec['max']}")
            v = int(v) if spec["type"] == "int" else v
        out[key] = v
    return out


def _trend(n: int, direction: str) -> dict:
    """The close above (or below) its `n`-day simple average, on daily bars."""
    return {
        "kind": "cross",
        "a": {"name": "close"},
        "b": {"name": "sma", "n": n},
        "direction": direction,
        "seed": True,
    }


def _near_close(signal: dict, at: str) -> dict:
    """A daily signal read on today's bar as it stands, each minute from `at` to the last minute."""
    return {
        "kind": "trigger",
        "trigger": {
            "signal": signal,
            "window": {"open": at, "close": LAST_MINUTE},
            "within": None,
            "daily": True,
            "developing": True,
        },
    }


def _intraday(signal: dict, s: dict) -> dict:
    """An intraday signal on `bar_minutes` bars, entered within 5 minutes of its first firing inside
    the window."""
    if _minute(s["from"]) > _minute(s["until"]):
        raise Refused("until: after from")
    trigger = {"signal": signal, "window": {"open": s["from"], "close": s["until"]}, "within": 5}
    return {"kind": "trigger", "trigger": trigger, "bar_minutes": s["bar_minutes"]}


def _entry(name: str, s: dict) -> dict:
    if name in ("daily_time", "near_close", "custom"):
        if _minute(s["at"]) + s["window"] - 1 > _minute(LAST_MINUTE):
            raise Refused(f"window: the entry would run past {LAST_MINUTE}")
        return {"kind": "time", "at": s["at"], "window": s["window"]}
    if name == "rsi_pullback":
        rsi = {
            "kind": "range",
            "a": {"name": "rsi", "n": s["rsi_n"]},
            "low": 0,
            "high": s["below"],
            "direction": "enters",
            "seed": True,
            "only_when": [_trend(s["trend_n"], "above")],
        }
        return _near_close(rsi, s["at"])
    if name == "rsi_overbought":
        rsi = {
            "kind": "range",
            "a": {"name": "rsi", "n": s["rsi_n"]},
            "low": s["above"],
            "high": 100,
            "direction": "enters",
            "seed": True,
            "only_when": [_trend(s["trend_n"], "below")],
        }
        return _near_close(rsi, s["at"])
    if name == "down_days":
        run = {
            "kind": "consecutive",
            "n": s["days"],
            "direction": "down",
            "seed": True,
            "only_when": [_trend(s["trend_n"], "above")],
        }
        return _near_close(run, s["at"])
    if name == "ma_cross":
        if s["fast"] >= s["slow"]:
            raise Refused("slow: longer than the fast average")
        cross = {
            "kind": "cross",
            "a": {"name": "ema", "n": s["fast"]},
            "b": {"name": "ema", "n": s["slow"]},
            "direction": s["direction"],
            "seed": True,
        }
        return _intraday(cross, s)
    if name == "orb":
        if _minute(s["latest"]) < _minute("09:30") + s["minutes"]:
            raise Refused("latest: after the opening range has formed")
        return {
            "kind": "orb",
            "minutes": s["minutes"],
            "direction": s["direction"],
            "latest": s["latest"],
            "single_shot": True,
        }
    if name == "gap_fill":
        return _intraday({"kind": "gap_fill", "min_pct": s["min_pct"]}, s)
    if name == "after_event":
        at = s["at"]
        if _minute(at) + 5 > _minute(LAST_MINUTE):
            raise Refused(f"at: at least 5 minutes before {LAST_MINUTE}")
        signal = {
            "kind": "event",
            "event": s["event"],
            "timing": "after",
            "days": s["sessions"],
            "unit": "sessions",
            "at": at,
        }
        trigger = {"signal": signal, "window": {"open": at, "close": _hhmm(_minute(at) + 5)}, "within": 5}
        return {"kind": "trigger", "trigger": trigger, "bar_minutes": 1}
    raise Refused(f"entry: one of {', '.join(ENTRIES)}")  # unreachable: _settings refused it


def _gates(name: str, s: dict) -> list[dict]:
    if name == "vix_band":
        if s["vix_min"] > s["vix_max"]:
            raise Refused("vix_max: at least vix_min")
        return [{"gate": "vix_level", "min": s["vix_min"], "max": s["vix_max"]}]
    if name == "iv_rank":
        return [{"gate": "vol_index_rank", "min": s["rank_min"], "lookback": s["lookback"]}]
    if name == "iv_over_rv":
        return [{"gate": "vol_index_minus_rv", "min": s["edge_min"], "n": s["rv_n"]}]
    if name == "calm_open":
        g = s["gap_max"]
        return [{"gate": "gap_pct", "min": -g, "max": g}, {"gate": "vix_change_pct", "max": s["vix_up_max"]}]
    if name == "quiet_day":
        m = s["move_max"]
        return [{"gate": "change_pct", "min": -m, "max": m}]
    if name == "uptrend":
        return [{"gate": "above_ma", "kind": "sma", "n": s["ma_n"]}]
    raise Refused(f"filter: one of {', '.join(FILTERS)}")  # unreachable


def build_entry(name: str, given: dict | None = None) -> dict:
    """{"preset", "settings", "entry"} for entry preset `name`. Raises Refused, naming the setting."""
    s = _settings(ENTRIES, "entry", name, given or {})
    return {"preset": name, "settings": s, "entry": _entry(name, s)}


def build_filter(name: str, given: dict | None = None) -> dict:
    """{"filter", "settings", "gates"} for filter `name`. Raises Refused, naming the setting."""
    s = _settings(FILTERS, "filter", name, given or {})
    return {"filter": name, "settings": s, "gates": _gates(name, s)}


def schema() -> dict:
    """What the presets offer: each entry and filter (label, group, about, what it suits, the settings
    it takes, its own defaults) and every setting's type, default, bounds and label."""

    def show(table: dict) -> dict:
        return {
            k: {key: v.get(key) for key in ("label", "group", "about", "suits", "settings")}
            | {"defaults": v.get("defaults", {}), "fits": v.get("fits", [])}
            for k, v in table.items()
        }

    return {"entries": show(ENTRIES), "filters": show(FILTERS), "settings": SETTINGS}
