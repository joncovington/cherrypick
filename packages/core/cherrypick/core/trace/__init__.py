"""One closed position's trace: how it got in, and what was decided while it was held.

docs/strategy-analysis-plan.md: "a trade opens a drawer with ... its attempt and decision trace". The
realised P&L says how a trade ended; the trace says what the loop tried and refused on the way in,
and what it weighed each tick while it held -- the difference between "the exit rule fired late"
and "the exit rule was blocked by a wide spread for forty minutes".

Two parts, each read from what the module already records, and nothing inferred:

- `entry`: the attempt that filled, and the attempts before it on the same session, arm and symbol
  since that arm's previous fill. Flies and MEIC link the filled attempt to the position
  (`position_id` / `ic_order_id`); bwb, calendars, pmcc and curve key attempts by session, arm and
  symbol only, so the filled attempt is the one at or before the position's entry time, and the
  result says it was MATCHED rather than linked. Calendars' attempts carry no arm (its arms share
  one entry plan). Earnings records a screening review, not attempts: its selected review for the
  symbol on the entry date, with the criteria it passed.
- `management`: the position's management events (bwb, calendars, pmcc, curve, earnings), in time
  order, collapsed into runs of the same (action, reason, executed, gate). Earnings writes an event
  every tick it holds, so a position can carry hundreds; a run keeps its count, first and last time
  and the last detail. An executed event always starts its own run.

- `orders`: flies' LIVE book records no entry attempts, but it does record every order behind a
  position (`fly_live_orders`: limit, mid at submit, outcome, the broker's fill and its gap to mid),
  which is the live trace of how it got in.

Flies, MEIC and contango keep no per-position management events, and contango no entry attempts;
those parts read `recorded: false` with the reason. Pure reads, no clock, no network.
"""

from __future__ import annotations

import json
from datetime import timedelta

from cherrypick.core import db as core_db
from cherrypick.core import ledgers
from cherrypick.core.excursions import as_et, iso_et

#: A list longer than this keeps its first and last runs and says how many it left out.
MAX_RUNS = 60

#: (positions table, key column) per schema.
POSITIONS = {
    "bwb_132": ("bwb_positions", "position_id"),
    "dc_week": ("dc_positions", "position_id"),
    "pmcc": ("pmcc_positions", "position_id"),
    "curve_vx": ("curve_positions", "position_id"),
    "fly_book": ("fly_positions", "position_id"),
    "meic_ic": ("ic_trades", "ic_order_id"),
    "earnings": ("trades", "order_id"),
    "contango_etf": ("contango_positions", "position_id"),
}

#: Entry attempts: (table, arm column or None, column linking a filled attempt to its position or None).
ATTEMPTS = {
    "bwb_132": ("bwb_entry_attempts", "arm", None),
    "dc_week": ("dc_entry_attempts", None, None),
    "pmcc": ("pmcc_entry_attempts", "arm", None),
    "curve_vx": ("curve_entry_attempts", "arm", None),
    "fly_book": ("fly_entry_attempts", "arm", "position_id"),
    "meic_ic": ("entry_attempts", "arm", "ic_order_id"),
}

#: Management events: (table, key column).
MANAGEMENT = {
    "bwb_132": ("bwb_management_events", "position_id"),
    "dc_week": ("dc_management_events", "position_id"),
    "pmcc": ("pmcc_management_events", "position_id"),
    "curve_vx": ("curve_management_events", "position_id"),
    "earnings": ("management_events", "order_id"),
}

FILLED = "filled"

#: A filled attempt is stamped a moment around the position it opened; one at most this long after
#: the entry time still matches.
_SLACK = timedelta(seconds=120)


_cols = core_db.column_set


def _detail(raw):
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def runs(rows: list[dict], key, at: str = "at") -> list[dict]:
    """Consecutive rows with the same `key(row)` folded into one run: `{**first row, count, first,
    last}` carrying the LAST row's other fields (its detail, block detail). Rows must already be in
    time order."""
    out: list[dict] = []
    for r in rows:
        k = key(r)
        if out and out[-1]["_key"] == k:
            out[-1]["count"] += 1
            out[-1]["last"] = r[at]
            out[-1].update({name: v for name, v in r.items() if name != at})
            continue
        out.append({**r, "_key": k, "count": 1, "first": r[at], "last": r[at]})
    for run in out:
        run.pop("_key")
        run.pop(at, None)
    return out


