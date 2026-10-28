"""Which names an earnings entry may open on: the suite's liquidity verdict, from 2026-10-28.

The user's rule (2026-10-27): every screener and candidate comes from names confirmed liquid --
weekly options, $100M a day in the stock, and monthly options with a tight ATM spread and real open
interest and volume -- "I don't want symbols in the screeners that I will never trade." Technicals
writes that verdict nightly (`liquidity.py`, 18:57 ET) to `<home>/data/market-report/liquidity/
verdicts.json`; this reads the file and imports no technicals. Declared as the 2026-10-28 boundary
(docs/operating-history.md), dated in code so it merges ahead of the session it binds on.

- **A refused name is a reason, never a silence:** `refusal` returns the execution-stage drop the
  caller records (`not_tradable: thin; <the verdict's reasons>`), naming the file's `updated_on`.
- **Unknown is refused:** a name the file does not hold is `not_tradable: unjudged`.
- **A missing or stale file refuses everything, loudly:** `liquidity_verdict_unavailable`, with
  why. A file not rewritten for `STALE_TRADING_DAYS` trading sessions is stale. That is the
  2026-08-17..24 outage (a stale bound that admitted nothing without a word) made visible: every
  refused row says why.

Pure apart from `load`, which reads the one file.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core import home as _home

#: The first session whose entries the verdict gates (the 2026-10-28 boundary).
ENTRY_UNIVERSE_FROM = "2026-10-28"
#: The first forward scan (`symbol_watch`) that previews only the verdict's names: the same session.
PREVIEW_FROM = "2026-10-28"
#: A verdict file not rewritten for more than this many trading sessions is stale.
STALE_TRADING_DAYS = 3
TRADABLE = "tradable"
UNAVAILABLE = "liquidity_verdict_unavailable"


def verdict_path() -> Path:
    return _home.data_dir("market-report") / "liquidity" / "verdicts.json"


def load(path: Path | None = None) -> dict:
    """The verdict file as {"names", "reference", "updated_on"}; {"error": why} when it cannot be read."""
    path = path or verdict_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": f"no verdict file ({path.name})"}
    except (OSError, ValueError) as exc:
        return {"error": f"verdict file unreadable: {type(exc).__name__}"}
    return {
        "names": doc.get("names") or {},
        "reference": doc.get("reference") or {},
        "updated_on": doc.get("updated_on"),
    }


def sessions_since(updated_on: str, session: str) -> int:
    """Trading sessions after `updated_on` up to and including `session` (0 when written that day)."""
    a, b = date.fromisoformat(updated_on), date.fromisoformat(session)
    n, d = 0, a
    while d < b:
        d = _calendar.next_trading_day(d)
        if d <= b:
            n += 1
    return n


def unavailable(verdict: dict, session: str) -> str | None:
    """Why the verdict cannot gate `session`, or None when it can."""
    if verdict.get("error"):
        return verdict["error"]
    updated = verdict.get("updated_on")
    if not updated:
        return "verdict file has no updated_on"
    try:
        age = sessions_since(updated, session)
    except ValueError:
        return f"verdict updated_on unreadable ({updated})"
    if age > STALE_TRADING_DAYS:
        return f"verdict written {updated}, {age} sessions old (over {STALE_TRADING_DAYS})"
    return None


def tradable(verdict: dict) -> set[str]:
    """Every name and reference fund the verdict calls tradable, in the file's spelling."""
    return {
        s for block in ("names", "reference") for s, v in (verdict.get(block) or {}).items()
        if (v or {}).get("verdict") == TRADABLE
    }  # fmt: skip


def spelled_in(symbol: str, names: set[str]) -> bool:
    """Is `symbol` in `names` in either share-class spelling (BRK/B, BRK.B)?"""
    return bool({symbol, symbol.replace("/", "."), symbol.replace(".", "/")} & names)


def _entry(verdict: dict, symbol: str) -> dict | None:
    for spelling in {symbol, symbol.replace("/", "."), symbol.replace(".", "/")}:
        for block in ("names", "reference"):
            v = (verdict.get(block) or {}).get(spelling)
            if v:
                return v
    return None


def refusal(symbol: str, session: str, verdict: dict) -> str | None:
    """The drop reason for opening `symbol` on `session`, or None when it may open. Before
    `ENTRY_UNIVERSE_FROM` nothing is refused."""
    if str(session) < ENTRY_UNIVERSE_FROM:
        return None
    why = unavailable(verdict, str(session))
    if why:
        return f"{UNAVAILABLE}: {why}"
    v = _entry(verdict, symbol)
    if v is None:
        return f"not_tradable: unjudged (verdict {verdict['updated_on']})"
    if v.get("verdict") == TRADABLE:
        return None
    reasons = "; ".join(v.get("reasons") or [])
    return (
        f"not_tradable: {v.get('verdict') or 'unjudged'}"
        + (f"; {reasons}" if reasons else "")
        + (f" (verdict {verdict['updated_on']})")
    )
