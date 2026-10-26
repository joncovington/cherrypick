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
  exit.
- `gross_at_mid_from_quote_history` -- flies paper and MEIC, which record no position marks: priced
  here from the gex recorder's SPX/XSP 0DTE quote history (`core.quotehistory`, every ~15 s, from
  2026-10-13), only where the module recorded nothing itself (marks are the record; this fills the
  gap). Flies is its opening vertical (legged: the short credit spread; debit_first: the debit
  spread) until `completed_at`, then the fly, at flies' own net timeline; its structure read must
  reproduce the module's gross at settlement (557 of 557). A MEIC side that has exited counts its
  recorded P&L from then on, never a later quote; the endpoint is the sum of the sides, which must
  equal the condor's gross (3,226 of 3,226).

  It is not the live loop's own view. Against the 28 live flies trades that have both, best and
  worst agree within $30 on about 80% of trades (median +/-$5), and differ by $100+ on a few: the
  two readers catch flickering 0DTE quotes at different instants (a tick a leg, even at the same
  spot), and the history samples ~4x as often, so it sees slightly wider extremes. MEIC's own
  per-side max cost agrees the same way (median +$0.03 a share, p10/p90 -$0.11/+$0.18).

Contango records only a daily fund mid, and flies' bwb_roll positions change shape in a way not
priced here: they read `recorded: false` with the reason, never a guessed path.

The entry point (0 at the entry mid, by construction) and the endpoint are part of the path, so a
trade that only ever lost has a best of 0 at entry. Pure reads, no clock, no network.
"""

from __future__ import annotations

from datetime import datetime

from cherrypick.core import db as core_db
from cherrypick.core import ledgers, quotehistory
from cherrypick.core.clock import ET

#: Leg-marked schemas: (positions table, legs table, marks table, assignments table or None).
LEG_SCHEMAS = {
    "bwb_132": ("bwb_positions", "bwb_legs", "bwb_marks", None),
    "dc_week": ("dc_positions", "dc_legs", "dc_marks", "dc_assignments"),
    "pmcc": ("pmcc_positions", "pmcc_legs", "pmcc_marks", "pmcc_assignments"),
    "curve_vx": ("curve_positions", "curve_legs", "curve_marks", "curve_assignments"),
}

NOT_RECORDED = {
    "contango_etf": "contango records one fund mid a day, not a position mark path",
}

#: The path handed back is thinned to about this many points; best, worst, entry and end are kept.
MAX_POINTS = 400

_MULTIPLIER = 100.0


def as_et(value) -> datetime | None:
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


def iso_et(value) -> str | None:
    t = as_et(value)
    return None if t is None else t.astimezone(ET).isoformat(timespec="seconds")


_EPOCH = datetime.fromtimestamp(0, ET)


_cols = core_db.column_set


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
        chosen = sorted(chosen + extra, key=lambda p: as_et(p[0]) or _EPOCH)
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
                "opened": as_et(leg.get(opened_col)),
                "closed": as_et(leg.get("closed_at")),
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
            shares.append(
                (as_et(a["assigned_at"]), as_et(a["disposed_at"]), a["share_pnl"], str(a["status"]))
            )

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
    points: list[tuple[str, float]] = [(iso_et(entry_at), 0.0)] if entry_at else []
    ticks = refused = 0
    current, mids = None, {}

    def flush():
        nonlocal ticks, refused
        if current is None:
            return
        ticks += 1
        when = as_et(current)
        v = value_at(when, mids) if when is not None else None
        if v is None:
            refused += 1
        else:
            points.append((iso_et(current), v))

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
        points.append((iso_et(end_at), endpoint))
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
    points = [(iso_et(entry_at), 0.0)] if entry_at else []
    ticks = 0
    for at, pnl in conn.execute(sql, (position_id,)):
        ticks += 1
        if pnl is not None:
            points.append((iso_et(at), float(pnl)))
    if ticks == 0:
        return {"recorded": False, "reason": "no marks were recorded for this position"}
    refused = ticks - (len(points) - (1 if entry_at else 0))
    endpoint = None
    if exit_at is not None and realised is not None:
        endpoint = round(float(realised), 2)
        points.append((iso_et(exit_at), float(realised)))
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


# --------------------------------------------------------------------------- from the quote history
#: A path priced here from the gex recorder's SPX/XSP 0DTE quote history (`core.quotehistory`,
#: every ~15 s from 2026-10-13), for a position whose module recorded no marks of its own. Gross at
#: mid, like the leg-built paths; recorded marks always win where they exist.
QUOTE_BASIS = "gross_at_mid_from_quote_history"
QUOTE_HISTORY_FROM = "2026-10-13"
_NO_QUOTES = "no quote history for the session (SPX/XSP 0DTE, recorded from 2026-10-13)"


def _mid(chain: dict, strike: float) -> float | None:
    q = chain.get(float(strike))
    return None if q is None or q.get("mid") is None else float(q["mid"])


def _fly_value(kind: str, side: str, c: float, w: float, chain: dict) -> float | None:
    """Per-contract mid value of a flies structure, same side throughout -- flies' own
    `fly.structure_mid` for a short vertical and a fly, and the debit-first opening vertical."""
    if kind == "fly":
        a, b, d = _mid(chain, c - w), _mid(chain, c), _mid(chain, c + w)
        return None if None in (a, b, d) else a - 2 * b + d
    if kind == "short_vertical":  # short c, long the wing on the protected side
        a, b = _mid(chain, c - w if side == "put" else c + w), _mid(chain, c)
        return None if None in (a, b) else a - b
    if kind == "long_vertical":  # debit_first: +1 (c-w) call / -1 c call; +1 (c+w) put / -1 c put
        a, b = _mid(chain, c - w if side == "call" else c + w), _mid(chain, c)
        return None if None in (a, b) else a - b
    return None


def _fly_payoff(kind: str, side: str, c: float, w: float, s: float) -> float | None:
    """flies' own expiry payoffs (`fly.fly_payoff`, `short_vertical_payoff`, `debit_vertical_payoff`)."""
    if kind == "fly":
        return max(0.0, w - abs(s - c))
    if kind == "short_vertical":
        return -max(0.0, min(w, c - s)) if side == "put" else -max(0.0, min(w, s - c))
    if kind == "long_vertical":
        return max(0.0, min(w, s - (c - w))) if side == "call" else max(0.0, min(w, (c + w) - s))
    return None


