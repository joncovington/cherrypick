"""Item 97: a published breakdown signal on 30-minute bars, sold as a same-day SPX call spread.

The declaration is research/edge-hypotheses-plan.md section 97; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

The signal reads two things core's signal language does not have (a rate of change, and a margin on a
prior-session break), so it is computed here as a pure function (`firing_minute`) on 30-minute bars
cut by `core.signals.from_trail`, with `core.indicators.ema`. The structure is then an automation
entered at the firing minute (`arm_at`) and replayed by `cherrypick.automations.driver`, the engine
paper runs, so every strike, fill, fee and settlement is paper's arithmetic. Three passes over one
window:

1. **The signal**: every session's spot trail, read once, in order; each session's firing minute.
2. **The arm**: the structure at that minute on each session it fired.
3. **The placebo**: every session of a part gets `CONTROL_RULE["draws"]` copies of the structure, each
   at a fixed minute drawn from the arm's own entry minutes in the same part (seed 97). Any session,
   never only the ones the signal fired on.

Two literals decide it, and a pass needs both: `RULE` (the kit's three bars on the arm's trades, cut at
the split) and `CONTROL_RULE` (the arm's mean net against its placebo's per-session mean).

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
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from cherrypick.core import progress as core_progress

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's three bars, on the arm's trades, over one run cut at the split.
RULE = {
    "t_bar": 2.0,
    "min_n": 150,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10000, "seed": 97},
    "delta": True,
    "splits": ["2025-07-01"],
}

#: Against chance, fixed by the declaration before any run.
CONTROL_RULE = {
    "draws": 20,
    "seed": 97,
    "t_bar": 2.0,
    "halves": "2024-01-01",
    "split": "2025-07-01",
    "min_n": 150,
    "holdout_min_n": 60,
}

#: The window: the whole store, the vendor's SPX one-minute file.
WINDOW = {"symbol": "SPX", "store": "SPX-60s.db", "from": "2022-05-16", "through": "2026-11-20"}

#: The source's own window, reported only (its published results cover it).
SOURCE_WINDOW = {"from": "2023-01-01", "through": "2026-11-20"}

#: The signal as the source states it.
SIGNAL = {
    "bar_minutes": 30,
    "break_pct": 0.03,  # the close below the prior session's low by this much more, in percent
    "roc_n": 3,  # the close's rate of change over this many bars, below zero
    "ema_n": 20,  # the close below this many bars' EMA
    "latest": "15:00",  # no entry on a bar that ends after this
}

SMOKE_SESSIONS = 10
SMOKE_DRAWS = 2

ARM = "breakdown-call-spread"

#: The structure as the source states it, in our engine's words; `arm_at` gives it its entry minute.
STRUCTURE = {
    "name": ARM,
    "symbol": "SPX",
    "enabled": True,
    "expiry": {"kind": "dte", "days": 0, "tolerance": 0},
    "weekdays": [0, 1, 2, 3, 4],
    "legs": [
        {
            "name": "short_call",
            "right": "C",
            "side": -1,
            "strike": {"rule": "delta", "target": 0.30, "match": "nearest"},
        },
        {
            "name": "long_call",
            "right": "C",
            "side": 1,
            "strike": {"rule": "from_leg", "leg": "short_call", "points": 15, "match": "exact"},
        },
    ],
    "gates": [],
    "size": {"contracts": 1},
    "limits": {"max_open": 1, "per_day": 1},
    "exit": {},
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the store already holds the window. The smoke runs first, so a crash or no trades shows in minutes.
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


def minute_of(text: str) -> int:
    h, mm = text.split(":")
    return int(h) * 60 + int(mm)


def bar_ticks(trail: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    """A 60 s store's samples as ticks for `core.signals.from_trail`: the sample at minute m is the close
    of the one-minute bar ending at m, so it is placed in that bar (m - 1). A 30-minute bar's close is
    then the sample at its end (10:00's for the 09:30 bar), and the 16:00 sample closes the last bar."""
    return [(m - 1, p) for m, p in trail]


def firing_minute(
    bars: Sequence,
    session: str,
    prior_ok: Callable[[str, str], bool],
    signal: dict = SIGNAL,
) -> int | None:
    """The minute `session`'s signal fires (its bar's end), or None.

    `bars` are 30-minute bars, oldest first, over several sessions ending with `session`'s. The signal:
    a bar's close below the previous session's low by `break_pct` percent more, crossing there (armed
    at the session's start, re-armed only once a close is back above the level), while the close's
    rate of change over `roc_n` bars is below zero and the close is below the `ema_n`-bar EMA (both
    across the close). A cross the two conditions stop is spent. The first firing on a bar ending by
    `latest` counts. The previous session must be the trading day before (`prior_ok`), else nothing."""
    from cherrypick.core import indicators as ind

    idx = [i for i, b in enumerate(bars) if b.session == session]
    if not idx:
        return None
    first = idx[0]
    prev = [i for i in range(first) if bars[i].session == bars[first - 1].session] if first else []
    if not prev or not prior_ok(bars[prev[0]].session, session):
        return None
    level = min(bars[i].low for i in prev) * (1.0 - signal["break_pct"] / 100.0)
    closes = [b.close for b in bars]
    ema = ind.ema(closes, signal["ema_n"])
    latest = minute_of(signal["latest"])
    n = signal["roc_n"]
    armed = True
    for i in idx:
        if bars[i].end > latest:
            break
        below = bars[i].close < level
        if not below:
            armed = True
            continue
        if not armed:
            continue
        armed = False
        roc = (closes[i] / closes[i - n] - 1.0) if i >= n else None
        if roc is not None and roc < 0 and ema[i] is not None and closes[i] < ema[i]:
            return bars[i].end
    return None


def arm_at(minute: int, name: str = ARM) -> dict:
    """The structure entered at a fixed `minute`, a one-minute time entry."""
    a = copy.deepcopy(STRUCTURE)
    a["name"] = name
    a["entry"] = {"kind": "time", "at": hhmm(minute), "window": 1}
    return a


def placebo_name(draw: int) -> str:
    return f"{ARM}{PLACEBO}{draw:02d}"


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
    """A defined-risk credit spread's buying power: the wing less the credit, whole position."""
    calls = sorted(g["strike"] for g in trade["legs"] if g["right"] == "C")
    width = (calls[-1] - calls[0]) if len(calls) == 2 else 0.0
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


def combine(bars: str, control: str) -> str:
    """The arm passes only on both literals; a failure of either fails it."""
    if FAIL in (bars, control):
        return FAIL
    if bars == PASS and control == PASS:
        return PASS
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

    d = paths.results_dir() / "research" / "item97"
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


def _prior_ok(prev: str, session: str) -> bool:
    from cherrypick.core import calendar as cal

    return cal.previous_trading_day(date.fromisoformat(session)).isoformat() == prev


def signal_minutes(h, sessions: list[str]) -> dict[str, int | None]:
    """Pass 1: each session's firing minute, every trail read once, in order."""
    from cherrypick.core import signals

    keep = 40  # bars carried: the EMA's 20 and the session before, with room
    bars: list = []
    out: dict[str, int | None] = {}
    for s in core_progress.track(
        sessions,
        job="breakdown_call_spread_item97-study",
        phase="signal-sessions",
        unit="days",
        key=lambda _: WINDOW["symbol"],
        current=str,
    ):
        bars = bars[-keep:] + signals.from_trail(s, bar_ticks(h.trail(s, 960)), SIGNAL["bar_minutes"])
        out[s] = firing_minute(bars, s, _prior_ok)
    return out


def cmd_study(args) -> int:
    from cherrypick.automations import arms as arms_mod
    from cherrypick.automations import config as ens_config
    from cherrypick.automations import driver, history
    from cherrypick.core import calendar as cal
    from cherrypick.core import volindex

    from cherrypick.backtest import licensing, paths, settings

    s = settings.load(None)
    if not licensing.status(s)["active"]:
        raise SystemExit("the vendor's licence does not allow new work: item 97 reads its store")
    problems = arms_mod.validate(arm_at(600))
    if problems:
        raise SystemExit(f"{ARM}: {problems[0]}")
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

    # Pass 1: the signal.
    fired = signal_minutes(h, sessions)

    # Pass 2: the arm, at each session's firing minute.
    book = driver.Book()
    for session in sessions:
        minute = fired.get(session)
        if minute is not None:
            driver.run({ARM: arm_at(minute)}, h, [session], costs=cfg, vix_daily=vix, book=book)
    trades = [t for t in book.trades if t["arm"] == ARM]

    # Pass 3: the placebo, each session's draws from its part's entry minutes.
    rng = random.Random(CONTROL_RULE["seed"])
    split = CONTROL_RULE["split"]
    pool: dict[str, list[int]] = {}
    for t in trades:
        pool.setdefault(part_of(t["session"], split), []).append(t["entry_minute"])
    plan = draw_minutes(sessions, {k: sorted(v) for k, v in pool.items()}, split, draws, rng)
    pbook = driver.Book()
    for session in core_progress.track(
        sessions,
        job="breakdown_call_spread_item97-study",
        phase="placebo-sessions",
        unit="days",
        key=lambda _: WINDOW["symbol"],
        current=str,
    ):
        todays = {placebo_name(i + 1): arm_at(m, placebo_name(i + 1)) for i, m in enumerate(plan[session])}
        if todays:
            driver.run(todays, h, [session], costs=cfg, vix_daily=vix, book=pbook)

    rows, missing_delta = [], 0
    for t in trades:
        r = kit_row(t, h, rate, with_delta=True)
        if r is None:
            missing_delta += 1
        else:
            rows.append(r)
    placebo = [kit_row(t, h, rate, with_delta=False) for t in pbook.trades]

    bars = kit_rule.evaluate(RULE, {ARM: rows})
    control = judge_control(rows, per_session_means(placebo), CONTROL_RULE)
    verdict = {ARM: combine(bars["verdict"][ARM], control["verdict"])}
    fired_days = [d for d, m in fired.items() if m is not None]
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 97,
        "rule": RULE,
        "control_rule": CONTROL_RULE,
        "signal": SIGNAL,
        "structure": STRUCTURE,
        "window": {
            **WINDOW,
            "sessions": [sessions[0], sessions[-1]] if sessions else None,
            "splits": RULE["splits"],
        },
        "arms": bars["arms"],
        "bars": bars,
        "control": {ARM: control},
        "verdict": verdict,
        "reported": {
            ARM: {
                **reported(rows),
                "placebo": describe(placebo),
                "missing_delta": missing_delta,
                "signal": {"sessions": len(sessions), "fired": len(fired_days), "entered": len(trades)},
                "reasons": dict(book.why.get(ARM, {})),
                "entry_minutes": {
                    "design": describe([{"net": r["minute"]} for r in rows if r["date"] < split]),
                    "holdout": describe([{"net": r["minute"]} for r in rows if r["date"] >= split]),
                },
            }
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
