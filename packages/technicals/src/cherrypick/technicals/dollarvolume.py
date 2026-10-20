"""Each name's dollar volume: the 50-session median of close x volume, from the broker's daily candles.

**Why its own file.** Until 2026-10-19 the security list's liquidity rank and the liquidity verdict
read this figure from the local Dolt clone, which made both -- and the income lists that stand on
them -- unavailable on a machine without Dolt. `scripts/fetch_dollar_volume.py` now takes it from
tastytrade's daily candles each evening and writes one file a session; this module reads it. The
EXTENDED-hours candle's volume is the consolidated figure: on AAPL, SPY and KO it matched Dolt's to
within about 0.05% a session (2026-10-19). The SDK's default candle is regular hours only, and its
volume is a fraction of the day's (a median 0.40 of Dolt's over 5,555 names), which halved the
rank-1-or-2 set; the fetch asks for extended hours explicitly.

**One source.** Every reader of this figure reads this file, Dolt or no Dolt, so a list means the
same thing on every machine. It never fills a gap from Dolt, and Dolt's history store never takes a
figure from it.

**Finished sessions only.** A session is dated as the producer dates a daily candle (its UTC day),
and the run's own session is dropped even after the close, so an evening file covers what the
05:30 Dolt pull covered before it: through the previous session.

The file keeps how many sessions each median read (at most `SESSIONS`), because the readers ask for
different minimums: the security list ranks a name on 20 or more, the liquidity verdict judges one
on the full 50.

Network-free: the script writes, this reads.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

from . import paths

SESSIONS = 50


def median_dollar_volume(bars: list[tuple[str, float, float]], today: str) -> tuple[float, int] | None:
    """(median close x volume over the last SESSIONS finished sessions, sessions read) from
    (session date, close, volume) bars; None when no finished session has a real close. Bars on or
    after `today` are dropped, and a repeated date keeps the last bar given."""
    by_day: dict[str, float] = {}
    for day, close, volume in bars:
        if day >= today or close is None or close <= 0 or volume is None or volume < 0:
            continue
        by_day[day] = close * volume
    if not by_day:
        return None
    last = [by_day[d] for d in sorted(by_day)[-SESSIONS:]]
    return statistics.median(last), len(last)


def _days(root: Path) -> list[str]:
    return sorted(p.stem for p in root.glob("????-??-??.json")) if root.is_dir() else []


def latest(day: str | None = None) -> tuple[dict[str, dict], dict]:
    """({symbol: {"median", "sessions"}}, the file's header without its names) from the newest file
    dated on or before `day` (default today). Empty and a header naming no file when there is none.
    Symbols are in the store's spelling (BRK.B)."""
    day = day or date.today().isoformat()
    root = paths.dollar_volume_dir()
    pick = max((d for d in _days(root) if d <= day), default=None)
    if pick is None:
        return {}, {"file": None}
    try:
        doc = json.loads((root / f"{pick}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"file": pick, "unreadable": True}
    names = doc.get("names") or {}
    return names, {"file": pick, **{k: v for k, v in doc.items() if k != "names"}}


def medians(names: dict[str, dict], min_sessions: int) -> dict[str, float]:
    """{symbol: median} for the names whose median read at least `min_sessions` sessions."""
    return {
        s: float(v["median"])
        for s, v in names.items()
        if isinstance(v, dict) and v.get("median") is not None and (v.get("sessions") or 0) >= min_sessions
    }
