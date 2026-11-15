"""Backtest automations (ensemble arms today) through the engine paper runs, over a stored history.

    python packages/backtest/scripts/automation_backtest.py run --arms iron-fly-1000,long-put-1030 \\
        --symbol SPX --name my-run --from 2026-10-13 --to 2026-11-13 [--source recorded-0dte]

The replay is `cherrypick.ensemble.driver` (the engine, the snapshot builder and paper's own arm
stacking), so a backtest's entries, exits, gates and fills are paper's. This script chooses the data and
writes the result:

- **The store** comes from the source registry (`cherrypick.core.sources`): a same-day-expiry chain for
  the symbol over the dates, the source that covers them, or the one named by `--source`. A vendor
  source is refused unless the licence is active (`licensing.status`): new work never reads the
  vendor's data after its subscription ends.
- **Daily bars** for a daily signal come from the recorder's index sessions (`index-sessions`).
- **The result** is descriptive (an explore run: no trial, no verdict) and goes where its inputs allow:
  under `results/automations/` when it read the vendor's store (licensed, purged with it), beside it
  otherwise (`paths.automations_dir`). Never into the repo.

Version 1 covers same-session expiries, the stores in the recorder's schema.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.core import home, jsonio
from cherrypick.core import sources as src
from cherrypick.core.clock import ET
from cherrypick.core.sources import inventory as src_inventory
from cherrypick.ensemble import config as ens_config
from cherrypick.ensemble import driver, events, history

from cherrypick.backtest import licensing, paths, settings


class Refused(Exception):
    pass


def store_path(source: src.Source, settings_doc: dict) -> Path:
    """The file a recorder-schema source reads: our recorder's under the data root, the vendor's in its
    licensed directory."""
    if source.probe.backend == "store_cache":
        return paths.vendor_dir(settings_doc) / source.probe.where
    return home.data_dir() / source.probe.where


def choose_source(
    symbol: str, start: str, end: str, named: str | None, *, licensed_ok: bool, inv: dict
) -> src.Source:
    """The 0DTE chain source for the run: `named`, or the registry's best cover. A licensed source only
    while the licence allows new work; one the registry cannot cover is refused with its reason."""
    if named:
        s = src.by_name(named)
        if s.licensed and not licensed_ok:
            raise Refused(f"{named} is the vendor's and the licence does not allow new work")
        if "0dte" not in s.expiries or s.probe.backend not in ("sqlite", "store_cache"):
            raise Refused(f"{named} is not a same-day chain store in the recorder's schema")
        return s
    cov = src.coverage(inv, "chain", symbol, start, end, expiry="0dte", licensed=licensed_ok, min_density=0.0)
    if not cov["best"]:
        why = "; ".join(f"{x['source']}: {x['why']}" for x in cov["sources"]) or "no source holds it"
        raise Refused(f"no store covers {symbol} 0DTE chains {start}..{end} ({why})")
    return src.by_name(cov["best"])


def index_daily(symbol: str) -> list[dict]:
    """The recorder's daily OHLC sessions for `symbol` (the `index-sessions` source), oldest first."""
    db = home.data_dir("gex") / "gex_history.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT trade_date, day_open, day_high, day_low, day_close FROM daily_sessions WHERE symbol = ? "
            "ORDER BY trade_date",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return [{"session": d, "open": o, "high": h, "low": lo, "close": c} for d, o, h, lo, c in rows]


def summarise(book) -> dict:
    out = {}
    for name, why in sorted(book.why.items()):
        trades = [t for t in book.trades if t["arm"] == name]
        nets = [t["net"]["verdict"] for t in trades]
        out[name] = {
            "sessions": sum(why.values()),
            "trades": len(trades),
            "net": round(sum(nets), 2),
            "mean_net": round(sum(nets) / len(nets), 2) if nets else None,
            "reasons": dict(why),
        }
    return out


def cmd_run(args) -> int:
    s = settings.load(None)
    cfg = ens_config.load_config()
    every = ens_config.load_arms(cfg)
    names = [n.strip() for n in args.arms.split(",") if n.strip()]
    missing = [n for n in names if n not in every]
    if missing:
        print(json.dumps({"ok": False, "error": f"no arm {missing[0]!r}"}))
        return 1
    chosen = {n: {**every[n], "enabled": True} for n in names}
    symbol = args.symbol.upper()
    start, end = args.start, args.end
    try:
        source = choose_source(
            symbol,
            start,
            end,
            args.source,
            licensed_ok=licensing.status(s)["active"],
            inv=src_inventory.load(),
        )
    except Refused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    store = store_path(source, s)
    h = history.StoreHistory(store, symbol, daily=index_daily(symbol))
    sessions = [d for d in h.sessions if start <= d <= end]
    calendar = events.load_calendar((cfg.get("events") or {}).get("path"))
    book = driver.run(chosen, h, sessions, events=calendar, costs=cfg)
    result = {
        "name": args.name,
        "kind": "explore",
        "symbol": symbol,
        "arms": chosen,
        "source": source.name,
        "store": store.relative_to(home.data_dir()).as_posix()
        if store.is_relative_to(home.data_dir())
        else store.name,
        "licensed": source.licensed,
        "sessions": [sessions[0], sessions[-1]] if sessions else None,
        "session_count": len(sessions),
        "summary": summarise(book),
        "trades": book.trades,
        "still_open": len(book.open),
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
    }
    out = paths.automations_dir(source.licensed) / f"{args.name}.json"
    jsonio.write_json_atomic(out, result)
    print(json.dumps({"ok": True, "path": str(out), "source": source.name, "trades": len(book.trades)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="backtest arms over a stored history and write the result")
    rn.add_argument("--arms", required=True, help="comma-separated arm names (shipped or this machine's)")
    rn.add_argument("--symbol", required=True)
    rn.add_argument("--name", required=True, help="the result's file name")
    rn.add_argument("--source", default=None, help="a source registry name; default: the best that covers")
    rn.add_argument("--from", dest="start", required=True)
    rn.add_argument("--to", dest="end", required=True)
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
