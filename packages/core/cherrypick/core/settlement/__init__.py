"""American physical settlement: the arithmetic two modules must not disagree about.

Deliberately small. `calendars` and `pmcc` both model physical delivery — pmcc's CLAUDE.md says it
uses "the calendars decomposition" — so the *money* has to be computed identically or the two
modules' paper results stop being comparable. That is the bar for putting something here.

**The disposal LOOPS are deliberately not here.** Both modules have a `_dispose_shares`/
`_dispose_longs` pass, and folding those in was considered and rejected: they differ in two real
ways (calendars filters on `back_expiration`; pmcc finalizes the position afterward), and what is
left once those are parameterized is I/O plumbing — the ledger query, the writer, the logger — that
would need roughly as many injection hooks as the twenty lines of logic it wrapped. An adapter
bigger than the duplication it removes is not a dedup. The spot reads inside those loops already
share one implementation via `cherrypick.core.streamcache`, and the fee stack already shares one via
`cherrypick.core.fees`, so what remained genuinely common is the function below.
"""

from __future__ import annotations

__all__ = ["share_pnl"]


def share_pnl(direction: str, shares: int, basis: float, price: float) -> float:
    """Dollar P&L of a delivered share position disposed at `price`. Long earns the rise.

    Rounded to the cent because it is booked, not intermediate: the calendars derivation validates
    itself against the real books to the cent, so an unrounded value here would show up there as a
    validation failure rather than as the rounding difference it actually is.
    """
    move = price - basis if direction == "long" else basis - price
    return round(move * shares, 2)
