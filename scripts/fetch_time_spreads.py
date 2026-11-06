"""The time-spread scanner's broker reads (docs/time-spread-scanner-plan.md). Read-only: no orders.

**Step 1, `term`.** The broker's market metrics for every name the liquidity verdict calls tradable,
in batches: each expiry's implied volatility, IV rank, 30-day IV and realised volatility, market cap
and the next earnings date. `cherrypick.manage.timespreads.term_row` picks the front and back expiries
and judges the prefilter; the passing names with the highest front/back ratios are the shortlist.

**Step 2, the shortlist's chains** (the default run does both steps). For each shortlisted name: the
spot, and both expiries' strikes within reach of it (`REACH_SD` standard deviations at the back IV,
at least `REACH_MIN`), quoted with volume and open interest. Manage builds the calendars and
diagonals from these when it evaluates (`timespreads.structures`), so the file holds quotes, never a
verdict.

A script, not package code, because it reaches the broker: the income reading's pattern
(fetch_screener_greeks.py income). Manage reads the day's file into its snapshot and stays
credential-free. One file a day, rewritten by each run that day: `time-spreads/<date>.json`; a
`--limit` run writes `<date>.trial.json`, which nothing reads.

    python scripts/fetch_time_spreads.py [--limit N] [--force] [--term-only] [--show]

`--force` runs outside regular hours (quotes after the close say nothing about a spread); `--show`
prints the structures manage would build from the file, for an on-demand look.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REACH_SD = 1.6  # strikes quoted either side of spot, in standard deviations over the back expiry
REACH_MIN = 0.08  # ... and at least this share of spot
METRICS_BATCH = 100


def _universe_builder():
    """The universe builder, loaded from beside this script: its symbol spelling, batches, pause and
    window are reused as they are."""
    path = Path(__file__).resolve().with_name("build_stock_universe.py")
    spec = importlib.util.spec_from_file_location("build_stock_universe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def out_path(day: date, trial: bool) -> Path:
    from cherrypick.core import home

    return home.data_dir("time-spreads") / f"{day.isoformat()}{'.trial' if trial else ''}.json"


def _f(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def metric_plain(m, sector: str | None) -> dict:
    """The SDK's metrics object as the plain values `timespreads.term_row` reads."""
    earnings = getattr(m, "earnings", None)
    return {
        "symbol": str(m.symbol).replace("/", "."),
        "iv_rank": _f(m.implied_volatility_index_rank),
        "iv_30d": _f(m.implied_volatility_30_day),
        "hv_30d": _f(m.historical_volatility_30_day),
        "market_cap": _f(m.market_cap),
        "earnings_date": str(earnings.expected_report_date)
        if earnings and earnings.expected_report_date
        else None,
        "expiries": [
            (str(e.expiration_date), _f(e.implied_volatility))
            for e in (m.option_expiration_implied_volatilities or [])
            if e.implied_volatility is not None
        ],
        "sector": sector,
    }


def reach(spot: float, iv: float | None, dte: int) -> float:
    """How far either side of spot to quote, in dollars."""
    sd = (iv or 0.0) * math.sqrt(max(dte, 1) / 365.0)
    return spot * max(REACH_MIN, REACH_SD * sd)


async def _metrics(session, ub, names: list[str]) -> list:
    from tastytrade.metrics import get_market_metrics

    out = []
    for batch in ub._batches([ub.to_tastytrade(s) for s in names], METRICS_BATCH):
        out += await get_market_metrics(session, batch)
        await asyncio.sleep(ub.TT_PAUSE_S)
    return out


