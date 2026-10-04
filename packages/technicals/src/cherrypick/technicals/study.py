"""The historical study: the chart setups over 2011 onward, scored under analysis plan v2.

docs/signal-log-plan.md holds the plan and its reasons; this is its arithmetic. In brief:

- **Universe**: a name counts on a session only if `universe.membership` admits it, from data up to
  the session before.
- **Holdout**: the four setup-sides whose rules were tuned on 2023-2026 (`TUNED`) count only before
  `TUNING_END`, or on names outside `tuning_names.NAMES`.
- **Fills**: the next session's open after the signal close, for entries and exits alike. Returns
  come from adjusted bars, which carry a dividend inside the holding period exactly as the plan's
  "raw bars with corporate actions applied" would: a long is paid it, a short owes it.
- **Measures**: R is the move in the trade's direction over ATR(14) at the signal bar, net of costs.
- **Costs**: half the Corwin-Schultz spread on every fill, averaged over the SPREAD_WINDOW sessions up
  to the day before; shorts also pay BORROW_RATE a year. Both declared on 2026-10-04, before any
  result was looked at.
- **Baseline**: for each counted entry, DRAWS_SAME_DATE entries on the same date in other names from
  that day's universe, and DRAWS_SAME_NAME entries in the same name on other dates it qualified,
  each held to the same setup's exit rule (`setups.exit_from`) and scored the same way. The draws are
  seeded from the entry's own identity, so they replay exactly and do not depend on run order.
- **Test**: per setup-side, the mean of (R - baseline R) in calendar time, one return per session,
  one-sided; Holm across the eight at a family-wise ALPHA. A setup-side short of MIN_EFFECTIVE
  effective entries reads "not yet judged".

Positions still open at the end of the data are left out. A name whose data ends early (delisted, or
renamed) closes an open position at its last close, flagged: its delisting return is not in Dolt.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import mean, median, pstdev

from . import history, paths, setups, store, tuning_names, universe

PLAN = "v2"
SEED = 20261004
DRAWS_SAME_DATE = 20
DRAWS_SAME_NAME = 20
SPREAD_WINDOW = 21
BORROW_RATE = 0.01  # a year, on shorts; declared 2026-10-04, before any result was looked at
TUNED = frozenset({"pullback", "pullback-short", "breakout", "breakout-short"})
TUNING_END = "2022-12-30"
SUB_PERIODS = (("2011-2018", "2011-01-01", "2018-12-31"), ("2019-2026", "2019-01-01", "2026-12-31"))
ALPHA = 0.05
MIN_EFFECTIVE = 780  # independent entries for a 55% vs 50% hit rate at power 0.8 (the plan's sizing)
ENDED_DAYS = 10  # a name whose last bar is this many days before the data's end has stopped trading
SIDE = {s.id: (1 if s.side == "long" else -1) for s in setups.SETUPS}
# Sessions after a suspected unrecorded corporate action (`history.suspected_actions`) whose signals
# are not counted: the indicators read the false crash or surge for this long (the squeeze looks
# back 120 sessions). Positions and baseline draws holding through one are not scored either. The
# same rule for real entries and draws, so the comparison stays fair; the counts are reported.
SHADOW = 120


# ------------------------------------------------------------------------------- a hindsight view

TRADABLE_RATING = 3
TRADABLE_EXPIRIES = 4


def tradable_today() -> tuple[set[str], str | None]:
    """(names options-tradable on the day the IV-rank file labels most names, that day): weeklies and a
    tastytrade liquidity rating of 3 or 4 on that one day. Today's list, so applied to past dates it
    is hindsight -- a view on the results, flagged as such, never the universe."""
    import json as _json

    try:
        doc = _json.loads(paths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), None
    # The day the most names carry an expiry count: each row is filed under tastytrade's own update
    # time, so a weekend fetch leaves a few stragglers on a later day (18 on 2026-10-03, against
    # 2,651 on 2026-10-02), and "the newest day" would read almost nothing.
    labelled = {
        d: sum(1 for v in rows.values() if v.get("expiries_35d") is not None)
        for d, rows in doc.get("days", {}).items()
    }
    if not labelled or max(labelled.values()) == 0:
        return set(), None
    day = max(labelled, key=lambda d: (labelled[d], d))
    rows = doc["days"][day]
    names = {
        s
        for s, v in rows.items()
        if (v.get("expiries_35d") or 0) >= TRADABLE_EXPIRIES
        and (v.get("liquidity_rating") or 0) >= TRADABLE_RATING
    }
    return names, day


# ------------------------------------------------------------------------------------------ costs


def corwin_schultz(highs: list[float], lows: list[float]) -> list[float | None]:
    """The two-day Corwin & Schultz (2012) spread estimate for each day, as a fraction of price;
    negatives set to zero, as the paper does. None on the first day and wherever a price is missing."""
    k = 3 - 2 * math.sqrt(2)
    out: list[float | None] = [None] * len(highs)
    for t in range(1, len(highs)):
        h0, l0, h1, l1 = highs[t - 1], lows[t - 1], highs[t], lows[t]
        if not (h0 and l0 and h1 and l1) or l0 <= 0 or l1 <= 0:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h0 / l0) ** 2
        gamma = math.log(max(h0, h1) / min(l0, l1)) ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
        out[t] = max(0.0, 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha)))
    return out


def spread_known(daily: list[float | None], i: int) -> float | None:
    """The mean daily estimate over the SPREAD_WINDOW sessions ending at `i`: what was known by the
    close of `i`, for a fill at the next open."""
    window = [s for s in daily[max(0, i - SPREAD_WINDOW + 1) : i + 1] if s is not None]
    return mean(window) if window else None


# ------------------------------------------------------------------------------------- one name


@dataclass
class Name:
    symbol: str
    dates: list[str]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    member: list[bool]
    dollar_volume: list[float | None]
    spreads: list[float | None]
    readings: setups.Readings
    ended: bool
    suspects: list[int] = None  # indices of suspected unrecorded corporate actions
    shadow: list[bool] = None  # True within SHADOW sessions after one


def load(conn, symbol: str, data_end: str, light: bool = False) -> Name | None:
    """A name's adjusted bars, its universe membership and, unless `light`, everything the setups
    and the scoring read (the membership pass needs only the first two)."""
    adj = store.adjusted_bars(conn, symbol)
    if len(adj) < universe.WINDOW + 10:
        return None
    raw = {b.date: b for b in store.raw_bars(conn, symbol)}
    member, basis = universe.membership([raw[b.date].close for b in adj], [raw[b.date].volume for b in adj])
    if light:
        return Name(symbol, [b.date for b in adj], [], [], [], [], member, basis, [], None, False)
    highs, lows, closes = [b.high for b in adj], [b.low for b in adj], [b.close for b in adj]
    last = date.fromisoformat(adj[-1].date)
    split_dates = [date.fromisoformat(x.ex_date) for x in store.splits(conn, symbol)]
    at = {b.date: k for k, b in enumerate(adj)}
    raw_list = [raw[b.date] for b in adj]
    suspects = sorted(at[d] for d in history.suspected_actions(raw_list, split_dates) if d in at)
    shadow = [False] * len(adj)
    for k in suspects:
        for j in range(k, min(len(adj), k + SHADOW + 1)):
            shadow[j] = True
    return Name(
        symbol=symbol,
        dates=[b.date for b in adj],
        opens=[b.open for b in adj],
        highs=highs,
        lows=lows,
        closes=closes,
        member=member,
        dollar_volume=basis,
        spreads=corwin_schultz(highs, lows),
        readings=setups.readings(highs, lows, closes, [b.volume or None for b in adj]),
        ended=(date.fromisoformat(data_end) - last).days > ENDED_DAYS,
        suspects=suspects,
        shadow=shadow,
    )


def spans_suspect(nm: Name, i: int, t: setups.Trade) -> bool:
    """A position from the fill after bar `i` to its exit fill (or the data's end) holds through a
    suspected unrecorded corporate action."""
    if not nm.suspects:
        return False
    last = t.exit + 1 if t.exit is not None else len(nm.closes) - 1
    return any(i + 1 <= k <= last for k in nm.suspects)


def score(nm: Name, setup_id: str, t: setups.Trade) -> dict | None:
    """One position, filled at the next opens and scored in R net of costs; None when it can't be
    scored (no next bar, no ATR, a missing price, or still open at the end of live data)."""
    i, n, side = t.entry, len(nm.closes), SIDE[setup_id]
    atr = nm.readings.atr14[i]
    if i + 1 >= n or not atr or not nm.opens[i + 1]:
        return None
    entry_fill = nm.opens[i + 1]
    ended = False
    if t.exit is not None and t.exit + 1 < n and nm.opens[t.exit + 1]:
        x, exit_fill = t.exit, nm.opens[t.exit + 1]
        exit_at = x + 1
    elif nm.ended:
        x, exit_fill, exit_at, ended = n - 1, nm.closes[n - 1], n - 1, True
    else:
        return None  # still open (or its exit fill is past the data): not scored
    s_in, s_out = spread_known(nm.spreads, i), spread_known(nm.spreads, x)
    cost = (s_in or 0.0) / 2 * entry_fill + (s_out or 0.0) / 2 * exit_fill
    days = (date.fromisoformat(nm.dates[exit_at]) - date.fromisoformat(nm.dates[i + 1])).days
    if side < 0:
        cost += BORROW_RATE * days / 365 * entry_fill
    gross = side * (exit_fill - entry_fill)
    path_hi = max(nm.highs[i + 1 : exit_at + 1])
    path_lo = min(nm.lows[i + 1 : exit_at + 1])
    favour, against = (
        (path_hi - entry_fill, path_lo - entry_fill)
        if side > 0
        else (
            entry_fill - path_lo,
            entry_fill - path_hi,
        )
    )
    return {
        "r": (gross - cost) / atr,
        "r_gross": gross / atr,
        "pct": (gross - cost) / entry_fill,
        "cost_r": cost / atr,
        "hold": exit_at - (i + 1),
        "mfe": favour / atr,
        "mae": against / atr,
        "ended": ended,
        "spread_known": s_in is not None and s_out is not None,
    }


def counted(nm: Name, setup_id: str, i: int) -> bool:
    """In the universe on the signal day, and, for a tuned setup-side, inside its holdout."""
    if not nm.member[i]:
        return False
    if nm.shadow and nm.shadow[i]:
        return False  # in the shadow of a suspected corporate action
    if setup_id in TUNED:
        return nm.dates[i] <= TUNING_END or nm.symbol not in tuning_names.NAMES
    return True


# ------------------------------------------------------------------------------- the three passes

_conn = None
_data_end = None


def _init(path: str, data_end: str) -> None:
    global _conn, _data_end
    from pathlib import Path

    _conn = history.connect(Path(path))
    _data_end = data_end


def _membership(symbols: list[str]) -> list[tuple[str, list[str]]]:
    """Pass A: each name's qualifying sessions."""
    out = []
    for sym in symbols:
        nm = load(_conn, sym, _data_end, light=True)
        if nm is not None and any(nm.member):
            out.append((sym, [d for d, ok in zip(nm.dates, nm.member, strict=True) if ok]))
    return out


def _real(symbols: list[str]) -> tuple[list[dict], dict[str, int]]:
    """Pass B: every setup's positions in each name, with whether each counts; and per setup, the
    in-universe positions left out for a suspected corporate action."""
    rows = []
    excluded: dict[str, int] = defaultdict(int)
    for sym in symbols:
        nm = load(_conn, sym, _data_end)
        if nm is None:
            continue
        for setup_id in setups.RUN:
            for t in setups.run(setup_id, nm.readings):
                if not nm.member[t.entry]:
                    continue  # out of the universe on its signal day: never counted, not kept
                if spans_suspect(nm, t.entry, t) or nm.shadow[t.entry]:
                    excluded[setup_id] += 1  # a suspected corporate action: not scored, counted
                    continue
                sc = score(nm, setup_id, t)
                if sc is None:
                    continue
                i = t.entry
                rows.append(
                    {
                        "symbol": sym,
                        "setup": setup_id,
                        "date": nm.dates[i],
                        "exit_date": nm.dates[t.exit] if t.exit is not None else None,
                        "reason": t.reason if not sc["ended"] else "data ended",
                        "counted": counted(nm, setup_id, i),
                        "member": nm.member[i],
                        "dollar_volume": nm.dollar_volume[i],
                        "rule201": SIDE[setup_id] < 0 and i > 0 and nm.lows[i] <= 0.9 * nm.closes[i - 1],
                        **sc,
                    }
                )
    return rows, dict(excluded)


def _draws(work: list[tuple[str, list[tuple[int, str, str, str]]]]) -> dict[int, list[float]]:
    """Pass C: score each baseline draw in each name; per entry key, [sum same-date, n, sum
    same-name, n]."""
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for sym, draws in work:
        nm = load(_conn, sym, _data_end)
        if nm is None:
            continue
        where = {d: i for i, d in enumerate(nm.dates)}
        for key, kind, setup_id, day in draws:
            i = where.get(day)
            if i is None:
                continue
            if nm.shadow[i]:
                continue
            t = setups.exit_from(setup_id, nm.readings, i)
            if spans_suspect(nm, i, t):
                continue
            sc = score(nm, setup_id, t)
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
    """For each counted entry, its seeded draws, bucketed by the name they land in."""
    work: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for key, row in enumerate(rows):
        if not row["counted"]:
            continue
        rng = random.Random(f"{SEED}:{row['symbol']}:{row['setup']}:{row['date']}")
        others = [s for s in by_date.get(row["date"], ()) if s != row["symbol"]]
        for sym in rng.sample(others, min(DRAWS_SAME_DATE, len(others))):
            work[sym].append((key, "date", row["setup"], row["date"]))
        days = [d for d in by_name.get(row["symbol"], ()) if d != row["date"]]
        for day in rng.sample(days, min(DRAWS_SAME_NAME, len(days))):
            work[row["symbol"]].append((key, "name", row["setup"], day))
    return work


def _chunks(items: list, n: int) -> list[list]:
    return [items[k::n] for k in range(n)]


def run(workers: int = 14, path=None, progress=print, tradable: set[str] | None = None) -> dict:
    path = path or paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    # A median can never exceed the largest day: a name whose peak dollar volume is under the floor
    # can never qualify, so it is skipped unread (`history.names_peaking_at_least`).
    symbols = history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME)
    conn.close()
    started = time.monotonic()
    init = (_init, (str(path), data_end))
    with ProcessPoolExecutor(max_workers=workers, initializer=init[0], initargs=init[1]) as pool:
        progress(f"pass A: membership over {len(symbols):,} names")
        member_rows = [r for part in pool.map(_membership, _chunks(symbols, workers * 8)) for r in part]
        by_name = {sym: days for sym, days in member_rows}
        by_date: dict[str, list[str]] = defaultdict(list)
        for sym, days in member_rows:
            for d in days:
                by_date[d].append(sym)
        names = sorted(by_name)
        progress(f"pass B: positions in {len(names):,} names that ever qualify")
        rows, excluded = [], defaultdict(int)
        for part, ex in pool.map(_real, _chunks(names, workers * 8)):
            rows.extend(part)
            for k, v in ex.items():
                excluded[k] += v
        rows.sort(key=lambda r: (r["symbol"], r["setup"], r["date"]))
        work = draw_plan(rows, by_date, by_name)
        progress(f"pass C: {sum(len(v) for v in work.values()):,} baseline draws in {len(work):,} names")
        items = sorted(work.items())
        acc: dict[int, list[float]] = {}
        for part in pool.map(_draws, _chunks(items, workers * 8)):
            acc.update(part)
    for key, row in enumerate(rows):
        a = acc.get(key)
        row["base_date"] = a[0] / a[1] if a and a[1] else None
        row["base_name"] = a[2] / a[3] if a and a[3] else None
        n = (a[1] + a[3]) if a else 0
        row["base"] = (a[0] + a[2]) / n if n else None
    result = summarise(rows, tradable)
    result.update(
        plan=PLAN,
        generated_at=datetime.now(UTC).isoformat(),
        data_end=data_end,
        seconds=round(time.monotonic() - started),
        parameters={
            "seed": SEED,
            "draws_same_date": DRAWS_SAME_DATE,
            "draws_same_name": DRAWS_SAME_NAME,
            "spread_window": SPREAD_WINDOW,
            "borrow_rate": BORROW_RATE,
            "tuning_end": TUNING_END,
            "tuned": sorted(TUNED),
            "universe": {
                "min_price": universe.MIN_PRICE,
                "min_dollar_volume": universe.MIN_DOLLAR_VOLUME,
                "window": universe.WINDOW,
            },
            "alpha": ALPHA,
            "min_effective": MIN_EFFECTIVE,
            "rules": {s.id: s.rule for s in setups.SETUPS},
        },
        names_in_universe=len(by_name),
        excluded_for_corporate_actions=dict(excluded),
    )
    return result


