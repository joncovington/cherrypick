"""One position, week by week -- and the arms, week by week. Read-only.

A held-long position lives ~10 months and closes ~40 weekly shorts against one long, so the closed-
position readers (`analytics.headline`, the suite's ledger reader, the review) see nothing of it
until its long is sold. This is the read that sees it the whole way: what the position is worth at
any moment, every short it sold, and the week-by-week roll-up a trader keeps in a spreadsheet.

**One valuation rule, `value_at`.** A position is valued as of an instant `t`: every leg closed by `t`
at its own recorded close (`engine.leg_pnl`), every leg open at `t` at its latest usable mark at or
before `t` (the console's `readers/unrealised.ts` rule, so the two agree to the cent), delivered
shares at their disposal or, still held, at the spot then -- minus every cost booked by `t`, each at
its own timestamp. The header is `value_at(now)` and every weekly row is `value_at(that week's
close)`, so the last row and the header are the same number by construction, and a reader of this
module cannot find two answers to "what is it worth".

**Money is the suite's layout** (root CLAUDE.md): P&L is net; cash flows are signed (a credit `+`, a
debit `-`) in whole-position dollars; fees, slippage (charged as a cost in this module) and
settlement are separate figures, and every total is the sum of its parts. `None` is "not
recorded" or "not priceable", never zero.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from cherrypick.pmcc import clock, db, engine, management


# --------------------------------------------------------------------------- time
def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return None


def _close_epoch(day: date) -> float:
    """The instant `day`'s session closes, ET (13:00 on an early close)."""
    close = clock.session_close_min(day)
    return datetime(day.year, day.month, day.day, close // 60, close % 60, tzinfo=clock.ET).timestamp()


def _week_end(day: date) -> date:
    """The last trading day of `day`'s ISO week: its Friday, holiday-shifted back."""
    return clock.weekly_expiration(day, 0) or day


def _opened(leg: dict, position: dict) -> float:
    return _epoch(leg.get("opened_at")) or _epoch(position.get("entry_time")) or 0.0


# --------------------------------------------------------------------------- the valuation rule
def _mark_at(conn, position_id: str, role: str, t: float) -> dict | None:
    row = conn.execute(
        "SELECT mid, delta, spot, marked_at FROM pmcc_marks WHERE position_id = ? AND leg_role = ? "
        "AND usable = 1 AND mid IS NOT NULL AND marked_at <= ? ORDER BY marked_at DESC LIMIT 1",
        (position_id, role, t),
    ).fetchone()
    return dict(row) if row else None


def _spot_at(conn, position_id: str, t: float) -> float | None:
    row = conn.execute(
        "SELECT spot FROM pmcc_marks WHERE position_id = ? AND spot IS NOT NULL AND marked_at <= ? "
        "ORDER BY marked_at DESC LIMIT 1",
        (position_id, t),
    ).fetchone()
    return row["spot"] if row else None


def costs_at(position: dict, legs: list[dict], assignments: list[dict], t: float) -> dict:
    """Every cost booked by `t`, split the suite's way: trading fees, slippage, settlement.

    From each leg's own share of its tickets (`pmcc_legs.entry_cost` and friends, 2026-10-04) when
    every leg carries them -- a cash-settled ITM leg's $5 event at its settlement, an assignment's
    fees at its disposal. A row from before the legs carried costs falls back to the position's own
    halves (`basis: "position"`): the entry ticket from entry, the rest once the position closed."""
    if legs and all(leg.get("entry_cost") is not None for leg in legs):
        fee = slip = settle = 0.0
        for leg in legs:
            if _opened(leg, position) <= t:
                fee += leg["entry_cost"] or 0.0
                slip += leg.get("entry_slippage") or 0.0
            closed = _epoch(leg.get("closed_at"))
            if closed is not None and closed <= t:
                fee += leg.get("close_cost") or 0.0
                slip += leg.get("close_slippage") or 0.0
                if leg.get("close_kind") == "cash_settled" and (leg.get("close_value") or 0.0) > 0:
                    settle += engine.settlement_fee(1)
        for a in assignments:
            disposed = _epoch(a.get("disposed_at"))
            if disposed is not None and disposed <= t:
                settle += a.get("fees") or 0.0
        return {
            "basis": "legs",
            "fees": round(fee, 2),
            "slippage": round(slip, 2),
            "settlement": round(settle, 2),
            "total": round(fee + slip + settle, 2),
        }
    closed_at = _epoch(position.get("closed_at"))
    entry_fee = position.get("entry_cost") or 0.0
    entry_slip = position.get("entry_slippage") or 0.0
    if closed_at is None or closed_at > t:
        return {
            "basis": "position",
            "fees": round(entry_fee, 2),
            "slippage": round(entry_slip, 2),
            "settlement": 0.0,
            "total": round(entry_fee + entry_slip, 2),
        }
    settlement = position.get("settlement_fees")
    exit_fee = position.get("exit_cost") or 0.0
    trading = entry_fee + exit_fee - (settlement or 0.0)
    slippage = entry_slip + (position.get("exit_slippage") or 0.0)
    return {
        "basis": "position",
        "fees": round(trading, 2),
        "slippage": round(slippage, 2),
        "settlement": round(settlement, 2) if settlement is not None else None,
        "total": round(float(position.get("fees") or 0.0), 2),
    }


def value_at(conn, position: dict, legs: list[dict], assignments: list[dict], t: float) -> dict | None:
    """The position as of instant `t` (see the module docstring), or None when any part of it is
    unpriceable then. Returned in whole-position dollars, gross before costs and net after."""
    pid = position["position_id"]
    mult = 100 * int(position.get("quantity") or 1)
    long_pnl = short_realised = short_open = 0.0
    marks: dict[str, dict] = {}
    for leg in legs:
        if _opened(leg, position) > t:
            continue
        closed = _epoch(leg.get("closed_at"))
        if closed is not None and closed <= t:
            per_share = engine.leg_pnl(leg)
            if per_share is None:
                return None
            pnl, is_open = per_share * mult, False
        else:
            mark = _mark_at(conn, pid, leg["leg_role"], t)
            if mark is None or leg.get("entry_mid") is None:
                return None
            marks[leg["leg_role"]] = mark
            sign = 1 if leg["action"] == "Buy to Open" else -1
            pnl, is_open = (mark["mid"] - leg["entry_mid"]) * sign * mult, True
        if leg["leg_role"] == "long_call":
            long_pnl += pnl
        elif is_open:
            short_open += pnl
        else:
            short_realised += pnl
    spot = _spot_at(conn, pid, t)
    shares = 0.0
    for a in assignments:
        if (_epoch(a.get("assigned_at")) or 0.0) > t:
            continue
        disposed = _epoch(a.get("disposed_at"))
        if disposed is not None and disposed <= t and a.get("share_pnl") is not None:
            shares += a["share_pnl"]
        elif spot is None:
            return None
        else:
            shares += engine.share_pnl(a["direction"], a["shares"], a["basis"], spot)
    costs = costs_at(position, legs, assignments, t)
    gross = long_pnl + short_realised + short_open + shares
    delta = None
    if marks and all(m.get("delta") is not None for m in marks.values()):
        delta = 0.0
        for leg in legs:
            if leg["leg_role"] in marks:
                sign = 1 if leg["action"] == "Buy to Open" else -1
                delta += sign * marks[leg["leg_role"]]["delta"] * mult
    return {
        "t": t,
        "spot": spot,
        "long": round(long_pnl, 2),
        "short_realised": round(short_realised, 2),
        "short_open": round(short_open, 2),
        "shares": round(shares, 2),
        "gross": round(gross, 2),
        "costs": costs,
        "net": round(gross - costs["total"], 2),
        "net_delta": round(delta, 1) if delta is not None else None,
        "marks": marks,
    }


# --------------------------------------------------------------------------- one position
def _load(conn, position_id: str) -> tuple[dict, list[dict], list[dict]] | None:
    row = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (position_id,)).fetchone()
    if row is None:
        return None
    return dict(row), db.legs_for(conn, position_id), db.assignments_for(conn, position_id)


