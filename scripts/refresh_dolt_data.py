"""Pull the DoltHub datasets the earnings module reads, and record how far its calendar now reaches.

**Why this exists.** On 2026-08-25 the earnings module was found not to have paper traded for eleven
sessions. Nothing was broken: Dolt was running, its tables were fully populated, the loop ticked and
the config was enabled. The local clone was simply 55 commits behind, so `earnings_calendar` ended
at 2026-08-14 and the scanner had no upcoming announcements to scan. An earnings calendar published
on a given day only reaches ~5 weeks forward, so a clone that is never pulled stops feeding the
module roughly a month later — silently, because "no candidates today" and "a quiet earnings week"
look identical.

Nothing in the suite refreshed it. The `earnings-dolt` job keeps the sql-server alive; it never
updated the data.

A script rather than package code, for the same reason as the narratives and the futures resolver:
this reaches the network, and nothing on a decision path may. It is read-only against the suite —
it writes only the Dolt clones it owns and one state file — and a failure leaves the previous data
exactly where it was.

The state file is what makes the staleness VISIBLE: `state/dolt_data.json` records the calendar's
furthest date, and the watchdog reads that file (it is stdlib-and-files only, so it cannot query
Dolt itself) to warn before the horizon runs out rather than after.

    python scripts/refresh_dolt_data.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home

STATE_NAME = "dolt_data.json"
# The clones the earnings module reads. `earnings` carries the announcement calendar that decides
# whether the scanner has anything to look at; the other two carry the price/option history it
# scores candidates with.
DATABASES = ("earnings", "options", "stocks")


def _data_dir() -> Path:
    return _home.data_dir("earnings")


def _pull(repo: Path) -> dict:
    if not (repo / ".dolt").is_dir():
        return {"ok": False, "reason": "not_a_dolt_clone"}
    try:
        res = subprocess.run(
            ["dolt", "pull"], cwd=repo, capture_output=True, text=True, timeout=1800
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    # Dolt draws an ANSI progress spinner on the same line; keeping it would bury the one line that
    # matters ("Everything up-to-date", or the commit range it moved) under kilobytes of backspaces.
    raw = (res.stdout or "") + (res.stderr or "")
    lines = []
    for chunk in raw.replace(chr(13), chr(10)).splitlines():
        seg = chunk.split(chr(8))[-1].strip()
        if seg and "Pulling..." not in seg and "Fetching..." not in seg:
            lines.append(seg)
    return {
        "ok": res.returncode == 0,
        "returncode": res.returncode,
        "tail": lines[-3:],
    }


def _calendar_max_date() -> str | None:
    """How far the announcement calendar now reaches, read through the running sql-server.

    None when it cannot be read — which the watchdog treats as unknown rather than fine, since a
    calendar nobody can query is exactly as useless to the scanner as an empty one."""
    try:
        import mysql.connector as _mysql

        cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database="earnings")
        try:
            cur = cn.cursor()
            cur.execute("SELECT MAX(date) FROM earnings_calendar")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] is not None else None
        finally:
            cn.close()
    except Exception:  # noqa: BLE001 — the pull still succeeded; only the reading is unavailable
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report state, pull nothing")
    args = ap.parse_args(argv)

    base = _data_dir()
    results = {}
    if not args.dry_run:
        for name in DATABASES:
            results[name] = _pull(base / name)

    payload = {
        "refreshed_at": datetime.now(UTC).isoformat(),
        "databases": results,
        # The one number the watchdog acts on: past this date the scanner has nothing to scan.
        "earnings_calendar_max_date": _calendar_max_date(),
    }
    if not args.dry_run:
        path = _home.state_dir() / STATE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    print(json.dumps(payload, indent=2))
    return 0 if all(r.get("ok") for r in results.values()) or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
