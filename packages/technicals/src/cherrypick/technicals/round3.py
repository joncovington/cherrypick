"""Round 3 of the historical study: two setups from published infographics, declared before they were run.

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 3"),
written before this module first ran. In brief:

- Two setups, each long and short (`setups.STUDIED`): Supertrend(10, 3) with the Vortex
  Indicator(14) (`st-vortex`), and a Bollinger squeeze broken on an RSI divergence (`squeeze-div`;
  the infographic draws the short, the long is its mirror). Textbook settings, nothing tuned, and
  nothing in either rule was suggested by this history, so every name counts, as in round 1.
- Each setup-side is tested against two baselines built from the same draws (DRAWS_SAME_DATE
  same-date and DRAWS_SAME_NAME same-name entries, seeded from the entry's identity, each held to
  the setup's own exit):
  - `random` keeps every draw: plan v2's baseline, the one round 1's table uses;
  - `matched` keeps only the draws made on a day the setup's STATE already held (`MATCHED`):
    Supertrend and the Vortex already on its side, or a close already outside the band. It asks
    whether the trigger adds anything over the state it fires in. Without it, a random draw outside
    that state leaves at the next close, a round trip that measures the spread and not an entry.
- Eight tests, Holm across all eight at ALPHA. A test passes with a Holm-significant positive edge
  AND a positive net R. A setup-side is confirmed only when both of its tests pass.
- Views, never tests: the two sub-periods, the $300M names, halves A and B, the index funds, and
  each rule with one of its two conditions removed (`ABLATIONS`, described without a baseline).

Everything else -- universe, corporate-action exclusions, fills, costs, the calendar-time test -- is
`study.py`'s, unchanged.
"""

from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime

from . import history, hypotheses, paths, setups, study, universe

SEED = 202610043
DRAWS_SAME_DATE = 20
DRAWS_SAME_NAME = 20
ALPHA = 0.05
FAMILY = ("st-vortex", "st-vortex-short", "squeeze-div", "squeeze-div-short")
BASELINES = ("random", "matched")
ABLATIONS = {
    "st-vortex": ("st-only", "vortex-only"),
    "st-vortex-short": ("st-only-short", "vortex-only-short"),
    "squeeze-div": ("squeeze-band", "div-band"),
    "squeeze-div-short": ("squeeze-band-short", "div-band-short"),
}
INDEX_FUNDS = frozenset({"SPY", "QQQ", "IWM", "DIA"})

# The state each setup's trigger fires in: a matched draw must be made on a day it held.
MATCHED: dict[str, Callable[[setups.Readings, int], bool]] = {
    "st-vortex": lambda r, i: setups.agree(r, i, True),
    "st-vortex-short": lambda r, i: setups.agree(r, i, False),
    "squeeze-div": lambda r, i: r.bb_upper[i] is not None and r.closes[i] > r.bb_upper[i],
    "squeeze-div-short": lambda r, i: r.bb_lower[i] is not None and r.closes[i] < r.bb_lower[i],
}


def hypothesis_id(setup_id: str, baseline: str) -> str:
    return f"{setup_id}:{baseline}"


# ------------------------------------------------------------------------------------- the passes


def _real(symbols: list[str]) -> list[dict]:
    """Every counted, scorable position of the family and its ablations, in each name."""
    rows = []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        for setup_id in FAMILY + tuple(a for v in ABLATIONS.values() for a in v):
            for t in setups.run(setup_id, nm.readings):
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
                        "exit_date": nm.dates[t.exit] if t.exit is not None else None,
                        "reason": t.reason if not sc["ended"] else "data ended",
                        "dollar_volume": nm.dollar_volume[i],
                        **sc,
                    }
                )
    return rows


def draw_plan(rows: list[dict], by_date: dict[str, list[str]], by_name: dict[str, list[str]]):
    """For each entry (by its index in `rows`), its seeded draws, bucketed by the name they land in."""
    work: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for key, row in enumerate(rows):
        rng = random.Random(f"{SEED}:{row['setup']}:{row['symbol']}:{row['date']}")
        others = [s for s in by_date.get(row["date"], ()) if s != row["symbol"]]
        for sym in rng.sample(others, min(DRAWS_SAME_DATE, len(others))):
            work[sym].append((key, row["setup"], row["date"]))
        days = [d for d in by_name.get(row["symbol"], ()) if d != row["date"]]
        for day in rng.sample(days, min(DRAWS_SAME_NAME, len(days))):
            work[row["symbol"]].append((key, row["setup"], day))
    return work


def _draws(work: list[tuple[str, list[tuple[int, str, str]]]]) -> dict[int, list[float]]:
    """Score each draw once; per entry key, [sum, n] over every draw and [sum, n] over the matched
    ones (`study.merge_sums` adds the four across workers)."""
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for sym, draws in work:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, setup_id, day in draws:
            i = where.get(day)
            if i is None or nm.shadow[i]:
                continue
            t = setups.exit_from(setup_id, nm.readings, i)
            if study.spans_suspect(nm, i, t):
                continue
            sc = study.score(nm, setup_id, t)
            if sc is None:
                continue
            slot = acc[key]
            slot[0] += sc["r"]
            slot[1] += 1
            if MATCHED[setup_id](nm.readings, i):
                slot[2] += sc["r"]
                slot[3] += 1
    return dict(acc)


