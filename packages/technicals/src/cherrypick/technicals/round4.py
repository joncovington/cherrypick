"""Round 4 of the historical study: the vendor's relative-strength breakout, declared before it was run.

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 4"),
committed before this module first ran. In brief:

- `rs-break`: a close above the prior LOOKBACK sessions' highest close on which close / SPY's close
  is also above its prior LOOKBACK high, with no such close in the FRESH sessions before.
  `rs-break-vol` adds the volume rule (at least VOLUME_MULT x the mean of the VOLUME_AVG sessions
  before). Both are long, held HOLD sessions, filled at the next opens like every study position.
- An exploratory look (outside the repo, disclosed in the plan) saw every entry on a day the name's
  median dollar volume was at least $300M. The tests count only the rest: the $20M-$300M slice
  (`in_slice`). Baseline draws come from the same slice as the entry, on the same day or the same
  name, held HOLD sessions and scored like it.
- Three tests, Holm across them at ALPHA: `rs-break:random`, `rs-break-vol:random`, and
  `rs-break-vol:matched`, whose draws are kept only on days the breakout state held (close and
  ratio both at a LOOKBACK high, any volume) -- does the volume rule add anything over the breakout?
- Views, never tests: the $300M slice (already seen), the two sub-periods, halves A and B.

Everything else -- universe, corporate-action exclusions, fills, costs, the calendar-time test -- is
`study.py`'s, unchanged.
"""

from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime

from . import history, hypotheses, paths, setups, store, study, universe

SEED = 202610091
DRAWS_SAME_DATE = 40
DRAWS_SAME_NAME = 40
ALPHA = 0.05
BENCHMARK = "SPY"
LOOKBACK = 21
FRESH = 10
VOLUME_AVG, VOLUME_MULT = 30, 1.5
HOLD = 21
FAMILY = ("rs-break", "rs-break-vol")
TESTS = (("rs-break", "random"), ("rs-break-vol", "random"), ("rs-break-vol", "matched"))
SLICES = ("tested", "seen")  # under $300M a day (never looked at) / at least $300M (the look's)


def hypothesis_id(setup_id: str, baseline: str) -> str:
    return f"{setup_id}:{baseline}"


def in_slice(dollar_volume: float | None) -> str:
    return "seen" if (dollar_volume or 0.0) >= universe.VIEW_DOLLAR_VOLUME else "tested"


# ------------------------------------------------------------------------------------- the rules


def ratio(dates: list[str], closes: list[float], bench: dict[str, float]) -> list[float | None]:
    """close / the benchmark's close, None on a session the benchmark did not trade."""
    out: list[float | None] = []
    for d, c in zip(dates, closes, strict=True):
        b = bench.get(d)
        out.append(c / b if b else None)
    return out


def new_high(values: list[float | None], i: int, n: int = LOOKBACK) -> bool:
    """values[i] strictly above every one of the n before it (all of them known)."""
    if i < n or values[i] is None:
        return False
    prior = values[i - n : i]
    return None not in prior and values[i] > max(prior)


def state(closes: list[float], rs: list[float | None], i: int) -> bool:
    """The breakout state: the close and the ratio both at a LOOKBACK-session high."""
    return new_high(closes, i) and new_high(rs, i)


def volume_confirms(volumes: list[float | None], i: int) -> bool:
    """At least VOLUME_MULT x the mean of the VOLUME_AVG sessions before; a missing volume never
    confirms."""
    if i < VOLUME_AVG or volumes[i] is None:
        return False
    window = volumes[i - VOLUME_AVG : i]
    if None in window:
        return False
    return volumes[i] >= VOLUME_MULT * sum(window) / VOLUME_AVG


def states(closes: list[float], rs: list[float | None]) -> list[bool]:
    return [state(closes, rs, i) for i in range(len(closes))]


def fires(st: list[bool], i: int) -> bool:
    """`rs-break`'s trigger: in the state today and in none of the FRESH sessions before."""
    return st[i] and not any(st[max(0, i - FRESH) : i])


