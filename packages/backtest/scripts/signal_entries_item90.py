"""Item 90: two published signal entries on SPX 0DTE, an opening-range breakout put spread and an ADX condor.

The declaration is research/edge-hypotheses-plan.md section 90; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Both arms are automations (`ARMS`) replayed by `cherrypick.automations.driver`, the engine paper runs,
over the vendor's SPX one-minute store, so every entry, gate, fill, fee and settlement is paper's
arithmetic. Two passes over one window:

1. **The arms** over every session. Each entry minute is kept.
2. **The placebo**: every session of a part gets `CONTROL_RULE["draws"]` copies of each arm's structure
   (its legs, gates, size and exit), each entered at a fixed minute drawn from that arm's own entry
   minutes in the same part (seed 90). The session is any session, never only the ones the signal
   fired on, so the control knows nothing of the day the signal saw.

Two literals decide it, and a pass needs both: `RULE` (the kit's three bars on each arm's trades, cut at
the split) and `CONTROL_RULE` (each arm's mean net against its placebo's per-session mean).

- `study`: writes `{results}/results.json` with `verdict` (`{arm: word}`).
- `study --smoke`: the first `SMOKE_SESSIONS` sessions with two draws, to `smoke.json`, never a verdict.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's three bars, on each arm's trades, over one run cut at the split.
RULE = {
    "t_bar": 2.0,
    "min_n": 150,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10000, "seed": 90},
    "delta": True,
    "splits": ["2025-07-01"],
}

#: Against chance, fixed by the declaration before any run.
CONTROL_RULE = {
    "draws": 20,
    "seed": 90,
    "t_bar": 2.0,
    "halves": "2024-01-01",
    "split": "2025-07-01",
    "holm_alpha": 0.05,
    "min_n": 150,
    "holdout_min_n": 60,
}

#: The window: the whole store, the vendor's SPX one-minute file.
WINDOW = {"symbol": "SPX", "store": "SPX-60s.db", "from": "2022-05-16", "through": "2026-11-13"}

#: The source's own window, reported only (its published results cover it).
SOURCE_WINDOW = {"from": "2023-01-01", "through": "2026-11-06"}

SMOKE_SESSIONS = 10
SMOKE_DRAWS = 2

_EVERY_DAY = [0, 1, 2, 3, 4]
_SAME_DAY = {"kind": "dte", "days": 0, "tolerance": 0}

#: The two arms as the source states them, in our engine's words. What the source leaves unsaid is
#: fixed here and listed in the declaration (the exit, the filters and the event skips).
ARMS = {
    "orb-put-spread": {
        "name": "orb-put-spread",
        "symbol": "SPX",
        "enabled": True,
        "expiry": _SAME_DAY,
        # The engine's opening-range entry, not core's `opening_range` signal: the store's first sample is
        # 09:31, and core refuses a range whose bars do not start at 09:30. On a 60 s store a sample is
        # the close of the one-minute bar ending then, so the first sample above the range is the
        # source's "a completed one-minute bar closes above the range high", looked for once a day.
        "entry": {"kind": "orb", "minutes": 60, "direction": "above", "latest": "13:00", "single_shot": True},
        "weekdays": _EVERY_DAY,
        "legs": [
            {
                "name": "short_put",
                "right": "P",
                "side": -1,
                "strike": {"rule": "delta", "target": 0.35, "match": "nearest"},
            },
            {
                "name": "long_put",
                "right": "P",
                "side": 1,
                "strike": {"rule": "from_leg", "leg": "short_put", "points": -10, "match": "exact"},
            },
        ],
        "gates": [],
        "size": {"contracts": 1},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
    "adx-condor": {
        "name": "adx-condor",
        "symbol": "SPX",
        "enabled": True,
        "expiry": _SAME_DAY,
        "entry": {
            "kind": "trigger",
            "bar_minutes": 5,
            "trigger": {
                "signal": {
                    "kind": "cross",
                    "a": {"name": "adx", "n": 14},
                    "b": 20,
                    "direction": "below",
                    "seed": False,
                    "first_of_day": True,
                    "latest": "15:30",
                },
                "window": {"open": "09:30", "close": "15:30"},
                "within": 1,
            },
        },
        "weekdays": _EVERY_DAY,
        "legs": [
            {
                "name": "short_put",
                "right": "P",
                "side": -1,
                "strike": {"rule": "spot", "points": -5, "match": "nearest"},
            },
            {
                "name": "long_put",
                "right": "P",
                "side": 1,
                "strike": {"rule": "from_leg", "leg": "short_put", "points": -5, "match": "exact"},
            },
            {
                "name": "short_call",
                "right": "C",
                "side": -1,
                "strike": {"rule": "spot", "points": 5, "match": "nearest"},
            },
            {
                "name": "long_call",
                "right": "C",
                "side": 1,
                "strike": {"rule": "from_leg", "leg": "short_call", "points": 5, "match": "exact"},
            },
        ],
        "gates": [{"gate": "reward_risk", "min": 100.0}, {"gate": "bid_ask", "max": 1.0}],
        "size": {"contracts": 1},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the store already holds the window. The smoke runs first, so a crash or an arm with no trades shows
#: in minutes.
CHAIN = (
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10
PLACEBO = "~p"


# --------------------------------------------------------------------------- pure pieces


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def placebo_arm(arm: dict, draw: int, minute: int) -> dict:
    """`arm`'s structure entered at a fixed `minute`: its legs, gates, size, limits and exit kept, its
    trigger replaced by a one-minute time entry. Named `<arm>~pNN`, the same name every session."""
    p = copy.deepcopy(arm)
    p["name"] = f"{arm['name']}{PLACEBO}{draw:02d}"
    p["entry"] = {"kind": "time", "at": hhmm(minute), "window": 1}
    return p


def base_arm(name: str) -> str:
    return name.split(PLACEBO, 1)[0]


def part_of(day: str, split: str) -> str:
    return "design" if day < split else "holdout"


def draw_minutes(
    sessions: list[str], minutes_by_part: dict[str, list[int]], split: str, draws: int, rng: random.Random
) -> dict[str, list[int]]:
    """Each session's `draws` entry minutes, drawn with replacement from its part's arm entry minutes.
    A part where the arm never entered draws nothing."""
    out: dict[str, list[int]] = {}
    for s in sessions:
        pool = minutes_by_part.get(part_of(s, split)) or []
        out[s] = [rng.choice(pool) for _ in range(draws)] if pool else []
    return out


def max_loss_dollars(trade: dict) -> float:
    """A defined-risk credit structure's buying power: the widest wing less the credit, whole position."""
    puts = sorted(g["strike"] for g in trade["legs"] if g["right"] == "P")
    calls = sorted(g["strike"] for g in trade["legs"] if g["right"] == "C")
    width = max(
        (puts[-1] - puts[0]) if len(puts) == 2 else 0.0, (calls[-1] - calls[0]) if len(calls) == 2 else 0.0
    )
    return round((width - trade["entry_mid"]) * 100.0 * trade["quantity"], 2)