async def _chains(session, ub, rows: dict[str, dict]) -> tuple[dict, str | None]:
    """{symbol: {spot, front, back}} for the shortlisted rows, each expiry's strikes within reach
    quoted; and the problem that stopped the reads, if one did (what was read is kept)."""
    from cherrypick.core.broker import standard_chains
    from tastytrade.instruments import NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type

    names = list(rows)
    spots: dict[str, float | None] = {}
    for batch in ub._batches(names, ub.TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, equities=[ub.to_tastytrade(s) for s in batch]):
            bid, ask = _f(q.bid), _f(q.ask)
            mid = (bid + ask) / 2 if bid and ask and ask >= bid else None
            spots[q.symbol.replace("/", ".")] = (
                mid or _f(getattr(q, "mark", None)) or _f(getattr(q, "last", None))
            )
        await asyncio.sleep(ub.TT_PAUSE_S)
    out: dict[str, dict] = {}
    legs: dict[str, dict] = {}
    problem = None
    for sym in names:
        row, spot = rows[sym], spots.get(sym)
        entry: dict = {"spot": spot}
        out[sym] = entry
        if not spot:
            entry["error"] = "no price"
            continue
        try:
            chains = await NestedOptionChain.get(session, ub.to_tastytrade(sym))
        except Exception as exc:  # noqa: BLE001 -- a 429 ends the reads; anything else is this name's
            if "429" in str(exc):
                problem = f"chain listings stopped: {type(exc).__name__}: {exc}"[:300]
                entry["error"] = "not read: the listings stopped"
                break
            entry["error"] = f"no chain: {type(exc).__name__}"
            continue
        width = reach(spot, row["back"]["iv"], row["back"]["dte"])
        for side in ("front", "back"):
            want = row[side]["expiration"]
            options = []
            for chain in standard_chains(chains, sym):
                for e in chain.expirations:
                    if e.expiration_date.isoformat() != want:
                        continue
                    for s in e.strikes:
                        k = float(s.strike_price)
                        if abs(k - spot) > width:
                            continue
                        for right, symbol in (("C", s.call), ("P", s.put)):
                            if symbol:
                                o = {"strike": k, "right": right, "option": symbol}
                                options.append(o)
                                legs[symbol] = o
            entry[side] = {"expiration": want, "dte": row[side]["dte"], "options": options}
            if not options:
                entry["error"] = f"no {side} strikes listed within reach"
        await asyncio.sleep(ub.TT_PAUSE_S)
    for batch in ub._batches(list(legs), ub.TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, options=batch):
            o = legs.get(q.symbol)
            if o is not None:
                o.update(bid=_f(q.bid), ask=_f(q.ask), volume=_f(q.volume), open_interest=_f(q.open_interest))
        await asyncio.sleep(ub.TT_PAUSE_S)
    return out, problem


def main(argv: list[str] | None = None) -> int:
    from zoneinfo import ZoneInfo

    from cherrypick.core import calendar as cal
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
    from cherrypick.manage import timespreads as ts
    from cherrypick.technicals import liquidity

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--limit", type=int, default=None, help="only the first N names (a trial file)")
    p.add_argument("--force", action="store_true", help="run outside regular hours")
    p.add_argument("--term-only", action="store_true", help="step 1 only: no chain reads")
    p.add_argument("--show", action="store_true", help="print the structures manage would build")
    args = p.parse_args(argv)

    ub = _universe_builder()
    now = datetime.now(ZoneInfo("America/New_York"))
    today = now.date()
    if not args.force and not ub.in_window(now, cal.is_trading_day(today), cal.session_close_hhmm(today)):
        print(json.dumps({"ok": True, "skipped": "outside regular hours (--force to run anyway)"}))
        return 0
    names = sorted(liquidity.tradable_names())[: args.limit]
    if not names:
        print(json.dumps({"ok": False, "reason": "no tradable names: the liquidity verdicts are missing"}))
        return 1
    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    try:
        from cherrypick.technicals import fundamentals

        sectors = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    except Exception:  # noqa: BLE001 -- the sector is a flag, never a reason not to scan
        sectors = {}
    started = datetime.now(UTC)
    session = SessionManager(store).get_session()

    async def both() -> tuple[list, list, dict, str | None]:
        # One event loop for every read: the session's connection belongs to the loop it opened on.
        metrics = await _metrics(session, ub, names)
        rows = [
            ts.term_row(metric_plain(m, sectors.get(str(m.symbol).replace("/", "."))), today) for m in metrics
        ]
        rows.sort(key=lambda r: r["symbol"])
        short = ts.shortlist(rows)
        if args.term_only or not short:
            return rows, short, {}, None
        by_name = {r["symbol"]: r for r in rows}
        chains, problem = await _chains(session, ub, {s: by_name[s] for s in short})
        return rows, short, chains, problem

    try:
        rows, short, chains, problem = asyncio.run(both())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}))
        return 1
    doc = {
        "taken_at": started.isoformat(),
        "session": today.isoformat(),
        "complete": problem is None and len(rows) == len(names),
        "problem": problem,
        "rule": ts.RULE,
        "order": ts.HYPOTHESIS,
        "universe": len(names),
        "read": len(rows),
        "passing": sum(r["passes"] for r in rows),
        "rows": rows,
        "shortlist": short,
        "chains": chains,
    }
    path = out_path(today, bool(args.limit))
    ub._write_json(path, doc)
    summary = {
        "ok": True,
        "written": str(path),
        "universe": len(names),
        "read": len(rows),
        "passing": doc["passing"],
        "shortlist": len(short),
        "chains": sum(1 for c in chains.values() if not c.get("error")),
        "problem": problem,
    }
    if args.show:
        from cherrypick.manage import rules as _rules

        rules = _rules.effective(None)[0]
        built = ts.build_all(doc, today, rules)
        summary["structures"] = [
            {
                k: r.get(k)
                for k in ("underlying", "kind", "label", "ratio", "debit_mid", "debit_natural", "theta_day")
            }
            for r in built["rows"]
        ]
        summary["dropped"] = built["dropped"]
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
