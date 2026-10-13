"""scripts/flies_stranded_replay.py: the salvage grid is refused unless its pricing reproduces the ledger.

A rescued spread is valued by completing it at `credit + x` through the same function that, at a
completed row's own debit, must give back that row's recorded P&L. The rows are real live control
fills (2026-09-28 / 09-29); the fee schedule is read from the module, not copied.
"""

import importlib.util
from pathlib import Path

import pytest

from cherrypick.flies import db as dbmod

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_stranded_replay.py"
DAY = "2026-09-28"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("flies_stranded_replay", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _position(conn, **row):
    base = {
        "book_id": f"{row['position_id']}-book",
        "trade_date": DAY,
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "side": "put",
        "wing_width": 5.0,
        "quantity": 1,
        "status": "settled",
    }
    row = {**base, **row}
    conn.execute(
        f"INSERT INTO fly_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()


# A put fly 7700/7695/7690 completed for a 0.25 credit, every strike in the money at 7683.69: the
# module's table charges three settlement events, $15.00.
MODULE_SETTLED = {
    "position_id": "live-control-7695-module",
    "kind": "fly",
    "center": 7695.0,
    "net": 0.25,
    "credit": 2.2,
    "debit": 1.95,
    "fees": 21.89,
    "settlement_fees": 15.0,
    "settlement_price": 7683.69,
    "entry_time": f"{DAY}T10:39:04-04:00",
    "completed_at": f"{DAY}T10:45:00-04:00",
    "completion_latency_min": 6.0,
    "pnl": 3.11,
}
# The same fly as the broker really settled it on 2026-09-28: $12.50 at the print, not $15.00.
BROKER_SETTLED = {
    **MODULE_SETTLED,
    "position_id": "live-control-7695-broker",
    "fees": 19.38,
    "settlement_fees": 12.5,
    "broker_reconciled_at": "2026-09-29T09:00:00-04:00",
    "pnl": 5.62,
}
# A 7675/7670 put spread that never completed and expired worthless.
STRANDED = {
    "position_id": "live-control-7675-stranded",
    "kind": "short_vertical",
    "center": 7675.0,
    "net": 2.45,
    "credit": 2.45,
    "fees": 3.44,
    "settlement_fees": 0.0,
    "settlement_price": 7683.69,
    "entry_time": f"{DAY}T11:01:46-04:00",
    "pnl": 241.56,
}


@pytest.fixture
def ledger(managed_home):
    root = managed_home / "data" / "flies"
    root.mkdir(parents=True)
    live = dbmod.connect(str(root / "live_trades.db"))
    dbmod.connect(str(root / "paper_trades.db")).close()  # spot paths are read from paper
    return live


def _main(script, *extra):
    return script.main(["--ledger", "live", "--since", DAY, *extra])


def test_a_ledger_the_pricing_reproduces_is_reported(script, ledger, capsys):
    _position(ledger, **MODULE_SETTLED)
    _position(ledger, **STRANDED)
    assert script.baseline_mismatches(script.load(ledger, "control", "SPX", DAY, None)) == []
    assert _main(script) == 0
    out = capsys.readouterr().out
    assert "== live control" in out and "x=0.25 T=90" in out


def test_a_completed_row_the_pricing_misses_refuses_the_grid(script, ledger, capsys):
    """Break the ledger on purpose: a recorded P&L a dollar off what its own parts give."""
    _position(ledger, **{**MODULE_SETTLED, "pnl": 4.11})
    _position(ledger, **STRANDED)
    missed = script.baseline_mismatches(script.load(ledger, "control", "SPX", DAY, None))
    assert [m["position_id"] for m in missed] == ["live-control-7695-module"]
    assert _main(script) == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and "x=0.25" not in out
    # Without the grid nothing modelled is printed, so nothing is refused.
    assert _main(script, "--no-salvage") == 0


def test_a_rescue_priced_off_the_modules_settlement_table_misses_a_broker_settled_row(script, ledger, capsys):
    """The live ledger's finding on 2026-10-12: the broker charged $12.50 at the print where the
    module's table says $15.00, so a completion priced from the table misses that row by $2.50."""
    _position(ledger, **BROKER_SETTLED)
    _position(ledger, **STRANDED)
    missed = script.baseline_mismatches(script.load(ledger, "control", "SPX", DAY, None))
    assert [(m["position_id"], round(m["replayed"] - m["recorded"], 2)) for m in missed] == [
        ("live-control-7695-broker", -2.5)
    ]
    assert _main(script) == 2
