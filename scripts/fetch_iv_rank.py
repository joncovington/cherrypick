"""Record tastytrade's IV rank (and its liquidity reading) for every symbol the technicals store holds.

**Why.** The technicals store ranks Dolt's IV history itself (`store.iv_rank`), but Dolt does not
cover every name -- none of the ADRs, for one -- and where it does, its IV is not the series the
vendor ranks (correlation 0.66 against the vendor's `impliedVolatilityRank`). tastytrade's market
metrics are the fallback where we cannot calculate: `store.iv_rank` reads this file for a name Dolt
has no IV for. Its liquidity rating rides along, recorded for a later comparison with the vendor's
`liquidityRank`.

A script, not package code, because it reaches the broker; `packages/technicals` stays
network-free and reads the file this writes. Read-only against the broker.

**One reading per symbol per session**, keyed by the New York date of tastytrade's own
`implied_volatility_updated_at`, so a rerun the same day replaces that day's reading rather than
adding one. Ranks are stored 0-100. Batches of 50 symbols, a second apart; a failed batch keeps the
file as it was for those symbols.

    python scripts/fetch_iv_rank.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
from cherrypick.technicals import paths, symbols

BATCH = 50
PAUSE_S = 1.0
NY = ZoneInfo("America/New_York")


def load() -> dict:
    try:
        return json.loads(paths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": {}}


def save(doc: dict) -> None:
    path = paths.tastytrade_iv_rank()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a file


def _pct(v) -> float | None:
    return None if v is None else round(float(v) * 100.0, 2)


def reading(m) -> tuple[str, dict]:
    """(session date, the fields worth keeping) for one market-metrics row."""
    stamp = m.implied_volatility_updated_at or m.updated_at or datetime.now(UTC)
    day = stamp.astimezone(NY).date().isoformat()
    return day, {
        "iv_rank": _pct(m.implied_volatility_index_rank),  # tastytrade's headline rank
        "iv_rank_source": m.implied_volatility_index_rank_source,
        "tw_iv_rank": _pct(m.tw_implied_volatility_index_rank),
        "tos_iv_rank": _pct(m.tos_implied_volatility_index_rank),
        "iv_percentile": _pct(m.implied_volatility_percentile),
        "iv_index": None if m.implied_volatility_index is None else float(m.implied_volatility_index),
        "hv_30": None if m.historical_volatility_30_day is None else float(m.historical_volatility_30_day),
        "liquidity_rating": m.liquidity_rating,
        "liquidity_rank": None if m.liquidity_rank is None else float(m.liquidity_rank),
        "updated_at": stamp.isoformat(),
    }


async def fetch(session, todo: list[str], doc: dict) -> dict:
    from tastytrade.metrics import get_market_metrics

    report = {"fetched": 0, "failed": []}
    for k in range(0, len(todo), BATCH):
        if k:
            await asyncio.sleep(PAUSE_S)
        batch = todo[k : k + BATCH]
        try:
            rows = await get_market_metrics(session, [s.replace(".", "/") for s in batch])
        except Exception as exc:  # noqa: BLE001 -- keep what the file holds; report and go on
            report["failed"].append(f"{batch[0]}..{batch[-1]}: {type(exc).__name__}: {str(exc)[:120]}")
            if "429" in str(exc):
                break
            continue
        for m in rows:
            day, fields = reading(m)
            doc["days"].setdefault(day, {})[m.symbol.replace("/", ".")] = fields
            report["fetched"] += 1
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--limit", type=int, help="fetch at most this many symbols this run")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    doc = load()
    todo = symbols.all_symbols()
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
        wanted=len(todo),
        days_held=len(doc["days"]),
        seconds=round(time.monotonic() - started),
    )
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
