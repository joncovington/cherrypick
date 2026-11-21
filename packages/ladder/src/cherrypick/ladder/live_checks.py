"""The ladder's live checks (issue #1337): the split source and the positions cross-check. Pure.

Nothing here reads a broker, a ledger or a file. `live_tick` hands these functions what it read and acts
on what they return, and a source scan in the tests keeps every trading import out of this file.

**Splits.** The suite has no split feed fit for a live tick. Dolt's split table is refreshed by hand
and is known to hold splits the prices never made and to miss real ones; the public split history is
fetched once per symbol by a script; the broker has no split endpoint; and a package may not import a
script. So the split source is the human's: `live.splits`, a list of
`{"fund": "TQQQ", "ex_date": "2026-12-01", "to": 2, "for": 1}` (`to` new shares for `for` old ones),
written by hand before or on the ex-date. `parse_splits` reads it and reports what it cannot use. An
entry that cannot be read is a problem, never a skipped entry: the tick refuses new buys until it is
fixed, because a split it did not understand is a split it may not be applying.

A split applies to a lot bought before its ex-date, once. The ledger records each application
(`live_db.lot_splits`), and `due` returns only the ones a lot has not had.

**The positions cross-check.** The broker's equity shares in each fund against the ledger's open live
shares in it. Ours only: the account may hold the same fund by hand, so

- broker shares below the ledger's are a **shortfall**: shares the ledger believes it holds are gone,
  and the fund's new buys stop;
- broker shares above the ledger's are a **surplus**: the user's own shares. Reported, never adopted,
  and no reason to stop;
- a mismatch whose ratio is a whole-number split factor (2:1 through 20:1, or the reverse) is a
  **suspected split**: reported, and the fund's new buys stop (a split the ledger has not heard of
  prices every buy off a close that may be on the wrong side of it), but nothing is restated. Only a
  `live.splits` entry restates;
- a positions read that fails is unreadable: every fund's new buys stop.

**Settled cash** is not here: the rule is shared, `cherrypick.core.live.cash.settled_cash`, and
`live_tick.check_cash` hands it the broker's figures and the ledger's unsettled sales.

Resting sells are never touched by any of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

MAX_WHOLE_FACTOR = 20
EQUITY = "Equity"


# --------------------------------------------------------------------------- splits
@dataclass(frozen=True)
class Split:
    fund: str
    ex_date: str  # ISO
    to: float  # new shares ...
    per: float  # ... for this many old ones

    @property
    def factor(self) -> float:
        return self.to / self.per

    @property
    def ratio(self) -> str:
        return f"{self.to:g}:{self.per:g}"


def _positive(value) -> bool:
    return (
        isinstance(value, int | float) and not isinstance(value, bool) and value > 0 and math.isfinite(value)
    )


def parse_splits(live: dict) -> tuple[list[Split], list[str]]:
    """The entries of `live.splits` that can be used, in ex-date order, and a plain sentence for each
    that cannot. A missing key is no splits; anything but a list is a problem."""
    raw = live.get("splits")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["splits must be a list of {fund, ex_date, to, for}"]
    out: list[Split] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(raw):
        label = f"splits[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object {{fund, ex_date, to, for}}")
            continue
        fund = str(entry.get("fund") or "").strip().upper()
        try:
            ex = date.fromisoformat(str(entry.get("ex_date")))
        except ValueError:
            ex = None
        to, per = entry.get("to"), entry.get("for")
        if not fund:
            problems.append(f"{label} has no fund")
        elif ex is None:
            problems.append(f"{label} ({fund}) has no ISO ex_date")
        elif not (_positive(to) and _positive(per)):
            problems.append(f"{label} ({fund}) needs positive numbers for `to` and `for`")
        elif to == per:
            problems.append(f"{label} ({fund}) is a {to:g}:{per:g} split, which changes nothing")
        elif (fund, ex.isoformat()) in seen:
            problems.append(f"{label} ({fund}) repeats the split on {ex.isoformat()}")
        else:
            seen.add((fund, ex.isoformat()))
            out.append(Split(fund, ex.isoformat(), float(to), float(per)))
    return sorted(out, key=lambda s: (s.ex_date, s.fund)), problems


def due(splits: list[Split], lot: dict, today: date, applied: set[tuple[str, str]]) -> list[Split]:
    """The splits a lot has not had: its fund's, gone ex after the lot was bought, on or before today,
    and not already recorded as applied to it (`applied` holds (position id, ex date))."""
    symbol = str(lot["symbol"]).upper()
    bought = str(lot["buy_session"])
    return [
        s
        for s in splits
        if s.fund == symbol
        and bought < s.ex_date <= today.isoformat()
        and (lot["position_id"], s.ex_date) not in applied
    ]


def restated_reference(
    raw: float | None, splits: list[Split], fund: str, *, bid, prev_session: date, today: date
):
    """The fund's reference close on the terms the market now trades at.

    The close the stream cache holds is the previous session's. If a split went ex after that session
    (and on or before today) it may still be on the old terms or the exchange may already have
    restated it; the cache does not say. The fund's own bid does: the close is divided by the split's
    factor only when that lands nearer the bid than the raw close does. With no bid there is nothing
    to judge by, and the answer is None, which blocks the fund's buys."""
    if not raw:
        return raw
    pending = [
        s for s in splits if s.fund == fund and prev_session.isoformat() < s.ex_date <= today.isoformat()
    ]
    if not pending:
        return raw
    if not bid or bid <= 0:
        return None
    adjusted = raw
    for s in pending:
        candidate = adjusted / s.factor
        if abs(math.log(bid / candidate)) < abs(math.log(bid / adjusted)):
            adjusted = candidate
    return adjusted


