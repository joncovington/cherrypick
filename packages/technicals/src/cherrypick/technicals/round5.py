"""Round 5 of the historical study: the 1-10 relative-strength score inside the vendor's fundamentals.

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 5"),
committed before this module first ran. In brief:

- **The score** is our reproduction of the vendor's 1-10 (`levels.rank_score`'s decile across the
  whole listed market, built exactly as `land.land_rank_cutoffs` builds it live), read on the signal
  session.
- **Fundamentals** are `fundamentals.py`'s weekly label: "compelling" or "weak", percentiles taken
  within the name's sector (the market report's sector map) -- or, as a side view, across every name
  with estimates.
- **Two directional triggers, each both ways:**
  - `stage`: the name becomes an early leader (`stage.classify`, the live rule) with no leader
    reading of any stage in the FRESH sessions before; bearish, an early laggard likewise;
  - `trend`: the short-term trend score reaches Bullish (>= 3) from below; bearish, Bearish (<= -3)
    from above.
- **Four tests:** bullish = compelling + trigger + score 1-3, long; bearish = weak + trigger +
  score 8-10, short. Each against the same label and trigger at the OTHER scores (4-10 bullish, 1-7
  bearish), entered within POOL_WINDOW sessions of it. Held HOLD sessions, next-open fills, study
  costs. Holm across the four at ALPHA; a test passes with a Holm-significant positive edge AND a
  positive net R.

Everything else -- universe, corporate-action exclusions, fills, costs, the calendar-time test -- is
`study.py`'s, unchanged.
"""

from __future__ import annotations

import json
import time
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime

from . import fundamentals, history, hypotheses, levels, paths, setups, stage, store, study, trend, universe

ALPHA = 0.05
HOLD = 21
FRESH = 10
POOL_WINDOW = 10
START = "2017-10-30"  # the first session after the estimates' first month of snapshots
LOW, HIGH = (1, 2, 3), (8, 9, 10)
TRIGGERS = ("stage", "trend")
VARIANTS = ("sector", "universe")  # where the fundamentals percentiles are taken; sector is tested
TESTS = tuple((side, trig) for side in ("bull", "bear") for trig in TRIGGERS)


def hypothesis_id(side: str, trigger: str) -> str:
    return f"{side}:{trigger}"


# ------------------------------------------------------------------------------- the 1-10 score


def market_scores(dates_idx: dict[str, int], raw, split_dates: list[str]) -> list[tuple[int, float]]:
    """One name's contribution to the whole-market cut-offs, as `land_rank_cutoffs` counts it: on
    each session it closed on, with closes 21 and 126 sessions back on the benchmark's calendar, a
    raw dollar volume of at least RANK_MIN_DOLLAR_VOLUME and no split inside the long window."""
    n = len(dates_idx)
    close = [None] * n
    vol = [0.0] * n
    for b in raw:
        k = dates_idx.get(b.date)
        if k is not None and b.close:
            close[k], vol[k] = b.close, b.volume or 0.0
    order = sorted(dates_idx, key=dates_idx.get)
    out = []
    for i in range(levels.RANK_LONG, n):
        c, cs, cl = close[i], close[i - levels.RANK_SHORT], close[i - levels.RANK_LONG]
        if not (c and cs and cl) or c * vol[i] < levels.RANK_MIN_DOLLAR_VOLUME:
            continue
        lo, hi = order[i - levels.RANK_LONG], order[i]
        if any(lo < d <= hi for d in split_dates):
            continue
        out.append((i, levels.RANK_SHORT_WEIGHT * (c / cs - 1) + (c / cl - 1)))
    return out


def decile(score: float | None, cutoffs: list[float] | None) -> int | None:
    if score is None or not cutoffs:
        return None
    return levels.rank_from_cutoffs(score, cutoffs)


# ------------------------------------------------------------------------------ the triggers


def stage_series(closes: list[float | None], bench: list[float]) -> list[stage.Stage | None]:
    """`stage.classify` on every session, both series on the benchmark's calendar."""
    rule = stage.DEFAULT_RULE
    out: list[stage.Stage | None] = []
    for i in range(len(bench)):

        def ex(n, i=i):
            if i - n < 0:
                return None
            a, b = closes[i - n], closes[i]
            if not (a and b):
                return None
            return (b / a - 1.0) - (bench[i] / bench[i - n] - 1.0)

        out.append(stage.classify([ex(n) for n in rule.windows], ex(1), rule))
    return out


