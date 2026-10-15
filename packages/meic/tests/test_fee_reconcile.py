"""MEIC live fee reconciliation against broker transactions.

The ledger rows are built through the live ledger's own writers (`db init_db`, `paper._save_trade`,
`paper._apply_exit_decision`), so the modeled fees under test are the ones the live loop records;
the broker side is a transcribed-shape transaction list, the fields `core.broker.transaction_history`
returns.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cherrypick.meic import fee_reconcile, paper

DAY = "2026-07-08"
NEXT = "2026-07-09"
SP, LP, SC, LC = ".XSP260708P583", ".XSP260708P578", ".XSP260708C598", ".XSP260708C603"
OCC = {
    SP: "XSP   260708P00583000",
    LP: "XSP   260708P00578000",
    SC: "XSP   260708C00598000",
    LC: "XSP   260708C00603000",
}
OID = "LIVE-XSP-111"
EXAMPLE = Path(__file__).resolve().parents[2] / "orchestrator" / "config.example.json"


def _init_db(tmp_path):
    db_path = str(tmp_path / "live.db")
    subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.db", "--db", db_path, "init_db"],
        check=True,
        capture_output=True,
    )
    return db_path


def _settled_row(tmp_path):
    """A live IC on XSP: entry order 111 filled, the put side stopped by order 222, the call side
    held to settlement with XSP at 600 -- the 598 short call settles $2 ITM, the 603 long expires."""
    db_path = _init_db(tmp_path)
    paper._save_trade(
        {
            "ic_order_id": OID,
            "trade_date": DAY,
            "entry_time": f"{DAY} 10:00:00",
            "expiration": DAY,
            "symbol": "XSP",
            "put_strike": 583,
            "call_strike": 598,
            "wing_width": 5,
            "put_symbol": SP,
            "call_symbol": SC,
            "long_put_symbol": LP,
            "long_call_symbol": LC,
            "put_credit": 0.55,
            "call_credit": 0.52,
            "net_credit": 1.07,
            "quantity": 1,
            "status": "open",
            "arm": "live",
            "execution_mode": "live",
            "fees": paper.open_fees("XSP", 1),
            "put_spread_entry_order_id": "111",
            "call_spread_entry_order_id": "111",
            "fill_confirmed_at": f"{DAY} 10:00:05",
        },
        db_path,
    )
    row = _row(db_path)
    paper._apply_exit_decision(row, {"action": "stop_put", "put_exit_price": 1.10}, "XSP", db_path)
    paper._update_trade(OID, {"put_stop_order_id": "222", "put_stop_fill_status": "filled"}, db_path)
    paper._apply_exit_decision(
        _row(db_path),
        {
            "action": "expire",
            "put_open": False,
            "call_open": True,
            "call_exit_price": 2.0,
            "settle_underlying": 600.0,
        },
        "XSP",
        db_path,
    )
    return db_path


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row(db_path):
    with _conn(db_path) as conn:
        return dict(conn.execute("SELECT * FROM ic_trades WHERE ic_order_id = ?", (OID,)).fetchone())


def _trade(order_id, symbol, action, value, *, commission=0.0, clearing=0.0, regulatory=0.0, qty=1):
    return {
        "transaction_type": "Trade",
        "transaction_date": DAY,
        "order_id": order_id,
        "symbol": symbol,
        "action": action,
        "quantity": qty,
        "value": value,
        "commission": -commission,
        "clearing_fees": -clearing,
        "regulatory_fees": -regulatory,
        "proprietary_index_option_fees": 0.0,
    }


def _settle(symbol, value, *, fee=0.0, qty=1):
    return {
        "transaction_type": "Receive Deliver",
        "transaction_date": DAY,
        "order_id": None,
        "symbol": symbol,
        "quantity": qty,
        "value": value,
        "clearing_fees": -fee,
    }


def _broker(settle_fee=5.00, short_call_qty=1, short_call_value=-200.0):
    """What the broker posted for the row above. Fees total 4.49 on the entry and 0.24 on the stop,
    so with a $5 settlement they agree with the ledger's model to the cent."""
    return [
        _trade(111, OCC[SP], "Sell to Open", 67.0, commission=1.0, clearing=0.10, regulatory=0.03),
        _trade(111, OCC[LP], "Buy to Open", -12.0, commission=1.0, clearing=0.10, regulatory=0.02),
        _trade(111, OCC[SC], "Sell to Open", 62.0, commission=1.0, clearing=0.10, regulatory=0.03),
        _trade(111, OCC[LC], "Buy to Open", -10.0, commission=1.0, clearing=0.10, regulatory=0.01),
        _trade(222, OCC[SP], "Buy to Close", -125.0, clearing=0.10, regulatory=0.02),
        _trade(222, OCC[LP], "Sell to Close", 15.0, clearing=0.10, regulatory=0.02),
        _settle(OCC[SC], short_call_value, fee=settle_fee, qty=short_call_qty),
        _settle(OCC[LC], 0.0),
        # Another live loop's order on the same underlying and day: never this row's.
        _trade(
            999, "XSP   260708C00605000", "Sell to Open", 40.0, commission=1.0, clearing=0.10, regulatory=0.03
        ),
    ]


