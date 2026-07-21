"""Read-only query layer over the paper database.

The dashboard, the suite section card, and the EOD writer all read through here. That is the point:
MEIC grew three call sites that disagree with each other about what "net" means — its Today grid uses
raw `pnl` while its profile comparison uses `pnl - fees` — and the cure is one layer that answers each
question exactly once.

Nothing here writes, trades, or reaches the network. Every function takes an open connection.

**Win rate is per position, net of fees.** MEIC counts wins per spread LEG, because an iron condor can
finish with one side a winner and the other a loser and no single verdict is honest. A butterfly and a
vertical each resolve to one number, so the simple definition is the correct one here. Do not "fix"
this into MEIC's leg-counting — it would be wrong for this instrument.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fly  # noqa: E402

GRANULARITIES = ("daily", "weekly", "monthly")

# Every P&L query filters to settled positions. An open credit spread is not a result yet, and
# counting it as one would flatter whichever arm happens to be holding something at the time.
_SETTLED = "status = 'settled'"


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def _rate(numerator, denominator, digits=4):
    return round(numerator / denominator, digits) if denominator else None


# --------------------------------------------------------------------------- period stats
def _period_clause(start=None, end=None, arm=None, symbol=None):
    clause, params = [_SETTLED], []
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    return " AND ".join(clause), params


def _summarize(rows) -> dict:
    gross = sum((r["gross_pnl"] or 0.0) for r in rows)
    fees = sum((r["fees"] or 0.0) for r in rows)
    nets = [(r["pnl"] or 0.0) for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    total_win, total_loss = sum(wins), abs(sum(losses))
    return {
        "trades": len(rows),
        "gross_pnl": _round(gross),
        "fees": _round(fees),
        "net_pnl": _round(sum(nets)),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _rate(len(wins), len(wins) + len(losses)),
        "avg_pnl": _round(sum(nets) / len(nets)) if nets else None,
        "avg_win": _round(total_win / len(wins)) if wins else None,
        "avg_loss": _round(-total_loss / len(losses)) if losses else None,
        # Fees as a share of gross credit taken in — the number that turned up a trade collecting
        # $4.00 against $4.96 of fees elsewhere in this suite.
        "fee_drag_pct": _round(fees / gross * 100) if gross > 0 else None,
        "profit_factor": _round(total_win / total_loss) if total_loss > 0 else None,
    }


def stats_for_period(conn, start=None, end=None, arm=None, symbol=None) -> dict:
    where, params = _period_clause(start, end, arm, symbol)
    rows = conn.execute(
        f"SELECT gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params).fetchall()
    return _summarize(rows)


def _week_start(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _bucket_key(trade_date: str, granularity: str) -> str:
    if granularity == "monthly":
        return trade_date[:7]
    if granularity == "weekly":
        # Computed in Python: SQLite's %W starts weeks on Sunday, which would split a Monday-anchored
        # trading week across two buckets.
        return _week_start(date.fromisoformat(trade_date))
    return trade_date


def pnl_series(conn, granularity: str = "daily", arm=None, symbol=None) -> list[dict]:
    """P&L bucketed by day / week / month.

    Shares `_period_clause` with `stats_for_period` deliberately, so summing this series over a range
    equals that function's `net_pnl` for the same range. That consistency is a guarantee the dashboard
    relies on and a test asserts.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}")
    where, params = _period_clause(arm=arm, symbol=symbol)
    rows = conn.execute(
        f"SELECT trade_date, gross_pnl, fees, pnl FROM fly_positions WHERE {where} "
        "AND trade_date IS NOT NULL ORDER BY trade_date", params).fetchall()

    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_bucket_key(r["trade_date"], granularity), []).append(r)

    out, cumulative = [], 0.0
    for key in sorted(buckets):
        summary = _summarize(buckets[key])
        cumulative += summary["net_pnl"] or 0.0
        out.append({"bucket": key, **summary, "cumulative_pnl": _round(cumulative)})
    return out