def stage_trigger(stages: list[stage.Stage | None], i: int, side: str) -> bool:
    """An early leader (laggard) today with no leader (laggard) reading in the FRESH sessions before."""
    want = "leader" if side == "bull" else "laggard"
    s = stages[i]
    if s is None or s.side != want or s.stage != "early" or i < FRESH:
        return False
    return not any(p is not None and p.side == want for p in stages[i - FRESH : i])


def trend_trigger(scores: list[int | None], i: int, side: str) -> bool:
    if i < 1 or scores[i] is None or scores[i - 1] is None:
        return False
    if side == "bull":
        return scores[i] >= 3 > scores[i - 1]
    return scores[i] <= -3 < scores[i - 1]


def held(i: int, size: int) -> setups.Trade:
    x = i + HOLD
    return setups.Trade(i, exit=x, reason="time") if x < size else setups.Trade(i)


# ------------------------------------------------------------------------------ the passes

_ctx: dict = {}


def _init(path: str, data_end: str) -> None:
    from pathlib import Path

    study._init(path, data_end)
    conn = history.connect(Path(path))
    spy = store.adjusted_bars(conn, "SPY")
    _ctx["dates"] = [b.date for b in spy]
    _ctx["idx"] = {d: k for k, d in enumerate(_ctx["dates"])}
    _ctx["spy"] = [b.close for b in spy]
    conn.close()


def _market(symbols: list[str]) -> list[tuple[int, float]]:
    out = []
    for sym in symbols:
        raw = store.raw_bars(study._conn, sym)
        if not raw:
            continue
        splits = [s.ex_date for s in store.splits(study._conn, sym)]
        out += market_scores(_ctx["idx"], raw, splits)
    return out


def _measures(symbols: list[str]) -> dict[str, dict[str, fundamentals.Measures]]:
    """Per snapshot, each name's measures (pass B)."""
    conn = study._conn
    conn.executescript(fundamentals.SCHEMA)
    out: dict[str, dict[str, fundamentals.Measures]] = defaultdict(dict)
    for sym in symbols:
        rows: dict[str, dict[str, tuple]] = defaultdict(dict)
        for d, kind, c, y in conn.execute(
            "SELECT date, kind, consensus, year_ago FROM estimates WHERE symbol = ?", (sym,)
        ):
            rows[d][kind] = (c, y)
        if not rows:
            continue
        qs = [
            tuple(r)
            for r in conn.execute(
                "SELECT period_end, sales, net_income, shares FROM quarters "
                "WHERE symbol = ? ORDER BY period_end",
                (sym,),
            )
        ]
        raw = store.raw_bars(conn, sym)
        bar_dates = [b.date for b in raw]
        for snap, r in rows.items():
            k = bisect_right(bar_dates, snap) - 1
            price = raw[k].close if k >= 0 else None
            cutoff = fundamentals.cutoff_for(snap)
            published = [q for q in qs if q[0] <= cutoff]
            shares = published[-1][3] if published else None
            margin = fundamentals.trailing_margin([(q[0], q[1], q[2]) for q in qs], snap)
            m = fundamentals.measures(price, r.get("eps"), r.get("sales"), shares, margin)
            if m is not None:
                out[snap][sym] = m
    return dict(out)


