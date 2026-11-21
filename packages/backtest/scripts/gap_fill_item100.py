"""Item 100: does an open price gap fill more often than any level the same distance away?

The declaration is research/edge-hypotheses-plan.md section 100; this script runs it. The data is the
technicals history store's daily bars (public, personal use); results stay under the backtest results
directory, never the repo.

A forecast study, not a trade. For each index fund, each session that opens with a true gap (its low
above the prior high, or its high below the prior low) is an event: does price trade back to the
prior session's edge within the horizon? Its baseline is the share of non-gap sessions of the same
fund, in the same part, on which price reached a level the same number of volatility units away in
the same direction within the same horizon. The statistic is the events' mean excess over that
baseline. The pass rule is `FORECAST_RULE`.

- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (`{"excess_fill": word}`).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import sqlite3
import sys
from datetime import date
from pathlib import Path

from cherrypick.core import progress as core_progress

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run.
FORECAST_RULE = {
    "kind": "forecast",
    "symbols": ["QQQ", "SPY", "IWM", "DIA"],
    "from": "2011-01-03",
    "through": "2026-11-20",
    "min_gap": 0.001,
    "horizon": 20,
    "vol_window": 20,
    "max_hole_days": 5,
    "statistic": "excess_fill",
    "t_bar": 2.0,
    "min_effect": 0.05,
    "min_n": 400,
    "holdout_min_n": 150,
    "bootstrap": {"block": 20, "resamples": 10000, "seed": 100},
    "splits": ["2022-01-01"],
    "halves": "2016-07-01",
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the study reads the daily bars the technicals store already holds.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
DIRECTIONS = ("up", "down")


# --------------------------------------------------------------------------- pure pieces


def gap_of(prev: dict, bar: dict, min_gap: float) -> str | None:
    """`up` when the bar's low clears the prior high by `min_gap`, `down` when its high is under the
    prior low by `min_gap`, else None."""
    if bar["l"] > prev["h"] * (1 + min_gap):
        return "up"
    if bar["h"] < prev["l"] * (1 - min_gap):
        return "down"
    return None


def stdev(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def build_rows(
    symbol: str,
    bars: list[dict],
    rule: dict,
    *,
    horizon: int | None = None,
    min_gap: float | None = None,
) -> tuple[list[dict], dict]:
    """One row per session with a full volatility history and a full horizon after it, and the counts
    dropped. `bars` are one fund's sessions in date order (`d`, `h`, `l`, `c`). A row carries its gap
    (None, `up` or `down`), the furthest excursion below and above its close within the horizon in
    volatility units (`m_below`, `m_above`) and, for a gap, its distance to the prior edge (`z`) and
    whether price traded back to that edge (`fill`). A calendar hole longer than `max_hole_days`
    anywhere in a row's history or horizon drops it."""
    h = horizon or rule["horizon"]
    g = rule["min_gap"] if min_gap is None else min_gap
    w = rule["vol_window"]
    hole = rule["max_hole_days"]
    days = [date.fromisoformat(b["d"]) for b in bars]
    holes = [0] * len(bars)  # holes[i]: count of holes between sessions 1..i (prefix sum)
    for i in range(1, len(bars)):
        holes[i] = holes[i - 1] + (1 if (days[i] - days[i - 1]).days > hole else 0)
    rets = [None] + [math.log(bars[i]["c"] / bars[i - 1]["c"]) for i in range(1, len(bars))]
    rows: list[dict] = []
    dropped = {"history": 0, "horizon": 0, "hole": 0}
    for i in range(len(bars)):
        if i < w:
            dropped["history"] += 1
            continue
        if i + h >= len(bars):
            dropped["horizon"] += 1
            continue
        if holes[i + h] - holes[i - w] > 0:
            dropped["hole"] += 1
            continue
        sigma = stdev(rets[i - w + 1 : i + 1])
        if sigma <= 0:
            dropped["history"] += 1
            continue
        c = bars[i]["c"]
        ahead = bars[i + 1 : i + h + 1]
        lo = min(b["l"] for b in ahead)
        hi = max(b["h"] for b in ahead)
        row = {
            "symbol": symbol,
            "date": bars[i]["d"],
            "gap": gap_of(bars[i - 1], bars[i], g),
            "m_below": math.log(c / lo) / sigma,
            "m_above": math.log(hi / c) / sigma,
            "ret_sigma": rets[i] / sigma,
        }
        if row["gap"] == "up":
            level = bars[i - 1]["h"]
            row.update(z=math.log(c / level) / sigma, fill=lo <= level, size=bars[i]["l"] / level - 1)
        elif row["gap"] == "down":
            level = bars[i - 1]["l"]
            row.update(z=math.log(level / c) / sigma, fill=hi >= level, size=1 - bars[i]["h"] / level)
        rows.append(row)
    return rows, dropped