# --------------------------------------------------------------------------- breakdowns
def by_arm(conn, start=None, end=None) -> list[dict]:
    """Per-arm comparison — the module's headline output. The arms exist to be compared; a blended
    total would hide the only contrast the experiment is designed to draw."""
    where, params = _period_clause(start, end)
    rows = conn.execute(
        f"SELECT arm, gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["arm"] or "unassigned", []).append(r)
    out = [{"arm": arm, **_summarize(rs)} for arm, rs in grouped.items()]
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


def by_entry_mode(conn, start=None, end=None) -> list[dict]:
    """legged vs outright. They perform differently enough that averaging them together would hide
    the finding — legged manufactures its own floor, outright spends one."""
    where, params = _period_clause(start, end)
    rows = conn.execute(
        f"SELECT entry_mode, gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["entry_mode"] or "unknown", []).append(r)
    return [{"entry_mode": m, **_summarize(rs)} for m, rs in sorted(grouped.items())]


def by_entry_window(conn, start=None, end=None) -> list[dict]:
    """Per time-of-day window.

    The windows are deliberately unranked in config — we had no intraday history to rank them with, so
    every trade is tagged and the ranking is meant to emerge here, from our own sessions.
    """
    where, params = _period_clause(start, end)
    rows = conn.execute(
        f"SELECT entry_window, arm, gross_pnl, fees, pnl FROM fly_positions WHERE {where}",
        params).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["entry_window"] or "unwindowed", []).append(r)
    return [{"window": w, **_summarize(rs)} for w, rs in sorted(grouped.items())]


def fee_drag(conn, start=None, end=None) -> list[dict]:
    """Fee drag per arm. Broken out because a legged fly pays two fee stacks against a credit that may
    be $35-105 — costs are not a rounding error for this strategy, they are the experiment."""
    return [{"arm": r["arm"], "gross_pnl": r["gross_pnl"], "fees": r["fees"],
             "net_pnl": r["net_pnl"], "fee_drag_pct": r["fee_drag_pct"], "trades": r["trades"]}
            for r in by_arm(conn, start, end)]


def daily_pnl(conn, arm=None) -> list[dict]:
    """Per-day totals for the calendar heatmap."""
    return [{"date": b["bucket"], "trades": b["trades"], "gross_pnl": b["gross_pnl"],
             "fees": b["fees"], "net_pnl": b["net_pnl"]}
            for b in pnl_series(conn, "daily", arm=arm)]


# --------------------------------------------------------------------------- completion & counterfactual
def completion_stats(conn, start=None, end=None) -> dict:
    """Completion rate, latency, and the counterfactual split — the numbers that decide whether this
    strategy is real.

    A legged entry that never completes leaves an ordinary short vertical carrying full defined risk.
    If that branch dominates, the strategy is short verticals wearing a costume, and no amount of P&L
    on the completed ones changes that. The counterfactual then says whether the misses were the
    market's fault or our gate's:

      never_offered   the best debit ever seen was still above the credit — no buffer would have helped
      buffer_too_tight  the best debit beat the credit but not the buffer — our threshold cost us the fly
    """
    clause, params = [], []
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(clause)) if clause else ""
    rows = conn.execute(
        f"SELECT kind, entry_mode, credit, best_completing_debit, completion_latency_min, "
        f"underlying_at_entry, spot_at_completion FROM fly_positions{where}", params).fetchall()

    legged = [r for r in rows if r["entry_mode"] == "legged"]
    completed = [r for r in legged if r["kind"] == "fly"]
    missed = [r for r in legged if r["kind"] != "fly"]

    never_offered, buffer_too_tight, unknown = 0, 0, 0
    for r in missed:
        best, credit = r["best_completing_debit"], r["credit"]
        if best is None or credit is None:
            unknown += 1
        elif best >= credit:
            never_offered += 1
        else:
            buffer_too_tight += 1

    latencies = [r["completion_latency_min"] for r in completed
                 if r["completion_latency_min"] is not None]
    moves = [abs((r["spot_at_completion"] or 0) - (r["underlying_at_entry"] or 0))
             for r in completed
             if r["spot_at_completion"] is not None and r["underlying_at_entry"] is not None]
    return {
        "legged_entries": len(legged),
        "completed": len(completed),
        "completion_rate": _rate(len(completed), len(legged)),
        "never_offered": never_offered,
        "buffer_too_tight": buffer_too_tight,
        "counterfactual_unknown": unknown,
        "median_latency_min": _round(_median(latencies), 1),
        "min_latency_min": _round(min(latencies), 1) if latencies else None,
        "max_latency_min": _round(max(latencies), 1) if latencies else None,
        "median_spot_move": _round(_median(moves)),
    }


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# --------------------------------------------------------------------------- arm divergence
def arm_divergence(conn, day: str | None = None) -> dict:
    """How often the arms actually picked DIFFERENT centres.

    The experiment can only separate two arms to the extent they disagree. If gex and control choose
    the same strike most of the time, the sample needed to distinguish them grows enormously, and the
    honest conclusion may be that the comparison cannot answer the question as framed. Far better to
    discover that in week one than after a month of collecting data that was never going to separate.
    """
    where, params = "", []
    if day:
        where, params = " WHERE trade_date = ?", [day]
    rows = conn.execute(
        f"SELECT iteration_ts, symbol, arm, center FROM fly_iterations{where} "
        "ORDER BY iteration_ts", params).fetchall()

    iterations: dict[tuple, dict] = {}
    for r in rows:
        iterations.setdefault((r["iteration_ts"], r["symbol"]), {})[r["arm"]] = r["center"]

    pairs: dict[tuple, list] = {}
    all_agree = 0
    considered = 0
    for centers in iterations.values():
        named = {a: c for a, c in centers.items() if c is not None}
        if len(named) < 2:
            continue
        considered += 1
        if len(set(named.values())) == 1:
            all_agree += 1
        arms = sorted(named)
        for i, a in enumerate(arms):
            for b in arms[i + 1:]:
                pairs.setdefault((a, b), []).append(named[a] == named[b])

    return {
        "iterations": considered,
        "all_agree_rate": _rate(all_agree, considered),
        "pairs": [
            {"arms": f"{a} vs {b}", "iterations": len(matches),
             "agreement_rate": _rate(sum(matches), len(matches))}
            for (a, b), matches in sorted(pairs.items())
        ],
    }