def _events(job: tuple[list[str], dict, dict, list[str], bool]) -> list[dict]:
    """Every trigger on a labelled, counted session, in each name (pass C). `labels` maps a name to
    {variant: {snapshot: label}}; `cutoffs` maps a session index to its nine cut-offs."""
    symbols, labels, cutoffs, snapshots, scored = job
    dates, idx, spy = _ctx["dates"], _ctx["idx"], _ctx["spy"]
    rows = []
    for sym in symbols:
        mine = labels.get(sym)
        if not mine:
            continue
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        on_cal: list[float | None] = [None] * len(dates)
        for d, c in zip(nm.dates, nm.closes, strict=True):
            k = idx.get(d)
            if k is not None:
                on_cal[k] = c
        stages = stage_series(on_cal, spy)
        tscores = trend.scores(nm.closes, trend.SHORT_TERM)
        raw = {b.date: b.close for b in store.raw_bars(study._conn, sym)}
        splits = [s.ex_date for s in store.splits(study._conn, sym)]
        for i, d in enumerate(nm.dates):
            if d < START:
                continue
            k = idx.get(d)
            if k is None or not study.counted(nm, "rs-fund", i):
                continue
            snap = _snapshot_before(snapshots, d)
            if snap is None:
                continue
            lab = {v: mine.get(v, {}).get(snap) for v in VARIANTS}
            if not any(lab.values()):
                continue
            fired = []
            for side in ("bull", "bear"):
                want = "compelling" if side == "bull" else "weak"
                if not any(lab[v] == want for v in VARIANTS):
                    continue
                if stage_trigger(stages, k, side):
                    fired.append((side, "stage"))
                if trend_trigger(tscores, i, side):
                    fired.append((side, "trend"))
            if not fired:
                continue
            score = _score_on(raw, splits, dates, k)
            dec = decile(score, cutoffs.get(k))
            if dec is None:
                continue
            for side, trig in fired:
                want = "compelling" if side == "bull" else "weak"
                row = {
                    "side": side,
                    "trigger": trig,
                    "symbol": sym,
                    "date": d,
                    "decile": dec,
                    **{f"label_{v}": lab[v] == want for v in VARIANTS},
                    "dollar_volume": nm.dollar_volume[i],
                }
                if scored:
                    t = held(i, len(nm.closes))
                    if study.spans_suspect(nm, i, t):
                        continue
                    sc = study.score(nm, "rs-fund" if side == "bull" else "rs-fund-short", t)
                    if sc is None:
                        continue
                    row.update(sc)
                rows.append(row)
    return rows


def _score_on(raw: dict[str, float], splits: list[str], dates: list[str], k: int) -> float | None:
    """The name's own rank score on session k, as the market's cut-offs were built."""
    if k < levels.RANK_LONG:
        return None
    d, ds, dl = dates[k], dates[k - levels.RANK_SHORT], dates[k - levels.RANK_LONG]
    c, cs, cl = raw.get(d), raw.get(ds), raw.get(dl)
    if not (c and cs and cl) or any(dl < s <= d for s in splits):
        return None
    return levels.RANK_SHORT_WEIGHT * (c / cs - 1) + (c / cl - 1)


def _snapshot_before(snapshots: list[str], session: str) -> str | None:
    k = bisect_right(snapshots, session) - 1
    if k >= 0 and snapshots[k] == session:
        k -= 1
    return snapshots[k] if k >= 0 else None


# ------------------------------------------------------------------------------ assembling it


def build(workers: int = 14, path=None, progress=print, scored: bool = True) -> dict:
    """Everything up to the verdict: the cut-offs, the weekly labels, and every event."""
    path = path or paths.history_db()
    conn = fundamentals.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    everyone = [s for (s,) in conn.execute("SELECT symbol FROM peaks ORDER BY symbol")]
    with_estimates = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM estimates ORDER BY symbol")]
    candidates = set(history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME))
    snapshots = fundamentals.snapshot_dates(conn)
    conn.close()
    sector_of = fundamentals.sector_map()
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(path), data_end)) as pool:
        progress(f"round 5: market scores over {len(everyone):,} names")
        by_day: dict[int, list[float]] = defaultdict(list)
        for part in pool.map(_market, study._chunks(everyone, workers * 8)):
            for k, s in part:
                by_day[k].append(s)
        cutoffs = {k: levels.rank_cutoffs(v) for k, v in by_day.items() if len(v) >= 100}
        del by_day
        progress(f"round 5: measures for {len(with_estimates):,} names over {len(snapshots)} snapshots")
        measures: dict[str, dict[str, fundamentals.Measures]] = defaultdict(dict)
        for part in pool.map(_measures, study._chunks(with_estimates, workers * 8)):
            for snap, ms in part.items():
                measures[snap].update(ms)
        labels: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
        counts = {v: [] for v in VARIANTS}
        for snap in snapshots:
            ms = measures.get(snap, {})
            for v in VARIANTS:
                group = sector_of if v == "sector" else {s: "all" for s in ms}
                lab = fundamentals.labels(fundamentals.scores(ms, group))
                counts[v].append(len(lab))
                for s, x in lab.items():
                    labels[s][v][snap] = x
        names = sorted(s for s in labels if s in candidates)
        progress(f"round 5: events in {len(names):,} labelled names")
        jobs = [
            (chunk, {s: dict(labels[s]) for s in chunk}, cutoffs, snapshots, scored)
            for chunk in study._chunks(names, workers * 4)
        ]
        rows = [r for part in pool.map(_events, jobs) for r in part]
    rows.sort(key=lambda r: (r["side"], r["trigger"], r["symbol"], r["date"]))
    return {
        "rows": rows,
        "snapshots": snapshots,
        "labelled_per_snapshot": {v: _summary(c) for v, c in counts.items()},
        "sessions_with_cutoffs": len(cutoffs),
        "names": len(names),
        "data_end": data_end,
        "seconds": round(time.monotonic() - started),
    }


