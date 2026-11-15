"""Item 81: the first hour's width against its own recent median, and the rest of the day.

The declaration is research/edge-hypotheses-plan.md section 81; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A forecast study, not a trade, on SPX's one-minute prices in the licensed store. Two declared tests
of one feature, the first hour's range over the median of the prior 20 sessions' first hours (log):

- `persistence`: does it raise the log range from 10:30 to the close, beyond the HAR terms and the
  gap? (intraday volatility persists; declared sign positive)
- `extension`: does a NARROW first hour make it likelier that the rest of the day trades a row beyond
  the first hour's range (the Market Profile reading: a wide initial balance contains the day, a narrow
  one invites a breakout)? (declared sign negative)

The pass rule is `FORECAST_RULE`. The pure helpers are copied from item 80's script (a study does not
load another study's script).

- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (`{"persistence": word, "extension": word}`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run.
FORECAST_RULE = {
    "kind": "forecast",
    "symbol": "SPX",
    "through": "2026-11-13",
    "min_samples": 380,
    "max_gap_days": 4,
    "har": [1, 5, 22],
    "ib_median_sessions": 20,
    "ib_end": "10:30",
    "outcome_from": "10:30",
    "extension_points": 2.5,
    "tests": {
        "persistence": {"outcome": "y", "sign": 1, "min_effect": 0.10},
        "extension": {"outcome": "ext", "sign": -1, "min_effect": 0.05},
    },
    "t_bar": 2.3,
    "min_n": 200,
    "holdout_min_n": 60,
    "bootstrap": {"block": 10, "resamples": 10000, "seed": 81},
    "splits": ["2025-07-01"],
    "halves": "2023-12-01",
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the study reads the SPX minutes the store already holds.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
CONTROLS = ("ln_r1", "ln_r5", "ln_r22", "gap")
FEATURE = "w"
HISTORY = 22


# --------------------------------------------------------------------------- pure pieces


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def ib_of(samples: list[tuple[float, float]], ib_end: int) -> tuple[float, float] | None:
    """The first hour's (low, high): every stamp before `ib_end`."""
    ib = [px for ts, px in samples if minute_of(ts) < ib_end]
    return (min(ib), max(ib)) if ib else None


def day_row(
    day: str,
    samples: list[tuple[float, float]],
    prior_close: float,
    history: list[float],
    ib_history: list[float],
    *,
    ib_end: int,
    outcome_from: int,
    extension_points: float,
    median_n: int,
) -> dict | None:
    """One session's feature, controls and both outcomes. `history` holds the prior sessions'
    range ÷ close and `ib_history` their first-hour range ÷ prior close, oldest first."""
    ib = ib_of(samples, ib_end)
    rest = [px for ts, px in samples if minute_of(ts) >= outcome_from]
    if ib is None or len(rest) < 2 or len(history) < HISTORY or len(ib_history) < median_n:
        return None
    ib_frac = (ib[1] - ib[0]) / prior_close
    med = statistics.median(ib_history[-median_n:])
    rest_range = max(rest) - min(rest)
    if ib_frac <= 0 or med <= 0 or rest_range <= 0 or min(history[-HISTORY:]) <= 0:
        return None
    return {
        "date": day,
        "w": math.log(ib_frac / med),
        "y": math.log(rest_range / prior_close),
        "ext": 1.0 if max(rest) >= ib[1] + extension_points or min(rest) <= ib[0] - extension_points else 0.0,
        "ln_r1": math.log(history[-1]),
        "ln_r5": math.log(sum(history[-5:]) / 5),
        "ln_r22": math.log(sum(history[-HISTORY:]) / HISTORY),
        "gap": abs(samples[0][1] - prior_close) / prior_close,
    }