def _leave(r, j: int, t: setups.Trade) -> str | None:
    return "time" if j - t.entry >= HOLD else None


def positions(setup_id: str, st: list[bool], volumes) -> list[setups.Trade]:
    vol = setup_id == "rs-break-vol"

    def enter(i: int) -> setups.Trade | None:
        if fires(st, i) and (not vol or volume_confirms(volumes, i)):
            return setups.Trade(i)
        return None

    return setups._walk(len(st), enter, lambda j, t: _leave(None, j, t))


def held(i: int, size: int) -> setups.Trade:
    """A position opened at bar i and held HOLD sessions: the baseline's trade."""
    x = i + HOLD
    return setups.Trade(i, exit=x, reason="time") if x < size else setups.Trade(i)


# ------------------------------------------------------------------------------------- the passes

_bench: dict[str, float] = {}


def _init(path: str, data_end: str) -> None:
    from pathlib import Path

    study._init(path, data_end)
    conn = history.connect(Path(path))
    _bench.clear()
    _bench.update({b.date: b.close for b in store.adjusted_bars(conn, BENCHMARK)})
    conn.close()


def _membership(symbols: list[str]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Each name's qualifying sessions, with the slice each falls in."""
    out = []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end, light=True)
        if nm is None or not any(nm.member):
            continue
        days = [
            (d, in_slice(dv)) for d, ok, dv in zip(nm.dates, nm.member, nm.dollar_volume, strict=True) if ok
        ]
        out.append((sym, days))
    return out


def _real(symbols: list[str]) -> list[dict]:
    rows = []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        st = states(nm.closes, ratio(nm.dates, nm.closes, _bench))
        for setup_id in FAMILY:
            for t in positions(setup_id, st, nm.readings.volumes):
                i = t.entry
                if not study.counted(nm, setup_id, i) or study.spans_suspect(nm, i, t):
                    continue
                sc = study.score(nm, setup_id, t)
                if sc is None:
                    continue
                rows.append(
                    {
                        "setup": setup_id,
                        "symbol": sym,
                        "date": nm.dates[i],
                        "slice": in_slice(nm.dollar_volume[i]),
                        "dollar_volume": nm.dollar_volume[i],
                        "reason": t.reason if not sc["ended"] else "data ended",
                        **sc,
                    }
                )
    return rows


def draw_plan(rows: list[dict], by_date: dict, by_name: dict):
    """For each entry, its seeded draws from its own slice, bucketed by the name they land in."""
    work: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for key, row in enumerate(rows):
        rng = random.Random(f"{SEED}:{row['setup']}:{row['symbol']}:{row['date']}")
        others = [s for s in by_date.get((row["date"], row["slice"]), ()) if s != row["symbol"]]
        for sym in rng.sample(others, min(DRAWS_SAME_DATE, len(others))):
            work[sym].append((key, row["setup"], row["date"]))
        days = [d for d in by_name.get((row["symbol"], row["slice"]), ()) if d != row["date"]]
        for day in rng.sample(days, min(DRAWS_SAME_NAME, len(days))):
            work[row["symbol"]].append((key, row["setup"], day))
    return work


def _draws(work: list[tuple[str, list[tuple[int, str, str]]]]) -> dict[int, list[float]]:
    """Per entry key, [sum, n] over every draw and [sum, n] over the draws made in the breakout
    state (`study.merge_sums` adds the four across workers)."""
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for sym, draws in work:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        rs = ratio(nm.dates, nm.closes, _bench)
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, setup_id, day in draws:
            i = where.get(day)
            if i is None or nm.shadow[i]:
                continue
            t = held(i, len(nm.closes))
            if study.spans_suspect(nm, i, t):
                continue
            sc = study.score(nm, setup_id, t)
            if sc is None:
                continue
            slot = acc[key]
            slot[0] += sc["r"]
            slot[1] += 1
            if state(nm.closes, rs, i):
                slot[2] += sc["r"]
                slot[3] += 1
    return dict(acc)


# ------------------------------------------------------------------------------------ the verdict


