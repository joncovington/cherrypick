"""Item 75's event dates (research/edge-hypotheses-plan.md): the history `cherrypick.ensemble.events` cannot
compute, written to one file the study reads.

    python packages/backtest/scripts/event_history_item75.py [--out PATH]

Public dates only, nothing licensed, and no network: it folds the calendar files the suite already keeps
(`<home>/data/market-files/calendar/`, written by `scripts/fetch_market_files.py`) into
`<home>/data/public/item75/events.json` with, per label, the date ranges it speaks for:

- **CPI**: the release dates FRED has given the suite (`fred_history.json`), covering exactly the ranges
  that file covers. Before them the label is unknown. To extend it, seed the history once with
  `python scripts/fetch_market_files.py fred-history --since 2022-05-01` (needs the FRED key; a read-only
  GET of FRED's release calendar). BLS refuses scripted access to its own schedule, and this does not work
  around it.
- **Sentiment**: Michigan publishes no schedule, so its preliminary reading is taken as the second Friday
  of the month and the final as the fourth (the rule 2026's published dates follow), replaced by any date
  `umich.json` holds. It is an approximation and is labelled so (`approximate`): a month the rule misses
  by a week would let the arm trade a release day, or skip an ordinary one.

The rule labels (PMI, options expiry, FOMC, month end, quarter end, first weekly) are computed by the
engine and are not here. A label with no coverage for a date is unknown there: its gate passes and the
study counts the sessions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import home

FIRST_YEAR = 2022
CPI_NAME = "Consumer Price Index"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def cpi(fred_history: dict | None) -> tuple[list[str], list[list[str]]]:
    """(release dates, covered ranges) from FRED's history file."""
    if not isinstance(fred_history, dict):
        return [], []
    days = sorted(
        {
            str(r.get("at"))[:10]
            for r in fred_history.get("releases") or []
            if isinstance(r, dict) and r.get("name") == CPI_NAME and r.get("at")
        }
    )
    return days, [list(r) for r in fred_history.get("coverage") or []]


def sentiment(first_year: int, last_year: int, umich: dict | None) -> list[str]:
    """Second and fourth Fridays of each month (the preliminary and final readings), with any month
    `umich.json` knows replaced by its own dates."""
    known: dict[str, list[str]] = {}
    for r in (umich or {}).get("releases") or []:
        at = str(r.get("at") or "")[:10]
        if at:
            known.setdefault(at[:7], []).append(at)
    out: list[str] = []
    for y in range(first_year, last_year + 1):
        for mth in range(1, 13):
            key = f"{y}-{mth:02d}"
            if key in known:
                out += known[key]
                continue
            for n in (2, 4):
                d = cal.nth_weekday(y, mth, cal.FRI, n)
                out.append((d if cal.is_trading_day(d) else d - timedelta(days=1)).isoformat())
    return sorted(set(out))


def build(calendar_dir: Path, today: date) -> dict:
    days, covered = cpi(_read(calendar_dir / "fred_history.json"))
    start = f"{FIRST_YEAR}-01-01"
    umich = _read(calendar_dir / "umich.json")
    return {
        "data": {"CPI": days, "Sentiment": sentiment(FIRST_YEAR, today.year, umich)},
        "covered": {"CPI": covered, "Sentiment": [[start, today.isoformat()]]},
        "approximate": ["Sentiment"],
        "built": today.isoformat(),
    }


def report(doc: dict, first: str) -> dict:
    """Which part of the study window each data label covers (a day count, never a date list)."""
    out = {}
    d0, d1 = date.fromisoformat(first), date.fromisoformat(doc["built"])
    span = (d1 - d0).days + 1
    for label, ranges in doc["covered"].items():
        got = 0
        for a, b in ranges:
            lo, hi = max(date.fromisoformat(a), d0), min(date.fromisoformat(b), d1)
            got += max((hi - lo).days + 1, 0)
        out[label] = {"covered_days": got, "of": span, "approximate": label in doc["approximate"]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--first", default="2022-05-16")
    args = ap.parse_args(argv)
    doc = build(home.data_dir("market-files") / "calendar", date.today())
    target = args.out or home.data_dir("public") / "item75" / "events.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(target), "coverage": report(doc, args.first)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