# ----------------------------------------------------------------------------------- statistics


def test(rows: list[dict]) -> dict:
    """The plan's test over one set of rows: (R - baseline) in calendar time, one-sided."""
    rows = [r for r in rows if r["base"] is not None]
    if len(rows) < 2:
        return {"entries": len(rows), "sessions": 0, "judged": False}
    diffs = [r["r"] - r["base"] for r in rows]
    by_day: dict[str, list[float]] = defaultdict(list)
    for r, d in zip(rows, diffs, strict=True):
        by_day[r["date"]].append(d)
    series = [mean(v) for v in by_day.values()]
    n_days, n = len(series), len(diffs)
    sd_days = pstdev(series) if n_days > 1 else 0.0
    sd_trades = pstdev(diffs)
    se = sd_days / math.sqrt(n_days) if n_days > 1 else float("inf")
    t = mean(series) / se if se and se != float("inf") else 0.0
    deff = ((sd_days**2 / n_days) / (sd_trades**2 / n)) if sd_trades and n_days > 1 else 1.0
    effective = n / deff if deff > 0 else n
    return {
        "entries": n,
        "sessions": n_days,
        "edge_r": mean(series),
        "edge_r_per_entry": mean(diffs),
        "t": t,
        "p": 0.5 * math.erfc(t / math.sqrt(2)),
        "design_effect": deff,
        "effective_entries": effective,
        "judged": effective >= MIN_EFFECTIVE,
    }


