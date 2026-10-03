"""Scheduled market events for a session: which releases a day had, when, and whether we can say.

Read from files already on this machine -- never the network. The calendar store
(`<home>/data/market-files/calendar/`) is written by `scripts/fetch_market_files.py`:

- `bea.json`          BEA's release-dates file for the year (PCE, GDP), with times.
- `census.json`       Census's economic-indicators calendar, with times: the headline dates for
                      retail sales, housing starts, new home sales, durable goods and the rest.
                      Census shows only the current year, so every fetch is folded in and no date
                      is ever dropped. Census is the source for its own releases; FRED the fallback.
- `fred.json`         FRED's next ~45 days (CPI, jobs, PPI, JOLTS, ADP, ECI, ...), dates only.
- `fred_history.json` every FRED date the fetcher has ever seen, and the date ranges it covers, so
                      a past session can be labelled exactly (the fetcher only ever asks forward).
- `umich.json`        the University of Michigan's own "next data release" note, every one ever
                      seen. Michigan publishes no year schedule and FRED lists only the final
                      reading, so this is the only source for the preliminary one.

plus the FOMC decision days curated in `cherrypick.core.calendar`, and releases that follow a
published rule and appear on no machine-readable calendar we can fetch (`RULE_EVENTS`: ISM,
Conference Board confidence, FOMC minutes, monthly options expiry), labelled `source: "rule"`.

**Why Census over FRED for its own releases (2026-10-02).** FRED mixes headline release dates with
other updates under one release id: it lists retail sales on 2026-09-28 (Census's headline was
09-16, its calendar has nothing on the 28th), and files new home sales (10:00) under "New
Residential Construction" (housing starts, 08:30). BLS's releases (CPI, PPI, jobs, JOLTS) came out
clean on FRED, and BLS refuses scripted access to its own schedule, so FRED stays their source.

**Why Michigan's own page (2026-10-02).** FRED's release 91 carries only the *final* consumer
sentiment reading (7/31, 8/28, 9/25, 10/23); the preliminary, the one that moves the market, was
never on this calendar, and 2026-10-09 read as a known day without it. QuikOptions' economic
calendar showed the gap (docs/quikoptions-plan.md). The preliminary has no fixed rule (2026: the
third Friday in July, the second in August), so it is read, not computed.

**Unknown is not quiet.** A day is `known` only when every source it cannot do without speaks for
it: BEA's file holds that year, FRED's coverage includes the date, and the FOMC year is bundled.
Otherwise the caller is told which source is missing, and a tag built on it must say `unknown`,
never `none` -- a session with no FRED key looks exactly like a session with no releases. A missing
Census file is `degraded`, not unknown: FRED still carries retail sales and housing (with the noise
above), so the day can still be told apart from a quiet one.

`phase` turns a day's events into the regime-tag form flies records: whether a MAJOR release has
already happened by a given minute, how long ago, and every release's label. Pure.
"""

from __future__ import annotations

import html as _html
import json
import re
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
    # FRED (BLS and others)
    "Consumer Price Index": ("CPI", "08:30", True),
    "Producer Price Index": ("PPI", "08:30", True),
    "Employment Situation": ("NFP", "08:30", True),
    "Job Openings and Labor Turnover Survey": ("JOLTS", "10:00", True),
    "Employment Cost Index": ("ECI", "08:30", True),
    "ADP National Employment Report": ("ADP", "08:15", False),
    "Unemployment Insurance Weekly Claims": ("Claims", "08:30", False),
    "Industrial Production and Capacity Utilization": ("IP", "09:15", False),
    "Surveys of Consumers (University of Michigan)": ("UMich", "10:00", False),
    # Michigan's own note (umich.json): the preliminary reading FRED never lists, and the final.
    "Surveys of Consumers (University of Michigan) - Preliminary": ("UMich", "10:00", False),
    "Surveys of Consumers (University of Michigan) - Final": ("UMich", "10:00", False),
    # FRED's copies of Census releases: the fallback when census.html is missing.
    "Advance Monthly Sales for Retail and Food Services": ("Retail", "08:30", True),
    "New Residential Construction": ("Housing", "08:30", False),
    # BEA (its file carries the time)
    "Personal Income and Outlays": ("PCE", None, True),
    "Gross Domestic Product": ("GDP", None, True),
    # Curated (cherrypick.core.calendar)
    "FOMC": ("FOMC", "14:00", True),
}

