"""Round 2 of the historical study: nine improvements, declared before they were run.

The declaration, its reasons and the decision rule are in docs/signal-log-plan.md ("Round 2"),
committed before this module first ran. In brief:

- Each hypothesis starts from one setup and changes one thing: a filter at entry (`FILTERS`) or a
  replacement exit (`EXITS`). Its positions are re-walked with the change, so a filtered-out signal
  never blocks a later one and a different exit frees the next entry when it says.
- Names are split for good into halves A and B by the CRC-32 of the ticker. Stage A tests the whole
  family on half A; stage B re-tests only stage A's survivors on half B. Baseline draws come from
  the same half as the entry.
- The baseline matches the conditions: for each counted entry, CANDIDATES same-date and CANDIDATES
  same-name draws are taken with a fixed seed, and only those passing the hypothesis's own filter
  that day are kept, held to the hypothesis's exit. A filter is credited only with what the setup
  adds under it.
- A hypothesis passes a stage only with a Holm-significant edge over that baseline (family-wise
  ALPHA, across the family tested in that stage) AND a positive net R: it must make money after
  costs on its own.

Everything else -- universe, holdout for the tuned families, corporate-action exclusions, fills,
costs, the calendar-time test -- is `study.py`'s, unchanged.
"""

from __future__ import annotations

import json
import random
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import history, indicators, paths, setups, store, study, trend, universe

SEED = 202610042
CANDIDATES = 40
ALPHA = 0.05


@dataclass(frozen=True)
class Hypothesis:
    id: str
    setup: str
    filter: str | None
    exit: str | None
    text: str


FAMILY = (
    Hypothesis("mr-trend-agrees", "reversion", "trend_agrees_long", None, "both trend scores above zero"),
    Hypothesis("mr-above-200", "reversion", "above_sma200", None, "close above the name's 200-session SMA"),
    Hypothesis("mr-300m", "reversion", "dollar_volume_300m", None, "median dollar volume >= $300M"),
    Hypothesis("trend-market-up", "trend", "spy_above_200", None, "SPY above its 200-session SMA"),
    Hypothesis("breakout-market-up", "breakout", "spy_above_200", None, "SPY above its 200-session SMA"),
    Hypothesis(
        "trend-short-market-down", "trend-short", "spy_below_200", None, "SPY below its 200-session SMA"
    ),
    Hypothesis(
        "pullback-short-market-down", "pullback-short", "spy_below_200", None, "SPY below its 200-session SMA"
    ),
    Hypothesis("pullback-hold-10", "pullback", None, "hold_10", "exit after 10 sessions, no target or stop"),
    Hypothesis(
        "pullback-no-target", "pullback", None, "chandelier_only", "no target; exit on the Chandelier stop"
    ),
)
BY_ID = {h.id: h for h in FAMILY}


def half(symbol: str) -> str:
    return "A" if zlib.crc32(symbol.encode("utf-8")) % 2 == 0 else "B"


# ------------------------------------------------------------------------- filters and exits


class Context:
    """One name's extra series, computed once and only if a filter asks for them."""

    def __init__(self, nm: study.Name, spy_above: dict[str, bool]):
        self.nm = nm
        self.spy_above = spy_above
        self._short = self._long = self._sma200 = None

    @property
    def short(self):
        if self._short is None:
            self._short = trend.scores(self.nm.closes, trend.SHORT_TERM)
        return self._short

    @property
    def long(self):
        if self._long is None:
            self._long = trend.scores(self.nm.closes, trend.LONG_TERM)
        return self._long

    @property
    def sma200(self):
        if self._sma200 is None:
            self._sma200 = indicators.sma(self.nm.closes, 200)
        return self._sma200


def _trend_agrees_long(c: Context, i: int) -> bool:
    s, lg = c.short[i], c.long[i]
    return s is not None and lg is not None and s > 0 and lg > 0


def _above_sma200(c: Context, i: int) -> bool:
    m = c.sma200[i]
    return m is not None and c.nm.closes[i] > m


def _dollar_volume_300m(c: Context, i: int) -> bool:
    return (c.nm.dollar_volume[i] or 0.0) >= universe.VIEW_DOLLAR_VOLUME


