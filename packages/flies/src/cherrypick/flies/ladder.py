"""The debit-first shadow ladder: the same trade, k strikes further out, on the same tape.

`debit-first-atm` buys its debit vertical at the money; `debit-first-up`/`-down` buy theirs at the
0.15-delta strike, which lands anywhere from about 3 to 11 strikes out depending on the day's
volatility. Nothing trades in between, and a strike nobody traded cannot be priced afterwards -- the
stream cache keeps no quote history. So at every fill of an arm that declares `debit_ladder`, this
module prices the same debit-first trade with its centre k strikes further out in each direction,
and the book carries each rung to settlement by the arm's own completion rule.

A rung is a shadow position. It is never booked, never cash, and in no arm's P&L; it exists to be
read by `analytics.debit_ladder`. Every function here is pure: geometry, the entry price, the
per-tick fold and the settlement all take plain dicts and return plain values, and `book.py` does
the writing. The completion test is `engine.evaluate_debit_completion` itself, called on a
synthetic position, so a rung at k = 0 is the anchor's own trade by construction rather than by a
second copy of its rule.
"""

from __future__ import annotations

from cherrypick.flies import engine, fly

DEFAULT_OFFSETS = (1, 2, 3, 4, 5, 6)
DIRECTIONS = ("up", "down")


def offsets(params: dict) -> tuple[int, ...]:
    """The rungs an arm declared (`debit_ladder.offsets_strikes`), or none when it declared no
    ladder. An empty list is an operator turning the ladder off, not a request for the default."""
    cfg = params.get("debit_ladder")
    if not cfg:
        return ()
    return tuple(int(k) for k in cfg.get("offsets_strikes", DEFAULT_OFFSETS))


def rung_geometry(anchor_center: float, direction: str, k: int, increment: float) -> tuple[str, float]:
    """(side, centre) of the rung k strikes out. Up buys a call debit spread centred above (it
    completes as spot rises into it), down a put debit spread centred below -- the same sides the
    delta pair trades, so a rung and a `debit-first-up`/`-down` fill at one centre are one trade."""
    if direction == "up":
        return fly.CALL, anchor_center + k * increment
    if direction == "down":
        return fly.PUT, anchor_center - k * increment
    raise ValueError(f"unknown ladder direction {direction!r}")


def stamp(snapshot: dict, params: dict, anchor: dict) -> list[dict]:
    """One row per (direction, k) for a fresh anchor fill. A rung whose legs are unquoted, or whose
    modelled debit is not a plausible debit, carries its refusal and nothing else -- never a zero
    price. `anchor` is the plan the arm just filled (centre, width, quantity)."""
    increment = params.get("strike_increment", 5)
    width = anchor["wing_width"]
    qty = anchor.get("quantity", 1)
    slip = params.get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    symbol = snapshot["symbol"]
    rows = []
    for direction in DIRECTIONS:
        for k in offsets(params):
            side, center = rung_geometry(anchor["center"], direction, k, increment)
            long_strike = center - width if side == fly.CALL else center + width
            row = {
                "direction": direction,
                "k": k,
                "side": side,
                "center": center,
                "wing_width": width,
                "spot_at_stamp": snapshot.get("underlying_price"),
            }
            if not engine._have(snapshot, side, [center, long_strike]):
                rows.append({**row, "refusal": engine._miss_reason(snapshot, [center, long_strike])})
                continue
            debit = fly.vertical_debit(
                engine.quote(snapshot, side, long_strike), engine.quote(snapshot, side, center), slip
            )
            if debit <= 0:
                rows.append({**row, "refusal": "implausible_debit_quote"})
                continue
            rows.append(
                {
                    **row,
                    "refusal": None,
                    "entry_debit": round(debit, 4),
                    "entry_fee": fly.vertical_open_fee(symbol, qty),
                    "quantity": qty,
                }
            )
    return rows


def as_position(rung: dict) -> dict:
    """The synthetic position the arm's own functions read: an open long vertical until the rung's
    first qualifying tick, a fly after it."""
    qty = rung.get("quantity") or 1
    fees = rung["entry_fee"]
    if rung.get("complete_credit") is None:
        return {
            "kind": "long_vertical",
            "side": rung["side"],
            "center": rung["center"],
            "wing_width": rung["wing_width"],
            "net": -rung["entry_debit"],
            "quantity": qty,
            "fees": fees,
            "entry_mode": "debit_first",
            "status": "open",
        }
    return {
        "kind": "fly",
        "side": rung["side"],
        "center": rung["center"],
        "wing_width": rung["wing_width"],
        "net": rung["complete_credit"] - rung["entry_debit"],
        "quantity": qty,
        "fees": fees + (rung.get("completion_fee") or 0.0),
        "entry_mode": "debit_first",
        "status": "open",
    }


def fold(snapshot: dict, params: dict, rung: dict, when: str) -> dict:
    """This tick's changes to one live rung: the first qualifying completion (taken, as the arm
    takes it) and the running max of the completing credit. Returns only the fields that moved,
    so an empty dict means nothing to write."""
    if rung.get("refusal") or rung.get("entry_debit") is None:
        return {}
    pos = as_position({**rung, "complete_credit": None})
    done, _reason, plan = engine.evaluate_debit_completion(snapshot, pos, params)
    if plan is None:
        return {}
    changed = {}
    best = rung.get("best_credit")
    if best is None or plan["credit"] > best:
        changed["best_credit"] = plan["credit"]
        changed["best_credit_at"] = when
    if done and rung.get("complete_credit") is None:
        changed["complete_credit"] = plan["credit"]
        changed["completion_fee"] = plan["completion_fee"]
        changed["first_complete_at"] = when
    return changed


def settle(rung: dict, settlement_price: float) -> float | None:
    """The rung's P&L at the print through `engine.settle`, so payoff, fees and the expiry fee are
    the module's own. None for a refused rung: it never existed, so it has no result."""
    if rung.get("refusal") or rung.get("entry_debit") is None:
        return None
    pos = {**as_position(rung), "position_id": "ladder"}
    [settled] = engine.settle([pos], settlement_price)
    return settled["pnl"]
