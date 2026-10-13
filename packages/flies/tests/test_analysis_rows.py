"""analysis_rows: the settled-row loader and the one split of a row's `fees`.

The fee figures are real ledger rows where one exists (live control, 2026-09-22..09-29; paper XSP,
July), because the split is only worth what it says about the rows the replays actually read.
"""

import sqlite3

import pytest

from cherrypick.flies import analysis_rows as ar
from cherrypick.flies import db as dbmod
from cherrypick.flies import fly

PRINT = 7670.84  # through both strikes of a 7685/7680 put spread, all three of a 7690/7685/7680 put fly
VERTICAL = fly.vertical_open_fee("SPX")


def row(**over):
    base = {
        "position_id": "p",
        "kind": "short_vertical",
        "entry_mode": "legged",
        "side": "put",
        "center": 7685.0,
        "wing_width": 5.0,
        "quantity": 1,
        "symbol": "SPX",
        "status": "settled",
        "settlement_price": PRINT,
        "settlement_fees": None,
    }
    return {**base, **over}


def parts_add_up(r):
    s = ar.fee_split(r)
    assert s["entry"] + s["completion"] + s["roll"] + s["settlement"] == pytest.approx(r["fees"])
    return s


# --------------------------------------------------------------------------- recorded settlement
def test_a_completed_fly_shares_its_trade_fees_between_entry_and_completion_never_the_print():
    # Live 2026-09-28: 19.38 in all, 12.50 of it the broker's settlement charge.
    r = row(kind="fly", center=7695.0, fees=19.38, settlement_fees=12.5, broker_reconciled_at="x")
    s = parts_add_up(r)
    assert s["settlement"] == 12.5 and s["settlement_source"] == ar.RECORDED
    assert s["entry"] == pytest.approx(3.44) and s["completion"] == pytest.approx(3.44)
    # Half the total -- the old live-start split -- would have put 6.25 of the print in the entry.
    assert s["entry"] != pytest.approx(r["fees"] / 2)


def test_an_uncompleted_spread_paid_one_trade_and_the_print():
    r = row(fees=10.94, settlement_fees=7.5)
    s = parts_add_up(r)
    assert s["entry"] == pytest.approx(3.44)
    assert s["completion"] == 0.0 and s["roll"] == 0.0
    assert ar.trade_fees(r) == pytest.approx(3.44)


def test_a_row_priced_under_an_older_schedule_is_split_by_shape_not_by_todays_fee():
    # Paper XSP, July: 2.49 to enter and 2.48 to complete, where today's table says 2.24 each.
    r = row(
        kind="fly",
        symbol="XSP",
        center=744.0,
        wing_width=1.0,
        settlement_price=750.0,
        fees=4.97,
        settlement_fees=0.0,
    )
    s = parts_add_up(r)
    assert s["entry"] == pytest.approx(2.485)
    # Subtracting today's modelled completion fee would have charged the entry 2.73.
    assert s["entry"] != pytest.approx(4.97 - fly.vertical_open_fee("XSP"))


# --------------------------------------------------------------------------- unrecorded settlement
def test_an_unreconciled_row_without_it_is_charged_the_modules_own_fee_at_its_print():
    # Both strikes ITM: two settlement events, the same $10 engine.settle folded into `fees`.
    r = row(fees=round(VERTICAL + 10.0, 2))
    fee, how = ar.settlement_fees_with_source(r)
    assert (fee, how) == (fly.assignment_fee(r, PRINT), ar.MODELLED) == (10.0, ar.MODELLED)
    assert parts_add_up(r)["entry"] == pytest.approx(round(VERTICAL + 10.0, 2) - 10.0)


@pytest.mark.parametrize(
    "kind, fees, broker_settlement",
    [
        # Live 2026-09-23 and 09-22, reconciled before `settlement_fees` existed. The broker charged
        # $7.50 / $2.50 where the module's table says $10 / $5 -- the model is not the broker.
        ("short_vertical", 10.94, 7.5),
        ("fly", 9.38, 2.5),
        ("fly", 6.88, 0.0),
    ],
)
def test_a_reconciled_row_without_it_takes_the_print_as_the_total_less_the_broker_trade_fees(
    kind, fees, broker_settlement
):
    r = row(kind=kind, fees=fees, broker_reconciled_at="2026-09-24T09:00:00-04:00")
    fee, how = ar.settlement_fees_with_source(r)
    assert how == ar.DERIVED
    assert fee == broker_settlement
    assert fee != fly.assignment_fee(r, PRINT) or broker_settlement == 0.0
    parts_add_up(r)


def test_an_open_row_has_paid_nothing_at_the_print():
    r = row(status="open", settlement_price=None, fees=VERTICAL)
    assert ar.settlement_fees_with_source(r) == (0.0, ar.NONE_YET)
    assert ar.entry_fees(r) == pytest.approx(VERTICAL)


