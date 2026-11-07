"""Items 60-62 (docs/edge-hypotheses-plan.md): three afternoon reads of the SPX 0DTE chain.

    python scripts/afternoon_0dte_items60_62.py fomc     [--json PATH]                 # item 60
    python scripts/afternoon_0dte_items60_62.py sandwich [--start D --end D] [--json P] # item 61
    python scripts/afternoon_0dte_items60_62.py fade [--start D --end D] [--allow-holdout] [--json P]  # 62

Reads only `SPX-60s.db` (SPXW 0DTE at one minute, its spot trail and the SPX daily closes), Cboe's
VIX history (the local market-files copy) and, for item 60, the Fed calendar item 33 cached under
`data/public/item33/calendar.json`. Writes under the backtest data dir (results/research/item6x/),
never into the repo: the inputs are licensed.

- **Item 60, FOMC expected-move flies.** On each scheduled FOMC decision day, from the 13:50 quote:
  the expected move is the at-the-money straddle's mid; sell two iron flies, one centred on the
  listed strike nearest spot + move and one nearest spot - move, each with 20-point wings. Each fly
  is bought back on the first later minute whose working buy-back is 90% of its credit or less (10%
  decayed), else settles at the close. Scored per session (both flies), against the same rule on
  every other session as a placebo.
- **Item 61, the 13:45 sandwich, read again (exploratory).** Item 9's rule: from the 13:45 quote, a
  condor with shorts at the strikes nearest spot -/+ 5 and 5-point wings, held to the close; two
  arms, unfiltered and with item 9's two filters (prior VIX close below 24, credit at least half the
  width). Exploratory only: item 9 failed its design, so this read cannot overturn it.
- **Item 62, the fade to the day's midpoint.** At 13:45 the day's midpoint is the mean of the spot
  trail's high and low from 09:30. Signal +1 when spot is below it, -1 above; the outcome is the
  signal times (close - 13:45 spot), in index points. A pass needs a positive mean with t above 2 in
  both design halves, then the holdout.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.core import fees, home, robustness

ET = ZoneInfo("America/New_York")
SIDE = {0: "P", 1: "C"}
OFFICIAL = "thetadata:index_eod"
STORE_START = "2022-05-16"
DESIGN_END = "2024-12-31"
HOLDOUT_END = "2026-11-06"
T_BAR = 2.0
PASS_STRESS = 1.5

FOMC_AT, FOMC_WING, FOMC_DECAY, FOMC_MIN = "13:50", 20, 0.10, 25
SANDWICH_AT, SANDWICH_OFFSET, SANDWICH_WING, SANDWICH_VIX, SANDWICH_RR = "13:45", 5.0, 5, 24.0, 1.0
FADE_AT, FADE_FROM, FADE_MIN = "13:45", "09:30", 100


# --------------------------------------------------------------------------- inputs
def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%H:%M")


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


def trading_days(db: sqlite3.Connection, start: str, end: str) -> list[str]:
    return [
        d
        for (d,) in db.execute(
            "SELECT trade_date FROM vendor_sessions WHERE symbol = 'SPX' AND status = 'complete' "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start, end),
        )
    ]


def spx_closes(db: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    return {
        d: (c, s)
        for d, c, s in db.execute("SELECT trade_date, close, source FROM daily_closes WHERE symbol='SPX'")
    }


def samples(db: sqlite3.Connection, day: str) -> dict[str, int]:
    """{hh:mm: sample id} for the session's same-day expiry."""
    return {
        hhmm(ts): sid
        for sid, ts, exp in db.execute(
            "SELECT id, ts, expiration FROM gex_quote_samples WHERE symbol = 'SPX' AND trade_date = ?", (day,)
        )
        if exp == day
    }


def spot_trail(db: sqlite3.Connection, day: str) -> dict[str, float]:
    return {
        hhmm(ts): s
        for ts, s in db.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol = 'SPX' AND trade_date = ?", (day,)
        )
    }


def chain(db: sqlite3.Connection, sample_id: int) -> dict:
    return {
        (k, SIDE[side]): {"bid": b, "ask": a}
        for side, k, b, a in db.execute(
            "SELECT side, strike, bid, ask FROM gex_quotes WHERE sample_id = ?", (sample_id,)
        )
    }


# --------------------------------------------------------------------------- pricing
def two_sided(q: dict | None) -> bool:
    return (
        bool(q)
        and q["bid"] is not None
        and q["ask"] is not None
        and q["ask"] >= q["bid"] >= 0
        and q["ask"] > 0
    )


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2


