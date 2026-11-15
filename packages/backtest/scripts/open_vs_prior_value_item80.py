"""Item 80: an open outside the prior value area, held through the first hour, and the day's range.

The declaration is research/edge-hypotheses-plan.md section 80; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A forecast study, not a trade: SPX's one-minute prices from the licensed store, each session's
Market Profile (`cherrypick.core.marketprofile`), and one least-squares model of the rest of the
day's log range on the HAR controls, the first hour's range, the gap and the day's class against the
prior value area. The pass rule is `FORECAST_RULE` (the kit's `RULE` judges trades on the three bars,
which a forecast has none of).

- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (`{"held_outside": word}`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import marketprofile as mp

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
    "row_size": 2.5,
    "value_area_share": 0.70,
    "ib_end": "10:30",
    "outcome_from": "10:30",
    "statistic": "held_outside",
    "t_bar": 2.0,
    "min_effect": 0.05,
    "min_n": 40,
    "holdout_min_n": 15,
    "bootstrap": {"block": 10, "resamples": 10000, "seed": 80},
    "splits": ["2025-07-01"],
    "halves": "2023-12-01",
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the study reads the SPX minutes the store already holds.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
CLASSES = ("inside", "held_outside", "returned")
CONTROLS = ("ln_r1", "ln_r5", "ln_r22", "ln_ib", "gap")
HISTORY = 22


# --------------------------------------------------------------------------- pure pieces


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def bars(samples: list[tuple[float, float]]) -> list[dict]:
    """Each stamp a one-minute bar from the previous stamp's price to its own (the first opens at its
    own), on its minute: the path between two stamps is traded, so no row between them is skipped."""
    out, prev = [], None
    for ts, px in samples:
        o = px if prev is None else prev
        t = math.floor(ts / 60) * 60
        out.append({"t": t, "o": o, "h": max(o, px), "l": min(o, px), "c": px})
        prev = px
    return out


def value_band(
    samples: list[tuple[float, float]], row_size: float, share: float
) -> tuple[float, float] | None:
    """The session's value area as a price band: VAL to the top of VAH's row."""
    day = datetime.fromtimestamp(samples[0][0], ET).date()
    prof = mp.build_profile(
        bars(samples), row_size=row_size, bar_seconds=60, session=day, value_area_share=share
    )
    if prof is None:
        return None
    return prof.val, prof.vah + row_size


def classify(open_px: float, ib_low: float, ib_high: float, band: tuple[float, float]) -> str:
    """`inside`, `held_outside` (outside at the open and every first-hour price on that side) or
    `returned` (outside at the open, back inside within the first hour)."""
    lo, hi = band
    if lo <= open_px < hi:
        return "inside"
    if open_px >= hi:
        return "held_outside" if ib_low >= hi else "returned"
    return "held_outside" if ib_high < lo else "returned"


def day_row(
    day: date,
    samples: list[tuple[float, float]],
    prior: list[tuple[float, float]],
    history: list[float],
    *,
    row_size: float,
    share: float,
    ib_end: int,
    outcome_from: int,
) -> dict | None:
    """One session's features and outcome. `history` is the prior sessions' range ÷ close, oldest
    first (at least 22). None when a needed piece is missing."""
    band = value_band(prior, row_size, share)
    ib = [px for ts, px in samples if minute_of(ts) < ib_end]
    rest = [px for ts, px in samples if minute_of(ts) >= outcome_from]
    if band is None or not ib or len(rest) < 2 or len(history) < HISTORY:
        return None
    prior_close = prior[-1][1]
    open_px = samples[0][1]
    rest_range = max(rest) - min(rest)
    ib_range = max(ib) - min(ib)
    if rest_range <= 0 or ib_range <= 0 or min(history[-HISTORY:]) <= 0:
        return None
    return {
        "date": day.isoformat(),
        "class": classify(open_px, min(ib), max(ib), band),
        "side": "above" if open_px >= band[1] else "below" if open_px < band[0] else "inside",
        "y": math.log(rest_range / prior_close),
        "ln_r1": math.log(history[-1]),
        "ln_r5": math.log(sum(history[-5:]) / 5),
        "ln_r22": math.log(sum(history[-HISTORY:]) / HISTORY),
        "ln_ib": math.log(ib_range / prior_close),
        "gap": abs(open_px - prior_close) / prior_close,
    }


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


def design_matrix(rows: list[dict], with_classes: bool = True) -> tuple[list[str], list[list[float]]]:
    names = ["const", *CONTROLS] + (["held_outside", "returned"] if with_classes else [])
    x = []
    for r in rows:
        v = [1.0, *(r[c] for c in CONTROLS)]
        if with_classes:
            v += [1.0 if r["class"] == "held_outside" else 0.0, 1.0 if r["class"] == "returned" else 0.0]
        x.append(v)
    return names, x


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


def ols(rows: list[dict], with_classes: bool = True) -> dict[str, float]:
    """Least squares of `y` on the controls (and the two class indicators), by the normal equations."""
    names, x = design_matrix(rows, with_classes)
    xtx, xty = _normal(x, [r["y"] for r in rows], range(len(rows)))
    return dict(zip(names, solve(xtx, xty), strict=True))


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """One stationary-bootstrap resample (Politis and Romano): runs of mean length `block`, wrapping."""
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se(rows: list[dict], name: str, *, block: int, resamples: int, seed: int) -> float | None:
    """The bootstrap standard error of one coefficient over the rows in time order."""
    names, x = design_matrix(rows)
    y = [r["y"] for r in rows]
    at = names.index(name)
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


def residual(row: dict, coef: dict[str, float]) -> float:
    """The outcome less the frozen controls' fit (no class terms)."""
    return row["y"] - coef["const"] - sum(coef[c] * row[c] for c in CONTROLS)


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None) -> dict:
    """The verdict block: the design part (coefficient, bootstrap t, both halves), then the holdout,
    read only after a design pass."""
    split = rule["splits"][0]
    stat = rule["statistic"]
    design = [r for r in rows if r["date"] < split]
    holdout = [r for r in rows if r["date"] >= split]
    n_design = sum(1 for r in design if r["class"] == stat)
    n_holdout = sum(1 for r in holdout if r["class"] == stat)
    out: dict = {
        "design": {"n": len(design), "n_stat": n_design},
        "holdout": {"n": len(holdout), "n_stat": n_holdout},
    }
    if n_design < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: NO_VERDICT}}
    coef = ols(design)
    bs = rule["bootstrap"]
    se = boot_se(design, stat, block=bs["block"], resamples=resamples or bs["resamples"], seed=bs["seed"])
    t = coef[stat] / se if se else None
    halves: dict[str, float | None] = {}
    for half, part in (
        ("first", [r for r in design if r["date"] < rule["halves"]]),
        ("second", [r for r in design if r["date"] >= rule["halves"]]),
    ):
        halves[half] = coef_or_none(part, stat)
    checks = {
        "effect": coef[stat] >= rule["min_effect"],
        "t": t is not None and t >= rule["t_bar"],
        "halves": all(v is not None and v > 0 for v in halves.values()),
    }
    out["design"].update({"coef": coef, "se": se, "t": t, "halves": halves, "checks": checks})
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: FAIL}}
    out["design"]["verdict"] = PASS
    frozen = ols(design, with_classes=False)
    held = [residual(r, frozen) for r in holdout if r["class"] == stat]
    inside = [residual(r, frozen) for r in holdout if r["class"] == "inside"]
    diff = (sum(held) / len(held) - sum(inside) / len(inside)) if held and inside else None
    word = PASS if diff is not None and diff > 0 else FAIL
    provisional = word == PASS and n_holdout < rule["holdout_min_n"]
    out["holdout"].update({"difference": diff, "verdict": word, "provisional": provisional})
    return {**out, "verdict": {stat: word}, "provisional": provisional}