# ------------------------------------------------------------------------------------ the verdict


def _against(rows: list[dict], baseline: str) -> list[dict]:
    return [{**r, "base": r[f"base_{baseline}"], "base_n": r[f"n_{baseline}"]} for r in rows]


def measure(rows: list[dict]) -> dict:
    """One set of entries: described against the random baseline, and tested against both."""
    d = study.describe(_against(rows, "random"))
    m = study.describe(_against(rows, "matched"))
    if rows:
        d["matched_baseline_r"] = m.get("baseline_r")
        d["median_matched_draws"] = m.get("median_baseline_draws")
    return {"describe": d, **{b: study.test(_against(rows, b)) for b in BASELINES}}


def decide(out: dict[str, dict]) -> dict[str, dict]:
    """The declared rule, in place, and each setup-side's verdict: a test passes with a
    Holm-significant positive edge (across the eight) AND a positive net R; a setup-side is
    confirmed only when both of its tests pass."""
    hypotheses.decide(out)
    verdicts = {}
    for setup_id in FAMILY:
        tests = {b: out[hypothesis_id(setup_id, b)] for b in BASELINES}
        verdicts[setup_id] = {
            "confirmed": all(v["passed"] for v in tests.values()),
            **{b: v["passed"] for b, v in tests.items()},
        }
    return verdicts


def run(workers: int = 14, path=None, progress=print) -> dict:
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    candidates = history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME)
    conn.close()
    started = time.monotonic()
    with ProcessPoolExecutor(
        max_workers=workers, initializer=study._init, initargs=(str(path), data_end)
    ) as pool:
        progress(f"round 3: membership over {len(candidates):,} names")
        member_rows = [
            r for part in pool.map(study._membership, study._chunks(candidates, workers * 8)) for r in part
        ]
        by_name = {sym: days for sym, days in member_rows}
        by_date: dict[str, list[str]] = defaultdict(list)
        for sym, days in member_rows:
            for d in days:
                by_date[d].append(sym)
        names = sorted(by_name)
        progress(f"round 3: positions in {len(names):,} names that ever qualify")
        rows = [r for part in pool.map(_real, study._chunks(names, workers * 8)) for r in part]
        rows.sort(key=lambda r: (r["setup"], r["symbol"], r["date"]))
        main = [r for r in rows if r["setup"] in FAMILY]
        work = draw_plan(main, by_date, by_name)
        progress(f"round 3: {len(main):,} entries, {sum(len(v) for v in work.values()):,} baseline draws")
        acc: dict[int, list[float]] = {}
        for part in pool.map(_draws, study._chunks(sorted(work.items()), workers * 8)):
            study.merge_sums(acc, part)
    for key, row in enumerate(main):
        a = acc.get(key, [0.0, 0, 0.0, 0])
        row["base_random"] = a[0] / a[1] if a[1] else None
        row["n_random"] = a[1]
        row["base_matched"] = a[2] / a[3] if a[3] else None
        row["n_matched"] = a[3]
    by_setup: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_setup[r["setup"]].append(r)
    out: dict[str, dict] = {}
    for setup_id in FAMILY:
        rs = by_setup.get(setup_id, [])
        for b in BASELINES:
            against = _against(rs, b)
            out[hypothesis_id(setup_id, b)] = {
                "setup": setup_id,
                "baseline": b,
                "describe": study.describe(against),
                "test": study.test(against),
            }
    verdicts = decide(out)
    setups_out = {}
    for setup_id in FAMILY:
        rs = by_setup.get(setup_id, [])
        setups_out[setup_id] = {
            "verdict": verdicts[setup_id],
            **measure(rs),
            "reasons": dict(sorted(_count(r["reason"] for r in rs).items())),
            "views": {
                **{
                    name: measure([r for r in rs if lo <= r["date"] <= hi])
                    for name, lo, hi in study.SUB_PERIODS
                },
                "dollar_volume_300m": measure(
                    [r for r in rs if (r["dollar_volume"] or 0) >= universe.VIEW_DOLLAR_VOLUME]
                ),
                "half_A": measure([r for r in rs if hypotheses.half(r["symbol"]) == "A"]),
                "half_B": measure([r for r in rs if hypotheses.half(r["symbol"]) == "B"]),
                "index_funds": measure([r for r in rs if r["symbol"] in INDEX_FUNDS]),
            },
            "ablations": {a: study.describe(by_setup.get(a, [])) for a in ABLATIONS[setup_id]},
        }
    return {
        "round": 3,
        "seed": SEED,
        "draws_same_date": DRAWS_SAME_DATE,
        "draws_same_name": DRAWS_SAME_NAME,
        "alpha": ALPHA,
        "rules": {s.id: s.rule for s in setups.STUDIED},
        "parameters": {
            "supertrend": [setups.SUPERTREND_N, setups.SUPERTREND_MULT],
            "vortex_n": setups.VORTEX_N,
            "divergence_pivot": setups.DIVERGENCE_PIVOT,
            "divergence_span": list(setups.DIVERGENCE_SPAN),
            "squeeze": [setups.SQUEEZE_LOOKBACK, setups.SQUEEZE_RECENT],
        },
        "names_in_universe": len(names),
        "data_end": data_end,
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started),
        "hypotheses": out,
        "setups": setups_out,
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return dict(out)


def write(result: dict) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"round3-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)