def build_rows(
    sessions: dict[str, list[tuple[float, float]]],
    rule: dict,
    *,
    ib_end: str | None = None,
    outcome_from: str | None = None,
    extension_points: float | None = None,
) -> tuple[list[dict], dict]:
    """Every judged day from the sessions (date -> stamps in order), and the counts dropped. A short
    session or a calendar gap restarts both histories, so no series runs across a hole."""
    rows: list[dict] = []
    dropped = {"short": 0, "gap": 0, "history": 0, "incomplete": 0}
    history: list[float] = []
    ib_history: list[float] = []
    prev: str | None = None
    ib_end_min = hhmm(ib_end or rule["ib_end"])
    for d in sorted(sessions):
        samples = sessions[d]
        if len(samples) < rule["min_samples"]:
            dropped["short"] += 1
            prev, history, ib_history = None, [], []
            continue
        prior_close = sessions[prev][-1][1] if prev is not None else None
        if (
            prev is not None
            and (date.fromisoformat(d) - date.fromisoformat(prev)).days > rule["max_gap_days"]
        ):
            dropped["gap"] += 1
            history, ib_history, prior_close = [], [], None
        elif (
            prior_close is not None
            and len(history) >= HISTORY
            and len(ib_history) >= rule["ib_median_sessions"]
        ):
            row = day_row(
                d, samples, prior_close, history, ib_history,
                ib_end=ib_end_min, outcome_from=hhmm(outcome_from or rule["outcome_from"]),
                extension_points=rule["extension_points"] if extension_points is None else extension_points,
                median_n=rule["ib_median_sessions"],
            )  # fmt: skip
            if row is None:
                dropped["incomplete"] += 1
            else:
                rows.append(row)
        elif prior_close is not None:
            dropped["history"] += 1
        px = [p for _, p in samples]
        ib = ib_of(samples, ib_end_min)
        if prior_close is not None and ib is not None:
            ib_history.append((ib[1] - ib[0]) / prior_close)
        history.append((max(px) - min(px)) / px[-1])
        prev = d
    return rows, dropped


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; raises ValueError on a singular system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("singular design matrix")
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


NAMES = ("const", *CONTROLS, FEATURE)


def _x(rows: list[dict]) -> list[list[float]]:
    return [[1.0, *(r[c] for c in CONTROLS), r[FEATURE]] for r in rows]


def _normal(x: list[list[float]], y: list[float], idx) -> tuple[list[list[float]], list[float]]:
    k = len(x[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in idx:
        xi, yi = x[i], y[i]
        for a in range(k):
            xa = xi[a]
            xty[a] += xa * yi
            row = xtx[a]
            for b in range(a, k):
                row[b] += xa * xi[b]
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]
    return xtx, xty


def ols(rows: list[dict], outcome: str) -> dict[str, float]:
    """Least squares of `outcome` on the controls and the feature, by the normal equations."""
    xtx, xty = _normal(_x(rows), [r[outcome] for r in rows], range(len(rows)))
    return dict(zip(NAMES, solve(xtx, xty), strict=True))


def coef_or_none(rows: list[dict], outcome: str) -> float | None:
    if len(rows) <= len(NAMES):
        return None
    try:
        return ols(rows, outcome)[FEATURE]
    except ValueError:
        return None


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis and Romano): runs of mean length `block`, wrapping."""
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se(rows: list[dict], outcome: str, *, block: int, resamples: int, seed: int) -> float | None:
    """The bootstrap standard error of the feature's coefficient over the rows in time order."""
    x, y = _x(rows), [r[outcome] for r in rows]
    at = NAMES.index(FEATURE)
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        xtx, xty = _normal(x, y, stationary_indices(len(rows), block, rng))
        try:
            draws.append(solve(xtx, xty)[at])
        except ValueError:
            continue
    if len(draws) < 2:
        return None
    mean = sum(draws) / len(draws)
    return math.sqrt(sum((d - mean) ** 2 for d in draws) / (len(draws) - 1))


def judge_test(rows: list[dict], rule: dict, name: str, *, resamples: int | None = None) -> dict:
    """One declared test: the design part (signed effect, bootstrap t, both halves), then the holdout
    refitted alone, read only after a design pass."""
    spec = rule["tests"][name]
    outcome, sign = spec["outcome"], spec["sign"]
    split = rule["splits"][0]
    design = [r for r in rows if r["date"] < split]
    holdout = [r for r in rows if r["date"] >= split]
    out: dict = {"design": {"n": len(design)}, "holdout": {"n": len(holdout)}}
    if len(design) < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": NO_VERDICT}
    coef = ols(design, outcome)[FEATURE]
    bs = rule["bootstrap"]
    se = boot_se(design, outcome, block=bs["block"], resamples=resamples or bs["resamples"], seed=bs["seed"])
    t = coef / se if se else None
    halves = {
        "first": coef_or_none([r for r in design if r["date"] < rule["halves"]], outcome),
        "second": coef_or_none([r for r in design if r["date"] >= rule["halves"]], outcome),
    }
    checks = {
        "effect": sign * coef >= spec["min_effect"],
        "t": t is not None and sign * t >= rule["t_bar"],
        "halves": all(v is not None and sign * v > 0 for v in halves.values()),
    }
    out["design"].update({"coef": coef, "se": se, "t": t, "halves": halves, "checks": checks})
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": FAIL}
    out["design"]["verdict"] = PASS
    hcoef = coef_or_none(holdout, outcome)
    word = PASS if hcoef is not None and sign * hcoef > 0 else FAIL
    provisional = word == PASS and len(holdout) < rule["holdout_min_n"]
    out["holdout"].update({"coef": hcoef, "verdict": word, "provisional": provisional})
    return {**out, "verdict": word, "provisional": provisional}


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None) -> dict:
    tests = {name: judge_test(rows, rule, name, resamples=resamples) for name in rule["tests"]}
    return {"tests": tests, "verdict": {name: t["verdict"] for name, t in tests.items()}}


