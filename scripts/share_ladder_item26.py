"""Item 26, the dip-buying share ladder on leveraged funds (docs/edge-hypotheses-plan.md): buy a lot
7% under the latest 4:00 pm close, sell each lot 4% over its own cost, no stop.

    python scripts/share_ladder_item26.py study [--json PATH] [--names A,B]
    python scripts/share_ladder_item26.py intraday [--json PATH]

Reads `scripts/fetch_ladder_bars.py`'s `bars.db` and writes under
`~/.cherrypick/data/research/item26/`, never into the repo.

**Bars.** A session takes the broker's daily candle unless it is missing or its close is more than
1% from Yahoo's, then Yahoo's (`merge_daily`).

**The arms.** Lot size `TRANCHE` dollars in whole shares; a name's budget is `cap x TRANCHE`.
- `once`: at most one new lot a session, at 93% of the previous session's close.
- `chain`: with no lot open, the first lot at 93% of the previous close; then each further lot at
  93% of the last lot bought, in the same session or later.
- `steps`: a lot at each 7% step under the previous close the session reaches.
- `scaled`: `once` with the drop and the gain at 1.5 and 0.9 times the 20-session standard
  deviation of daily returns, read at the previous close.
Each with caps 1, 3, 5; `scaled` at 3.

**Fills on daily bars.** A buy fills at the open when the session opens at or under the level, else
at the level when the low reaches it. A sell fills at the open when the session opens at or over the
target, else at the target when the high reaches it. A lot bought in a session is not sold in it,
and a lot sold in a session does not free its slot until the next (the worse order). Costs `COST`
a side on the fill price. Lots open at a window's end are marked at the last close.

**Control.** For each arm, name and window: as many single-lot entries as the arm opened, on
sessions drawn at random, bought at the open, with the arm's exit, cap and budget; `DRAWS` draws,
the mean taken. Edge = the arm's net less the control's mean, as % of the budget a year.

**The verdict**, per arm: in the design, the per-name edge's mean above zero with one-sided t above
2 and Holm across the ten arms, positive on more than half the names, both halves' mean edge
positive, and the mean edge positive at `STRESS_COST`; then the holdout's mean edge above zero and
positive on more than half the names. Under `MIN_NAMES` names or `MIN_LOTS` closed lots in an arm
in a window is no verdict.

`intraday` replays `once` at cap 3 for SOXL, TECL and TQQQ on hourly bars cut to 8:00-20:00 ET
(the broker's, Yahoo's before them with outside-hours highs and lows clipped to 3% beyond the
bar's open and close) beside the daily replay over the same span, and checks the user's own fills
(a local file, never in the repo) against the broker's 5-minute bars. Reported, never judged.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from cherrypick.core import stats as core_stats
from cherrypick.core.home import data_dir

DROP = 0.07
GAIN = 0.04
SCALED = (1.5, 0.9)
SIGMA_SESSIONS = 20
TRANCHE = 10_000.0
COST = 0.0005
STRESS_COST = 0.0015
RULES = ("once", "chain", "steps")
CAPS = (1, 3, 5)
ARMS = tuple(f"{r}-{c}" for r in RULES for c in CAPS) + ("scaled-3",)
DESIGN = ("2011-01-03", "2019-12-31")
HALVES = (("2011-01-03", "2015-12-31"), ("2016-01-01", "2019-12-31"))
HOLDOUT = ("2020-01-02", "2026-10-28")
FIRST_BAR = "2011-01-03"
MIN_DOLLAR_VOLUME = 20e6
DOLLAR_VOLUME_FILE = "2026-10-29"
DISAGREE = 0.01
MIN_NAMES = 8
MIN_LOTS = 300
T_BAR = 2.0
ALPHA = 0.05
DRAWS = 200
SEED = 26
SESSIONS_A_YEAR = 252
INTRADAY = ("SOXL", "TECL", "TQQQ")
CLIP = 0.03

DIR = data_dir("research") / "item26"


@dataclass(frozen=True)
class Bar:
    start: str
    o: float
    h: float
    l: float  # noqa: E741
    c: float


@dataclass
class Lot:
    price: float
    shares: int
    opened: str
    target: float


@dataclass
class Run:
    pnl: float = 0.0
    opened: int = 0
    closed: int = 0
    open_at_end: int = 0
    peak_lots: int = 0
    longest_days: int = 0
    max_drawdown: float = 0.0
    sessions: int = 0
    holds: list = field(default_factory=list)


def arm_parts(arm: str) -> tuple[str, int]:
    rule, cap = arm.rsplit("-", 1)
    return rule, int(cap)


# ---- bars ----


def load(conn: sqlite3.Connection, symbol: str, source: str, interval: str) -> dict[str, Bar]:
    rows = conn.execute(
        "SELECT start, open, high, low, close FROM bars WHERE symbol = ? AND source = ? AND interval = ?",
        (symbol, source, interval),
    )
    return {r[0]: Bar(*r) for r in rows}


def merge_daily(broker: dict[str, Bar], yahoo: dict[str, Bar]) -> list[Bar]:
    """The broker's bar for a session unless missing or its close is over 1% from Yahoo's."""
    out = []
    for d in sorted(set(broker) | set(yahoo)):
        b, y = broker.get(d), yahoo.get(d)
        out.append(b if b and (y is None or abs(b.c / y.c - 1) <= DISAGREE) else y)
    return out


def sigma(bars: list[Bar], i: int) -> float | None:
    """Standard deviation of the 20 daily returns ending at bar i-1's close."""
    if i < SIGMA_SESSIONS + 1:
        return None
    rets = [bars[j].c / bars[j - 1].c - 1 for j in range(i - SIGMA_SESSIONS, i)]
    return statistics.stdev(rets)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


