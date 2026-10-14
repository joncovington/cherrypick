"""What a live limit order needed from the market before it filled, and the paper rules that would
reproduce it. Pure: no I/O, no clock, no broker.

**Why this exists.** Paper and live complete a legged spread by two different rules. Paper takes the
first tick whose MODELLED debit (mid plus `slippage_frac` of each leg's bid-ask) clears
`credit - fee_buffer`, and pays that modelled debit. Live rests a Day limit at
`live_orders.max_safe_completion_debit` and pays the limit whenever the market reaches it. Over
2026-07-30..10-02, 43 of 48 live completions filled BELOW the best modelled debit the live loop ever
saw while the spread was open (median 0.14 points better) -- completions paper's rule would have
refused. Before paper can be made to fill like live, what live fills on has to be measured.

**Three distances, every fill.** "How far had the market come" has three honest readings, each the
basis of a different paper rule, so all three are recorded and none is privileged in storage:

- `dist_center` -- spot against the centre (the short strike the open spread was sold at), in
  points, signed so `+` is the completing direction (`fly.completing_side_direction`): the way spot
  must travel for the completing spread to cheapen.
- `dist_long` -- spot against the completing long strike, same sign. `dist_center - wing_width`.
- `mid_gap` / `natural_gap` -- price: the order's own spread at mid and at the natural against the
  working limit, in points, signed so `+` means the market had NOT reached the limit at that price
  (a fill with `mid_gap > 0` filled before mid got there). One sign for both order kinds: an entry
  sells for a credit and a completion buys for a debit, and the gap is measured in each order's
  own direction.

`dist_widths` (`dist_center / wing_width`) and `dist_moves` (`dist_center` over the entry's own ATM
straddle in points, the expected-move proxy `engine._classify_vol` already stores) are the two
normalisations, so a distance rule can be read across symbols and across quiet and fast days.

**First touch, not path.** A paper rule replayed against a grid needs only the FIRST time each
threshold was met, so `update_touches` keeps exactly that (plus the best gap and the furthest
distance) -- a compact record any grid value can be replayed from exactly. The live side also keeps
the full path (`fly_order_path`), from which the same touches are derived at read time.
"""

from __future__ import annotations

import json
from datetime import datetime

from cherrypick.flies import fly

# Thresholds every touch record keeps a first-touch time for. A rule at a value OFF these grids
# cannot be replayed from the compact record (the live path can), so they are deliberately wide:
# live's first 48 fills sat 0.0-0.49 points under the best modelled debit and 0-13 points (0-2.6
# widths at 5-wide SPX) past the centre. The gap grid runs two ways from the live limit:
#   positive  the market had not reached the limit -- "does a resting order fill before mid gets
#             there" (the fill rule);
#   negative  the market ran PAST the limit -- the first time a DEEPER resting limit at
#             limit + value would have been reached, i.e. a bigger net target. Down to -0.75 so net
#             targets to credit - 1.00 replay exactly, with the cutoff (widened 2026-10-02 before any
#             shadow row was written: the ledger keeps only the day's lowest debit, whose time is
#             usually after the 15:30 cutoff, so a deeper target could not be replayed from it).
GAP_GRID = (
    -0.75,
    -0.60,
    -0.50,
    -0.45,
    -0.40,
    -0.35,
    -0.30,
    -0.25,
    -0.20,
    -0.15,
    -0.10,
    -0.05,
    0.0,
    0.05,
    0.10,
    0.15,
    0.20,
    0.25,
    0.30,
    0.40,
    0.50,
)  # points
WIDTH_GRID = (-0.5, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)  # wing widths

ENTRY = "entry"
COMPLETION = "completion"

_EPS = 1e-9


def grid_key(value: float) -> str:
    """One spelling for a grid value as a JSON key, so a writer and a reader can never disagree
    about whether 0.1 is "0.1" or "0.10"."""
    return f"{value:.2f}"


# --------------------------------------------------------------------------- geometry
def completing_long_strike(side: str, center: float, width: float) -> float:
    """The strike a legged completion buys (`live_orders.completing_long_strike`, restated from
    the geometry alone so this module needs no position row)."""
    return center + width if side == fly.PUT else center - width


