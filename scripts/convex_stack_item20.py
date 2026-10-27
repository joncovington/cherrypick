"""Item 20, the Convex Stack (docs/edge-hypotheses-plan.md): one with-trend out-of-the-money SPX fly
bought on Mondays and Thursdays at about 5 DTE, scored on three exit arms.

    python scripts/convex_stack_item20.py [--start 2022-05-16] [--end 2024-12-31] [--allow-holdout]
        [--account N] [--json PATH]

Reads only `SPX-convex-stack.db` (scripts/fetch_spx_convex_stack.py), `SPX-60s.db`'s spot history and
daily closes, and Cboe's published VIX history (the local market-files copy). Writes its result under
the backtest data dir (results/research/item20/), never into the repo: the inputs are licensed.
Self-contained on purpose (it shares item 19's pricing shape but not its code), so a later change to
another study cannot move this one's declared rules.

**The rule, as declared.** On entry day D at 15:45, with X its expiry and `w` set by D-1's VIX close
(25 below 17, 30 to 24.5, 40 to 32, 50 above), the side is a call fly when a 14-bar sine-weighted
average of SPX on regular-session four-hour bars (the second still open, closing at the 15:45 spot) is
above its value one bar earlier, else a put fly. The body is the listed strike nearest spot on that
side whose fly costs at most 10% of `w` at the working price, every leg two-sided. Arm `hold` settles
at X's close; arm `target_3x` sells at the first five-minute mark from 09:35 on a later session worth
at least 3x the debit; arm `day2_1545` sells at the second session's 15:45 mark. The opposite side's
fly is priced and scored beside it, never judged.

**The verdict**, per arm: the mean net per entry above zero with a one-sided t above 2, Holm across
the three arms, both time-halves of the window positive, still positive at 1.5x costs, and the
week-as-one-observation t above 2. Fewer than 100 entries is no verdict. The holdout (past
2024-12-31) is refused without --allow-holdout.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.core import calendar as _calendar
from cherrypick.core import fees, home, robustness
from cherrypick.core import stats as core_stats

DESIGN = ("2022-05-16", "2024-12-31")
HOLDOUT_END = "2026-10-23"
ENTRY_TIME = "15:45"
DEBIT_SHARE = 0.10
#: (VIX below, wing width). The last band has no ceiling.
WIDTH_BANDS = ((17.0, 25), (24.5, 30), (32.0, 40), (math.inf, 50))
#: The robustness ladder for "one band narrower and wider" (description only).
WIDTH_LADDER = (20, 25, 30, 40, 50, 70)
MA_LENGTH = 14
#: Regular-session four-hour bars, [opens, closes): the second is the one open at entry.
BARS = (("09:30", "13:30"), ("13:30", "16:01"))
TARGET_FROM = "09:35"
TARGET_MULTIPLE = 3.0
DAY2_SESSION = 2
DAY2_TIME = "15:45"
MIN_ENTRIES = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
ARMS = ("hold", "target_3x", "day2_1545")
FILLS = ("working", "mid", "mid_0.05")
ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- inputs
def vix_closes(path: Path) -> dict[str, float]:
    out = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m, d, y = row["DATE"].split("/")
            out[f"{y}-{m}-{d}"] = float(row["CLOSE"])
    return out


def prior_close(closes: dict[str, float], day: str) -> float | None:
    earlier = [d for d in closes if d < day]
    return closes[max(earlier)] if earlier else None


def width_for(vix: float) -> int:
    return next(w for top, w in WIDTH_BANDS if vix < top)


def spx_closes(db: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    rows = db.execute("SELECT trade_date, close, source FROM daily_closes WHERE symbol = 'SPX'")
    return {d: (c, s) for d, c, s in rows}


def spot_history(db: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    """{trade_date: [(HH:MM ET, spot), ...] in time order} from the 60 s spot history."""
    out: dict[str, list] = defaultdict(list)
    for day, ts, spot in db.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = 'SPX' ORDER BY ts"
    ):
        out[day].append((datetime.fromtimestamp(ts, ET).strftime("%H:%M"), spot))
    return dict(out)


def hhmm(ts: str) -> str:
    return ts[11:16]


def load_entry(db: sqlite3.Connection, day: str) -> tuple[str, dict, dict] | None:
    """(expiration, {hhmm: chain} for D's entry bars, {(session, hhmm): chain} for the path)."""
    rows = db.execute(
        "SELECT expiration, leg, ts, strike, right, bid, ask, underlying FROM quotes WHERE day = ?", (day,)
    )
    exp = None
    entry: dict = defaultdict(dict)
    path: dict = defaultdict(dict)
    for e, leg, ts, k, r, bid, ask, und in rows:
        exp = e
        q = {"bid": bid, "ask": ask, "underlying": und}
        if leg == "entry":
            entry[hhmm(ts)][(k, r)] = q
        else:
            path[(ts[:10], hhmm(ts))][(k, r)] = q
    if exp is None:
        return None
    return exp, entry, path


# --------------------------------------------------------------------------- direction
def swma(closes: list[float]) -> float:
    """Sine-weighted mean of the last MA_LENGTH closes: weight sin(i*pi/(n+1)) for i = 1..n."""
    xs = closes[-MA_LENGTH:]
    ws = [math.sin(i * math.pi / (MA_LENGTH + 1)) for i in range(1, MA_LENGTH + 1)]
    return sum(w * x for w, x in zip(ws, xs, strict=True)) / sum(ws)


def bar_closes(prints: list[tuple[str, float]], *, until: str | None = None) -> list[float]:
    """A session's four-hour bar closes: each bar's last print inside it. With `until`, prints after
    it are not yet known, so the bar open at `until` closes at the last print by then."""
    out = []
    for start, end in BARS:
        inside = [s for t, s in prints if start <= t < end and (until is None or t <= until)]
        if inside:
            out.append(inside[-1])
    return out


def direction(spots: dict[str, list], day: str, at: str) -> str | None:
    """'C' when the average is above its value one bar earlier, else 'P'; None with too few bars."""
    closes: list[float] = []
    for d in sorted(x for x in spots if x < day)[-(MA_LENGTH + 1) :]:
        closes += bar_closes(spots[d])
    if day not in spots:
        return None
    closes += bar_closes(spots[day], until=at)
    if len(closes) < MA_LENGTH + 1:
        return None
    return "C" if swma(closes) > swma(closes[:-1]) else "P"


# --------------------------------------------------------------------------- pricing
def two_sided(q: dict | None) -> bool:
    return (
        bool(q)
        and q["bid"] is not None
        and q["ask"] is not None
        and q["ask"] >= q["bid"] >= 0
        and q["ask"] > 0
    )


def fly_legs(body: float, w: int, right: str) -> list[tuple[tuple[float, str], int]]:
    return [((body - w, right), 1), ((body, right), -2), ((body + w, right), 1)]


def price(chain: dict, legs: list, *, buy: bool, fill: str) -> float | None:
    """Per share, what the fly costs to buy (`buy`) or brings to sell. `working` is halfway between
    the mid and the natural fill; `mid_0.05` is the mid conceded by $0.05 a fly. None when a leg is
    not two-sided (entry) or has no ask (exit: a wing bid at zero sells for zero)."""
    qs = [(chain.get(k), n) for k, n in legs]
    if buy and not all(two_sided(q) for q, _ in qs):
        return None
    if not all(q and q["ask"] is not None for q, _ in qs):
        return None
    sign = 1 if buy else -1
    mid = sum(sign * n * ((q["bid"] or 0) + q["ask"]) / 2 for q, n in qs)
    natural = sum(sign * n * (q["ask"] if sign * n > 0 else (q["bid"] or 0)) for q, n in qs)
    if fill == "mid":
        p = mid
    elif fill == "mid_0.05":
        p = mid + 0.05
    else:
        p = (mid + natural) / 2
    return round(p if buy else max(0.0, -p), 4)


def intrinsic(legs: list, s: float) -> tuple[float, int]:
    """The fly's settlement value per share, and how many distinct legs finish in the money."""
    value, itm = 0.0, 0
    for (k, r), n in legs:
        v = max(0.0, s - k) if r == "C" else max(0.0, k - s)
        value += n * v
        itm += v > 0
    return round(value, 4), itm


def pick_body(chain: dict, spot: float, w: int, right: str, share: float, fill: str) -> tuple | None:
    """The listed strike nearest spot on `right`'s side, walking away, whose fly costs at most share x w."""
    strikes = sorted({k for k, r in chain if r == right})
    bodies = [k for k in strikes if k > spot] if right == "C" else [k for k in reversed(strikes) if k < spot]
    for body in bodies:
        legs = fly_legs(body, w, right)
        debit = price(chain, legs, buy=True, fill=fill)
        if debit is not None and 0 < debit <= share * w:
            return body, legs, debit
    return None


# --------------------------------------------------------------------------- exits
def nth_session_after(day: str, n: int) -> str:
    d = date.fromisoformat(day)
    while n:
        d += timedelta(days=1)
        if _calendar.is_trading_day(d):
            n -= 1
    return d.isoformat()


def exit_target(path: dict, legs: list, debit: float, fill: str, multiple: float) -> tuple | None:
    for session, t in sorted(path):
        if t < TARGET_FROM:
            continue
        v = price(path[(session, t)], legs, buy=False, fill=fill)
        if v is not None and v >= multiple * debit:
            return v, f"{session} {t}"
    return None


def exit_day2(path: dict, legs: list, fill: str, day: str, expiration: str) -> tuple | None:
    session = nth_session_after(day, DAY2_SESSION)
    if session > expiration:
        return None
    for s, t in sorted((k for k in path if k[0] == session and k[1] <= DAY2_TIME), reverse=True):
        v = price(path[(s, t)], legs, buy=False, fill=fill)
        if v is not None:
            return v, f"{s} {t}"
    return None


def fly_result(legs: list, debit: float, sale: tuple | None, settle_at: float) -> dict:
    """Whole-position dollars at one contract: gross, fees, settlement, net."""
    open_fee = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4)
    if sale is not None:
        value, when = sale
        close_fee, settle_fee, how = (
            fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4),
            0.0,
            "closed",
        )
    else:
        value, itm = intrinsic(legs, settle_at)
        close_fee, settle_fee, how, when = 0.0, fees.ic_expire_fee(itm), "settled", "expiry"
    gross = round((value - debit) * 100, 2)
    cost = round(open_fee + close_fee + settle_fee, 2)
    return {"debit": debit, "exit": value, "how": how, "when": when, "gross": gross, "cost": cost,
            "net": round(gross - cost, 2)}  # fmt: skip


