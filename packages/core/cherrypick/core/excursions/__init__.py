"""One closed position's mark path, and its best and worst point: the excursion.

docs/strategy-analysis-plan.md: "best and worst excursion per position, with the time each happened,
from the marks tables". How far a trade went for and against before it ended is what an exit rule
is tuned against, and the realised P&L alone cannot show it.

Every module records marks differently, so each path says which BASIS it is in, and a reader puts
it beside the trade's own figures on that basis only:

- `gross_at_mid` -- bwb, calendars, pmcc, curve. Built here from the module's own legs and per-leg
  marks: at each recorded tick, every leg closed by then at its close value, every leg still open at
  that tick's mid, sold legs inverted, x 100 x the leg's quantity, plus the share P&L of any
  assignment disposed of by then. That is the rule the module's own gross is (fills at mid,
  slippage charged as a cost), so the path's endpoint must equal the recorded gross, and `endpoint`
  says whether it does: on 2026-10-17 it did for all 289 closed positions across the four modules,
  once assignments were counted. A tick at which any open leg had no usable mark is refused, never
  priced from a stale leg.
- `gross_as_recorded` -- earnings: its own `position_marks.unrealized_pnl` (usable rows only),
  ending at the trade's gross `pnl` at its close. Over every earnings trade that stores one, the
  path's max and min equal the module's own `max_unrealized_pnl` / `min_unrealized_pnl` (259 of
  259 on 2026-10-17).
- `net_of_fees_as_recorded` -- flies LIVE: `fly_live_marks.mark_pnl`, which the live loop writes
  net of entry fees and before settlement, ending at the trade's net (settlement included) at its
  exit. Flies paper records no position marks.

MEIC records only each side's worst spot and cost, and contango only a daily fund mid, so neither has
a path: they read `recorded: false` with the reason, never a guessed path.

The entry point (0 at the entry mid, by construction) and the endpoint are part of the path, so a
trade that only ever lost has a best of 0 at entry. Pure reads, no clock, no network.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from cherrypick.core import ledgers
from cherrypick.core.clock import ET

#: Leg-marked schemas: (positions table, legs table, marks table, assignments table or None).
LEG_SCHEMAS = {
    "bwb_132": ("bwb_positions", "bwb_legs", "bwb_marks", None),
    "dc_week": ("dc_positions", "dc_legs", "dc_marks", "dc_assignments"),
    "pmcc": ("pmcc_positions", "pmcc_legs", "pmcc_marks", "pmcc_assignments"),
    "curve_vx": ("curve_positions", "curve_legs", "curve_marks", "curve_assignments"),
}

NOT_RECORDED = {
    "meic_ic": "MEIC records each side's worst spot and cost, not a position mark path",
    "contango_etf": "contango records one fund mid a day, not a position mark path",
}

#: The path handed back is thinned to about this many points; best, worst, entry and end are kept.
MAX_POINTS = 400

_MULTIPLIER = 100.0


def _ts(value) -> datetime | None:
    """An aware datetime from an ISO stamp or epoch seconds (the marks tables write epoch seconds,
    the legs ISO text); a naive ISO stamp is read as ET, the zone every module stamps in."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), ET)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(value), ET)
        except (OverflowError, OSError, ValueError):
            return None
    return t.replace(tzinfo=ET) if t.tzinfo is None else t


def _iso(value) -> str | None:
    t = _ts(value)
    return None if t is None else t.astimezone(ET).isoformat(timespec="seconds")


_EPOCH = datetime.fromtimestamp(0, ET)


def _cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _sign(action) -> float:
    return -1.0 if str(action) == "Sell to Open" else 1.0


def _summary(points: list[tuple[str, float]], basis: str, ticks: int, refused: int, **extra) -> dict:
    """Best and worst over the path, and the path thinned for drawing."""
    if not points:
        return {"recorded": True, "basis": basis, "ticks": ticks, "refused_ticks": refused,
                "best": None, "worst": None, "path": [], **extra}  # fmt: skip
    best = max(points, key=lambda p: p[1])
    worst = min(points, key=lambda p: p[1])
    return {
        "recorded": True,
        "basis": basis,
        "ticks": ticks,
        "refused_ticks": refused,
        "best": {"pnl": round(best[1], 2), "at": best[0]},
        "worst": {"pnl": round(worst[1], 2), "at": worst[0]},
        "path": thin(points, keep=(best, worst)),
        **extra,
    }


