"""Item 27, getting the share ladder out of multi-year holds (docs/edge-hypotheses-plan.md): six
changes to item 26's `once-3`, each paired against it on the same names.

    python scripts/share_ladder_item27.py study [--json PATH] [--names A,B]

Reads item 26's `bars.db` and its judged names, bars, fill rules and costs
(`scripts/share_ladder_item26.py`), and writes under `~/.cherrypick/data/research/item27/`, never
into the repo.

**The arms** (`ARMS`), each `once-3` with one change unless stated:
- `avg-5`: the first lot as `once`; while a position is open, a further lot at each 7% under the
  last lot bought, up to 5; the whole position sells at 104% of its average cost.
- `decay`: a lot's target is 104% of cost to day 30, breakeven after costs from day 31, 95% of cost
  from day 121 (calendar days, read at each session).
- `avg-5-decay`: `avg-5` with the blended target on that schedule from the position's first lot.
- `time-120`: a lot older than 120 calendar days at a session's open sells at that open.
- `trend`: no new lot while the 1x counterpart's previous close is under its 200-session average
  (none either until it has 200 sessions). Names with a counterpart only.
- `confirm`: a session reaching the 7% trigger arms a signal; the first session from it, within 5,
  that closes up schedules a lot at the next session's open; otherwise it lapses. One signal at a
  time.

**Fills** are item 26's: a target or level is met at the open when the session opens through it,
else at the level when the range reaches it; nothing bought in a session sells in it, and a slot
freed in a session opens the next. An averaged position's exit is read on the lots held at the
session's start.

**Measured**, per name, arm and window: the longest hold and its 90th percentile (open lots aged to
the window's end), capital-days per budget, the share of sessions with the cap full, the drawdown
and % a year on the arm's own budget, lots opened and closed, losses realised.

**The verdict**, per arm, against `once-3` on the same names: in the design, the per-name log ratio
of the longest hold has a mean below zero with one-sided t under -2, Holm across the six, below
zero on more than half the names and in both halves; the mean return difference is -2 points or
better at both costs; the mean drawdown difference is zero or better; then in the holdout the mean
log ratio is below zero and the return condition holds. Under item 26's name and lot floors in a
window is no verdict.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cherrypick.core import stats as core_stats
from cherrypick.core.home import data_dir

HERE = Path(__file__).resolve().parent


def _item26():
    spec = importlib.util.spec_from_file_location("share_ladder_item26", HERE / "share_ladder_item26.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["share_ladder_item26"] = module  # its dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


sl = _item26()

BASE = "once-3"
ARMS = ("avg-5", "decay", "avg-5-decay", "time-120", "trend", "confirm")
SPEC = {
    "once-3": {"cap": 3},
    "avg-5": {"cap": 5, "avg": True},
    "decay": {"cap": 3, "decay": True},
    "avg-5-decay": {"cap": 5, "avg": True, "decay": True},
    "time-120": {"cap": 3, "time_stop": 120},
    "trend": {"cap": 3, "trend": True},
    "confirm": {"cap": 3, "confirm": True},
}
DECAY = ((30, None), (120, "breakeven"), (math.inf, 0.95))  # up to day N: 104% / breakeven / 95%
TREND_SESSIONS = 200
CONFIRM_SESSIONS = 5
RETURN_MARGIN = -2.0
DIR = data_dir("research") / "item27"


@dataclass
class Lot:
    price: float
    shares: int
    opened: str


@dataclass
class Result:
    pnl: float = 0.0
    sessions: int = 0
    opened: int = 0
    closed: int = 0
    open_at_end: int = 0
    cap_full: int = 0
    max_drawdown: float = 0.0
    capital_days: float = 0.0
    losses: int = 0
    loss_sum: float = 0.0
    holds: list = field(default_factory=list)


def target(price: float, age: int, *, decay: bool, cost: float) -> float:
    """A lot's (or a position's average cost's) target at `age` calendar days."""
    if not decay or age <= DECAY[0][0]:
        return price * (1 + sl.GAIN)
    if age <= DECAY[1][0]:
        return price * (1 + cost) / (1 - cost)
    return price * DECAY[2][1]


def trend_ok(under_dates: list[str], under_ok: list[bool | None], day: str) -> bool:
    """The counterpart's previous close at or over its 200-session average."""
    k = bisect.bisect_left(under_dates, day) - 1
    return k >= 0 and bool(under_ok[k])


def trend_flags(under: list) -> tuple[list[str], list[bool | None]]:
    dates = [b.start for b in under]
    closes = [b.c for b in under]
    flags: list[bool | None] = []
    run = 0.0
    for i, c in enumerate(closes):
        run += c
        if i >= TREND_SESSIONS:
            run -= closes[i - TREND_SESSIONS]
        flags.append(None if i < TREND_SESSIONS - 1 else c >= run / TREND_SESSIONS)
    return dates, flags


