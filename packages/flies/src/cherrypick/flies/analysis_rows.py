"""The rows the read-side replays work from, and the one way a row's `fees` is taken apart.

`scripts/flies_*_replay.py` each loaded settled positions with their own query and each took the
entry's share of `fees` its own way (half the total, the total less settlement less a modelled
completion fee, a median over other rows, the modelled fee alone). They disagreed by the settlement
charge on every row that settled in the money. This module is the one answer. Read-only; pure apart
from `open_ledger` / `settled_positions`.

**What `fees` holds** (db.py, `engine.settle`, `live_loop`, `fee_reconcile`). The TOTAL the
position paid: the entry trade's fee, plus the completion's (legged/debit_first) or the roll's
(bwb), each added when that trade fills, plus the exercise/assignment charge folded in once at
settlement. `settlement_fees` records that last part beside the total, never instead of it; NULL
means it was never recorded -- on paper only before 2026-09-24 (the backfill script filled those),
on live wherever `fee_reconcile` replaced `fees` with the broker's total before the column existed
(20 live rows, 2026-07-30..09-23, which the backfill deliberately left NULL).

**The split.**

- `settlement_fees(row)`: the recorded part when there is one. Unrecorded, on a settled row:
  - broker-reconciled (live): the total less the trades' fees at the broker's per-order cents.
    The broker's settlement charge is not the module's table (it differs from `fly.assignment_fee`
    on 24 of the 68 reconciled live rows that record it, e.g. $7.50 against $10.00), but its trade
    fees are: on all 68, the total less the cent-rounded modelled trade fees IS the recorded
    settlement figure (checked 2026-10-12). So the remainder is the print's.
  - otherwise: `fly.assignment_fee` at the row's own print -- the same function `engine.settle`
    folded into `fees`, so it is what was charged.

  An open row has paid none.
- The rest (`trade_fees`) is the trades', shared between them in proportion to each trade's
  modelled fee (`trade_shapes`). Entry and completion are the same two-leg shape, so a completed
  legged fly's entry is half its trade fees, and it stays right on rows priced under an older fee
  schedule (July's XSP rows paid 2.49 + 2.48 where today's table says 2.24 each), where subtracting
  today's modelled completion fee would not.

`closed_before_expiry` rows (the pre-close exit removed 2026-08-01) folded an unrecorded close fee
into `fees`; there is no honest split of them, so `fee_split` refuses one and the loader leaves them
out unless asked.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cherrypick.core.db import connect_ro

from cherrypick.flies import db as dbmod
from cherrypick.flies import fly

# How `settlement_fees` was arrived at, carried on every split so a reader can say which.
RECORDED, DERIVED, MODELLED, NONE_YET = "recorded", "derived", "modelled", "not_settled"


# --------------------------------------------------------------------------- loading
def ledger_path(name: str) -> Path:
    """`paper` or `live`, off `$CHERRYPICK_HOME` (default `~/.cherrypick`)."""
    if name == "paper":
        return Path(dbmod.default_db_path())
    if name == "live":
        return Path(dbmod.live_db_path())
    raise ValueError(f"unknown ledger {name!r} (paper or live)")


def open_ledger(name_or_path: str | Path) -> sqlite3.Connection:
    """A read-only connection (rows as `sqlite3.Row`) to a ledger by name or path."""
    path = ledger_path(name_or_path) if name_or_path in ("paper", "live") else Path(name_or_path)
    return connect_ro(path)


def settled_positions(
    conn: sqlite3.Connection,
    *,
    arm: str | None = None,
    symbol: str | None = "SPX",
    since: str | None = None,
    until: str | None = None,
    entry_mode: str | None = None,
    include_closed_early: bool = False,
) -> list[dict]:
    """Settled, unvoided positions in entry order, as plain dicts with every column.

    `None` for a filter means any. Rows that closed before expiry are left out by default: they are
    not comparable to settled ones (db.py) and their fees cannot be split."""
    where = ["status = 'settled'", "void_reason IS NULL"]
    params: list = []
    for col, val in (("arm", arm), ("symbol", symbol), ("entry_mode", entry_mode)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    if since is not None:
        where.append("trade_date >= ?")
        params.append(since)
    if until is not None:
        where.append("trade_date <= ?")
        params.append(until)
    if not include_closed_early:
        where.append("COALESCE(closed_before_expiry, 0) = 0")
    q = f"SELECT * FROM fly_positions WHERE {' AND '.join(where)} ORDER BY entry_time"
    return [dict(r) for r in conn.execute(q, params)]


# --------------------------------------------------------------------------- the fee split
def _qty(row: dict) -> int:
    return int(row.get("quantity") or 1)


def _symbol(row: dict) -> str:
    return row.get("symbol") or "SPX"


def completed(row: dict) -> bool:
    """Whether the row's second trade (completion, or bwb's roll) filled: its kind says so."""
    return row["kind"] in ("fly", "iron_fly")


def trade_shapes(row: dict) -> dict[str, float]:
    """Each trade the row made, with the module's own modelled fee for it.

    legged / debit_first: a two-leg vertical to enter and another to complete; outright: a fly
    bought whole; bwb_roll: a fly opened whole and a vertical to roll its far wing in. A row written
    before `entry_mode` existed was legged (the only mode there was)."""
    mode = row.get("entry_mode") or "legged"
    sym, qty = _symbol(row), _qty(row)
    vertical = fly.vertical_open_fee(sym, qty)
    if mode in ("legged", "debit_first"):
        out = {"entry": vertical}
        if completed(row):
            out["completion"] = vertical
        return out
    if mode == "outright":
        return {"entry": fly.fly_open_fee(sym, qty)}
    if mode == "bwb_roll":
        out = {"entry": fly.fly_open_fee(sym, qty)}
        if row["kind"] == "fly":
            out["roll"] = vertical
        return out
    raise ValueError(f"no fee shape for entry_mode {mode!r}")


def _refuse_closed_early(row: dict) -> None:
    if row.get("closed_before_expiry"):
        raise ValueError(
            f"{row.get('position_id')}: closed before expiry -- its fees hold an unrecorded close fee"
        )


def settlement_fees_with_source(row: dict) -> tuple[float, str]:
    """The part of `fees` paid at the print, and how it was arrived at (see the module docstring)."""
    _refuse_closed_early(row)
    if row.get("settlement_fees") is not None:
        return float(row["settlement_fees"]), RECORDED
    if row.get("status") not in (None, "settled") or row.get("settlement_price") is None:
        return 0.0, NONE_YET
    total = float(row.get("fees") or 0.0)
    if row.get("broker_reconciled_at") or row.get("broker_reconciliation_status") == "reconciled":
        traded = sum(round(f, 2) for f in trade_shapes(row).values())
        return round(max(0.0, total - traded), 2), DERIVED
    fee = round(fly.assignment_fee(row, row["settlement_price"]), 2)
    if fee > total + 0.005:
        raise ValueError(
            f"{row.get('position_id')}: modelled settlement fee {fee} exceeds the recorded total {total}"
        )
    return fee, MODELLED


def settlement_fees(row: dict) -> float:
    """The part of `fees` paid at the print."""
    return settlement_fees_with_source(row)[0]


def trade_fees(row: dict) -> float:
    """The part of `fees` the trades paid: the total less the print's."""
    return float(row.get("fees") or 0.0) - settlement_fees(row)