def _window(symbol, trade_date, t0, t1, history_db) -> list[dict]:
    samples = quotehistory.session(symbol, trade_date, history_db=history_db)
    return [s for s in samples if t0.timestamp() <= s["ts"] <= t1.timestamp() and s.get("dte") == 0]


def _flies_from_quotes(conn, position_id: str, history_db) -> dict:
    if not _cols(conn, "fly_positions"):
        return {"recorded": False, "reason": "this ledger keeps no fly positions"}
    row = conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (position_id,)).fetchone()
    if row is None:
        return {"recorded": False, "reason": f"no position {position_id!r}"}
    p = dict(row)
    if p.get("entry_mode") not in ("legged", "debit_first"):
        return {
            "recorded": False,
            "reason": f"a {p.get('entry_mode')} position's legs are not priced from quotes",
        }
    t0, t1 = as_et(p.get("entry_time")), as_et(p.get("exit_time"))
    if t0 is None or t1 is None or str(p.get("trade_date")) < QUOTE_HISTORY_FROM:
        return {"recorded": False, "reason": _NO_QUOTES}
    samples = _window(p["symbol"], p["trade_date"], t0, t1, history_db)
    if not samples:
        return {"recorded": False, "reason": _NO_QUOTES}
    legged = p["entry_mode"] == "legged"
    opening = (
        ("short_vertical", float(p["credit"] or 0.0))
        if legged
        else ("long_vertical", -float(p["debit"] or 0.0))
    )
    done_at = as_et(p.get("completed_at"))
    qty = float(p.get("quantity") or 1)
    side, c, w = p["side"], float(p["center"]), float(p["wing_width"])

    points = [(iso_et(t0), 0.0)]
    refused = 0
    for s in samples:
        when = as_et(s["ts"])
        kind, net = ("fly", float(p["net"])) if done_at is not None and done_at <= when else opening
        v = _fly_value(kind, side, c, w, s["puts" if side == "put" else "calls"])
        if v is None:
            refused += 1
            continue
        points.append((iso_et(s["ts"]), (net + v) * _MULTIPLIER * qty))

    # The structure as read here must reproduce the module's own gross at settlement: the check that
    # strikes, side and net are read as flies reads them (585 of 585 settled positions, 2026-10-17).
    gross = p.get("gross_pnl")
    reconciles = None
    final_kind = "fly" if done_at is not None else opening[0]
    if p.get("settlement_price") is not None and gross is not None and not p.get("closed_before_expiry"):
        payoff = _fly_payoff(final_kind, side, c, w, float(p["settlement_price"]))
        reconciles = abs((float(p["net"]) + payoff) * _MULTIPLIER * qty - float(gross)) < 0.01
    if gross is not None:
        points.append((iso_et(t1), float(gross)))
    return _summary(
        points, QUOTE_BASIS, len(samples), refused, endpoint=gross, gross=gross, reconciles=reconciles
    )


