"""The signal board over history: each name's count of agreeing methods on every past session.

    python scripts/board_history.py [--workers 12] [--start 2017-10-26] [--end 2025-06-30]
                                    [--verify 400] [--out PATH] [--limit-names N]

The live board (`cherrypick.technicals.board`) counts, for one session, how many of six methods
agree on a name's side: the relative-strength stage, its sector fund's rotation, fundamentals
against industry peers, fundamentals against price, the trend-following scan and an analyst
revision. This replays that count on every session from the first estimate snapshot (2017-10-26),
for edge-hypotheses item 14's selector B (docs/edge-hypotheses-plan.md). It lives in `scripts/`
because it reads `history.db`, which `packages/backtest` may not import.

**The same rules, read as of each session.** Every method is the live module's own function:
- stage: `round5.stage_series` (`stage.series` on SPY's calendar, as `stage.stages_on` reads it);
- rotation: `rotation.states_on` on the sector funds' closes cut at the session;
- fundamentals: `fundamentals.market_percentiles` (industry peers) on the newest weekly estimate
  snapshot on or before the session, with `board`'s tails;
- fundamentals vs price: that place against `levels.rank_score`'s decile, the whole-market cut-offs
  built as `land.land_rank_cutoffs` builds them live (`round5.market_scores`);
- scan: `signals.matches` on the session and the one before, trend-following rules only;
- revision: `revisions.by_session`, the session's own events.

Indicators are computed once per name over its whole series and read at each index. That is only
correct because every one is causal (a value at index i reads closes up to i), so the build proves
it: **the look-ahead guard** (`verify`) recomputes a random sample of name-sessions through the live
`board.tags_on` with every input cut at the session (`as_of`, which itself refuses anything dated
after it), and refuses the build if any count differs. `tests/test_board_history_script.py` shows
the guard failing on a planted one-session leak.

**Declared limits.** The sector and industry maps are today's (survivors in the older years);
revisions cover the names their fetch reads (today's); the three flow tags are not counted, as on
the live board. Bars are adjusted on today's basis: a split or dividend after a session multiplies
every bar up to it by one constant, and every method reads ratios of those bars, so the count is
unchanged (the guard's report also counts agreement with bars adjusted as of the session).

**Writes** `board_history.db` under the technicals data dir by default: one row per name-session
with two or more tags on either side (symbol, session, bullish, bearish, the methods each side),
and a `meta` row with the guard's result. Dolt-derived, not vendor data. Read-only otherwise.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
import sqlite3
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from cherrypick.technicals import (
    board,
    fundamentals,
    history,
    levels,
    paths,
    revisions,
    rotation,
    round5,
    signals,
    stage,
    store,
    symbols,
)

START = "2017-10-26"  # Dolt's first estimate snapshot: fundamentals and fvp need it
END = "2025-06-30"  # item 14's design window ends here (the holdout starts the next day)
DOLLAR_FLOOR = 300e6  # names that ever traded this much a day (`peaks`), item 14's universe
KEEP_AT = 2  # rows stored: two or more tags on a side
METHODS = tuple(board.METHODS)


class LookAheadError(AssertionError):
    """An input dated after the session reached a board reading, or the fast path disagreed with the
    as-of reference."""


@dataclass
class Market:
    """Whole-market inputs, each keyed by the session (or snapshot) it is known on."""

    calendar: list[str]  # SPY's adjusted sessions
    bench: list[float]  # SPY's adjusted closes on them
    fund_closes: dict[str, dict[str, float]]  # sector funds + benchmarks, every session
    rotation: dict[str, dict[str, str | None]] = field(default_factory=dict)  # session -> fund -> state
    snapshots: list[str] = field(default_factory=list)
    fund_pct: dict[str, dict[str, float]] = field(default_factory=dict)  # snapshot -> symbol -> place
    cutoffs: dict[str, list[float]] = field(default_factory=dict)  # session -> 1-10 cut-offs
    events: list = field(default_factory=list)  # revision events
    rev: dict = field(default_factory=dict)  # (symbol, session) -> revisions.Tag
    sector_of: dict[str, str] = field(default_factory=dict)

    def snapshot_on(self, day: str) -> str | None:
        k = bisect_right(self.snapshots, day) - 1
        return self.snapshots[k] if k >= 0 else None


# ------------------------------------------------------------------------------------ the fast path


def scan_sides(highs: list[float], lows: list[float], closes: list[float]) -> list[set[int]]:
    """For each index, the sides of the trend-following rules `signals.matches` finds on the bars up
    to it (empty when any reading is missing). Causal: every indicator here reads only the past."""
    return [
        set() if r is None else {board.SCAN_SIDE[m] for m in signals.matches(r) if m in board.SCAN_SIDE}
        for r in signals.readings_series(highs, lows, closes)
    ]


def rank_score_at(closes: list[float], i: int) -> float | None:
    """`levels.rank_score(closes[: i + 1])` without the copy."""
    if i < levels.RANK_LONG:
        return None
    c, cs, cl = closes[i], closes[i - levels.RANK_SHORT], closes[i - levels.RANK_LONG]
    if cl <= 0 or cs <= 0:
        return None
    return levels.RANK_SHORT_WEIGHT * (c / cs - 1) + (c / cl - 1)


def name_tags(sym: str, bars: list, mk: Market, lo: str, hi: str) -> dict[str, tuple[set[str], set[str]]]:
    """{session: (bullish methods, bearish methods)} for every session in [lo, hi] the name has a
    bar on and at least one tag, read exactly as `board.tags_on` reads that session."""
    if not bars:
        return {}
    dates = [b.date for b in bars]
    closes = [b.close for b in bars]
    at_cal = {d: k for k, d in enumerate(mk.calendar)}
    aligned: list[float | None] = [None] * len(mk.calendar)
    for b in bars:
        k = at_cal.get(b.date)
        if k is not None:
            aligned[k] = b.close
    stages = round5.stage_series(aligned, mk.bench)
    scans = scan_sides([b.high for b in bars], [b.low for b in bars], closes)
    fund = board.SECTOR_FUND.get(mk.sector_of.get(sym, ""))
    out: dict[str, tuple[set[str], set[str]]] = {}
    for i, d in enumerate(dates):
        if d < lo or d > hi:
            continue
        sides: list[tuple[str, int]] = []
        k = at_cal.get(d)
        st = stages[k] if k is not None else None
        if st is not None:
            sides.append(("stage", 1 if st.side == "leader" else -1))
        if fund:
            r = board.ROTATION_SIDE.get((mk.rotation.get(d) or {}).get(fund))
            if r:
                sides.append(("rotation", r))
        snap = mk.snapshot_on(d)
        p = mk.fund_pct.get(snap, {}).get(sym) if snap else None
        if p is not None and (p >= board.COMPELLING_AT or p <= board.WEAK_AT):
            sides.append(("fundamentals", 1 if p >= board.COMPELLING_AT else -1))
        hit = set(scans[i]) | (set(scans[i - 1]) if i >= 1 else set())
        if len(hit) == 1:
            sides.append(("scan", next(iter(hit))))
        cut = mk.cutoffs.get(d)
        if p is not None and cut:
            sc = rank_score_at(closes, i)
            if sc is not None:
                rank = levels.rank_from_cutoffs(sc, cut)
                if p >= board.FVP_MEDIAN and rank <= board.FVP_PRICE_LAGS_AT:
                    sides.append(("fvp", 1))
                elif p <= board.FVP_MEDIAN and rank >= board.FVP_PRICE_LEADS_AT:
                    sides.append(("fvp", -1))
        rt = mk.rev.get((sym, d))
        if rt is not None and rt.direction:
            sides.append(("revision", rt.direction))
        if sides:
            out[d] = ({m for m, s in sides if s > 0}, {m for m, s in sides if s < 0})
    return out


# ------------------------------------------------------------------------------- the as-of reference


def as_of(day: str, sym: str, bars: list, mk: Market) -> dict:
    """`board.tags_on`'s arguments for one name on `day`, every input cut at it. Refuses (raises
    `LookAheadError`) if anything handed on is dated after `day`: the guard's own guard."""
    snap = mk.snapshot_on(day)
    kw = {
        "day": day,
        "bars": {sym: [b for b in bars if b.date <= day]},
        "bench": {d: c for d, c in zip(mk.calendar, mk.bench, strict=True) if d <= day},
        "fund_closes": {f: {d: c for d, c in s.items() if d <= day} for f, s in mk.fund_closes.items()},
        "sector_of": mk.sector_of,
        "rev_tags": revisions.tags(
            [e for e in mk.events if e.symbol == sym and revisions.session_of(e.graded_at) <= day], day
        ),
        "fund_pct": mk.fund_pct.get(snap, {}) if snap else {},
        "rank_cutoffs": mk.cutoffs.get(day),
    }
    assert_as_of(day, kw, snap)
    return kw


