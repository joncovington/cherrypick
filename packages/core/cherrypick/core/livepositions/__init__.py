"""cherrypick.core.livepositions -- what the broker holds against what the live ledgers say is held.

The orphan sweeps (2026-10-08) catch a WORKING order the ledger never recorded. This catches the
other half: a FILLED one -- a position at the broker that no ledger knows, or a ledger leg the broker
no longer holds (closed, assigned or expired without the ledger hearing). Either way the live loop is
deciding from a book that is not the account's.

Pure functions over data already fetched: the broker's `get_positions` rows and each live module's
expected legs. Nothing here talks to a broker or opens a ledger.

A leg's key is ``(underlying, expiry, right, strike)`` -- the broker's `underlying_symbol` (SPX for an
SPXW contract) and the OCC symbol's date, type and strike -- and its value is a signed contract count
(long +, short -). Only underlyings a live module trades are compared: anything else in the account
(a discretionary position on another name) is not this check's business.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

Key = tuple[str, str, str, float]


def parse_occ(symbol: str) -> tuple[str, str, str, float] | None:
    """``(root, YYYY-MM-DD, right, strike)`` from an OCC option symbol (``SPXW  261008P05800000``), or
    None when it is not one. The last 15 characters are fixed width: YYMMDD, C/P, strike x 1000."""
    s = (symbol or "").strip()
    if len(s) < 16:
        return None
    tail, root = s[-15:], s[:-15].strip()
    date, right, strike = tail[:6], tail[6], tail[7:]
    if not (root and date.isdigit() and right in "CP" and strike.isdigit()):
        return None
    return root, f"20{date[:2]}-{date[2:4]}-{date[4:]}", right, int(strike) / 1000


def _signed_qty(position: dict[str, Any]) -> int | None:
    try:
        qty = int(float(position.get("quantity") or 0))
    except (TypeError, ValueError):
        return None
    direction = str(position.get("quantity_direction") or "").lower()
    if direction == "short":
        return -abs(qty)
    if direction == "long":
        return abs(qty)
    return None  # "Zero" or unknown: nothing held, or nothing we can sign


def broker_legs(positions: Iterable[dict[str, Any]], underlyings: Iterable[str]) -> dict[Key, int]:
    """The broker's option positions on `underlyings`, netted by key. Rows that are not options, are
    on another underlying, or cannot be signed are left out."""
    wanted = {u.upper() for u in underlyings}
    out: dict[Key, int] = {}
    for p in positions:
        parsed = parse_occ(str(p.get("symbol") or ""))
        under = str(p.get("underlying_symbol") or "").upper()
        qty = _signed_qty(p)
        if parsed is None or qty is None or under not in wanted:
            continue
        _, expiry, right, strike = parsed
        key = (under, expiry, right, strike)
        out[key] = out.get(key, 0) + qty
    return {k: v for k, v in out.items() if v}


def expected_legs(legs: Iterable[dict[str, Any]]) -> dict[Key, int]:
    """Module-reported legs (``{underlying, expiry, right, strike, qty}``) netted by key -- two
    modules on one account sum, as the broker does."""
    out: dict[Key, int] = {}
    for leg in legs:
        key = (
            str(leg["underlying"]).upper(),
            str(leg["expiry"]),
            str(leg["right"]).upper(),
            float(leg["strike"]),
        )
        out[key] = out.get(key, 0) + int(leg["qty"])
    return {k: v for k, v in out.items() if v}


def drop_expired(legs: dict[Key, int], today: str, after_close: bool) -> tuple[dict[Key, int], int]:
    """`legs` without contracts already expired -- an earlier expiry, or today's once the close has
    passed -- and how many keys were dropped. The broker keeps listing an expired contract until its
    overnight processing, while the ledger settles it at the close: comparing them then reads every
    settled 0DTE leg as unrecorded (seen on the first real run, 2026-10-08)."""
    kept = {k: v for k, v in legs.items() if k[1] > today or (k[1] == today and not after_close)}
    return kept, len(legs) - len(kept)


def compare(expected: dict[Key, int], broker: dict[Key, int]) -> list[dict[str, Any]]:
    """Every key where the two disagree, sorted: ``{underlying, expiry, right, strike, expected,
    broker, kind}`` with kind ``unrecorded`` (the broker holds what no ledger does), ``missing`` (a
    ledger leg the broker does not hold) or ``quantity``."""
    diffs = []
    for key in sorted(set(expected) | set(broker)):
        e, b = expected.get(key, 0), broker.get(key, 0)
        if e == b:
            continue
        kind = "unrecorded" if e == 0 else "missing" if b == 0 else "quantity"
        under, expiry, right, strike = key
        diffs.append(
            {
                "underlying": under,
                "expiry": expiry,
                "right": right,
                "strike": strike,
                "expected": e,
                "broker": b,
                "kind": kind,
            }
        )
    return diffs


def confirmed(previous: Iterable[dict[str, Any]], current: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The diffs in `current` that the previous check saw identically. One check can land between a
    broker fill and the tick that records it; the same disagreement twice, minutes apart, cannot."""
    seen = {_diff_id(d) for d in previous}
    return [d for d in current if _diff_id(d) in seen]


def _diff_id(d: dict[str, Any]) -> tuple:
    return (
        d["underlying"],
        d["expiry"],
        d["right"],
        float(d["strike"]),
        int(d["expected"]),
        int(d["broker"]),
    )