def position_delta(trade: dict, deltas: dict[tuple[float, str], float]) -> float | None:
    """The structure's delta in shares at entry, from each leg's quoted delta; None when one is missing."""
    total = 0.0
    for g in trade["legs"]:
        d = deltas.get((float(g["strike"]), g["right"]))
        if d is None:
            return None
        total += g["side"] * d * 100.0 * trade["quantity"]
    return total


def welch(a: list[float], b: list[float]) -> dict:
    """Mean of `a` less mean of `b`, with Welch's t and its one-sided p (normal tail)."""
    if len(a) < 2 or len(b) < 2:
        return {"n": len(a), "n_control": len(b), "diff": None, "t": None, "p": None}
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    t = (ma - mb) / se if se > 0 else None
    p = 0.5 * math.erfc(t / math.sqrt(2)) if t is not None else None
    return {"n": len(a), "n_control": len(b), "mean": ma, "control_mean": mb, "diff": ma - mb, "t": t, "p": p}


def judge_control(arm_rows: list[dict], placebo_rows: list[dict], rule: dict) -> dict:
    """The arm's trades against its placebo's per-session means: design diff > 0 at t >= t_bar and > 0 in
    each design half; then the holdout diff > 0, read only after a design pass."""
    split, halves = rule["split"], rule["halves"]

    def nets(rows, lo=None, hi=None):
        return [r["net"] for r in rows if (lo is None or r["date"] >= lo) and (hi is None or r["date"] < hi)]

    design = welch(nets(arm_rows, hi=split), nets(placebo_rows, hi=split))
    first = welch(nets(arm_rows, hi=halves), nets(placebo_rows, hi=halves))
    second = welch(nets(arm_rows, lo=halves, hi=split), nets(placebo_rows, lo=halves, hi=split))
    out = {"design": design, "halves": {"first": first, "second": second}}
    if design["n"] < rule["min_n"]:
        out["verdict"] = NO_VERDICT
        out["why"] = f"{design['n']} design trades, under {rule['min_n']}"
        out["holdout"] = {"verdict": NOT_READ}
        return out
    ok = (
        design["t"] is not None
        and design["diff"] > 0
        and design["t"] >= rule["t_bar"]
        and all(h["diff"] is not None and h["diff"] > 0 for h in (first, second))
    )
    if not ok:
        out["verdict"] = FAIL
        out["holdout"] = {"verdict": NOT_READ}
        return out
    hold = welch(nets(arm_rows, lo=split), nets(placebo_rows, lo=split))
    if hold["n"] < rule["holdout_min_n"]:
        hold["verdict"] = NO_VERDICT
    else:
        hold["verdict"] = PASS if hold["diff"] is not None and hold["diff"] > 0 else FAIL
    out["holdout"] = hold
    out["verdict"] = hold["verdict"]
    return out


