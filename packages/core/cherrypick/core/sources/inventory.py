"""Read every declared source's span, read-only, into one cache: per symbol its first and last date and
how many sessions it holds. `coverage` answers from the cache, so asking never scans a store.

- **sqlite**: one grouped query per source (`MIN`, `MAX`, `COUNT(DISTINCT date)` by symbol), opened
  `mode=ro`. The largest, the stock history, takes seconds.
- **dolt**: the server's tables are keyed by date first, so a symbol's span is a full scan there. They
  are read one date at a time instead (all symbols on a date is a key lookup), and only the dates
  after the last run's, so the first run takes minutes and later ones seconds. `connect` is passed
  in: core does not depend on the MySQL connector.
- **csv**: one file per symbol, named by it, dates in the first column.
- **store_cache**: the backtest's own inventory of the vendor's stores (`store_inventory.json`, its
  counts only), never the store itself.

A source that fails is recorded with its `error` and the rest still run. A store not on this machine
(no Dolt, no gex recorder, the vendor's stores after their purge) is `absent`, and a Dolt source not
read on a run is `skipped`: neither is a failure. `undeclared` names a data file
no source declares and `IGNORED` does not explain, so a new store is declared as it lands.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import IGNORED, SOURCES, Source, sparse_years

#: The suffixes a data file has; anything else under the data root is not a store.
DATA_SUFFIXES = (".db", ".sqlite", ".csv", ".parquet")


def data_root() -> Path:
    return _home.data_dir()


def path() -> Path:
    """Where the inventory is cached."""
    return _home.state_dir() / "sources_inventory.json"


def load(where: Path | None = None) -> dict:
    p = where or path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}}


def write(doc: dict, where: Path | None = None) -> Path:
    p = where or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return p


def _span(first, last, sessions) -> dict:
    return {"first": str(first)[:10], "last": str(last)[:10], "sessions": int(sessions)}


def _from_years(by_year: dict[str, dict[str, list]]) -> dict[str, dict]:
    """{symbol: {year: [first, last, sessions]}} into each symbol's span with its per-year counts."""
    out = {}
    for sym, years in by_year.items():
        firsts, lasts = [v[0] for v in years.values()], [v[1] for v in years.values()]
        out[sym] = {
            **_span(min(firsts), max(lasts), sum(v[2] for v in years.values())),
            "years": {y: int(v[2]) for y, v in sorted(years.items())},
        }
    return out


def probe_sqlite(root: Path, s: Source) -> dict:
    p = s.probe
    db = root / p.where
    if not db.exists():
        return {"absent": f"no {p.where} on this machine"}
    where = f"WHERE {p.filter}" if p.filter else ""
    # Grouped by the upper-cased symbol, so one symbol spelt two ways is one span, not two; and by
    # year, so a span sparse in some years is read year by year (`sources.window_sessions`).
    sql = (
        f'SELECT UPPER("{p.symbol_col}") AS sym, SUBSTR("{p.date_col}", 1, 4) AS yr, MIN("{p.date_col}"), '
        f'MAX("{p.date_col}"), COUNT(DISTINCT "{p.date_col}") FROM "{p.table}" {where} GROUP BY sym, yr'
    )
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    by_year: dict[str, dict[str, list]] = {}
    for sym, yr, lo, hi, n in rows:
        if sym and lo:
            by_year.setdefault(str(sym), {})[str(yr)] = [str(lo)[:10], str(hi)[:10], n]
    return {"symbols": _from_years(by_year)}


def _csv_date(raw: str, fmt: str) -> str | None:
    raw = raw.strip()
    try:
        if fmt == "mdy":
            return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
        return datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def probe_csv(root: Path, s: Source) -> dict:
    folder = root / s.probe.where
    if not folder.is_dir():
        return {"absent": f"no {s.probe.where} on this machine"}
    by_series: dict[str, set[str]] = {}
    for f in sorted(folder.glob("*.csv")):
        with f.open(encoding="utf-8", newline="") as fh:
            days = {d for row in csv.reader(fh) if row and (d := _csv_date(row[0], s.probe.date_format))}
        # One file per symbol, named by it; or, with `series`, every file one series (one per year).
        by_series.setdefault(s.probe.series or f.stem.upper(), set()).update(days)
    by_year: dict[str, dict[str, list]] = {}
    for k, days in by_series.items():
        for d in days:
            v = by_year.setdefault(k, {}).setdefault(d[:4], [d, d, 0])
            v[0], v[1], v[2] = min(v[0], d), max(v[1], d), v[2] + 1
    return {"symbols": _from_years(by_year)}


def probe_store_cache(root: Path, s: Source) -> dict:
    cache = root / "backtest" / "results" / "store_inventory.json"
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"absent": "the backtest's store inventory is not on this machine"}
    hit = doc.get(s.probe.where)
    if not hit:
        return {
            "absent": f"{s.probe.where} is not in the backtest's store inventory (purged or never fetched)"
        }
    tables = (hit.get("shape") or {}).get("tables") or {}
    names = [s.probe.table] if s.probe.table else sorted(tables)
    for t in names:
        info = tables.get(t) or {}
        if info.get("span"):
            lo, hi = info["span"]
            # Rows are a count of the table's rows, not sessions: kept only for the session table.
            sessions = info.get("rows") if t == "vendor_sessions" else None
            return {"store": {"first": lo[:10], "last": hi[:10], "sessions": sessions}}
    return {"error": f"{s.probe.where} has no dated table in the backtest's store inventory"}


