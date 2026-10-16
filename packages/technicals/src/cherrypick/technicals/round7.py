"""Round 7 of the historical study: does the bearing itself carry an edge?

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 7"),
committed before this module first ran with scoring on. In brief:

- **The bearing** is `bearing_table.TABLE` (the lookup frozen from the 2026-10-07 security list) on
  our own inputs: the 1M and 6M trend scores (`trend.SHORT_TERM`, `trend.LONG_TERM`) and the tier
  of the 1-10 score (round 5's whole-market decile), each on the session's close.
- **Four tests**, each a name ENTERING a state it was in on none of the FRESH sessions before:
  `strong-bull` (long), `strong-bear` (short), `pullback` (long: a falling 1M inside a rising 6M)
  and `rally` (short: the reverse). Held HOLD sessions, filled at the next opens.
- **Against random entry**: DRAWS_SAME_DATE names in the study universe that day and DRAWS_SAME_NAME
  other days the name was in it, held HOLD sessions and scored like the entry.
- Holm across the four at ALPHA; a test passes with a significant positive edge AND a positive net R.
- Views, never tests: the two sub-periods, halves A and B.

The universe is the whole study's (`universe.membership`), delisted names included. Fills, costs,
exclusions and the calendar-time test are `study.py`'s, unchanged.
"""

from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from statistics import median

from . import (
    bearing_table,
    compass,
    history,
    hypotheses,
    paths,
    round5,
    setups,
    store,
    study,
    trend,
    universe,
)

SEED = 202610161
DRAWS_SAME_DATE = 40
DRAWS_SAME_NAME = 40
ALPHA = 0.05
HOLD = 21
FRESH = 10
TESTS = (
    ("strong-bull", "strong bull", 1),
    ("strong-bear", "strong bear", -1),
    ("pullback", "pullback", 1),
    ("rally", "rally", -1),
)
STATE_OF = {t: label for t, label, _ in TESTS}
SIDE_OF = {t: side for t, _, side in TESTS}


def setup_of(test: str) -> str:
    return "bearing" if SIDE_OF[test] > 0 else "bearing-short"


def bearings(s1: list, s6: list, deciles: list) -> list[str | None]:
    """The bearing on every session, None where an input is missing or the cell was never seen."""
    out = []
    for a, b, d in zip(s1, s6, deciles, strict=True):
        k = compass.key(a, b, d)
        out.append(bearing_table.TABLE.get(k) if k is not None else None)
    return out


def enters(labels: list[str | None], i: int, state: str) -> bool:
    """In `state` on session i and on none of the FRESH sessions before."""
    return labels[i] == state and state not in labels[max(0, i - FRESH) : i]


def held(i: int, size: int) -> setups.Trade:
    x = i + HOLD
    return setups.Trade(i, exit=x, reason="time") if x < size else setups.Trade(i)


# ------------------------------------------------------------------------------------- the passes


def _init(path: str, data_end: str) -> None:
    round5._init(path, data_end)


def _names(job: tuple[list[str], dict]) -> list[tuple[str, list[str], list[tuple[str, str]]]]:
    """Per name: the sessions it was in the universe (the random pool), and its entries."""
    symbols, cutoffs = job
    dates, idx = round5._ctx["dates"], round5._ctx["idx"]
    out = []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None or not any(nm.member):
            continue
        pool = [d for d, ok, sh in zip(nm.dates, nm.member, nm.shadow, strict=True) if ok and not sh]
        s1 = trend.scores(nm.closes, trend.SHORT_TERM)
        s6 = trend.scores(nm.closes, trend.LONG_TERM)
        raw = {b.date: b.close for b in store.raw_bars(study._conn, sym)}
        splits = [s.ex_date for s in store.splits(study._conn, sym)]
        deciles = []
        for d in nm.dates:
            k = idx.get(d)
            deciles.append(
                round5.decile(round5._score_on(raw, splits, dates, k), cutoffs.get(k))
                if k is not None
                else None
            )
        labels = bearings(s1, s6, deciles)
        entries = []
        for i, d in enumerate(nm.dates):
            for test, state, _ in TESTS:
                if enters(labels, i, state) and study.counted(nm, setup_of(test), i):
                    entries.append((test, d))
        out.append((sym, pool, entries))
    return out


def _score(work: list[tuple[str, list[tuple[int, str, str, bool]]]]) -> tuple[dict, dict]:
    """Per symbol, its own entries scored ({key: score}) and the draws landing in it ({key: [sum, n]})."""
    real: dict[int, dict] = {}
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0])
    for sym, items in work:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, day, test, is_draw in items:
            i = where.get(day)
            if i is None:
                continue
            t = held(i, len(nm.closes))
            if study.spans_suspect(nm, i, t):
                continue
            sc = study.score(nm, setup_of(test), t)
            if sc is None:
                continue
            if is_draw:
                slot = acc[key]
                slot[0] += sc["r"]
                slot[1] += 1
            else:
                real[key] = sc
    return real, dict(acc)


# ------------------------------------------------------------------------------------- assembling