FILTERS: dict[str, Callable[[Context, int], bool]] = {
    "trend_agrees_long": _trend_agrees_long,
    "above_sma200": _above_sma200,
    "dollar_volume_300m": _dollar_volume_300m,
    "spy_above_200": lambda c, i: c.spy_above.get(c.nm.dates[i]) is True,
    "spy_below_200": lambda c, i: c.spy_above.get(c.nm.dates[i]) is False,
}

HOLD = 10


def _hold_10(r: setups.Readings, j: int, t: setups.Trade) -> str | None:
    return "time" if j - t.entry >= HOLD else None


def _chandelier_only(r: setups.Readings, j: int, t: setups.Trade) -> str | None:
    return setups._pullback_leave(r, j, replace(t, target=None))


EXITS: dict[str, Callable[[setups.Readings, int, setups.Trade], str | None]] = {
    "hold_10": _hold_10,
    "chandelier_only": _chandelier_only,
}


def spy_regime(conn) -> dict[str, bool]:
    """Session -> SPY closed above its 200-session SMA (adjusted closes); no entry before 200."""
    bars = store.adjusted_bars(conn, "SPY")
    closes = [b.close for b in bars]
    sma = indicators.sma(closes, 200)
    return {b.date: b.close > m for b, m in zip(bars, sma, strict=True) if m is not None}


def positions(h: Hypothesis, nm: study.Name, ctx: Context) -> list[setups.Trade]:
    allow = (lambda i: FILTERS[h.filter](ctx, i)) if h.filter else None
    return setups.run(h.setup, nm.readings, allow=allow, leave=EXITS[h.exit] if h.exit else None)


# ------------------------------------------------------------------------------- the passes

_spy: dict[str, bool] = {}


def _init(path: str, data_end: str) -> None:
    global _spy
    study._init(path, data_end)
    _spy = spy_regime(study._conn)


def _real(job: tuple[list[str], list[str]]) -> tuple[list[dict], list[dict]]:
    """Each hypothesis's counted positions in each name, and the base setups' (for comparison,
    without a baseline)."""
    symbols, ids = job
    family = [BY_ID[k] for k in ids]
    rows, base = [], []
    for sym in symbols:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        ctx = Context(nm, _spy)
        for setup_id in sorted({h.setup for h in family}):
            for t in setups.run(setup_id, nm.readings):
                if study.counted(nm, setup_id, t.entry) and not study.spans_suspect(nm, t.entry, t):
                    sc = study.score(nm, setup_id, t)
                    if sc is not None:
                        base.append({"setup": setup_id, "date": nm.dates[t.entry], **sc})
        for h in family:
            for t in positions(h, nm, ctx):
                i = t.entry
                if not study.counted(nm, h.setup, i) or study.spans_suspect(nm, i, t):
                    continue
                sc = study.score(nm, h.setup, t)
                if sc is not None:
                    rows.append({"hypothesis": h.id, "symbol": sym, "date": nm.dates[i], **sc})
    return rows, base


def baseline_trade(h: Hypothesis, nm: study.Name, ctx: Context, i: int) -> setups.Trade | None:
    """A baseline draw at bar `i`: None unless it passes the hypothesis's own filter (and is clear
    of a suspected corporate action); otherwise held to the hypothesis's exit."""
    if nm.shadow and nm.shadow[i]:
        return None
    if h.filter and not FILTERS[h.filter](ctx, i):
        return None  # the baseline holds the hypothesis's own conditions
    t = setups.exit_from(h.setup, nm.readings, i, leave=EXITS[h.exit] if h.exit else None)
    return None if study.spans_suspect(nm, i, t) else t


def _draws(work: list[tuple[str, list[tuple[int, str, str, str]]]]) -> dict[int, list[float]]:
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for sym, draws in work:
        nm = study.load(study._conn, sym, study._data_end)
        if nm is None:
            continue
        ctx = Context(nm, _spy)
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, kind, hid, day in draws:
            h = BY_ID[hid]
            i = where.get(day)
            t = None if i is None else baseline_trade(h, nm, ctx, i)
            if t is None:
                continue
            sc = study.score(nm, h.setup, t)
            if sc is None:
                continue
            slot = acc[key]
            if kind == "date":
                slot[0] += sc["r"]
                slot[1] += 1
            else:
                slot[2] += sc["r"]
                slot[3] += 1
    return dict(acc)


