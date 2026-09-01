"""Resolving a position whose options have already expired.

The module's management rules all assume a market: they read a mark, compare it to a threshold, and
close by trading out. That assumption quietly fails the morning after expiration. An expired
contract does not stop quoting -- the feed keeps answering with a zero bid against a stale ask,
which prices out as a 200% spread and a plausible-looking mid -- so the position marks "usable",
decides to close, and is then refused by the execution gate for a spread that will never narrow
because there is nothing left to trade. `provider.snapshot` now refuses those legs outright; this
module is the other half, and supplies what they actually need.

What an expired option is worth is not a quote at all. It is intrinsic against the settlement
print: a number with no bid-ask width, no slippage, and no dependence on whether anyone is still
making a market. That comes from the local `stocks.ohlcv` history the scanner already reads for its
winrate work, so settlement is a deterministic local lookup rather than a network call -- the same
answer next week as today, which is the only kind of number worth putting in a ledger.

Two shapes arrive here, and they are genuinely different trades:

  * Every leg expired -- the credit structures (iron condor, iron fly, broken-wing butterfly,
    directional credit spread). All legs share one expiration, so the whole position settles at
    intrinsic and nothing survives. No share delivery has to be modelled: an assigned short and an
    exercised long inside a defined-risk vertical net to the cash difference between the strikes,
    which is exactly what intrinsic already says. This is why earnings is tractable where pmcc's
    assignment problem was not.

  * Only the front expired -- the calendars, whose back month outlives the front by weeks. The
    structure's thesis completes when the front goes, so the position ends there: the front settles
    at intrinsic and the back is closed at its own real, still-quoted market. That is the exit
    `db_paper`'s schema comment has named `front_expiry` since before anything produced one.

The result is shaped exactly like a `provider.snapshot`, so `close_position` records a settlement
through the same arithmetic as every other close. That is deliberate and worth keeping: a
settlement priced by a better formula than the one every other row used would be a different
measurement wearing the same column name.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from cherrypick.earnings import provider, scanner


def option_intrinsic(occ: str, underlying_close: float) -> float | None:
    """What an expired option is worth against its settlement print, or None if unparseable.

    Cash value only. A call is worth what spot exceeds the strike by, a put the reverse, and
    neither is ever worth less than nothing -- an out-of-the-money option expires worthless rather
    than owing anything.
    """
    raw = occ or ""
    kind = raw[12:13].upper()
    try:
        strike = int(raw[-8:]) / 1000.0
    except (TypeError, ValueError):
        return None
    if kind == "C":
        return max(0.0, underlying_close - strike)
    if kind == "P":
        return max(0.0, strike - underlying_close)
    return None


def settlement_close(symbol: str, expiry: date, config: dict) -> float | None:
    """The underlying's close on the expiration day itself, from local ohlcv history.

    Strictly that day: `_nearest_close` walks backwards up to ten days to find a row, which is the
    right behaviour for the winrate sampling it was written for and the wrong one here. Settling
    against a close from three days earlier would be a fabricated print, so a non-exact match is
    refused and the caller leaves the position open to be reported rather than resolved on a guess.
    """
    row = scanner._nearest_close(symbol, expiry, "on_or_before", config)
    if not row or row.get("close") is None:
        return None
    got = row.get("date")
    if isinstance(got, datetime):
        got = got.date()
    if got != expiry:
        return None
    return float(row["close"])


def split_legs(legs: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """`(expired, live)` -- the legs past their last trading day, and those still listed."""
    gone = set(provider.expired_legs(legs, today))
    return [leg for leg in legs if leg["symbol"] in gone], [
        leg for leg in legs if leg["symbol"] not in gone
    ]


def due(trade: dict, now: datetime) -> str | None:
    """`"expired"`, `"front_expiry"`, or None if nothing has expired yet.

    The reason names the shape, because the two are different exits and pooling them would hide
    which one the ledger is describing.
    """
    legs = provider.legs_from_trade(trade)
    if not legs:
        return None
    expired, live = split_legs(legs, now.date())
    if not expired:
        return None
    return "front_expiry" if live else "expired"


def resolve(
    trade: dict,
    config: dict,
    now: datetime,
    *,
    max_quote_age_seconds: float | None = None,
    rest_snapshot=None,
) -> dict:
    """A priced snapshot for a position with expired legs, shaped like `provider.snapshot`.

    Expired legs get a zero-width quote at intrinsic -- which is not a shortcut but the fact:
    settlement has no spread, so the cost model's slippage haircut correctly comes out at zero and
    the fee stack reduces to the clearing and regulatory pass-throughs that an expiring contract
    really does incur.

    `rest_snapshot` is the broker fallback for the surviving half of a calendar, injected rather
    than imported so this module stays below the loop. It matters more here than when marking: the
    front is already settled and unpriceable forever, so a back month the cache happens not to be
    serving would otherwise hold the whole position open until it expired too -- three more weeks of
    exactly the stall this module exists to end.
    """
    legs = provider.legs_from_trade(trade)
    if not legs:
        return {"ok": False, "reason": "no_legs_recorded"}
    symbol = trade.get("symbol") or ""
    expired, live = split_legs(legs, now.date())
    if not expired:
        return {"ok": False, "reason": "nothing_expired"}

    quotes: dict[str, dict] = {}
    for leg in expired:
        expiry = provider.expiry_from_occ(leg["symbol"])
        close = settlement_close(symbol, expiry, config) if expiry else None
        if close is None:
            return {"ok": False, "reason": "no_settlement_close", "source": "settlement"}
        value = option_intrinsic(leg["symbol"], close)
        if value is None:
            return {"ok": False, "reason": "unparseable_leg", "source": "settlement"}
        quotes[leg["symbol"]] = {"bid": value, "ask": value, "mid": value, "iv": None, "delta": None}

    spot = None
    if live:
        # The surviving legs are a position in their own right, so they are priced as one -- reusing
        # the cache path's freshness and spread handling rather than growing a second copy of it.
        remainder = dict(trade)
        remainder["legs_json"] = json.dumps(live)
        # `expiration` on the row names the FRONT month, which is the half that just expired. The
        # broker path keys its chain lookup off that column, so leaving it would ask the dead
        # expiration for the surviving legs and find nothing -- the remainder's own expiry is the
        # only one that can price it.
        live_expiries = {provider.expiry_from_occ(one["symbol"]) for one in live}
        if len(live_expiries) == 1:
            only = live_expiries.pop()
            if only is not None:
                remainder["expiration"] = only.isoformat()
        # The tick's own clock, not the machine's: a settlement decided against one reading of "now"
        # and priced against another is two measurements, and the freshness rule is the half that
        # would silently disagree.
        sub = provider.snapshot(
            remainder,
            now_ts=now.timestamp(),
            max_quote_age_seconds=max_quote_age_seconds,
        )
        if not sub.get("ok") and rest_snapshot is not None:
            sub = rest_snapshot(remainder)
        if not sub.get("ok"):
            return {"ok": False, "reason": sub.get("reason"), "source": "settlement"}
        quotes.update(sub["quotes"])
        spot = sub.get("spot")

    return {
        "ok": True,
        "source": "settlement",
        "quotes": quotes,
        "spot": spot,
        "fresh": len(quotes),
        "stale": 0,
        # No width anywhere in the settled half, and the live half was already gate-checked above.
        "max_spread_pct": 0.0,
        "settled_legs": [leg["symbol"] for leg in expired],
    }
