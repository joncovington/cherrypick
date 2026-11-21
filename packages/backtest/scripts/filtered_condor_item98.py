"""Item 98: a published SPX 0DTE condor, $60 out with $15 wings at 10:30, on a filtered day, out at 75%.

The declaration is research/edge-hypotheses-plan.md section 98; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Three automations (`ARMS`) are replayed by `cherrypick.automations.driver`, the engine paper runs, over
the vendor's SPX one-minute store, so every entry, gate, fill, fee and settlement is paper's arithmetic:

- `ic60`: the condor as the source states it, its filters and event skips, out at 75% of the credit.
  The only arm judged.
- `ic60-every-day`: the same structure, credit floor and exit on every session, no market filter and no
  event skip. Its trades on the sessions `ic60` did not enter are the skipped days `FILTER_RULE` reads.
- `ic60-hold`: `ic60` held to settlement, the source's own comparison. Reported, never judged.

Two literals decide it, and a pass needs both: `RULE` (the kit's three bars on `ic60`'s trades, cut at
the split) and `FILTER_RULE` (the kept days' mean net against the skipped days').

- `study`: writes `{results}/results.json` with `verdict` (`{"ic60": word}`).
- `study --smoke`: the first `SMOKE_SESSIONS` sessions, to `smoke.json`, never a verdict.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.core import progress as core_progress

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's three bars, on `ic60`'s trades, over one run cut at the split.
RULE = {
    "t_bar": 2.0,
    "min_n": 100,
    "holdout_min_n": 50,
    "tail": {"per_year": "sample", "resamples": 10000, "seed": 98},
    "delta": True,
    "splits": ["2025-07-01"],
}

#: The filters against the days they skip, fixed by the declaration before any run.
FILTER_RULE = {
    "t_bar": 2.0,
    "halves": "2023-10-01",
    "split": "2025-07-01",
    "min_n": 100,
    "holdout_min_n": 50,
}

#: The window: the vendor's SPX one-minute store, through the last session stored with its deltas.
WINDOW = {"symbol": "SPX", "store": "SPX-60s.db", "from": "2022-05-16", "through": "2026-11-13"}

SPLIT = RULE["splits"][0]
SMOKE_SESSIONS = 10
JUDGED = "ic60"

_EVERY_DAY = [0, 1, 2, 3, 4]
_SAME_DAY = {"kind": "dte", "days": 0, "tolerance": 0}


def _legs(points: float, wing: float) -> list[dict]:
    return [
        {
            "name": "short_put",
            "right": "P",
            "side": -1,
            "strike": {"rule": "spot", "points": -points, "match": "nearest"},
        },
        {
            "name": "long_put",
            "right": "P",
            "side": 1,
            "strike": {"rule": "from_leg", "leg": "short_put", "points": -wing, "match": "exact"},
        },
        {
            "name": "short_call",
            "right": "C",
            "side": -1,
            "strike": {"rule": "spot", "points": points, "match": "nearest"},
        },
        {
            "name": "long_call",
            "right": "C",
            "side": 1,
            "strike": {"rule": "from_leg", "leg": "short_call", "points": wing, "match": "exact"},
        },
    ]


#: The source's market filters, in the engine's gates, before its credit floor.
MARKET_GATES = [
    {"gate": "vix_level", "min": 17.0, "max": 30.0},
    {"gate": "change_pct", "max": 1.0},
    {"gate": "open_change_pct", "min": -1.25, "max": 0.0},
]
CREDIT_FLOOR = {"gate": "reward_risk", "min": 6.0}
EVENT_SKIPS = {"labels": ["MonthEnd", "PMI-Mfg"], "through_expiry": False}

_IC60 = {
    "name": "ic60",
    "symbol": "SPX",
    "enabled": True,
    "expiry": _SAME_DAY,
    "entry": {"kind": "time", "at": "10:30", "window": 1},
    "weekdays": _EVERY_DAY,
    "skip_events": EVENT_SKIPS,
    "legs": _legs(60.0, 15.0),
    "gates": [*MARKET_GATES, CREDIT_FLOOR],
    "size": {"contracts": 1},
    "limits": {"max_open": 1, "per_day": 1},
    "exit": {"profit_target_pct": 75.0},
}


def every_day(arm: dict) -> dict:
    """`arm` with no market filter and no event skip: its structure, credit floor, size and exit kept."""
    a = copy.deepcopy(arm)
    a["name"] = f"{arm['name']}-every-day"
    a["gates"] = [g for g in arm["gates"] if g not in MARKET_GATES]
    a.pop("skip_events", None)
    return a


def held(arm: dict) -> dict:
    """`arm` held to settlement: everything kept but the profit target."""
    a = copy.deepcopy(arm)
    a["name"] = f"{arm['name']}-hold"
    a["exit"] = {}
    return a


ARMS = {a["name"]: a for a in (_IC60, every_day(_IC60), held(_IC60))}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the store already holds the window. The smoke runs first, so a crash or an arm with no trades shows
#: in minutes.
CHAIN = (
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10


# --------------------------------------------------------------------------- pure pieces


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


def skipped(kept: list[dict], every: list[dict]) -> list[dict]:
    """The every-day arm's trades on the sessions the filtered arm did not enter."""
    entered = {r["date"] for r in kept}
    return [r for r in every if r["date"] not in entered]


