"""Item 58, the closing fly (docs/edge-hypotheses-plan.md): a 0DTE SPX iron fly or narrow condor sold
from the 15:55 quote and held to the 16:00 settlement, scored on two structure arms.

    python packages/backtest/scripts/close_fly_item58.py [--start 2022-05-16] [--end 2024-12-31]
    [--allow-holdout]
        [--account N] [--json PATH]

Reads only `SPX-60s.db` (the 0DTE SPXW chain at one minute, its spot trail and the SPX daily closes)
and Cboe's published VIX history (the local market-files copy). Writes its result under the backtest
data dir (results/research/item58/), never into the repo: the inputs are licensed.

**The rule, as declared.** On each session at 15:55 ET, from that minute's quote of the SPXW expiring
the same day:
- arm `fly`: short the call and the put at the listed strike nearest spot; long the call `WING`
  points above and the put `WING` points below.
- arm `condor`: short the highest put strike at or below spot - 1 and the lowest call strike at or
  above spot + 1; each long `WING` points further out.
One contract a structure, every leg two-sided or the session is skipped and counted. No stop, no
adjustment: the structure settles at that day's SPX close.

**The verdict**, per arm: the mean net per session above zero with a one-sided t above 2, Holm
across the two arms, both time-halves of the window positive, the mean still positive at 1.5x costs,
and the mean positive on the sessions settled at the official close (`thetadata:index_eod`). Fewer
than 100 entered sessions, or fewer than 50 official-close sessions, is no verdict. The holdout (past
2024-12-31) is refused without --allow-holdout, and the declaration reads it only after a design pass.
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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import fees, home, robustness
from cherrypick.core import stats as core_stats

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings

ET = ZoneInfo("America/New_York")
DESIGN = ("2022-05-16", "2024-12-31")
ENTRY_TIME = "15:55"
WING = 10
CONDOR_OFFSET = 1.0
OFFICIAL = "thetadata:index_eod"
#: VIX bands for the description (the prior session's close), never a filter.
VIX_BANDS = (15.0, 20.0, 25.0, math.inf)
MIN_SESSIONS = 100
MIN_OFFICIAL = 50
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
ARMS = ("fly", "condor")
FILLS = ("working", "mid", "mid_0.05", "natural")
WORST_N = 5
#: Store sides: 0 is the put, 1 the call.
SIDE = {0: "P", 1: "C"}


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


def vix_band(vix: float) -> str:
    low = 0.0
    for top in VIX_BANDS:
        if vix < top:
            return f"{low:g}-{top:g}" if top != math.inf else f"{low:g}+"
        low = top
    raise AssertionError("unreachable")


def spx_closes(db: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    rows = db.execute("SELECT trade_date, close, source FROM daily_closes WHERE symbol = 'SPX'")
    return {d: (c, s) for d, c, s in rows}


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%H:%M")


def load_entry(db: sqlite3.Connection, day: str, at: str) -> tuple[float, dict] | None:
    """(spot, {(strike, right): quote}) from the 0DTE sample at `at` ET, or None when the session has
    no such sample (an early close) or the sample is not the same-day expiry."""
    sample = None
    for sid, ts, exp in db.execute(
        "SELECT id, ts, expiration FROM gex_quote_samples WHERE symbol = 'SPX' AND trade_date = ?", (day,)
    ):
        if hhmm(ts) == at and exp == day:
            sample = (sid, ts)
            break
    if sample is None:
        return None
    spot = db.execute(
        "SELECT spot FROM gex_spot_history WHERE symbol = 'SPX' AND trade_date = ? AND ts = ?",
        (day, sample[1]),
    ).fetchone()
    if spot is None:
        return None
    chain = {
        (k, SIDE[side]): {"bid": bid, "ask": ask}
        for side, k, bid, ask in db.execute(
            "SELECT side, strike, bid, ask FROM gex_quotes WHERE sample_id = ?", (sample[0],)
        )
    }
    return spot[0], chain


# --------------------------------------------------------------------------- the structures
def fly_legs(chain: dict, spot: float, wing: int) -> list | None:
    """Short call and put at the listed strike nearest spot, wings `wing` out. None when no strike
    lists both sides."""
    centres = sorted(
        {k for k, r in chain if (k, "C") in chain and (k, "P") in chain}, key=lambda k: (abs(k - spot), k)
    )
    if not centres:
        return None
    k = centres[0]
    return [((k, "C"), -1), ((k, "P"), -1), ((k + wing, "C"), 1), ((k - wing, "P"), 1)]


def condor_legs(chain: dict, spot: float, wing: int, offset: float = CONDOR_OFFSET) -> list | None:
    """Short the highest put at or below spot - offset and the lowest call at or above spot + offset,
    wings `wing` further out. None when either short is not listed."""
    puts = [k for k, r in chain if r == "P" and k <= spot - offset]
    calls = [k for k, r in chain if r == "C" and k >= spot + offset]
    if not puts or not calls:
        return None
    p, c = max(puts), min(calls)
    return [((c, "C"), -1), ((p, "P"), -1), ((c + wing, "C"), 1), ((p - wing, "P"), 1)]


BUILD = {"fly": fly_legs, "condor": condor_legs}


# --------------------------------------------------------------------------- pricing
def two_sided(q: dict | None) -> bool:
    return (
        bool(q)
        and q["bid"] is not None
        and q["ask"] is not None
        and q["ask"] >= q["bid"] >= 0
        and q["ask"] > 0
    )


def credit(chain: dict, legs: list, fill: str) -> float | None:
    """Per share, what the structure brings when sold to open. `working` is halfway between the mid
    and the natural fill (the shorts' bids less the longs' asks); `mid_0.05` concedes $0.05 a
    structure; `natural` is the natural fill itself. None when any leg is not two-sided."""
    qs = [(chain.get(k), n) for k, n in legs]
    if not all(two_sided(q) for q, _ in qs):
        return None
    mid = -sum(n * (q["bid"] + q["ask"]) / 2 for q, n in qs)
    natural = -sum(n * (q["bid"] if n < 0 else q["ask"]) for q, n in qs)
    p = {"mid": mid, "mid_0.05": mid - 0.05, "natural": natural, "working": (mid + natural) / 2}[fill]
    return round(max(0.0, p), 4)


def settle(legs: list, s: float) -> tuple[float, int]:
    """What the sold structure owes per share at settlement price `s`, and how many distinct legs
    finish in the money (each a $5 settlement event)."""
    owed, itm = 0.0, 0
    for (k, r), n in legs:
        v = max(0.0, s - k) if r == "C" else max(0.0, k - s)
        owed -= n * v
        itm += v > 0
    return round(owed, 4), itm


def result(legs: list, got: float, settle_at: float) -> dict:
    """Whole-position dollars at one structure: gross, costs, net. Opening pays the suite's
    tastytrade schedule; settlement pays $5 per in-the-money symbol; nothing is closed."""
    owed, itm = settle(legs, settle_at)
    gross = round((got - owed) * 100, 2)
    cost = round(fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4) + fees.ic_expire_fee(itm), 2)
    return {
        "credit": got,
        "owed": owed,
        "itm": itm,
        "gross": gross,
        "cost": cost,
        "net": round(gross - cost, 2),
    }


def run_session(
    entry: tuple[float, dict] | None, settle_at: float, *, arm: str, wing: int, fill: str
) -> dict:
    if entry is None:
        return {"skip": "no entry quote"}
    spot, chain = entry
    legs = BUILD[arm](chain, spot, wing)
    if legs is None:
        return {"skip": "no short strike"}
    got = credit(chain, legs, fill)
    if got is None:
        return {"skip": "a leg not two-sided"}
    return {
        "spot": spot,
        "shorts": sorted({k for (k, _), n in legs if n < 0}),
        **result(legs, got, settle_at),
    }


# --------------------------------------------------------------------------- calendar labels
def third_friday(day: date) -> date:
    first = day.replace(day=1)
    return first.replace(day=1 + (4 - first.weekday()) % 7 + 14)


def labels(day: str, sessions_in_month: set[str]) -> dict:
    d = date.fromisoformat(day)
    return {
        "weekday": d.strftime("%a"),
        "monthly_expiry": d == third_friday(d),
        "month_end": day == max(sessions_in_month),
    }


# --------------------------------------------------------------------------- sessions
def sessions(
    db: sqlite3.Connection, days: list[str], vix: dict, closes: dict, *, at: str, wing: int, fill: str
) -> dict[str, tuple[list[dict], Counter]]:
    by_month: dict = defaultdict(set)
    for d in days:
        by_month[d[:7]].add(d)
    out = {a: ([], Counter()) for a in ARMS}
    for day in days:
        v = prior_close(vix, day)
        close = closes.get(day)
        if v is None or close is None:
            for a in ARMS:
                out[a][1]["no VIX or settlement close"] += 1
            continue
        entry = load_entry(db, day, at)
        for a in ARMS:
            r = run_session(entry, close[0], arm=a, wing=wing, fill=fill)
            if "skip" in r:
                out[a][1][r["skip"]] += 1
                continue
            out[a][0].append(
                {
                    "day": day,
                    "vix": v,
                    "settle_source": close[1],
                    "close_vs_spot": round(close[0] - r["spot"], 2),
                    **labels(day, by_month[day[:7]]),
                    **r,
                }
            )
    return out


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


def score(rows: list[dict], start: str, end: str) -> dict:
    nets = [r["net"] for r in rows]
    costs = [r["cost"] for r in rows]
    mid = midpoint(start, end)
    halves = {h: [r["net"] for r in rows if (r["day"] < mid) == (h == "first")] for h in ("first", "second")}
    official = [r["net"] for r in rows if r["settle_source"] == OFFICIAL]
    t = t_stat(nets)
    out = {
        "n": len(nets),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "mean_gross": round(statistics.fmean(r["gross"] for r in rows), 2) if rows else None,
        "mean_cost": round(statistics.fmean(costs), 2) if costs else None,
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "official_close": {
            "n": len(official),
            "mean": round(statistics.fmean(official), 2) if official else None,
        },
        "cost_stress": robustness.cost_stress(nets, costs, pass_stress=PASS_STRESS) if nets else None,
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
        "mean_credit": round(statistics.fmean(r["credit"] for r in rows) * 100, 2) if rows else None,
    }
    if len(nets) < MIN_SESSIONS:
        out["verdict"], out["why"] = "no verdict", f"{len(nets)} entered sessions < {MIN_SESSIONS}"
    elif len(official) < MIN_OFFICIAL:
        out["verdict"], out["why"] = "no verdict", f"{len(official)} official-close sessions < {MIN_OFFICIAL}"
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
            "survives_1_5x_costs": bool(v["cost_stress"] and v["cost_stress"]["survives"]),
            "official_close_positive": (v["official_close"]["mean"] or 0) > 0,
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def describe(rows: list[dict]) -> dict:
    """Description only, never the verdict: the declared calendar slices, VIX band, settlement
    source, the close's direction from entry spot (drift), the worst sessions, ITM counts."""

    def m(xs):
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    def split(key):
        g: dict = defaultdict(list)
        for r in rows:
            g[key(r)].append(r["net"])
        return {str(k): m(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}

    return {
        "by_weekday": split(lambda r: r["weekday"]),
        "friday_vs_other": split(lambda r: "Fri" if r["weekday"] == "Fri" else "other"),
        "monthly_expiry": split(lambda r: r["monthly_expiry"]),
        "month_end": split(lambda r: r["month_end"]),
        "by_vix_band": split(lambda r: vix_band(r["vix"])),
        "by_settlement_source": split(lambda r: r["settle_source"]),
        "close_direction": split(
            lambda r: "up" if r["close_vs_spot"] > 0 else ("down" if r["close_vs_spot"] < 0 else "flat")
        ),
        "by_year": split(lambda r: r["day"][:4]),
        "itm_legs": dict(Counter(r["itm"] for r in rows)),
        "worst_sessions": [
            {"day": r["day"], "net": r["net"]} for r in sorted(rows, key=lambda r: r["net"])[:WORST_N]
        ],
    }


# --------------------------------------------------------------------------- main
def trading_days(db: sqlite3.Connection, start: str, end: str) -> list[str]:
    return [
        d
        for (d,) in db.execute(
            "SELECT trade_date FROM vendor_sessions WHERE symbol = 'SPX' AND status = 'complete' "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
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
    ap.add_argument("--json", type=Path, help="default: results/research/item58/study-<window>.json")
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
    db = sqlite3.connect(f"file:{vendor / 'SPX-60s.db'}?mode=ro", uri=True)
    vix = vix_closes(home.data_dir("market-files") / "cboe" / "VIX.csv")
    closes = spx_closes(db)
    days = trading_days(db, args.start, args.end)
    declared = {"at": ENTRY_TIME, "wing": WING}

    by_fill = {f: sessions(db, days, vix, closes, fill=f, **declared) for f in FILLS}
    working = by_fill["working"]
    tests = {a: score(working[a][0], args.start, args.end) for a in ARMS}
    judge(tests)
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 58,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "sessions_in_window": len(days),
        "verdict": {a: tests[a].get("verdict") for a in ARMS},
        "tests": tests,
        "skips": {a: dict(working[a][1]) for a in ARMS},
        "describe": {a: describe(working[a][0]) for a in ARMS},
        "other_fills": {
            f: {a: score(by_fill[f][a][0], args.start, args.end) for a in ARMS}
            for f in FILLS
            if f != "working"
        },
        "planned_mde_B12": {
            a: robustness.planned_mde([r["net"] for r in working[a][0]], len(working[a][0])) for a in ARMS
        },
    }
    passing = [a for a in ARMS if tests[a].get("verdict") == "pass"]
    if passing:
        b13 = {}
        for a in passing:

            def mean_at(a=a, **over):
                rows = sessions(db, days, vix, closes, fill="working", **{**declared, **over})[a][0]
                return round(statistics.fmean(r["net"] for r in rows), 2) if rows else None

            wings = {w: mean_at(wing=w) for w in (5, WING, 15, 20)}
            rows = working[a][0]
            rep = robustness.report(
                [r["net"] for r in rows],
                [r["cost"] for r in rows],
                means_by_setting=wings,
                declared=WING,
                sessions=len(rows),
                account=args.account,
                account_share=0.10 if args.account else None,
                pass_stress=PASS_STRESS,
            )
            b13[a] = {
                **rep,
                "entry_time_means": {t: mean_at(at=t) for t in ("15:50", "15:53", ENTRY_TIME, "15:57")},
            }
        out["robustness_B13"] = b13

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item58" / f"study-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"sessions-{args.start}-{args.end}.json").write_text(
        json.dumps({a: working[a][0] for a in ARMS}, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
