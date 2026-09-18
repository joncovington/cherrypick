"""Reconcile the bwb LIVE ledger against actual broker cash flow.

A live row's costs are estimates until this runs: `fees` is the broker's own dry-run fee
estimate (or the schedule when it gave none), and the $5-per-ITM-leg settlement fee is modeled at
settlement because it posts to transaction history the next business day. This module fetches the
real transactions for a settled expiration and recomputes `fees` and `gross_pnl` purely from summed
transaction fields -- never re-derived from the model -- after snapshotting the modeled values once
(`modeled_fees` / `modeled_gross_pnl`) so nothing is lost.

Matching is exact, not fuzzy (the flies rule): trade-side legs are matched by the order ids the
row already carries (`entry_order_id`, `addon_order_id` -- one order each, however many legs);
settlement-side legs by OCC option symbol on the expiration date, built from the ledger's own leg
rows. A position with no matching broker data is marked `unmatched` rather than guessed at.

Split in two on purpose: `reconcile_date` is pure given already-fetched transactions (testable
against a transcribed chain), and the async fetch is the live loop's `BrokerAdapter.history` or
this module's own CLI -- neither of which this file imports.

Two copies now (flies and bwb). That is the point at which the matching-agnostic part -- fee
summing, the modeled snapshot, the variance report -- gets measured for a core home; not before.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from cherrypick.bwb import clock, db, engine

# A position's fee is the sum of these fields' magnitudes across every matched transaction --
# tastytrade reports each as a signed (negative) charge or, on some rows, absent entirely.
_FEE_FIELDS = ("commission", "clearing_fees", "regulatory_fees", "proprietary_index_option_fees")
_SETTLEMENT_TRANSACTION_TYPE = "Receive Deliver"
_TRADE_TRANSACTION_TYPE = "Trade"


def _fee_total(txn: dict) -> float:
    return sum(abs(float(txn[field])) for field in _FEE_FIELDS if txn.get(field) not in (None, ""))


def pending_reconciliation(conn, symbol: str, lookback_days: int = 7, today: str | None = None) -> list[str]:
    """Expiration dates with closed, unreconciled `symbol` positions, strictly before today
    (settlement fees post the next business day) and within `lookback_days`."""
    today = today or clock.now_et().date().isoformat()
    cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT expiration FROM bwb_positions WHERE symbol = ? AND status = 'closed' "
        "AND expiration < ? AND expiration >= ? AND reconciled_at IS NULL "
        "AND (fees_source IS NULL OR fees_source NOT IN ('reconciled', 'unmatched')) "
        "AND entry_order_id IS NOT NULL",
        (symbol, today, cutoff),
    ).fetchall()
    return sorted(r["expiration"] for r in rows)


def reconcile_date(conn, expiration: str, symbol: str, transactions: list[dict], *, log=print) -> dict:
    """Reconcile every closed, unreconciled live position expiring `expiration` against
    already-fetched broker `transactions`. Idempotent: reconciled or unmatched rows are skipped."""
    positions = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM bwb_positions WHERE expiration = ? AND symbol = ? AND status = 'closed' "
            "AND reconciled_at IS NULL AND (fees_source IS NULL OR fees_source NOT IN ('reconciled', 'unmatched')) "
            "AND entry_order_id IS NOT NULL",
            (expiration, symbol),
        ).fetchall()
    ]
    result = {
        "expiration": expiration,
        "symbol": symbol,
        "reconciled": [],
        "unmatched": [],
        "variance": [],
        "fee_variance": [],
    }
    for position in positions:
        pid = position["position_id"]
        order_ids = {str(position[k]) for k in ("entry_order_id", "addon_order_id") if position.get(k)}
        trade_txns = [
            t
            for t in transactions
            if t.get("transaction_type") == _TRADE_TRANSACTION_TYPE and str(t.get("order_id")) in order_ids
        ]
        leg_symbols = {leg["occ_symbol"] for leg in db.legs_for(conn, pid) if leg.get("occ_symbol")}
        settlement_txns = [
            t
            for t in transactions
            if t.get("transaction_type") == _SETTLEMENT_TRANSACTION_TYPE
            and t.get("symbol") in leg_symbols
            and t.get("transaction_date") == expiration
        ]
        if not trade_txns or not settlement_txns:
            db.save_position(conn, {"position_id": pid, "fees_source": "unmatched"})
            result["unmatched"].append(pid)
            log(
                f"fee_reconcile: {pid} unmatched (trade_txns={len(trade_txns)}, settlement_txns={len(settlement_txns)})"
            )
            continue

        real_credit = sum(float(t["value"]) for t in trade_txns)  # dollars, the credits received
        real_payoff = sum(float(t["value"]) for t in settlement_txns)  # dollars, the cash settlement
        real_fees = round(sum(_fee_total(t) for t in trade_txns + settlement_txns), 2)
        real_gross = round(real_credit + real_payoff, 2)
        modeled_fees = position.get("fees")
        modeled_gross = position.get("gross_pnl")
        db.save_position(
            conn,
            {
                "position_id": pid,
                "modeled_fees": position.get("modeled_fees")
                if position.get("modeled_fees") is not None
                else modeled_fees,
                "modeled_gross_pnl": (
                    position.get("modeled_gross_pnl")
                    if position.get("modeled_gross_pnl") is not None
                    else modeled_gross
                ),
                "fees": real_fees,
                "gross_pnl": real_gross,
                "fees_source": "reconciled",
                "reconciled_at": clock.now_iso(),
            },
        )
        result["reconciled"].append(pid)
        modeled_net = round(float(modeled_gross or 0.0) - float(modeled_fees or 0.0), 2)
        real_net = round(real_gross - real_fees, 2)
        delta = round(real_net - modeled_net, 2)
        result["variance"].append(
            {"position_id": pid, "modeled_net": modeled_net, "broker_net": real_net, "delta": delta}
        )
        if abs(delta) > 1.0:
            log(f"fee_reconcile WARN: {pid} modeled net {modeled_net} vs broker {real_net} (delta {delta})")

        # Per-symbol settlement-fee comparison: "modeled $5 vs real $10 on ONE symbol" is the
        # unambiguous form of a fee-model change; an aggregate delta reads as slippage noise. The
        # doubled body is one symbol -- whether the broker charges it once or per contract is
        # exactly what this comparison measures (the flies 2026-07-31 finding, still open here).
        per_event = engine.settlement_fee(1)
        for txn in settlement_txns:
            real_fee = round(_fee_total(txn), 2)
            if real_fee <= 0:
                continue
            if abs(real_fee - per_event) > 0.01:
                v = {
                    "position_id": pid,
                    "symbol": txn.get("symbol"),
                    "quantity": txn.get("quantity"),
                    "modeled_fee": per_event,
                    "real_fee": real_fee,
                }
                result["fee_variance"].append(v)
                log(
                    f"fee_reconcile FEE-MODEL WARN: {pid} {v['symbol']} (qty {v['quantity']}) "
                    f"modeled ${per_event:.2f} vs real ${real_fee:.2f}"
                )
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="reconcile the bwb live ledger against broker transactions")
    ap.add_argument("--date", help="YYYY-MM-DD expiration to reconcile (default: every pending one)")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    from cherrypick.bwb import cli as climod
    from cherrypick.bwb import live_loop

    config = climod.load_config(args.config)
    symbol = (config.get("symbol") or "SPX").strip().upper()
    conn = db.connect(db.live_db_path())
    broker = live_loop.BrokerAdapter(config)
    dates = [args.date] if args.date else pending_reconciliation(conn, symbol)
    out = []
    for d in dates:
        transactions, err = broker.history(d, symbol)
        if transactions is None:
            out.append({"expiration": d, "ok": False, "error": err})
            continue
        out.append({"ok": True, **reconcile_date(conn, d, symbol, transactions)})
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
