"""Record what each LIVE order was asked to do, what the market looked like while it worked, and
where everything stood when it filled -- the measurements `fill_model.py` turns into paper rules.

Three jobs over two tables (`fly_live_orders`, `fly_order_path`, see db.py):

- **On the live loop's path** (`placed`, `observe`, `resolved`, `filled`), called by `live_loop.py`
  at placement, on every tick and watcher cycle while an order works, and when it fills or dies.
  Telemetry: every call site goes through `live_loop._telemetry`, so a failure here never costs a
  trade.
- **`rebuild`** re-derives one order's at-fill measures from what is stored, so a change to a
  measure's definition is a re-run, not a lost history.
- **`backfill`** reconstructs rows for orders placed before this existed (2026-07-30..10-02) from the
  ledger, the decision journal, the broker's transactions (GET-only) and the gex recorder's spot
  trail. It recovers broker fill times and prices and every SPOT distance, and the spot path of each
  completion order while it worked (`fly_order_path` rows with `source = 'trail'` and no quotes). It
  cannot recover a price gap -- the stream cache keeps no quote history -- so those stay NULL, never
  estimated. Dry-run by default.

The broker's fill time and price come from the order's per-leg fills (`fills` on the order status,
from `cherrypick.core.broker`) on the live path and from the transactions on backfill. Without
either a row records the time WE noticed the fill and says so (`fill_time_source = 'noticed'`), and
`limit_price` is never passed off as a fill price.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.flies import clock, engine, fill_model, live_orders
from cherrypick.flies import db as dbmod

ET = ZoneInfo("America/New_York")
COMPLETION_CUTOFF = "15:30"  # the live block's `completion_cutoff` throughout the backfilled era
# How far a spot reading may sit from the moment it describes. The trail records every ~15s and the
# watcher looks every ~10s, so a minute is several missed reads; past that, NULL beats a stale spot.
MAX_SPOT_AGE_SECONDS = 60.0
_TRADE = "Trade"


# --------------------------------------------------------------------------- live path
def _quote_at(snapshot: dict):
    return lambda side, strike: engine.quote(snapshot, side, strike)


def _geometry(pos: dict) -> dict:
    return {
        "trade_date": pos["trade_date"],
        "position_id": pos["position_id"],
        "side": pos["side"],
        "center": pos["center"],
        "wing_width": pos["wing_width"],
        "quantity": pos.get("quantity") or 1,
    }


def placed(
    conn,
    pos: dict,
    *,
    leg: str,
    order_id,
    limit_price: float,
    snapshot: dict,
    mid_at_submit: float | None = None,
) -> None:
    """The placement row. `mid_at_submit` is the caller's when it priced off fresher quotes than
    the snapshot (the live entry's REST re-price); otherwise the snapshot's own spread mid."""
    if mid_at_submit is None and snapshot.get("ok"):
        q = fill_model.quotes_for(leg, pos["side"], pos["center"], pos["wing_width"], _quote_at(snapshot))
        prices = fill_model.spread_prices(leg, **q)
        mid_at_submit = prices["mid"] if prices else None
    dbmod.save_live_order(
        conn,
        {
            "order_id": str(order_id),
            **_geometry(pos),
            "leg": leg,
            "limit_price": limit_price,
            "mid_at_submit": mid_at_submit,
            "spot_at_submit": snapshot.get("underlying_price"),
            "placed_at": clock.now_iso(),
            "outcome": "working",
            "source": "live",
        },
    )


def observe(conn, snapshot: dict, pos: dict, *, leg: str, order_id, limit_price, source: str) -> bool:
    """One `fly_order_path` row for a working order off this snapshot. False on a refused snapshot
    (nothing to observe -- a gap in the path is the honest record of that)."""
    if not snapshot.get("ok") or not order_id:
        return False
    q = fill_model.quotes_for(leg, pos["side"], pos["center"], pos["wing_width"], _quote_at(snapshot))
    dbmod.record_order_path(
        conn,
        observed_at=clock.now_iso(),
        trade_date=pos["trade_date"],
        position_id=pos["position_id"],
        order_id=str(order_id),
        leg=leg,
        source=source,
        limit_price=limit_price,
        spot=snapshot.get("underlying_price"),
        **q,
    )
    return True


def _ensure(conn, pos: dict, *, leg: str, order_id, limit_price) -> None:
    """A minimal row for an order placed before its placement could be recorded (the orders
    working across the deploy), so its resolution has somewhere to land."""
    if dbmod.live_order(conn, order_id) is None:
        dbmod.save_live_order(
            conn,
            {
                "order_id": str(order_id),
                **_geometry(pos),
                "leg": leg,
                "limit_price": limit_price,
                "source": "live",
            },
        )


def resolved(conn, pos: dict, *, leg: str, order_id, outcome: str, limit_price=None) -> None:
    """An order that died unfilled: cancelled at the cutoff, replaced, rejected, expired."""
    _ensure(conn, pos, leg=leg, order_id=order_id, limit_price=limit_price)
    dbmod.save_live_order(
        conn, {"order_id": str(order_id), "outcome": outcome, "resolved_at": clock.now_iso()}
    )


def filled(conn, pos: dict, *, leg: str, order_id, status: dict | None, limit_price) -> dict:
    """An order the broker reports filled: its fill time and price from the per-leg fills when the
    status carries them, then every at-fill measure. Returns the measures written."""
    fills = (status or {}).get("fills")
    broker_time = fill_model.latest_fill_time(fills)
    net = fill_model.net_fill_price(fills, pos.get("quantity") or 1)
    price = None if net is None else (net if leg == fill_model.COMPLETION else -net)
    _ensure(conn, pos, leg=leg, order_id=order_id, limit_price=limit_price)
    dbmod.save_live_order(
        conn,
        {
            "order_id": str(order_id),
            "outcome": "filled",
            "resolved_at": clock.now_iso(),
            "broker_filled_at": broker_time,
            "broker_fill_price": price,
            "fill_time_source": "broker_order" if broker_time else "noticed",
        },
    )
    return rebuild(conn, order_id)


# --------------------------------------------------------------------------- measures
def _position_row(conn, position_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (position_id,)).fetchone()
    return dict(row) if row is not None else None


def measures(order: dict, position: dict | None, path: list[dict], trail=None) -> dict:
    """Every at-fill measure for one FILLED order, from its stored path and, where the path has no
    spot near the fill, the spot trail (`trail(when_iso) -> spot | None`). Pure given its inputs.
    The fill moment is the broker's when known, else when we noticed."""
    when = order.get("broker_filled_at") or order.get("resolved_at")
    quoted = [r for r in path if r.get("buy_bid") is not None]
    observation = fill_model.observation_at(quoted, when, max_age_seconds=MAX_SPOT_AGE_SECONDS)
    spot_obs = fill_model.observation_at(
        [r for r in path if r.get("spot") is not None], when, max_age_seconds=MAX_SPOT_AGE_SECONDS
    )
    spot, spot_source = None, None
    if spot_obs is not None and spot_obs.get("source") != "trail":
        spot, spot_source = spot_obs["spot"], "path"
    elif trail is not None and when is not None:
        spot = trail(when)
        spot_source = "trail" if spot is not None else None
    elif spot_obs is not None:
        spot, spot_source = spot_obs["spot"], "trail"
    straddle = None
    if position is not None:
        straddle = fill_model.straddle_points(
            position.get("entry_vol_value"), position.get("underlying_at_entry")
        )
    return fill_model.fill_measures(
        order,
        side=order["side"],
        center=order["center"],
        width=order["wing_width"],
        observation=observation,
        spot=spot,
        spot_source=spot_source,
        straddle=straddle,
    )


def rebuild(conn, order_id, *, trail=None) -> dict:
    """Re-derive and store one filled order's at-fill measures. {} for an order not filled."""
    order = dbmod.live_order(conn, order_id)
    if order is None or order.get("outcome") != "filled":
        return {}
    out = measures(order, _position_row(conn, order["position_id"]), dbmod.order_path(conn, order_id), trail)
    dbmod.save_live_order(conn, {"order_id": str(order_id), **out})
    return out


# --------------------------------------------------------------------------- backfill
def transaction_fills(transactions: list[dict]) -> dict[str, dict]:
    """Per broker order id: the last execution time and the net cash per share (+ received,
    − paid), from the Trade transactions. `value` is the broker's signed cash, the same field
    `fee_reconcile` reconciles `net` from, so the two can never disagree about a fill."""
    out: dict[str, dict] = {}
    for t in transactions:
        if t.get("transaction_type") != _TRADE or t.get("order_id") is None:
            continue
        rec = out.setdefault(str(t["order_id"]), {"executed_at": None, "cash": 0.0})
        try:
            rec["cash"] += float(t.get("value") or 0.0)
        except (TypeError, ValueError):
            pass
        when = t.get("executed_at")
        if when and (
            rec["executed_at"] is None or fill_model.parse_ts(when) > fill_model.parse_ts(rec["executed_at"])
        ):
            rec["executed_at"] = str(when)
    return out


def _et_iso(when: str | None) -> str | None:
    """A timestamp re-stamped in ET, the ledger's own convention (the broker's are UTC)."""
    if when is None:
        return None
    return fill_model.parse_ts(when).astimezone(ET).isoformat(timespec="seconds")


def _journal_time(conn, position_id: str, reason: str) -> str | None:
    row = conn.execute(
        "SELECT first_seen FROM fly_decisions WHERE position_id = ? AND mode = 'completion' AND reason = ? "
        "ORDER BY id LIMIT 1",
        (position_id, reason),
    ).fetchone()
    return row["first_seen"] if row is not None else None


def _resting_limit(conn, position_id: str) -> float | None:
    """The working completion limit the live marks recorded (2026-09-18 onward), floored to the tick
    exactly as the order was submitted; None before marks existed."""
    try:
        row = conn.execute(
            "SELECT resting_limit FROM fly_live_marks WHERE position_id = ? AND resting_limit IS NOT NULL "
            "ORDER BY iteration_ts LIMIT 1",
            (position_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 -- a ledger from before the marks table
        return None
    return live_orders.tick_floor(row["resting_limit"]) if row is not None else None


def backfill_rows(conn, *, fills: dict[str, dict], trail_path, since: str | None = None) -> dict:
    """The rows a backfill would write: {"orders": [...], "paths": {order_id: [...]}}. Pure over the
    ledger (read-only), the transactions (`fills`, from `transaction_fills`) and `trail_path(start,
    end) -> [(iso, spot)]`. Skips an order that already has a row written live -- the live record is
    always the better one."""
    clause, params = ("AND trade_date >= ?", [since]) if since else ("", [])
    positions = [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM fly_positions WHERE entry_order_id IS NOT NULL {clause} ORDER BY entry_time",
            params,
        ).fetchall()
    ]
    orders: list[dict] = []
    paths: dict[str, list[dict]] = {}
    for pos in positions:
        geo = _geometry(pos)
        entry_id = str(pos["entry_order_id"])
        entry_fill = fills.get(entry_id)
        existing = dbmod.live_order(conn, entry_id)
        if existing is None or existing.get("source") == "backfill":
            entry_filled = pos.get("entry_fill_status") == "filled"
            orders.append(
                {
                    "order_id": entry_id,
                    **geo,
                    "leg": fill_model.ENTRY,
                    "limit_price": pos.get("credit"),
                    "mid_at_submit": pos.get("entry_mid_at_submit"),
                    "spot_at_submit": pos.get("underlying_at_entry"),
                    "placed_at": pos.get("entry_time"),
                    "outcome": "filled" if entry_filled else (pos.get("entry_fill_status") or "unknown"),
                    "broker_filled_at": _et_iso(entry_fill["executed_at"]) if entry_fill else None,
                    "broker_fill_price": round(entry_fill["cash"] / (100 * geo["quantity"]), 4)
                    if entry_fill
                    else None,
                    "fill_time_source": "transactions" if entry_fill else None,
                    "source": "backfill",
                }
            )
        if pos.get("entry_fill_status") != "filled":
            continue

        # The completion. A filled one kept its broker id; an unfilled one's id was cleared when it
        # died, so it is keyed by the external identifier the order carried at the broker
        # (`<position_id>-completion`, live_loop.place_resting_completion) -- a real name for it,
        # never a fabricated number.
        completion_id = str(pos.get("completion_order_id") or f"{pos['position_id']}-completion")
        existing = dbmod.live_order(conn, completion_id)
        if existing is not None and existing.get("source") != "backfill":
            continue
        comp_fill = fills.get(completion_id)
        comp_filled = pos.get("completion_fill_status") == "filled"
        cutoff_at = _journal_time(conn, pos["position_id"], "cutoff_cancelled")
        if comp_filled:
            outcome = "filled"
        elif cutoff_at:
            outcome = "cutoff_cancelled"
        elif pos.get("status") == "settled":
            outcome = "unfilled"
        else:
            continue  # still working (today's session): the live path will record it
        placed_at = (
            _journal_time(conn, pos["position_id"], "placed")
            or (orders[-1]["broker_filled_at"] if orders and orders[-1]["order_id"] == entry_id else None)
            or pos.get("entry_time")
        )
        broker_filled_at = _et_iso(comp_fill["executed_at"]) if comp_fill else None
        limit = pos.get("debit") if comp_filled else _resting_limit(conn, pos["position_id"])
        order = {
            "order_id": completion_id,
            **geo,
            "leg": fill_model.COMPLETION,
            "limit_price": limit,
            "placed_at": placed_at,
            "outcome": outcome,
            "resolved_at": pos.get("completed_at") if comp_filled else cutoff_at,
            "broker_filled_at": broker_filled_at,
            "broker_fill_price": round(-comp_fill["cash"] / (100 * geo["quantity"]), 4)
            if comp_fill
            else None,
            "fill_time_source": "transactions" if comp_fill else ("noticed" if comp_filled else None),
            "source": "backfill",
        }
        orders.append(order)
        end = (
            broker_filled_at
            or (pos.get("completed_at") if comp_filled else None)
            or cutoff_at
            or _cutoff_iso(pos["trade_date"])
        )
        if placed_at:
            paths[completion_id] = [
                {
                    "observed_at": ts,
                    "trade_date": pos["trade_date"],
                    "position_id": pos["position_id"],
                    "order_id": completion_id,
                    "leg": fill_model.COMPLETION,
                    "source": "trail",
                    "limit_price": limit,
                    "buy_bid": None,
                    "buy_ask": None,
                    "sell_bid": None,
                    "sell_ask": None,
                    "spot": spot,
                }
                for ts, spot in trail_path(placed_at, end)
            ]
    for order in orders:
        if order["outcome"] == "filled":
            position = _position_row(conn, order["position_id"])
            path = paths.get(order["order_id"], [])
            order.update(measures(order, position, path, _point_trail(trail_path)))
    return {"orders": orders, "paths": paths}


def write_backfill(conn, plan: dict) -> dict:
    """Write what `backfill_rows` planned. A re-run replaces earlier backfilled path rows rather
    than appending beside them."""
    for order in plan["orders"]:
        dbmod.save_live_order(conn, order)
    for order_id, rows in plan["paths"].items():
        conn.execute("DELETE FROM fly_order_path WHERE order_id = ? AND source = 'trail'", (order_id,))
        conn.commit()
        for r in rows:
            dbmod.record_order_path(conn, **r)
    return {"orders": len(plan["orders"]), "path_rows": sum(len(v) for v in plan["paths"].values())}


# --------------------------------------------------------------------------- the spot trail
def trail_reader(spot_conn, symbol: str):
    """`trail_path(start_iso, end_iso) -> [(iso_et, spot)]` over the gex recorder's spot trail
    (`gex_spot_history`, epoch seconds), read-only. An unreadable trail reads as empty."""

    def trail_path(start: str, end: str) -> list[tuple[str, float]]:
        if spot_conn is None:
            return []
        lo = fill_model.parse_ts(start).timestamp() - MAX_SPOT_AGE_SECONDS
        hi = fill_model.parse_ts(end).timestamp()
        try:
            rows = spot_conn.execute(
                "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND ts BETWEEN ? AND ? ORDER BY ts",
                (symbol, lo, hi),
            ).fetchall()
        except Exception:  # noqa: BLE001 -- an unreadable trail is an unmeasured row, not a crash
            return []
        return [(datetime.fromtimestamp(ts, ET).isoformat(timespec="seconds"), spot) for ts, spot in rows]

    return trail_path


def _point_trail(trail_path):
    """The last trail spot at or before a moment, within `MAX_SPOT_AGE_SECONDS`."""

    def at(when: str) -> float | None:
        t = fill_model.parse_ts(when)
        start = (t - timedelta(seconds=MAX_SPOT_AGE_SECONDS)).isoformat()
        rows = [(ts, s) for ts, s in trail_path(start, when) if fill_model.parse_ts(ts) <= t]
        return rows[-1][1] if rows else None

    return at


def _cutoff_iso(trade_date: str) -> str:
    hh, mm = (int(x) for x in COMPLETION_CUTOFF.split(":"))
    d = date.fromisoformat(trade_date)
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- command line
async def _fetch_transactions(symbols: list[str], dates: list[str]) -> list[dict]:
    """Every symbol's transactions over `dates`, in ONE event loop: the cached broker session's async client
    binds to the loop that first drives it, so a second `asyncio.run` fails with "Event loop is
    closed" (the same trap `cherrypick.core.execution.Broker` documents)."""
    from cherrypick.core import broker as _broker

    from cherrypick.flies import credentials as creds

    session = creds.get_session()
    account = await _broker.resolve_account(session, creds.designated_account())
    out: list[dict] = []
    # One trading day per call, as fee_reconcile fetches: a single range call came back capped at
    # one 250-row page and silently dropped every earlier day's fills.
    for symbol in symbols:
        for day in sorted(set(dates)):
            d = date.fromisoformat(day)
            out.extend(
                await _broker.transaction_history(account, session, start_date=d, underlying_symbol=symbol)
            )
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m cherrypick.flies.fill_facts",
        description="Backfill or rebuild live fill-realism rows (fly_live_orders / fly_order_path).",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backfill", help="reconstruct rows for orders placed before recording began")
    b.add_argument("--since", default="2026-07-30")
    b.add_argument(
        "--symbol", action="append", help="underlying(s) to fetch transactions for (default: every traded)"
    )
    b.add_argument(
        "--no-broker", action="store_true", help="skip the transaction fetch (no broker times/prices)"
    )
    b.add_argument("--write", action="store_true", help="write the rows (default: dry run)")
    r = sub.add_parser("rebuild", help="re-derive at-fill measures for filled orders from stored data")
    r.add_argument("--date", help="one trade_date (default: every filled order)")
    for p in (b, r):
        p.add_argument("--db", help="ledger path (default: the LIVE ledger)")
        p.add_argument("--gex", help="gex history db (default: the recorder's)")
    return ap


def _open_trail(path: str | None):
    from cherrypick.core import db as _core_db
    from cherrypick.core import home as _home

    try:
        return _core_db.connect_ro(path or (_home.data_dir("gex") / "gex_history.db"))
    except Exception:  # noqa: BLE001 -- no trail is reported, not fatal
        return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conn = dbmod.connect(args.db or dbmod.live_db_path())
    spot_conn = _open_trail(args.gex)
    if args.command == "rebuild":
        clause, params = ("AND trade_date = ?", [args.date]) if args.date else ("", [])
        ids = [
            r[0]
            for r in conn.execute(
                f"SELECT order_id FROM fly_live_orders WHERE outcome = 'filled' {clause}", params
            )
        ]
        by_symbol = {
            r["position_id"]: r["symbol"]
            for r in conn.execute("SELECT position_id, symbol FROM fly_positions")
        }
        done = 0
        for oid in ids:
            order = dbmod.live_order(conn, oid)
            symbol = by_symbol.get(order["position_id"], "SPX")
            rebuild(conn, oid, trail=_point_trail(trail_reader(spot_conn, symbol)))
            done += 1
        print(json.dumps({"ok": True, "rebuilt": done, "trail": spot_conn is not None}))
        return 0

    symbols = args.symbol or [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM fly_positions WHERE entry_order_id IS NOT NULL AND trade_date >= ?",
            (args.since,),
        )
    ]
    dates = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trade_date FROM fly_positions WHERE entry_order_id IS NOT NULL AND trade_date >= ?",
            (args.since,),
        )
    ]
    txns: list[dict] = []
    if not args.no_broker and dates:
        txns = asyncio.run(_fetch_transactions(symbols, dates))
    fills = transaction_fills(txns)
    summary = {
        "ok": True,
        "write": args.write,
        "since": args.since,
        "transactions": len(txns),
        "by_symbol": {},
    }
    for symbol in symbols:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT position_id FROM fly_positions WHERE symbol = ? AND entry_order_id IS NOT NULL "
                "AND trade_date >= ?",
                (symbol, args.since),
            )
        ]
        plan = backfill_rows(conn, fills=fills, trail_path=trail_reader(spot_conn, symbol), since=args.since)
        plan["orders"] = [o for o in plan["orders"] if o["position_id"] in set(ids)]
        plan["paths"] = {k: v for k, v in plan["paths"].items() if v and v[0]["position_id"] in set(ids)}
        filled = [o for o in plan["orders"] if o["outcome"] == "filled"]
        summary["by_symbol"][symbol] = {
            "orders": len(plan["orders"]),
            "filled": len(filled),
            "with_broker_time": sum(1 for o in filled if o.get("broker_filled_at")),
            "with_fill_spot": sum(1 for o in filled if o.get("fill_spot") is not None),
            "path_rows": sum(len(v) for v in plan["paths"].values()),
        }
        if args.write:
            summary["by_symbol"][symbol]["written"] = write_backfill(conn, plan)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
