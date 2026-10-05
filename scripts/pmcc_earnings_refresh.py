"""Refresh pmcc's declared earnings calendar from the local Dolt clone.

pmcc refuses a weekly short whose life spans an earnings announcement (`earnings_span`, from
2026-10-06), reading the dates from its own config's `earnings` block -- nothing on a loop path may
read Dolt. This script keeps that block current: for every stock symbol in the block it writes the
announcements the local `earnings.earnings_calendar` holds from a fortnight back, and a
`declared_through` a week short of the calendar's own forward horizon (the published calendar only
reaches ~5 weeks ahead, and its last week is still filling in). An ETF entry (`"kind": "etf"`) has no
announcements and is left as declared.

Run daily after the 05:30 Dolt pull (`pmcc-earnings-refresh`). If Dolt cannot be read nothing is
written: the declared horizon then lapses on its own, and pmcc refuses `earnings_calendar_lapsed`
loudly -- a stale calendar must never read as "no announcement".

Writes through the orchestrator's config editor (backup, atomic write, guarded pointers); a run that
changes nothing writes nothing.

    python scripts/pmcc_earnings_refresh.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

LOOKBACK_DAYS = 14
HORIZON_MARGIN_DAYS = 7


def read_calendar(symbols: list[str]) -> tuple[str, dict[str, list[str]]]:
    """(the calendar's forward horizon, {symbol: dates from the lookback on})."""
    import mysql.connector as _mysql

    cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database="earnings", connection_timeout=15)
    try:
        cur = cn.cursor()
        cur.execute("SELECT MAX(date) FROM earnings_calendar")
        (horizon,) = cur.fetchone()
        if horizon is None:
            raise RuntimeError("earnings_calendar is empty")
        since = date.today() - timedelta(days=LOOKBACK_DAYS)
        out: dict[str, list[str]] = {s: [] for s in symbols}
        for s in symbols:
            cur.execute(
                "SELECT date FROM earnings_calendar WHERE act_symbol = %s AND date >= %s ORDER BY date",
                (s, since),
            )
            out[s] = [d.isoformat() for (d,) in cur.fetchall()]
        return horizon.isoformat(), out
    finally:
        cn.close()


def plan(block: dict, horizon: str, dates: dict[str, list[str]]) -> dict[str, dict]:
    """The new value for every stock entry of the `earnings` block. Pure, so it is tested alone."""
    through = (date.fromisoformat(horizon) - timedelta(days=HORIZON_MARGIN_DAYS)).isoformat()
    new = {}
    for symbol, entry in block.items():
        if symbol.startswith("_") or not isinstance(entry, dict) or entry.get("kind") == "etf":
            continue
        new[symbol] = {
            **entry,
            "declared_through": through,
            "dates": dates.get(symbol, []),
            "source": "local Dolt earnings.earnings_calendar",
        }
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the new entries; write nothing")
    args = ap.parse_args(argv)

    from cherrypick.orchestrator import config as cfgmod
    from cherrypick.orchestrator import configedit

    cfg = cfgmod.load_config()
    loaded = configedit.load(cfg, "pmcc")
    block = (loaded.get("doc") or {}).get("earnings")
    if not isinstance(block, dict):
        print(json.dumps({"ok": True, "skipped": "pmcc config declares no earnings calendar"}))
        return 0
    stocks = [
        s
        for s, e in block.items()
        if not s.startswith("_") and isinstance(e, dict) and e.get("kind") != "etf"
    ]
    try:
        horizon, dates = read_calendar(stocks)
    except Exception as exc:  # noqa: BLE001 -- nothing written; the declared horizon lapses on its own
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    new = plan(block, horizon, dates)
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "horizon": horizon, "entries": new}, indent=1))
        return 0
    text = loaded["text"]
    for symbol, entry in new.items():
        text = configedit.splice_value(text, f"/earnings/{symbol}", entry)
    result = configedit.apply_raw_save(cfg, "pmcc", text, loaded["mtime"])
    print(json.dumps({"horizon": horizon, "symbols": sorted(new), **result}, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