def _end_t(position: dict, now: float) -> float:
    closed = _epoch(position.get("closed_at"))
    return closed if closed is not None and position.get("status") == "closed" else now


def _weeks(position: dict, end_t: float) -> list[tuple[str, date, float]]:
    """`[(ISO week label, its last trading day, the instant it is valued at)]` from the entry week to
    the week of `end_t`; the last week is valued at `end_t` itself (now, or the close)."""
    start = date.fromisoformat(position["entry_session"])
    end_day = datetime.fromtimestamp(end_t, tz=clock.ET).date()
    out = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end_day:
        week_end = _week_end(monday)
        t = min(_close_epoch(week_end), end_t)
        iso = monday.isocalendar()
        out.append((f"{iso[0]}-W{iso[1]:02d}", week_end, t))
        monday += timedelta(days=7)
    return out


def _extrinsic(price: float | None, spot: float | None, strike: float) -> float | None:
    if price is None or spot is None:
        return None
    return round(price - max(0.0, spot - strike), 4)


def _short_rows(position: dict, legs: list[dict], value_now: dict | None) -> list[dict]:
    mult = 100 * int(position.get("quantity") or 1)
    rows = []
    shorts = [leg for leg in legs if leg["leg_role"] != "long_call"]
    shorts.sort(
        key=lambda leg: int(leg["leg_role"].rsplit("_", 1)[-1]) if leg["leg_role"][-1].isdigit() else 0
    )
    for n, leg in enumerate(shorts, 1):
        entry_spot = (
            leg.get("entry_spot") if leg.get("entry_spot") is not None else position.get("entry_spot")
        )
        sold = leg.get("entry_mid")
        is_open = leg["status"] == "open"
        mark = ((value_now or {}).get("marks") or {}).get(leg["leg_role"]) if is_open else None
        close_price = (mark or {}).get("mid") if is_open else leg.get("close_value")
        close_spot = (value_now or {}).get("spot") if is_open else leg.get("close_spot")
        ext_sold = _extrinsic(sold, entry_spot, leg["strike"])
        ext_left = _extrinsic(close_price, close_spot, leg["strike"])
        opened_day = leg.get("opened_session") or position.get("entry_session")
        closed_day = (leg.get("closed_at") or "")[:10] or None
        held_to = date.fromisoformat(closed_day) if closed_day else datetime.now(tz=clock.ET).date()
        fees = None
        slip = None
        if leg.get("entry_cost") is not None:
            fees = round((leg.get("entry_cost") or 0.0) + (leg.get("close_cost") or 0.0), 2)
            slip = round((leg.get("entry_slippage") or 0.0) + (leg.get("close_slippage") or 0.0), 2)
        entry_cash = round(sold * mult, 2) if sold is not None else None
        exit_cash = round(-close_price * mult, 2) if close_price is not None else None
        gross = round(entry_cash + exit_cash, 2) if entry_cash is not None and exit_cash is not None else None
        rows.append(
            {
                "n": n,
                "leg_role": leg["leg_role"],
                "status": leg["status"],
                "opened": opened_day,
                "closed": closed_day,
                "days_held": (held_to - date.fromisoformat(opened_day)).days if opened_day else None,
                "spot_open": entry_spot,
                "spot_close": close_spot,
                "strike": leg["strike"],
                "expiration": leg["expiration"],
                "sold": sold,
                "bought": close_price,
                "entry": entry_cash,
                "exit": exit_cash,
                "how": leg.get("close_kind") if not is_open else "open",
                "why": leg.get("close_reason"),
                "gross": gross,
                "fees": fees,
                "slippage": slip,
                "net": round(gross - fees - slip, 2) if gross is not None and fees is not None else None,
                "extrinsic_sold": round(ext_sold * mult, 2) if ext_sold is not None else None,
                "extrinsic_left": round(ext_left * mult, 2) if ext_left is not None else None,
                "extrinsic_captured": (
                    round((ext_sold - ext_left) * mult, 2)
                    if ext_sold is not None and ext_left is not None
                    else None
                ),
            }
        )
    return rows