def fee_split(row: dict) -> dict:
    """`fees` taken apart: entry, completion, roll, settlement, plus `settlement_source`.

    The parts add back to `fees` exactly. A trade the row never made reads 0.0."""
    settle, source = settlement_fees_with_source(row)
    traded = float(row.get("fees") or 0.0) - settle
    shapes = trade_shapes(row)
    modelled = sum(shapes.values())
    out = {k: traded * v / modelled for k, v in shapes.items()}
    return {
        "entry": out["entry"],
        "completion": out.get("completion", 0.0),
        "roll": out.get("roll", 0.0),
        "settlement": settle,
        "settlement_source": source,
    }


def entry_fees(row: dict) -> float:
    """Fees the entry trade paid: what the position had paid before anything else happened to it."""
    return fee_split(row)["entry"]


def completion_fees(row: dict) -> float:
    """Fees the completing trade paid; 0.0 on a row that never completed (or a bwb's roll: see
    `fee_split`'s `roll`)."""
    return fee_split(row)["completion"]


# --------------------------------------------------------------------------- replaying a legged row
def open_cash(row: dict) -> float:
    """Per-contract cash a legged row held as a spread: its opening credit. A row that completed
    carries net = credit - debit, so the debit is put back."""
    return row["net"] + ((row.get("debit") or 0.0) if completed(row) else 0.0)


