"""cherrypick.core.legmarks -- one position leg priced at a mark, and legs summed by underlying.

Two callers price the real account's legs: the orchestrator's `positions` verb, from the broker's
position rows, and manage's re-mark, which moves a stored snapshot's legs to the stream cache's newer
quotes between full snapshots. Both price a leg here, so a re-marked leg is the leg the next full
snapshot would show at the same quote.

**A mid is not a fill.** Every value is marked at the midpoint, so each leg carries what that mid could
be wrong by (`mark_doubt`, half the spread on every contract) and a `wide` flag. **An unpriced leg is
not a free leg:** a leg with no mark stays `priced: False` and out of every total.
"""

from __future__ import annotations

from typing import Any

# A leg is "wide" when its bid/ask spread exceeds this fraction of its mid. Chosen from what the marks
# are used for rather than from a market convention: below roughly a tenth, mid-vs-fill is small against
# the P/L being reported, and above it the mid stops being a number worth quoting a decision on.
WIDE_SPREAD_RATIO = 0.10
# ...but a ratio alone flags every cheap leg (a 0.01/0.03 far wing is 100% wide and $2 of doubt), so a
# leg must also carry this much absolute spread per contract to be worth a reader's attention.
WIDE_SPREAD_MIN_DOLLARS = 0.10

#: The fields a mark sets on a leg; a leg re-marked without a quote keeps none of them.
MARK_FIELDS = (
    "bid", "ask", "mid", "mark_source", "mark_age_seconds", "value", "open_pl", "day_pl", "spread",
    "mark_doubt", "wide",
)  # fmt: skip


def apply_mark(
    leg: dict[str, Any],
    mark: dict | None,
    *,
    contracts: float,
    multiplier: float,
    open_price: float | None,
    prev_close: float | None,
) -> dict[str, Any]:
    """`leg` priced at `mark` ({bid, ask, mid, source, age_seconds}), in place and returned. `contracts`
    is signed (a short leg is negative), so a short leg's value and P/L both move against the mark. No
    mark, or one with no mid, leaves the leg unpriced."""
    if mark is None or mark.get("mid") is None:
        leg["priced"] = False
        return leg
    mid = mark["mid"]
    bid, ask = mark.get("bid"), mark.get("ask")
    spread = (ask - bid) if (bid is not None and ask is not None) else None
    leg.update(
        {
            "priced": True,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "mark_source": mark.get("source"),
            "mark_age_seconds": mark.get("age_seconds"),
            "value": contracts * multiplier * mid,
            "open_pl": (contracts * multiplier * (mid - open_price)) if open_price is not None else None,
            "day_pl": (contracts * multiplier * (mid - prev_close)) if prev_close is not None else None,
            "spread": spread,
            # What the mid could be wrong by if this leg had to trade at the bad side of its own market.
            "mark_doubt": (abs(contracts) * multiplier * spread / 2.0) if spread is not None else None,
            "wide": bool(
                spread is not None
                and spread >= WIDE_SPREAD_MIN_DOLLARS
                and mid > 0
                and (spread / mid) >= WIDE_SPREAD_RATIO
            ),
        }
    )
    return leg


def total(values) -> float:
    return sum(v for v in values if v is not None)


def totals(legs: list[dict]) -> dict[str, Any]:
    """Value, open and day P/L and mark doubt over the priced legs, and how many were not priced."""
    priced = [leg for leg in legs if leg.get("priced")]
    return {
        "leg_count": len(legs),
        "unpriced_count": len(legs) - len(priced),
        "value": total(leg.get("value") for leg in priced),
        "open_pl": total(leg.get("open_pl") for leg in priced),
        "day_pl": total(leg.get("day_pl") for leg in priced),
        "mark_doubt": total(leg.get("mark_doubt") for leg in priced),
    }


def group_by_underlying(legs: list[dict]) -> list[dict]:
    """Legs collapsed to one row per underlying, sorted by absolute open P/L.

    Sorted by magnitude rather than signed value because the reader's question is "what needs my
    attention", and a $200 loser and a $200 winner are equally worth seeing before a $3 anything.
    """
    groups: dict[str, dict] = {}
    for leg in legs:
        name = leg["underlying"]
        group = groups.setdefault(name, {"underlying": name, "legs": [], "unpriced": 0})
        group["legs"].append(leg)
        if not leg.get("priced"):
            group["unpriced"] += 1
    out = []
    for group in groups.values():
        t = totals(group["legs"])
        priced = [leg for leg in group["legs"] if leg.get("priced")]
        group.update(
            {
                "leg_count": t["leg_count"],
                "value": t["value"],
                "open_pl": t["open_pl"],
                "day_pl": t["day_pl"],
                "mark_doubt": t["mark_doubt"],
                "wide": any(leg.get("wide") for leg in priced),
            }
        )
        out.append(group)
    return sorted(out, key=lambda g: -abs(g["open_pl"]))