def _against(rows: list[dict], baseline: str) -> list[dict]:
    return [{**r, "base": r[f"base_{baseline}"], "base_n": r[f"n_{baseline}"]} for r in rows]


def measure(rows: list[dict]) -> dict:
    d = study.describe(_against(rows, "random"))
    m = study.describe(_against(rows, "matched"))
    if rows:
        d["matched_baseline_r"] = m.get("baseline_r")
        d["median_matched_draws"] = m.get("median_baseline_draws")
    return {
        "describe": d,
        "random": study.test(_against(rows, "random")),
        "matched": study.test(_against(rows, "matched")),
    }


def run(workers: int = 14, path=None, progress=print) -> dict:
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    candidates = history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME)
    conn.close()
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(path), data_end)) as pool:
        progress(f"round 4: membership over {len(candidates):,} names")
        member_rows = [
            r for part in pool.map(_membership, study._chunks(candidates, workers * 8)) for r in part
        ]
        by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
        by_date: dict[tuple[str, str], list[str]] = defaultdict(list)
        for sym, days in member_rows:
            for d, sl in days:
                by_name[(sym, sl)].append(d)
                by_date[(d, sl)].append(sym)
        names = sorted({sym for sym, _ in member_rows})
        progress(f"round 4: positions in {len(names):,} names that ever qualify")
        rows = [r for part in pool.map(_real, study._chunks(names, workers * 8)) for r in part]
        rows.sort(key=lambda r: (r["setup"], r["symbol"], r["date"]))
        work = draw_plan(rows, by_date, by_name)
        progress(f"round 4: {len(rows):,} entries, {sum(len(v) for v in work.values()):,} baseline draws")
        acc: dict[int, list[float]] = {}
        for part in pool.map(_draws, study._chunks(sorted(work.items()), workers * 8)):
            study.merge_sums(acc, part)
    for key, row in enumerate(rows):
        a = acc.get(key, [0.0, 0, 0.0, 0])
        row["base_random"] = a[0] / a[1] if a[1] else None
        row["n_random"] = a[1]
        row["base_matched"] = a[2] / a[3] if a[3] else None
        row["n_matched"] = a[3]
    tested: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["slice"] == "tested":
            tested[r["setup"]].append(r)
    out: dict[str, dict] = {}
    for setup_id, baseline in TESTS:
        against = _against(tested.get(setup_id, []), baseline)
        out[hypothesis_id(setup_id, baseline)] = {
            "setup": setup_id,
            "baseline": baseline,
            "describe": study.describe(against),
            "test": study.test(against),
        }
    hypotheses.decide(out)
    setups_out = {}
    for setup_id in FAMILY:
        rs_ = tested.get(setup_id, [])
        seen = [r for r in rows if r["setup"] == setup_id and r["slice"] == "seen"]
        setups_out[setup_id] = {
            **measure(rs_),
            "views": {
                **{
                    name: measure([r for r in rs_ if lo <= r["date"] <= hi])
                    for name, lo, hi in study.SUB_PERIODS
                },
                "seen_300m": measure(seen),
                "half_A": measure([r for r in rs_ if hypotheses.half(r["symbol"]) == "A"]),
                "half_B": measure([r for r in rs_ if hypotheses.half(r["symbol"]) == "B"]),
            },
        }
    return {
        "round": 4,
        "seed": SEED,
        "draws_same_date": DRAWS_SAME_DATE,
        "draws_same_name": DRAWS_SAME_NAME,
        "alpha": ALPHA,
        "rules": {s.id: s.rule for s in setups.STUDIED if s.id in FAMILY},
        "parameters": {
            "benchmark": BENCHMARK,
            "lookback": LOOKBACK,
            "fresh": FRESH,
            "volume": [VOLUME_AVG, VOLUME_MULT],
            "hold": HOLD,
            "slice_below": universe.VIEW_DOLLAR_VOLUME,
        },
        "names_in_universe": len(names),
        "data_end": data_end,
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started),
        "hypotheses": out,
        "setups": setups_out,
    }


def write(result: dict) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"round4-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)