def tracker(conn, position_id: str, config: dict | None = None, *, now: float | None = None) -> dict | None:
    """The spreadsheet for one position: header, the long, the current short, every short sold,
    and the weekly roll-up. None for an unknown id."""
    loaded = _load(conn, position_id)
    if loaded is None:
        return None
    position, legs, assignments = loaded
    params = management.effective_params(position, config or {})
    now = now if now is not None else time.time()
    end_t = _end_t(position, now)
    mult = 100 * int(position.get("quantity") or 1)
    current = value_at(conn, position, legs, assignments, end_t)

    long_leg = next((leg for leg in legs if leg["leg_role"] == "long_call"), None)
    long_cost = (
        round(long_leg["entry_mid"] * mult, 2) if long_leg and long_leg.get("entry_mid") is not None else None
    )
    notional = round(position["entry_spot"] * mult, 2) if position.get("entry_spot") else None

    lots = []
    if long_leg is not None:
        mark = ((current or {}).get("marks") or {}).get("long_call")
        price_now = (mark or {}).get("mid") if long_leg["status"] == "open" else long_leg.get("close_value")
        spot_now = (current or {}).get("spot") if long_leg["status"] == "open" else long_leg.get("close_spot")
        entry_spot = (
            long_leg.get("entry_spot")
            if long_leg.get("entry_spot") is not None
            else position.get("entry_spot")
        )
        ext_paid = _extrinsic(long_leg["entry_mid"], entry_spot, long_leg["strike"])
        ext_now = _extrinsic(price_now, spot_now, long_leg["strike"])
        lots.append(
            {
                "opened": long_leg.get("opened_session") or position["entry_session"],
                "expiration": long_leg["expiration"],
                "spot_open": entry_spot,
                "strike": long_leg["strike"],
                "quantity": int(position.get("quantity") or 1),
                "cost": -long_cost if long_cost is not None else None,
                "price_now": price_now,
                "value_now": round(price_now * mult, 2) if price_now is not None else None,
                "gain": round((price_now - long_leg["entry_mid"]) * mult, 2)
                if price_now is not None
                else None,
                "extrinsic_paid": round(ext_paid * mult, 2) if ext_paid is not None else None,
                "extrinsic_now": round(ext_now * mult, 2) if ext_now is not None else None,
                "delta_now": (mark or {}).get("delta"),
                "status": long_leg["status"],
            }
        )

    shorts = _short_rows(position, legs, current)
    open_short = next((s for s in shorts if s["status"] == "open"), None)
    current_short = None
    if open_short is not None:
        ext_sold = open_short["extrinsic_sold"]
        ext_left = open_short["extrinsic_left"]
        session_close = clock.session_close_min(date.fromisoformat(open_short["expiration"]))
        roll_at = session_close - int(params.get("roll_time_offset", 60))
        decay = params.get("early_roll_decay")
        current_short = {
            "stock_at_sale": open_short["spot_open"],
            "strike": open_short["strike"],
            "expiration": open_short["expiration"],
            "premium": open_short["sold"],
            "intrinsic_at_sale": (
                round(max(0.0, open_short["spot_open"] - open_short["strike"]), 4)
                if open_short["spot_open"] is not None
                else None
            ),
            "extrinsic_at_sale": ext_sold,
            "extrinsic_now": ext_left,
            "decayed_pct": round(1 - ext_left / ext_sold, 4) if ext_sold and ext_left is not None else None,
            "extrinsic_on_long_cost_pct": round(ext_sold / long_cost, 4)
            if ext_sold is not None and long_cost
            else None,
            "projected": ext_sold,
            "breakeven": (
                round(open_short["spot_open"] - open_short["sold"], 4)
                if open_short["spot_open"] is not None and open_short["sold"] is not None
                else None
            ),
            "dte": (
                date.fromisoformat(open_short["expiration"])
                - datetime.fromtimestamp(end_t, tz=clock.ET).date()
            ).days,
            "next": {
                "expiry_roll_at": f"{open_short['expiration']} {roll_at // 60:02d}:{roll_at % 60:02d} ET",
                "decay_roll_below": (
                    round((1 - decay) * ext_sold, 2) if decay is not None and ext_sold is not None else None
                ),
                "breach_at": open_short["strike"] if params.get("breach_roll") else None,
            },
        }

    weeks = []
    prev = 0.0
    for label, week_end, t in _weeks(position, end_t):
        v = current if t == end_t else value_at(conn, position, legs, assignments, t)
        row = {
            "week": label,
            "week_end": week_end.isoformat(),
            "priced": v is not None,
            "spot": (v or {}).get("spot"),
            "long_mark": ((v or {}).get("marks") or {}).get("long_call", {}).get("mid"),
            "short_open": (v or {}).get("short_open"),
            "short_realised": (v or {}).get("short_realised"),
            "costs": ((v or {}).get("costs") or {}).get("total"),
            "net": (v or {}).get("net"),
            "change": round(v["net"] - prev, 2) if v is not None else None,
            "return_on_long_cost": round(v["net"] / long_cost, 4) if v is not None and long_cost else None,
            "return_on_notional": round(v["net"] / notional, 4) if v is not None and notional else None,
            "net_delta": (v or {}).get("net_delta"),
        }
        if v is not None:
            prev = v["net"]
        weeks.append(row)

    ext_captured = sum(s["extrinsic_captured"] or 0.0 for s in shorts if s["extrinsic_captured"] is not None)
    long_decay = None
    if lots and lots[0]["extrinsic_paid"] is not None and lots[0]["extrinsic_now"] is not None:
        long_decay = round(lots[0]["extrinsic_paid"] - lots[0]["extrinsic_now"], 2)
    spot_now = (current or {}).get("spot")
    # The two exits that close a held-long position, as `management.evaluate_held_long` tests them:
    # net to date at or below -stop_loss_frac x the long's cost, and the long at long_close_dte.
    exits = None
    if management.is_held_long(params) and long_leg is not None:
        stop = params.get("stop_loss_frac")
        stop_at = round(-stop * long_cost, 2) if stop and long_cost else None
        long_exp = date.fromisoformat(long_leg["expiration"])
        exits = {
            "stop_net_at": stop_at,
            "stop_room": (
                round(current["net"] - stop_at, 2) if stop_at is not None and current is not None else None
            ),
            "long_close_on": (long_exp - timedelta(days=int(params.get("long_close_dte", 45)))).isoformat(),
            "long_dte": (long_exp - datetime.fromtimestamp(end_t, tz=clock.ET).date()).days,
        }
    header = {
        "long_cost": -long_cost if long_cost is not None else None,
        "long_value": lots[0]["value_now"] if lots else None,
        "long_gain": (current or {}).get("long"),
        "short_realised": (current or {}).get("short_realised"),
        "short_open": (current or {}).get("short_open"),
        "shares": (current or {}).get("shares"),
        "gross": (current or {}).get("gross"),
        "costs": (current or {}).get("costs"),
        "net": (current or {}).get("net"),
        "return_on_long_cost": round(current["net"] / long_cost, 4)
        if current is not None and long_cost
        else None,
        "return_on_notional": round(current["net"] / notional, 4)
        if current is not None and notional
        else None,
        "underlying_since_open": (
            round(spot_now / position["entry_spot"] - 1, 4)
            if spot_now and position.get("entry_spot")
            else None
        ),
        "days_in_trade": (
            datetime.fromtimestamp(end_t, tz=clock.ET).date() - date.fromisoformat(position["entry_session"])
        ).days,
        "net_delta": (current or {}).get("net_delta"),
        "extrinsic_captured": round(ext_captured, 2),
        "long_extrinsic_decay": long_decay,
        "net_extrinsic": round(ext_captured - long_decay, 2) if long_decay is not None else None,
        "shorts_sold": len(shorts),
        "notional": notional,
        "exits": exits,
    }
    return {
        "position": {
            k: position.get(k)
            for k in (
                "position_id",
                "symbol",
                "arm",
                "era",
                "status",
                "exit_reason",
                "entry_session",
                "closed_session",
                "quantity",
                "entry_spot",
                "roll_count",
                "exposure_ticks",
            )
        }
        | {"lifecycle": params.get("lifecycle", "weekly")},
        "header": header,
        "long_lots": lots,
        "current_short": current_short,
        "shorts": shorts,
        "weeks": weeks,
        "integrity": {
            "exposure_ticks": position.get("exposure_ticks"),
            "unpriced_weeks": sum(1 for w in weeks if not w["priced"]),
            "weeks_without_a_short": _weeks_without_short(legs, position, weeks),
            "costs_basis": ((current or {}).get("costs") or {}).get("basis"),
        },
        "as_of": end_t,
    }


