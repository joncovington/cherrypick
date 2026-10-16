"""Round 6 of the historical study: does an analyst revision add weight to the direction?

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 6"),
committed before this module first ran with scoring on. In brief:

- **The direction** is the strong core of our *bearing* (`bearing`; declared as "trend+RS agree"):
  bullish when the 1M and 6M trend scores (`trend.SHORT_TERM`, `trend.LONG_TERM`) are both +STRONG
  or more and the 1-10 score (round 5's whole-market decile) is RS_HIGH or more; bearish when both
  are -STRONG or less and the score is RS_LOW or less. It is the rule that fitted the board's labels,
  NOT the vendor's own direction read, which failed its out-of-sample check (7-8 of 18). That read
  was later found to be a lookup on 1M x 6M x score tier (score 7+ for strong bull, 4- for strong
  bear), so this rule is a stricter subset of its strong states.
- **The revision** is `revisions.by_session`'s tag: that session's qualifying events, its direction
  decided by a rating change before a target move, the largest move first.
- **Four tests**, each a session in the direction with a revision that session, against the same
  direction's sessions WITHOUT one -- DRAWS_SAME_DATE other names in that direction that day and
  DRAWS_SAME_NAME other days the name was in it, untagged, held HOLD sessions and scored the same:
  `agree-bull` (long), `agree-bear` (short), and the warnings `against-bull` (long with a bearish
  revision) and `against-bear` (short with a bullish one), which pass when they do WORSE.
- Holm across the four at ALPHA. An agree test passes with a significant positive edge AND a positive
  net R; a warning test with a significant shortfall alone (it is a veto, not a trade).
- Views, never tests: the rating vs target-only split, the two sub-periods, halves A and B.

The universe is the names the revision source covers (today's ~600), on sessions `study.counted`
admits; draws come from the same names, so both sides carry the same survivorship. Fills, costs,
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

from . import history, hypotheses, paths, revisions, round5, setups, store, study, trend

SEED = 202610151
DRAWS_SAME_DATE = 40
DRAWS_SAME_NAME = 40
ALPHA = 0.05
HOLD = 21
STRONG = 3
RS_HIGH, RS_LOW = 8, 3
TEST_OF = {(1, 1): "agree-bull", (-1, -1): "agree-bear", (1, -1): "against-bull", (-1, 1): "against-bear"}
TESTS = ("agree-bull", "agree-bear", "against-bull", "against-bear")
WARNINGS = frozenset({"against-bull", "against-bear"})


def bearing(s1: int | None, s6: int | None, decile: int | None) -> int:
    """+1 / -1 when the 1M trend, the 6M trend and the 1-10 score all agree; 0 otherwise."""
    if s1 is None or s6 is None or decile is None:
        return 0
    if s1 >= STRONG and s6 >= STRONG and decile >= RS_HIGH:
        return 1
    if s1 <= -STRONG and s6 <= -STRONG and decile <= RS_LOW:
        return -1
    return 0


def setup_of(state: int) -> str:
    return "rev-dir" if state > 0 else "rev-dir-short"


def held(i: int, size: int) -> setups.Trade:
    x = i + HOLD
    return setups.Trade(i, exit=x, reason="time") if x < size else setups.Trade(i)


def session_tags(events: list[revisions.Event]) -> dict[str, dict[str, tuple[int, str]]]:
    """{symbol: {session: (direction, kind)}}."""
    out: dict[str, dict[str, tuple[int, str]]] = defaultdict(dict)
    for (sym, session), t in revisions.by_session(events).items():
        out[sym][session] = (t.direction, t.kind)
    return dict(out)


# ------------------------------------------------------------------------------------- the passes


def _init(path: str, data_end: str) -> None:
    round5._init(path, data_end)  # study's connection, and the benchmark calendar round 5 ranks on


def _states(job: tuple[list[str], dict, dict]) -> list[tuple]:
    """(symbol, date, state, tag direction or 0, tag kind or "") for every counted session in a
    direction, in each name."""
    symbols, tags_of, cutoffs = job
    dates, idx = round5._ctx["dates"], round5._ctx["idx"]
    out = []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        s1 = trend.scores(nm.closes, trend.SHORT_TERM)
        s6 = trend.scores(nm.closes, trend.LONG_TERM)
        raw = {b.date: b.close for b in store.raw_bars(study._conn, sym)}
        splits = [s.ex_date for s in store.splits(study._conn, sym)]
        mine = tags_of.get(sym, {})
        for i, d in enumerate(nm.dates):
            k = idx.get(d)
            if k is None:
                continue
            dec = round5.decile(round5._score_on(raw, splits, dates, k), cutoffs.get(k))
            state = bearing(s1[i], s6[i], dec)
            if not state or not study.counted(nm, setup_of(state), i):
                continue
            tag_dir, kind = mine.get(d, (0, ""))
            out.append((sym, d, state, tag_dir, kind))
    return out


def _score(work: list[tuple[str, list[tuple[int, str, int, bool]]]]) -> tuple[dict, dict]:
    """Per symbol, its own entries scored ({key: score}) and the draws landing in it, summed per
    entry key ({key: [sum, n]})."""
    real: dict[int, dict] = {}
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0])
    for sym, items in work:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, day, state, is_draw in items:
            i = where.get(day)
            if i is None:
                continue
            t = held(i, len(nm.closes))
            if study.spans_suspect(nm, i, t):
                continue
            sc = study.score(nm, setup_of(state), t)
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


def plan(states: list[tuple]) -> tuple[list[dict], dict, dict]:
    """The entries, and the untagged pools they draw from: by (date, state) and by (symbol, state)."""
    entries, by_date, by_name = [], defaultdict(list), defaultdict(list)
    for sym, d, state, tag_dir, kind in states:
        if tag_dir:
            entries.append(
                {"test": TEST_OF[(state, tag_dir)], "symbol": sym, "date": d, "state": state, "kind": kind}
            )
        else:
            by_date[(d, state)].append(sym)
            by_name[(sym, state)].append(d)
    entries.sort(key=lambda r: (r["test"], r["symbol"], r["date"]))
    return entries, by_date, by_name


def draw_plan(
    entries: list[dict], by_date: dict, by_name: dict
) -> dict[str, list[tuple[int, str, int, bool]]]:
    work: dict[str, list[tuple[int, str, int, bool]]] = defaultdict(list)
    for key, e in enumerate(entries):
        work[e["symbol"]].append((key, e["date"], e["state"], False))
        rng = random.Random(f"{SEED}:{e['test']}:{e['symbol']}:{e['date']}")
        others = [s for s in by_date.get((e["date"], e["state"]), ()) if s != e["symbol"]]
        for sym in rng.sample(others, min(DRAWS_SAME_DATE, len(others))):
            work[sym].append((key, e["date"], e["state"], True))
        days = [d for d in by_name.get((e["symbol"], e["state"]), ()) if d != e["date"]]
        for day in rng.sample(days, min(DRAWS_SAME_NAME, len(days))):
            work[e["symbol"]].append((key, day, e["state"], True))
    return work


def build(workers: int = 14, path=None, progress=print) -> dict:
    """Everything before scoring: the cut-offs, every session in a direction, the entries and pools."""
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    everyone = [s for (s,) in conn.execute("SELECT symbol FROM peaks ORDER BY symbol")]
    held_names = {s for (s,) in conn.execute("SELECT DISTINCT symbol FROM bars")}
    conn.close()
    tags_of = session_tags(revisions.load())
    covered = sorted(s for s in tags_of if s in held_names)
    started = time.monotonic()
    pool = ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(path), data_end))
    progress(f"round 6: market scores over {len(everyone):,} names")
    by_day: dict[int, list[float]] = defaultdict(list)
    for part in pool.map(round5._market, study._chunks(everyone, workers * 8)):
        for k, s in part:
            by_day[k].append(s)
    cutoffs = {k: round5.levels.rank_cutoffs(v) for k, v in by_day.items() if len(v) >= 100}
    del by_day
    progress(f"round 6: directions in {len(covered):,} covered names")
    jobs = [(chunk, {s: tags_of[s] for s in chunk}, cutoffs) for chunk in study._chunks(covered, workers * 4)]
    states = [r for part in pool.map(_states, jobs) for r in part]
    entries, by_date, by_name = plan(states)
    return {
        "pool": pool,
        "entries": entries,
        "by_date": by_date,
        "by_name": by_name,
        "states": len(states),
        "covered": len(covered),
        "data_end": data_end,
        "started": started,
    }


def counts(b: dict) -> dict:
    """Frequencies only -- what the declaration may look at: entries per test and kind, pool sizes."""
    out = {}
    for test in TESTS:
        es = [e for e in b["entries"] if e["test"] == test]
        out[test] = {
            "entries": len(es),
            "sessions": len({e["date"] for e in es}),
            "by_kind": {k: sum(1 for e in es if e["kind"] == k) for k in ("rating", "target")},
            "median_same_date_pool": median(
                [len(b["by_date"].get((e["date"], e["state"]), ())) for e in es] or [0]
            ),
            "median_same_name_pool": median(
                [len(b["by_name"].get((e["symbol"], e["state"]), ())) for e in es] or [0]
            ),
            "first": min((e["date"] for e in es), default=None),
        }
    return {"states": b["states"], "covered": b["covered"], "data_end": b["data_end"], "tests": out}


def _rows_for(rows: list[dict], test: str) -> list[dict]:
    """Rows as `study.test` reads them. A warning is tested on (pool - entry): it passes when the
    revision against the direction does worse than the direction alone."""
    if test in WARNINGS:
        return [{**r, "r": -r["r"], "base": -r["base"]} for r in rows if r["base"] is not None]
    return rows


def measure(rows: list[dict], test: str) -> dict:
    return {"describe": study.describe(rows), "test": study.test(_rows_for(rows, test))}


def decide(out: dict[str, dict]) -> None:
    judged = {k: v["test"]["p"] for k, v in out.items() if v["test"].get("judged")}
    holm = study.holm(judged, ALPHA)
    for k, v in out.items():
        sig = bool(holm.get(k, False) and v["test"].get("edge_r", 0) > 0)
        profitable = (v["describe"].get("expectancy_r") or 0) > 0
        v["significant"], v["profitable"] = sig, profitable
        v["passed"] = sig if k in WARNINGS else sig and profitable


def run(workers: int = 14, path=None, progress=print) -> dict:
    b = build(workers, path, progress)
    pool, entries = b["pool"], b["entries"]
    try:
        work = draw_plan(entries, b["by_date"], b["by_name"])
        progress(
            f"round 6: {len(entries):,} entries, {sum(len(v) for v in work.values()):,} positions to score"
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
    out = {t: measure(by_test.get(t, []), t) for t in TESTS}
    decide(out)
    views = {}
    for t in TESTS:
        rs = by_test.get(t, [])
        views[t] = {
            **{k: measure([r for r in rs if r["kind"] == k], t) for k in ("rating", "target")},
            **{
                name: measure([r for r in rs if lo <= r["date"] <= hi], t)
                for name, lo, hi in study.SUB_PERIODS
            },
            "half_A": measure([r for r in rs if hypotheses.half(r["symbol"]) == "A"], t),
            "half_B": measure([r for r in rs if hypotheses.half(r["symbol"]) == "B"], t),
        }
    return {
        "round": 6,
        "seed": SEED,
        "draws_same_date": DRAWS_SAME_DATE,
        "draws_same_name": DRAWS_SAME_NAME,
        "alpha": ALPHA,
        "parameters": {
            "hold": HOLD,
            "strong": STRONG,
            "rs_high": RS_HIGH,
            "rs_low": RS_LOW,
            "pt_min": revisions.PT_MIN,
        },
        "counts": counts(b),
        "data_end": b["data_end"],
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - b["started"]),
        "hypotheses": out,
        "views": views,
    }


def write(result: dict, name: str = "round6") -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)
