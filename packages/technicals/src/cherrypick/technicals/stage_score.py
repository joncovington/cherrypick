"""Score the stage rule against every saved edition.

The universe for scoring is every name any edition has listed: the vendor's own universe is not
published, and a name it has listed on some day is known to be in it. Within that universe, per
session and in total:

- **side recall**: of the vendor's listings, how many we place on the same side;
- **stage agreement**: of those, how many we give the same stage;
- **extra**: names we list that the vendor did not that day -- reported as a rate, because the
  vendor's daily universe varies in a way no price rule can see (see `stage`);
- **counts**: ours beside the vendor's stated leaders and laggards.
"""

from __future__ import annotations

from . import editions, stage, store


def score(rule: stage.StageRule = stage.DEFAULT_RULE, conn=None) -> dict:
    vendor = editions.load()
    universe = sorted({s for day in vendor.values() for s in day})
    own = conn is None
    conn = conn or store.connect()
    closes = {s: {b.date: b.close for b in store.adjusted_bars(conn, s)} for s in [*universe, rule.benchmark]}
    if own:
        conn.close()
    bench = closes.pop(rule.benchmark)
    sessions: dict[str, dict] = {}
    totals = {"listed": 0, "side": 0, "stage": 0, "extra": 0, "missed": 0}
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
        for k in totals:
            totals[k] += row[k]
    listed = totals["listed"] or 1
    return {
        "rule": {
            "name": rule.name,
            "windows": rule.windows,
            "margins": rule.margins,
            "day_margin": rule.day_margin,
            "benchmark": rule.benchmark,
        },
        "universe": len(universe),
        "editions": len(vendor),
        "side_recall": round(totals["side"] / listed, 4),
        "stage_agreement": round(totals["stage"] / max(totals["side"], 1), 4),
        "extra_rate": round(totals["extra"] / listed, 4),
        "totals": totals,
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
