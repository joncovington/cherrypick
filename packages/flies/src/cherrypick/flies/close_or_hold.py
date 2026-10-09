"""Close now, or hold to expiry? A read on each open position. Read-only; it places nothing.

For every open position in a ledger (live by default) this sets two numbers side by side, both net
and in whole-position dollars:

**Close now** is what the position would realise if it were closed at this moment at the price you
could actually trade at: every long leg sold at its bid, every short leg bought at its ask (natural),
less the module's own close fees (`cherrypick.core.fees.ic_close_fee`, the schedule that also prices
a vertical's round trip in `close_tags`). The legs are `fly.position_legs`, the module's one answer
to "which contracts does this row hold", so a doubled centre is bought back twice. A leg with no
usable quote in the snapshot (absent, older than the provider's `max_quote_age_seconds`, crossed, or
no ask) gives no close number at all and says which leg -- a hole priced at zero would read as a
phantom profit or loss.

**Hold** is the expected expiry outcome under the empirical distribution of SPX moves from this
time of day to the close. For each past session the gex recorder has a spot reading for within
`WINDOW_MINUTES` of now's ET clock time (`gex_spot_history`), the move is `r = close / spot_t - 1`
against that session's official close (`daily_closes`). Each `r` is applied to today's spot and the
position is settled there with `fly.position_pnl`, which charges the $5-per-ITM-strike settlement
fee that price would trigger. Today and later sessions are excluded, as are sessions with no close
on file and NYSE early-close sessions (a 13:00 close is a different horizon).

**Limits, stated rather than hidden:**

- The distribution is **unscaled**. Today's move is drawn from all recorded sessions alike, a quiet
  day and a fast one weighted the same. Scaling each `r` by today's realised-so-far (or implied)
  move against that session's needs an estimator and a window nobody has measured here, so it is
  not done; the output says `scaled: false`.
- The recorder's trail is short: roughly 50 to 80 sessions, so a mean difference of a few dollars
  is inside the noise (`hold_mean_se` is the standard error of the mean, reported beside it).
  Below `MIN_SESSIONS` no hold estimate is given.
- It values the structure **as it stands**. A short vertical with a completion order resting is
  valued as a vertical held to the print; the completion it may still get is not modelled.
- The close price is the snapshot's quotes, which are the cache's and can be up to
  `max_quote_age_seconds` old. A natural close in a fast market can be worse than this.
- It is a read, not advice, and nothing in the loops consults it. Rule 5 of the module (no
  adjustments after establishment) is unchanged by its existence.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from cherrypick.core import calendar as _cal
from cherrypick.core import fees as _fees
from cherrypick.core.clock import ET

from cherrypick.flies import engine, fly

WINDOW_MINUTES = 5
MIN_SESSIONS = 20
SESSION_OPEN_MIN = 9 * 60 + 30
SESSION_CLOSE_MIN = 16 * 60

_RIGHT_TO_SIDE = {"P": fly.PUT, "C": fly.CALL}


# --------------------------------------------------------------------------- close now
def close_fee(position: dict) -> float:
    """The modelled fee to close every leg of `position` now: no opening commission, one leg per
    contract, a sell leg for each long contract sold (`ic_close_fee`'s convention)."""
    legs = fly.position_legs(position)
    qty = int(position.get("quantity") or 1)
    sells = sum(1 for leg in legs if leg[3] > 0)
    return _fees.ic_close_fee(position.get("symbol") or "SPX", qty, legs=len(legs), sell_legs=sells)


def _usable(q: dict | None) -> bool:
    if not q:
        return False
    bid, ask = q.get("bid"), q.get("ask")
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return False
    return ask > 0 and 0 <= bid <= ask


def natural_close(position: dict, quote_at) -> dict:
    """Per-contract cash from closing every leg at natural (`cash`, signed: + received, - paid) and
    at mid (`mid_cash`, for reference only), off `quote_at(side, strike)`. A refusal names the first
    leg without a usable quote and carries no price."""
    cash = mid_cash = 0.0
    for _expiry, right, strike, sign in fly.position_legs(position):
        side = _RIGHT_TO_SIDE[right]
        q = quote_at(side, strike)
        if not _usable(q):
            return {"ok": False, "reason": f"no usable quote for the {strike:g} {side}"}
        mid = q.get("mid")
        mid = float(mid) if isinstance(mid, (int, float)) else (q["bid"] + q["ask"]) / 2.0
        if sign > 0:  # held long: sell it at the bid
            cash += float(q["bid"])
            mid_cash += mid
        else:  # held short: buy it back at the ask
            cash -= float(q["ask"])
            mid_cash -= mid
    return {"ok": True, "cash": cash, "mid_cash": mid_cash}


def close_now(position: dict, quote_at) -> dict:
    """Closing now, in whole-position dollars: `value` is the exit's cash after close fees and `pnl`
    the position's whole result (entry net, fees already paid, the exit)."""
    priced = natural_close(position, quote_at)
    if not priced["ok"]:
        return priced
    scale = fly.CONTRACT_MULTIPLIER * int(position.get("quantity") or 1)
    fees = close_fee(position)
    value = priced["cash"] * scale - fees
    return {
        "ok": True,
        "exit_cash": priced["cash"] * scale,
        "exit_cash_mid": priced["mid_cash"] * scale,
        "close_fees": fees,
        "value": value,
        "pnl": _entry_pnl(position) + value,
    }


def _entry_pnl(position: dict) -> float:
    """Cash so far, whole position: the recorded net less the fees already charged."""
    qty = int(position.get("quantity") or 1)
    return position["net"] * fly.CONTRACT_MULTIPLIER * qty - (position.get("fees") or 0.0)


# --------------------------------------------------------------------------- the move distribution
def seconds_of_day(ts: float) -> float:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def session_moves(
    trail, closes: dict, *, today: str, clock_min: int, window_minutes: int = WINDOW_MINUTES
) -> dict:
    """The empirical move from `clock_min` (minutes after midnight ET) to the close, one per past
    session: `trail` is `(trade_date, ts, spot)` readings, `closes` is `{trade_date: close}`.

    Each session contributes its reading nearest to the clock time, and only one within
    `window_minutes` of it. Excluded, and counted by reason: `today` and later sessions, sessions
    with no close on file, NYSE early closes, and sessions with no reading in the window."""
    target = clock_min * 60.0
    limit = window_minutes * 60.0
    best: dict = {}
    seen: set = set()
    for trade_date, ts, spot in trail:
        seen.add(trade_date)
        if not spot or spot <= 0:
            continue
        gap = abs(seconds_of_day(ts) - target)
        if gap > limit:
            continue
        if trade_date not in best or gap < best[trade_date][0]:
            best[trade_date] = (gap, float(spot), ts)

    excluded = {"today_or_later": 0, "no_close": 0, "early_close": 0, "no_reading_in_window": 0}
    moves = []
    for trade_date in sorted(seen):
        if trade_date >= today:
            excluded["today_or_later"] += 1
            continue
        if trade_date not in best:
            excluded["no_reading_in_window"] += 1
            continue
        close = closes.get(trade_date)
        if close is None or close <= 0:
            excluded["no_close"] += 1
            continue
        if _cal.is_early_close(date.fromisoformat(trade_date)):
            excluded["early_close"] += 1
            continue
        _gap, spot, _ts = best[trade_date]
        moves.append({"trade_date": trade_date, "spot": spot, "close": float(close), "r": close / spot - 1.0})
    return {"moves": moves, "excluded": excluded}


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100) of a non-empty list."""
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def hold_distribution(position: dict, spot: float, moves: list[dict]) -> dict:
    """The position settled at `spot * (1 + r)` for every move: mean, standard error, P(loss) and
    percentiles of the position's P&L, net of the settlement fee each price would trigger. `value`
    figures are the same outcomes from now forward (payoff less settlement fee)."""
    pos = {**position, "status": "open"}  # position_pnl prices the settlement fee fresh
    pnls = [fly.position_pnl(pos, spot * (1.0 + m["r"])) for m in moves]
    n = len(pnls)
    mean = sum(pnls) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (n - 1)) if n > 1 else 0.0
    base = _entry_pnl(position)
    return {
        "n": n,
        "mean_pnl": mean,
        "mean_value": mean - base,
        "mean_se": sd / math.sqrt(n),
        "p_loss": sum(1 for p in pnls if p < 0) / n,
        "p10": percentile(pnls, 10),
        "p50": percentile(pnls, 50),
        "p90": percentile(pnls, 90),
        "min": min(pnls),
        "max": max(pnls),
        "pnls": pnls,
    }


def verdict(close: dict | None, hold: dict | None, hold_reason: str | None = None) -> str:
    """One plain line. Closing needs a price; holding needs an estimate; with both, the higher
    expected P&L wins and the margin is stated."""
    if not close or not close.get("ok"):
        reason = (close or {}).get("reason") or "no snapshot"
        return f"no close price: {reason}"
    if hold is None:
        return f"no hold estimate: {hold_reason or 'no distribution'}"
    diff = hold["mean_pnl"] - close["pnl"]
    if abs(diff) < 0.005:
        return "even: closing now and the expected hold are the same"
    if diff > 0:
        return f"hold: expected value higher by ${diff:,.2f}"
    return f"close: closing now is higher than the expected hold by ${-diff:,.2f}"


def _legs(position: dict) -> list[str]:
    out = []
    for _expiry, right, strike, sign in fly.position_legs(position):
        out.append(f"{'+1' if sign > 0 else '-1'} {strike:g}{right}")
    return out


def evaluate(
    position: dict,
    quote_at,
    spot: float | None,
    moves: list[dict],
    *,
    min_sessions: int = MIN_SESSIONS,
    snapshot_reason: str | None = None,
    hold_refusal: str | None = None,
) -> dict:
    """The whole read for one open position. `quote_at` is None when there is no snapshot (then
    `snapshot_reason` says why); `spot` is None when there is no fresh spot; `hold_refusal` names
    a reason the caller already knows there is no hold estimate (outside regular hours)."""
    if quote_at is None:
        close = {"ok": False, "reason": f"snapshot refused: {snapshot_reason or 'unknown'}"}
    else:
        close = close_now(position, quote_at)

    hold, hold_reason = None, None
    if hold_refusal:
        hold_reason = hold_refusal
    elif spot is None:
        hold_reason = "no fresh spot"
    elif len(moves) < min_sessions:
        hold_reason = f"only {len(moves)} sessions in the window (need {min_sessions})"
    else:
        hold = hold_distribution(position, spot, moves)

    worst = fly.position_floor(position)
    out = {
        "position_id": position.get("position_id"),
        "arm": position.get("arm"),
        "kind": position["kind"],
        "side": position.get("side"),
        "center": position["center"],
        "wing_width": position["wing_width"],
        "quantity": int(position.get("quantity") or 1),
        "legs": _legs(position),
        "completion_pending": position.get("completion_fill_status") == "pending",
        "entry_net": round(position["net"] * fly.CONTRACT_MULTIPLIER * int(position.get("quantity") or 1), 2),
        "fees_paid": round(position.get("fees") or 0.0, 2),
        "worst_case_if_held": round(worst, 2),
        "close_now": None,
        "close_now_reason": None,
        "hold": None,
        "hold_reason": hold_reason,
        "hold_minus_close": None,
        "verdict": verdict(close, hold, hold_reason),
    }
    if close["ok"]:
        out["close_now"] = {k: round(v, 2) for k, v in close.items() if k != "ok"}
    else:
        out["close_now_reason"] = close["reason"]
    if hold is not None:
        out["hold"] = {
            "n_sessions": hold["n"],
            "expected_pnl": round(hold["mean_pnl"], 2),
            "expected_value": round(hold["mean_value"], 2),
            "expected_pnl_se": round(hold["mean_se"], 2),
            "p_loss": round(hold["p_loss"], 4),
            "p10": round(hold["p10"], 2),
            "p50": round(hold["p50"], 2),
            "p90": round(hold["p90"], 2),
            "min": round(hold["min"], 2),
            "max": round(hold["max"], 2),
        }
        if close["ok"]:
            out["hold_minus_close"] = round(hold["mean_pnl"] - close["pnl"], 2)
            out["hold"]["p_hold_below_close"] = round(
                sum(1 for p in hold["pnls"] if p < close["pnl"]) / hold["n"], 4
            )
    return out


# --------------------------------------------------------------------------- the reader
def open_positions(conn, today: str) -> tuple[list[dict], int]:
    """Today's open, unvoided positions whose entry filled, and how many open rows sit on earlier
    dates (left unevaluated: their expiry has passed and they await settlement, not a decision)."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM fly_positions WHERE status = 'open' AND trade_date = ? AND void_reason IS NULL "
            "ORDER BY id",
            (today,),
        )
    ]
    rows = [r for r in fly.held(rows) if r.get("entry_fill_status") != "pending"]
    stale = conn.execute(
        "SELECT COUNT(*) FROM fly_positions WHERE status = 'open' AND trade_date < ? AND void_reason IS NULL",
        (today,),
    ).fetchone()[0]
    return rows, int(stale)