def order_strikes(leg: str, side: str, center: float, width: float) -> tuple[float, float]:
    """(strike bought, strike sold) by the order `leg` names. Entry: sell the centre, buy the wing.
    Completion: buy the far strike, sell the centre again (doubling it into the fly's -2)."""
    if leg == ENTRY:
        wing = center - width if side == fly.PUT else center + width
        return wing, center
    if leg == COMPLETION:
        return completing_long_strike(side, center, width), center
    raise ValueError(f"unknown order leg {leg!r}")


def spot_distances(
    side: str,
    center: float,
    width: float,
    spot: float | None,
    *,
    straddle_points: float | None = None,
) -> dict:
    """The spot distances of `spot` from an open spread, signed so `+` is the completing direction.
    Every value is None when spot is unknown -- never a zero, which would read as "at the centre"."""
    if spot is None:
        return {"dist_center": None, "dist_long": None, "dist_widths": None, "dist_moves": None}
    sign = 1.0 if fly.completing_side_direction(side) == "up" else -1.0
    d_center = sign * (float(spot) - float(center))
    d_long = sign * (float(spot) - completing_long_strike(side, center, width))
    return {
        "dist_center": round(d_center, 4),
        "dist_long": round(d_long, 4),
        "dist_widths": round(d_center / width, 4) if width else None,
        "dist_moves": round(d_center / straddle_points, 4) if straddle_points else None,
    }


def straddle_points(vol_value: float | None, spot: float | None) -> float | None:
    """The entry's ATM straddle in points, from the stored `entry_vol_value` (straddle / spot) and
    the spot it was read at. None when either is missing."""
    if vol_value is None or spot is None or vol_value <= 0:
        return None
    return float(vol_value) * float(spot)


# --------------------------------------------------------------------------- price
def _side_price(q: dict | None, field: str) -> float | None:
    if not q:
        return None
    v = q.get(field)
    return float(v) if isinstance(v, (int, float)) else None


def spread_prices(
    leg: str,
    buy_bid: float | None,
    buy_ask: float | None,
    sell_bid: float | None,
    sell_ask: float | None,
) -> dict | None:
    """The order's spread priced in its OWN direction: a completion as the debit it pays, an entry
    as the credit it receives. `mid` is the leg mids' difference; `natural` is what crossing both
    legs would get (pay the ask on the bought leg, hit the bid on the sold one). None when any of
    the four sides is missing -- a spread with a hole has no price."""
    if None in (buy_bid, buy_ask, sell_bid, sell_ask):
        return None
    debit_mid = (buy_bid + buy_ask) / 2 - (sell_bid + sell_ask) / 2
    debit_natural = buy_ask - sell_bid
    if leg == COMPLETION:
        return {"mid": round(debit_mid, 4), "natural": round(debit_natural, 4)}
    if leg == ENTRY:
        return {"mid": round(-debit_mid, 4), "natural": round(-debit_natural, 4)}
    raise ValueError(f"unknown order leg {leg!r}")


def quotes_for(leg: str, side: str, center: float, width: float, quote_at) -> dict:
    """The four quote sides of an order's two legs, off `quote_at(side, strike)`. Missing sides are
    None -- the caller stores what was there rather than a guessed zero."""
    buy_k, sell_k = order_strikes(leg, side, center, width)
    buy_q, sell_q = quote_at(side, buy_k), quote_at(side, sell_k)
    return {
        "buy_bid": _side_price(buy_q, "bid"),
        "buy_ask": _side_price(buy_q, "ask"),
        "sell_bid": _side_price(sell_q, "bid"),
        "sell_ask": _side_price(sell_q, "ask"),
    }


def gaps(leg: str, limit: float | None, prices: dict | None) -> dict:
    """`mid_gap` / `natural_gap`: how far the market sat from the limit at mid and at the natural,
    `+` meaning not yet reached. A completion (debit) is reached when the price falls to the limit;
    an entry (credit) when it rises to it."""
    if limit is None or prices is None:
        return {"mid_gap": None, "natural_gap": None}
    sign = 1.0 if leg == COMPLETION else -1.0
    return {
        "mid_gap": round(sign * (prices["mid"] - limit), 4),
        "natural_gap": round(sign * (prices["natural"] - limit), 4),
    }