def thin(points: list[tuple[str, float]], keep=(), limit: int = MAX_POINTS) -> list[list]:
    """Every n-th point, plus the first, the last and `keep`, in time order, rounded to cents."""
    if len(points) <= limit:
        chosen = points
    else:
        step = len(points) / limit
        idx = {int(i * step) for i in range(limit)} | {0, len(points) - 1}
        chosen = [points[i] for i in sorted(idx)]
        extra = [p for p in keep if p not in chosen]
        chosen = sorted(chosen + extra, key=lambda p: _ts(p[0]) or _EPOCH)
    return [[t, round(v, 2)] for t, v in chosen]


def _leg_path(conn, schema: str, position_id: str) -> dict:
    positions, legs_t, marks_t, assignments_t = LEG_SCHEMAS[schema]
    pos = conn.execute(f"SELECT * FROM {positions} WHERE position_id = ?", (position_id,)).fetchone()
    if pos is None:
        return {"recorded": False, "reason": f"no position {position_id!r}"}
    pos = dict(pos)
    leg_cols = _cols(conn, legs_t)
    opened_col = "opened_at" if "opened_at" in leg_cols else "created_at"
    legs = []
    for leg in conn.execute(f"SELECT * FROM {legs_t} WHERE position_id = ?", (position_id,)):
        leg = dict(leg)
        legs.append(
            {
                "role": leg["leg_role"],
                "sign": _sign(leg["action"]),
                "size": _MULTIPLIER * float(leg.get("quantity") or 1),
                "entry": leg.get("entry_mid"),
                "opened": _ts(leg.get(opened_col)),
                "closed": _ts(leg.get("closed_at")),
                "close_value": leg.get("close_value"),
            }
        )
    if not legs:
        return {"recorded": False, "reason": "the position has no legs recorded"}

    # (assigned_at, disposed_at, share_pnl, status) per assignment.
    shares: list[tuple[datetime | None, datetime | None, float | None, str]] = []
    a_cols = _cols(conn, assignments_t) if assignments_t is not None else set()
    if a_cols:
        assigned_col = "assigned_at" if "assigned_at" in a_cols else "NULL"
        for a in conn.execute(
            f"SELECT status, share_pnl, disposed_at, {assigned_col} AS assigned_at FROM {assignments_t} "
            "WHERE position_id = ?",
            (position_id,),
        ):
            shares.append((_ts(a["assigned_at"]), _ts(a["disposed_at"]), a["share_pnl"], str(a["status"])))

    def value_at(when: datetime, mids: dict) -> float | None:
        # Shares held from an assignment and not yet disposed of are an open position no mark
        # prices: a tick then is refused, never valued without them (calendars' `path` arm holds
        # the back month across a weekend of assigned shares, and pricing those ticks without the
        # shares read a $477 loss as a $523 gain).
        for assigned, disposed, _, _ in shares:
            if assigned is not None and assigned <= when and (disposed is None or disposed > when):
                return None
        total = 0.0
        for leg in legs:
            if leg["entry"] is None:
                return None
            if leg["opened"] is not None and leg["opened"] > when:
                continue
            if leg["closed"] is not None and leg["closed"] <= when:
                if leg["close_value"] is None:
                    return None
                price = leg["close_value"]
            else:
                price = mids.get(leg["role"])
                if price is None:
                    return None
            total += leg["sign"] * (float(price) - float(leg["entry"])) * leg["size"]
        for _, disposed, pnl, _ in shares:
            if disposed is not None and disposed <= when and pnl is not None:
                total += float(pnl)
        return total

    entry_at = pos.get("entry_time") or min(
        (str(leg["opened"]) for leg in legs if leg["opened"]), default=None
    )
    points: list[tuple[str, float]] = [(_iso(entry_at), 0.0)] if entry_at else []
    ticks = refused = 0
    current, mids = None, {}

    def flush():
        nonlocal ticks, refused
        if current is None:
            return
        ticks += 1
        when = _ts(current)
        v = value_at(when, mids) if when is not None else None
        if v is None:
            refused += 1
        else:
            points.append((_iso(current), v))

    for m in conn.execute(
        f"SELECT leg_role, marked_at, mid, usable FROM {marks_t} WHERE position_id = ? "
        "ORDER BY marked_at, rowid",
        (position_id,),
    ):
        if m["marked_at"] != current:
            flush()
            current, mids = m["marked_at"], {}
        if m["usable"] and m["mid"] is not None:
            mids[m["leg_role"]] = float(m["mid"])
        # A refused row for a leg leaves it unpriced at this tick; value_at refuses the tick only
        # if that leg was still open then.

    flush()

    # The endpoint: every leg closed at its close value and every assignment disposed of. It is
    # the module's own gross rule, so it must equal the recorded gross.
    end_at = max((leg["closed"] for leg in legs if leg["closed"] is not None), default=None)
    for _, disposed, _, _ in shares:
        if disposed is not None and (end_at is None or disposed > end_at):
            end_at = disposed
    all_closed = all(leg["closed"] is not None for leg in legs) and all(s[3] == "disposed" for s in shares)
    endpoint = value_at(end_at, {}) if (all_closed and end_at is not None) else None
    if endpoint is not None:
        points.append((_iso(end_at), endpoint))
    gross = pos.get("gross_pnl")
    return _summary(
        points,
        "gross_at_mid",
        ticks,
        refused,
        endpoint=None if endpoint is None else round(endpoint, 2),
        gross=gross,
        reconciles=None if (endpoint is None or gross is None) else abs(endpoint - float(gross)) < 0.01,
    )


