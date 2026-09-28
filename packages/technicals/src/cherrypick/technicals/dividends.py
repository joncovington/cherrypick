"""Reconciling Dolt's dividend history with tastytrade's.

**Why two sources.** Checked against the vendor's own adjusted bars for 40 names (2026-09-27), Dolt's
`dividend` table alone agreed on 86.8% of prices. It has gaps (CSCO's and CHT's July 2024 dividends
are missing), zeros (BAP's September 2024 dividend is 0.0) and wrong amounts (BAP's May 2024 is 0.939
where the payout was 9.2875). Tastytrade's per-symbol history, fetched by
scripts/fetch_dividends.py, has those right -- preferring it lifted agreement to 93.3% -- but it has
errors of its own: BAP's May 2026 is 50.00 where the vendor's bars show Dolt's 14.478, and AU's
March 2024 dividend sits a day apart in the two (13th against 14th), which a naive union counts twice.

**The rule**, pure and per symbol:

1. An entry in one source and an entry in the other within `PAIR_DAYS` of it are the same event.
2. An event in only one source is kept (a gap in the other), unless its amount is not positive.
3. A paired event within `OUTLIER` (3x) takes tastytrade's date and amount. Modest disagreements
   are mostly one source carrying a split-adjusted or currency-converted amount, and against the
   vendor's bars tastytrade's was right more often (CHT 2026, 1.611 against 1.653).
4. A paired event further apart than that is a data error on one side: it takes whichever amount
   is closer, in ratio, to the median of the symbol's other dividends -- BAP's usual payout is ~10,
   so 0.939 and 50.00 both lose -- and is reported as a dispute, so the choice is visible.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from .adjust import Dividend

PAIR_DAYS = 3
# Two amounts more than this far apart cannot both be the same payout: one is a data error.
OUTLIER = 3.0


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def reconcile(dolt: list[Dividend], tasty: list[Dividend]) -> tuple[list[Dividend], list[dict]]:
    """(the dividends to adjust with, the disputes). Either list may be empty."""
    dolt = sorted((d for d in dolt if d.amount > 0), key=lambda d: d.ex_date)
    tasty = sorted((d for d in tasty if d.amount > 0), key=lambda d: d.ex_date)
    used: set[int] = set()
    pairs: list[tuple[Dividend | None, Dividend | None]] = []
    for t in tasty:
        match = next(
            (i for i, d in enumerate(dolt) if i not in used and _days(d.ex_date, t.ex_date) <= PAIR_DAYS),
            None,
        )
        if match is None:
            pairs.append((None, t))
        else:
            used.add(match)
            pairs.append((dolt[match], t))
    pairs += [(d, None) for i, d in enumerate(dolt) if i not in used]

    every = [x.amount for pair in pairs for x in pair if x is not None]
    out: list[Dividend] = []
    disputes: list[dict] = []
    for d, t in pairs:
        if d is None or t is None:
            out.append(d or t)
            continue
        ratio = max(d.amount, t.amount) / min(d.amount, t.amount)
        if ratio <= OUTLIER:
            # Agreeing, or disagreeing modestly: tastytrade's. Modest disagreements are mostly one
            # source recording a later split-adjusted or currency-converted amount, and against the
            # vendor's bars tastytrade's was right more often (CHT 2026, 1.611 against 1.653).
            out.append(t)
            continue
        others = [a for a in every if a not in (d.amount, t.amount)]
        typical = statistics.median(others) if others else None
        if typical:
            pick = min((t, d), key=lambda x: abs(math.log(x.amount / typical)))
        else:
            pick = t
        out.append(pick)
        disputes.append(
            {
                "ex_date": t.ex_date,
                "dolt": d.amount,
                "tastytrade": t.amount,
                "typical": typical,
                "chose": "tastytrade" if pick is t else "dolt",
            }
        )
    return sorted(out, key=lambda x: x.ex_date), disputes