def holm(ps: dict[str, float | None], alpha: float) -> dict[str, bool]:
    """Holm's step-down over the arms' design p values: which arms survive at `alpha`."""
    ranked = sorted((p, a) for a, p in ps.items() if p is not None)
    keep, m = {a: False for a in ps}, len(ranked)
    for i, (p, a) in enumerate(ranked):
        if p > alpha / (m - i):
            break
        keep[a] = True
    return keep


def combine(bars: str, control: str, survives_holm: bool) -> str:
    """An arm passes only on both literals and Holm; a failure of either fails it."""
    if FAIL in (bars, control):
        return FAIL
    if bars == PASS and control == PASS:
        return PASS if survives_holm else FAIL
    return NO_VERDICT


def per_session_means(rows: list[dict]) -> list[dict]:
    """Placebo rows pooled per session: one row a session, its draws' mean net."""
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["date"], []).append(r["net"])
    return [{"date": d, "net": statistics.fmean(v)} for d, v in sorted(by.items())]


def describe(rows: list[dict], key: str = "net") -> dict:
    if not rows:
        return {"n": 0}
    v = [r[key] for r in rows]
    return {
        "n": len(v),
        "mean": round(statistics.fmean(v), 2),
        "sum": round(sum(v), 2),
        "win_rate": round(sum(1 for x in v if x > 0) / len(v), 4),
    }


def reported(rows: list[dict]) -> dict:
    """Context, never judged: every year, the source's own window, and the mid-fill net beside it."""
    years: dict[str, list[dict]] = {}
    for r in rows:
        years.setdefault(r["date"][:4], []).append(r)
    src = [r for r in rows if SOURCE_WINDOW["from"] <= r["date"] <= SOURCE_WINDOW["through"]]
    before = [r for r in rows if r["date"] < SOURCE_WINDOW["from"]]
    exits: dict[str, int] = {}
    for r in rows:
        exits[r["exit"]] = exits.get(r["exit"], 0) + 1
    return {
        "years": {y: describe(v) for y, v in sorted(years.items())},
        "source_window": {"verdict_fill": describe(src), "mid_fill": describe(src, "net_mid")},
        "before_source_window": describe(before),
        "exits": exits,
        "costs": {
            "fees": round(sum(r["fees"] for r in rows), 2),
            "slippage": round(sum(r["slippage"] for r in rows), 2),
        },
    }


# --------------------------------------------------------------------------- the replay


def _rate_reader():
    from cherrypick.core import treasury

    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item90"
    d.mkdir(parents=True, exist_ok=True)
    return d


def kit_row(trade: dict, h, rate, *, with_delta: bool) -> dict | None:
    """One trade as the kit reads it. None when its entry delta cannot be read (counted)."""
    from cherrypick.backtest.study import checks as ck

    bp = max_loss_dollars(trade)
    row = {
        "date": trade["session"],
        "net": trade["net"]["verdict"],
        "net_mid": trade["net"]["mid"],
        "bp": bp,
        "days": 1,
        "rate": rate(trade["session"]),
        "minute": trade["entry_minute"],
        "exit": trade["exit_kind"],
        "fees": trade["fees"],
        "slippage": trade["slippage"]["verdict"],
    }
    if with_delta:
        quotes = h.rows(trade["session"], trade["expiry"], trade["entry_minute"])
        deltas = {(float(q["strike"]), q["right"]): q["delta"] for q in quotes if q.get("delta") is not None}
        d = position_delta(trade, deltas)
        f0 = h.spot_at(trade["session"], trade["entry_minute"])
        f1 = h.settle(trade["session"])
        if d is None or f0 is None or f1 is None:
            return None
        row["financed"] = ck.financed_delta(d, f0, f1, bp, row["rate"], 1)
    return row


