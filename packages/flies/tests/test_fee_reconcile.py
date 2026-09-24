"""Tests for fee_reconcile.py, built from the real 2026-07-30 XSP 744-center fly's transcribed
broker transactions (the same session used to discover the modeled-vs-real P&L gap this module
exists to close) — same spirit as tests/fixtures/books.json's real order chains.
"""

from __future__ import annotations

import pytest

from cherrypick.flies import db as dbmod
from cherrypick.flies import fee_reconcile

TRADE_DATE = "2026-07-30"
SYMBOL = "XSP"


def _trade_txn(order_id, value, fees=(-0.02, -0.1, -1.0, 0.0)):
    reg, clr, comm, prop = fees
    return {
        "transaction_type": "Trade",
        "order_id": order_id,
        "value": str(value),
        "regulatory_fees": str(reg),
        "clearing_fees": str(clr),
        "commission": str(comm),
        "proprietary_index_option_fees": str(prop),
    }


def _settlement_txn(symbol, value, clearing_fee=None):
    return {
        "transaction_type": "Receive Deliver",
        "transaction_date": TRADE_DATE,
        "symbol": symbol,
        "value": str(value),
        "regulatory_fees": None,
        "clearing_fees": str(clearing_fee) if clearing_fee is not None else None,
        "commission": None,
        "proprietary_index_option_fees": None,
    }


# The 744-center fly's real order chain and settlement (order3=entry, order4=completion).
_744_TRANSACTIONS = [
    _trade_txn("489436397", 190.0),  # Sell to Open 744P @1.90
    _trade_txn("489436397", -118.0),  # Buy to Open 743P @1.18
    _trade_txn("489436686", -97.0),  # Buy to Open 745P @0.97
    _trade_txn("489436686", 42.0),  # Sell to Open 744P @0.42
    _settlement_txn("XSP   260730P00743000", 0.0),  # expired worthless
    _settlement_txn("XSP   260730P00744000", 0.0),  # assignment removal (no fee row)
    _settlement_txn("XSP   260730P00744000", -48.0, clearing_fee=-5.0),  # cash-settled assignment
    _settlement_txn("XSP   260730P00745000", 0.0),  # exercise removal (no fee row)
    _settlement_txn("XSP   260730P00745000", 124.0, clearing_fee=-5.0),  # cash-settled exercise
]


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _save_744_position(conn, **overrides):
    row = {
        "position_id": "744-fly",
        "trade_date": TRADE_DATE,
        "arm": "gex",
        "symbol": SYMBOL,
        "kind": "fly",
        "side": "put",
        "center": 744,
        "wing_width": 1,
        "quantity": 1,
        "net": 0.10,  # modeled: 0.65 credit - 0.55 debit
        "fees": 19.49,  # modeled: vertical_open_fee x2 + flat $5/contract assignment estimate
        "gross_pnl": 86.0,
        "pnl": 66.51,
        "expiry_payoff": 0.85,
        "status": "settled",
        "entry_order_id": "489436397",
        "completion_order_id": "489436686",
    }
    row.update(overrides)
    row.setdefault("book_id", f"{row['trade_date']}:{row['arm']}:{row['symbol']}")
    dbmod.save_position(conn, row)
    dbmod.save_book(
        conn,
        {
            "book_id": row["book_id"],
            "trade_date": row["trade_date"],
            "arm": row["arm"],
            "symbol": row["symbol"],
            "pnl": row["pnl"],
            "fees": row["fees"],
            "status": "settled",
        },
    )
    return row