def _recorded_path(conn, sql: str, position_id, basis: str, entry_at, exit_at, realised) -> dict:
    """A module's own recorded mark path, from the entry (0) to the trade's realised figure at its
    exit. The exit is part of the excursion: a fill no mark caught (an expiry, a gap) is still where
    the trade went."""
    points = [(_iso(entry_at), 0.0)] if entry_at else []
    ticks = 0
    for at, pnl in conn.execute(sql, (position_id,)):
        ticks += 1
        if pnl is not None:
            points.append((_iso(at), float(pnl)))
    if ticks == 0:
        return {"recorded": False, "reason": "no marks were recorded for this position"}
    refused = ticks - (len(points) - (1 if entry_at else 0))
    endpoint = None
    if exit_at is not None and realised is not None:
        endpoint = round(float(realised), 2)
        points.append((_iso(exit_at), float(realised)))
    return _summary(points, basis, ticks, refused, endpoint=endpoint)


def _earnings_path(conn, position_id: str) -> dict:
    if not _cols(conn, "position_marks"):
        return {"recorded": False, "reason": "this ledger keeps no position marks"}
    row = conn.execute(
        "SELECT opened_at, closed_at, pnl FROM trades WHERE order_id = ?", (position_id,)
    ).fetchone()
    if row is None:
        return {"recorded": False, "reason": f"no position {position_id!r}"}
    entry_at = ledgers._iso_from_epoch(row["opened_at"]) if row["opened_at"] is not None else None
    exit_at = ledgers._iso_from_epoch(row["closed_at"]) if row["closed_at"] is not None else None
    sql = (
        "SELECT marked_at, CASE WHEN usable = 1 THEN unrealized_pnl END FROM position_marks "
        "WHERE order_id = ? ORDER BY marked_at, rowid"
    )
    # Earnings' `pnl` is its gross, the basis its marks are in.
    return _recorded_path(conn, sql, position_id, "gross_as_recorded", entry_at, exit_at, row["pnl"])


def _flies_path(conn, position_id: str) -> dict:
    if not _cols(conn, "fly_live_marks"):
        return {"recorded": False, "reason": "this ledger keeps no position marks"}
    row = conn.execute(
        "SELECT entry_time, exit_time, pnl FROM fly_positions WHERE position_id = ?", (position_id,)
    ).fetchone()
    if row is None:
        return {"recorded": False, "reason": f"no position {position_id!r}"}
    sql = (
        "SELECT iteration_ts, mark_pnl FROM fly_live_marks WHERE position_id = ? ORDER BY iteration_ts, rowid"
    )
    # The exit is the trade's net, settlement included, which the live marks are before.
    out = _recorded_path(
        conn, sql, position_id, "net_of_fees_as_recorded", row["entry_time"], row["exit_time"], row["pnl"]
    )
    if not out["recorded"] and conn.execute("SELECT 1 FROM fly_live_marks LIMIT 1").fetchone() is None:
        # A ledger with no marks at all is the paper book; a live trade with none predates them.
        out["reason"] = "flies records position marks on its live book only"
    return out


def excursion(conn, schema: str, position_id: str) -> dict:
    """The excursion of one position, on the basis its module records (module docstring)."""
    schema = ledgers.canonical_schema(schema)
    if schema in NOT_RECORDED:
        return {"recorded": False, "reason": NOT_RECORDED[schema]}
    if schema in LEG_SCHEMAS:
        return _leg_path(conn, schema, position_id)
    if schema == "earnings":
        return _earnings_path(conn, position_id)
    if schema == "fly_book":
        return _flies_path(conn, position_id)
    return {"recorded": False, "reason": f"unknown schema {schema!r}"}
