"""Large options orders: prints grouped into orders, each order's side read from its fill, and the
size distribution the "large" test needs. Pure: plain rows in, plain rows out.

The rules are `docs/flow-signal-plan.md`'s, declared before any history was read, and they are the
same for every source: the ThetaData fetch here, and the broker's nightly feed later, which imports
this module so the two cannot read an order differently.

A **print** is a dict: ``expiration`` (ISO date), ``strike``, ``right`` ("CALL"/"PUT"), ``ts_ms``,
``size``, ``price``, ``bid`` and ``ask`` (the quote standing at the print; None where absent), and
``kind``: what its trade condition says it is (`print_kind`).

- **Excluded:** every leg of a multi-leg or stock-and-option order (a spread's legs are not two
  opinions) and every cancelled print.
- **An order** is a run of prints in one contract, on one side, each within `ORDER_GAP_S` of the one
  before: an intermarket sweep across exchanges at one instant, or an algorithm slicing a block.
- **Its side** is read from where each print filled against the quote: edge = (price - mid) / half
  the spread; at or past +`READ_EDGE` bought, at or past -`READ_EDGE` sold, between unread. The
  QuikOptions scorer's own edge, so the two agree on what "bought" means. Inferred, not known: the
  print says where it filled, not who started it.
- **Its size** is premium: contracts x 100 x price.

"Large" needs a cut (`CUTS`) and the name's own top `TOP_SHARE` of orders over its previous
`LOOKBACK_SESSIONS` sessions. The second needs every order's premium, not only the large ones, so
each name-day keeps a histogram of its orders' premiums on fixed log buckets (`histogram`), and the
top-share cut is read off the summed histograms of the sessions before (`top_share_cut`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

#: ThetaData's trade conditions (its condition table). An intermarket sweep, single-leg auction ISO
#: and single-leg cross ISO are sweeps.
ISO = frozenset({95, 126, 128})
#: Spread and combo prints, every multi-leg condition (electronic, auction, cross, floor, against
#: single legs) and every stock-and-option one.
MULTI_LEG = frozenset({35, 38, *range(130, 144)})
CANCELLED = frozenset({40, 41, 42, 43, 44})

#: Seconds between two prints of one order. Declared before the test, never tuned by it.
ORDER_GAP_S = 3.0
READ_EDGE = 0.5
#: Premium cuts for "large", declared in the plan, never extended.
CUTS = (50_000.0, 100_000.0, 250_000.0)
#: Orders kept whole: every order at or above the smallest cut, whatever its side.
KEEP_PREMIUM = CUTS[0]
TOP_SHARE = 0.01
LOOKBACK_SESSIONS = 20
#: Next-session open interest must rise by at least this share of the order for it to have opened.
OPEN_SHARE = 0.5

#: The premium histogram: log10 buckets of BUCKET wide from LOG_LO ($100) to LOG_HI ($10M), plus an
#: underflow bucket first and an overflow bucket last.
LOG_LO, LOG_HI, BUCKET = 2.0, 7.0, 0.05
N_BUCKETS = round((LOG_HI - LOG_LO) / BUCKET) + 2


def print_kind(condition: int) -> str:
    """'cancelled', 'multi_leg', 'sweep' or 'single' from a ThetaData trade condition."""
    c = int(condition)
    if c in CANCELLED:
        return "cancelled"
    if c in MULTI_LEG:
        return "multi_leg"
    return "sweep" if c in ISO else "single"


def edge(price: float, bid: float | None, ask: float | None) -> float | None:
    """Where a fill sat in the spread: 0 at the mid, +1 at the ask, -1 at the bid (past them beyond).
    None for a missing, one-sided or crossed quote."""
    if bid is None or ask is None or not (bid > 0 and ask > bid):
        return None
    if not all(math.isfinite(x) for x in (price, bid, ask)):
        return None
    half = (ask - bid) / 2.0
    return (price - (bid + ask) / 2.0) / half


def side_of(e: float | None) -> str:
    if e is None:
        return "unread"
    if e >= READ_EDGE:
        return "bought"
    if e <= -READ_EDGE:
        return "sold"
    return "unread"


def orders(prints: Iterable[dict]) -> list[dict]:
    """The single-leg prints of one name's day grouped into orders (see the module note), each
    {expiration, strike, right, side, first_ms, last_ms, prints, size, premium, vwap, edge, sweep},
    `edge` the size-weighted mean of its prints'. Sorted by first print."""
    by_contract: dict[tuple, list[dict]] = {}
    for p in prints:
        if p["kind"] in ("cancelled", "multi_leg") or not p["size"] or p["size"] <= 0:
            continue
        by_contract.setdefault((p["expiration"], float(p["strike"]), p["right"]), []).append(p)
    out = []
    gap_ms = ORDER_GAP_S * 1000.0
    for (exp, strike, right), ps in by_contract.items():
        ps.sort(key=lambda p: p["ts_ms"])
        cur: dict | None = None
        for p in ps:
            e = edge(p["price"], p["bid"], p["ask"])
            s = side_of(e)
            if cur is not None and cur["side"] == s and p["ts_ms"] - cur["last_ms"] <= gap_ms:
                cur["last_ms"] = p["ts_ms"]
            else:
                cur = {"expiration": exp, "strike": strike, "right": right, "side": s,
                       "first_ms": p["ts_ms"], "last_ms": p["ts_ms"], "_p": []}  # fmt: skip
                out.append(cur)
            cur["_p"].append((p, e))
    for o in out:
        ps = o.pop("_p")
        size = sum(p["size"] for p, _ in ps)
        premium = sum(p["size"] * p["price"] * 100.0 for p, _ in ps)
        read = [(p["size"], e) for p, e in ps if e is not None]
        o.update(
            prints=len(ps),
            size=size,
            premium=premium,
            vwap=premium / (size * 100.0),
            edge=sum(n * e for n, e in read) / sum(n for n, _ in read) if read else None,
            sweep=any(p["kind"] == "sweep" for p, _ in ps),
        )
    out.sort(key=lambda o: (o["first_ms"], o["expiration"], o["strike"], o["right"]))
    return out