def test_reconcile_date_recomputes_pnl_from_real_broker_cash_flow(live_conn):
    _save_744_position(live_conn)
    result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)

    assert result["reconciled"] == ["744-fly"]
    assert result["unmatched"] == []

    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone()
    # Real cash: trades net +$17.00, settlement net +$76.00, fees $14.48 -> gross $93.00, pnl $78.52.
    assert row["net"] == pytest.approx(0.17, abs=1e-4)
    assert row["expiry_payoff"] == pytest.approx(0.76, abs=1e-4)
    assert row["fees"] == pytest.approx(14.48, abs=1e-2)
    assert row["gross_pnl"] == pytest.approx(93.0, abs=1e-2)
    assert row["pnl"] == pytest.approx(78.52, abs=1e-2)
    assert row["broker_reconciliation_status"] == "reconciled"
    assert row["broker_reconciled_at"] is not None
    # The original modeled figures are snapshotted, not lost.
    assert row["modeled_pnl"] == pytest.approx(66.51)
    assert row["modeled_fees"] == pytest.approx(19.49)
    assert row["modeled_gross_pnl"] == pytest.approx(86.0)

    variance = result["variance"][0]
    assert variance["position_id"] == "744-fly"
    assert variance["delta"] == pytest.approx(12.01, abs=1e-2)

    book = live_conn.execute("SELECT * FROM fly_books WHERE book_id = ?", (row["book_id"],)).fetchone()
    assert book["pnl"] == pytest.approx(78.52, abs=1e-2)
    assert book["broker_reconciliation_status"] == "reconciled"


def test_reconcile_date_is_idempotent(live_conn):
    _save_744_position(live_conn)
    fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)
    first = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone())

    # Second call: the position no longer matches the "unreconciled" query (broker_reconciled_at
    # is set), so it's a no-op -- not a second overwrite of modeled_* or a double fee count.
    second_result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)
    second = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone())

    assert second_result["reconciled"] == []
    assert second_result["unmatched"] == []
    assert second == first


def test_reconcile_date_leaves_unmatched_position_untouched(live_conn):
    row = _save_744_position(
        live_conn, position_id="orphan-fly", entry_order_id="999999", completion_order_id="888888"
    )
    result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)

    assert result["reconciled"] == []
    assert result["unmatched"] == ["orphan-fly"]

    stored = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'orphan-fly'").fetchone()
    assert stored["broker_reconciliation_status"] == "unmatched"
    assert stored["broker_reconciled_at"] is None
    # Canonical columns untouched -- no guessing.
    assert stored["pnl"] == pytest.approx(row["pnl"])
    assert stored["fees"] == pytest.approx(row["fees"])
    assert stored["modeled_pnl"] is None


# The real 2026-09-21 SPX chain. The instrument's OCC root is SPXW -- SPX 0DTE trades as the
# weekly -- while the position's `symbol` column says "SPX". Constructing the settlement symbol
# from that column produced "SPX   260921C07720000", matched nothing, and marked every SPX session
# unmatched from 2026-08-03 onward while the XSP era (whose root IS "XSP") reconciled fine.
_SPXW_TRANSACTIONS = [
    {**_trade_txn("507935135", 235.0), "symbol": "SPXW  260921C07720000"},
    {**_trade_txn("507935135", -0.0), "symbol": "SPXW  260921C07725000"},
    {
        **_settlement_txn("SPXW  260921C07720000", -4470.0, clearing_fee=-5.0),
        "transaction_date": "2026-09-21",
    },
    {
        **_settlement_txn("SPXW  260921C07725000", 3970.0, clearing_fee=-5.0),
        "transaction_date": "2026-09-21",
    },
]


def _save_spxw_position(conn, **overrides):
    row = {
        "position_id": "spxw-vertical",
        "trade_date": "2026-09-21",
        "arm": "control",
        "symbol": "SPX",
        "kind": "short_vertical",
        "side": "call",
        "center": 7720.0,
        "wing_width": 5.0,
        "quantity": 1,
        "net": 2.35,
        "fees": 13.44,
        "gross_pnl": -265.0,
        "pnl": -278.44,
        "status": "settled",
        "entry_order_id": "507935135",
        "center_leg_symbol": ".SPXW260921C7720",
        "wing_leg_symbol": ".SPXW260921C7725",
        "completing_leg_symbol": ".SPXW260921C7715",
    }
    row.update(overrides)
    row.setdefault("book_id", f"{row['trade_date']}:{row['arm']}:{row['symbol']}")
    dbmod.save_position(conn, row)
    return row


