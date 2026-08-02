"""Reconcile the flies LIVE ledger against actual broker cash flow.

Two of `fly_positions`' canonical columns are modeled, not real, on a live row: `net`/`credit`/
`debit` are corrected in real time from `order_status`'s `price` field (the order's own working/
limit price — "the closest a `PlacedOrder` gets to a real fill price without a separate
transactions-API call", per that function's own docstring, not a true realized amount), and
`fees` folds in a flat modeled assignment-fee constant (`fly.expire_fee`) rather than what the
broker actually charged overnight. A 2026-07-30 XSP session showed this drifting the reported
`pnl` by $12.02 against real broker cash flow.

This module fetches the real transactions (`cherrypick.core.broker.transaction_history`, GET-only)
for a settled session and recomputes `net`/`fees`/`gross_pnl`/`pnl`/`expiry_payoff` purely from
summed real transaction fields — never re-derived from the model. The original modeled values are
snapshotted into `modeled_*` columns first (once, non-destructively) so nothing is lost.

Matching is exact, not fuzzy: trade-side legs (entry/completion/close) are matched by `order_id`,
already stored on the position row for exactly this purpose; settlement-side legs (assignment/
exercise/expiration) are matched by OCC option symbol + trade date, built from the position's own
`side`/`center`/`wing_width`. A position with no matching broker data is left untouched and marked
`unmatched` rather than guessed at — the same honesty stance the rest of this module takes.

Deliberately split in two: `reconcile_date` is pure given already-fetched transactions (easy to
test against real transcribed order chains, same spirit as `tests/fixtures/books.json`); the async
broker fetch lives in `live_loop.py`'s `BrokerAdapter.history` (morning auto-run) or this module's
own `main()` (manual/CLI run) — neither of which this file imports, to stay broker-agnostic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import clock  # noqa: E402
import db as dbmod  # noqa: E402
import fly  # noqa: E402

# A position's fee is the sum of these fields' magnitudes across every matched transaction —
# tastytrade reports each as a signed (negative) charge or, on some rows, absent entirely.
_FEE_FIELDS = ("commission", "clearing_fees", "regulatory_fees", "proprietary_index_option_fees")

# Live v1 is legged-only (see live_orders.entry_spec's own guard) — a settled live position's
# `kind` is always one of these two shapes. bwb/iron/debit_first are paper-only research arms.
_SETTLEMENT_TRANSACTION_TYPE = "Receive Deliver"
_TRADE_TRANSACTION_TYPE = "Trade"


def _occ_symbol(root_symbol: str, expiration: str, side: str, strike: float) -> str:
    """Build the OCC option symbol tastytrade reports on a transaction — e.g. `XSP   260730P00744000`
    for a 2026-07-30 XSP 744 put. `expiration` is `trade_date` (0DTE: always expires same day)."""
    yy, mm, dd = expiration[2:4], expiration[5:7], expiration[8:10]
    call_put = "P" if side == fly.PUT else "C"
    return f"{root_symbol.ljust(6)}{yy}{mm}{dd}{call_put}{int(round(strike * 1000)):08d}"


def _position_leg_symbols(position: dict) -> list[str]:
    """The settlement-relevant OCC leg symbols for a settled live position."""
    side, center, width = position["side"], position["center"], position["wing_width"]
    long_strike = center - width if side == fly.PUT else center + width
    strikes = [center, long_strike]
    if position["kind"] == "fly":
        completing_strike = center + width if side == fly.PUT else center - width
        strikes.append(completing_strike)
    expiration = position["trade_date"]
    symbol = position["symbol"]
    return [_occ_symbol(symbol, expiration, side, strike) for strike in strikes]


def _fee_total(txn: dict) -> float:
    return sum(abs(float(txn[field])) for field in _FEE_FIELDS if txn.get(field) not in (None, ""))


def pending_reconciliation(conn, symbol: str, lookback_days: int = 5, today: str | None = None) -> list[str]:
    """Settled trade dates for `symbol` in the live DB that haven't been reconciled yet, strictly
    before today (broker settlement fees post the next business day, never same-day) and within
    `lookback_days` (so a gap of a few missed mornings still gets caught)."""
    today = today or clock.today_iso()
    cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM fly_positions "
        "WHERE symbol = ? AND status = 'settled' AND trade_date < ? AND trade_date >= ? "
        "AND broker_reconciled_at IS NULL",
        (symbol, today, cutoff),
    ).fetchall()
    return sorted(r["trade_date"] for r in rows)


def _update_book_rollup(conn, book_id: str) -> None:
    """Recompute `fly_books.pnl`/`fees` as the sum over its settled positions' (now possibly
    broker-confirmed) `pnl`/`fees` — not re-derived through `fly.book_pnl`'s model, since the
    per-position columns already hold the final numbers. `broker_reconciliation_status` is
    'reconciled' only once every settled position in the book has been; 'partial' otherwise."""
    book = conn.execute(
        "SELECT pnl, fees, modeled_pnl, modeled_fees FROM fly_books WHERE book_id = ?", (book_id,)
    ).fetchone()
    if book is None:
        return
    agg = conn.execute(
        "SELECT SUM(pnl) AS pnl, SUM(fees) AS fees, "
        "SUM(CASE WHEN broker_reconciliation_status = 'reconciled' THEN 1 ELSE 0 END) AS reconciled_n, "
        "COUNT(*) AS total_n FROM fly_positions WHERE book_id = ? AND status = 'settled'",
        (book_id,),
    ).fetchone()
    if not agg["total_n"] or not agg["reconciled_n"]:
        return
    status = "reconciled" if agg["reconciled_n"] == agg["total_n"] else "partial"
    conn.execute(
        "UPDATE fly_books SET modeled_pnl = COALESCE(modeled_pnl, ?), modeled_fees = COALESCE(modeled_fees, ?), "
        "pnl = ?, fees = ?, broker_reconciled_at = ?, broker_reconciliation_status = ? WHERE book_id = ?",
        (
            book["pnl"],
            book["fees"],
            round(agg["pnl"] or 0.0, 2),
            round(agg["fees"] or 0.0, 2),
            clock.now_iso(),
            status,
            book_id,
        ),
    )
    conn.commit()


def reconcile_date(conn, trade_date: str, symbol: str, transactions: list[dict], *, log=print) -> dict:
    """Reconcile every settled, unreconciled `symbol` position on `trade_date` against
    already-fetched broker `transactions` (see module docstring for why the fetch is separate).
    Idempotent: a position already reconciled (or already marked unmatched) is skipped."""
    positions = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM fly_positions WHERE trade_date = ? AND symbol = ? AND status = 'settled' "
            "AND broker_reconciled_at IS NULL AND broker_reconciliation_status IS NULL",
            (trade_date, symbol),
        ).fetchall()
    ]
    result = {
        "trade_date": trade_date,
        "symbol": symbol,
        "reconciled": [],
        "unmatched": [],
        "variance": [],
        "fee_variance": [],
    }
    if not positions:
        return result

    book_ids = set()
    for position in positions:
        book_ids.add(position["book_id"])
        order_ids = {
            str(position[k])
            for k in ("entry_order_id", "completion_order_id", "close_order_id")
            if position.get(k)
        }
        trade_txns = [
            t
            for t in transactions
            if t.get("transaction_type") == _TRADE_TRANSACTION_TYPE and str(t.get("order_id")) in order_ids
        ]
        leg_symbols = set(_position_leg_symbols(position))
        settlement_txns = [
            t
            for t in transactions
            if t.get("transaction_type") == _SETTLEMENT_TRANSACTION_TYPE
            and t.get("symbol") in leg_symbols
            and t.get("transaction_date") == trade_date
        ]
        if not trade_txns or not settlement_txns:
            conn.execute(
                "UPDATE fly_positions SET broker_reconciliation_status = 'unmatched' WHERE position_id = ?",
                (position["position_id"],),
            )
            conn.commit()
            result["unmatched"].append(position["position_id"])
            log(
                f"fee_reconcile: {position['position_id']} unmatched "
                f"(trade_txns={len(trade_txns)}, settlement_txns={len(settlement_txns)})"
            )
            continue

        qty = position.get("quantity") or 1
        real_net = sum(float(t["value"]) for t in trade_txns) / (100 * qty)
        real_payoff = sum(float(t["value"]) for t in settlement_txns) / (100 * qty)
        real_fees = round(sum(_fee_total(t) for t in trade_txns + settlement_txns), 2)
        real_gross = round((real_net + real_payoff) * fly.CONTRACT_MULTIPLIER * qty, 2)
        real_pnl = round(real_gross - real_fees, 2)
        modeled_pnl = position.get("pnl")

        conn.execute(
            "UPDATE fly_positions SET "
            "modeled_net = COALESCE(modeled_net, ?), modeled_fees = COALESCE(modeled_fees, ?), "
            "modeled_gross_pnl = COALESCE(modeled_gross_pnl, ?), modeled_pnl = COALESCE(modeled_pnl, ?), "
            "modeled_expiry_payoff = COALESCE(modeled_expiry_payoff, ?), "
            "net = ?, fees = ?, gross_pnl = ?, pnl = ?, expiry_payoff = ?, "
            "broker_reconciled_at = ?, broker_reconciliation_status = 'reconciled' "
            "WHERE position_id = ?",
            (
                position.get("net"),
                position.get("fees"),
                position.get("gross_pnl"),
                position.get("pnl"),
                position.get("expiry_payoff"),
                round(real_net, 4),
                real_fees,
                real_gross,
                real_pnl,
                round(real_payoff, 4),
                clock.now_iso(),
                position["position_id"],
            ),
        )
        conn.commit()
        result["reconciled"].append(position["position_id"])
        delta = round(real_pnl - (modeled_pnl or 0.0), 2)

        # Per-SYMBOL settlement-fee comparison, not just the aggregate P&L delta. The 2026-07-31
        # per-contract-vs-per-event bug surfaced only as a ~$12 aggregate delta that read as
        # ordinary slippage noise; "modeled $10.00 vs real $5.00 on ONE symbol" is unambiguous.
        # This is the check that would catch any future re-definition of the fee -- including
        # whether it starts scaling with quantity at sizes larger than the 1-2 contracts the
        # current flat-per-event model was verified against.
        modeled_per_event = fly.expire_fee(1)
        fee_variance = []
        for txn in settlement_txns:
            real_fee = round(_fee_total(txn), 2)
            if real_fee <= 0:
                continue  # an OTM expiration line carries no fee; nothing to compare
            if abs(real_fee - modeled_per_event) > 0.01:
                fee_variance.append(
                    {
                        "position_id": position["position_id"],
                        "symbol": txn.get("symbol"),
                        "quantity": txn.get("quantity"),
                        "modeled_fee": modeled_per_event,
                        "real_fee": real_fee,
                    }
                )
        for v in fee_variance:
            log(
                f"fee_reconcile FEE-MODEL WARN: {v['position_id']} {v['symbol']} "
                f"(qty {v['quantity']}) modeled ${v['modeled_fee']:.2f} vs real ${v['real_fee']:.2f} "
                f"-- the per-settlement-event fee model may no longer match the broker"
            )
        result["fee_variance"].extend(fee_variance)

        result["variance"].append(
            {
                "position_id": position["position_id"],
                "modeled_pnl": modeled_pnl,
                "broker_pnl": real_pnl,
                "delta": delta,
            }
        )
        if abs(delta) > 1.0:
            log(
                f"fee_reconcile WARN: {position['position_id']} modeled {modeled_pnl} vs broker {real_pnl} (delta {delta})"
            )

    for book_id in book_ids:
        _update_book_rollup(conn, book_id)
    return result


async def _fetch_transactions(account, session, trade_date: str, symbol: str) -> list[dict]:
    from cherrypick.core import broker as _broker

    d = date.fromisoformat(trade_date)
    return await _broker.transaction_history(account, session, start_date=d, underlying_symbol=symbol)


async def _reconcile_all(conn, symbol: str, dates: list[str], *, log=print) -> list[dict]:
    from cherrypick.core import broker as _broker

    import credentials as creds

    session = creds.get_session()
    account = await _broker.resolve_account(session, creds.designated_account())
    results = []
    for trade_date in dates:
        transactions = await _fetch_transactions(account, session, trade_date, symbol)
        results.append(reconcile_date(conn, trade_date, symbol, transactions, log=log))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reconcile the flies LIVE ledger against actual broker cash flow."
    )
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date", help="A single trade_date (YYYY-MM-DD). Default: every pending date.")
    ap.add_argument("--lookback-days", type=int, default=5)
    ap.add_argument("--db")
    args = ap.parse_args()

    conn = dbmod.connect(args.db or dbmod.live_db_path())
    dates = [args.date] if args.date else pending_reconciliation(conn, args.symbol, args.lookback_days)
    if not dates:
        print(json.dumps({"ok": True, "symbol": args.symbol, "pending": [], "results": []}))
        return 0

    results = asyncio.run(_reconcile_all(conn, args.symbol, dates))
    print(
        json.dumps(
            {"ok": True, "symbol": args.symbol, "pending": dates, "results": results}, indent=2, default=str
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