# ---- the replay ----


def entry_fills(
    rule: str, bar: Bar, ref: float, drop: float, last_buy: float | None, slots: int
) -> list[float]:
    """Fill prices of the lots a session opens, at most `slots`."""
    fills: list[float] = []
    if slots <= 0:
        return fills
    if rule in ("once", "scaled"):
        level = ref * (1 - drop)
        if bar.l <= level:
            fills.append(min(bar.o, level))
    elif rule == "steps":
        k = 1
        while len(fills) < slots and k * drop < 1:
            level = ref * (1 - k * drop)
            if bar.l > level:
                break
            fills.append(min(bar.o, level))
            k += 1
    elif rule == "chain":
        level = (last_buy if last_buy is not None else ref) * (1 - drop)
        while len(fills) < slots and bar.l <= level:
            px = min(bar.o, level)
            fills.append(px)
            level = px * (1 - drop)
    else:
        raise ValueError(rule)
    return fills[:slots]


def simulate(
    bars: list[Bar],
    start: str,
    end: str,
    rule: str,
    cap: int,
    *,
    cost: float = COST,
    random_days: set[str] | None = None,
    session_key=lambda b: b.start[:10],
    refs: list[float] | None = None,
) -> Run:
    """One name, one arm, one window. `random_days` turns it into the control: one lot at the open
    of each drawn session. `refs` (intraday) gives each bar's reference close; daily bars use the
    previous bar's close. `session_key` groups bars into sessions for `once`."""
    run = Run()
    lots: list[Lot] = []
    cash = 0.0
    last_buy: float | None = None
    eq_peak = 0.0
    bought_in: dict[str, int] = {}
    last = None
    keys = [b.start[:10] for b in bars]
    lo = max(1, bisect.bisect_left(keys, start))
    hi = bisect.bisect_right(keys, end)
    for i in range(lo, hi):
        bar = bars[i]
        run.sessions += 1
        ref = refs[i] if refs is not None else bars[i - 1].c
        # Exits: lots from earlier bars only.
        n_before = len(lots)
        keep = []
        for lot in lots:
            px = bar.o if bar.o >= lot.target else (lot.target if bar.h >= lot.target else None)
            if px is None:
                keep.append(lot)
                continue
            cash += lot.shares * px * (1 - cost)
            run.closed += 1
            run.holds.append(_days(lot.opened, bar.start))
        lots = keep
        if not lots:
            last_buy = None
        slots = cap - n_before  # a slot sold today frees tomorrow
        drop, gain = DROP, GAIN
        if rule == "scaled":
            s = sigma(bars, i) if refs is None else None
            if s is None:
                slots = 0
            else:
                drop, gain = SCALED[0] * s, SCALED[1] * s
        key = session_key(bar)
        if random_days is not None:
            fills = [bar.o] if key in random_days and slots > 0 and bought_in.get(key, 0) == 0 else []
        else:
            if rule in ("once", "scaled") and bought_in.get(key, 0):
                slots = 0
            fills = entry_fills(rule, bar, ref, drop, last_buy, slots)
        for px in fills:
            shares = math.floor(TRANCHE / px)
            if shares < 1:
                continue
            cash -= shares * px * (1 + cost)
            lots.append(Lot(px, shares, bar.start, px * (1 + gain)))
            last_buy = px
            run.opened += 1
            bought_in[key] = bought_in.get(key, 0) + 1
        run.peak_lots = max(run.peak_lots, len(lots))
        equity = cash + sum(lot.shares * bar.c for lot in lots)
        eq_peak = max(eq_peak, equity)
        run.max_drawdown = max(run.max_drawdown, eq_peak - equity)
        last = bar
    if last is not None:
        run.pnl = cash + sum(lot.shares * last.c for lot in lots)
        run.open_at_end = len(lots)
        run.holds.extend(_days(lot.opened, last.start) for lot in lots)
    run.longest_days = max(run.holds, default=0)
    return run