def baselines(controls: list[dict]) -> dict[tuple[str, str], list[float]]:
    """Each fund's control excursions, sorted, by the direction of the event they are a baseline for:
    an up gap's edge lies below the close (`m_below`), a down gap's above it (`m_above`)."""
    out: dict[tuple[str, str], list[float]] = {}
    for r in controls:
        out.setdefault((r["symbol"], "up"), []).append(r["m_below"])
        out.setdefault((r["symbol"], "down"), []).append(r["m_above"])
    for v in out.values():
        v.sort()
    return out


def reach_share(sorted_m: list[float], z: float) -> float | None:
    """The share of control excursions at least `z`: how often a level `z` units away was reached."""
    if not sorted_m:
        return None
    return (len(sorted_m) - bisect.bisect_left(sorted_m, z)) / len(sorted_m)


def excess(rows: list[dict], control=None) -> dict:
    """The events' mean fill less each one's baseline (its fund and direction, at its own `z`), from
    the non-gap rows given (or those `control` keeps). An event with no baseline is left out."""
    events = [r for r in rows if r["gap"]]
    controls = [r for r in rows if not r["gap"] and (control is None or control(r))]
    base = baselines(controls)
    diffs, fills, ps = [], [], []
    for e in events:
        p = reach_share(base.get((e["symbol"], e["gap"]), []), e["z"])
        if p is None:
            continue
        diffs.append((1.0 if e["fill"] else 0.0) - p)
        fills.append(1.0 if e["fill"] else 0.0)
        ps.append(p)
    n = len(diffs)
    return {
        "n": n,
        "controls": len(controls),
        "excess": sum(diffs) / n if n else None,
        "fill_rate": sum(fills) / n if n else None,
        "baseline": sum(ps) / n if n else None,
    }


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis and Romano): runs of mean length `block`, wrapping."""
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se(rows: list[dict], *, block: int, resamples: int, seed: int, rep=None) -> float | None:
    """The bootstrap standard error of the excess. Sessions are drawn in blocks of dates, and every
    fund's row on a drawn date comes with it, events and controls alike, so the baseline's own error
    and the funds' shared days are both in the draw."""
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    rng = random.Random(seed)
    draws = []
    for k in range(resamples):
        sample = [r for i in stationary_indices(len(dates), block, rng) for r in by_date[dates[i]]]
        x = excess(sample)["excess"]
        if x is not None:
            draws.append(x)
        if rep is not None:
            rep.advance("bootstrap", current=k + 1)
    if len(draws) < 2:
        return None
    return stdev(draws)


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None, rep=None) -> dict:
    """The verdict block: the design part (excess, bootstrap t, both halves), then the holdout, read
    only after a design pass."""
    split = rule["splits"][0]
    stat = rule["statistic"]
    design = [r for r in rows if r["date"] < split]
    holdout = [r for r in rows if r["date"] >= split]
    d = excess(design)
    out: dict = {"design": dict(d), "holdout": {"n": sum(1 for r in holdout if r["gap"])}}
    if d["n"] < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: NO_VERDICT}}
    bs = rule["bootstrap"]
    n_draws = resamples or bs["resamples"]
    if rep is not None:
        rep.phase("bootstrap", total=n_draws, unit="draws")
    se = boot_se(design, block=bs["block"], resamples=n_draws, seed=bs["seed"], rep=rep)
    t = d["excess"] / se if se else None
    halves = {
        "first": excess([r for r in design if r["date"] < rule["halves"]])["excess"],
        "second": excess([r for r in design if r["date"] >= rule["halves"]])["excess"],
    }
    checks = {
        "effect": d["excess"] >= rule["min_effect"],
        "t": t is not None and t >= rule["t_bar"],
        "halves": all(v is not None and v > 0 for v in halves.values()),
    }
    out["design"].update({"se": se, "t": t, "halves": halves, "checks": checks})
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: FAIL}}
    out["design"]["verdict"] = PASS
    h = excess(holdout)
    word = PASS if h["excess"] is not None and h["excess"] > 0 else FAIL
    provisional = word == PASS and h["n"] < rule["holdout_min_n"]
    out["holdout"].update({**h, "verdict": word, "provisional": provisional})
    return {**out, "verdict": {stat: word}, "provisional": provisional}


def all_rows(bars_by_symbol: dict[str, list[dict]], rule: dict, **kw) -> tuple[list[dict], dict]:
    rows, dropped = [], {}
    for s in rule["symbols"]:
        r, dr = build_rows(s, bars_by_symbol.get(s, []), rule, **kw)
        rows += r
        dropped[s] = dr
    return rows, dropped