# --------------------------------------------------------------------------- entries
def run_entry(
    entry: dict, path: dict, w: int, settle_at: float, side: str, day: str, expiration: str,
    *, at: str, share: float, fill: str, multiple: float = TARGET_MULTIPLE,
) -> dict:  # fmt: skip
    chain = entry.get(at)
    if not chain:
        return {"skip": "no entry quote"}
    spot = next(iter(chain.values()))["underlying"]
    out: dict = {"spot": spot, "w": w, "side": side}
    for which, right in (("trend", side), ("opposite", "P" if side == "C" else "C")):
        pick = pick_body(chain, spot, w, right, share, fill)
        if pick is None:
            if which == "trend":
                return {"skip": "no affordable body"}
            continue
        body, legs, debit = pick
        out[which] = {
            "body": body,
            "debit": debit,
            "arms": {
                "hold": fly_result(legs, debit, None, settle_at),
                "target_3x": fly_result(
                    legs, debit, exit_target(path, legs, debit, fill, multiple), settle_at
                ),
                "day2_1545": fly_result(legs, debit, exit_day2(path, legs, fill, day, expiration), settle_at),
            },
        }
    for arm in ARMS:
        out[arm] = {k: out["trend"]["arms"][arm][k] for k in ("net", "cost")}
    out["debit"] = out["trend"]["debit"]
    return out