def describe(rows: list[dict]) -> dict:
    if not rows:
        return {"entries": 0}
    r = [x["r"] for x in rows]
    wins, losses = [v for v in r if v > 0], [v for v in r if v <= 0]
    base = [x["base"] for x in rows if x.get("base") is not None]
    return {
        "entries": len(rows),
        "expectancy_r": mean(r),
        "expectancy_r_gross": mean(x["r_gross"] for x in rows),
        "expectancy_pct": mean(x["pct"] for x in rows),
        "cost_r": mean(x["cost_r"] for x in rows),
        "hit_rate": len(wins) / len(r),
        "payoff": (mean(wins) / -mean(losses)) if wins and losses and mean(losses) < 0 else None,
        "median_hold": median(x["hold"] for x in rows),
        "median_mfe": median(x["mfe"] for x in rows),
        "median_mae": median(x["mae"] for x in rows),
        "baseline_r": mean(base) if base else None,
        "ended_by_data": sum(1 for x in rows if x["ended"]),
    }


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, bool]:
    """Holm's step-down: reject the k-th smallest p while p <= alpha / (m - k)."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m, out, stop = len(order), {}, False
    for k, key in enumerate(order):
        ok = not stop and pvalues[key] <= alpha / (m - k)
        stop = stop or not ok
        out[key] = ok
    return out


def summarise(rows: list[dict], tradable: set[str] | None = None) -> dict:
    by_setup: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["counted"]:
            by_setup[r["setup"]].append(r)
    out: dict[str, dict] = {}
    for setup_id in setups.RUN:
        rs = by_setup.get(setup_id, [])
        entry = {"describe": describe(rs), "test": test(rs)}
        entry["sub_periods"] = {
            name: {"describe": describe(sub), "test": test(sub)}
            for name, lo, hi in SUB_PERIODS
            for sub in [[r for r in rs if lo <= r["date"] <= hi]]
        }
        big = [r for r in rs if (r["dollar_volume"] or 0) >= universe.VIEW_DOLLAR_VOLUME]
        entry["views"] = {"dollar_volume_300m": {"describe": describe(big), "test": test(big)}}
        if tradable:
            today = [r for r in rs if r["symbol"] in tradable]
            entry["views"]["options_tradable_today_hindsight"] = {
                "describe": describe(today),
                "test": test(today),
            }
        if SIDE[setup_id] < 0:
            clean = [r for r in rs if not r["rule201"]]
            entry["views"]["without_rule201"] = {"describe": describe(clean), "test": test(clean)}
            entry["rule201_flagged"] = len(rs) - len(clean)
        out[setup_id] = entry
    judged = {k: v["test"]["p"] for k, v in out.items() if v["test"].get("judged")}
    passed = holm(judged)
    for k, v in out.items():
        v["verdict"] = (
            "not yet judged"
            if k not in judged
            else "edge over baseline"
            if passed[k] and v["test"]["edge_r"] > 0
            else "no edge over baseline"
        )
    return {
        "setups": out,
        "positions_scored": len(rows),
        "positions_counted": sum(1 for r in rows if r["counted"]),
    }


def write(result: dict, rows: list[dict] | None = None) -> str:
    out = paths.study_dir()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = out / f"history-results-{stamp}.json"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    tmp.replace(target)
    return str(target)