def probe_dolt(s: Source, connect: Callable[[str], Any], previous: dict | None) -> dict:
    """Read one date at a time from a date-keyed Dolt table, carrying on from `previous` (this source's
    last inventory entry) so only new dates are read."""
    database, table = s.probe.where.split(".", 1)
    prev = previous if previous and previous.get("symbols") else {}
    if any("years" not in v for v in (prev.get("symbols") or {}).values()):
        prev = {}  # an entry written before per-year counts were kept: read it all again
    spans: dict[str, dict] = {k: dict(v) for k, v in (prev.get("symbols") or {}).items()}
    through = prev.get("read_through")
    conn = connect(database)
    try:
        cur = conn.cursor()
        dc, sc = s.probe.date_col, s.probe.symbol_col
        if through:
            cur.execute(
                f"SELECT DISTINCT `{dc}` FROM `{table}` WHERE `{dc}` > %s ORDER BY `{dc}`", (through,)
            )
        else:
            cur.execute(f"SELECT DISTINCT `{dc}` FROM `{table}` ORDER BY `{dc}`")
        dates = [str(r[0])[:10] for r in cur.fetchall()]
        for d in dates:
            cur.execute(f"SELECT DISTINCT `{sc}` FROM `{table}` WHERE `{dc}` = %s", (d,))
            for (sym,) in cur.fetchall():
                key = str(sym).upper()
                span = spans.get(key)
                if span is None:
                    span = spans[key] = {"first": d, "last": d, "sessions": 0, "years": {}}
                span["first"] = min(span["first"], d)
                span["last"] = max(span["last"], d)
                span["sessions"] += 1
                # Kept whole for a Dolt source: the next run adds to these counts.
                years = span.setdefault("years", {})
                years[d[:4]] = years.get(d[:4], 0) + 1
            through = d
    finally:
        conn.close()
    return {"symbols": spans, "read_through": through}


def run(
    sources: Iterable[Source] = SOURCES,
    *,
    root: Path | None = None,
    previous: dict | None = None,
    connect_dolt: Callable[[str], Any] | None = None,
    only: set[str] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """Read every source's span. `previous` is the last inventory: a Dolt source carries on from it, and
    a source skipped by `only` keeps its old entry. Never raises for one source: its error is kept."""
    root = root or data_root()
    prev_sources = (previous or {}).get("sources") or {}
    out: dict[str, dict] = {}
    for s in sources:
        if only and s.name not in only:
            if s.name in prev_sources:
                out[s.name] = prev_sources[s.name]
            continue
        t0 = time.monotonic()
        try:
            if s.probe.backend == "sqlite":
                entry = probe_sqlite(root, s)
            elif s.probe.backend == "csv":
                entry = probe_csv(root, s)
            elif s.probe.backend == "store_cache":
                entry = probe_store_cache(root, s)
            elif s.probe.backend == "dolt":
                if connect_dolt is None:
                    kept = prev_sources.get(s.name) or {}
                    entry = kept if kept.get("symbols") else {"skipped": "Dolt is not read on this run"}
                else:
                    entry = probe_dolt(s, connect_dolt, prev_sources.get(s.name))
            else:
                entry = {"error": f"unknown backend {s.probe.backend!r}"}
        except Exception as exc:  # noqa: BLE001 -- one failing source never stops the rest
            entry = {"error": f"{type(exc).__name__}: {exc}"}
        if s.probe.backend in ("sqlite", "csv"):
            # Read whole each run, so a dense span keeps only its ends; a sparse one keeps its years.
            for span in (entry.get("symbols") or {}).values():
                kept = sparse_years(span, span.pop("years", {}))
                if kept:
                    span["years"] = kept
        entry["read_at"] = datetime.now().isoformat(timespec="seconds")
        out[s.name] = entry
        n = len(entry.get("symbols") or {})
        log(f"{s.name}: {entry.get('error') or f'{n} symbols'} ({time.monotonic() - t0:.1f}s)")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": out,
        "undeclared": undeclared(root),
    }


def declared_paths(sources: Iterable[Source] = SOURCES) -> set[str]:
    """Each declared file or folder under the data root, `/`-separated."""
    return {s.probe.where for s in sources if s.probe.backend in ("sqlite", "csv")}


def undeclared(root: Path | None = None, sources: Iterable[Source] = SOURCES) -> list[str]:
    """Data files under the data root that no source declares and `IGNORED` does not explain."""
    root = root or data_root()
    if not root.is_dir():
        return []
    declared = declared_paths(sources)
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in DATA_SUFFIXES:
            continue
        rel = p.relative_to(root).as_posix()
        if rel in declared or any(rel.startswith(d + "/") for d in declared):
            continue
        if any(rel.startswith(prefix) for prefix in IGNORED):
            continue
        out.append(rel)
    return out