def assert_as_of(day: str, kw: dict, snapshot: str | None) -> None:
    late = [b.date for bs in kw["bars"].values() for b in bs if b.date > day]
    late += [d for d in kw["bench"] if d > day]
    late += [d for s in kw["fund_closes"].values() for d in s if d > day]
    late += [revisions.session_of(e.graded_at) for t in kw["rev_tags"].values() for e in t.events]
    late = [d for d in late if d > day]
    if snapshot is not None and snapshot > day:
        late.append(snapshot)
    if late:
        raise LookAheadError(f"{day}: inputs dated after the session reached the board: {sorted(late)[:3]}")


def reference(day: str, sym: str, bars: list, mk: Market) -> tuple[set[str], set[str]]:
    """The live board's own reading of one name on `day`, from inputs cut at it."""
    tags = board.tags_on(**as_of(day, sym, bars, mk)).get(sym, [])
    return {t.method for t in tags if t.side > 0}, {t.method for t in tags if t.side < 0}


def verify(samples: list[tuple[str, str]], bars_of, mk: Market) -> dict:
    """The look-ahead guard: each sampled (symbol, session) read by the fast path and by the as-of
    reference. Any difference raises `LookAheadError`; a clean run returns the counts."""
    bad = []
    for sym, day in samples:
        bars = bars_of(sym)
        fast = name_tags(sym, bars, mk, day, day).get(day, (set(), set()))
        ref = reference(day, sym, bars, mk)
        if fast != ref:
            bad.append(
                {
                    "symbol": sym,
                    "session": day,
                    "fast": [sorted(x) for x in fast],
                    "ref": [sorted(x) for x in ref],
                }
            )
    if bad:
        raise LookAheadError(
            f"{len(bad)} of {len(samples)} sampled readings differ from the as-of board: {bad[:3]}"
        )
    return {"sampled": len(samples), "differ": 0}