def _capped(items: list[dict]) -> tuple[list[dict], int]:
    """At most MAX_RUNS, keeping the first third and the rest from the end."""
    if len(items) <= MAX_RUNS:
        return items, 0
    head = MAX_RUNS // 3
    tail = MAX_RUNS - head
    return items[:head] + items[-tail:], len(items) - MAX_RUNS


def _position(conn, schema: str, position_id: str) -> dict | None:
    table, key = POSITIONS[schema]
    if not _cols(conn, table):
        return None
    row = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (position_id,)).fetchone()
    if row is None:
        return None
    p = dict(row)
    if schema == "meic_ic":
        p["arm"] = p.get(core_db.arm_column(conn, "ic_trades"))
    if schema == "earnings":
        p["entry_time"] = (
            ledgers._iso_from_epoch(p.get("opened_at")) if p.get("opened_at") is not None else None
        )
    entry = as_et(p.get("entry_time"))
    p["_entry"] = entry
    p["_session"] = p.get("trade_date") or (entry.date().isoformat() if entry else None)
    return p


def _attempt_at(r: dict):
    """An attempt's time. MEIC stamps `ts` as HH:MM with the date in `trade_date`; the rest stamp
    a full ISO time."""
    at = as_et(r.get("ts"))
    if at is None and r.get("ts") and r.get("trade_date"):
        at = as_et(f"{r['trade_date']}T{r['ts']}")
    return at


#: Live orders behind a position: (table, key column). Flies' live loop records each leg's order
#: (limit, mid at submit, outcome, the broker's fill and its gap to mid) and no entry attempts.
ORDERS = {"fly_book": ("fly_live_orders", "position_id")}

_ORDER_FIELDS = (
    "leg", "side", "quantity", "limit_price", "mid_at_submit", "spot_at_submit", "outcome", "resolved_at",
    "broker_filled_at", "broker_fill_price", "fill_mid", "fill_mid_gap", "fill_natural_gap", "source",
)  # fmt: skip


def _orders(conn, schema: str, position_id: str) -> dict:
    if schema not in ORDERS:
        return {"recorded": False, "reason": "this module records no orders per position"}
    table, key = ORDERS[schema]
    cols = _cols(conn, table)
    rows = (
        [
            dict(r)
            for r in conn.execute(f"SELECT * FROM {table} WHERE {key} = ? ORDER BY placed_at", (position_id,))
        ]
        if cols
        else []
    )
    if not rows:
        return {
            "recorded": False,
            "reason": "no orders recorded for this position (the paper book places none)",
        }
    return {
        "recorded": True,
        "orders": [
            {"at": iso_et(r.get("placed_at")), **{k: r.get(k) for k in _ORDER_FIELDS if k in cols}}
            for r in rows
        ],
    }


def _entry_attempts(conn, schema: str, pos: dict, position_id: str) -> dict:
    table, arm_col, link_col = ATTEMPTS[schema]
    cols = _cols(conn, table)
    if not cols:
        return {"recorded": False, "reason": "this ledger keeps no entry attempts"}
    session = pos["_session"]
    if session is None:
        return {"recorded": False, "reason": "the position records no entry time"}
    where, params = "trade_date = ?", [session]
    if arm_col is not None and arm_col in cols:
        where += f" AND {arm_col} = ?"
        params.append(pos.get("arm"))
    if "symbol" in cols and pos.get("symbol"):
        where += " AND symbol = ?"
        params.append(pos["symbol"])
    detail_cols = [
        c for c in cols if c not in ("id", "ts", "trade_date", "arm", "symbol", "outcome", "block_detail")
    ]
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY ts, id", params)]
    if not rows:
        return {"recorded": False, "reason": "no entry attempts were recorded that session"}
    for r in rows:
        r["_at"] = _attempt_at(r)

    # The attempt that filled this position: linked where the module links it, else the last
    # filled attempt at or before the entry time (matched, and said to be).
    filled_idx, basis = None, None
    if link_col is not None and link_col in cols:
        filled_idx = next((i for i, r in enumerate(rows) if r.get(link_col) == position_id), None)
        basis = "linked"
    if filled_idx is None and pos["_entry"] is not None:
        candidates = [
            i
            for i, r in enumerate(rows)
            if r.get("outcome") == FILLED and r["_at"] is not None and r["_at"] <= pos["_entry"] + _SLACK
        ]
        if candidates:
            filled_idx, basis = candidates[-1], "matched"
    if filled_idx is None:
        return {"recorded": False, "reason": "no filled entry attempt matches this position"}

    start = filled_idx
    while start > 0 and rows[start - 1].get("outcome") != FILLED:
        start -= 1
    before = [
        {
            "at": iso_et(r["_at"]),
            "outcome": r.get("outcome"),
            "block_detail": r.get("block_detail"),
            "detail": {k: r.get(k) for k in detail_cols if r.get(k) is not None and k != link_col},
        }
        for r in rows[start:filled_idx]
    ]
    folded, omitted = _capped(runs(before, key=lambda r: r["outcome"]))
    filled = rows[filled_idx]
    return {
        "recorded": True,
        "basis": basis,
        "session": session,
        "attempts_before": len(before),
        "runs": folded,
        "runs_omitted": omitted,
        "filled": {
            "at": iso_et(filled["_at"]),
            "detail": {k: filled.get(k) for k in detail_cols if filled.get(k) is not None and k != link_col},
        },
    }