# Census's calendar names its releases at length; matched by prefix. (label, major); the time is
# Census's own.
CENSUS_RELEASES: dict[str, tuple[str, bool]] = {
    "Advance Monthly Sales for Retail and Food Services": ("Retail", True),
    "New Residential Construction": ("Housing", False),
    "New Residential Sales": ("NewHome", False),
    "Advance Report on Durable Goods": ("Durables", False),
    "Full Report - Manufacturers' Shipments": ("Factory", False),
    "Construction Spending": ("Construction", False),
    "Advance Economic Indicators Report": ("AdvIndicators", False),
}
CENSUS_LABELS = {label for label, _ in CENSUS_RELEASES.values()}

# Releases with a published rule and no fetchable calendar: (label, ET time or None, major).
# ISM's "business day" is not NYSE's trading day, so on a holiday week (Good Friday) the rule can
# land a day off; recorded as `source: "rule"` so a cut can leave them out.
RULE_EVENTS: dict[str, tuple[str, str | None, bool]] = {
    "ISM Manufacturing": ("ISM-Mfg", "10:00", True),  # 1st business day of the month
    "ISM Services": ("ISM-Svcs", "10:00", True),  # 3rd business day of the month
    "Conference Board Consumer Confidence": ("ConfBoard", "10:00", False),  # last Tuesday
    "FOMC Minutes": ("FOMC-Minutes", "14:00", False),  # three weeks after each decision
    "Monthly Options Expiration": ("OPEX", None, False),  # 3rd Friday, the Thursday on a holiday
}
FRED_WINDOW_DAYS = 45  # how far ahead the fetcher asks FRED; fred.json covers fetched..+45


def calendar_dir() -> Path:
    return _home.data_dir("market-files") / "calendar"


def bea_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "bea.json"


def census_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "census.json"


def fred_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "fred.json"


def fred_history_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "fred_history.json"


def umich_path(root: Path | None = None) -> Path:
    return (root or calendar_dir()) / "umich.json"


def source_paths(root: Path | None = None) -> tuple[Path, ...]:
    """Every file `day_events` reads, so a caller that caches on their modification times (flies'
    provider) cannot fall behind a new source."""
    return (bea_path(root), census_path(root), fred_path(root), fred_history_path(root), umich_path(root))


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


_CENSUS_DATE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
_CENSUS_TIME = re.compile(r"^\d{1,2}:\d{2} [AP]M$")
_CENSUS_CODE = re.compile(r"^A(\d{12})$")


def parse_census(text: str) -> list[dict]:
    """[{name, at (ET 'YYYY-MM-DDTHH:MM'), source}] from Census's economic-indicators calendar
    (the list view). Each row reads name | date | time | reference period | A<yyyymmddhhmm> | ...;
    the code is the release's own timestamp, so the date and time come from it. A release that
    covers two reference periods on one day collapses to one."""
    tokens = [re.sub(r"\s+", " ", _html.unescape(t)).strip() for t in re.split(r"<[^>]+>", text or "")]
    tokens = [t for t in tokens if t]
    seen = set()
    for i in range(1, len(tokens) - 2):
        if not (_CENSUS_DATE.match(tokens[i]) and _CENSUS_TIME.match(tokens[i + 1])):
            continue
        code = next((m for m in (_CENSUS_CODE.match(t) for t in tokens[i + 2 : i + 5]) if m), None)
        if code is None:
            continue
        c = code.group(1)
        seen.add((tokens[i - 1], f"{c[:4]}-{c[4:6]}-{c[6:8]}T{c[8:10]}:{c[10:12]}"))
    return [{"name": n, "at": a, "source": "Census"} for n, a in sorted(seen, key=lambda x: (x[1], x[0]))]


_UMICH_NOTE = re.compile(
    r"Next data release:\s*[A-Z][a-z]+day,\s*([A-Z][a-z]+ \d{1,2}, \d{4})\s+for\s+(Preliminary|Final)\s+"
    r"([A-Z][a-z]+)\s+data\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s+ET",
    re.I,
)


