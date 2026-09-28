"""The daily market files: Cboe's index histories, Treasury's yield curve, and the release calendars.

Fetched by `scripts/fetch_market_files.py` (outside every package, the same fence as the narratives:
this package stays network-free) into `~/.cherrypick/data/market-files/`, and read here read-only.
The parsers live in this module and the fetcher imports them, so a file is validated on arrival by
the same code that will read it -- a file the pack could not parse is never written over a good one.

Every reader answers "as of the session before `session`": the pack is built pre-open, and a value
dated on the session itself would be a close that has not happened. A file that is missing, stale
or unparseable yields `None`, never a guess.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

ET = ZoneInfo("America/New_York")

# Cboe index histories, one CSV each. SKEW and VVIX carry one value column; VIX and VXN carry OHLC.
CBOE_INDEXES = ("SKEW", "VIX", "VVIX", "VXN")
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"

# Treasury's daily par yield curve, one CSV per calendar year. Posted by about 18:00 ET.
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
    "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
# The tenors the pack reports, by Treasury's own column names.
TENORS = {"3m": "3 Mo", "2y": "2 Yr", "5y": "5 Yr", "10y": "10 Yr", "30y": "30 Yr"}

# BEA's release schedule, keyless: GDP, Personal Income and Outlays (PCE), trade, and others.
BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"


def store_dir() -> Path:
    return _home.data_dir("market-files")


def cboe_path(symbol: str) -> Path:
    return store_dir() / "cboe" / f"{symbol}.csv"


def treasury_path(year: int) -> Path:
    return store_dir() / "treasury" / f"{year}.csv"


def bea_path() -> Path:
    return store_dir() / "calendar" / "bea.json"


def fred_releases_path() -> Path:
    return store_dir() / "calendar" / "fred.json"


# --------------------------------------------------------------------------- parsers (pure)


def _us_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_cboe(text: str) -> list[tuple[date, float]]:
    """[(session, close)] oldest first. A one-value file (SKEW, VVIX) is that value; an OHLC file
    (VIX, VXN) is its CLOSE. Rows that don't parse, and zero closes, are dropped rather than kept as
    readings -- SKEW's stream backfill once returned a zero among five rows."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or not rows[0] or rows[0][0].strip().upper() != "DATE":
        return []
    header = [h.strip().upper() for h in rows[0]]
    col = header.index("CLOSE") if "CLOSE" in header else 1
    out = []
    for row in rows[1:]:
        if len(row) <= col:
            continue
        day = _us_date(row[0])
        try:
            value = float(row[col])
        except ValueError:
            continue
        if day and value > 0:
            out.append((day, value))
    out.sort()
    return out


def parse_treasury(text: str) -> list[tuple[date, dict[str, float]]]:
    """[(session, {tenor: yield %})] oldest first, for the tenors in `TENORS` that the row carries."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        return []
    out = []
    for row in reader:
        day = _us_date(row.get("Date") or "")
        if not day:
            continue
        curve = {}
        for key, column in TENORS.items():
            try:
                curve[key] = float(row.get(column) or "")
            except ValueError:
                continue
        if curve:
            out.append((day, curve))
    out.sort()
    return out


def parse_bea(text: str) -> list[dict]:
    """[{name, at (UTC ISO), source}] from BEA's release-dates file. Duplicate entries (the file
    repeats some) collapse to one."""
    try:
        body = json.loads(text.lstrip("﻿"))
    except ValueError:
        return []
    if not isinstance(body, dict):
        return []
    seen = set()
    for name, row in body.items():
        dates = row.get("release_dates") if isinstance(row, dict) else None
        for at in dates or []:
            seen.add((str(name).strip(), str(at)))
    return [{"name": n, "at": a, "source": "BEA"} for n, a in sorted(seen, key=lambda x: (x[1], x[0]))]


def history_problems(old: list, new: list) -> list[str]:
    """Why a freshly fetched history must not replace the one on disk; empty means it may. A
    download that parses to less than the file already holds, or ends earlier, is a truncated or
    wrong file, not a newer one."""
    if not new:
        return ["the new file parses to no rows"]
    problems = []
    if old and len(new) < len(old):
        problems.append(f"the new file has {len(new)} rows, fewer than the {len(old)} on disk")
    if old and new[-1][0] < old[-1][0]:
        problems.append(f"the new file ends {new[-1][0]}, before the {old[-1][0]} on disk")
    return problems


# --------------------------------------------------------------------------- readers


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def cboe_series(symbol: str, session: str) -> list[tuple[date, float]]:
    """Cboe's closes for `symbol` strictly before `session`, oldest first."""
    text = _read(cboe_path(symbol))
    day = date.fromisoformat(session)
    return [row for row in parse_cboe(text) if row[0] < day] if text else []


def treasury_curve(session: str) -> list[tuple[date, dict[str, float]]]:
    """Treasury's curves strictly before `session`, oldest first, across this year's file and last
    year's (the first sessions of January need December)."""
    day = date.fromisoformat(session)
    rows = []
    for year in (day.year - 1, day.year):
        text = _read(treasury_path(year))
        if text:
            rows.extend(parse_treasury(text))
    return sorted(r for r in rows if r[0] < day)


def releases(session: str, days: int = 7) -> list[dict]:
    """Scheduled releases from the session's date through `days` calendar days after it, oldest
    first, each with its ET date and time. BEA always; FRED's (CPI, jobs, PPI and more) when a
    FRED key has let the fetcher store them."""
    start = date.fromisoformat(session)
    end = start + timedelta(days=days)
    rows = parse_bea(_read(bea_path()) or "")
    fred = _read(fred_releases_path())
    if fred:
        try:
            rows += [r for r in json.loads(fred).get("releases", []) if isinstance(r, dict)]
        except ValueError:
            pass
    out = []
    for r in rows:
        try:
            at = datetime.fromisoformat(str(r["at"]))
        except (KeyError, ValueError):
            continue
        et = at.astimezone(ET) if at.tzinfo else at
        if start <= et.date() <= end:
            out.append(
                {
                    "name": r.get("name"),
                    "date": et.date().isoformat(),
                    "time_et": et.strftime("%H:%M") if at.tzinfo else None,
                    "source": r.get("source"),
                    "today": et.date() == start,
                }
            )
    out.sort(key=lambda r: (r["date"], r["time_et"] or "", r["name"] or ""))
    return out


# --------------------------------------------------------------------------- arithmetic (pure)


def realized_vol(closes: list[float], sessions: int = 10) -> float | None:
    """Annualized close-to-close volatility over the last `sessions` returns, in percent: the
    standard deviation of log returns (sample, n-1) times the square root of 252. Needs
    `sessions + 1` closes; fewer is None."""
    if len(closes) < sessions + 1 or any(c <= 0 for c in closes[-(sessions + 1) :]):
        return None
    tail = closes[-(sessions + 1) :]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:], strict=False)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def weekly_expected_move_pct(vix: float) -> float:
    """VIX is a 30-day implied vol in annual percent; one week is 1/52 of a year, so the one-sigma
    weekly move is VIX / sqrt(52), in percent."""
    return vix / math.sqrt(52)