def structure(ch: dict, legs: list, fill: str, *, close: bool = False) -> float | None:
    """Per share: the credit a sale brings, or (close=True) the debit a buy-back costs. `fill` is
    mid, working (halfway to natural), q25 (a quarter of the way) or natural. None when a leg does not
    price."""
    qs = [(ch.get(k), n) for k, n in legs]
    if not all(two_sided(q) for q, _ in qs):
        return None
    if close:
        qs = [(q, -n) for q, n in qs]
    m = -sum(n * mid(q) for q, n in qs)
    nat = -sum(n * (q["bid"] if n < 0 else q["ask"]) for q, n in qs)
    share = {"mid": 0.0, "q25": 0.25, "working": 0.5, "natural": 1.0}[fill]
    p = m + share * (nat - m)
    return round(-p, 4) if close else round(max(0.0, p), 4)


def settle(legs: list, s: float) -> tuple[float, int]:
    owed, itm = 0.0, 0
    for (k, r), n in legs:
        v = max(0.0, s - k) if r == "C" else max(0.0, k - s)
        owed -= n * v
        itm += v > 0
    return round(owed, 4), itm


def iron_fly(centre: float, wing: int) -> list:
    return [((centre, "C"), -1), ((centre, "P"), -1), ((centre + wing, "C"), 1), ((centre - wing, "P"), 1)]


def nearest(strikes, x: float) -> float | None:
    s = sorted(strikes, key=lambda k: (abs(k - x), k))
    return s[0] if s else None


def open_fee() -> float:
    return fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4)


def close_fee() -> float:
    return fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4)


# --------------------------------------------------------------------------- statistics
def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def summary(rows: list[dict], key: str = "net") -> dict:
    xs = [r[key] for r in rows]
    t = t_stat(xs)
    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 2) if xs else None,
        "t": None if t is None else round(t, 3),
        "win_rate": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None,
    }


def judge(
    rows: list[dict], start: str, end: str, *, key: str = "net", min_n: int, costs: bool = True
) -> dict:
    """The declared bar: mean above zero, one-sided t above 2, both time-halves positive and, where
    there are costs, positive at 1.5x costs. Fewer than `min_n` observations is no verdict."""
    out = summary(rows, key)
    cut = midpoint(start, end)
    halves = {h: [r[key] for r in rows if (r["day"] < cut) == (h == "first")] for h in ("first", "second")}
    out["halves"] = {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()}
    out["halves_n"] = {h: len(v) for h, v in halves.items()}
    if costs and rows:
        out["cost_stress"] = robustness.cost_stress(
            [r[key] for r in rows], [r["cost"] for r in rows], pass_stress=PASS_STRESS
        )
    if out["n"] < min_n:
        out["verdict"], out["why"] = "no verdict", f"{out['n']} < {min_n}"
        return out
    checks = {
        "mean_positive": (out["mean"] or 0) > 0,
        "t_above_bar": (out["t"] or 0) > T_BAR,
        "both_halves_positive": all((x or 0) > 0 for x in out["halves"].values()),
    }
    if costs:
        checks["survives_1_5x_costs"] = bool(out["cost_stress"] and out["cost_stress"]["survives"])
    out["checks"] = checks
    out["verdict"] = "pass" if all(checks.values()) else "fail"
    return out