def quartiles(rows: list[dict]) -> list[dict]:
    """The design's days by quartile of the feature: mean outcome and extension rate (reported)."""
    if len(rows) < 8:
        return []
    ws = sorted(r[FEATURE] for r in rows)
    cuts = [ws[len(ws) * q // 4] for q in (1, 2, 3)]
    groups: list[list[dict]] = [[], [], [], []]
    for r in rows:
        groups[sum(1 for c in cuts if r[FEATURE] >= c)].append(r)
    return [
        {
            "q": i + 1,
            "n": len(g),
            "mean_y": sum(r["y"] for r in g) / len(g),
            "ext_rate": sum(r["ext"] for r in g) / len(g),
        }
        for i, g in enumerate(groups)
        if g
    ]


def fingerprint(sessions: dict[str, list]) -> dict:
    """What the run read, as counts and a hash of them: never a price."""
    lines = "\n".join(f"{d}:{len(s)}" for d, s in sorted(sessions.items()))
    return {
        "sessions": len(sessions),
        "stamps": sum(len(s) for s in sessions.values()),
        "first": min(sessions) if sessions else None,
        "last": max(sessions) if sessions else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- the store (read-only)


def store_path() -> Path:
    from cherrypick.backtest import paths, settings

    return paths.vendor_dir(settings.load()) / "SPX-60s.db"


def load_sessions(
    conn: sqlite3.Connection, symbol: str, through: str
) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for d, ts, spot in conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date <= ? AND spot > 0 "
        "ORDER BY trade_date, ts",
        (symbol, through),
    ):
        out.setdefault(d, []).append((float(ts), float(spot)))
    return out


def fomc_days() -> set[str] | None:
    from cherrypick.core import home

    f = home.data_dir() / "public" / "item33" / "calendar.json"
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))["fomc"]["scheduled"])
    except (OSError, ValueError, KeyError):
        return None


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item81"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reported(sessions: dict, rows: list[dict], rule: dict) -> dict:
    """The declared cuts, on the design part, reported and never judged."""
    split = rule["splits"][0]

    def design_of(rs: list[dict]) -> list[dict]:
        return [r for r in rs if r["date"] < split]

    def both(rs: list[dict]) -> dict:
        return {name: coef_or_none(rs, spec["outcome"]) for name, spec in rule["tests"].items()}

    design = design_of(rows)
    out: dict = {
        "ib_to_1000": both(design_of(build_rows(sessions, rule, ib_end="10:00", outcome_from="10:00")[0])),
        "outcome_from_1230": both(design_of(build_rows(sessions, rule, outcome_from="12:30")[0])),
        "extension_0": coef_or_none(design_of(build_rows(sessions, rule, extension_points=0.0)[0]), "ext"),
        "extension_5": coef_or_none(design_of(build_rows(sessions, rule, extension_points=5.0)[0]), "ext"),
        "quartiles": quartiles(design),
    }
    fomc = fomc_days()
    out["without_fomc"] = both([r for r in design if r["date"] not in fomc]) if fomc else "no calendar"
    return out


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    conn = sqlite3.connect(f"file:{store_path()}?mode=ro", uri=True)
    try:
        sessions = load_sessions(conn, rule["symbol"], rule["through"])
    finally:
        conn.close()
    rows, dropped = build_rows(sessions, rule)
    block = judge(rows, rule)
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 81,
        "rule": rule,
        "window": {
            "from": rows[0]["date"] if rows else None,
            "through": rule["through"],
            "splits": rule["splits"],
        },
        "days": len(rows),
        "dropped": dropped,
        **block,
        "reported": reported(sessions, rows, rule),
        "inputs": fingerprint(sessions),
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
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