def simulate(bars: list, start: str, end: str, arm: str, *, cost: float = sl.COST, under=None) -> Result:
    spec = SPEC[arm]
    cap = spec["cap"]
    res = Result()
    lots: list[Lot] = []
    cash = 0.0
    eq_peak = 0.0
    signal_left = 0  # sessions a confirm signal still has
    buy_next_open = False
    keys = [b.start[:10] for b in bars]
    lo, hi = max(1, bisect.bisect_left(keys, start)), bisect.bisect_right(keys, end)
    under_dates, under_ok = trend_flags(under) if spec.get("trend") and under else ([], [])
    last = None

    def sell(lot: Lot, px: float, day: str) -> None:
        nonlocal cash
        cash += lot.shares * px * (1 - cost)
        net = lot.shares * (px * (1 - cost) - lot.price * (1 + cost))
        res.closed += 1
        res.holds.append(sl._days(lot.opened, day))
        res.capital_days += lot.shares * lot.price * sl._days(lot.opened, day)
        if net < 0:
            res.losses += 1
            res.loss_sum += net

    for i in range(lo, hi):
        bar, ref, day = bars[i], bars[i - 1].c, bars[i].start
        res.sessions += 1
        n_before = len(lots)
        # Exits, on the lots held at the session's start.
        if spec.get("avg") and lots:
            shares = sum(lot.shares for lot in lots)
            avg = sum(lot.shares * lot.price for lot in lots) / shares
            t = target(avg, sl._days(lots[0].opened, day), decay=spec.get("decay", False), cost=cost)
            px = bar.o if bar.o >= t else (t if bar.h >= t else None)
            if px is not None:
                for lot in lots:
                    sell(lot, px, day)
                lots = []
        else:
            keep = []
            for lot in lots:
                age = sl._days(lot.opened, day)
                if spec.get("time_stop") and age > spec["time_stop"]:
                    sell(lot, bar.o, day)
                    continue
                t = target(lot.price, age, decay=spec.get("decay", False), cost=cost)
                px = bar.o if bar.o >= t else (t if bar.h >= t else None)
                if px is None:
                    keep.append(lot)
                else:
                    sell(lot, px, day)
            lots = keep
        slots = 0 if (spec.get("avg") and n_before and not lots) else cap - n_before
        # Entries.
        fills: list[float] = []
        if spec.get("confirm"):
            if buy_next_open and slots > 0:
                fills = [bar.o]
            buy_next_open = False
            if signal_left == 0 and bar.l <= ref * (1 - sl.DROP):
                signal_left = CONFIRM_SESSIONS
            if signal_left:
                if bar.c > ref:
                    buy_next_open, signal_left = True, 0
                else:
                    signal_left -= 1
        elif spec.get("avg"):
            last_buy = lots[-1].price if lots else None
            fills = sl.entry_fills("chain", bar, ref, sl.DROP, last_buy, slots)
        else:
            if not spec.get("trend") or trend_ok(under_dates, under_ok, day[:10]):
                fills = sl.entry_fills("once", bar, ref, sl.DROP, None, slots)
        for px in fills:
            shares = math.floor(sl.TRANCHE / px)
            if shares < 1:
                continue
            cash -= shares * px * (1 + cost)
            lots.append(Lot(px, shares, day))
            res.opened += 1
        res.cap_full += len(lots) >= cap
        equity = cash + sum(lot.shares * bar.c for lot in lots)
        eq_peak = max(eq_peak, equity)
        res.max_drawdown = max(res.max_drawdown, eq_peak - equity)
        last = bar
    if last is not None:
        res.pnl = cash + sum(lot.shares * last.c for lot in lots)
        res.open_at_end = len(lots)
        for lot in lots:
            res.holds.append(sl._days(lot.opened, last.start))
            res.capital_days += lot.shares * lot.price * sl._days(lot.opened, last.start)
    return res


def describe(res: Result, cap: int, start: str, end: str) -> dict:
    budget = cap * sl.TRANCHE
    span_days = max(1, sl._days(start, end))
    holds = sorted(res.holds)
    p90 = holds[min(len(holds) - 1, int(0.9 * len(holds)))] if holds else 0
    years = res.sessions / sl.SESSIONS_A_YEAR if res.sessions else 0
    return {
        "pct_year": 100.0 * res.pnl / budget / years if years else 0.0,
        "longest_days": holds[-1] if holds else 0,
        "p90_days": p90,
        "capital_share": res.capital_days / (budget * span_days),
        "cap_full_share": res.cap_full / res.sessions if res.sessions else 0.0,
        "max_drawdown_pct": 100.0 * res.max_drawdown / budget,
        "opened": res.opened,
        "closed": res.closed,
        "open_at_end": res.open_at_end,
        "losses": res.losses,
        "loss_sum": res.loss_sum,
    }


