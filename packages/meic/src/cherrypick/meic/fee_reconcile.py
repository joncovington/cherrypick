"""Reconcile the MEIC LIVE ledger's fees against what the broker actually charged.

A live `ic_trades` row's `fees` is the model until this runs: the open fee from the schedule
(`paper.open_fees`), each close from `close_fees_one_side`/`close_fees_full_ic`, and $5 per ITM
strike at settlement (`paper.expire_fees`), which the broker posts overnight. This module fetches
the real transactions for a settled session (`cherrypick.core.broker.transaction_history`,
GET-only) and compares, per row, the broker's trading fees and settlement fees with the ledger's.
**Dry-run by default**: it reports and writes nothing. With `--apply` it replaces `fees` (the
total, as every reader sums it) and `settlement_fees` (the part of it that is settlement) with the
broker's, after snapshotting the modeled values once into `fee_reconciliations`.

Matching is exact, never fuzzy -- the flies rule. Trade lines are matched by the broker order ids
the row carries (`put_spread_entry_order_id`/`call_spread_entry_order_id`, the same id twice today,
and each side's `{side}_stop_order_id`; an agent-era row whose `ic_order_id` IS the broker id
counts that too). Settlement lines are matched by streamer symbol on the trade date, only for the
sides `ic_spread_legs` records as `expired` -- the sides actually held to settlement -- and split
per contract (`cherrypick.core.fees.allocate_settlement`, the split flies uses), because flies and
bwb trade SPX in this account too and one broker line settles everyone's net holding in a strike.

How it differs from flies, and why:

  - **Fees only; the gross is reported, never written.** Flies rewrites net/gross/pnl from broker
    cash because its ledger has no per-side record. MEIC's `pnl` is the gross and is split per
    side in `ic_spread_legs.pnl`; rewriting the row total alone would leave the two disagreeing.
    Live fills already book from the broker's own leg fills (`entry_fill_credits`,
    `exit_fill_price`), so the gross delta is reported as a measurement and a WARN past $1.
  - **A quantity-less settlement line is never taken whole.** Flies accepts one when no OTHER flies
    position references the symbol; here a MEIC-only count cannot see a flies or bwb holding on the
    same strike, so such a line leaves the row unmatched.
  - **No `modeled_*` columns on `ic_trades`.** The snapshot and the reconciliation state live in
    this module's own `fee_reconciliations` table in the live ledger, created on first `--apply`, so
    the `meic_ic` schema the orchestrator reads is unchanged.
  - **A row with no per-side record** (the agent-era rows of June 2026) is unmatched rather than
    guessed at: which side settled is not knowable from the row.

`unmatched` is a state to retry, never a verdict: only `reconciled` is terminal, and
`pending_reconciliation` keeps offering an unmatched date until it leaves the lookback.

Run by the supervisor's `meic-fee-reconcile` job (the orchestrator config's `fee_reconcile_at` /
`fee_reconcile_argv`), unconditional on arming for the reason flies' docstring gives; by hand,
`python -m cherrypick.meic.fee_reconcile` reports and `--apply` corrects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import date, timedelta

from cherrypick.core import db as _core_db
from cherrypick.core import fees as _fees
from cherrypick.core import streamcache

from cherrypick.meic import paper
from cherrypick.meic import paths as _paths

_fee_total = _fees.broker_fee_total
_SETTLEMENT_TRANSACTION_TYPE = "Receive Deliver"
_TRADE_TRANSACTION_TYPE = "Trade"

# A row that is still live (or never was) has nothing settled to reconcile.
_UNSETTLED = ("pending", "open", "partial", "partial_entry", "cancelled")
_ENTRY_ID_COLUMNS = ("put_spread_entry_order_id", "call_spread_entry_order_id")
_SIDES = ("put", "call")
# Signed contracts per unit, by the column holding each leg's streamer symbol.
_SIDE_LEGS = {
    "put": (("put_symbol", -1), ("long_put_symbol", 1)),
    "call": (("call_symbol", -1), ("long_call_symbol", 1)),
}
# Past this, a fee or gross difference is written up as a WARN in the log line.
_FEE_TOLERANCE = 0.01
_GROSS_WARN = 1.00

_RECON_DDL = """
CREATE TABLE IF NOT EXISTS fee_reconciliations (
    ic_order_id              TEXT PRIMARY KEY REFERENCES ic_trades(ic_order_id),
    trade_date               TEXT NOT NULL,
    status                   TEXT NOT NULL,
    reason                   TEXT,
    modeled_fees             REAL,
    modeled_settlement_fees  REAL,
    broker_trade_fees        REAL,
    broker_settlement_fees   REAL,
    ledger_gross             REAL,
    broker_gross             REAL,
    reconciled_at            TEXT,
    updated_at               TEXT NOT NULL
)
"""


def _has_table(conn, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        is not None
    )


def _reconciled_ids(conn) -> set[str]:
    if not _has_table(conn, "fee_reconciliations"):
        return set()
    return {
        r[0] for r in conn.execute("SELECT ic_order_id FROM fee_reconciliations WHERE status = 'reconciled'")
    }


def _order_ids(row: dict) -> tuple[set[str], set[str]]:
    """(required, all) broker order ids for a row. Required: the entry, and each side whose close
    the broker confirmed filled -- a recorded fill with no trade line means the broker has not
    posted it. A close that died (cancelled/rejected) keeps its id on the row but filled nothing,
    so it is collected if the broker shows lines on it and never required."""
    entry = {str(row[c]) for c in _ENTRY_ID_COLUMNS if row.get(c)}
    if not entry and str(row.get("ic_order_id") or "").isdigit():
        entry = {str(row["ic_order_id"])}  # agent era: the row's own id is the broker's
    required = set(entry)
    every = set(entry)
    for side in _SIDES:
        oid = row.get(f"{side}_stop_order_id")
        if not oid:
            continue
        every.add(str(oid))
        if row.get(f"{side}_stop_fill_status") == "filled":
            required.add(str(oid))
    return required, every


def _side_statuses(conn, ic_order_id: str) -> dict[str, str]:
    if not _has_table(conn, "ic_spread_legs"):
        return {}
    return {
        r["side"]: r["status"]
        for r in conn.execute("SELECT side, status FROM ic_spread_legs WHERE ic_order_id = ?", (ic_order_id,))
    }


def _settled_legs(row: dict, sides: dict[str, str]) -> dict[str, int] | None:
    """`{streamer symbol: signed contracts}` for the legs this row held to settlement, or None when
    a settled side's leg symbol is missing from the row."""
    qty = int(row.get("quantity") or 1)
    out: dict[str, int] = {}
    for side in _SIDES:
        if sides.get(side) != "expired":
            continue
        for col, per_unit in _SIDE_LEGS[side]:
            sym = row.get(col)
            if not sym:
                return None
            out[str(sym)] = out.get(str(sym), 0) + per_unit * qty
    return out


