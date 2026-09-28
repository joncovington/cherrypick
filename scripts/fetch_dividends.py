"""Fetch tastytrade's dividend history for every symbol the technicals store holds.

**Why.** Dolt's dividend table has gaps, zeros and wrong amounts (CSCO's and CHT's July 2024
dividends missing, BAP's September 2024 recorded as 0.0), and adjusted prices inherit every one:
against the vendor's own bars for 40 names, Dolt alone agreed on 86.8% of prices. Reconciled with
tastytrade's history (packages/technicals, `dividends.reconcile`) it is 93.3%, with 36 of the 40
names at 99% or better. This script supplies the second source.

A script, not package code, because it reaches the broker; `packages/technicals` stays
network-free and reads the file this writes. Read-only against the broker.

**Paced and incremental.** One symbol a second. A symbol is refetched only when its history is
older than `REFRESH_DAYS`, so the first run takes about ten minutes for ~500 symbols and a daily run
after that touches only the ones that have aged out. A failure keeps that symbol's previous history.

    python scripts/fetch_dividends.py [--all] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta

from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
from cherrypick.technicals import paths, symbols

PAUSE_S = 1.0
REFRESH_DAYS = 7


def load() -> dict:
    try:
        return json.loads(paths.tastytrade_dividends().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"symbols": {}}


def save(doc: dict) -> None:
    path = paths.tastytrade_dividends()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a file


def due(doc: dict, wanted: list[str], now: datetime, force: bool) -> list[str]:
    if force:
        return wanted
    cutoff = now - timedelta(days=REFRESH_DAYS)
    out = []
    for sym in wanted:
        fetched = (doc["symbols"].get(sym) or {}).get("fetched_at")
        if not fetched or datetime.fromisoformat(fetched) < cutoff:
            out.append(sym)
    return out


async def fetch(session, todo: list[str], doc: dict) -> dict:
    from tastytrade.metrics import get_dividends

    report = {"fetched": 0, "failed": []}
    for i, sym in enumerate(todo):
        if i:
            await asyncio.sleep(PAUSE_S)
        try:
            rows = await get_dividends(session, sym.replace(".", "/"))
        except Exception as exc:  # noqa: BLE001 -- keep the previous history; report and go on
            report["failed"].append(f"{sym}: {type(exc).__name__}: {str(exc)[:120]}")
            if "429" in str(exc):
                break
            continue
        doc["symbols"][sym] = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "dividends": sorted([str(r.occurred_date), float(r.amount)] for r in rows),
        }
        report["fetched"] += 1
        if report["fetched"] % 50 == 0:
            save(doc)  # progress survives an interrupted first run
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--all", action="store_true", help="refetch every symbol, not only the aged-out ones")
    ap.add_argument("--limit", type=int, help="fetch at most this many symbols this run")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    doc = load()
    todo = due(doc, symbols.all_symbols(), datetime.now(UTC), args.all)
    if args.limit is not None:
        todo = todo[: args.limit]
    started = time.monotonic()
    try:
        report = asyncio.run(fetch(SessionManager(store).get_session(), todo, doc))
    finally:
        doc["updated_at"] = datetime.now(UTC).isoformat()
        save(doc)
    report.update(
        ok=not report["failed"],
        due=len(todo),
        held=len(doc["symbols"]),
        seconds=round(time.monotonic() - started),
    )
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
