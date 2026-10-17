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
   is closer, in ratio, to the median of the symbol's other dividends within `TYPICAL_DAYS` of it
   -- BAP's usual payout is ~10, so 0.939 and 50.00 both lose -- and is reported as a dispute, so
   the choice is visible. The window matters: over BAP's whole history the median is ~1.5, and
   0.939 won (2026-10-17, against the vendor's bars).

**Two repairs before pairing** (2026-10-17, from the vendor bar check's failing names):

- **Tastytrade states some names' history on today's share basis.** AAPL's 2014 dividend is 0.1175
  there and 0.47 in Dolt, a quarter of it, the 2020 4-for-1 split; BKNG's 2024 ones are 0.35
  against 8.75, its 2026 25-for-1. Adjustment wants each amount on its own date's basis, so a
  split-adjusted amount under-adjusts every bar before it (BKNG 27% of prices agreeing, AVGO 77%,
  FAST, WSM, WMT, IBKR, APH). It is not every name -- the sector funds' are on their own basis --
  so an amount is restated only where Dolt confirms it: a Dolt entry for the same event equal to
  tastytrade's times the splits after it, within `BASIS_TOL`.
- **An exact repeat inside one source is one event.** Tastytrade lists EWZ's 2025-12-30
  distribution twice; both copies were applied.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from .adjust import Dividend, Split

PAIR_DAYS = 3
# Two amounts more than this far apart cannot both be the same payout: one is a data error.
OUTLIER = 3.0
# How close tastytrade's amount, restated by the later splits, must come to Dolt's to be taken as
# split-adjusted. Splits are integer ratios, so a match is exact but for rounding.
BASIS_TOL = 0.02
# The dividends a dispute's usual payout is read from: those within this many days of it.
TYPICAL_DAYS = 730


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _later_split_factor(ex_date: str, splits: list[Split]) -> float:
    """Shares now per share on `ex_date`: the product of every split after it."""
    f = 1.0
    for s in splits:
        if s.ex_date > ex_date and s.to_factor > 0 and s.for_factor > 0:
            f *= s.to_factor / s.for_factor
    return f


def own_basis(dolt: list[Dividend], tasty: list[Dividend], splits: list[Split]) -> list[Dividend]:
    """Tastytrade's dividends with each split-adjusted amount Dolt confirms restated on its own
    date's share basis (see the module note); every other amount as it was."""
    out = []
    for t in tasty:
        f = _later_split_factor(t.ex_date, splits)
        if f != 1.0:
            match = next((d for d in dolt if d.amount > 0 and _days(d.ex_date, t.ex_date) <= PAIR_DAYS), None)
            if match is not None and abs(t.amount * f / match.amount - 1.0) <= BASIS_TOL:
                t = Dividend(t.ex_date, t.amount * f)
        out.append(t)
    return out


def reconcile(
    dolt: list[Dividend], tasty: list[Dividend], splits: list[Split] = ()
) -> tuple[list[Dividend], list[dict]]:
    """(the dividends to adjust with, the disputes). Either list may be empty. `splits` are the
    symbol's splits, all of them (tastytrade's basis is today's, whatever the bars run to)."""
    dolt = sorted(dict.fromkeys(d for d in dolt if d.amount > 0), key=lambda d: d.ex_date)
    tasty = sorted(dict.fromkeys(d for d in tasty if d.amount > 0), key=lambda d: d.ex_date)
    tasty = own_basis(dolt, tasty, list(splits))
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

    every = [x for pair in pairs for x in pair if x is not None]
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
        near = [x.amount for x in every if _days(x.ex_date, t.ex_date) <= TYPICAL_DAYS]
        others = [a for a in (near or [x.amount for x in every]) if a not in (d.amount, t.amount)]
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