def entries(
    cs: sqlite3.Connection, days: list[str], vix: dict, closes: dict, spots: dict, **kw
) -> tuple[list[dict], Counter]:
    rows, skips = [], Counter()
    width_shift = kw.pop("width_shift", 0)
    for day in days:
        loaded = load_entry(cs, day)
        if loaded is None:
            skips["not fetched"] += 1
            continue
        exp, entry, path = loaded
        v = prior_close(vix, day)
        settle = closes.get(exp)
        if v is None or settle is None:
            skips["no VIX or settlement close"] += 1
            continue
        side = direction(spots, day, kw["at"])
        if side is None:
            skips["too few four-hour bars"] += 1
            continue
        w = width_for(v)
        if width_shift:
            i = WIDTH_LADDER.index(w) + width_shift
            w = WIDTH_LADDER[max(0, min(i, len(WIDTH_LADDER) - 1))]
        r = run_entry(entry, path, w, settle[0], side, day, exp, **kw)
        if "skip" in r:
            skips[r["skip"]] += 1
            continue
        rows.append({"day": day, "expiration": exp, "vix": v, "settle_source": settle[1], **r})
    return rows, skips


# --------------------------------------------------------------------------- statistics
def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def p_one(t: float | None, n: int) -> float:
    return 1.0 if t is None or n < 2 else 1.0 - core_stats.t_cdf(t, n - 1)


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def week_nets(rows: list[dict], arm: str) -> list[float]:
    """Each ISO week's summed net: a Monday entry and the Thursday after it as one observation."""
    weeks: dict = defaultdict(float)
    for r in rows:
        weeks[date.fromisoformat(r["day"]).isocalendar()[:2]] += r[arm]["net"]
    return list(weeks.values())