def draw_plan(rows: list[dict], by_date: dict[str, list[str]], by_name: dict[str, list[str]]):
    work: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for key, row in enumerate(rows):
        rng = random.Random(f"{SEED}:{row['hypothesis']}:{row['symbol']}:{row['date']}")
        others = [s for s in by_date.get(row["date"], ()) if s != row["symbol"]]
        for sym in rng.sample(others, min(CANDIDATES, len(others))):
            work[sym].append((key, "date", row["hypothesis"], row["date"]))
        days = [d for d in by_name.get(row["symbol"], ()) if d != row["date"]]
        for day in rng.sample(days, min(CANDIDATES, len(days))):
            work[row["symbol"]].append((key, "name", row["hypothesis"], day))
    return work


def decide(out: dict[str, dict]) -> None:
    """The declared rule, in place: `passed` needs a Holm-significant positive edge over the
    matched baseline (across the hypotheses in `out`) AND a positive net R."""
    judged = {k: v["test"]["p"] for k, v in out.items() if v["test"].get("judged")}
    holm = study.holm(judged, ALPHA)
    for k, v in out.items():
        sig = bool(holm.get(k, False) and v["test"].get("edge_r", 0) > 0)
        profitable = (v["describe"].get("expectancy_r") or 0) > 0
        v["significant"], v["profitable"] = sig, profitable
        v["passed"] = sig and profitable


def survivors(stage_a: dict) -> list[str]:
    return [k for k, v in stage_a["hypotheses"].items() if v["passed"]]


def run(stage: str, ids: list[str] | None = None, workers: int = 14, path=None, progress=print) -> dict:
    ids = ids or [h.id for h in FAMILY]
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    candidates = [
        s for s in history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME) if half(s) == stage
    ]
    conn.close()
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(path), data_end)) as pool:
        progress(f"stage {stage}: membership over {len(candidates):,} names")
        member_rows = [
            r for part in pool.map(study._membership, study._chunks(candidates, workers * 8)) for r in part
        ]
        by_name = {sym: days for sym, days in member_rows}
        by_date: dict[str, list[str]] = defaultdict(list)
        for sym, days in member_rows:
            for d in days:
                by_date[d].append(sym)
        names = sorted(by_name)
        progress(f"stage {stage}: {len(ids)} hypotheses over {len(names):,} names that ever qualify")
        rows, base = [], []
        for part, b in pool.map(_real, [(c, ids) for c in study._chunks(names, workers * 8)]):
            rows.extend(part)
            base.extend(b)
        rows.sort(key=lambda r: (r["hypothesis"], r["symbol"], r["date"]))
        work = draw_plan(rows, by_date, by_name)
        progress(f"stage {stage}: {sum(len(v) for v in work.values()):,} baseline candidates")
        acc: dict[int, list[float]] = {}
        for part in pool.map(_draws, study._chunks(sorted(work.items()), workers * 8)):
            acc.update(part)
    for key, row in enumerate(rows):
        a = acc.get(key)
        n = (a[1] + a[3]) if a else 0
        row["base"] = (a[0] + a[2]) / n if n else None
        row["base_n"] = n
    by_h: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_h[r["hypothesis"]].append(r)
    out: dict[str, dict] = {}
    for hid in ids:
        h = BY_ID[hid]
        rs = by_h.get(hid, [])
        out[hid] = {
            "setup": h.setup,
            "change": h.text,
            "describe": study.describe(rs),
            "test": study.test(rs),
            "base_setup_describe": study.describe([b for b in base if b["setup"] == h.setup]),
            "sub_periods": {
                name: study.describe([r for r in rs if lo <= r["date"] <= hi])
                for name, lo, hi in study.SUB_PERIODS
            },
            "median_baseline_draws": sorted(r["base_n"] for r in rs)[len(rs) // 2] if rs else 0,
        }
    decide(out)
    return {
        "round": 2,
        "stage": stage,
        "seed": SEED,
        "candidates_per_kind": CANDIDATES,
        "alpha": ALPHA,
        "family": ids,
        "names_in_universe": len(names),
        "data_end": data_end,
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started),
        "hypotheses": out,
    }


def write(result: dict) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"round2-stage{result['stage']}-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return str(target)


def latest(stage: str) -> dict | None:
    found = sorted(paths.study_dir().glob(f"round2-stage{stage}-*.json"))
    return json.loads(Path(found[-1]).read_text(encoding="utf-8")) if found else None