def pct_year(run: Run, cap: int, per_year: float = SESSIONS_A_YEAR) -> float:
    years = run.sessions / per_year if run.sessions else 0
    return 100.0 * run.pnl / (cap * TRANCHE) / years if years else 0.0


def control(
    bars: list[Bar], start: str, end: str, rule: str, cap: int, entries: int, cost: float, seed: int
) -> float:
    """The random-entry control's mean % a year."""
    days = [b.start[:10] for i, b in enumerate(bars) if i > 0 and start <= b.start[:10] <= end]
    if rule == "scaled":
        days = [
            b.start[:10] for i, b in enumerate(bars) if i > SIGMA_SESSIONS and start <= b.start[:10] <= end
        ]
    if not days or entries == 0:
        return 0.0
    rng = random.Random(seed)
    out = []
    for _ in range(DRAWS):
        drawn = set(rng.sample(days, min(entries, len(days))))
        out.append(pct_year(simulate(bars, start, end, rule, cap, cost=cost, random_days=drawn), cap))
    return statistics.mean(out)


def measure(bars: list[Bar], start: str, end: str, arm: str, cost: float, seed: int) -> dict:
    rule, cap = arm_parts(arm)
    run = simulate(bars, start, end, rule, cap, cost=cost)
    own = pct_year(run, cap)
    ctrl = control(bars, start, end, rule, cap, run.opened, cost, seed)
    return {
        "pct_year": own,
        "control": ctrl,
        "edge": own - ctrl,
        "opened": run.opened,
        "closed": run.closed,
        "open_at_end": run.open_at_end,
        "peak_lots": run.peak_lots,
        "longest_days": run.longest_days,
        "max_drawdown_pct": 100.0 * run.max_drawdown / (cap * TRANCHE),
    }


def buy_and_hold(bars: list[Bar], start: str, end: str) -> float | None:
    span = [b for b in bars if start <= b.start[:10] <= end]
    if len(span) < 2:
        return None
    years = len(span) / SESSIONS_A_YEAR
    return 100.0 * ((span[-1].c / span[0].c) ** (1 / years) - 1)


# ---- names ----


def names_in(conn: sqlite3.Connection) -> tuple[list[str], dict]:
    from cherrypick.technicals import dollarvolume
    from cherrypick.technicals.skate import leveraged_funds

    dv_names, meta = dollarvolume.latest(DOLLAR_VOLUME_FILE)
    med = dollarvolume.medians(dv_names, 30)
    lev = sorted(s for s, f in leveraged_funds().items() if str(f.get("kind") or "leveraged") == "leveraged")
    first = dict(conn.execute("SELECT symbol, MIN(start) FROM bars WHERE interval = '1d' GROUP BY symbol"))
    picked = [s for s in lev if med.get(s, 0) >= MIN_DOLLAR_VOLUME and (first.get(s) or "9999") <= FIRST_BAR]
    return picked, {
        "dollar_volume_file": meta.get("file"),
        "liquid": sum(med.get(s, 0) >= MIN_DOLLAR_VOLUME for s in lev),
    }


def daily_bars(conn: sqlite3.Connection, symbol: str) -> list[Bar]:
    return merge_daily(load(conn, symbol, "broker", "1d"), load(conn, symbol, "yahoo", "1d"))


# ---- the verdict ----