def draw_plan(
    entries: list[dict], by_date: dict, by_name: dict
) -> dict[str, list[tuple[int, str, str, bool]]]:
    work: dict[str, list[tuple[int, str, str, bool]]] = defaultdict(list)
    for key, e in enumerate(entries):
        work[e["symbol"]].append((key, e["date"], e["test"], False))
        rng = random.Random(f"{SEED}:{e['test']}:{e['symbol']}:{e['date']}")
        others = [s for s in by_date.get(e["date"], ()) if s != e["symbol"]]
        for sym in rng.sample(others, min(DRAWS_SAME_DATE, len(others))):
            work[sym].append((key, e["date"], e["test"], True))
        days = [d for d in by_name.get(e["symbol"], ()) if d != e["date"]]
        for day in rng.sample(days, min(DRAWS_SAME_NAME, len(days))):
            work[e["symbol"]].append((key, day, e["test"], True))
    return work


def build(workers: int = 14, path=None, progress=print) -> dict:
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    everyone = [s for (s,) in conn.execute("SELECT symbol FROM peaks ORDER BY symbol")]
    candidates = history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME)
    conn.close()
    started = time.monotonic()
    pool = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(path), data_end))
    progress(f"round 7: market scores over {len(everyone):,} names")
    by_day: dict[int, list[float]] = defaultdict(list)
    for part in pool.map(round5._market, study._chunks(everyone, workers * 8)):
        for k, s in part:
            by_day[k].append(s)
    cutoffs = {k: round5.levels.rank_cutoffs(v) for k, v in by_day.items() if len(v) >= 100}
    del by_day
    progress(f"round 7: bearings over {len(candidates):,} candidate names")
    jobs = [(chunk, cutoffs) for chunk in study._chunks(candidates, workers * 8)]
    by_date: dict[str, list[str]] = defaultdict(list)
    by_name: dict[str, list[str]] = {}
    entries = []
    for part in pool.map(_names, jobs):
        for sym, member_days, ents in part:
            by_name[sym] = member_days
            for d in member_days:
                by_date[d].append(sym)
            entries += [{"test": t, "symbol": sym, "date": d} for t, d in ents]
    entries.sort(key=lambda r: (r["test"], r["symbol"], r["date"]))
    return {
        "pool": pool,
        "entries": entries,
        "by_date": by_date,
        "by_name": by_name,
        "names": len(by_name),
        "data_end": data_end,
        "started": started,
    }


def counts(b: dict) -> dict:
    """Frequencies only -- what the declaration may look at."""
    out = {}
    for test, _, _ in TESTS:
        es = [e for e in b["entries"] if e["test"] == test]
        out[test] = {
            "entries": len(es),
            "sessions": len({e["date"] for e in es}),
            "names": len({e["symbol"] for e in es}),
            "first": min((e["date"] for e in es), default=None),
            "median_same_date_pool": median([len(b["by_date"].get(e["date"], ())) for e in es] or [0]),
        }
    return {"names_in_universe": b["names"], "data_end": b["data_end"], "tests": out}


def measure(rows: list[dict]) -> dict:
    return {"describe": study.describe(rows), "test": study.test(rows)}


def run(workers: int = 14, path=None, progress=print) -> dict:
    b = build(workers, path, progress)
    pool, entries = b["pool"], b["entries"]
    try:
        work = draw_plan(entries, b["by_date"], b["by_name"])
        progress(
            f"round 7: {len(entries):,} entries, {sum(len(v) for v in work.values()):,} positions to score"
        )
        real: dict[int, dict] = {}
        acc: dict[int, list[float]] = {}
        for part_real, part_acc in pool.map(_score, study._chunks(sorted(work.items()), workers * 8)):
            real.update(part_real)
            for key, (s, n) in part_acc.items():
                slot = acc.setdefault(key, [0.0, 0])
                slot[0] += s
                slot[1] += n
    finally:
        pool.shutdown()
    rows = []
    for key, e in enumerate(entries):
        sc = real.get(key)
        if sc is None:
            continue
        s, n = acc.get(key, [0.0, 0])
        rows.append({**e, **sc, "base": s / n if n else None, "base_n": n})
    by_test: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_test[r["test"]].append(r)
    out = {t: measure(by_test.get(t, [])) for t, _, _ in TESTS}
    hypotheses.decide(out)
    views = {}
    for t, _, _ in TESTS:
        rs = by_test.get(t, [])
        views[t] = {
            **{name: measure([r for r in rs if lo <= r["date"] <= hi]) for name, lo, hi in study.SUB_PERIODS},
            "half_A": measure([r for r in rs if hypotheses.half(r["symbol"]) == "A"]),
            "half_B": measure([r for r in rs if hypotheses.half(r["symbol"]) == "B"]),
        }
    return {
        "round": 7,
        "seed": SEED,
        "draws_same_date": DRAWS_SAME_DATE,
        "draws_same_name": DRAWS_SAME_NAME,
        "alpha": ALPHA,
        "parameters": {"hold": HOLD, "fresh": FRESH, "table": bearing_table.SOURCE},
        "counts": counts(b),
        "data_end": b["data_end"],
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - b["started"]),
        "hypotheses": out,
        "views": views,
    }


def write(result: dict) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"round7-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)
