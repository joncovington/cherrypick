"""Scheduled market events for a session: which releases a day had, when, and whether we can say.

Read from files already on this machine -- never the network. The calendar store
(`<home>/data/market-files/calendar/`) is written by `scripts/fetch_market_files.py`:

- `bea.json`          BEA's release-dates file for the year (PCE, GDP), with times.
- `fred.json`         FRED's next ~45 days (CPI, jobs, PPI, JOLTS, retail sales, ...), dates only.
- `fred_history.json` every FRED date the fetcher has ever seen, and the date ranges it covers, so
                      a past session can be labelled exactly (the fetcher only ever asks forward).

plus the FOMC decision days curated in `cherrypick.core.calendar`.

**Unknown is not quiet.** A day is `known` only when every source can speak for it: BEA's file
holds that year, FRED's coverage includes the date, and the FOMC year is bundled. Otherwise the
caller is told which source is missing, and a tag built on it must say `unknown`, never `none` --
a session with no FRED key looks exactly like a session with no releases, and that is the mistake
this guards against.

`phase` turns a day's events into the regime-tag form flies records: whether a MAJOR release has
already happened by a given minute, how long ago, and every release's label. Pure.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as _calendar
from cherrypick.core import home as _home

ET = ZoneInfo("America/New_York")

# Release name -> (label, standard ET time when the source gives none, major).
# FRED gives dates only, so its releases carry their standing schedule time. "Major" is the set
# that moves index options on the day; the rest are recorded but do not set the phase.
RELEASES: dict[str, tuple[str, str | None, bool]] = {
    # FRED
    "Consumer Price Index": ("CPI", "08:30", True),
    "Producer Price Index": ("PPI", "08:30", True),
    "Employment Situation": ("NFP", "08:30", True),
    "Advance Monthly Sales for Retail and Food Services": ("Retail", "08:30", True),
    "Job Openings and Labor Turnover Survey": ("JOLTS", "10:00", True),
    "Unemployment Insurance Weekly Claims": ("Claims", "08:30", False),
    "Industrial Production and Capacity Utilization": ("IP", "09:15", False),
    "New Residential Construction": ("Housing", "08:30", False),
    "Surveys of Consumers (University of Michigan)": ("UMich", "10:00", False),
    # BEA (its file carries the time)
    "Personal Income and Outlays": ("PCE", None, True),
    "Gross Domestic Product": ("GDP", None, True),
    # Curated (cherrypick.core.calendar)
    "FOMC": ("FOMC", "14:00", True),
}
FRED_WINDOW_DAYS = 45  # how far ahead the fetcher asks FRED; fred.json covers fetched..+45


def calendar_dir() -> Path:
    return _home.data_dir("market-files") / "calendar"


def bea_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "bea.json"


def fred_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "fred.json"


def fred_history_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "fred_history.json"


# --------------------------------------------------------------------------- parsers (pure)
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


def merge_fred_history(old: dict | None, rows: list[dict], start: str, end: str) -> dict:
    """Fold one fetch of FRED rows covering `start`..`end` (ISO dates) into the history document.
    Rows are unioned (a date is never dropped), and the covered ranges are merged, so `covers` can
    say for any past date whether FRED was asked about it at all."""
    seen = {(r["at"], r["name"]) for r in (old or {}).get("releases", []) if r.get("at") and r.get("name")}
    seen |= {(str(r["at"]), str(r["name"])) for r in rows if r.get("at") and r.get("name")}
    ranges = sorted([*((old or {}).get("coverage") or []), [start, end]])
    merged: list[list[str]] = []
    for s, e in ranges:
        if merged and date.fromisoformat(s) <= date.fromisoformat(merged[-1][1]) + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return {
        "coverage": merged,
        "releases": [{"at": a, "name": n, "source": "FRED"} for a, n in sorted(seen)],
    }


def _covers(coverage: list, day: date) -> bool:
    return any(date.fromisoformat(s) <= day <= date.fromisoformat(e) for s, e in coverage or [])


# --------------------------------------------------------------------------- one day
def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _fred_view(root: Path | None) -> tuple[list[dict], list]:
    """Every FRED row on disk and the date ranges they speak for: the history file's own coverage
    plus the current forward window (fetched date .. +45 days)."""
    rows: list[dict] = []
    coverage: list = []
    hist = _read_json(fred_history_path(root))
    if isinstance(hist, dict):
        rows += [r for r in hist.get("releases") or [] if isinstance(r, dict)]
        coverage += hist.get("coverage") or []
    cur = _read_json(fred_path(root))
    if isinstance(cur, dict):
        rows += [r for r in cur.get("releases") or [] if isinstance(r, dict)]
        fetched = str(cur.get("fetched_at") or "")[:10]
        if fetched:
            end = date.fromisoformat(fetched) + timedelta(days=FRED_WINDOW_DAYS)
            coverage.append([fetched, end.isoformat()])
    return rows, coverage


def day_events(day: date, *, root: Path | None = None) -> dict:
    """The scheduled releases on `day`: {"date", "known", "missing", "events": [{label, name,
    time_et, major, source}]}, oldest first. `missing` names each source that cannot speak for the
    day; `known` is True only when it is empty."""
    missing: list[str] = []
    events: list[dict] = []

    bea_text = None
    try:
        bea_text = bea_path(root).read_text(encoding="utf-8-sig")
    except OSError:
        pass
    bea_rows = parse_bea(bea_text or "")
    if not any(str(r["at"])[:4] == str(day.year) for r in bea_rows):
        missing.append("bea")
    for r in bea_rows:
        spec = RELEASES.get(r["name"])
        if spec is None:
            continue
        try:
            at = datetime.fromisoformat(str(r["at"]).replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            continue
        if at.date() == day:
            events.append(
                {
                    "label": spec[0],
                    "name": r["name"],
                    "time_et": at.strftime("%H:%M"),
                    "major": spec[2],
                    "source": "BEA",
                }
            )

    fred_rows, coverage = _fred_view(root)
    if not _covers(coverage, day):
        missing.append("fred")
    seen = set()
    for r in fred_rows:
        spec = RELEASES.get(str(r.get("name")))
        if spec is None or str(r.get("at"))[:10] != day.isoformat() or r["name"] in seen:
            continue
        seen.add(r["name"])
        events.append(
            {"label": spec[0], "name": r["name"], "time_et": spec[1], "major": spec[2], "source": "FRED"}
        )

    if not _calendar.fomc_year_known(day.year):
        missing.append("fomc")
    elif _calendar.is_fomc_day(day):
        label, time_et, major = RELEASES["FOMC"]
        events.append(
            {"label": label, "name": "FOMC", "time_et": time_et, "major": major, "source": "curated"}
        )

    events.sort(key=lambda e: (e["time_et"] or "99:99", e["label"]))
    return {"date": day.isoformat(), "known": not missing, "missing": missing, "events": events}


def _minute(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def phase(day_doc: dict | None, now_min: int | None) -> tuple[str, float | None, str | None]:
    """(bucket, minutes since the latest major release at or before `now_min`, labels).

    bucket: 'unknown' (a source cannot speak for the day, or no clock), 'none' (no major release
    today), 'before' (one is still to come and none has happened), 'after' (at least one has).
    labels: every release that day as 'NFP 08:30, Claims 08:30', or None when unknown -- the minor
    ones are recorded too, so a later cut can promote one without a backfill."""
    if not day_doc or not day_doc.get("known") or now_min is None:
        return "unknown", None, None
    events = day_doc.get("events") or []
    labels = (
        ", ".join(f"{e['label']} {e['time_et']}" if e.get("time_et") else e["label"] for e in events) or ""
    )
    major = [_minute(e["time_et"]) for e in events if e.get("major") and e.get("time_et")]
    if not major:
        return "none", None, labels
    past = [m for m in major if m <= now_min]
    if not past:
        return "before", None, labels
    return "after", float(now_min - max(past)), labels