def test_an_spx_position_settles_against_its_spxw_legs(live_conn):
    """Shown to fail before the root fix: `symbol` is "SPX" but the traded instrument is SPXW, so
    the constructed OCC symbol matched no settlement line and the position fell to `unmatched`.
    The real cash flow here is -4470 + 3970 = -500, the 5-wide's max loss."""
    _save_spxw_position(live_conn)
    result = fee_reconcile.reconcile_date(
        live_conn, "2026-09-21", "SPX", _SPXW_TRANSACTIONS, log=lambda *_: None
    )
    assert result["reconciled"] == ["spxw-vertical"]
    assert result["unmatched"] == []
    stored = dict(
        live_conn.execute(
            "SELECT gross_pnl, expiry_payoff, broker_reconciliation_status FROM fly_positions "
            "WHERE position_id = 'spxw-vertical'"
        ).fetchone()
    )
    assert stored["broker_reconciliation_status"] == "reconciled"
    assert stored["expiry_payoff"] == pytest.approx(-5.0)  # (-4470 + 3970) / 100


def test_a_row_predating_the_leg_symbol_columns_takes_the_root_from_the_broker(live_conn):
    """August's rows were written before the leg-symbol columns existed, so the root has to come
    from the trade transactions the order id already matched -- the broker's own record of what we
    traded, rather than a guess from the underlying's name."""
    _save_spxw_position(live_conn, center_leg_symbol=None, wing_leg_symbol=None, completing_leg_symbol=None)
    result = fee_reconcile.reconcile_date(
        live_conn, "2026-09-21", "SPX", _SPXW_TRANSACTIONS, log=lambda *_: None
    )
    assert result["reconciled"] == ["spxw-vertical"]


def test_an_unmatched_position_is_retried_rather_than_written_off(live_conn):
    """`pending_reconciliation` kept listing these dates while `reconcile_date` skipped them, so the
    live loop refetched broker history every tick and did nothing with it -- forever. Only a
    successful reconcile is terminal."""
    _save_spxw_position(live_conn)
    first = fee_reconcile.reconcile_date(live_conn, "2026-09-21", "SPX", [], log=lambda *_: None)
    assert first["unmatched"] == ["spxw-vertical"]

    again = fee_reconcile.reconcile_date(
        live_conn, "2026-09-21", "SPX", _SPXW_TRANSACTIONS, log=lambda *_: None
    )
    assert again["reconciled"] == ["spxw-vertical"], "an unmatched row must be re-examinable"

    settled = fee_reconcile.reconcile_date(
        live_conn, "2026-09-21", "SPX", _SPXW_TRANSACTIONS, log=lambda *_: None
    )
    assert settled["reconciled"] == [] and settled["unmatched"] == [], "reconciled stays terminal"


def test_an_unmatched_date_stays_pending_so_the_retry_can_reach_it(live_conn):
    _save_spxw_position(live_conn)
    fee_reconcile.reconcile_date(live_conn, "2026-09-21", "SPX", [], log=lambda *_: None)
    pending = fee_reconcile.pending_reconciliation(live_conn, "SPX", lookback_days=5, today="2026-09-22")
    assert pending == ["2026-09-21"]


def test_pending_reconciliation_excludes_today_and_out_of_window(live_conn):
    _save_744_position(live_conn, trade_date="2026-07-30")
    _save_744_position(live_conn, position_id="too-old", trade_date="2026-07-01")
    _save_744_position(live_conn, position_id="today", trade_date="2026-07-31")

    pending = fee_reconcile.pending_reconciliation(live_conn, SYMBOL, lookback_days=5, today="2026-07-31")
    assert pending == ["2026-07-30"]


def test_real_fills_agree_with_the_per_settlement_event_fee_model(live_conn):
    """The real 2026-07-30 chain charged $5.00 on each of two settling SYMBOLS -- including the
    744 leg that carried 2 contracts. With the corrected per-event model those match exactly, so
    no fee-model variance is reported."""
    _save_744_position(live_conn, trade_date="2026-07-30")
    result = fee_reconcile.reconcile_date(
        live_conn, "2026-07-30", SYMBOL, _744_TRANSACTIONS, log=lambda *_: None
    )
    assert result["fee_variance"] == []