# ------------------------------------------------------------------------------------- whole market

_w: dict = {}


def _init(path: str, mk: Market | None = None) -> None:
    _w["conn"] = history.connect(Path(path))
    _w["mk"] = mk
    if mk is not None:
        _w["idx"] = {d: k for k, d in enumerate(mk.calendar)}


def _market_part(syms: list[str]) -> list[tuple[int, float]]:
    conn, out = _w["conn"], []
    idx = _w["idx"]
    for s in syms:
        raw = store.raw_bars(conn, s)
        if raw:
            out += round5.market_scores(idx, raw, [x.ex_date for x in store.splits(conn, s)])
    return out


def _pct_part(snaps: list[str]) -> dict[str, dict[str, float]]:
    conn = fundamentals.connect(Path(_w["path"])) if "path" in _w else _w["conn"]
    return {s: fundamentals.market_percentiles(conn, s, _w["sector_of"], by_industry=True) for s in snaps}


def _init_pct(path: str, sector_of: dict) -> None:
    _w["path"] = path
    _w["sector_of"] = sector_of


def _names_part(syms: list[str]) -> list[tuple]:
    conn, mk = _w["conn"], _w["mk"]
    rows = []
    for s in syms:
        bars = store.adjusted_bars(conn, s)
        for d, (bull, bear) in name_tags(s, bars, mk, _w["lo"], _w["hi"]).items():
            if max(len(bull), len(bear)) >= KEEP_AT:
                rows.append((s, d, len(bull), len(bear), ",".join(sorted(bull)), ",".join(sorted(bear))))
    return rows


def _init_names(path: str, mk: Market, lo: str, hi: str) -> None:
    _init(path, mk)
    _w["lo"], _w["hi"] = lo, hi