def log_ratio(arm: dict, base: dict) -> float:
    return math.log(max(1, arm["longest_days"]) / max(1, base["longest_days"]))


def judge(per_name: dict[str, dict]) -> dict:
    """per_name: {name: {arm: {window: describe()}}}; BASE present for every name."""
    out = {}
    for arm in ARMS:
        names = [n for n, v in per_name.items() if arm in v]

        def series(window: str, f, arm: str = arm, names: list = names) -> list[float]:
            return [f(per_name[n][arm][window], per_name[n][BASE][window]) for n in names]

        def lots(window: str, who: str, names: list = names) -> int:
            return sum(per_name[n][who][window]["closed"] for n in names)

        neg_lr = series("design", lambda a, b: -log_ratio(a, b))
        d = sl.stat(neg_lr)
        ret = {
            w: series(w, lambda a, b: a["pct_year"] - b["pct_year"]) for w in ("design", "stress", "holdout")
        }
        dd = series("design", lambda a, b: a["max_drawdown_pct"] - b["max_drawdown_pct"])
        h_lr = series("holdout", lambda a, b: log_ratio(a, b))
        mean = lambda xs: statistics.mean(xs) if xs else 0.0  # noqa: E731
        checks = {
            "hold_shorter": d["mean"] > 0,
            "t_above_bar": d["t"] > sl.T_BAR,
            "shorter_on_more_than_half": d["positive_share"] > 0.5,
            "first_half_shorter": mean(series("half1", log_ratio)) < 0,
            "second_half_shorter": mean(series("half2", log_ratio)) < 0,
            "return_within_margin": mean(ret["design"]) >= RETURN_MARGIN,
            "stress_return_within_margin": mean(ret["stress"]) >= RETURN_MARGIN,
            "drawdown_no_worse": mean(dd) <= 0,
            "holdout_hold_shorter": mean(h_lr) < 0,
            "holdout_return_within_margin": mean(ret["holdout"]) >= RETURN_MARGIN,
        }
        enough = {
            w: len(names) >= sl.MIN_NAMES and min(lots(w, arm), lots(w, BASE)) >= sl.MIN_LOTS
            for w in ("design", "holdout")
        }
        out[arm] = {
            "names": len(names),
            "design_hold": d,
            "mean_return_difference": {w: mean(v) for w, v in ret.items()},
            "mean_drawdown_difference": mean(dd),
            "holdout_mean_log_ratio": mean(h_lr),
            "checks": checks,
            "enough": enough,
        }
    holm = core_stats.holm({a: v["design_hold"]["p"] for a, v in out.items()}, sl.ALPHA)
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
            holdout_ok = c["holdout_hold_shorter"] and c["holdout_return_within_margin"]
            v["verdict"] = "pass" if holdout_ok else "fail"
    return out


def study(db: Path, only: list[str] | None) -> dict:
    import sqlite3

    sys.path.insert(0, str(HERE))
    from fetch_ladder_bars import COUNTERPART

    conn = sqlite3.connect(db)
    names, meta = sl.names_in(conn)
    if only:
        names = [n for n in names if n in only]
    windows = {"design": sl.DESIGN, "half1": sl.HALVES[0], "half2": sl.HALVES[1], "holdout": sl.HOLDOUT}
    per_name: dict[str, dict] = {}
    for k, name in enumerate(names):
        bars = sl.daily_bars(conn, name)
        one_x = COUNTERPART.get(name)
        under = sl.daily_bars(conn, one_x) if one_x else None
        per_name[name] = {}
        for arm in (BASE, *ARMS):
            if arm == "trend" and not under:
                continue
            cap = SPEC[arm]["cap"]
            per_name[name][arm] = {
                w: describe(simulate(bars, s, e, arm, under=under), cap, s, e)
                for w, (s, e) in windows.items()
            }
            per_name[name][arm]["stress"] = describe(
                simulate(bars, *sl.DESIGN, arm, cost=sl.STRESS_COST, under=under), cap, *sl.DESIGN
            )
        print(f"{name} done ({k + 1}/{len(names)})", file=sys.stderr, flush=True)
    return {
        "item": 27,
        "base": BASE,
        "names": names,
        "universe": meta,
        "verdict": judge(per_name),
        "per_name": per_name,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("study",))
    ap.add_argument("--db", type=Path, default=sl.DIR / "bars.db")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--names", default=None, help="comma-separated subset (a trial run, not a verdict)")
    args = ap.parse_args(argv)
    result = study(args.db, args.names.split(",") if args.names else None)
    path = args.json or DIR / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps({"written": str(path), "summary": {a: v["verdict"] for a, v in result["verdict"].items()}})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