def test_a_fee_that_stops_matching_the_model_is_flagged_per_symbol(live_conn):
    """The guard against a silent re-definition of the fee (e.g. if the broker began charging per
    contract, or tiered at larger size). One symbol charged $10 where the model says $5 must be
    named explicitly -- the aggregate P&L delta alone read as ordinary slippage noise when this
    exact bug was live."""
    _save_744_position(live_conn, trade_date="2026-07-30")
    txns = [
        t
        if t.get("symbol") != "XSP   260730P00744000" or not t.get("clearing_fees")
        else {**t, "clearing_fees": -10.0}
        for t in _744_TRANSACTIONS
    ]
    logged = []
    result = fee_reconcile.reconcile_date(live_conn, "2026-07-30", SYMBOL, txns, log=logged.append)
    assert len(result["fee_variance"]) == 1
    v = result["fee_variance"][0]
    assert v["symbol"] == "XSP   260730P00744000"
    assert v["modeled_fee"] == 5.0 and v["real_fee"] == 10.0
    assert any("FEE-MODEL WARN" in m for m in logged)


# --------------------------------------------------------------------------- a strike two positions share
# 2026-09-22 SPX, as the broker actually reported it. Three flies; the two put flies SHARE the 7765P
# strike -- the 7760 fly's upper wing is the 7770 fly's lower wing -- so the broker settled the pair
# on a single 7765P line: $72.00 for 2 contracts. The reconciler credited that whole line, and its
# $5 fee, to BOTH flies and wrote the session $67.00 above the cash the broker paid.
_0922 = "2026-09-22"


def _t(order_id, value):
    return {
        "transaction_type": "Trade",
        "order_id": order_id,
        "value": str(value),
        "regulatory_fees": "-0.02",
        "clearing_fees": "-0.1",
        "commission": "-1.0",
        "proprietary_index_option_fees": "-0.6",
    }


def _s(strike_side, qty, value, fee=None):
    return {
        "transaction_type": "Receive Deliver",
        "transaction_date": _0922,
        "symbol": f"SPXW  260922{strike_side[-1]}0{strike_side[:-1]}000",
        "quantity": str(qty),
        "value": str(value),
        "regulatory_fees": None,
        "clearing_fees": str(fee) if fee is not None else None,
        "commission": None,
        "proprietary_index_option_fees": None,
    }


_0922_TRANSACTIONS = [
    # call fly 7765 (7760 / 7765x2 / 7770): +938 -693 entry, +463 -683 completion
    _t("508339476", 938.0),
    _t("508339476", -693.0),
    _t("508339536", 463.0),
    _t("508339536", -683.0),
    # put fly 7760 (7755 / 7760x2 / 7765)
    _t("508395276", 832.0),
    _t("508395276", -607.0),
    _t("508395337", 357.0),
    _t("508395337", -552.0),
    # put fly 7770 (7765 / 7770x2 / 7775)
    _t("508472249", 483.0),
    _t("508472249", -273.0),
    _t("508472400", 178.0),
    _t("508472400", -363.0),
    # settlement: each strike once, for the broker's NET holding
    _s("7760C", 1, 0.0),
    _s("7760C", 1, 464.0, -5.0),
    _s("7765C", 2, 0.0),
    _s("7770C", 1, 0.0),
    _s("7755P", 1, 0.0),
    _s("7760P", 2, 0.0),
    _s("7765P", 2, 0.0),
    _s("7765P", 2, 72.0, -5.0),  # <- the shared line
    _s("7770P", 2, 0.0),
    _s("7770P", 2, -1072.0, -5.0),
    _s("7775P", 1, 0.0),
    _s("7775P", 1, 1036.0, -5.0),
]


