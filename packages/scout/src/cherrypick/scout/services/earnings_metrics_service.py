"""Read-only access to the **earnings** package's ``entry_reviews`` table -- the per-symbol
screening metric vector recorded on every entry scan (accepted or rejected), written by
``cherrypick.earnings.db``/``db_paper`` into either of that module's two SQLite databases (live
``earnings_trades.db``, paper ``paper_trades.db`` -- the one actually populated daily, by the
automated forced-sampling harness).

Scout stays decoupled from ``cherrypick.earnings`` (no import of that package anywhere -- see
CLAUDE.md's invariants). The two DB paths are resolved directly from
``cherrypick.core.home.data_dir`` using the same env var name (``EARNINGS_DATA_DIR``) and
filenames earnings' own ``paths.py`` resolves to, rather than importing that module -- a
deliberate, narrow duplication of two path strings, not a code dependency. Both files are opened
``mode=ro`` and never written to -- scout is a reader of this data, never the producer, the same
posture ``services/streamcache.py`` already takes for the suite's shared stream cache. A missing DB
file (a fresh install, or a machine that has never run earnings) degrades to an empty result, never
an exception; likewise a DB that predates the ``entry_reviews`` table (or this feature's columns)
raising ``sqlite3.OperationalError``.

``get_upcoming`` is a thin composition on top of ``calendar_service.get_calendar`` -- the forward
(next-N-**trading**-days) earnings view the Calendar tab already computes. ``days`` here means
trading days (matching earnings' own ``symbol_watch.py`` scan window); this function converts to
a calendar-day span via ``cherrypick.core.calendar.nth_trading_day`` before calling
``get_calendar``, which itself still only ever takes calendar days. ``watchlist_symbols`` is
sourced exactly as ``api/calendar.py`` sources it for the Calendar tab's own default call
(scout's own watchlist file); ``get_calendar`` itself already unions that with tastytrade's own
public watchlists, so this view carries the same coverage without re-implementing that union here.
It adds no new per-symbol chain/DXLink/straddle call of its own.

``get_upcoming`` also merges in ``symbol_watch.json`` -- the earnings package's own scheduled
forward-preview scan (``cherrypick.earnings.symbol_watch``, run outside this module's request path
by an orchestrator-scheduled task), a third sibling of the ``entry_reviews`` read-only exception
above. It carries the genuinely broker-chain-heavy signals (price, expected_move_pct,
term_structure, iv_rv_ratio, winrate, historical move stats, and a recommended/near_miss/fail
``tier`` badge) this module deliberately never computes itself (see calendar_service.py's own
docstring on why), always merged in when present. It ALSO fills a real gap in calendar_service's
own metrics fields (market_cap/iv_rank/iv_percentile) as a fallback only: those come from
calendar_service's watchlist-scoped metrics call, which covers a narrower symbol set than the
scan's own liquid-universe filter -- any calendar row outside that set carries none of them, since
Dolt's earnings_calendar table has no such columns. The scan already paid for that exact data per
symbol, so this reuses it rather than leaving those rows permanently blank; a live metrics reading
is never overwritten, since it is fresher than the scan's.

**A calendar row that never matched a scan entry (by symbol AND earnings_date) is dropped, not
merely left blank** -- the whole point of this redesign is that Upcoming only shows what the scan
actually scanned (the liquid-enough universe, see symbol_watch.py's own docstring), not the broad
Dolt fallback calendar_service otherwise carries. A rescheduled earnings date is handled the same
way: since the merge key includes the date, a moved date simply drops that row until the next
scan pass catches up, rather than silently showing a reading computed for the old date. A missing/
empty/mid-pass snapshot means every row is currently unmatched, so the result is an empty list (in
that case the ``watch`` status -- never_run / scanning N of M / last refreshed -- is what a caller
should render, not the entries list). Entries are sorted tier-first (recommended, then near_miss,
then fail, then unscored), then by date, then symbol.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date as _date
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core import home as _home

from . import calendar_service

_EARNINGS_DATA_ENV = "EARNINGS_DATA_DIR"

_ENTRY_REVIEWS_MISSING_NOTE = "entry_reviews table not found (earnings database predates this feature)"
_NO_DB_NOTE = "no earnings database found yet"
_NO_REVIEWS_NOTE = "no entry reviews recorded yet"

_SYMBOL_WATCH_FILENAME = "symbol_watch.json"

# The subset of a symbol_watch.json entry merged onto a matching calendar row -- symbol/
# earnings_date/earnings_timing stay source-of-truth on the calendar side (metrics/Dolt), not
# overwritten here. These are fields calendar_service never computes itself (see its own
# docstring), so the scan is their only possible source -- always taken when present.
_SYMBOL_WATCH_MERGE_FIELDS = (
    "price",
    "avg_volume",
    "iv_rv_ratio",
    "iv_rv_source",
    "winrate",
    "winrate_sample",
    "avg_actual_move_pct",
    "move_dispersion_pct",
    "max_actual_move_pct",
    "move_tail_veto",
    "term_structure",
    "expected_move_pct",
    "implied_vs_avg_actual",
    "combined_open_interest",
    "combined_option_volume",
    "bid_ask_spread_pct",
    "net_combo_spread_pct",
    "tier",
    "tier_reasons",
)

# Tier-first sort order for the merged Upcoming list -- an unscored row (no chain data ever
# resolved, tier is None) sorts last, after every judged row including outright "fail".
_TIER_SORT_ORDER = {"recommended": 0, "near_miss": 1, "fail": 2}

# Fields calendar_service CAN also populate, from its own watchlist-scoped metrics call --
# filled in from the scan only as a fallback (entry's own value is None), never overwriting a
# live metrics reading, which is fresher than the scan's (stale by up to a scan interval). The
# broad Dolt-sourced majority of Upcoming rows (everything outside the ~85-symbol "All Earnings"
# watchlist calendar_service unions in) carries none of these on its own -- Dolt's
# earnings_calendar table has no such columns -- so without this fallback those rows would never
# show a market cap or IV rank at all, even though the scan already paid for that exact data via
# fetch_liquidity_criteria's per-symbol get_market_metrics call.
_SYMBOL_WATCH_FALLBACK_FIELDS = ("market_cap", "iv_rank", "iv_percentile")


def _earnings_data_dir() -> Path:
    """Mirrors ``cherrypick.earnings.paths``' own resolution (``data_dir("earnings",
    env="EARNINGS_DATA_DIR")``) without importing that package."""
    return _home.data_dir("earnings", env=_EARNINGS_DATA_ENV)


def live_db_path() -> Path:
    return _earnings_data_dir() / "earnings_trades.db"


def paper_db_path() -> Path:
    return _earnings_data_dir() / "paper_trades.db"


def _db_path(mode: str) -> Path:
    return live_db_path() if mode == "live" else paper_db_path()


def _symbol_watch_path() -> Path:
    return _earnings_data_dir() / _SYMBOL_WATCH_FILENAME


def _load_symbol_watch() -> dict:
    """Mirrors ``cherrypick.earnings.symbol_watch.read_snapshot`` without importing that
    package -- same narrow, deliberate duplication as the DB paths above. Never raises: a
    missing file (fresh install, or the scheduled scan has never run), a corrupt/partial file
    caught mid-write (the writer replaces atomically, but a defensive read stays cheap
    insurance), or any other read error all degrade to an empty shell."""
    path = _symbol_watch_path()
    if not path.exists():
        return {"pass_started_at": None, "pass_completed_at": None, "total": 0, "done": 0, "symbols": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"pass_started_at": None, "pass_completed_at": None, "total": 0, "done": 0, "symbols": {}}
    if not isinstance(data, dict):
        return {"pass_started_at": None, "pass_completed_at": None, "total": 0, "done": 0, "symbols": {}}
    return data


def get_watch_status() -> dict:
    """Progress of the earnings package's scheduled forward-preview scan -- what drives scout's
    "N of M done" / spinner UX for the Upcoming section. ``never_run`` is true when no snapshot
    file exists yet (a fresh install, or the scheduled task hasn't fired once)."""
    data = _load_symbol_watch()
    return {
        "ok": True,
        "never_run": not data.get("symbols"),
        "pass_started_at": data.get("pass_started_at"),
        "pass_completed_at": data.get("pass_completed_at"),
        "total": data.get("total") or 0,
        "done": data.get("done") or 0,
    }


def _merge_symbol_watch(entries: list[dict]) -> list[dict]:
    """Filter **and** merge: a calendar row survives only if it matches a scan entry by symbol
    AND earnings_date -- see this module's own docstring for why (the Upcoming display universe
    IS the scan's liquid-enough universe, not calendar_service's broader Dolt-inclusive one)."""
    watch_symbols = _load_symbol_watch().get("symbols") or {}
    merged = []
    for entry in entries:
        watch = watch_symbols.get(entry.get("symbol"))
        if not watch or watch.get("earnings_date") != entry.get("date"):
            continue
        merged.append(
            {
                **entry,
                **{field: watch.get(field) for field in _SYMBOL_WATCH_MERGE_FIELDS if field in watch},
                **{
                    field: watch.get(field)
                    for field in _SYMBOL_WATCH_FALLBACK_FIELDS
                    if entry.get(field) is None and watch.get(field) is not None
                },
                "watch_error": watch.get("error"),
                "watch_refreshed_at": watch.get("refreshed_at"),
            }
        )
    return merged


def _sort_key(entry: dict) -> tuple:
    tier_rank = _TIER_SORT_ORDER.get(entry.get("tier"), 3)
    return (tier_rank, entry.get("date") or "", entry.get("symbol") or "")


def open_ro(path: Path) -> sqlite3.Connection | None:
    """A read-only connection to one of the earnings DBs, or ``None`` if the file doesn't exist /
    can't be opened -- never raises. Mirrors ``services/streamcache.py``'s ``open_ro``."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def get_screen_dates(mode: str = "paper") -> list[str]:
    """Distinct ``scan_date`` values from ``entry_reviews``, most recent first. Empty list when the
    DB is missing, has no ``entry_reviews`` table yet, or has no rows."""
    conn = open_ro(_db_path(mode))
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT DISTINCT scan_date FROM entry_reviews ORDER BY scan_date DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [row["scan_date"] for row in rows]


def get_screens(scan_date: str | None = None, mode: str = "paper") -> dict:
    """Entry-review rows for ``scan_date`` (default: the most recent scan date on record), ranked by
    ``composite_score`` descending with nulls last. Degrades gracefully -- never raises -- when the
    DB file is missing, predates ``entry_reviews``, or simply has no rows yet."""
    conn = open_ro(_db_path(mode))
    if conn is None:
        return {"ok": True, "mode": mode, "scan_date": None, "rows": [], "note": _NO_DB_NOTE}
    try:
        if not scan_date:
            row = conn.execute("SELECT MAX(scan_date) AS d FROM entry_reviews").fetchone()
            scan_date = row["d"] if row else None
        if not scan_date:
            return {"ok": True, "mode": mode, "scan_date": None, "rows": [], "note": _NO_REVIEWS_NOTE}
        rows = conn.execute(
            "SELECT * FROM entry_reviews WHERE scan_date = ? "
            "ORDER BY (composite_score IS NULL), composite_score DESC",
            (scan_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {
            "ok": True,
            "mode": mode,
            "scan_date": None,
            "rows": [],
            "note": _ENTRY_REVIEWS_MISSING_NOTE,
        }
    finally:
        conn.close()
    return {"ok": True, "mode": mode, "scan_date": scan_date, "rows": [dict(r) for r in rows]}


async def get_upcoming(conn, session, cfg, days: int = 10) -> dict:
    """The forward (next-``days`` **trading days**) earnings view -- filters+merges
    ``calendar_service.get_calendar``'s rows against the scan's own snapshot (see
    ``_merge_symbol_watch``), then sorts tier-first (recommended, near_miss, fail, unscored), then
    by date, then symbol. Adds no data-fetch path of its own. ``watchlist_symbols`` is sourced
    exactly as ``api/calendar.py`` sources it for the Calendar tab's own default call (scout's own
    watchlist file); ``get_calendar`` itself already unions that with tastytrade's own public
    watchlists, so this view carries the same coverage without re-implementing that union here.
    ``days`` is trading days, converted to a calendar-day span before calling ``get_calendar``
    (which only ever takes calendar days) via ``cherrypick.core.calendar.nth_trading_day``."""
    from .. import config as _config
    from . import watchlist as _watchlist_service

    own_watchlist = _watchlist_service.load(_config.watchlist_path())
    today = _date.today()
    calendar_days = (_calendar.nth_trading_day(today, days) - today).days
    result = await calendar_service.get_calendar(conn, session, cfg, own_watchlist, days=calendar_days)
    if not result.get("ok"):
        return result
    entries = _merge_symbol_watch(result.get("entries", []))
    entries.sort(key=_sort_key)
    return {**result, "entries": entries, "watch": get_watch_status()}