# --------------------------------------------------------------------------- journal & positions
def decision_journal(conn, day: str, arm: str | None = None) -> list[dict]:
    """The day's decisions, newest run first. Already collapsed at write time, so this is a plain read."""
    clause, params = ["trade_date = ?"], [day]
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    rows = conn.execute(
        f"SELECT * FROM fly_decisions WHERE {' AND '.join(clause)} ORDER BY id DESC", params).fetchall()
    return [dict(r) for r in rows]


def positions_for_day(conn, day: str, arm: str | None = None) -> list[dict]:
    clause, params = ["trade_date = ?"], [day]
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    rows = conn.execute(
        f"SELECT * FROM fly_positions WHERE {' AND '.join(clause)} ORDER BY entry_time", params
    ).fetchall()
    return [dict(r) for r in rows]


def trade_log(conn, limit: int = 1000, arm=None, symbol=None) -> list[dict]:
    where, params = _period_clause(arm=arm, symbol=symbol)
    rows = conn.execute(
        f"SELECT * FROM fly_positions WHERE {where} "
        "ORDER BY trade_date DESC, entry_time DESC LIMIT ?", [*params, limit]).fetchall()
    return [dict(r) for r in rows]


def books_for_day(conn, day: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fly_books WHERE trade_date = ? ORDER BY arm", (day,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- the risk graph
def payoff_curve(conn, day: str, arm: str, step: float = 1.0, points: int = 120) -> dict:
    """The profit forest itself: book P&L across a price grid, plus the floor and the band it holds over.

    This is the visual the strategy is named for — green across a band with a peak at each centre — and
    it is the one view that shows at a glance whether a book is genuinely safe or merely safe-looking.
    Returns empty (not an error) for a book with no positions; an empty day is ordinary.
    """
    positions = [
        {"kind": r["kind"], "side": r["side"], "center": r["center"], "wing_width": r["wing_width"],
         "net": r["net"], "quantity": r["quantity"] or 1, "fees": r["fees"] or 0.0}
        for r in positions_for_day(conn, day, arm)
    ]
    if not positions:
        return {"ok": True, "empty": True, "prices": [], "pnl": [], "positions": 0}

    centers = [p["center"] for p in positions]
    width = max(p["wing_width"] for p in positions)
    lo, hi = min(centers) - 3 * width, max(centers) + 3 * width
    span = hi - lo
    grid_step = max(step, span / points) if span else step

    prices, pnls = [], []
    x = lo
    while x <= hi + 1e-9:
        prices.append(round(x, 2))
        pnls.append(round(fly.book_pnl(positions, x), 2))
        x += grid_step

    floor = fly.book_floor(positions, step=grid_step)
    cash = fly.book_cash(positions)
    return {
        "ok": True,
        "empty": False,
        "positions": len(positions),
        "prices": prices,
        "pnl": pnls,
        "centers": sorted(set(centers)),
        "floor": floor,
        "cash": cash,
    }


# --------------------------------------------------------------------------- the session timeline
def _state_at(row: dict, when: str) -> dict | None:
    """This position as it stood at `when`, or None if it was not on the book yet.

    A legged entry is a SHORT VERTICAL until it completes and a butterfly afterwards, and the stored
    row only ever holds the latest state. Replaying an intraday book straight from those rows would
    draw the morning as though every fly existed from the moment its credit spread was sold — which
    asserts exactly the per-position floor the module refuses to claim loosely (honesty rule 3).

    The rewind is exact rather than approximate. The completing purchase is a 2-leg vertical, so the
    pre-completion fee is `vertical_open_fee` for the same symbol and size, and the pre-completion net
    is the recorded `credit` — both of which `book.py` wrote and neither of which is inferred.
    """
    entry = row.get("entry_time")
    if not entry or when < entry:
        return None
    state = {
        "kind": row["kind"], "side": row["side"], "center": row["center"],
        "wing_width": row["wing_width"], "net": row["net"],
        "quantity": row["quantity"] or 1, "fees": row["fees"] or 0.0,
    }
    completed = row.get("completed_at")
    if (row.get("entry_mode") == "legged" and completed and when < completed
            and row.get("credit") is not None):
        state["kind"] = "short_vertical"
        state["net"] = row["credit"]
        state["fees"] = fly.vertical_open_fee(row["symbol"], state["quantity"])
    return state


def session_timeline(conn, day: str | None = None) -> dict:
    """The session along a TIME axis — the one axis every other view here lacks.

    `payoff_curve` plots price at expiry, so nothing in it moves during a session. But the module
    already records an intraday history and then spends it on scalars: `fly_iterations` holds spot and
    each arm's wanted centre on every iteration, and it exists only to produce one agreement
    percentage. This assembles that record instead:

      ticks     spot and each arm's wanted centre, every iteration
      events    entries and completions, placed on the same axis
      spans     leg-in to completion, so completion latency is a LENGTH beside the spot drift that
                bought it — the 2026-07-20 finding (completions arrived only after 10-21 points of
                drift) is a shape over time and has had no axis to appear on

    `settle_now` is the book replayed at each tick: what it would have been worth had the session
    ended at that moment and that price. It is an expiry payoff evaluated at a live spot, NOT a mark
    — the positions are not quoted intraday and nothing here pretends otherwise. Read-only, and
    computed from rows already written, so nothing on the decision path changes to produce it.
    """
    day = day or today()
    rows = positions_for_day(conn, day)
    iterations = conn.execute(
        "SELECT iteration_ts, arm, center, center_reason, underlying_price FROM fly_iterations "
        "WHERE trade_date = ? ORDER BY iteration_ts", (day,)).fetchall()
    feed, feed_summary = data_quality(conn, day)

    arms = sorted({r["arm"] for r in rows if r["arm"]} | {r["arm"] for r in iterations if r["arm"]})
    by_arm: dict[str, list] = {a: [] for a in arms}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)

    grouped: dict[str, list] = {}
    for r in iterations:
        grouped.setdefault(r["iteration_ts"], []).append(r)

    ticks = []
    for ts in sorted(grouped):
        entries = grouped[ts]
        spots = [e["underlying_price"] for e in entries if e["underlying_price"] is not None]
        spot = spots[0] if spots else None
        settle_now = {}
        if spot is not None:
            for arm in arms:
                states = [s for s in (_state_at(dict(p), ts) for p in by_arm.get(arm, [])) if s]
                if states:
                    settle_now[arm] = _round(fly.book_pnl(states, spot))
        ticks.append({
            "ts": ts,
            "spot": _round(spot),
            "centers": {e["arm"]: e["center"] for e in entries if e["center"] is not None},
            "reasons": {e["arm"]: e["center_reason"] for e in entries if e["center_reason"]},
            "settle_now": settle_now,
        })

    events, spans = [], []
    for r in rows:
        if r.get("entry_time"):
            events.append({
                "kind": "entry", "ts": r["entry_time"], "arm": r["arm"],
                "entry_mode": r["entry_mode"], "position_id": r["position_id"],
                "center": r["center"], "spot": r["underlying_at_entry"],
                "structure": "fly" if r["entry_mode"] == "outright" else f"short {r['side']}",
            })
        if r.get("completed_at"):
            events.append({
                "kind": "completion", "ts": r["completed_at"], "arm": r["arm"],
                "entry_mode": r["entry_mode"], "position_id": r["position_id"],
                "center": r["center"], "spot": r["spot_at_completion"], "structure": "fly",
            })
            drift = (None if r["spot_at_completion"] is None or r["underlying_at_entry"] is None
                     else abs(r["spot_at_completion"] - r["underlying_at_entry"]))
            spans.append({
                "position_id": r["position_id"], "arm": r["arm"], "center": r["center"],
                "from": r["entry_time"], "to": r["completed_at"],
                "latency_min": r["completion_latency_min"], "drift": _round(drift, 1),
            })
    events.sort(key=lambda e: e["ts"])

    # Credit spreads still waiting: the branch that carries full defined risk until it completes, and
    # the one a time axis makes visible while it is still happening rather than at settlement.
    waiting = [{"position_id": r["position_id"], "arm": r["arm"], "center": r["center"],
                "from": r["entry_time"], "best_debit": r["best_completing_debit"],
                "credit": r["credit"]}
               for r in rows
               if r["entry_mode"] == "legged" and not r.get("completed_at")]

    return {"date": day, "arms": arms, "ticks": ticks, "events": events, "spans": spans,
            "waiting": waiting, "feed": feed, "feed_summary": feed_summary}


