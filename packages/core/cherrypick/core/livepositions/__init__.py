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


def in_scope(
    broker: dict[Key, int], expected: dict[Key, int], declared: Iterable[str] | None
) -> tuple[dict[Key, int], int]:
    """`broker` cut to the expiries the live modules can hold, and how many keys were left out.
    `declared` is the union of the modules' declared expiries, or None when any module on the account
    declares none -- then every expiry is compared, as before. An expiry the ledger holds is always in
    scope, so a ledger leg is never compared against nothing. Added 2026-10-30: a hand-placed SPX
    condor expiring 11-20 in MEIC's account (MEIC enters today's expiry only) read as four unrecorded
    fills and raised a CRITICAL."""
    if declared is None:
        return dict(broker), 0
    expiries = set(declared) | {k[1] for k in expected}
    kept = {k: v for k, v in broker.items() if k[1] in expiries}
    return kept, len(broker) - len(kept)


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


# --------------------------------------------------------------------------- cross-module legs
#
# 2026-10-15. flies, bwb and MEIC trade SPX in ONE account, and each loop's leg-sign rule
# (`core.entry.sign_conflict`) sees its own ledger only. The broker nets per contract across the
# account, so MEIC short the 5800 put beside flies long it is one flat position two ledgers both
# think they hold: a later close is rejected or opens a new position, and neither P&L is right.
# Same-sign stacking is fine -- each loop closes its own quantity. So every live loop publishes the
# legs it holds or has working (`publish_open_legs`) each tick, and refuses an ENTRY whose
# opening legs would sit opposite another loop's (`cross_module_conflict`). Files only; a stale
# file can only refuse more, and a missing one is a loop that has never ticked.

OPEN_LEGS_DIRNAME = "live_open_legs"


def _open_legs_dir():
    from cherrypick.core import home as _home

    return _home.state_dir() / OPEN_LEGS_DIRNAME


def publish_open_legs(module: str, legs: Iterable[dict[str, Any]], *, now_iso: str) -> None:
    """Write `module`'s held and working legs (``{underlying, expiry, right, strike, qty}``) for the
    other live loops to read. Best-effort: a failed write leaves the previous file, which can only
    make the others refuse more."""
    import json

    try:
        target = _open_legs_dir() / f"{module}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        rows = [
            {"underlying": k[0], "expiry": k[1], "right": k[2], "strike": k[3], "qty": q}
            for k, q in sorted(expected_legs(legs).items())
        ]
        tmp.write_text(json.dumps({"module": module, "at": now_iso, "legs": rows}), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def other_modules_legs(module: str) -> dict[Key, int]:
    """Every other live loop's published legs, netted by key as the broker would. An unreadable file
    contributes nothing."""
    import json

    legs: list[dict[str, Any]] = []
    try:
        files = list(_open_legs_dir().glob("*.json"))
    except OSError:
        return {}
    for f in files:
        if f.stem == module:
            continue
        try:
            legs.extend(json.loads(f.read_text(encoding="utf-8")).get("legs") or [])
        except (OSError, ValueError, AttributeError):
            continue
    try:
        return expected_legs(legs)
    except (KeyError, TypeError, ValueError):
        return {}


def order_opening_legs(spec: dict[str, Any], underlying: str) -> dict[Key, int]:
    """The legs an order spec would OPEN, keyed like everything else here. Closing legs are left out:
    a close reduces this loop's own position and cannot set it against another's. Raises ValueError
    on a leg that is not an OCC option symbol -- a leg this cannot read must not pass as harmless."""
    out: dict[Key, int] = {}
    for leg in spec.get("legs") or []:
        action = str(leg.get("action") or "").lower()
        if "to open" not in action:
            continue
        parsed = parse_occ(str(leg.get("symbol") or ""))
        if parsed is None:
            raise ValueError(f"order leg {leg.get('symbol')!r} is not an OCC option symbol")
        _, expiry, right, strike = parsed
        sign = -1 if action.startswith("sell") else 1
        key = (underlying.upper(), expiry, right, strike)
        out[key] = out.get(key, 0) + sign * int(leg.get("quantity") or 1)
    return out


def cross_module_conflict(module: str, underlying: str, spec: dict[str, Any]) -> Key | None:
    """The first contract where `spec` would open a leg opposite one another live loop holds or has
    working, or None. A spec this cannot read is reported as a conflict on a placeholder key -- the
    entry is refused rather than sent unchecked."""
    try:
        proposed = order_opening_legs(spec, underlying)
    except ValueError:
        return (underlying.upper(), "?", "?", 0.0)
    others = other_modules_legs(module)
    for key, qty in proposed.items():
        held = others.get(key, 0)
        if held and (held > 0) != (qty > 0):
            return key
    return None


def describe(key: Key) -> str:
    under, expiry, right, strike = key
    return f"{under} {expiry} {strike:g}{right}"


def _diff_id(d: dict[str, Any]) -> tuple:
    return (
        d["underlying"],
        d["expiry"],
        d["right"],
        float(d["strike"]),
        int(d["expected"]),
        int(d["broker"]),
    )