def _summary(xs: list[int]) -> dict:
    xs = sorted(xs)
    return {"min": xs[0], "median": xs[len(xs) // 2], "max": xs[-1]} if xs else {}


def pools(rows: list[dict], variant: str, dates: list[str]) -> list[dict]:
    """The tested entries, each with its baseline: the mean R of the same side, trigger and label at
    the other deciles, entered within POOL_WINDOW sessions."""
    pos = {d: k for k, d in enumerate(dates)}
    out = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r[f"label_{variant}"]:
            groups[(r["side"], r["trigger"])].append(r)
    for (side, _trig), rs in groups.items():
        tested_deciles = LOW if side == "bull" else HIGH
        others = sorted((pos[r["date"]], r["r"]) for r in rs if r["decile"] not in tested_deciles)
        keys = [k for k, _ in others]
        for r in rs:
            if r["decile"] not in tested_deciles:
                continue
            k = pos[r["date"]]
            lo, hi = bisect_right(keys, k - POOL_WINDOW - 1), bisect_right(keys, k + POOL_WINDOW)
            window = [x for _, x in others[lo:hi]]
            out.append(
                {
                    **r,
                    "base": sum(window) / len(window) if window else None,
                    "base_n": len(window),
                }
            )
    return out


def run(workers: int = 14, path=None, progress=print) -> dict:
    built = build(workers, path, progress)
    conn = history.connect(path or paths.history_db())
    dates = [b.date for b in store.adjusted_bars(conn, "SPY")]
    conn.close()
    result_tests: dict[str, dict] = {}
    views: dict[str, dict] = {}
    for variant in VARIANTS:
        entries = pools(built["rows"], variant, dates)
        for side, trig in TESTS:
            rs = [r for r in entries if r["side"] == side and r["trigger"] == trig]
            block = {"describe": study.describe(rs), "test": study.test(rs), "side": side, "trigger": trig}
            if variant == "sector":
                result_tests[hypothesis_id(side, trig)] = block
            else:
                views[f"universe:{hypothesis_id(side, trig)}"] = block
    hypotheses.decide(result_tests)
    for side, trig in TESTS:
        for variant in VARIANTS:
            rs = [
                r
                for r in built["rows"]
                if r["side"] == side
                and r["trigger"] == trig
                and r[f"label_{variant}"]
                and r["decile"] in (HIGH if side == "bull" else LOW)
            ]
            views[f"late:{variant}:{hypothesis_id(side, trig)}"] = {"describe": study.describe(rs)}
    return {
        "round": 5,
        "alpha": ALPHA,
        "parameters": {
            "hold": HOLD,
            "fresh": FRESH,
            "pool_window": POOL_WINDOW,
            "start": START,
            "label_fraction": fundamentals.LABEL_FRACTION,
            "margin_weight": fundamentals.MARGIN_WEIGHT,
            "publication_lag_days": fundamentals.PUBLICATION_LAG,
            "low": LOW,
            "high": HIGH,
        },
        **{k: v for k, v in built.items() if k != "rows"},
        "entries": len(built["rows"]),
        "generated_at": datetime.now(UTC).isoformat(),
        "hypotheses": result_tests,
        "views": views,
    }


def write(result: dict) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"round5-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)
