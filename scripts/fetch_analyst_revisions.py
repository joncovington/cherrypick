"""Fetch per-firm analyst rating and price-target events from Yahoo's public quote-summary history.

**Why.** The vendor's board counts an Analyst Revision tag (an upgrade or downgrade, an initiation
that starts bullish or bearish, or a price-target change of 5% or more), and the market-report plan
had it as paid data (gap 2). Yahoo's `upgradeDowngradeHistory` carries every field the rule needs:
firm, old and new rating, action, and old and new target, back to 2012. Checked against the saved
editions on 2026-10-15, it held the event behind 33 of the board's 37 tags; the four it lacks are
firms it does not carry (Bernstein, Erste) or single notes it missed.

**Unofficial and personal-use.** The endpoint is undocumented and its terms are personal use only,
the same footing as the sector fetch (`scripts/fetch_sectors.py`). This is research input
for one person's own report, stored locally and never redistributed.

**Append-only, so it is point-in-time.** Every fetch returns a symbol's whole history. A new event is
inserted with `first_seen_at`; one already held only has `last_seen_at` moved. Values are never
rewritten, so an event Yahoo later edits keeps what we first saw, and one it deletes stays with an
old `last_seen_at` -- the history the backtest reads is what was knowable, plus how late we saw it.

A script, not package code, because it reaches the network; `packages/technicals` stays
network-free and reads the table read-only (`revisions.load`).

**Which names** (`default_symbols`): every name the technicals store lands plus every name on the
latest income screener lists, about 1,020 on 2026-10-17. Each is asked every run, since a fetch is
the whole history and there is no asking for only what is new. Run nightly by the orchestrator's
`technicals-revisions` job.

**Paced.** One symbol every `PAUSE_S` seconds (about 35 minutes for the ~1,020 names); a 429
stops the run with everything fetched so far kept. Funds return no history and cost one call.

    python scripts/fetch_analyst_revisions.py [--symbols A B ...] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from cherrypick.technicals import paths, symbols

PAUSE_S = 2.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
HISTORY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=upgradeDowngradeHistory&crumb={crumb}"
SOURCE = "yahoo"

SCHEMA = """
CREATE TABLE IF NOT EXISTS revision_events (
    symbol TEXT NOT NULL, graded_at TEXT NOT NULL,   -- UTC ISO timestamp of the note
    firm TEXT NOT NULL, action TEXT NOT NULL,        -- up, down, init, main, reit
    from_grade TEXT, to_grade TEXT,
    pt_action TEXT, pt_prior REAL, pt_current REAL,  -- Yahoo writes 0 for "no target"
    source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    PRIMARY KEY (symbol, graded_at, firm, action));
CREATE TABLE IF NOT EXISTS fetches (
    symbol TEXT NOT NULL, fetched_at TEXT NOT NULL,
    events INTEGER, new_events INTEGER, error TEXT,
    PRIMARY KEY (symbol, fetched_at));
"""


def session() -> tuple[urllib.request.OpenerDirector, str]:
    """An opener holding Yahoo's consent cookie, and the crumb that must accompany each call."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    try:
        opener.open(COOKIE_URL, timeout=20)
    except urllib.error.HTTPError:
        pass  # it answers 404 and still sets the cookie
    crumb = opener.open(CRUMB_URL, timeout=20).read().decode().strip()
    if not crumb or "<" in crumb:
        raise RuntimeError(f"no crumb: {crumb[:80]!r}")
    return opener, crumb


def parse(doc: dict) -> list[dict]:
    """The history rows, or [] for a symbol Yahoo holds none for."""
    result = (doc.get("quoteSummary") or {}).get("result") or []
    if not result:
        return []
    return (result[0].get("upgradeDowngradeHistory") or {}).get("history") or []


def rows_of(symbol: str, history: list[dict], seen_at: str) -> list[tuple]:
    out = []
    for h in history:
        epoch = h.get("epochGradeDate")
        if epoch is None or not h.get("firm"):
            continue
        out.append(
            (
                symbol,
                datetime.fromtimestamp(epoch, UTC).isoformat(),
                h["firm"].strip(),
                (h.get("action") or "").strip(),
                (h.get("fromGrade") or "").strip(),
                (h.get("toGrade") or "").strip(),
                (h.get("priceTargetAction") or "").strip(),
                h.get("priorPriceTarget"),
                h.get("currentPriceTarget"),
                SOURCE,
                seen_at,
                seen_at,
            )
        )
    return out


def store(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """Insert new events, touch the ones already held; returns how many were new."""
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO revision_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    new = conn.total_changes - before
    conn.executemany(
        "UPDATE revision_events SET last_seen_at = ? "
        "WHERE symbol = ? AND graded_at = ? AND firm = ? AND action = ?",
        [(r[11], r[0], r[1], r[2], r[3]) for r in rows],
    )
    return new


def default_symbols() -> list[str]:
    """Every name the store lands (the universe candidates the boards are scored on, the earnings
    calendar's liquid names, the funds and benchmarks) and every name on the latest income screener
    lists, which the store does not all hold."""
    from cherrypick.technicals import liquidity

    return sorted({*symbols.all_symbols(), *liquidity.screener_names()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", help="these tickers instead of the technicals universe")
    ap.add_argument("--limit", type=int, help="fetch at most this many symbols this run")
    ap.add_argument("--dry-run", action="store_true", help="fetch and count; store nothing")
    args = ap.parse_args(argv)

    todo = [s.strip().upper() for s in args.symbols] if args.symbols else default_symbols()
    if args.limit is not None:
        todo = todo[: args.limit]
    conn = None
    if not args.dry_run:
        path = paths.analyst_revisions()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
    report = {"due": len(todo), "fetched": 0, "events": 0, "new_events": 0, "failed": [], "stopped": None}
    started = time.monotonic()
    opener, crumb = session()
    for i, sym in enumerate(todo):
        if i:
            time.sleep(PAUSE_S)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        url = HISTORY_URL.format(symbol=sym.replace(".", "-"), crumb=crumb)
        try:
            history = parse(json.loads(opener.open(url, timeout=30).read()))
            error = None
        except urllib.error.HTTPError as exc:
            history, error = [], f"HTTP {exc.code}"
            if exc.code == 404:
                error = None  # a symbol Yahoo does not know: no history, not a failure
            elif exc.code == 429:
                report["stopped"] = f"429 at {sym}"
        except Exception as exc:  # noqa: BLE001 -- one symbol's failure keeps its previous rows
            history, error = [], f"{type(exc).__name__}: {str(exc)[:120]}"
        rows = rows_of(sym, history, now)
        new = 0
        if conn is not None:
            new = store(conn, rows) if not error else 0
            conn.execute(
                "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?)", (sym, now, len(rows), new, error)
            )
            conn.commit()
        if error:
            report["failed"].append(f"{sym}: {error}")
        else:
            report["fetched"] += 1
            report["events"] += len(rows)
            report["new_events"] += new
        if report["stopped"]:
            break
    if conn is not None:
        conn.close()
    report.update(ok=not report["failed"], seconds=round(time.monotonic() - started))
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