def data_quality(conn, day: str | None = None) -> tuple[list[dict], dict]:
    """What the feed gave us this session — the record that separates 'the data was thin' from 'the
    strategy found nothing', which is a distinction this module is built to keep.

    Returns the per-tick series (every snapshot attempt, whether it built or was refused) and a
    summary counting refusals by reason. A stretch of the day with refused rows is a feed problem; a
    stretch with NO rows at all is the loop not running — two different findings, and only separable
    because the refused tick is now recorded rather than logged and forgotten.
    """
    day = day or today()
    rows = conn.execute(
        "SELECT iteration_ts, symbol, status, quotes_fresh, quotes_rejected, underlying_price "
        "FROM fly_snapshots WHERE trade_date = ? ORDER BY iteration_ts", (day,)).fetchall()

    feed = [{"ts": r["iteration_ts"], "symbol": r["symbol"], "status": r["status"],
             "fresh": r["quotes_fresh"], "rejected": r["quotes_rejected"],
             "spot": _round(r["underlying_price"])} for r in rows]

    by_reason: dict[str, int] = {}
    ok = 0
    for r in rows:
        if r["status"] == "ok":
            ok += 1
        else:
            by_reason[r["status"]] = by_reason.get(r["status"], 0) + 1
    summary = {
        "ticks": len(rows),
        "ok": ok,
        "refused": len(rows) - ok,
        "by_reason": by_reason,
        "ok_rate": _rate(ok, len(rows)),
    }
    return feed, summary


# --------------------------------------------------------------------------- rollup
def today() -> str:
    return datetime.now().date().isoformat()


def session_overview(conn, day: str | None = None) -> dict:
    """Everything the Today view and the section card need, in one call."""
    day = day or today()
    books = books_for_day(conn, day)
    positions = positions_for_day(conn, day)
    open_positions = [p for p in positions if p["status"] == "open"]
    flies = [p for p in positions if p["kind"] == "fly"]
    return {
        "date": day,
        "books": books,
        "positions": positions,
        "open_count": len(open_positions),
        "fly_count": len(flies),
        "risk_free_count": len([p for p in flies if p["risk_free"]]),
        "stats": stats_for_period(conn, day, day),
        "completion": completion_stats(conn, day, day),
        "divergence": arm_divergence(conn, day),
        "journal": decision_journal(conn, day),
    }