def score(rows: list[dict], arm: str, start: str, end: str) -> dict:
    nets = [r[arm]["net"] for r in rows]
    costs = [r[arm]["cost"] for r in rows]
    mid = midpoint(start, end)
    halves = {
        h: [r[arm]["net"] for r in rows if (r["day"] < mid) == (h == "first")] for h in ("first", "second")
    }
    t = t_stat(nets)
    weekly = week_nets(rows, arm)
    tw = t_stat(weekly)
    out = {
        "n": len(nets),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "weeks": len(weekly),
        "week_t": None if tw is None else round(tw, 3),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "cost_stress": robustness.cost_stress(nets, costs, pass_stress=PASS_STRESS),
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
        "mean_debit": round(statistics.fmean(r["debit"] for r in rows) * 100, 2) if rows else None,
        "mean_net_over_debit": round(statistics.fmean(r[arm]["net"] / (r["debit"] * 100) for r in rows), 4)
        if rows
        else None,
    }
    if len(nets) < MIN_ENTRIES:
        out["verdict"] = "no verdict"
        out["why"] = f"{len(nets)} entries < {MIN_ENTRIES}"
    return out


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        v["holm_ok"] = holm[k]
        if v.get("verdict") == "no verdict":
            continue
        checks = {
            "mean_positive": (v["mean_net"] or 0) > 0,
            "t_above_bar": (v["t"] or 0) > T_BAR,
            "holm": v["holm_ok"],
            "both_halves_positive": all((x or 0) > 0 for x in v["halves"].values()),
            "survives_1_5x_costs": v["cost_stress"]["survives"],
            "week_t_above_bar": (v["week_t"] or 0) > T_BAR,
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def describe(rows: list[dict], arm: str) -> dict:
    """Description only, never the verdict: by side, weekday, width and settlement source, how flies
    end, and the trend side against the opposite side's fly at the same entries."""

    def m(xs):
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    by: dict = {"side": defaultdict(list), "weekday": defaultdict(list), "width": defaultdict(list),
                "settlement_source": defaultdict(list)}  # fmt: skip
    ends = Counter()
    paired = []
    for r in rows:
        net = r[arm]["net"]
        by["side"][r["side"]].append(net)
        by["weekday"][date.fromisoformat(r["day"]).strftime("%a")].append(net)
        by["width"][str(r["w"])].append(net)
        by["settlement_source"][r["settle_source"]].append(net)
        ends[r["trend"]["arms"][arm]["how"]] += 1
        if "opposite" in r:
            paired.append(net - r["opposite"]["arms"][arm]["net"])
    opposite = [r["opposite"]["arms"][arm]["net"] for r in rows if "opposite" in r]
    t = t_stat(paired)
    return {
        **{f"by_{k}": {kk: m(vv) for kk, vv in sorted(v.items())} for k, v in by.items()},
        "fly_endings": dict(ends),
        "opposite_side": m(opposite),
        "trend_minus_opposite": {**m(paired), "t": None if t is None else round(t, 3)},
    }


# --------------------------------------------------------------------------- main
def entry_days(cs: sqlite3.Connection, start: str, end: str) -> list[str]:
    return [
        d
        for (d,) in cs.execute(
            "SELECT DISTINCT day FROM fetched WHERE leg = 'entry' AND day BETWEEN ? AND ? ORDER BY day",
            (start, end),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default=DESIGN[0])
    ap.add_argument("--end", default=DESIGN[1])
    ap.add_argument(
        "--allow-holdout", action="store_true", help="an --end past the design window reads the holdout"
    )
    ap.add_argument(
        "--account", type=float, help="B13's size line, passed at run time (never stored in the repo)"
    )
    ap.add_argument("--json", type=Path, help="default: results/research/item20/<window>.json")
    args = ap.parse_args(argv)
    if args.end > DESIGN[1] and not args.allow_holdout:
        print(
            f"refused: --end {args.end} is past the design window's end {DESIGN[1]}, into the holdout; "
            "pass --allow-holdout",
            file=sys.stderr,
        )
        return 2
    cfg = _settings.load()
    vendor = paths.ensure_vendor_dir(cfg)
    cs = sqlite3.connect(f"file:{vendor / 'SPX-convex-stack.db'}?mode=ro", uri=True)
    spx = sqlite3.connect(f"file:{vendor / 'SPX-60s.db'}?mode=ro", uri=True)
    vix = vix_closes(home.data_dir("market-files") / "cboe" / "VIX.csv")
    closes = spx_closes(spx)
    spots = spot_history(spx)
    days = entry_days(cs, args.start, args.end)

    def run(**kw):
        return entries(cs, days, vix, closes, spots, **{"at": ENTRY_TIME, "share": DEBIT_SHARE, **kw})

    results: dict = {}
    for fill in FILLS:
        rows, skips = run(fill=fill)
        tests = {arm: score(rows, arm, args.start, args.end) for arm in ARMS}
        if fill == "working":
            judge(tests)
            working_rows = rows
        results[fill] = {
            "tests": tests,
            "skips": dict(skips),
            "describe": {a: describe(rows, a) for a in ARMS},
        }

    verdict = results["working"]["tests"]
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 20,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "entry_days_in_window": len(days),
        "verdict": {a: verdict[a].get("verdict") for a in ARMS},
        "tests": verdict,
        "skips": results["working"]["skips"],
        "describe": results["working"]["describe"],
        "other_fills": {f: results[f]["tests"] for f in FILLS if f != "working"},
        "planned_mde_B12": {
            a: robustness.planned_mde([r[a]["net"] for r in working_rows], len(working_rows)) for a in ARMS
        },
    }
    passing = [a for a in ARMS if verdict[a].get("verdict") == "pass"]
    if passing:

        def mean(rows, a):
            return round(statistics.fmean(r[a]["net"] for r in rows), 2) if rows else None

        b13 = {}
        for a in passing:
            means = {s: mean(run(fill="working", share=s)[0], a) for s in (0.05, 0.075, DEBIT_SHARE)}
            rep = robustness.report(
                [r[a]["net"] for r in working_rows],
                [r[a]["cost"] for r in working_rows],
                means_by_setting=means,
                declared=DEBIT_SHARE,
                sessions=len(working_rows),
                account=args.account,
                account_share=0.10 if args.account else None,
                pass_stress=PASS_STRESS,
            )
            b13[a] = {
                **rep,
                "entry_time_means": {
                    t: mean(run(fill="working", at=t)[0], a) for t in ("15:40", ENTRY_TIME, "15:50")
                },
                "width_shift_means": {s: mean(run(fill="working", width_shift=s)[0], a) for s in (-1, 0, 1)},
            }
            if a == "target_3x":
                b13[a]["multiple_means"] = {
                    k: mean(run(fill="working", multiple=k)[0], a) for k in (2.0, TARGET_MULTIPLE, 4.0)
                }
        out["robustness_B13"] = b13

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item20" / f"study-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"entries-{args.start}-{args.end}.json").write_text(
        json.dumps(working_rows, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