def coef_or_none(rows: list[dict], name: str = "held_outside") -> float | None:
    """A coefficient from a cut, or None when the cut cannot fit it (no such day, or singular)."""
    if not any(r["class"] == name for r in rows):
        return None
    try:
        return ols(rows)[name]
    except ValueError:
        return None


def build_rows(
    sessions: dict[str, list[tuple[float, float]]],
    rule: dict,
    *,
    row_size: float | None = None,
    ib_end: str | None = None,
    outcome_from: str | None = None,
) -> tuple[list[dict], dict]:
    """Every judged day from the sessions (date -> stamps in order), and the counts dropped. A short
    session or a calendar gap restarts the history, so no range series runs across a hole."""
    rows: list[dict] = []
    dropped = {"short": 0, "gap": 0, "history": 0, "incomplete": 0}
    history: list[float] = []
    prev_day: str | None = None
    for d in sorted(sessions):
        samples = sessions[d]
        if len(samples) < rule["min_samples"]:
            dropped["short"] += 1
            prev_day, history = None, []
            continue
        if (
            prev_day is not None
            and (date.fromisoformat(d) - date.fromisoformat(prev_day)).days > rule["max_gap_days"]
        ):
            dropped["gap"] += 1
            history = []
        elif prev_day is not None and len(history) >= HISTORY:
            row = day_row(
                date.fromisoformat(d), samples, sessions[prev_day], history,
                row_size=row_size or rule["row_size"], share=rule["value_area_share"],
                ib_end=hhmm(ib_end or rule["ib_end"]),
                outcome_from=hhmm(outcome_from or rule["outcome_from"]),
            )  # fmt: skip
            if row is None:
                dropped["incomplete"] += 1
            else:
                rows.append(row)
        elif prev_day is not None:
            dropped["history"] += 1
        px = [p for _, p in samples]
        history.append((max(px) - min(px)) / px[-1])
        prev_day = d
    return rows, dropped


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

    d = paths.results_dir() / "research" / "item80"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reported(sessions: dict, rows: list[dict], rule: dict) -> dict:
    """The declared cuts, reported and never judged (the design part, like the statistic)."""
    split = rule["splits"][0]

    def design_of(rs: list[dict]) -> list[dict]:
        return [r for r in rs if r["date"] < split]

    design = design_of(rows)
    out: dict = {}
    for size in (1.25, 5.0):
        out[f"rows_{size}"] = coef_or_none(design_of(build_rows(sessions, rule, row_size=size)[0]))
    out["ib_to_1000"] = coef_or_none(
        design_of(build_rows(sessions, rule, ib_end="10:00", outcome_from="10:00")[0])
    )
    out["outcome_from_1230"] = coef_or_none(design_of(build_rows(sessions, rule, outcome_from="12:30")[0]))
    fomc = fomc_days()
    out["without_fomc"] = (
        coef_or_none([r for r in design if r["date"] not in fomc]) if fomc else "no calendar"
    )
    for side in ("above", "below"):
        out[f"side_{side}"] = coef_or_none([r for r in design if r["class"] == "inside" or r["side"] == side])
    out["returned"] = coef_or_none(design, "returned")
    by_year: dict[str, dict[str, int]] = {}
    for r in rows:
        by_year.setdefault(r["date"][:4], dict.fromkeys(CLASSES, 0))[r["class"]] += 1
    out["days_by_year"] = by_year
    try:
        from cherrypick.core import robustness

        frozen = ols(design, with_classes=False)
        n_held = sum(1 for r in design if r["class"] == "held_outside")
        out["planned_mde"] = robustness.planned_mde(
            [residual(r, frozen) for r in design], n_held, paired=False
        )
    except Exception as exc:  # reported only; never blocks the verdict
        out["planned_mde"] = f"unavailable: {exc}"
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
        "item": 80,
        "rule": rule,
        "window": {
            "from": rows[0]["date"] if rows else None,
            "through": rule["through"],
            "splits": rule["splits"],
        },
        "days": {c: sum(1 for r in rows if r["class"] == c) for c in CLASSES},
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