def parse_umich(text: str) -> list[dict]:
    """[{name, period, at, source}] from the Surveys of Consumers home page's one-line note, "Next
    data release: Friday, October 09, 2026 for Preliminary October data at 10am ET". `period` is the
    data month ("2026-10"), which with the name identifies the release if Michigan moves its date.
    Empty when the note is missing or reads differently: a changed page is refused by the fetcher,
    never guessed at."""
    flat = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", text or "")))
    m = _UMICH_NOTE.search(flat)
    if not m:
        return []
    try:
        day = datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return []
    try:
        month = datetime.strptime(m.group(3), "%B").month
    except ValueError:
        return []
    # The data month is the release's own, or December's data released in January.
    year = day.year - (1 if month > day.month else 0)
    hour = int(m.group(4)) % 12 + (12 if m.group(6).lower() == "pm" else 0)
    name = f"Surveys of Consumers (University of Michigan) - {m.group(2).capitalize()}"
    at = f"{day.isoformat()}T{hour:02d}:{m.group(5) or '00'}"
    return [{"name": name, "period": f"{year}-{month:02d}", "at": at, "source": "UMich"}]


def _merge_ranges(ranges: list) -> list[list[str]]:
    """ISO date ranges, sorted, with overlapping and touching ones joined."""
    merged: list[list[str]] = []
    for s, e in sorted([list(r) for r in ranges]):
        if merged and date.fromisoformat(s) <= date.fromisoformat(merged[-1][1]) + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def merge_fred_history(old: dict | None, rows: list[dict], start: str, end: str) -> dict:
    """Fold one fetch of FRED rows covering `start`..`end` (ISO dates) into the history document.
    Rows are unioned (a date is never dropped), and the covered ranges are merged, so `covers` can
    say for any past date whether FRED was asked about it at all."""
    seen = {(r["at"], r["name"]) for r in (old or {}).get("releases", []) if r.get("at") and r.get("name")}
    seen |= {(str(r["at"]), str(r["name"])) for r in rows if r.get("at") and r.get("name")}
    return {
        "coverage": _merge_ranges([*((old or {}).get("coverage") or []), [start, end]]),
        "releases": [{"at": a, "name": n, "source": "FRED"} for a, n in sorted(seen)],
    }


def _merge_releases(old: dict | None, rows: list[dict], source: str) -> dict:
    seen = {(r["at"], r["name"]) for r in (old or {}).get("releases", []) if r.get("at") and r.get("name")}
    seen |= {(str(r["at"]), str(r["name"])) for r in rows if r.get("at") and r.get("name")}
    return {"releases": [{"at": a, "name": n, "source": source} for a, n in sorted(seen)]}


def merge_census(old: dict | None, rows: list[dict]) -> dict:
    """Fold one parse of Census's calendar into the stored document; a date is never dropped (the
    page shows only the current year, so January would otherwise erase December's)."""
    return _merge_releases(old, rows, "Census")


def merge_umich(old: dict | None, rows: list[dict], fetched: str) -> dict:
    """Fold one reading of Michigan's next-release note, taken on `fetched` (ISO date), into the
    stored document. Kept for good (the page only ever shows one), except that a release Michigan
    moves replaces its old date: a release is its name and data month, not its date. Coverage is
    each fetch's span, `fetched` to the release it names: a note saying the next release is the 9th
    says nothing comes out before it, so those days can be told apart from days nobody looked at."""
    by_release: dict[tuple[str, str], str] = {}
    for r in [*((old or {}).get("releases") or []), *rows]:
        if r.get("at") and r.get("name"):
            by_release[(str(r["name"]), str(r.get("period") or r["at"]))] = str(r["at"])
    ranges = list((old or {}).get("coverage") or [])
    ranges += [[fetched, max(fetched, str(r["at"])[:10])] for r in rows if r.get("at")]
    return {
        "coverage": _merge_ranges(ranges),
        "releases": [
            {"at": at, "name": name, "period": period, "source": "UMich"}
            for (name, period), at in sorted(by_release.items(), key=lambda kv: (kv[1], kv[0]))
        ],
    }


def _covers(coverage: list, day: date) -> bool:
    return any(date.fromisoformat(s) <= day <= date.fromisoformat(e) for s, e in coverage or [])