def _settlement_lines(transactions: list[dict], trade_date: str) -> dict[str, list[dict]]:
    """The broker's settlement lines dated `trade_date`, grouped by streamer symbol (the ledger's
    spelling) through the one shared converter."""
    out: dict[str, list[dict]] = {}
    for t in transactions:
        if (
            t.get("transaction_type") != _SETTLEMENT_TRANSACTION_TYPE
            or t.get("transaction_date") != trade_date
        ):
            continue
        out.setdefault(streamcache.occ_to_streamer_symbol(str(t.get("symbol") or "")), []).append(t)
    return out


def _settled_rows(conn, *, trade_date: str | None = None, symbol: str | None = None) -> list[dict]:
    where = ["exit_time IS NOT NULL", f"status NOT IN ({','.join('?' * len(_UNSETTLED))})"]
    params: list = list(_UNSETTLED)
    if trade_date:
        where.append("trade_date = ?")
        params.append(trade_date)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    sql = f"SELECT * FROM ic_trades WHERE {' AND '.join(where)} ORDER BY id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def pending_reconciliation(
    conn, symbol: str | None = None, lookback_days: int = 5, today: str | None = None
) -> list[tuple[str, str]]:
    """`(trade_date, symbol)` pairs with settled, unreconciled live rows, strictly before today (the
    broker posts settlement fees overnight) and within `lookback_days`. `unmatched` does not close a
    date -- only `reconciled` is terminal (the defect flies and bwb each had one half of)."""
    today = today or paper._now_et().date().isoformat()
    cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    done = _reconciled_ids(conn)
    out = {
        (r["trade_date"], r["symbol"])
        for r in _settled_rows(conn, symbol=symbol)
        if cutoff <= r["trade_date"] < today and r["ic_order_id"] not in done
    }
    return sorted(out)