def by(rows: list[dict], fn, key: str = "net") -> dict:
    g: dict = defaultdict(list)
    for r in rows:
        g[fn(r)].append(r)
    return {str(k): summary(v, key) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


# --------------------------------------------------------------------------- item 60
def fomc_session(
    db, day: str, close: float, *, fill: str = "working", at: str = FOMC_AT, wing: int = FOMC_WING,
    decay: float = FOMC_DECAY,
) -> dict:  # fmt: skip
    ids = samples(db, day)
    if at not in ids:
        return {"skip": "no entry minute"}
    spots = spot_trail(db, day)
    ch0 = chain(db, ids[at])
    if at not in spots or not ch0:
        return {"skip": "no entry quote"}
    later = {t: ids[t] for t in sorted(ids) if t > at}
    return fomc_trade(
        ch0, spots[at], later, lambda sid: chain(db, sid), close, fill=fill, wing=wing, decay=decay
    )


def fomc_trade(
    ch0: dict, spot: float, later: dict, load, close: float, *, fill: str, wing: int, decay: float
) -> dict:
    """The two expected-move flies from one entry chain; `later` maps hh:mm to whatever `load` turns
    into that minute's chain."""
    both = [k for k, r in ch0 if r == "C" and (k, "P") in ch0]
    k0 = nearest(both, spot)
    if k0 is None or not (two_sided(ch0.get((k0, "C"))) and two_sided(ch0.get((k0, "P")))):
        return {"skip": "no straddle"}
    move = mid(ch0[(k0, "C")]) + mid(ch0[(k0, "P")])
    flies = {}
    for name, target in (("upper", spot + move), ("lower", spot - move)):
        centre = nearest(both, target)
        legs = iron_fly(centre, wing)
        got = structure(ch0, legs, fill)
        if got is None or got <= 0:
            return {"skip": f"{name} fly does not price"}
        flies[name] = {"centre": centre, "legs": legs, "credit": got, "open": True}
    for t, ref in later.items():
        if not any(f["open"] for f in flies.values()):
            break
        ch = load(ref)
        for f in flies.values():
            if not f["open"]:
                continue
            debit = structure(ch, f["legs"], fill, close=True)
            if debit is not None and debit <= (1 - decay) * f["credit"]:
                f.update(open=False, exit=t, debit=debit, cost=open_fee() + close_fee())
    net = cost = 0.0
    for f in flies.values():
        if f["open"]:
            owed, itm = settle(f["legs"], close)
            f.update(exit="16:00", debit=owed, cost=open_fee() + fees.ic_expire_fee(itm))
        f["gross"] = round((f["credit"] - f["debit"]) * 100, 2)
        f["net"] = round(f["gross"] - f["cost"], 2)
        net += f["net"]
        cost += f["cost"]
        del f["legs"]
    return {"move": round(move, 2), "flies": flies, "net": round(net, 2), "cost": round(cost, 2)}


def fomc_days() -> list[str]:
    cal = json.loads((home.data_dir() / "public" / "item33" / "calendar.json").read_text(encoding="utf-8"))
    return cal["fomc"]["scheduled"]


def cmd_fomc(db, args) -> dict:
    closes = spx_closes(db)
    days = trading_days(db, STORE_START, HOLDOUT_END)
    fed = set(fomc_days())
    rows, control, skips = [], [], defaultdict(int)
    by_fill: dict = {"mid": [], "natural": []}
    for day in days:
        if day not in closes:
            continue
        r = fomc_session(db, day, closes[day][0])
        if "skip" in r:
            skips[("fed " if day in fed else "other ") + r["skip"]] += 1
            continue
        (rows if day in fed else control).append({"day": day, **r})
        if day in fed:
            for f in by_fill:
                x = fomc_session(db, day, closes[day][0], fill=f)
                if "skip" not in x:
                    by_fill[f].append({"day": day, **x})
    test = judge(rows, STORE_START, HOLDOUT_END, min_n=FOMC_MIN)
    ctrl = summary(control)
    out = {
        "item": 60,
        "window": [STORE_START, HOLDOUT_END],
        "verdict": test["verdict"],
        "test": test,
        "placebo_other_days": ctrl,
        "excess_over_placebo": round(test["mean"] - ctrl["mean"], 2) if rows and control else None,
        "other_fills": {f: summary(v) for f, v in by_fill.items()},
        "by_year": by(rows, lambda r: r["day"][:4]),
        "worst": [{"day": r["day"], "net": r["net"]} for r in sorted(rows, key=lambda r: r["net"])[:5]],
        "skips": dict(skips),
        "planned_mde_B12": robustness.planned_mde([r["net"] for r in rows], len(rows)) if rows else None,
    }
    if test["verdict"] == "pass":
        out["neighbours"] = {
            w: summary(
                [{"net": fomc_session(db, r["day"], closes[r["day"]][0], wing=w)["net"]} for r in rows]
            )
            for w in (10, 30)
        }
    return out


# --------------------------------------------------------------------------- item 61
def sandwich_legs(
    ch: dict, spot: float, offset: float = SANDWICH_OFFSET, wing: int = SANDWICH_WING
) -> list | None:
    puts = [k for k, r in ch if r == "P"]
    calls = [k for k, r in ch if r == "C"]
    sp, sc = nearest(puts, spot - offset), nearest(calls, spot + offset)
    if sp is None or sc is None or sp >= sc:
        return None
    return [((sc, "C"), -1), ((sp, "P"), -1), ((sc + wing, "C"), 1), ((sp - wing, "P"), 1)]


def sandwich_session(db, day: str, close: float, vix: float | None, *, fill: str) -> dict:
    ids, spots = samples(db, day), spot_trail(db, day)
    if SANDWICH_AT not in ids or SANDWICH_AT not in spots:
        return {"skip": "no entry minute"}
    ch = chain(db, ids[SANDWICH_AT])
    legs = sandwich_legs(ch, spots[SANDWICH_AT])
    if legs is None:
        return {"skip": "no strikes"}
    got = structure(ch, legs, fill)
    if got is None:
        return {"skip": "a leg not two-sided"}
    owed, itm = settle(legs, close)
    cost = open_fee() + fees.ic_expire_fee(itm)
    gross = round((got - owed) * 100, 2)
    rr = got / (SANDWICH_WING - got) if got < SANDWICH_WING else math.inf
    passes = vix is not None and vix < SANDWICH_VIX and rr >= SANDWICH_RR
    return {
        "credit": got,
        "gross": gross,
        "cost": round(cost, 2),
        "net": round(gross - cost, 2),
        "filtered_in": passes,
    }


def cmd_sandwich(db, args) -> dict:
    closes, vix = spx_closes(db), vix_closes(home.data_dir("market-files") / "cboe" / "VIX.csv")
    out = {"item": 61, "window": [args.start, args.end], "verdict": "exploratory", "fills": {}}
    for fill in ("working", "q25", "mid"):
        rows = []
        for day in trading_days(db, args.start, args.end):
            if day not in closes:
                continue
            r = sandwich_session(db, day, closes[day][0], prior_close(vix, day), fill=fill)
            if "skip" not in r:
                rows.append({"day": day, **r})
        filt = [r for r in rows if r["filtered_in"]]
        out["fills"][fill] = {
            "unfiltered": {**summary(rows), "by_year": by(rows, lambda r: r["day"][:4])},
            "filtered": {**summary(filt), "by_year": by(filt, lambda r: r["day"][:4])},
            "mean_cost": round(statistics.fmean(r["cost"] for r in rows), 2) if rows else None,
        }
    return out


# --------------------------------------------------------------------------- item 62
def fade_signal(
    trail: dict[str, float], at: str = FADE_AT, start: str = FADE_FROM
) -> tuple[int, float, float] | None:
    """(+1 below the day's midpoint, -1 above, 0 on it; spot at `at`; the midpoint), from the trail's
    minutes in [start, at]. None without the entry minute."""
    if at not in trail:
        return None
    seen = [v for t, v in trail.items() if start <= t <= at]
    if not seen:
        return None
    m = (max(seen) + min(seen)) / 2
    s = trail[at]
    return (1 if s < m else -1 if s > m else 0), s, m


def cmd_fade(db, args) -> dict:
    closes = spx_closes(db)
    rows = []
    for day in trading_days(db, args.start, args.end):
        if day not in closes:
            continue
        sig = fade_signal(spot_trail(db, day))
        if sig is None or sig[0] == 0:
            continue
        s, spot, m = sig
        rows.append(
            {
                "day": day,
                "signal": s,
                "points": round(s * (closes[day][0] - spot), 2),
                "distance": round(abs(spot - m), 2),
                "settle_source": closes[day][1],
            }
        )
    test = judge(rows, args.start, args.end, key="points", min_n=FADE_MIN, costs=False)
    out = {
        "item": 62,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "verdict": test["verdict"],
        "test": test,
        "by_signal": by(
            rows, lambda r: "below mid (buy)" if r["signal"] > 0 else "above mid (sell)", "points"
        ),
        "by_distance": by(
            rows, lambda r: "0-5" if r["distance"] < 5 else "5-15" if r["distance"] < 15 else "15+", "points"
        ),
        "by_year": by(rows, lambda r: r["day"][:4], "points"),
        "by_settlement_source": by(rows, lambda r: r["settle_source"], "points"),
    }
    return out


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("item", choices=("fomc", "sandwich", "fade"))
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--allow-holdout", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if args.item == "fade":
        args.start, args.end = args.start or STORE_START, args.end or DESIGN_END
        if args.end > DESIGN_END and not args.allow_holdout:
            msg = f"refused: --end {args.end} is past the design end {DESIGN_END}; pass --allow-holdout"
            print(msg, file=sys.stderr)
            return 2
    elif args.item == "sandwich":
        args.start, args.end = args.start or "2025-01-01", args.end or HOLDOUT_END
    cfg = _settings.load()
    db = sqlite3.connect(f"file:{paths.ensure_vendor_dir(cfg) / 'SPX-60s.db'}?mode=ro", uri=True)
    out = {"fomc": cmd_fomc, "sandwich": cmd_sandwich, "fade": cmd_fade}[args.item](db, args)
    out["licence"] = "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo"
    item = out["item"]
    window = f"{out['window'][0]}-{out['window'][1]}"
    target = (
        args.json
        or home.data_dir("backtest") / "results" / "research" / f"item{item}" / f"study-{window}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