def completed_pnl(row: dict, debit: float) -> float:
    """Settled P&L of a legged row completed into a fly for `debit` points, at its own print.

    Built from what the row really paid: its entry fee, its own completion fee where it completed
    (else the module's), and the print's charge it really paid plus only the settlement events the
    completing strike adds (the module's table: the fly's ITM strikes less the row's own). So a
    row that did complete, replayed at its own debit, gives back its recorded `pnl` -- the broker's
    settlement charge is not the module's table, and charging the module's for the whole fly
    misses a live row by $2.50 to $5.00 (`ledger_mismatches`)."""
    qty, sp = _qty(row), row["settlement_price"]
    split = fee_split(row)
    completion_fee = split["completion"] if completed(row) else fly.vertical_open_fee(_symbol(row), qty)
    fly_row = {
        "kind": "fly",
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "quantity": qty,
        "net": open_cash(row) - debit,
        "status": "settled",
        "settlement_price": sp,
    }
    # The recorded charge is for the row's own shape at the print: a completed row's already is the fly's.
    added = fly.assignment_fee(fly_row, sp) - fly.assignment_fee(row, sp)
    fly_row["fees"] = split["entry"] + completion_fee + split["settlement"] + added
    return fly.position_pnl(fly_row, sp)


def held_pnl(row: dict) -> float:
    """Settled P&L of an uncompleted legged row held as its spread to the print, from the same parts
    `completed_pnl` builds from -- so its recorded `pnl` checks them."""
    if completed(row):
        raise ValueError(f"{row.get('position_id')}: completed; its print was the fly's, not the spread's")
    split = fee_split(row)
    spread = {
        "kind": "short_vertical",
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "quantity": _qty(row),
        "net": open_cash(row),
        "fees": split["entry"] + split["settlement"],
        "status": "settled",
        "settlement_price": row["settlement_price"],
    }
    return fly.position_pnl(spread, row["settlement_price"])


def ledger_mismatches(rows: list[dict], value, *, tolerance: float = 0.01) -> list[dict]:
    """Every settled row whose replayed value (`value(row)`, the replay's OWN pricing of the row as
    it really went) is not its recorded `pnl` to `tolerance` dollars. A replay whose baseline does
    not reproduce the ledger is comparing its policies against something that never happened, so
    a non-empty answer is a reason to refuse, never a footnote. Rows without a `pnl` or a print are
    skipped (nothing to reproduce)."""
    out = []
    for r in rows:
        if r.get("pnl") is None or r.get("settlement_price") is None:
            continue
        got = value(r)
        # To the cent: the ledger rounds `fees` and `pnl` to cents, so a cent either way is rounding.
        if abs(round(got - r["pnl"], 2)) > tolerance:
            out.append(
                {
                    "position_id": r.get("position_id"),
                    "trade_date": r.get("trade_date"),
                    "kind": r["kind"],
                    "recorded": r["pnl"],
                    "replayed": round(got, 2),
                    "problem": f"replayed {got:+.2f}, ledger {r['pnl']:+.2f} ({got - r['pnl']:+.2f})",
                }
            )
    return out