def net_fill_price(fills: list[dict] | None, quantity: int = 1) -> float | None:
    """The per-share net an order actually filled at, from the broker's per-leg fills: bought legs'
    prices less sold legs', over the order's quantity -- positive for a debit order, negative for a
    credit order. None when no fill carries a parseable price: the order's own `price` field is its
    LIMIT, so falling back to it would hide exactly the price improvement this is here to see."""
    total = 0.0
    seen = False
    for f in fills or []:
        try:
            price = float(f.get("fill_price"))
            qty = float(f.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        action = str(f.get("action") or "").lower()
        if "buy" in action:
            total += price * qty
        elif "sell" in action:
            total -= price * qty
        else:
            continue
        seen = True
    if not seen or not quantity:
        return None
    return round(total / quantity, 4)


def broker_price(fills: list[dict] | None, quantity: int, leg: str) -> float | None:
    """The broker's fill price for one order in the order's own direction, positive: the credit an
    entry took, the debit a completion paid. None without parseable leg fills."""
    net = net_fill_price(fills, quantity)
    if net is None:
        return None
    return net if leg == COMPLETION else -net


def booked_price(fills: list[dict] | None, quantity: int, leg: str, limit: float | None) -> tuple:
    """The price a live position books for a filled order, and where it came from: ``(price,
    "broker")`` from the broker's own leg fills, else ``(limit, "limit")``.

    The order status's `price` is the LIMIT, so booking it hid every price improvement (2026-10-12:
    23 of 163 live fills, each checked against the broker's transactions, all to the cent). A limit
    order never fills worse than its limit, so a leg sum that says it did is a status read that
    does not add up (a partial leg list, a parse miss), and the limit is kept rather than a wrong
    number. So is a non-positive price. Pure."""
    return choose_booked(broker_price(fills, quantity, leg), leg, limit)


def choose_booked(price: float | None, leg: str, limit: float | None) -> tuple:
    """`booked_price`'s rule on a broker price already read: keep it unless it is missing,
    non-positive, or worse than the limit. Pure."""
    if price is None or price <= 0:
        return limit, "limit"
    if limit is not None and (price < limit - 1e-9 if leg == ENTRY else price > limit + 1e-9):
        return limit, "limit"
    return round(price, 4), "broker"


def latest_fill_time(fills: list[dict] | None) -> str | None:
    """The broker's own fill time for an order: the LAST leg fill's `filled_at` (an order is filled
    when its final leg is). None when no fill carries one."""
    times = [str(f["filled_at"]) for f in fills or [] if f.get("filled_at")]
    if not times:
        return None
    return max(times, key=parse_ts)


# --------------------------------------------------------------------------- first touch
def update_touches(
    state: dict | None,
    *,
    ts: str,
    mid_gap: float | None,
    natural_gap: float | None,
    dist_widths: float | None,
) -> tuple[dict, bool]:
    """Fold one observation into a first-touch record. Returns (record, changed).

    `mid` / `natural` map each `GAP_GRID` value to the first `ts` the gap was at or under it;
    `dist` maps each `WIDTH_GRID` value to the first `ts` spot was at or past it. `best_gap` and
    `max_dist` keep the extremes with their times, so "never got there" reads with how close it
    came. Never mutates `state`."""
    rec = {
        "mid": dict((state or {}).get("mid") or {}),
        "natural": dict((state or {}).get("natural") or {}),
        "dist": dict((state or {}).get("dist") or {}),
        "best_gap": (state or {}).get("best_gap"),
        "max_dist": (state or {}).get("max_dist"),
    }
    changed = False
    for basis, value in (("mid", mid_gap), ("natural", natural_gap)):
        if value is None:
            continue
        for g in GAP_GRID:
            k = grid_key(g)
            if k not in rec[basis] and value <= g + _EPS:
                rec[basis][k] = ts
                changed = True
    if mid_gap is not None and (rec["best_gap"] is None or mid_gap < rec["best_gap"][0] - _EPS):
        rec["best_gap"] = [mid_gap, ts]
        changed = True
    if dist_widths is not None:
        for x in WIDTH_GRID:
            k = grid_key(x)
            if k not in rec["dist"] and dist_widths >= x - _EPS:
                rec["dist"][k] = ts
                changed = True
        if rec["max_dist"] is None or dist_widths > rec["max_dist"][0] + _EPS:
            rec["max_dist"] = [dist_widths, ts]
            changed = True
    return rec, changed


def touches_from_path(rows: list[dict], *, leg: str, side: str, center: float, width: float) -> dict | None:
    """The first-touch record derived from a stored order path (`fly_order_path` rows, oldest
    first). None for an empty path."""
    rec = None
    for r in rows:
        prices = spread_prices(leg, r.get("buy_bid"), r.get("buy_ask"), r.get("sell_bid"), r.get("sell_ask"))
        g = gaps(leg, r.get("limit_price"), prices)
        d = spot_distances(side, center, width, r.get("spot"))
        rec, _ = update_touches(
            rec,
            ts=r["observed_at"],
            mid_gap=g["mid_gap"],
            natural_gap=g["natural_gap"],
            dist_widths=d["dist_widths"],
        )
    return rec


# --------------------------------------------------------------------------- at the fill
def observation_at(rows: list[dict], when: str | None, *, max_age_seconds: float = 60.0) -> dict | None:
    """The last path observation at or before `when`, if it is no older than `max_age_seconds`.
    Never one AFTER the fill: a later quote describes a market the order had already left."""
    if when is None:
        return None
    t = parse_ts(when)
    best = None
    for r in rows:
        rt = parse_ts(r["observed_at"])
        if rt <= t and (best is None or rt >= parse_ts(best["observed_at"])):
            best = r
    if best is None or (t - parse_ts(best["observed_at"])).total_seconds() > max_age_seconds:
        return None
    return best


def fill_measures(
    order: dict,
    *,
    side: str,
    center: float,
    width: float,
    observation: dict | None,
    spot: float | None,
    spot_source: str | None,
    straddle: float | None,
) -> dict:
    """Every at-fill measure for one filled order, as `fly_live_orders` columns. `observation` is
    the path row nearest before the fill (its quotes give the price distances); `spot` may come
    from it or from the gex spot trail, and `spot_source` says which."""
    leg = order["leg"]
    out = {"fill_spot": spot, "fill_spot_source": spot_source if spot is not None else None}
    out.update(
        {
            f"fill_{k}": v
            for k, v in spot_distances(side, center, width, spot, straddle_points=straddle).items()
        }
    )
    prices = None
    if observation is not None:
        prices = spread_prices(
            leg,
            observation.get("buy_bid"),
            observation.get("buy_ask"),
            observation.get("sell_bid"),
            observation.get("sell_ask"),
        )
    g = gaps(leg, order.get("limit_price"), prices)
    out.update(
        {
            "fill_obs_at": observation["observed_at"] if observation is not None and prices else None,
            "fill_mid": prices["mid"] if prices else None,
            "fill_natural": prices["natural"] if prices else None,
            "fill_mid_gap": g["mid_gap"],
            "fill_natural_gap": g["natural_gap"],
        }
    )
    return out


# --------------------------------------------------------------------------- scoring a rule
def score_rule(orders: list[dict], basis: str, value: float) -> dict:
    """How well "fill at the first touch of `basis` at `value`" reproduces what live actually did.

    Each order is {"filled_at": iso | None, "touches": first-touch record}. A filled order the rule
    touched at or before its fill is a hit (its timing error, touch minus fill in seconds, is kept:
    negative means the rule fires early); one it never touched is a miss. An unfilled order the rule
    touched is a false fill. The path stops when the order resolves, so an order is only ever judged
    on what was observed while it worked."""
    k = grid_key(value)
    hit = miss = false_fill = true_no_fill = 0
    errors: list[float] = []
    for o in orders:
        touched_at = ((o.get("touches") or {}).get(basis) or {}).get(k)
        if o.get("filled_at"):
            if touched_at is not None:
                hit += 1
                errors.append((parse_ts(touched_at) - parse_ts(o["filled_at"])).total_seconds())
            else:
                miss += 1
        elif touched_at is not None:
            false_fill += 1
        else:
            true_no_fill += 1
    n = hit + miss + false_fill + true_no_fill
    return {
        "basis": basis,
        "value": value,
        "orders": n,
        "hit": hit,
        "miss": miss,
        "false_fill": false_fill,
        "true_no_fill": true_no_fill,
        "agreement": round((hit + true_no_fill) / n, 4) if n else None,
        "timing_error_s": quantiles(errors),
    }


# --------------------------------------------------------------------------- the paper shadow
def shadow_outcome(row: dict, basis: str, value: float, *, cutoff: str | None) -> dict | None:
    """One settled paper legged row replayed under a live-like completion: a resting limit at
    `shadow_completion_limit`, filled at the first touch of `basis`/`value` no later than `cutoff`
    (HH:MM ET), paying the limit. A NEGATIVE price value is a deeper resting limit at
    `limit + value` (a bigger net target) and pays that; a positive one is the same limit filled
    before the market quite reached it, and pays the limit. Returns the shadow's completion and P&L at the row's own
    settlement price, or None for a row the shadow cannot judge (no limit, not settled, unparsed
    touches). Fees are the modelled stack -- one vertical's open fee, two if the shadow completed --
    plus the settlement fee that price triggers, so shadow and paper are on the same cost basis."""
    limit = row.get("shadow_completion_limit")
    touches = row.get("shadow_touches")
    settle = row.get("settlement_price")
    if limit is None or touches is None or settle is None or row.get("credit") is None:
        return None
    touched_at = (touches.get(basis) or {}).get(grid_key(value))
    if touched_at is not None and cutoff is not None and _hhmm(touched_at) > cutoff:
        touched_at = None
    qty = row.get("quantity") or 1
    opened = fly.vertical_open_fee(row["symbol"], qty)
    completed = touched_at is not None
    paid = limit + min(value, 0.0) if basis in ("mid", "natural") else limit
    position = {
        "kind": "fly" if completed else "short_vertical",
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "net": row["credit"] - paid if completed else row["credit"],
        "quantity": qty,
        "fees": opened * (2 if completed else 1),
    }
    return {
        "completed": completed,
        "completed_at": touched_at,
        "paid": round(paid, 4) if completed else None,
        "pnl": round(fly.position_pnl(position, settle), 2),
    }


# --------------------------------------------------------------------------- the entry shadow
# Would a live ENTRY order have filled? (2026-10-14.) Live asks the plan's credit floored to the tick
# (`live_orders.entry_spec`; mid less 0.05 on SPX) and fills in seconds -- 43 of its first 50 recorded
# entry orders, most within 0-4 s. The 7 it missed were not a random 14%: on the gex spot trail, spot
# moved toward the completing side in 6 of them (+0.76 pts at 20 s, +1.00 at 60 s) against about half
# of the fills (-0.07 / -0.02). A spread that starts working cheapens under the limit before the order
# fills, so live misses the entries that were going its way. Paper ticks every 15 s and cannot see
# the seconds that decide it, so it keeps the first `ENTRY_SHADOW_WINDOW_SECONDS` after each entry and
# a rule fitted on live's own orders judges the path on the read side. The order path cannot fit that
# rule: it stops at the fill, so a filled order's later moves were never observed.
ENTRY_SHADOW_WINDOW_SECONDS = 60
# Spot's move toward the completing side since entry, in points.
MOVE_GRID = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
ENTRY_BASES = ("move", "mid_gap", "natural_gap")


def entry_move(side: str, spot0: float | None, spot: float | None) -> float | None:
    """Spot's move since entry, signed so `+` is the completing direction (a put spread completes on
    a rise). None when either spot is unknown -- never a zero, which would read as "stood still"."""
    if spot0 is None or spot is None:
        return None
    sign = 1.0 if fly.completing_side_direction(side) == "up" else -1.0
    return round(sign * (float(spot) - float(spot0)), 4)


def entry_observation(
    *,
    seconds: float,
    side: str,
    spot0: float | None,
    spot: float | None,
    limit: float | None,
    prices: dict | None,
) -> dict:
    """One look at an entry's market `seconds` after it: spot, its move toward completion, and the
    entry spread's gaps to the limit live would have asked (`+` = the market had fallen under it)."""
    g = gaps(ENTRY, limit, prices)
    return {
        "s": int(round(seconds)),
        "spot": spot,
        "move": entry_move(side, spot0, spot),
        "mid_gap": g["mid_gap"],
        "natural_gap": g["natural_gap"],
    }


def extend_entry_path(
    path: list | None, obs: dict, *, window: float = ENTRY_SHADOW_WINDOW_SECONDS
) -> tuple[list, bool]:
    """Append `obs` to an entry's path when it is inside the window and later than the last look.
    Returns (path, changed); never mutates `path`."""
    rows = list(path or [])
    if obs["s"] < 0 or obs["s"] > window or (rows and obs["s"] <= rows[-1]["s"]):
        return rows, False
    return [*rows, obs], True


def load_entry_path(raw) -> list | None:
    """A stored entry path (JSON text), or None; an unreadable one reads as None (telemetry)."""
    if not raw:
        return None
    if isinstance(raw, list):
        return raw
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return out if isinstance(out, list) else None


def entry_fills(
    path: list | None, basis: str, value: float, *, window: float = ENTRY_SHADOW_WINDOW_SECONDS
) -> bool | None:
    """The rule "live misses an entry whose market moved `value` or more its way within `window`":
    False when any look after the entry has `basis` at or past `value`, True otherwise. None when
    there is no look after the entry, or none carries the basis -- an unwatched entry is unjudged,
    never a fill."""
    if basis not in ENTRY_BASES:
        raise ValueError(f"unknown entry basis {basis!r}")
    seen = False
    for o in path or []:
        if not 0 < o.get("s", 0) <= window or o.get(basis) is None:
            continue
        seen = True
        if o[basis] >= value - _EPS:
            return False
    return True if seen else None


def score_entry_rule(
    orders: list[dict], basis: str, value: float, *, window: float = ENTRY_SHADOW_WINDOW_SECONDS
) -> dict:
    """How well `entry_fills` reproduces live's entry orders. Each order is {"filled": bool,
    "path": entry path}. A rule that says miss on a filled order is a false miss; one that says fill
    on an unfilled order is a false fill. Orders the rule cannot judge are counted apart."""
    hit = false_miss = false_fill = true_miss = unjudged = 0
    for o in orders:
        verdict = entry_fills(o.get("path"), basis, value, window=window)
        if verdict is None:
            unjudged += 1
        elif o.get("filled"):
            hit, false_miss = (hit + 1, false_miss) if verdict else (hit, false_miss + 1)
        else:
            false_fill, true_miss = (false_fill + 1, true_miss) if verdict else (false_fill, true_miss + 1)
    n = hit + false_miss + false_fill + true_miss
    return {
        "basis": basis,
        "value": value,
        "window": window,
        "orders": n,
        "unjudged": unjudged,
        "hit": hit,
        "false_miss": false_miss,
        "false_fill": false_fill,
        "true_miss": true_miss,
        "agreement": round((hit + true_miss) / n, 4) if n else None,
    }


# --------------------------------------------------------------------------- helpers
def parse_ts(ts: str) -> datetime:
    """A stored or broker timestamp (ISO, any offset, `Z` accepted) as an aware datetime."""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def load_touches(raw) -> dict | None:
    """A stored first-touch record (JSON text), or None. An unreadable one reads as None rather
    than failing -- it is telemetry, and what it held is the only thing lost."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _hhmm(ts: str) -> str:
    """HH:MM of a stored timestamp in its own offset (the ledger stamps ET)."""
    return parse_ts(ts).strftime("%H:%M")


def quantiles(values: list[float]) -> dict | None:
    if not values:
        return None
    s = sorted(values)

    def q(p: float) -> float:
        i = (len(s) - 1) * p
        lo = int(i)
        hi = min(lo + 1, len(s) - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 2)

    return {"n": len(s), "p25": q(0.25), "p50": q(0.5), "p75": q(0.75), "min": s[0], "max": s[-1]}