# --------------------------------------------------------------------------- rules (pure)
def _nth_trading_day_of_month(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    if not _calendar.is_trading_day(d):
        d = _calendar.nth_trading_day(d, 1)
    return _calendar.nth_trading_day(d, n - 1) if n > 1 else d


def rule_events(day: date) -> list[dict]:
    """The RULE_EVENTS falling on `day` (FOMC minutes only when the FOMC year is bundled)."""
    out = []

    def add(name):
        label, time_et, major = RULE_EVENTS[name]
        out.append({"label": label, "name": name, "time_et": time_et, "major": major, "source": "rule"})

    if day == _nth_trading_day_of_month(day.year, day.month, 1):
        add("ISM Manufacturing")
    if day == _nth_trading_day_of_month(day.year, day.month, 3):
        add("ISM Services")
    if day == _calendar.last_weekday(day.year, day.month, _calendar.TUE):
        add("Conference Board Consumer Confidence")
    minutes_from = day - timedelta(days=21)
    if _calendar.fomc_year_known(minutes_from.year) and _calendar.is_fomc_day(minutes_from):
        add("FOMC Minutes")
    opex = _calendar.nth_weekday(day.year, day.month, _calendar.FRI, 3)
    if not _calendar.is_trading_day(opex):
        opex -= timedelta(days=1)
    if day == opex:
        add("Monthly Options Expiration")
    return out


# --------------------------------------------------------------------------- one day
def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


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


def _event(label: str, name: str, time_et: str | None, major: bool, source: str) -> dict:
    return {"label": label, "name": name, "time_et": time_et, "major": major, "source": source}


def day_events(day: date, *, root: Path | None = None) -> dict:
    """The scheduled releases on `day`: {"date", "known", "missing", "degraded", "events": [{label,
    name, time_et, major, source}]}, by time. `missing` names each source that cannot speak for the
    day (`known` is True only when it is empty); `degraded` names one whose absence costs accuracy
    but not the day (a missing Census calendar, covered by FRED's copies)."""
    missing: list[str] = []
    degraded: list[str] = []
    events: list[dict] = []

    bea_rows = parse_bea(_read_text(bea_path(root)))
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
            events.append(_event(spec[0], r["name"], at.strftime("%H:%M"), spec[2], "BEA"))

    census_doc = _read_json(census_path(root))
    census_rows = [r for r in (census_doc or {}).get("releases") or [] if isinstance(r, dict) and r.get("at")]
    census_ok = any(r["at"][:4] == str(day.year) for r in census_rows)
    if not census_ok:
        degraded.append("census")
    for r in census_rows if census_ok else []:
        if r["at"][:10] != day.isoformat():
            continue
        spec = next((v for k, v in CENSUS_RELEASES.items() if r["name"].startswith(k)), None)
        if spec is not None:
            events.append(_event(spec[0], r["name"], r["at"][11:16], spec[1], "Census"))

    fred_rows, coverage = _fred_view(root)
    if not _covers(coverage, day):
        missing.append("fred")
    seen = set()
    for r in fred_rows:
        spec = RELEASES.get(str(r.get("name")))
        if spec is None or str(r.get("at"))[:10] != day.isoformat() or r["name"] in seen:
            continue
        if census_ok and spec[0] in CENSUS_LABELS:
            continue  # Census is the source for its own releases
        seen.add(r["name"])
        events.append(_event(spec[0], r["name"], spec[1], spec[2], "FRED"))

    # A day Michigan's note never spoke for is degraded, not unknown: FRED still has the final
    # reading, but a preliminary there may be missing. That includes every day before the first
    # fetch, so a restamp of past sessions says so rather than writing the gap into history.
    umich_doc = _read_json(umich_path(root)) or {}
    umich_rows = [r for r in umich_doc.get("releases") or [] if isinstance(r, dict)]
    if not _covers(umich_doc.get("coverage"), day):
        degraded.append("umich")
    for r in umich_rows:
        spec = RELEASES.get(str(r.get("name")))
        if spec is not None and str(r.get("at"))[:10] == day.isoformat():
            events.append(_event(spec[0], r["name"], str(r["at"])[11:16] or spec[1], spec[2], "UMich"))

    if not _calendar.fomc_year_known(day.year):
        missing.append("fomc")
    elif _calendar.is_fomc_day(day):
        label, time_et, major = RELEASES["FOMC"]
        events.append(_event(label, "FOMC", time_et, major, "curated"))
    events += rule_events(day)

    # One release reported by two sources (BEA and Census both list the trade report) counts once.
    unique = {(e["label"], e["time_et"]): e for e in events}
    ordered = sorted(unique.values(), key=lambda e: (e["time_et"] or "99:99", e["label"]))
    return {
        "date": day.isoformat(),
        "known": not missing,
        "missing": missing,
        "degraded": degraded,
        "events": ordered,
    }


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
    labels = ", ".join(f"{e['label']} {e['time_et']}" if e.get("time_et") else e["label"] for e in events)
    major = [_minute(e["time_et"]) for e in events if e.get("major") and e.get("time_et")]
    if not major:
        return "none", None, labels
    past = [m for m in major if m <= now_min]
    if not past:
        return "before", None, labels
    return "after", float(now_min - max(past)), labels