def reconcile_date(
    conn, trade_date: str, symbol: str, transactions: list[dict], *, apply: bool = False, log=print
) -> dict:
    """Compare every settled, unreconciled `symbol` row on `trade_date` with already-fetched broker
    `transactions`. Pure given the transactions; writes only when `apply`."""
    done = _reconciled_ids(conn)
    rows = [
        r for r in _settled_rows(conn, trade_date=trade_date, symbol=symbol) if r["ic_order_id"] not in done
    ]
    result = {
        "trade_date": trade_date,
        "symbol": symbol,
        "applied": apply,
        "reconciled": [],
        "unmatched": [],
        "fee_mismatch": [],
        "variance": [],
        "fee_variance": [],
        "warnings": [],
    }
    if not rows:
        return result
    lines = _settlement_lines(transactions, trade_date)
    trades = [t for t in transactions if t.get("transaction_type") == _TRADE_TRANSACTION_TYPE]
    now = str(paper._now_et())
    if apply:
        conn.execute(_RECON_DDL)

    def unmatched(row: dict, reason: str) -> None:
        result["unmatched"].append({"ic_order_id": row["ic_order_id"], "reason": reason})
        log(f"fee_reconcile: {row['ic_order_id']} unmatched ({reason})")
        if apply:
            conn.execute(
                "INSERT INTO fee_reconciliations (ic_order_id, trade_date, status, reason, updated_at) "
                "VALUES (?, ?, 'unmatched', ?, ?) ON CONFLICT(ic_order_id) DO UPDATE SET "
                "status = 'unmatched', reason = excluded.reason, updated_at = excluded.updated_at",
                (row["ic_order_id"], trade_date, reason, now),
            )

    for row in rows:
        oid = row["ic_order_id"]
        required, every = _order_ids(row)
        if not required:
            unmatched(row, "no broker order id on the row")
            continue
        trade_txns = [t for t in trades if str(t.get("order_id")) in every]
        posted = {str(t.get("order_id")) for t in trade_txns}
        missing = sorted(required - posted)
        if missing:
            unmatched(row, f"no trade lines for order(s) {', '.join(missing)}")
            continue
        for dead in sorted((every - required) & posted):
            msg = f"{oid}: order {dead} has fills the ledger never recorded (a close it marked dead)"
            result["warnings"].append(msg)
            log(f"fee_reconcile WARN: {msg}")

        sides = _side_statuses(conn, oid)
        if not sides:
            unmatched(row, "no per-side record in ic_spread_legs")
            continue
        legs = _settled_legs(row, sides)
        if legs is None:
            unmatched(row, "a settled side has no leg symbol on the row")
            continue
        settle_cash, settle_fees = 0.0, 0.0
        if legs:
            # Empty referenced_by: a MEIC-only count cannot see flies' or bwb's holding on a strike,
            # so a line with no quantity is never taken whole (see the module docstring).
            allocated = _fees.allocate_settlement(legs, lines, {})
            if allocated is None:
                unmatched(row, "a settled leg has no splittable settlement line")
                continue
            settle_cash, settle_fees = allocated

        trade_fees = round(sum(_fee_total(t) for t in trade_txns), 2)
        settle_fees = round(settle_fees, 2)
        broker_fees = round(trade_fees + settle_fees, 2)
        broker_gross = round(sum(float(t.get("value") or 0) for t in trade_txns) + settle_cash, 2)
        ledger_fees = row.get("fees")
        ledger_settle = row.get("settlement_fees")
        ledger_gross = row.get("pnl")
        fee_delta = round(broker_fees - float(ledger_fees or 0.0), 2)
        gross_delta = None if ledger_gross is None else round(broker_gross - float(ledger_gross), 2)
        entry = {
            "ic_order_id": oid,
            "ledger_fees": ledger_fees,
            "ledger_settlement_fees": ledger_settle,
            "broker_trade_fees": trade_fees,
            "broker_settlement_fees": settle_fees,
            "broker_fees": broker_fees,
            "fee_delta": fee_delta,
            "ledger_gross": ledger_gross,
            "broker_gross": broker_gross,
            "gross_delta": gross_delta,
        }
        result["variance"].append(entry)
        settle_off = ledger_settle is None or abs(settle_fees - float(ledger_settle)) > _FEE_TOLERANCE
        if ledger_fees is None or abs(fee_delta) > _FEE_TOLERANCE or settle_off:
            result["fee_mismatch"].append(oid)
            log(
                f"fee_reconcile FEE MISMATCH: {oid} ledger fees {ledger_fees} (settlement {ledger_settle}) "
                f"vs broker {broker_fees} (trading {trade_fees} + settlement {settle_fees})"
            )
        if gross_delta is not None and abs(gross_delta) > _GROSS_WARN:
            log(
                f"fee_reconcile WARN: {oid} ledger gross {ledger_gross} vs broker {broker_gross} (delta {gross_delta})"
            )

        # Per-symbol settlement fee against the modeled per-event charge -- the unambiguous form of
        # a fee-model change, as in flies and bwb.
        per_event = paper.expire_fees(1)
        for sym in legs:
            for txn in lines.get(sym, []):
                real = round(_fee_total(txn), 2)
                if real > 0 and abs(real - per_event) > _FEE_TOLERANCE:
                    v = {
                        "ic_order_id": oid,
                        "symbol": txn.get("symbol"),
                        "quantity": txn.get("quantity"),
                        "modeled_fee": per_event,
                        "real_fee": real,
                    }
                    result["fee_variance"].append(v)
                    log(
                        f"fee_reconcile FEE-MODEL WARN: {oid} {v['symbol']} (qty {v['quantity']}) "
                        f"modeled ${per_event:.2f} vs real ${real:.2f}"
                    )

        result["reconciled"].append(oid)
        if apply:
            conn.execute(
                "INSERT INTO fee_reconciliations (ic_order_id, trade_date, status, reason, modeled_fees, "
                "modeled_settlement_fees, broker_trade_fees, broker_settlement_fees, ledger_gross, "
                "broker_gross, reconciled_at, updated_at) VALUES (?, ?, 'reconciled', NULL, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ic_order_id) DO UPDATE SET status = 'reconciled', reason = NULL, "
                "modeled_fees = COALESCE(fee_reconciliations.modeled_fees, excluded.modeled_fees), "
                "modeled_settlement_fees = COALESCE(fee_reconciliations.modeled_settlement_fees, "
                "excluded.modeled_settlement_fees), broker_trade_fees = excluded.broker_trade_fees, "
                "broker_settlement_fees = excluded.broker_settlement_fees, ledger_gross = excluded.ledger_gross, "
                "broker_gross = excluded.broker_gross, reconciled_at = excluded.reconciled_at, "
                "updated_at = excluded.updated_at",
                (
                    oid,
                    trade_date,
                    ledger_fees,
                    ledger_settle,
                    trade_fees,
                    settle_fees,
                    ledger_gross,
                    broker_gross,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE ic_trades SET fees = ?, settlement_fees = ?, updated_at = ? WHERE ic_order_id = ?",
                (broker_fees, settle_fees, now, oid),
            )
    if apply:
        conn.commit()
    return result


def _designated_account():
    from cherrypick.meic import credentials as _creds

    return _creds.store.designated_account()


async def _reconcile_all(conn, pairs: list[tuple[str, str]], *, apply: bool, log=print) -> list[dict]:
    from cherrypick.core import broker as _broker

    from cherrypick.meic.session import get_session

    designated = _designated_account()
    if not designated:
        raise RuntimeError(
            "no designated account (unset or unreadable) -- run `cherrypick account --module meic --set <last4>`"
        )
    session = get_session()
    account = await _broker.resolve_account(session, designated)
    results = []
    for trade_date, symbol in pairs:
        # One trading day per call, as flies fetches: settlement lines are dated the expiry.
        transactions = await _broker.transaction_history(
            account, session, start_date=date.fromisoformat(trade_date), underlying_symbol=symbol
        )
        results.append(reconcile_date(conn, trade_date, symbol, transactions, apply=apply, log=log))
    return results


def build_parser() -> argparse.ArgumentParser:
    """The command line the supervisor's `meic-fee-reconcile` job invokes, from the orchestrator
    config's `fee_reconcile_argv` -- pinned against that shipped argv by test_fee_reconcile."""
    ap = argparse.ArgumentParser(
        description="Compare the MEIC live ledger's fees with the broker's (dry-run unless --apply)."
    )
    ap.add_argument("--symbol", help="Underlying to reconcile. Default: every symbol with pending rows.")
    ap.add_argument("--date", help="A single trade_date (YYYY-MM-DD). Default: every pending date.")
    ap.add_argument("--lookback-days", type=int, default=5)
    ap.add_argument("--db", help="Ledger path. Default: the live ledger (meic_trades.db).")
    ap.add_argument("--apply", action="store_true", help="Write the broker's fees to the ledger.")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    symbol = args.symbol.strip().upper() if args.symbol else None
    conn = _core_db.connect(args.db or _paths.live_db_path(), pragmas=("journal_mode=WAL", "foreign_keys=ON"))
    try:
        if not _has_table(conn, "ic_trades"):
            print(json.dumps({"ok": True, "applied": args.apply, "pending": [], "results": []}))
            return 0
        if args.date:
            symbols = (
                [symbol]
                if symbol
                else sorted({r["symbol"] for r in _settled_rows(conn, trade_date=args.date)})
            )
            pairs = [(args.date, s) for s in symbols]
        else:
            pairs = pending_reconciliation(conn, symbol, args.lookback_days)
        if not pairs:
            print(json.dumps({"ok": True, "applied": args.apply, "pending": [], "results": []}))
            return 0
        try:
            results = asyncio.run(_reconcile_all(conn, pairs, apply=args.apply))
        except (RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"ok": False, "pending": pairs, "error": f"{type(exc).__name__}: {exc}"}))
            return 1
        print(
            json.dumps(
                {"ok": True, "applied": args.apply, "pending": pairs, "results": results},
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
