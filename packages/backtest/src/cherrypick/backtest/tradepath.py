"""One trade's path through its session: what the position was worth at each stored sample, with spot
and VIX beside it, from entry to its last exit.

MEIC's condors only (`meic_ic`): the run's ledger names the four strikes, the entry time and each
side's exit (time, price, reason), and the vendor store holds every strike's quote at each sample. A
side's mark is its short leg's mid less its long leg's, at mid and before any fill model, so the path
shows the market the trade sat in, not a second replay of it. After a side's exit its mark is its own
recorded exit price; a sample missing a leg's quote is a gap (None), never a neighbour's value.

`path` is a pure function over a trade, its sides and the session's samples. `for_trade` reads them
from the run and the store it read. Nothing here is a trial, and nothing reaches the advisor.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from cherrypick.core import clock as _clock
from cherrypick.core import db as core_db
from cherrypick.core import quotehistory

from . import paths, store
from . import settings as _settings

#: The schema whose ledgers this reads; other modules' trades have no path yet.
SCHEMA = "meic"
SYMBOL = "SPX"


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def _mid(chain: dict, strike: float) -> float | None:
    q = chain.get(strike)
    return None if q is None or q.get("mid") is None else float(q["mid"])


def _side_mark(snap: dict, side: str, short: float, wing: float) -> float | None:
    """The cost to close one vertical at mid: the short leg's mid less the long leg's."""
    chain = snap["puts"] if side == "put" else snap["calls"]
    long_strike = short - wing if side == "put" else short + wing
    s, lg = _mid(chain, short), _mid(chain, long_strike)
    return None if s is None or lg is None else round(s - lg, 4)


def _vix_at(vix: list[tuple[float, float]], ts: float) -> float | None:
    """The last usable VIX reading at or before `ts` (no look-ahead)."""
    best = None
    for when, value in vix:
        if when > ts:
            break
        best = value
    return best


def path(
    trade: dict, sides: list[dict], samples: list[dict], vix: list[tuple[float, float]] | None = None
) -> dict:
    """The trade's points from entry to its last exit. `trade` is its `ic_trades` row, `sides` its
    `ic_spread_legs` rows, `samples` the session's stored samples (`core.quotehistory.session`)."""
    mult = float(trade.get("dollar_multiplier") or 100.0) * float(trade.get("quantity") or 1)
    wing = float(trade.get("wing_width") or 0.0)
    legs = {
        "put": (
            float(trade["put_strike"]),
            float(trade.get("put_wing") or wing),
            float(trade.get("put_credit") or 0.0),
        ),
        "call": (
            float(trade["call_strike"]),
            float(trade.get("call_wing") or wing),
            float(trade.get("call_credit") or 0.0),
        ),
    }
    exits = {s["side"]: s for s in sides if s.get("side") in legs}
    start = _epoch(trade.get("entry_time"))
    ends = [_epoch(s.get("exit_time")) for s in exits.values()] or [_epoch(trade.get("exit_time"))]
    end = max((e for e in ends if e is not None), default=None)
    points = []
    for snap in samples:
        ts = snap["ts"]
        if start is None or ts < start or (end is not None and ts > end):
            continue
        point = {
            "ts": ts,
            "time": datetime.fromtimestamp(ts, _clock.ET).strftime("%H:%M"),
            "spot": snap.get("underlying_price"),
        }
        pnl: float | None = 0.0
        for side, (short, w, credit) in legs.items():
            ex = exits.get(side)
            closed = (
                ex is not None and _epoch(ex.get("exit_time")) is not None and ts >= _epoch(ex["exit_time"])
            )
            mark = float(ex.get("exit_price") or 0.0) if closed else _side_mark(snap, side, short, w)
            point[f"{side}_mark"] = mark
            pnl = None if pnl is None or mark is None else pnl + (credit - mark) * mult
        point["pnl"] = None if pnl is None else round(pnl, 2)
        point["vix"] = _vix_at(vix or [], ts)
        points.append(point)
    return {
        "strikes": {
            "put": [legs["put"][0] - legs["put"][1], legs["put"][0]],
            "call": [legs["call"][0], legs["call"][0] + legs["call"][1]],
        },
        "credit": {"put": legs["put"][2], "call": legs["call"][2]},
        "entry_time": trade.get("entry_time"),
        "exits": [
            {
                "side": side,
                "time": ex.get("exit_time"),
                "reason": ex.get("exit_reason"),
                "price": ex.get("exit_price"),
            }
            for side, ex in sorted(exits.items())
        ],
        "points": points,
        "gaps": sum(1 for p in points if p["pnl"] is None),
    }


def _row(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


def _store_for(report: dict) -> Path:
    """The store a run read: the file its report names (from 2026-10-31), else the untagged file at
    its cadence, which every run before then that was not a tagged study read."""
    s = _settings.load(None)
    named = report.get("store")
    if named:
        return paths.vendor_dir(s) / Path(named).name
    return store.db_path(s, SYMBOL, report.get("cadence"))


def _vix(db: Path, day: str) -> list[tuple[float, float]]:
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "market_regime_history" not in have:
            return []
        return [
            (ts, value)
            for ts, value in conn.execute(
                "SELECT ts, value FROM market_regime_history WHERE trade_date = ? AND reading = 'vix' "
                "AND usable = 1 AND value IS NOT NULL ORDER BY ts",
                (day,),
            )
        ]
    finally:
        conn.close()


def for_trade(run_id: str, arm: str, session: str, position_id: str) -> dict:
    """One trade's path, read from the run's ledger for `session` and the store the run read."""
    runs = paths.results_dir() / "runs"
    report_path = runs / f"{run_id}.json"
    if not run_id.replace("-", "").isalnum() or not report_path.exists():
        return {"ok": False, "error": f"no run {run_id!r}"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("module") or SCHEMA) != SCHEMA:
        return {"ok": False, "error": "a trade's path is read for MEIC's condors only"}
    if arm not in (report.get("arms") or {}):
        return {"ok": False, "error": f"run {run_id} has no arm {arm!r}"}
    ledger = runs / run_id / f"{session}.db"
    if not session.replace("-", "").isdigit() or not ledger.exists():
        return {"ok": False, "error": f"run {run_id} kept no ledger for {session!r}"}
    conn = sqlite3.connect(f"file:{ledger.as_posix()}?mode=ro", uri=True)
    try:
        arm_col = core_db.arm_column(conn, "ic_trades")  # `risk_profile` in an older ledger
        trades = _row(
            conn, f"SELECT * FROM ic_trades WHERE ic_order_id = ? AND {arm_col} = ?", (position_id, arm)
        )
        sides = _row(conn, "SELECT * FROM ic_spread_legs WHERE ic_order_id = ?", (position_id,))
    finally:
        conn.close()
    if not trades:
        return {"ok": False, "error": f"no trade {position_id!r} for {arm} on {session}"}
    db = _store_for(report)
    samples = quotehistory.session(SYMBOL, session, history_db=db)
    if not samples:
        return {"ok": False, "error": f"the store {db.name} holds no samples for {session}"}
    out = path(trades[0], sides, samples, _vix(db, session))
    return {
        "ok": True,
        "run_id": run_id,
        "arm": arm,
        "session": session,
        "position_id": position_id,
        "store": db.name,
        **out,
    }