def load_trail_and_closes(gex_conn, symbol: str, today: str) -> tuple[list, dict]:
    trail = gex_conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date < ?",
        (symbol, today),
    ).fetchall()
    closes = {
        r[0]: r[1]
        for r in gex_conn.execute(
            "SELECT trade_date, close FROM daily_closes WHERE symbol = ? AND trade_date < ?", (symbol, today)
        )
    }
    return [tuple(r) for r in trail], closes


def report(
    ledger_conn,
    *,
    snapshot_for,
    gex_conn,
    now: datetime,
    clock_min: int | None = None,
    min_sessions: int = MIN_SESSIONS,
    window_minutes: int = WINDOW_MINUTES,
) -> dict:
    """Every open position in `ledger_conn`, read. `snapshot_for(symbol)` returns a provider
    snapshot (or a refusal); `clock_min` overrides now's ET clock time for the window (testing)."""
    today = now.date().isoformat()
    clock = now.hour * 60 + now.minute if clock_min is None else clock_min
    positions, stale = open_positions(ledger_conn, today)
    out = {
        "ok": True,
        "today": today,
        "clock": f"{clock // 60:02d}:{clock % 60:02d}",
        "clock_overridden": clock_min is not None,
        "window_minutes": window_minutes,
        "scaled": False,
        "earlier_open_rows_not_evaluated": stale,
        "symbols": {},
        "positions": [],
        "note": "a read, not advice: unscaled empirical moves to the close; close priced at natural",
    }
    by_symbol: dict = {}
    for p in positions:
        by_symbol.setdefault(p["symbol"] or "SPX", []).append(p)
    for symbol, rows in by_symbol.items():
        snap = snapshot_for(symbol)
        quote_at = (lambda s, k, _snap=snap: engine.quote(_snap, s, k)) if snap.get("ok") else None
        # A refused snapshot may still carry a fresh spot (the CLI adds one from the cache's last
        # trade, age-gated): the hold estimate needs only that, the close needs the quotes.
        spot = snap.get("underlying_price")
        if not SESSION_OPEN_MIN <= clock < SESSION_CLOSE_MIN:
            moves, excluded, hold_refusal = [], {}, "outside regular hours"
        else:
            trail, closes = load_trail_and_closes(gex_conn, symbol, today)
            sample = session_moves(trail, closes, today=today, clock_min=clock, window_minutes=window_minutes)
            moves, excluded, hold_refusal = sample["moves"], sample["excluded"], None
        out["symbols"][symbol] = {
            "spot": spot,
            "snapshot_ok": bool(snap.get("ok")),
            "snapshot_reason": None if snap.get("ok") else snap.get("reason"),
            "n_sessions": len(moves),
            "first_session": moves[0]["trade_date"] if moves else None,
            "last_session": moves[-1]["trade_date"] if moves else None,
            "excluded": excluded,
            "r_p10": round(percentile([m["r"] for m in moves], 10), 5) if moves else None,
            "r_p90": round(percentile([m["r"] for m in moves], 90), 5) if moves else None,
        }
        for p in rows:
            out["positions"].append(
                evaluate(
                    p,
                    quote_at,
                    spot,
                    moves,
                    min_sessions=min_sessions,
                    snapshot_reason=snap.get("reason"),
                    hold_refusal=hold_refusal,
                )
            )
    return out