def _weeks_without_short(legs: list[dict], position: dict, weeks: list[dict]) -> list[str]:
    """ISO weeks whose close found no short open: an ex-dividend gap, a deadline buyback not yet
    replaced. A held-long position's weekly income is zero those weeks, and the page says so."""
    out = []
    for w in weeks:
        t = _close_epoch(date.fromisoformat(w["week_end"]))
        open_short = any(
            leg["leg_role"] != "long_call"
            and _opened(leg, position) <= t
            and (_epoch(leg.get("closed_at")) is None or _epoch(leg.get("closed_at")) > t)
            for leg in legs
        )
        if not open_short:
            out.append(w["week"])
    return out


# --------------------------------------------------------------------------- the picker and the arms
def tracker_index(
    conn, config: dict | None = None, *, now: float | None = None, limit: int = 300
) -> list[dict]:
    """Every position the tracker can open, open ones first: identity, lifecycle, how many shorts it
    has sold, and its net -- realised once closed, marked to market while open."""
    now = now if now is not None else time.time()
    rows = []
    for p in conn.execute(
        "SELECT * FROM pmcc_positions ORDER BY (status = 'closed'), COALESCE(closed_session, entry_session) DESC, "
        "symbol, arm LIMIT ?",
        (limit,),
    ):
        position = dict(p)
        params = management.effective_params(position, config or {})
        if position["status"] == "closed":
            net = (
                round(position["gross_pnl"] - position["fees"], 2)
                if position.get("gross_pnl") is not None and position.get("fees") is not None
                else None
            )
        else:
            v = value_at(
                conn,
                position,
                db.legs_for(conn, position["position_id"]),
                db.assignments_for(conn, position["position_id"]),
                now,
            )
            net = v["net"] if v is not None else None
        shorts = conn.execute(
            "SELECT COUNT(*) FROM pmcc_legs WHERE position_id = ? AND leg_role != 'long_call'",
            (position["position_id"],),
        ).fetchone()[0]
        rows.append(
            {
                "position_id": position["position_id"],
                "symbol": position["symbol"],
                "arm": position["arm"],
                "era": position.get("era"),
                "lifecycle": params.get("lifecycle", "weekly"),
                "status": position["status"],
                "entry_session": position["entry_session"],
                "closed_session": position.get("closed_session"),
                "shorts": shorts,
                "net": net,
            }
        )
    return rows