# --------------------------------------------------------------------------- positions
def split_like(ledger: int, held: float) -> str | None:
    """A whole-number split that explains `held` shares against `ledger`, as `n:1` or `1:n`, or None.
    The reverse reading allows for the broker's rounding down of the fraction."""
    if ledger <= 0 or held <= 0 or held == ledger:
        return None
    for n in range(2, MAX_WHOLE_FACTOR + 1):  # an exact ratio first
        if held == ledger * n:
            return f"{n}:1"
        if held * n == ledger:
            return f"1:{n}"
    for n in range(2, MAX_WHOLE_FACTOR + 1):  # then a reverse split that dropped a fraction
        if held == ledger // n:
            return f"1:{n}"
    return None


def equity_shares(rows: list[dict], fund: str) -> float:
    """The broker's shares of `fund`, signed. Options and every other instrument are not shares."""
    return sum(
        float(r.get("quantity") or 0)
        for r in rows
        if str(r.get("instrument_type")) == EQUITY and str(r.get("symbol") or "").upper() == fund
    )


def cross_check(ledger: dict[str, int], rows: list[dict] | None, funds: list[str]) -> dict:
    """Compare the ledger's open shares per fund with the broker's.

    Returns {"readable", "funds": {fund: {ledger, broker, status, suspected_split}}, "blocked":
    {fund: reason}}. `rows` None is an unreadable read: every fund is blocked."""
    wanted = list(dict.fromkeys([*funds, *ledger]))
    if rows is None:
        reason = "positions_unreadable"
        return {"readable": False, "funds": {}, "blocked": {f: reason for f in wanted}}
    out: dict = {"readable": True, "funds": {}, "blocked": {}}
    for fund in wanted:
        mine, held = int(ledger.get(fund, 0)), equity_shares(rows, fund)
        suspect = split_like(mine, held)
        if held < mine:
            status = "shortfall"
        elif held > mine:
            status = "surplus"
        else:
            status = "match"
        out["funds"][fund] = {
            "ledger": mine,
            "broker": held,
            "status": status,
            "suspected_split": suspect,
        }
        if suspect:
            out["blocked"][fund] = f"suspected_split {suspect}: broker holds {held:g}, ledger {mine}"
        elif status == "shortfall":
            out["blocked"][fund] = f"positions_shortfall: broker holds {held:g}, ledger {mine}"
    return out