def welch(a: list[float], b: list[float]) -> dict:
    """Mean of `a` less mean of `b`, with Welch's t and its one-sided p (normal tail)."""
    if len(a) < 2 or len(b) < 2:
        return {"n": len(a), "n_control": len(b), "diff": None, "t": None, "p": None}
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    t = (ma - mb) / se if se > 0 else None
    p = 0.5 * math.erfc(t / math.sqrt(2)) if t is not None else None
    return {"n": len(a), "n_control": len(b), "mean": ma, "control_mean": mb, "diff": ma - mb, "t": t, "p": p}


def judge_filter(kept: list[dict], skip: list[dict], rule: dict) -> dict:
    """Kept days against skipped days: the design difference > 0 at t >= t_bar and > 0 in each design
    half; then the holdout difference > 0, read only after a design pass. The floors count kept trades."""
    split, halves = rule["split"], rule["halves"]

    def nets(rows, lo=None, hi=None):
        return [r["net"] for r in rows if (lo is None or r["date"] >= lo) and (hi is None or r["date"] < hi)]

    design = welch(nets(kept, hi=split), nets(skip, hi=split))
    first = welch(nets(kept, hi=halves), nets(skip, hi=halves))
    second = welch(nets(kept, lo=halves, hi=split), nets(skip, lo=halves, hi=split))
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
    hold = welch(nets(kept, lo=split), nets(skip, lo=split))
    if hold["n"] < rule["holdout_min_n"]:
        hold["verdict"] = NO_VERDICT
    else:
        hold["verdict"] = PASS if hold["diff"] is not None and hold["diff"] > 0 else FAIL
    out["holdout"] = hold
    out["verdict"] = hold["verdict"]
    return out


def combine(bars: str, filt: str) -> str:
    """A pass needs both literals; a failure of either fails it."""
    if FAIL in (bars, filt):
        return FAIL
    if bars == PASS and filt == PASS:
        return PASS
    return NO_VERDICT


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
    """Context, never judged: every year, each part, exits by kind, costs, and the mid-fill net beside."""
    years: dict[str, list[dict]] = {}
    for r in rows:
        years.setdefault(r["date"][:4], []).append(r)
    exits: dict[str, int] = {}
    for r in rows:
        exits[r["exit"]] = exits.get(r["exit"], 0) + 1
    return {
        "years": {y: describe(v) for y, v in sorted(years.items())},
        "design": {
            "verdict_fill": describe([r for r in rows if r["date"] < SPLIT]),
            "mid_fill": describe([r for r in rows if r["date"] < SPLIT], "net_mid"),
        },
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

    d = paths.results_dir() / "research" / "item98"
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
    from cherrypick.automations import events as ev
    from cherrypick.core import calendar as cal
    from cherrypick.core import volindex

    from cherrypick.backtest import licensing, paths, settings

    s = settings.load(None)
    if not licensing.status(s)["active"]:
        raise SystemExit("the vendor's licence does not allow new work: item 98 reads its store")
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
    if args.smoke:
        sessions = sessions[:SMOKE_SESSIONS]
    cfg = ens_config.load_config()
    vix = volindex.closes("VIX")
    rate = _rate_reader()

    calendar = ev.load_calendar((cfg.get("events") or {}).get("path"))
    book = driver.Book()
    for session in core_progress.track(
        sessions,
        job="filtered_condor_item98-study",
        phase="sessions",
        unit="days",
        key=lambda _: WINDOW["symbol"],
        current=str,
    ):
        driver.run(ARMS, h, [session], events=calendar, costs=cfg, vix_daily=vix, book=book)
    rows, missing_delta = {}, {}
    for n in ARMS:
        rows[n], missing_delta[n] = [], 0
        for t in (t for t in book.trades if t["arm"] == n):
            r = kit_row(t, h, rate, with_delta=(n == JUDGED))
            if r is None:
                missing_delta[n] += 1
            else:
                rows[n].append(r)

    bars = kit_rule.evaluate(RULE, {JUDGED: rows[JUDGED]})
    skip = skipped(rows[JUDGED], rows[f"{JUDGED}-every-day"])
    filt = judge_filter(rows[JUDGED], skip, FILTER_RULE)
    verdict = {JUDGED: combine(bars["verdict"][JUDGED], filt["verdict"])}
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 98,
        "rule": RULE,
        "filter_rule": FILTER_RULE,
        "arms_spec": ARMS,
        "window": {
            **WINDOW,
            "sessions": [sessions[0], sessions[-1]] if sessions else None,
            "splits": RULE["splits"],
        },
        "arms": bars["arms"],
        "bars": bars,
        "filter": filt,
        "verdict": verdict,
        "reported": {
            **{
                n: {
                    **reported(rows[n]),
                    "missing_delta": missing_delta[n],
                    "reasons": dict(book.why.get(n, {})),
                }
                for n in ARMS
            },
            "skipped_days": describe(skip),
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
    p.add_argument("--smoke", action="store_true", help=f"the first {SMOKE_SESSIONS} sessions")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
