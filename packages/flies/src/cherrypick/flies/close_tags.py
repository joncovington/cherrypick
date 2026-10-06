"""Tagged closes of stranded verticals: tag, don't gate (docs/intraday-agent-plan.md).

The `trend-rule` and `intraday-agent` arms decide when a stranded vertical should be closed rather
than ridden to settlement. Paper flies has no intraday close any more (the pre-close exit was
removed 2026-08-01, and settlement treats every position as open until the print), so a close is
**recorded, not executed**, the way the hedge overlay and the reversal book measure a different
path: at the decision tick the vertical's NATURAL close debit (pay the short's ask, take the long's
bid) and its mid are stamped on the row, the position settles as it always does, and the read side
(`analytics.close_tag_result`) values the arm as if it had closed.

Priced at natural rather than mid less a concession, because a close in a running market is the
most fill-sensitive thing the agent does and live has never closed a stranded vertical to calibrate
against. The read side also reports a 2x haircut (one more spread's worth), the plan's bar.
"""

from __future__ import annotations

from cherrypick.core import fees as core_fees

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, fly
from cherrypick.flies.book import book_id_for, entry_leg_strikes

RULE = "rule"
AGENT = "agent"


def spot_past_short(position: dict, spot: float) -> float:
    """How far spot has moved through the short strike toward a full loss, in points (signed)."""
    center = float(position["center"])
    return spot - center if position["side"] == fly.CALL else center - spot


def natural_close(snapshot: dict, position: dict) -> dict | None:
    """The debit to close one open short vertical now: `natural` pays the short's ask and takes the
    long's bid; `mid` is the same at mid. None when either leg has no usable quote."""
    side, center, width = position["side"], float(position["center"]), float(position["wing_width"])
    strikes = entry_leg_strikes("short_vertical", side, center, width)
    short_q, long_q = (
        engine.quote(snapshot, side, strikes["center"]),
        engine.quote(snapshot, side, strikes["wing"]),
    )
    if not short_q or not long_q:
        return None
    try:
        natural = float(short_q["ask"]) - float(long_q["bid"])
        mid = float(short_q["mid"]) - float(long_q["mid"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"natural": round(max(natural, 0.0), 2), "mid": round(max(mid, 0.0), 2)}


def rule_wants_close(position: dict, snapshot: dict, params: dict) -> bool:
    """`trend-rule`'s close: the vertical is through its short but not yet past its wing (the loss
    can still grow), and the day has committed (|drift| beyond the trend band) in the direction that
    strands it. Past the wing the loss is already full and closing saves nothing."""
    spot = snapshot.get("underlying_price")
    day_open = (snapshot.get("session") or {}).get("day_open")
    if spot is None or day_open is None:
        return False
    past = spot_past_short(position, float(spot))
    if not 0 <= past < float(position["wing_width"]):
        return False
    drift = float(spot) - float(day_open)
    band = float(params.get("regime_trend_points", 20.0))
    against = drift > band if position["side"] == fly.CALL else drift < -band
    return against


def round_trip_fees(symbol: str, quantity: int) -> float:
    """The modelled fees of a vertical opened and then closed: two legs each way, one sold."""
    return round(
        core_fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1)
        + core_fees.ic_close_fee(symbol, quantity, legs=2, sell_legs=1),
        2,
    )


def closed_value(credit: float, quantity: int, natural: float, mid: float | None, fees: float) -> dict:
    """A tagged vertical valued as closed at the tag: its credit less the natural debit, less the round
    trip's fees, in whole-position dollars. `closed_net_2x` charges one more spread's worth
    (natural - mid), the plan's bar for an edge that survives worse fills."""
    worse = natural + max(natural - (natural if mid is None else mid), 0.0)
    return {
        "closed_net": round((credit - natural) * 100 * quantity - fees, 2),
        "closed_net_2x": round((credit - worse) * 100 * quantity - fees, 2),
    }


def tag(conn, snapshot: dict, arm: str, params: dict, *, source: str, agent_ids=(), now: str) -> list[dict]:
    """Stamp a close tag on each open, untagged short vertical of `arm`'s book that `source` wants
    closed (`rule`: `rule_wants_close`; `agent`: the ids in `agent_ids`). Returns what was tagged.
    A position is tagged once; its settlement is untouched."""
    book_id = book_id_for(snapshot["date"], arm, snapshot["symbol"])
    wanted = set(agent_ids or ())
    spot = snapshot.get("underlying_price")
    out = []
    for row in dbmod.book_positions(conn, book_id):
        pos = dict(row)
        if pos.get("kind") != "short_vertical" or pos.get("status") != "open" or pos.get("close_tag_at"):
            continue
        if source == RULE:
            if not rule_wants_close(pos, snapshot, params):
                continue
        elif source == AGENT:
            if pos.get("position_id") not in wanted:
                continue
        else:
            continue
        price = natural_close(snapshot, pos)
        if price is None:
            continue
        conn.execute(
            "UPDATE fly_positions SET close_tag_at = ?, close_tag_source = ?, close_tag_natural = ?, "
            "close_tag_mid = ?, close_tag_spot = ?, close_tag_fees = ? WHERE id = ?",
            (
                now,
                source,
                price["natural"],
                price["mid"],
                spot,
                round_trip_fees(snapshot["symbol"], int(pos.get("quantity") or 1)),
                pos["id"],
            ),
        )
        out.append({"position_id": pos.get("position_id"), "source": source, **price})
    if out:
        conn.commit()
    return out