def fingerprint(h, sessions: list[str]) -> dict:
    """What the run read: the store's sessions and each one's stored minutes, hashed."""
    digest = hashlib.sha256()
    for s in sessions:
        digest.update(f"{s}:{len(h.minutes(s))};".encode())
    return {"store": WINDOW["store"], "sessions": len(sessions), "minutes_sha256": digest.hexdigest()}


def cmd_study(args) -> int:
    from cherrypick.automations import arms as arms_mod
    from cherrypick.automations import config as ens_config
    from cherrypick.automations import driver, history
    from cherrypick.core import calendar as cal
    from cherrypick.core import volindex

    from cherrypick.backtest import licensing, paths, settings

    s = settings.load(None)
    if not licensing.status(s)["active"]:
        raise SystemExit("the vendor's licence does not allow new work: item 90 reads its store")
    for a in ARMS.values():
        problems = arms_mod.validate(a)
        if problems:
            raise SystemExit(f"{a['name']}: {problems[0]}")
    store = paths.vendor_dir(s) / WINDOW["store"]
    h = history.StoreHistory(store, WINDOW["symbol"])
    sessions = [
        d
        for d in h.sessions
        if WINDOW["from"] <= d <= WINDOW["through"] and cal.is_trading_day(date.fromisoformat(d))
    ]
    draws = CONTROL_RULE["draws"]
    if args.smoke:
        sessions, draws = sessions[:SMOKE_SESSIONS], SMOKE_DRAWS
    cfg = ens_config.load_config()
    vix = volindex.closes("VIX")
    rate = _rate_reader()

    # Pass 1: the arms.
    book = driver.run(ARMS, h, sessions, costs=cfg, vix_daily=vix)
    trades = {n: [t for t in book.trades if t["arm"] == n] for n in ARMS}

    # Pass 2: the placebo, each session's draws from its part's entry minutes.
    rng = random.Random(CONTROL_RULE["seed"])
    split = CONTROL_RULE["split"]
    plan: dict[str, dict[str, list[int]]] = {}
    for n in ARMS:
        pool: dict[str, list[int]] = {}
        for t in trades[n]:
            pool.setdefault(part_of(t["session"], split), []).append(t["entry_minute"])
        plan[n] = draw_minutes(sessions, {k: sorted(v) for k, v in pool.items()}, split, draws, rng)
    pbook = driver.Book()
    for session in sessions:
        todays = {}
        for n, arm in ARMS.items():
            for i, minute in enumerate(plan[n][session]):
                p = placebo_arm(arm, i + 1, minute)
                todays[p["name"]] = p
        if todays:
            driver.run(todays, h, [session], costs=cfg, vix_daily=vix, book=pbook)

    rows, missing_delta = {}, {}
    for n in ARMS:
        rows[n], missing_delta[n] = [], 0
        for t in trades[n]:
            r = kit_row(t, h, rate, with_delta=True)
            if r is None:
                missing_delta[n] += 1
            else:
                rows[n].append(r)
    placebo = {n: [] for n in ARMS}
    for t in pbook.trades:
        placebo[base_arm(t["arm"])].append(kit_row(t, h, rate, with_delta=False))

    bars = kit_rule.evaluate(RULE, rows)
    control = {n: judge_control(rows[n], per_session_means(placebo[n]), CONTROL_RULE) for n in ARMS}
    survives = holm({n: control[n]["design"]["p"] for n in ARMS}, CONTROL_RULE["holm_alpha"])
    verdict = {n: combine(bars["verdict"][n], control[n]["verdict"], survives[n]) for n in ARMS}
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 90,
        "rule": RULE,
        "control_rule": CONTROL_RULE,
        "arms_spec": ARMS,
        "window": {
            **WINDOW,
            "sessions": [sessions[0], sessions[-1]] if sessions else None,
            "splits": RULE["splits"],
        },
        "arms": bars["arms"],
        "bars": bars,
        "control": control,
        "holm": survives,
        "verdict": verdict,
        "reported": {
            n: {
                **reported(rows[n]),
                "placebo": describe(placebo[n]),
                "missing_delta": missing_delta[n],
                "reasons": dict(book.why.get(n, {})),
                "entry_minutes": {
                    "design": describe([{"net": r["minute"]} for r in rows[n] if r["date"] < split]),
                    "holdout": describe([{"net": r["minute"]} for r in rows[n] if r["date"] >= split]),
                },
            }
            for n in ARMS
        },
        "inputs": fingerprint(h, sessions),
    }
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--smoke", action="store_true", help=f"the first {SMOKE_SESSIONS} sessions, two draws")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
