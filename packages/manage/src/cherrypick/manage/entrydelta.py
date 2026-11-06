"""The delta at entry, recorded once from the first full snapshot after a call is bought.

The PMCC view (pmcc.py) recognises a PMCC by its long's delta at entry. Solved from the fill and the
entry day's close, that delta is unknown until the close reaches the snapshot's `underlying_history`,
and the close is not the price at the fill. A hand-placed fill is heard within seconds (the
orchestrator's `account-alerts` inbox), and `manage-marks` takes a full snapshot about 30 seconds
after it, so that snapshot's underlying price is the nearest the suite has to the price at the fill.
This module records the delta from it, once, and the PMCC view prefers the record to the close.

**What is recorded.** For every call bought to open on the snapshot's session that has no record
yet: the fill price, the underlying's last price in the snapshot (`metrics.<NAME>.last`, read live
by the snapshot), the gap between the fill and the snapshot, the dividend yield, the implied
volatility and the delta, solved by pmcc's own solver at the rules' rate. The broker's delta for the
leg, when the snapshot has it, is kept beside as a check, never used: the broker's greeks carry no
rate or dividend, which can move a delta near the floor.

**The gap is an approximation, so it is bounded.** The underlying moves between the fill and the
snapshot. The record carries the delta at the underlying two standard deviations either way over the
gap, at the solved volatility (`delta_low`, `delta_high`), so a delta whose band straddles the PMCC
floor says so. A gap over `max_gap_seconds` (a fill after `manage-marks` stops, a missed snapshot)
or before the fill is not recorded: that fill keeps the close-based solve.

**Recorded once, carried.** Records are kept in `entry-deltas.json` in manage's data folder, keyed
by the transaction id, and never rewritten. Each snapshot carries the records for its own rows
(`snapshot.entry_deltas`), so `reevaluate` reads the same record and gives the same answer twice.

Pure apart from `record`, which reads and writes the one store file.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from cherrypick.core import bs
from cherrypick.core.jsonio import write_json_atomic

from cherrypick.manage import pmcc as _pmcc
from cherrypick.manage import wheel as _wheel

# Trading seconds in a year, for the move over the gap (252 sessions of 6.5 hours).
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
# The band's width, in standard deviations of the underlying's move over the gap.
BAND_SIGMAS = 2.0
BASIS = "the underlying's price in the first full snapshot after the fill"


def _when(v: Any) -> datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rows in (snapshot.get("transactions") or {}).values():
        out += [r for r in rows or [] if isinstance(r, dict)]
    return out


def _bought_call(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("action") != "Buy to Open" or row.get("transaction_type") != "Trade":
        return None
    leg = _wheel.occ(_wheel._sym(row.get("symbol")))
    return leg if leg and leg["right"] == "C" else None


def solve(
    *,
    row: dict[str, Any],
    snapshot: dict[str, Any],
    rate: float,
    max_gap_seconds: float,
) -> dict[str, Any] | None:
    """The record for one fill, or None when it cannot be recorded (not a call bought to open on the
    snapshot's session, no underlying price, a gap out of bounds, or no volatility prices the fill)."""
    leg = _bought_call(row)
    filled, taken = _when(row.get("executed_at")), _when(snapshot.get("generated_at"))
    session = str(snapshot.get("session") or "")
    if leg is None or filled is None or taken is None or str(row.get("executed_at"))[:10] != session:
        return None
    gap = (taken - filled).total_seconds()
    if gap < 0 or gap > max_gap_seconds:
        return None
    underlying = str(row.get("underlying_symbol") or "")
    spot = _pmcc._num(((snapshot.get("metrics") or {}).get(underlying) or {}).get("last"))
    price = _pmcc._num(row.get("price"))
    hist = (snapshot.get("underlying_history") or {}).get(underlying)
    div = _pmcc._yield(hist, session, spot)
    days = (datetime.fromisoformat(leg["expiration"]).date() - filled.date()).days
    years = days / 365.0
    iv, delta = _pmcc._solve(spot, leg["strike"], years, price, "C", rate, div)
    if delta is None or iv is None or spot is None or div is None:
        return None
    move = spot * iv * math.sqrt(gap / TRADING_SECONDS_PER_YEAR) * BAND_SIGMAS
    low = bs.delta(spot - move, leg["strike"], years, iv, "C", rate, div)
    high = bs.delta(spot + move, leg["strike"], years, iv, "C", rate, div)
    # The broker's keys keep the OCC padding ("XYZ   280121C..."); compare them as the rows are compared.
    greeks = {_wheel._sym(k): v for k, v in (snapshot.get("broker_greeks") or {}).items()}
    broker = (greeks.get(_wheel._sym(row.get("symbol"))) or {}).get("delta")
    return {
        "id": str(row.get("id")),
        "symbol": _wheel._sym(row.get("symbol")),
        "underlying": underlying,
        "executed_at": row.get("executed_at"),
        "price": price,
        "spot": spot,
        "spot_at": snapshot.get("generated_at"),
        "gap_seconds": round(gap, 1),
        "div_yield": div,
        "iv": iv,
        "delta": delta,
        "delta_low": low,
        "delta_high": high,
        "broker_delta": _pmcc._num(broker),
        "basis": BASIS,
        "session": session,
    }


def new_records(
    snapshot: dict[str, Any], rules: dict[str, Any], known: set[str]
) -> dict[str, dict[str, Any]]:
    """{transaction id: record} for the snapshot's fills that have none yet and can be recorded."""
    cfg = rules["pmcc"]["entry_record"]
    if not cfg.get("enabled", True):
        return {}
    rate = float(rules["pricing"]["rate"])
    out: dict[str, dict[str, Any]] = {}
    for row in _rows(snapshot):
        rid = str(row.get("id") or "")
        if not rid or rid in known or rid in out:
            continue
        rec = solve(row=row, snapshot=snapshot, rate=rate, max_gap_seconds=float(cfg["max_gap_seconds"]))
        if rec is not None:
            out[rid] = rec
    return out


def carried(store: dict[str, dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The stored records for the snapshot's own transaction rows."""
    ids = {str(r.get("id") or "") for r in _rows(snapshot)}
    return {rid: rec for rid, rec in store.items() if rid in ids}


def record(snapshot: dict[str, Any], rules: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    """Add the snapshot's new records to the store at `path` (never rewriting one) and return the
    records its rows carry. A store that cannot be read is left alone and nothing new is written to it,
    so a damaged file never loses the records it holds; the snapshot then carries what it can solve."""
    store: dict[str, dict[str, Any]] = {}
    readable = True
    if path.exists():
        try:
            store = dict(json.loads(path.read_text(encoding="utf-8")).get("records") or {})
        except (OSError, ValueError, AttributeError):
            readable = False
    fresh = new_records(snapshot, rules, set(store))
    if fresh and readable:
        write_json_atomic(path, {"records": {**store, **fresh}})
    return carried({**store, **fresh}, snapshot)