def test_the_ledger_and_the_broker_agree_when_the_model_was_right(tmp_path):
    db_path = _settled_row(tmp_path)
    before = _row(db_path)
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(conn, DAY, "XSP", _broker(), log=lambda *_: None)
    assert out["reconciled"] == [OID] and out["unmatched"] == []
    assert out["fee_mismatch"] == [], out["variance"]
    v = out["variance"][0]
    assert v["broker_trade_fees"] == 4.73 and v["broker_settlement_fees"] == 5.00
    assert v["broker_fees"] == pytest.approx(before["fees"], abs=0.01)
    # Gross: +107 entry, -110 stop, -200 settlement -- what the ledger's per-side P&L recorded.
    assert v["broker_gross"] == -203.0 and v["gross_delta"] == pytest.approx(0.0, abs=0.01)


def test_a_fee_mismatch_is_reported_and_dry_run_writes_nothing(tmp_path):
    db_path = _settled_row(tmp_path)
    before = _row(db_path)
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(conn, DAY, "XSP", _broker(settle_fee=10.00), log=lambda *_: None)
        assert not fee_reconcile._has_table(conn, "fee_reconciliations")
    assert out["fee_mismatch"] == [OID]
    v = out["variance"][0]
    assert v["broker_settlement_fees"] == 10.00 and v["fee_delta"] == pytest.approx(5.00, abs=0.01)
    assert out["fee_variance"][0]["real_fee"] == 10.00 and out["fee_variance"][0]["modeled_fee"] == 5.00
    after = _row(db_path)
    assert (after["fees"], after["settlement_fees"]) == (before["fees"], before["settlement_fees"])


def test_apply_writes_the_brokers_fees_and_keeps_the_model_once(tmp_path):
    db_path = _settled_row(tmp_path)
    before = _row(db_path)
    with _conn(db_path) as conn:
        fee_reconcile.reconcile_date(
            conn, DAY, "XSP", _broker(settle_fee=10.00), apply=True, log=lambda *_: None
        )
        snap = dict(
            conn.execute("SELECT * FROM fee_reconciliations WHERE ic_order_id = ?", (OID,)).fetchone()
        )
        assert fee_reconcile.pending_reconciliation(conn, today=NEXT) == []
        again = fee_reconcile.reconcile_date(conn, DAY, "XSP", _broker(), apply=True, log=lambda *_: None)
    after = _row(db_path)
    # Trading fees and settlement stay separate: the total in `fees`, the settlement part beside it.
    assert after["fees"] == 14.73 and after["settlement_fees"] == 10.00
    assert after["pnl"] == before["pnl"], "the gross is reported, never rewritten"
    assert snap["status"] == "reconciled"
    assert snap["modeled_fees"] == pytest.approx(before["fees"]) and snap["modeled_settlement_fees"] == 5.00
    assert snap["broker_trade_fees"] == 4.73 and snap["broker_settlement_fees"] == 10.00
    assert again["reconciled"] == [], "a reconciled row is terminal"


def test_only_this_rows_order_ids_are_counted(tmp_path):
    """The matching rule. Order 999 is another loop's trade on the same underlying and day; a match
    by underlying or date instead of by order id would charge its $1.13 to this row."""
    db_path = _settled_row(tmp_path)
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(conn, DAY, "XSP", _broker(), log=lambda *_: None)
    assert out["variance"][0]["broker_trade_fees"] == 4.73
    assert out["variance"][0]["broker_gross"] == -203.0


def test_a_shared_settlement_line_is_split_per_contract(tmp_path):
    """flies or bwb short the same 598 call twice: the broker settles all three contracts on one
    line with one $5 fee. This row takes its one contract's share of each."""
    db_path = _settled_row(tmp_path)
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(
            conn, DAY, "XSP", _broker(short_call_qty=3, short_call_value=-600.0), log=lambda *_: None
        )
    v = out["variance"][0]
    assert v["broker_settlement_fees"] == pytest.approx(1.67, abs=0.005)
    assert v["broker_gross"] == -203.0


def test_an_unposted_order_leaves_the_row_unmatched_and_still_pending(tmp_path):
    db_path = _settled_row(tmp_path)
    txns = [t for t in _broker() if t.get("order_id") != 222]
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(conn, DAY, "XSP", txns, apply=True, log=lambda *_: None)
        assert fee_reconcile.pending_reconciliation(conn, today=NEXT) == [(DAY, "XSP")]
        assert fee_reconcile.pending_reconciliation(conn, today=DAY) == []  # fees post overnight
    assert out["unmatched"][0]["reason"] == "no trade lines for order(s) 222"
    assert _row(db_path)["fees"] == pytest.approx(
        paper.open_fees("XSP") + paper.close_fees_one_side("XSP") + 5.0
    )


def test_a_settlement_line_without_a_quantity_is_never_taken_whole(tmp_path):
    """Another loop may hold the strike, and a MEIC-only count cannot see it."""
    db_path = _settled_row(tmp_path)
    txns = _broker()
    for t in txns:
        if t["transaction_type"] == "Receive Deliver" and t["symbol"] == OCC[SC]:
            t["quantity"] = None
    with _conn(db_path) as conn:
        out = fee_reconcile.reconcile_date(conn, DAY, "XSP", txns, log=lambda *_: None)
    assert out["unmatched"][0]["reason"] == "a settled leg has no splittable settlement line"


def test_the_shipped_fee_reconcile_argv_is_one_this_module_accepts():
    """bwb's shipped argv once died in argparse every morning; this parses meic's through the real
    parser, and the job must write (`--apply`) or it reconciles nothing."""
    argv = json.loads(EXAMPLE.read_text(encoding="utf-8"))["modules"]["meic"]["paper"]["fee_reconcile_argv"]
    assert argv[:2] == ["-m", "cherrypick.meic.fee_reconcile"], argv
    args = fee_reconcile.build_parser().parse_args(argv[2:])
    assert args.apply is True