def stat(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.mean(values) if values else 0.0
    sd = statistics.stdev(values) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else (math.inf if mean > 0 else 0.0)
    p = 1.0 - core_stats.t_cdf(t, n - 1) if n > 1 and math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return {
        "n": n,
        "mean": mean,
        "t": t,
        "p": p,
        "positive_share": sum(v > 0 for v in values) / n if n else 0.0,
    }


def judge(per_name: dict[str, dict]) -> dict:
    """per_name: {name: {arm: {window: measure}}} for the judged names."""
    out = {}
    for arm in ARMS:

        def edges(window: str, arm: str = arm) -> list[float]:
            return [v[arm][window]["edge"] for v in per_name.values() if window in v[arm]]

        def lots(window: str, arm: str = arm) -> int:
            return sum(v[arm][window]["closed"] for v in per_name.values() if window in v[arm])

        d, h = stat(edges("design")), stat(edges("holdout"))
        checks = {
            "mean_positive": d["mean"] > 0,
            "t_above_bar": d["t"] > T_BAR,
            "more_than_half_positive": d["positive_share"] > 0.5,
            "first_half_positive": stat(edges("half1"))["mean"] > 0,
            "second_half_positive": stat(edges("half2"))["mean"] > 0,
            "stress_positive": stat(edges("stress"))["mean"] > 0,
            "holdout_mean_positive": h["mean"] > 0,
            "holdout_more_than_half_positive": h["positive_share"] > 0.5,
        }
        out[arm] = {
            "design": d,
            "holdout": h,
            "checks": checks,
            "lots": {"design": lots("design"), "holdout": lots("holdout")},
            "enough": {
                "design": d["n"] >= MIN_NAMES and lots("design") >= MIN_LOTS,
                "holdout": h["n"] >= MIN_NAMES and lots("holdout") >= MIN_LOTS,
            },
        }
    holm = core_stats.holm({a: v["design"]["p"] for a, v in out.items()}, ALPHA)
    for arm, v in out.items():
        v["holm_ok"] = holm[arm]
        c = v["checks"]
        design_ok = all(c[k] for k in c if not k.startswith("holdout")) and v["holm_ok"]
        if not v["enough"]["design"]:
            v["verdict"] = "no verdict"
        elif not design_ok:
            v["verdict"] = "fail"
        elif not v["enough"]["holdout"]:
            v["verdict"] = "no verdict (holdout)"
        else:
            v["verdict"] = (
                "pass" if c["holdout_mean_positive"] and c["holdout_more_than_half_positive"] else "fail"
            )
    return out


def study(db: Path, only: list[str] | None) -> dict:
    from fetch_ladder_bars import COUNTERPART  # noqa: PLC0415 - the fetch's own table, beside this script

    conn = sqlite3.connect(db)
    names, meta = names_in(conn)
    if only:
        names = [n for n in names if n in only]
    windows = {"design": DESIGN, "half1": HALVES[0], "half2": HALVES[1], "holdout": HOLDOUT}
    per_name: dict[str, dict] = {}
    reported: dict[str, dict] = {}
    for k, name in enumerate(names):
        bars = daily_bars(conn, name)
        per_name[name] = {}
        for a, arm in enumerate(ARMS):
            seed = SEED * 1000 + k * 37 + a
            per_name[name][arm] = {w: measure(bars, s, e, arm, COST, seed) for w, (s, e) in windows.items()}
            per_name[name][arm]["stress"] = measure(bars, *DESIGN, arm, STRESS_COST, seed)
        one_x = COUNTERPART.get(name)
        cp = daily_bars(conn, one_x) if one_x else []
        reported[name] = {
            "buy_and_hold": {
                w: buy_and_hold(bars, s, e) for w, (s, e) in (("design", DESIGN), ("holdout", HOLDOUT))
            },
            "counterpart": one_x,
            "counterpart_arms": {
                arm: {
                    w: pct_year(simulate(cp, s, e, *arm_parts(arm)), arm_parts(arm)[1])
                    for w, (s, e) in (("design", DESIGN), ("holdout", HOLDOUT))
                }
                for arm in ARMS
            }
            if cp
            else None,
        }
        print(f"{name} done ({k + 1}/{len(names)})", file=sys.stderr, flush=True)
    return {
        "item": 26,
        "names": names,
        "universe": meta,
        "constants": {
            "drop": DROP,
            "gain": GAIN,
            "tranche": TRANCHE,
            "cost": COST,
            "stress_cost": STRESS_COST,
        },
        "verdict": judge(per_name),
        "per_name": per_name,
        "reported": reported,
    }


# ---- intraday check ----


def clip(bar: Bar) -> Bar:
    """An outside-hours Yahoo bar's high and low, kept within 3% of its open and close."""
    hi, lo = max(bar.o, bar.c) * (1 + CLIP), min(bar.o, bar.c) * (1 - CLIP)
    return Bar(bar.start, bar.o, min(bar.h, hi), max(bar.l, lo), bar.c)


def regular(start: str) -> bool:
    return "09:30" <= start[11:16] < "16:00"


def hourly_bars(conn: sqlite3.Connection, symbol: str) -> list[Bar]:
    broker = load(conn, symbol, "broker", "1h")
    yahoo = load(conn, symbol, "yahoo", "1h")
    first = min(broker, default="9999")
    out = [b for s, b in yahoo.items() if s < first]
    out = [b if regular(b.start) else clip(b) for b in out] + list(broker.values())
    return sorted((b for b in out if "08:00" <= b.start[11:16] < "20:00"), key=lambda b: b.start)


def reference_closes(bars: list[Bar], daily: list[Bar]) -> list[float]:
    """Each bar's latest 4:00 pm close: the previous session's before 16:00, the same session's after."""
    closes = {b.start: b.c for b in daily}
    days = sorted(closes)
    out = []
    for bar in bars:
        d = bar.start[:10]
        if bar.start[11:16] >= "16:00" and d in closes:
            out.append(closes[d])
        else:
            k = bisect.bisect_left(days, d)
            out.append(closes[days[k - 1]] if k else math.nan)
    return out


def fills_check(conn: sqlite3.Connection, daily: list[Bar]) -> dict:
    path = DIR / "user_fills.json"
    if not path.exists():
        return {"skipped": f"no {path}"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    bars = load(conn, doc["symbol"], "broker", "5m")
    closes = {b.start: b.c for b in daily}
    days = sorted(closes)
    open_buys: list[float] = []
    out = []
    for f in doc["fills"]:
        at = f["at"]
        minute = int(at[14:16]) // 5 * 5
        bar = bars.get(f"{at[:13]}:{minute:02d}")
        d = at[:10]
        ref_day = d if at[11:16] >= "16:00" else max((x for x in days if x < d), default=None)
        ref = closes.get(ref_day) if ref_day else None
        row = {
            **f,
            "bar": bar.__dict__ if bar else None,
            "in_range": bool(bar and bar.l <= f["price"] <= bar.h),
        }
        if f["side"] == "buy":
            row["reference_close"] = ref
            row["under_trigger"] = bool(ref and f["price"] <= ref * (1 - DROP))
            open_buys.append(f["price"])
        else:
            match = [b for b in open_buys if f["price"] >= round(b * (1 + GAIN), 2) - 0.01]
            row["matches_lot"] = match[0] if match else None
            if match:
                open_buys.remove(match[0])
        out.append(row)
    return {"fills": out}


def intraday(db: Path) -> dict:
    conn = sqlite3.connect(db)
    out = {}
    for name in INTRADAY:
        daily = daily_bars(conn, name)
        bars = hourly_bars(conn, name)
        if not bars:
            out[name] = {"skipped": "no hourly bars"}
            continue
        refs = reference_closes(bars, daily)
        start, end = bars[1].start[:10], bars[-1].start[:10]
        sessions = len({b.start[:10] for b in bars})
        hourly = simulate(bars, start, end, "once", 3, refs=refs)
        hourly.sessions = sessions
        day = simulate(daily, start, end, "once", 3)
        out[name] = {
            "span": [start, end],
            "hourly": {"pct_year": pct_year(hourly, 3), "opened": hourly.opened, "closed": hourly.closed},
            "daily": {"pct_year": pct_year(day, 3), "opened": day.opened, "closed": day.closed},
        }
    out["user_fills"] = fills_check(conn, daily_bars(conn, "SOXL"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("study", "intraday"))
    ap.add_argument("--db", type=Path, default=DIR / "bars.db")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--names", default=None, help="comma-separated subset (a trial run, not a verdict)")
    args = ap.parse_args(argv)
    if args.step == "study":
        result = study(args.db, args.names.split(",") if args.names else None)
        path = args.json or DIR / "results.json"
        summary = {a: v["verdict"] for a, v in result["verdict"].items()}
    else:
        result = intraday(args.db)
        path = args.json or DIR / "intraday.json"
        summary = {k: v for k, v in result.items() if k != "user_fills"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "summary": summary}, default=str))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