def _save_0922(conn, pid, center, side, entry_id, completion_id, legs):
    centre, wing, completing = (f".SPXW260922{side[0].upper()}{k}" for k in legs)
    row = {
        "position_id": pid,
        "trade_date": _0922,
        "arm": "control",
        "symbol": "SPX",
        "book_id": f"{_0922}:control:SPX",
        "kind": "fly",
        "side": side,
        "center": center,
        "wing_width": 5,
        "quantity": 1,
        "status": "settled",
        "net": 0.25,
        "fees": 11.88,
        "entry_order_id": entry_id,
        "completion_order_id": completion_id,
        "center_leg_symbol": centre,
        "wing_leg_symbol": wing,
        "completing_leg_symbol": completing,
    }
    dbmod.save_position(conn, row)


def _seed_0922(conn):
    _save_0922(conn, "c7765", 7765, "call", "508339476", "508339536", (7765, 7770, 7760))
    _save_0922(conn, "p7760", 7760, "put", "508395276", "508395337", (7760, 7755, 7765))
    _save_0922(conn, "p7770", 7770, "put", "508472249", "508472400", (7770, 7765, 7775))
    dbmod.save_book(
        conn,
        {
            "book_id": f"{_0922}:control:SPX",
            "trade_date": _0922,
            "arm": "control",
            "symbol": "SPX",
            "pnl": 0.0,
            "fees": 0.0,
            "status": "settled",
        },
    )


def test_a_settlement_line_two_positions_share_is_split_not_counted_twice(live_conn):
    _seed_0922(live_conn)
    result = fee_reconcile.reconcile_date(live_conn, _0922, "SPX", _0922_TRANSACTIONS)
    assert sorted(result["reconciled"]) == ["c7765", "p7760", "p7770"]

    rows = {r["position_id"]: r for r in live_conn.execute("SELECT * FROM fly_positions")}
    # Each fly owns ONE of the two 7765P contracts: 0.36 apiece, not 0.72 to each.
    assert rows["c7765"]["expiry_payoff"] == pytest.approx(4.64)
    assert rows["p7760"]["expiry_payoff"] == pytest.approx(0.36)
    assert rows["p7770"]["expiry_payoff"] == pytest.approx(0.00, abs=1e-9)
    # ...and the one $5 fee on that line is split between them, not charged to each.
    assert rows["p7760"]["fees"] == pytest.approx(6.88 + 2.50)
    assert rows["p7770"]["fees"] == pytest.approx(6.88 + 2.50 + 5.00 + 5.00)
    assert rows["c7765"]["pnl"] == pytest.approx(477.12)
    assert rows["p7760"]["pnl"] == pytest.approx(56.62)
    assert rows["p7770"]["pnl"] == pytest.approx(5.62)


def test_the_book_reconciles_to_exactly_the_cash_the_broker_moved(live_conn):
    """The invariant that makes the split checkable without trusting the split: summed over every
    position, the attributed cash must equal what the broker actually moved, each line once."""
    _seed_0922(live_conn)
    fee_reconcile.reconcile_date(live_conn, _0922, "SPX", _0922_TRANSACTIONS)

    broker_cash = sum(float(t["value"]) for t in _0922_TRANSACTIONS)  # trades + settlement, once each
    broker_fees = sum(fee_reconcile._fee_total(t) for t in _0922_TRANSACTIONS)
    assert broker_cash - broker_fees == pytest.approx(539.36)

    total = live_conn.execute("SELECT SUM(pnl) FROM fly_positions").fetchone()[0]
    assert total == pytest.approx(539.36), "attributed P&L no longer equals the broker's cash"
    book = live_conn.execute("SELECT pnl FROM fly_books").fetchone()[0]
    assert book == pytest.approx(539.36)


def test_a_shared_line_without_quantities_is_left_unmatched_not_guessed(live_conn):
    """Without a quantity a shared line cannot be split. Guessing -- whole line to each, or half to
    each -- would write a confident number that nothing supports. Unmatched is retried later."""
    _seed_0922(live_conn)
    stripped = [
        {**t, "quantity": None} if t["transaction_type"] == "Receive Deliver" else t
        for t in _0922_TRANSACTIONS
    ]
    result = fee_reconcile.reconcile_date(live_conn, _0922, "SPX", stripped)

    assert "c7765" in result["reconciled"], "its lines are its own, so it still reconciles"
    assert set(result["unmatched"]) == {"p7760", "p7770"}
