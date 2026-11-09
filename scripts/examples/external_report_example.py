"""An example external-report script: the last sessions of the VIX, from Cboe's free daily history.

This is the pattern for your own report (docs/guide/external-reports.md). A script, not package
code, because it reaches the network; the suite's packages stay network-free. Copy it under your
suite home (`~/.cherrypick/scripts/`), change what it fetches and how it shapes the rows, and list
it in `config.json` under `external_reports`.

    python external_report_example.py [--sessions N]

It writes `<home>/data/external/vix-history.json` (the home is `$CHERRYPICK_HOME` or
`~/.cherrypick`), atomically, so the console never reads a half-written file. Check the result with
`python run.py external-check ~/.cherrypick/data/external/vix-history.json`.

The data is Cboe's public index history file, fetched for your own use. Read the site's terms
before you point a script at any other source, and do not republish what you fetch.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core.jsonio import write_json_atomic

REPORT_ID = "vix-history"
# The same free file the suite's own market-files job reads (OHLC columns, one row a session).
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
SESSIONS = 30
# A weekend plus a holiday is three days; the card turns amber past this.
STALE_AFTER_HOURS = 96
USER_AGENT = "cherrypick-external-report-example/1.0"


def parse_history(text: str) -> list[tuple[str, float, float, float, float]]:
    """(iso date, open, high, low, close) per session, oldest first, from Cboe's CSV text. A row that
    does not parse is skipped rather than failing the report."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    header = [h.strip().upper() for h in rows[0]]
    if header[:1] != ["DATE"] or not {"OPEN", "HIGH", "LOW", "CLOSE"} <= set(header):
        raise ValueError(f"unexpected columns: {rows[0]}")
    idx = [header.index(c) for c in ("OPEN", "HIGH", "LOW", "CLOSE")]
    out = []
    for row in rows[1:]:
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date().isoformat()
            o, h, lo, c = (float(row[i]) for i in idx)
        except (ValueError, IndexError):
            continue
        out.append((day, o, h, lo, c))
    out.sort()
    return out


def shape_report(text: str, now: datetime, sessions: int = SESSIONS) -> dict:
    """The report document for the last `sessions` sessions in `text`, newest first. Pure: the
    clock is passed in and nothing is read or written."""
    sessions = max(1, min(sessions, 400))  # the contract allows 500 rows a table
    history = parse_history(text)
    if not history:
        raise ValueError("the history file held no sessions")
    last = history[-sessions:]
    rows = []
    for i in range(len(last) - 1, -1, -1):
        day, o, h, lo, c = last[i]
        previous = history[len(history) - len(last) + i - 1][4] if len(history) - len(last) + i > 0 else None
        change = round(c - previous, 2) if previous is not None else None
        rows.append([day, o, h, lo, c, change])
    latest = last[-1]
    high = max(r[2] for r in last)
    low = min(r[3] for r in last)
    return {
        "schema": 1,
        "id": REPORT_ID,
        "title": "VIX daily history",
        "source": "Cboe public index history (example script)",
        "as_of": now.astimezone().isoformat(timespec="seconds"),
        "stale_after_hours": STALE_AFTER_HOURS,
        "summary": (
            f"VIX closed {latest[4]:.2f} on {latest[0]}; {len(last)}-session range {low:.2f} to {high:.2f}."
        ),
        "sections": [
            {
                "heading": f"Last {len(last)} sessions",
                "table": {"columns": ["Date", "Open", "High", "Low", "Close", "Change"], "rows": rows},
            },
            {
                "heading": "About this report",
                "text": "Written by scripts/examples/external_report_example.py from Cboe's free daily "
                "history file. Change is the close less the session before's.",
            },
        ],
    }


def fetch(url: str = CBOE_URL, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- a fixed https URL
        return resp.read().decode("utf-8", errors="replace")


def report_path() -> Path:
    return _home.data_dir() / "external" / f"{REPORT_ID}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=int, default=SESSIONS, help="how many sessions to show")
    args = parser.parse_args(argv)
    try:
        doc = shape_report(fetch(), datetime.now().astimezone(), args.sessions)
    except (OSError, ValueError) as exc:
        print(f"could not build the report: {exc}", file=sys.stderr)
        return 1
    path = write_json_atomic(report_path(), doc, default=None)
    print(json.dumps({"wrote": str(path), "sessions": len(doc["sections"][0]["table"]["rows"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
