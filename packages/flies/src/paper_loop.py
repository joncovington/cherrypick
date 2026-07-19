"""Paper session driver — fetch a snapshot, run every arm, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything it
decides is decided by `engine.py`; this layer just supplies snapshots and persists what came back.
That split is what makes the strategy testable, and it is also the suite guardrail: no network, no MCP,
and no model call anywhere on a decision path.

Typical use is one scheduled `--once` per interval during RTH plus a `--settle` after the close, which
is how the orchestrator drives it (by subprocess, never by import). `--interval` runs the same loop
in-process for a manual session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import book as bookmod  # noqa: E402
import cli as climod  # noqa: E402
import db as dbmod  # noqa: E402
import eod as eodmod  # noqa: E402
import provider  # noqa: E402

# Regular trading hours, ET, as minutes of day. The engine's own entry windows sit inside this; the
# session gate exists so an out-of-hours run is a clean no-op rather than an iteration against a
# frozen cache full of yesterday's quotes.
RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60


def stream_cache_path(config: dict) -> str:
    """Where MEIC's streamer keeps its cache. Config first, then the managed home, and `~` expands —
    portable paths only, no machine-specific absolutes anywhere in the suite."""
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "meic", "stream_cache.db")


def _log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def run_once(config: dict, conn, *, cache_path: str, when=None, force: bool = False) -> dict:
    """One iteration across every configured symbol and every enabled arm."""
    when = when or provider.now_et()
    now_min = provider.minute_of_day(when)
    if not force and not in_session(now_min):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}

    arms = climod.enabled_arms(config)
    results = []
    for symbol in config.get("symbols", ["SPX"]):
        snapshot = provider.build_snapshot(
            cache_path, symbol, when=when,
            max_quote_age_seconds=config.get("defaults", {}).get(
                "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS),
        )
        if not snapshot.get("ok"):
            # Not an error. A streamer still warming up, or a symbol with no fresh quotes, is an
            # ordinary condition — log it so a barren session is explicable afterwards.
            _log(f"{symbol}: no snapshot ({snapshot['reason']})")
            results.append({"symbol": symbol, "ok": False, "reason": snapshot["reason"]})
            continue

        stats = snapshot["quote_stats"]
        _log(f"{symbol}: spot {snapshot['underlying_price']:.2f} dte {snapshot['dte']} "
             f"quotes {stats['fresh']} fresh / {stats['rejected']} rejected")
        for arm in arms:
            outcome = bookmod.process_snapshot(snapshot, config, conn, arm)
            for action in outcome["actions"]:
                if action["action"] not in ("entry_skipped", "completion_skipped"):
                    _log(f"  [{arm}] {action}")
            results.append({"symbol": symbol, "arm": arm, "ok": True, **outcome})
    return {"ok": True, "iterations": len(results), "results": results}


def run_settle(config: dict, conn, *, cache_path: str, when=None,
               price: float | None = None) -> dict:
    """Settle every book for the session at the settlement price.

    Caveat worth knowing when reading the results: `price` defaults to the last streamed trade, which
    approximates but is not identical to the official settlement print. For 0DTE SPX that difference is
    usually small, and it is systematic rather than random — but a position centred within a point of
    spot can settle on the wrong side of its centre because of it. Pass `--price` with the official
    print for a book that matters.
    """
    when = when or provider.now_et()
    trade_date = when.date().isoformat()
    out = []
    for symbol in config.get("symbols", ["SPX"]):
        settlement = price if price is not None else provider.read_spot(cache_path, symbol)
        if settlement is None:
            _log(f"{symbol}: cannot settle — no price available")
            out.append({"symbol": symbol, "ok": False, "reason": "no_settlement_price"})
            continue
        source = "explicit" if price is not None else "last_trade"
        for arm in climod.enabled_arms(config):
            result = bookmod.settle_book(conn, trade_date, arm, symbol, settlement, config)
            _log(f"{symbol} [{arm}] settled at {settlement:.2f} ({source}): "
                 f"P&L {result['pnl']:+.2f}, stats {result['stats']}")
            out.append({"symbol": symbol, "arm": arm, "ok": True,
                        "settlement_source": source, **result})

    # Written here rather than on a separate schedule so the reports can never describe a session that
    # hasn't settled yet. The orchestrator's digest and insight pick them up by filename alone.
    reports = eodmod.write_reports(conn, trade_date)
    _log(f"wrote {reports['paper_eod']} and {reports['eod_analysis']}")
    return {"ok": True, "settled": len(out), "results": out, "reports": reports}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """Health view for the orchestrator: is the upstream cache there, and what has this module done
    today? Deliberately file-only — no broker, no network — so it stays safe on a watchdog path."""
    when = provider.now_et()
    today = when.date().isoformat()
    books = [dict(r) for r in conn.execute(
        "SELECT * FROM fly_books WHERE trade_date = ?", (today,)).fetchall()]
    positions = conn.execute(
        "SELECT COUNT(*) FROM fly_positions WHERE trade_date = ?", (today,)).fetchone()[0]
    return {
        "ok": True,
        "date": today,
        "in_session": in_session(provider.minute_of_day(when)),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        "books": books,
        "positions_today": positions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-flies paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache", help="override MEIC's stream cache path")
    ap.add_argument("--once", action="store_true", help="run a single iteration")
    ap.add_argument("--interval", type=int, metavar="SECONDS",
                    help="run continuously until the close")
    ap.add_argument("--settle", action="store_true", help="cash-settle today's books")
    ap.add_argument("--price", type=float, help="explicit settlement price (see --settle)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--eod-reports", action="store_true",
                    help="rewrite paper-eod / eod-analysis for a day without re-settling")
    ap.add_argument("--date", help="with --eod-reports, the day (YYYY-MM-DD); default today")
    ap.add_argument("--force", action="store_true", help="ignore the RTH session gate")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    conn = dbmod.connect(args.db)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if args.eod_reports:
        day = args.date or provider.now_et().date().isoformat()
        print(json.dumps(eodmod.write_reports(conn, day), indent=2, default=str))
        return 0
    if args.settle:
        print(json.dumps(run_settle(config, conn, cache_path=cache_path, price=args.price),
                         indent=2, default=str))
        return 0
    if args.interval:
        _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
        while args.force or in_session(provider.minute_of_day(provider.now_et())):
            run_once(config, conn, cache_path=cache_path, force=args.force)
            time.sleep(args.interval)
        _log("session closed")
        return 0
    if args.once:
        print(json.dumps(run_once(config, conn, cache_path=cache_path, force=args.force),
                         indent=2, default=str))
        return 0

    ap.error("choose one of --once, --interval, --settle, --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