def _earnings_review(conn, pos: dict) -> dict:
    if not _cols(conn, "entry_reviews") or pos["_entry"] is None:
        return {"recorded": False, "reason": "no entry review recorded"}
    base_arm = str(pos.get("arm") or "").split(":")[0]
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM entry_reviews WHERE scan_date = ? AND symbol = ? ORDER BY selected DESC, id DESC",
            (pos["_entry"].date().isoformat(), pos.get("symbol")),
        )
    ]
    rows = [r for r in rows if not r.get("arm") or r["arm"] == base_arm] or rows
    if not rows:
        return {"recorded": False, "reason": "no entry review recorded for the symbol that day"}
    r = rows[0]
    keep = (
        "selected", "reason", "timing", "winrate", "winrate_sample", "iv_rv_ratio", "term_structure",
        "expected_move_pct", "iv_rank", "composite_score", "bid_ask_spread_pct", "implied_vs_avg_actual",
    )  # fmt: skip
    return {
        "recorded": True,
        "basis": "review",
        "session": r.get("scan_date"),
        "review": {k: r.get(k) for k in keep if k in r},
        "criteria": _detail(r.get("criteria_json")),
    }


def _management(conn, schema: str, position_id: str) -> dict:
    if schema not in MANAGEMENT:
        return {"recorded": False, "reason": "this module keeps no per-position management events"}
    table, key = MANAGEMENT[schema]
    if not _cols(conn, table):
        return {"recorded": False, "reason": "this ledger keeps no management events"}
    events = [
        {
            "at": iso_et(r["occurred_at"]),
            "action": r["action"],
            "reason": r["reason"],
            "executed": bool(r["executed"]),
            "gate": r["gate"],
            "detail": _detail(r["detail_json"]),
        }
        for r in conn.execute(
            f"SELECT occurred_at, action, reason, executed, gate, detail_json FROM {table} "
            f"WHERE {key} = ? ORDER BY occurred_at, id",
            (position_id,),
        )
    ]
    if not events:
        return {"recorded": True, "events": 0, "runs": [], "runs_omitted": 0, "executed": []}
    # An executed event is its own run: the moment something was done is never folded into a
    # string of times it was only considered.
    folded = runs(
        events,
        key=lambda e: (
            (e["action"], e["reason"], e["executed"], e["gate"]) + ((id(e),) if e["executed"] else ())
        ),
    )
    capped, omitted = _capped(folded)
    return {
        "recorded": True,
        "events": len(events),
        "runs": capped,
        "runs_omitted": omitted,
        "executed": [
            {k: e[k] for k in ("at", "action", "reason", "detail")} for e in events if e["executed"]
        ],
    }


def trace(conn, schema: str, position_id: str) -> dict:
    """The entry and management trace of one position (module docstring)."""
    schema = ledgers.canonical_schema(schema)
    if schema not in POSITIONS:
        return {"found": False, "reason": f"unknown schema {schema!r}"}
    pos = _position(conn, schema, position_id)
    if pos is None:
        return {"found": False, "reason": f"no position {position_id!r}"}
    if schema in ATTEMPTS:
        entry = _entry_attempts(conn, schema, pos, position_id)
    elif schema == "earnings":
        entry = _earnings_review(conn, pos)
    else:
        entry = {"recorded": False, "reason": "this module records no entry attempts"}
    return {
        "found": True,
        "entry": entry,
        "orders": _orders(conn, schema, position_id),
        "management": _management(conn, schema, position_id),
    }