def open_mtm(conn, *, now: float | None = None) -> dict:
    """`{arm: {symbol: {"positions", "net", "unpriced"}}}` over OPEN positions, each marked by
    `value_at(now)`: the readout a held-long arm has before its first position closes. `net` is None
    while any of the cell's positions is unpriceable -- a partial sum is not that arm's mark."""
    now = now if now is not None else time.time()
    out: dict[str, dict[str, dict]] = {}
    for p in conn.execute("SELECT * FROM pmcc_positions WHERE status != 'closed' ORDER BY arm, symbol"):
        position = dict(p)
        cell = out.setdefault(position["arm"], {}).setdefault(
            position["symbol"], {"positions": 0, "net": 0.0, "unpriced": 0}
        )
        cell["positions"] += 1
        v = value_at(
            conn,
            position,
            db.legs_for(conn, position["position_id"]),
            db.assignments_for(conn, position["position_id"]),
            now,
        )
        if v is None:
            cell["unpriced"] += 1
        else:
            cell["net"] += v["net"]
    for arms in out.values():
        for cell in arms.values():
            cell["net"] = round(cell["net"], 2) if not cell["unpriced"] else None
    return out


def weekly_by_arm(conn, *, era: str | None = None, now: float | None = None) -> dict:
    """The A/B readout: per (arm, symbol, ISO week), the change in net P&L over that week summed
    across the arm's positions -- a closed control cycle's result in the week it closed, a held-long
    position's weekly mark-to-market move every week it is open. `cumulative` runs it forward.

    A position unpriceable at a week's close is counted in `unpriced` and left out of that week's
    sum rather than guessed into it."""
    now = now if now is not None else time.time()
    where, args = "", []
    if era and era != "ALL":
        where, args = " WHERE era = ?", [era]
    cells: dict[tuple, dict] = {}
    for p in conn.execute(f"SELECT * FROM pmcc_positions{where}", args):
        position = dict(p)
        legs = db.legs_for(conn, position["position_id"])
        assignments = db.assignments_for(conn, position["position_id"])
        end_t = _end_t(position, now)
        prev = 0.0
        for label, week_end, t in _weeks(position, end_t):
            key = (position["arm"], position["symbol"], label)
            cell = cells.setdefault(
                key, {"week_end": week_end.isoformat(), "change": 0.0, "positions": 0, "unpriced": 0}
            )
            cell["positions"] += 1
            v = value_at(conn, position, legs, assignments, t)
            if v is None:
                cell["unpriced"] += 1
                continue
            cell["change"] += v["net"] - prev
            prev = v["net"]
    rows = []
    running: dict[tuple, float] = {}
    for (arm, symbol, week), cell in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        running[(arm, symbol)] = running.get((arm, symbol), 0.0) + cell["change"]
        rows.append(
            {
                "arm": arm,
                "symbol": symbol,
                "week": week,
                "week_end": cell["week_end"],
                "change": round(cell["change"], 2),
                "cumulative": round(running[(arm, symbol)], 2),
                "positions": cell["positions"],
                "unpriced": cell["unpriced"],
            }
        )
    return {"era": era, "rows": rows}