def _meic_from_quotes(conn, position_id: str, history_db) -> dict:
    if not _cols(conn, "ic_trades") or not _cols(conn, "ic_spread_legs"):
        return {"recorded": False, "reason": "this ledger keeps no condors"}
    row = conn.execute("SELECT * FROM ic_trades WHERE ic_order_id = ?", (position_id,)).fetchone()
    if row is None:
        return {"recorded": False, "reason": f"no position {position_id!r}"}
    t = dict(row)
    t0, t1 = as_et(t.get("entry_time")), as_et(t.get("exit_time"))
    if t0 is None or t1 is None or str(t.get("trade_date")) < QUOTE_HISTORY_FROM:
        return {"recorded": False, "reason": _NO_QUOTES}
    sides = {
        r["side"]: (as_et(r["exit_time"]), r["pnl"])
        for r in conn.execute(
            "SELECT side, exit_time, pnl FROM ic_spread_legs WHERE ic_order_id = ?", (position_id,)
        )
    }
    if set(sides) != {"put", "call"}:
        return {"recorded": False, "reason": "the condor's per-side exits are not recorded"}
    samples = _window(t["symbol"], t["trade_date"], t0, t1, history_db)
    if not samples:
        return {"recorded": False, "reason": _NO_QUOTES}
    w = float(t["wing_width"])
    size = float(t.get("dollar_multiplier") or _MULTIPLIER) * float(t.get("quantity") or 1)
    spreads = {  # side: (short strike, long strike, credit)
        "put": (float(t["put_strike"]), float(t["put_strike"]) - w, t.get("put_credit")),
        "call": (float(t["call_strike"]), float(t["call_strike"]) + w, t.get("call_credit")),
    }

    def value_at(when, chains) -> float | None:
        total = 0.0
        for side, (short, long_, credit) in spreads.items():
            exited, pnl = sides[side]
            if exited is not None and exited <= when:
                if pnl is None:
                    return None
                total += float(pnl)
                continue
            a, b = _mid(chains[side], short), _mid(chains[side], long_)
            if None in (a, b) or credit is None:
                return None
            total += (float(credit) - (a - b)) * size
        return total

    points = [(iso_et(t0), 0.0)]
    refused = 0
    for s in samples:
        v = value_at(as_et(s["ts"]), {"put": s["puts"], "call": s["calls"]})
        if v is None:
            refused += 1
        else:
            points.append((iso_et(s["ts"]), v))
    # The condor's gross is the sum of its two sides' P&L, which MEIC records per side.
    side_sum = (
        None
        if any(pnl is None for _, pnl in sides.values())
        else sum(float(pnl) for _, pnl in sides.values())
    )
    gross = t.get("pnl")
    if side_sum is not None:
        points.append((iso_et(max(e for e, _ in sides.values() if e is not None)), side_sum))
    return _summary(
        points,
        QUOTE_BASIS,
        len(samples),
        refused,
        endpoint=None if side_sum is None else round(side_sum, 2),
        gross=gross,
        reconciles=None if (side_sum is None or gross is None) else abs(side_sum - float(gross)) < 0.01,
    )


def excursion(conn, schema: str, position_id: str, *, history_db=None) -> dict:
    """The excursion of one position, on the basis its module records (module docstring). Flies and
    MEIC positions with no recorded marks are priced from the quote history instead, and say so."""
    schema = ledgers.canonical_schema(schema)
    if schema in NOT_RECORDED:
        return {"recorded": False, "reason": NOT_RECORDED[schema]}
    if schema in LEG_SCHEMAS:
        return _leg_path(conn, schema, position_id)
    if schema == "earnings":
        return _earnings_path(conn, position_id)
    if schema == "fly_book":
        recorded = _flies_path(conn, position_id)
        return recorded if recorded["recorded"] else _flies_from_quotes(conn, position_id, history_db)
    if schema == "meic_ic":
        return _meic_from_quotes(conn, position_id, history_db)
    return {"recorded": False, "reason": f"unknown schema {schema!r}"}
