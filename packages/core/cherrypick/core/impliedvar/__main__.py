"""`python -m cherrypick.core.impliedvar` — read model-free implied variance off the stream cache.

Commands:
    term   --symbol SPX --root SPXW --expiration YYYY-MM-DD
           One expiration's single-term result (variance, vol, forward, K0, wing completeness).
    check  --index {VIX,VIX9D}
           The same arithmetic interpolated to the index's constant maturity from the two cached
           expirations that bracket it, against the index's own print in the cache.

    declare --index {VIX,VIX9D} [--today YYYY-MM-DD] [--clear]
           Write (or with --clear, remove) state/stream_requests/impliedvar.json asking the streamer
           for the two SPXW expirations that bracket the index's maturity through today's session,
           with their whole strip at Quote and Greeks. Declare before the open (an expiration added
           mid-session is served on the producer's next window pass, no restart) and clear after
           the check; the leg query selects nothing once its dates have passed, but the request file
           stays until cleared.

Shared options: --db (default: the shared stream cache), --rate (decimal, default 0.0 — the suite
carries no Treasury curve; the effect is second order and the output states it), --max-age (quote
age limit in seconds against --as-of, default 10), --as-of (epoch seconds; default now).

`check` is only a check when both strips are complete — a strip cut off by the cache's strike window
reads low by construction, so it reports `truncated` rather than a verdict. It reports
`not_bracketed` when the cache holds no pair of expirations around the target, which is the normal
state of a cache that only declares the expirations its modules trade.

After the close the streamer replays its last quotes on reconnect, so `updated_at` stops meaning
"now". Pass --as-of at the index's last print and a --max-age wide enough to admit the replay, and
read the result as a close snapshot. Never treat it as a live one.

Exit 0 when the check agrees within --tolerance (default 0.5 vol points — Cboe's own index-level
filtering threshold for VIX), 1 otherwise, 2 when the cache cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import db as _db
from cherrypick.core import home as _home
from cherrypick.core import impliedvar as _iv
from cherrypick.core import streamrequests as _requests
from cherrypick.core.clock import ET as _ET

# The request file `declare` writes; a validation instrument, not a module.
REQUEST_NAME = "impliedvar"

# The constant maturity of each index this arithmetic reproduces. VIX1D is deliberately absent:
# it is computed by a different (0DTE/1DTE, time-weighted) methodology, not this one.
INDEX_DAYS = {"VIX": 30, "VIX9D": 9}


def _term(conn, args, symbol: str, root: str, expiration: str, now_ts: float) -> dict:
    strip = _iv.strip_from_cache(conn, symbol, expiration, root, now_ts=now_ts, max_age_seconds=args.max_age)
    head = {
        "expiration": expiration,
        "exercise_style": strip["exercise_style"],
        "settlement_type": strip["settlement_type"],
        "listed": strip["listed"],
        "quoted": len(strip["quotes"]),
        "missing": strip["missing"],
    }
    if strip["expires_at"] is None:
        return {**head, "ok": False, "reason": "no_expiry_time"}
    years = _iv.years_between(now_ts, strip["expires_at"])
    return {**head, **_iv.single_term(strip["quotes"], years=years, rate=args.rate)}


def _bracket(conn, symbol: str, root: str, now_ts: float, days: float) -> tuple[str | None, str | None, list]:
    """The latest expiration at or before the target and the earliest at or after it."""
    offered = []
    for (expiration,) in conn.execute(
        "SELECT DISTINCT expiration FROM stream_chain WHERE underlying_symbol = ? ORDER BY expiration",
        (symbol,),
    ):
        expires = _iv.expiry_fields(conn, symbol, expiration, root)["expires_at"]
        if expires is not None and expires > now_ts:
            offered.append((expiration, (expires - now_ts) / 86_400.0))
    near = [e for e, d in offered if d <= days]
    nxt = [e for e, d in offered if d >= days]
    return (near[-1] if near else None), (nxt[0] if nxt else None), offered


def cmd_term(conn, args, now_ts: float) -> tuple[dict, int]:
    out = _term(conn, args, args.symbol, args.root, args.expiration, now_ts)
    return {"as_of": now_ts, "rate": args.rate, **out}, 0 if out.get("ok") else 1


def cmd_check(conn, args, now_ts: float) -> tuple[dict, int]:
    days = INDEX_DAYS[args.index]
    head = {"index": args.index, "days": days, "as_of": now_ts, "rate": args.rate}
    row = conn.execute("SELECT last, event_at FROM stream_trades WHERE symbol = ?", (args.index,)).fetchone()
    published = float(row[0]) if row and row[0] is not None else None
    head["published"] = published
    head["published_at"] = float(row[1]) if row and row[1] is not None else None
    if published is None:
        return {**head, "ok": False, "reason": "no_index_print"}, 1

    near_exp, next_exp, offered = _bracket(conn, args.symbol, args.root, now_ts, days)
    if near_exp is None or next_exp is None:
        offered_days = [[e, round(d, 2)] for e, d in offered]
        return {**head, "ok": False, "reason": "not_bracketed", "offered": offered_days}, 1
    near = _term(conn, args, args.symbol, args.root, near_exp, now_ts)
    nxt = _term(conn, args, args.symbol, args.root, next_exp, now_ts)
    if near_exp == next_exp:  # an expiration exactly at the target: one term, no interpolation
        cm = {"ok": near.get("ok"), "vol": near.get("vol"), "complete": near.get("complete")}
    else:
        cm = _iv.constant_maturity(near, nxt, days=days)
    out = {**head, "near": near, "next": nxt}
    if not cm.get("ok"):
        return {**out, "ok": False, "reason": cm.get("reason", "term_unavailable")}, 1
    diff = cm["vol"] - published
    if not cm["complete"]:
        verdict = "truncated"
    else:
        verdict = "agrees" if abs(diff) <= args.tolerance else "disagrees"
    out.update({"ok": True, "computed": round(cm["vol"], 4), "diff": round(diff, 4), "verdict": verdict})
    return out, 0 if verdict == "agrees" else 1


def cmd_declare(args, path: Path) -> tuple[dict, int]:
    request = _requests.request_path(REQUEST_NAME)
    if args.clear:
        existed = request.exists()
        request.unlink(missing_ok=True)
        return {"ok": True, "cleared": str(request), "existed": existed}, 0
    today = date.fromisoformat(args.today) if args.today else datetime.now(_ET).date()
    near, nxt = _iv.bracket_dates(today, INDEX_DAYS[args.index])
    query = _iv.strip_query(args.symbol, args.root, (near, nxt))
    _requests.write_request(
        REQUEST_NAME,
        [args.symbol],
        leg_sources=[_requests.leg_source(path, query)],
        expirations={args.symbol: [near.isoformat(), nxt.isoformat()]},
        # Accurate, and never growth: this reads its declared dates' strips, never the nearest
        # window, and only quotes on its own windows. A symbol narrows only when every declarer does.
        window_events={args.symbol: ["Quote"]},
        nearest_window={args.symbol: False},
    )
    return {
        "ok": True,
        "request": str(request),
        "index": args.index,
        "session": today.isoformat(),
        "expirations": [near.isoformat(), nxt.isoformat()],
        "query": query,
    }, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.core.impliedvar", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("term", "check", "declare"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=None)
        p.add_argument("--symbol", default="SPX")
        p.add_argument("--root", default="SPXW")
        p.add_argument("--rate", type=float, default=0.0)
        p.add_argument("--max-age", type=float, default=10.0)
        p.add_argument("--as-of", type=float, default=None)
        if name == "term":
            p.add_argument("--expiration", required=True)
        else:
            p.add_argument("--index", choices=sorted(INDEX_DAYS), default="VIX9D")
        if name == "check":
            p.add_argument("--tolerance", type=float, default=0.5)
        if name == "declare":
            p.add_argument("--today", default=None)
            p.add_argument("--clear", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.db) if args.db else _home.data_dir("marketdata") / "stream_cache.db"
    if args.cmd == "declare":
        out, code = cmd_declare(args, path)
        print(json.dumps(out, indent=2))
        return code
    try:
        conn = _db.connect_ro(path)
    except Exception as exc:  # noqa: BLE001 -- an unreadable store is a reported state
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    now_ts = args.as_of if args.as_of is not None else time.time()
    try:
        out, code = (cmd_term if args.cmd == "term" else cmd_check)(conn, args, now_ts)
    finally:
        conn.close()
    print(json.dumps(out, indent=2, default=str))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