def build_market(path: Path, lo: str, hi: str, workers: int, progress=print) -> Market:
    conn = history.connect(path)
    spy = store.adjusted_bars(conn, stage.DEFAULT_RULE.benchmark)
    funds = sorted(
        {*board.SECTOR_FUND.values(), rotation.DEFAULT_RULE.benchmark, rotation.DEFAULT_RULE.asset_benchmark}
    )
    fund_closes = {f: {b.date: b.close for b in store.adjusted_bars(conn, f)} for f in funds}
    everyone = [s for (s,) in conn.execute("SELECT symbol FROM peaks ORDER BY symbol")]
    snaps = [s for s in fundamentals.snapshot_dates(fundamentals.connect(path)) if s <= hi]
    conn.close()
    mk = Market([b.date for b in spy], [b.close for b in spy], fund_closes)
    mk.sector_of = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    mk.snapshots = snaps
    sessions = [d for d in mk.calendar if lo <= d <= hi]

    t0 = time.monotonic()
    for d in sessions:
        mk.rotation[d] = rotation.states_on(
            d, {f: {x: c for x, c in s.items() if x <= d} for f, s in fund_closes.items()}, symbols.ASSET_ETFS
        )
    progress(f"rotation: {len(sessions)} sessions ({time.monotonic() - t0:.0f}s)")

    want = {mk.calendar.index(d) for d in sessions}
    by_day: dict[int, list[float]] = defaultdict(list)
    with ProcessPoolExecutor(
        workers, initializer=_init, initargs=(str(path), Market(mk.calendar, [], {}))
    ) as ex:
        for part in ex.map(_market_part, [everyone[k :: workers * 4] for k in range(workers * 4)]):
            for k, s in part:
                if k in want:
                    by_day[k].append(s)
    mk.cutoffs = {mk.calendar[k]: levels.rank_cutoffs(v) for k, v in by_day.items() if len(v) >= 100}
    del by_day
    progress(f"rank cut-offs: {len(mk.cutoffs)} sessions ({time.monotonic() - t0:.0f}s)")

    need = [s for s in snaps if s >= (mk.snapshot_on(lo) or lo)]
    with ProcessPoolExecutor(workers, initializer=_init_pct, initargs=(str(path), mk.sector_of)) as ex:
        for part in ex.map(_pct_part, [need[k::workers] for k in range(workers)]):
            mk.fund_pct.update(part)
    progress(f"fundamentals: {len(mk.fund_pct)} snapshots ({time.monotonic() - t0:.0f}s)")

    mk.events = revisions.load()
    mk.rev = revisions.by_session(mk.events)
    progress(f"revisions: {len(mk.events)} events")
    return mk


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--verify", type=int, default=400, help="name-sessions the look-ahead guard samples")
    ap.add_argument("--out", type=Path, default=paths.data_dir() / "board_history.db")
    ap.add_argument("--limit-names", type=int)
    ap.add_argument(
        "--market-cache", type=Path, help="keep the whole-market inputs here between runs (a trial)"
    )
    args = ap.parse_args(argv)

    path = paths.history_db()
    if args.market_cache and args.market_cache.exists():
        mk = pickle.loads(args.market_cache.read_bytes())
    else:
        mk = build_market(path, args.start, args.end, args.workers)
        if args.market_cache:
            args.market_cache.write_bytes(pickle.dumps(mk))
    conn = history.connect(path)
    etf = {s for (s,) in conn.execute("SELECT symbol FROM listings WHERE is_etf = 1")}
    names = [
        s
        for s in history.names_peaking_at_least(conn, DOLLAR_FLOOR)
        if s not in etf and s not in symbols.INDEXES
    ]
    if args.limit_names:
        names = names[: args.limit_names]

    cache: dict[str, list] = {}

    def bars_of(s):
        if s not in cache:
            cache[s] = store.adjusted_bars(conn, s)
        return cache[s]

    # Sessions the name has an (adjusted) bar on: the only ones the fast path reads, and the only ones
    # a trade can enter on. (The live board also tags a name with no bar that day, on rotation,
    # fundamentals and revisions alone; the guard found that difference on its first run.)
    rng = random.Random(20261023)
    samples: list[tuple[str, str]] = []
    while len(samples) < args.verify:
        s = rng.choice(names)
        days = [b.date for b in bars_of(s) if args.start <= b.date <= args.end]
        if days:
            samples.append((s, rng.choice(days)))

    guard = verify(samples, bars_of, mk)
    # Information only: the same sample against bars adjusted as of each session.
    asof_agree = sum(
        name_tags(s, store.adjusted_bars(conn, s, as_of=d), mk, d, d).get(d, (set(), set()))
        == name_tags(s, bars_of(s), mk, d, d).get(d, (set(), set()))
        for s, d in samples
    )
    conn.close()
    print(f"look-ahead guard: {guard}; as-of adjustment agrees on {asof_agree}/{len(samples)}", flush=True)

    rows: list[tuple] = []
    with ProcessPoolExecutor(
        args.workers, initializer=_init_names, initargs=(str(path), mk, args.start, args.end)
    ) as ex:
        for part in ex.map(_names_part, [names[k :: args.workers * 4] for k in range(args.workers * 4)]):
            rows += part
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript(
        "CREATE TABLE board (symbol TEXT NOT NULL, session TEXT NOT NULL, bullish INTEGER, bearish INTEGER,"
        " bull_methods TEXT, bear_methods TEXT, PRIMARY KEY (symbol, session));"
        "CREATE INDEX board_by_session ON board (session);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    out.executemany("INSERT INTO board VALUES (?,?,?,?,?,?)", rows)
    meta = {
        "start": args.start, "end": args.end, "names": len(names), "keep_at": KEEP_AT,
        "board_version": board.BOARD_VERSION, "guard": guard,
        "asof_adjusted_agree": [asof_agree, len(samples)],
        "snapshots": len(mk.fund_pct), "cutoff_sessions": len(mk.cutoffs), "revision_events": len(mk.events),
    }  # fmt: skip
    out.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
    out.commit()
    out.close()
    tmp.replace(args.out)
    print(json.dumps({**meta, "rows": len(rows), "out": str(args.out)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