def reported(bars_by_symbol: dict, rows: list[dict], rule: dict, ex_div: set[tuple[str, str]]) -> dict:
    """The declared cuts, reported and never judged (the design part, like the statistic)."""
    split = rule["splits"][0]

    def design_of(rs: list[dict]) -> list[dict]:
        return [r for r in rs if r["date"] < split]

    design = design_of(rows)
    out: dict = {}
    for h in (5, 60):
        out[f"horizon_{h}"] = excess(design_of(all_rows(bars_by_symbol, rule, horizon=h)[0]))
    for g in (0.0025, 0.005):
        out[f"min_gap_{g}"] = excess(design_of(all_rows(bars_by_symbol, rule, min_gap=g)[0]))
    for s in rule["symbols"]:
        out[f"symbol_{s}"] = excess([r for r in design if r["symbol"] == s])
    for side in DIRECTIONS:
        out[f"direction_{side}"] = excess([r for r in design if r["gap"] in (None, side)])
    out["same_move_control"] = {
        side: excess(
            [r for r in design if r["gap"] in (None, side)],
            control=(lambda r, sd=side: r["ret_sigma"] >= 1.0 if sd == "up" else r["ret_sigma"] <= -1.0),
        )
        for side in DIRECTIONS
    }
    out["without_ex_dividend"] = excess(
        [r for r in design if not (r["gap"] and (r["symbol"], r["date"]) in ex_div)]
    )
    by_year: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["gap"]:
            by_year.setdefault(r["date"][:4], dict.fromkeys(DIRECTIONS, 0))[r["gap"]] += 1
    out["events_by_year"] = by_year
    return out


def fingerprint(bars_by_symbol: dict[str, list[dict]]) -> dict:
    """What the run read, as counts and a hash of the dates: never a price."""
    lines = "\n".join(f"{s}:{b['d']}" for s, bs in sorted(bars_by_symbol.items()) for b in bs)
    return {
        "sessions": {s: len(bs) for s, bs in bars_by_symbol.items()},
        "first": min((bs[0]["d"] for bs in bars_by_symbol.values() if bs), default=None),
        "last": max((bs[-1]["d"] for bs in bars_by_symbol.values() if bs), default=None),
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- the store (read-only)


def store_path() -> Path:
    from cherrypick.technicals import paths

    return paths.history_db()


def load_bars(conn: sqlite3.Connection, symbol: str, rule: dict) -> list[dict]:
    return [
        {"d": d, "h": float(h), "l": float(lo), "c": float(c)}
        for d, h, lo, c in conn.execute(
            "SELECT date, high, low, close FROM bars WHERE symbol = ? AND date >= ? AND date <= ? "
            "AND high > 0 AND low > 0 AND close > 0 ORDER BY date",
            (symbol, rule["from"], rule["through"]),
        )
    ]


def load_ex_dividend(conn: sqlite3.Connection, symbols: list[str]) -> set[tuple[str, str]]:
    marks = ",".join("?" * len(symbols))
    return {
        (s, d)
        for s, d in conn.execute(f"SELECT symbol, ex_date FROM dividends WHERE symbol IN ({marks})", symbols)
    }


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item100"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    conn = sqlite3.connect(f"file:{store_path()}?mode=ro", uri=True)
    with core_progress.Reporter("gap_fill_item100-study") as rep:
        try:
            rep.split("symbols", rule["symbols"], unit="funds")
            bars_by_symbol = {}
            for s in rule["symbols"]:
                bars_by_symbol[s] = load_bars(conn, s, rule)
                rep.advance("symbols", key=s, current=s)
            ex_div = load_ex_dividend(conn, rule["symbols"])
        finally:
            conn.close()
        rows, dropped = all_rows(bars_by_symbol, rule)
        block = judge(rows, rule, resamples=args.resamples, rep=rep)
        rep.at("bootstrap", "reported cuts")
        doc = {
            "data": "public daily bars (technicals history store), personal use: never commit or publish",
            "item": 100,
            "rule": rule,
            "window": {
                "from": rows[0]["date"] if rows else None,
                "through": rule["through"],
                "splits": rule["splits"],
            },
            "events": {
                s: {
                    side: sum(1 for r in rows if r["symbol"] == s and r["gap"] == side) for side in DIRECTIONS
                }
                for s in rule["symbols"]
            },
            "dropped": dropped,
            **block,
            "reported": reported(bars_by_symbol, rows, rule, ex_div),
            "inputs": fingerprint(bars_by_symbol),
        }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--resamples", type=int, default=None, help="bootstrap draws (default: the rule's)")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
