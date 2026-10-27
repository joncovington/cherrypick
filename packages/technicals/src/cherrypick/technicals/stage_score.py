"""Score the stage rule against every saved edition.

The universe for scoring is every name any edition has listed: the vendor's own universe is not
published, and a name it has listed on some day is known to be in it. Within that universe, per
session and in total:

- **side recall**: of the vendor's listings, how many we place on the same side;
- **stage agreement**: of those, how many we give the same stage;
- **extra**: names we list that the vendor did not that day, as a rate of its listings;
- **counts**: ours beside the vendor's stated leaders and laggards.

Each is also given apart for the sessions the 2026-10-14 refit fitted on (`FIT_THROUGH`) and the
ones after, which it never saw. The vendor's editions end with session 2026-10-22, so the later
half is final.
"""

from __future__ import annotations

from . import editions, stage, store

# The refit of 2026-10-14 fitted on the editions describing sessions through this one; the later
# ones are the unseen half every re-score reports apart.
FIT_THROUGH = "2026-10-02"


def _rates(t: dict) -> dict:
    listed = t["listed"] or 1
    return {
        "side_recall": round(t["side"] / listed, 4),
        "stage_agreement": round(t["stage"] / max(t["side"], 1), 4),
        "extra_rate": round(t["extra"] / listed, 4),
    }


def score(rule: stage.StageRule = stage.DEFAULT_RULE, conn=None, split: str = FIT_THROUGH) -> dict:
    """The scores over every edition, and apart for the sessions through `split` and after it."""
    vendor = editions.load()
    universe = sorted({s for day in vendor.values() for s in day})
    own = conn is None
    conn = conn or store.connect()
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in [*universe, rule.benchmark]}
    if own:
        conn.close()
    bench = closes.pop(rule.benchmark)
    sessions: dict[str, dict] = {}
    keys = ("listed", "side", "stage", "extra", "missed")
    totals = dict.fromkeys(keys, 0)
    halves = {name: dict.fromkeys((*keys, "editions"), 0) for name in ("fit", "later")}
    for day, theirs in sorted(vendor.items()):
        ours = stage.stages_on(day, {s: closes[s] for s in universe if closes.get(s)}, bench, rule)
        row = {
            "listed": len(theirs),
            "side": sum(1 for s, t in theirs.items() if s in ours and ours[s].side == t.side),
            "stage": sum(1 for s, t in theirs.items() if ours.get(s) == t),
            "extra": sum(1 for s in ours if s not in theirs),
            "missed": sum(1 for s in theirs if s not in ours),
            "counts": {
                "ours": [
                    sum(v.side == "leader" for v in ours.values()),
                    sum(v.side == "laggard" for v in ours.values()),
                ],
                "vendor": [
                    sum(v.side == "leader" for v in theirs.values()),
                    sum(v.side == "laggard" for v in theirs.values()),
                ],
            },
        }
        sessions[day] = row
        half = halves["fit" if day <= split else "later"]
        half["editions"] += 1
        for k in keys:
            totals[k] += row[k]
            half[k] += row[k]
    return {
        "rule": {
            "name": rule.name,
            "method": rule.method,
            "windows": rule.windows,
            "laggard_windows": rule.side_windows("laggard"),
            "margins": rule.margins,
            "day_margin": rule.day_margin,
            "benchmark": rule.benchmark,
        },
        "universe": len(universe),
        "editions": len(vendor),
        **_rates(totals),
        "totals": totals,
        "split": split,
        "halves": {name: {**_rates(t), "totals": t} for name, t in halves.items()},
        "sessions": sessions,
    }


def score_rotation(rule=None, conn=None) -> dict:
    """The rotation rule against every saved edition: exact state agreement over the vendor's
    placements, and `extra` -- funds we place in a state the vendor left empty that day."""
    from . import rotation, symbols

    rule = rule or rotation.DEFAULT_RULE
    vendor = editions.load_rotation()
    own = conn is None
    conn = conn or store.connect()
    names = sorted({*symbols.ROTATION_ETFS, rule.benchmark, rule.asset_benchmark})
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in names}
    if own:
        conn.close()
    sessions, placed, agree, extra = {}, 0, 0, 0
    for day, theirs in sorted(vendor.items()):
        ours = rotation.states_on(day, closes, symbols.ASSET_ETFS, rule)
        row = {
            "placed": len(theirs),
            "agree": sum(1 for f, st in theirs.items() if ours.get(f) == st),
            "extra": sum(1 for f in symbols.ROTATION_ETFS if ours.get(f) and f not in theirs),
            "disagree": {f: [ours.get(f), st] for f, st in theirs.items() if ours.get(f) != st},
        }
        sessions[day] = row
        placed += row["placed"]
        agree += row["agree"]
        extra += row["extra"]
    return {
        "rule": {
            "name": rule.name,
            "fast": rule.fast,
            "slow": rule.slow,
            "fast_margin": rule.fast_margin,
            "slow_margin": rule.slow_margin,
        },
        "editions": len(vendor),
        "agreement": round(agree / placed, 4) if placed else None,
        "placed": placed,
        "agree": agree,
        "extra": extra,
        "sessions": sessions,
    }