def bucket(premium: float) -> int:
    if premium <= 0 or math.log10(premium) < LOG_LO:
        return 0
    i = int((math.log10(premium) - LOG_LO) / BUCKET) + 1
    return min(i, N_BUCKETS - 1)


def bucket_floor(i: int) -> float:
    """The smallest premium bucket `i` holds (0 for the underflow bucket)."""
    return 0.0 if i == 0 else 10 ** (LOG_LO + (i - 1) * BUCKET)


def histogram(day_orders: Iterable[dict]) -> list[int]:
    h = [0] * N_BUCKETS
    for o in day_orders:
        h[bucket(o["premium"])] += 1
    return h


def top_share_cut(histograms: Iterable[list[int]], share: float = TOP_SHARE) -> float | None:
    """The premium from which the top `share` of the pooled orders begins: the floor of the bucket
    where the count from the top first reaches it. Bucket-coarse by design (a 12% step); None
    with no orders."""
    pooled = [0] * N_BUCKETS
    for h in histograms:
        for i, n in enumerate(h):
            pooled[i] += n
    total = sum(pooled)
    if not total:
        return None
    need, seen = share * total, 0
    for i in range(N_BUCKETS - 1, -1, -1):
        seen += pooled[i]
        if seen >= need:
            return bucket_floor(i)
    return 0.0  # pragma: no cover -- the loop always reaches `need` by bucket 0


def opened(size: int, oi_before: int | None, oi_after: int | None) -> bool | None:
    """Whether an order opened a position: open interest rose by at least OPEN_SHARE of it from the
    session's start to the next session's start. None while either is unknown."""
    if oi_before is None or oi_after is None:
        return None
    return (oi_after - oi_before) >= OPEN_SHARE * size