def test_a_modelled_fee_larger_than_the_total_is_refused_not_split():
    with pytest.raises(ValueError, match="exceeds the recorded total"):
        ar.settlement_fees(row(fees=VERTICAL))


def test_a_row_closed_before_expiry_is_refused():
    with pytest.raises(ValueError, match="closed before expiry"):
        ar.fee_split(row(fees=12.0, settlement_fees=0.0, closed_before_expiry=1))


# --------------------------------------------------------------------------- the other entry modes
def test_debit_first_splits_like_legged():
    unfilled = row(kind="long_vertical", entry_mode="debit_first", fees=3.44, settlement_fees=0.0)
    filled = row(kind="fly", entry_mode="debit_first", fees=6.89, settlement_fees=0.0)
    assert parts_add_up(unfilled)["entry"] == pytest.approx(3.44)
    assert parts_add_up(filled)["entry"] == pytest.approx(6.89 / 2)


def test_a_bwb_opens_a_whole_fly_and_rolls_one_vertical():
    open_fee = fly.fly_open_fee("SPX")
    unrolled = row(kind="bwb", entry_mode="bwb_roll", fees=round(open_fee, 2), settlement_fees=0.0)
    s = parts_add_up(unrolled)
    assert s["entry"] == pytest.approx(round(open_fee, 2)) and s["roll"] == 0.0
    rolled = row(
        kind="fly", entry_mode="bwb_roll", fees=round(open_fee + VERTICAL, 2) + 5.0, settlement_fees=5.0
    )
    s = parts_add_up(rolled)
    assert s["roll"] / s["entry"] == pytest.approx(VERTICAL / open_fee)
    assert s["completion"] == 0.0


def test_an_outright_fly_paid_one_trade():
    r = row(kind="fly", entry_mode="outright", fees=6.89, settlement_fees=0.0)
    assert parts_add_up(r)["entry"] == pytest.approx(6.89)


def test_a_row_from_before_entry_mode_was_recorded_is_legged():
    r = row(kind="fly", fees=6.88, settlement_fees=0.0)
    r.pop("entry_mode")
    assert ar.entry_fees(r) == pytest.approx(3.44)


def test_an_unknown_entry_mode_is_refused():
    with pytest.raises(ValueError, match="no fee shape"):
        ar.fee_split(row(entry_mode="ratio", fees=3.44, settlement_fees=0.0))


# --------------------------------------------------------------------------- the loader
def _insert(conn, **over):
    r = {
        "book_id": "b",
        "trade_date": "2026-10-01",
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "kind": "short_vertical",
        "side": "put",
        "center": 7685.0,
        "wing_width": 5.0,
        "quantity": 1,
        "net": 2.0,
        "credit": 2.0,
        "fees": 3.44,
        "status": "settled",
        "settlement_price": 7700.0,
        "entry_time": "2026-10-01T10:15:00-04:00",
        **over,
    }
    conn.execute(
        f"INSERT INTO fly_positions ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values())
    )
    conn.commit()


def test_the_loader_keeps_settled_unvoided_rows_in_entry_order_read_only(tmp_path):
    path = tmp_path / "paper_trades.db"
    w = dbmod.connect(str(path))
    _insert(w, position_id="late", entry_time="2026-10-01T11:00:00-04:00")
    _insert(w, position_id="early")
    _insert(w, position_id="open", status="open", settlement_price=None)
    _insert(w, position_id="void", void_reason="bad legs")
    _insert(w, position_id="closed", closed_before_expiry=1)
    _insert(w, position_id="other-arm", arm="gex")
    _insert(w, position_id="xsp", symbol="XSP")
    _insert(w, position_id="before", trade_date="2026-09-30")
    _insert(w, position_id="debit", entry_mode="debit_first", kind="long_vertical")
    w.close()

    conn = ar.open_ledger(path)
    ids = [r["position_id"] for r in ar.settled_positions(conn, arm="control", since="2026-10-01")]
    assert ids == ["early", "debit", "late"]
    legged = ar.settled_positions(conn, arm="control", since="2026-10-01", entry_mode="legged")
    assert [r["position_id"] for r in legged] == ["early", "late"]
    every_symbol = ar.settled_positions(conn, arm="control", symbol=None, until="2026-10-01")
    assert {r["position_id"] for r in every_symbol} == {"early", "late", "xsp", "before", "debit"}
    with_closed = ar.settled_positions(conn, arm="control", since="2026-10-01", include_closed_early=True)
    assert "closed" in {r["position_id"] for r in with_closed}
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM fly_positions")


def test_ledgers_resolve_off_the_cherrypick_home(managed_home):
    assert ar.ledger_path("live") == managed_home / "data" / "flies" / "live_trades.db"
    assert ar.ledger_path("paper") == managed_home / "data" / "flies" / "paper_trades.db"
    with pytest.raises(ValueError):
        ar.ledger_path("shadow")
