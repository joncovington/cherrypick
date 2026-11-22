"""Item 106: in an S&P drawdown, does a week of rising E-mini open interest come before a faster fall,
and a week of falling open interest before a slower one?

The declaration is research/edge-hypotheses-plan.md section 106; this script runs it. It reads the
CFTC reports item 105's `fetch` step keeps (`research/item105/cot/`), Cboe's daily SPX and VIX
closes (`scripts/fetch_market_files.py`), and reuses item 105's parsing, roll calendar and drawdown
rules by loading that script. All public, personal use; results stay under the backtest results
directory, never the repo.

A forecast study, not a trade. Each weekly report taken in a drawdown, outside the roll window, whose
prior report is in the same quarterly cycle and also outside the roll window, is a row. Its
predictor is the week's change in open interest. Its outcome is how much further the S&P falls in
the next 20 sessions from the session after the report's release. The statistic is the slope of
that further fall on the open interest change, with VIX, the prior five sessions' return and the
drawdown's depth held fixed. The pass rule is `FORECAST_RULE`.

- `study`: one run over the whole history, cut at the declared split, written to
  `{results}/results.json` with a top-level `verdict` (`{"oi_slope": word}`).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import progress as core_progress

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run.
FORECAST_RULE = {
    "kind": "forecast",
    "from": "2006-06-13",
    "through": "2026-11-17",
    "high_window": 252,
    "drawdown_enter": 0.07,
    "drawdown_exit": 0.03,
    "swell_days": 13,
    "max_gap_days": 10,
    "horizon": 20,
    "prior_sessions": 5,
    "statistic": "oi_slope",
    "max_slope": -0.5,
    "t_bar": -2.0,
    "min_n": 80,
    "holdout_min_n": 40,
    "bootstrap": {"block": 13, "resamples": 10000, "seed": 106},
    "splits": ["2019-01-01"],
}

#: The research-queue chain. Nothing is fetched: item 105's fetch keeps the reports.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
FEATURES = ("doi", "vix", "prior", "depth")
#: The horizons (sessions after the reference session) of the reported time profile, never judged.
TIME_PROFILE = (5, 10, 20, 40, 63)


def _item105():
    path = Path(__file__).resolve().parent / "futures_oi_bottom_item105.py"
    spec = importlib.util.spec_from_file_location("futures_oi_bottom_item105", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


i105 = _item105()


# --------------------------------------------------------------------------- pure pieces


def parse_vix(text: str) -> dict[str, float]:
    """Cboe's VIX history (`DATE,OPEN,HIGH,LOW,CLOSE`, M/D/YYYY): iso date -> close."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 5 or not parts[4]:
            continue
        c = float(parts[4])
        if c > 0:
            out[datetime.strptime(parts[0], "%m/%d/%Y").date().isoformat()] = c
    return out


def build_rows(
    reports: list[dict],
    spx: list[tuple[str, float]],
    vix: dict[str, float],
    rule: dict,
    *,
    in_drawdown_only: bool = True,
) -> tuple[list[dict], dict]:
    """One row per usable report, and the counts dropped.

    The drawdown is item 105's (enter at `drawdown_enter` under the prior `high_window` sessions' high
    close, leave under `drawdown_exit`). A row needs: the report outside the roll window; the prior
    report at most `max_gap_days` earlier and in the same quarterly cycle, so a change never spans an
    expiry (the roll window closes each cycle, so a prior report in it is always in the cycle before);
    a VIX close on the as-of session; and a full horizon after the reference session (the first
    session after the Friday release).

    `doi` is the week's open interest change (fraction), `vix` the as-of VIX close, `prior` the SPX
    return over the `prior_sessions` sessions to the as-of close, `depth` the drawdown's depth then.
    `fall` is min(0, lowest close in the next `horizon` sessions over the reference close - 1), in
    percent; `ret` the return over the horizon, in percent; `week_ret` the SPX return from the prior
    report's as-of close to this one's, in percent, for the same-week reading."""
    days = [d for d, _ in spx]
    closes = [c for _, c in spx]
    w, h, k = rule["high_window"], rule["horizon"], rule["prior_sessions"]
    rows: list[dict] = []
    dropped = {"history": 0, "outside": 0, "swell": 0, "no_prior": 0, "no_vix": 0, "horizon": 0}
    in_dd = False
    prev = None  # (report, as-of index, swell, cycle)
    j = 0
    for rpt in reports:
        d = rpt["d"]
        if d < rule["from"] or d > rule["through"]:
            continue
        while j + 1 < len(days) and days[j + 1] <= d:
            j += 1
        if days[j] > d or j < w:
            dropped["history"] += 1
            prev = None
            continue
        depth = 1 - closes[j] / max(closes[j - w : j + 1])
        if not in_dd and depth >= rule["drawdown_enter"]:
            in_dd = True
        elif in_dd and depth < rule["drawdown_exit"]:
            in_dd = False
        dd = date.fromisoformat(d)
        swell = i105.is_swell(dd, rule["swell_days"])
        cycle = i105.expiry_on_or_after(dd)
        here = (rpt, j, swell, cycle)
        p, prev = prev, here
        if in_drawdown_only and not in_dd:
            dropped["outside"] += 1
            continue
        if swell:
            dropped["swell"] += 1
            continue
        if p is None or p[3] != cycle or (dd - date.fromisoformat(p[0]["d"])).days > rule["max_gap_days"]:
            dropped["no_prior"] += 1
            continue
        v = vix.get(days[j])
        if v is None:
            dropped["no_vix"] += 1
            continue
        release = (dd + timedelta(days=3)).isoformat()
        r = next((x for x in range(j, len(days)) if days[x] > release), None)
        if r is None or r + h >= len(days):
            dropped["horizon"] += 1
            continue
        ahead = closes[r + 1 : r + h + 1]
        low = min(ahead)
        rows.append(
            {
                "date": d,
                "ref": days[r],
                "doi": rpt["oi"] / p[0]["oi"] - 1,
                "vix": v,
                "prior": closes[j] / closes[j - k] - 1,
                "depth": depth,
                "fall": 100 * min(0.0, low / closes[r] - 1),
                "ret": 100 * (closes[r + h] / closes[r] - 1),
                "low_day": ahead.index(low) + 1 if low < closes[r] else 0,
                "week_ret": 100 * (closes[j] / closes[p[1]] - 1),
            }
        )
    return rows, dropped


def scaling(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """Each feature's mean and standard deviation over the rows given (the design part)."""
    out = {}
    for f in FEATURES:
        xs = [r[f] for r in rows]
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
        out[f] = (m, sd or 1.0)
    return out


def solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when the system is singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda i: abs(m[i][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        for i in range(c + 1, n):
            f = m[i][c] / m[c][c]
            for k in range(c, n + 1):
                m[i][k] -= f * m[c][k]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][k] * x[k] for k in range(i + 1, n))) / m[i][i]
    return x


def fit(rows: list[dict], scale: dict, outcome: str = "fall", features=FEATURES) -> dict | None:
    """OLS of the outcome on an intercept and the standardized features (design-part scaling). The
    coefficients are in outcome units (percent of price) per one standard deviation of a feature."""
    if len(rows) <= len(features) + 1:
        return None
    X = [[1.0] + [(r[f] - scale[f][0]) / scale[f][1] for f in features] for r in rows]
    y = [r[outcome] for r in rows]
    p = len(X[0])
    xtx = [[sum(x[i] * x[j] for x in X) for j in range(p)] for i in range(p)]
    xty = [sum(x[i] * yy for x, yy in zip(X, y, strict=True)) for i in range(p)]
    beta = solve(xtx, xty)
    if beta is None:
        return None
    return {"n": len(rows), "coef": dict(zip(("intercept", *features), beta, strict=True))}


def stationary_indices(n: int, block: int, rng: random.Random) -> list[int]:
    out, i = [], rng.randrange(n)
    p = 1.0 / block
    while len(out) < n:
        out.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return out


def boot_se(rows: list[dict], scale: dict, rule: dict, *, resamples: int, rep=None) -> float | None:
    """The bootstrap standard error of the open interest slope: weeks drawn in blocks, in order."""
    bs = rule["bootstrap"]
    rng = random.Random(bs["seed"])
    draws = []
    for k in range(resamples):
        f = fit([rows[i] for i in stationary_indices(len(rows), bs["block"], rng)], scale)
        if f is not None:
            draws.append(f["coef"]["doi"])
        if rep is not None:
            rep.advance("bootstrap", current=k + 1)
    if len(draws) < 2:
        return None
    m = sum(draws) / len(draws)
    return math.sqrt(sum((x - m) ** 2 for x in draws) / (len(draws) - 1))


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None, rep=None) -> dict:
    """The verdict block: the design slope and its bootstrap t, then the holdout slope (design
    scaling), read only after a design pass. A pass needs a slope at most `max_slope` (a rise of one
    standard deviation in the week's open interest change goes with at least that much more further
    fall, in percent of price) and t at most `t_bar`."""
    split = rule["splits"][0]
    stat = rule["statistic"]
    design = [r for r in rows if r["date"] < split]
    holdout = [r for r in rows if r["date"] >= split]
    out: dict = {"design": {"n": len(design)}, "holdout": {"n": len(holdout)}}
    if len(design) < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: NO_VERDICT}}
    scale = scaling(design)
    f = fit(design, scale)
    if f is None:  # a singular design (a feature that never varies): nothing to judge
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: NO_VERDICT}}
    slope = f["coef"]["doi"]
    n_draws = resamples or rule["bootstrap"]["resamples"]
    if rep is not None:
        rep.phase("bootstrap", total=n_draws, unit="draws")
    se = boot_se(design, scale, rule, resamples=n_draws, rep=rep)
    t = slope / se if se else None
    checks = {"effect": slope <= rule["max_slope"], "t": t is not None and t <= rule["t_bar"]}
    out["design"].update({"coef": f["coef"], "slope": slope, "se": se, "t": t, "checks": checks,
                          "scale": {k: list(v) for k, v in scale.items()}})  # fmt: skip
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": {stat: FAIL}}
    out["design"]["verdict"] = PASS
    hf = fit(holdout, scale)
    hs = hf["coef"]["doi"] if hf else None
    word = PASS if hs is not None and hs < 0 else FAIL
    provisional = word == PASS and len(holdout) < rule["holdout_min_n"]
    out["holdout"].update({"slope": hs, "coef": hf["coef"] if hf else None, "verdict": word,
                           "provisional": provisional})  # fmt: skip
    return {**out, "verdict": {stat: word}, "provisional": provisional}


def mean_of(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def terciles(rows: list[dict], cuts: tuple[float, float]) -> dict:
    """Mean further fall and return by open interest change tercile (design cut points), and each end
    less the middle: the rising end's acceleration and the falling end's slowing, read apart."""
    groups = {
        "falling": [r for r in rows if r["doi"] < cuts[0]],
        "middle": [r for r in rows if cuts[0] <= r["doi"] < cuts[1]],
        "rising": [r for r in rows if r["doi"] >= cuts[1]],
    }
    out = {
        g: {
            "n": len(rs),
            "fall": mean_of([r["fall"] for r in rs]),
            "ret": mean_of([r["ret"] for r in rs]),
            "low_day": mean_of([r["low_day"] for r in rs if "low_day" in r]),
        }
        for g, rs in groups.items()
    }
    for key in ("fall", "ret"):
        mid = out["middle"][key]
        for g in ("falling", "rising"):
            v = out[g][key]
            out[g][f"{key}_less_middle"] = None if mid is None or v is None else v - mid
    return out


def tercile_cuts(rows: list[dict]) -> tuple[float, float]:
    xs = sorted(r["doi"] for r in rows)
    return xs[len(xs) // 3], xs[(2 * len(xs)) // 3]


def corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else None


def reported(reports, spx, vix, rows: list[dict], rule: dict) -> dict:
    """The declared cuts, reported and never judged (the design part, like the statistic)."""
    split = rule["splits"][0]

    def design_of(rs):
        return [r for r in rs if r["date"] < split]

    design = design_of(rows)
    if len(design) < 3:
        return {"design_rows": len(design)}
    scale = scaling(design)
    cuts = tercile_cuts(design)
    out: dict = {"tercile_cuts": list(cuts), "terciles": terciles(design, cuts)}
    out["without_controls"] = fit(design, scale, features=("doi",))
    out["forward_return"] = fit(design, scale, outcome="ret")
    profile = {}
    for hz in TIME_PROFILE:
        alt = {**rule, "horizon": hz}
        rs = design_of(build_rows(reports, spx, vix, alt)[0])
        if len(rs) <= 10:
            profile[str(hz)] = None
            continue
        sc = scaling(rs)
        f, fr = fit(rs, sc), fit(rs, sc, outcome="ret")
        profile[str(hz)] = {
            "n": len(rs),
            "fall_slope": f["coef"]["doi"] if f else None,
            "ret_slope": fr["coef"]["doi"] if fr else None,
            "terciles": terciles(rs, cuts),
        }
    out["time_profile"] = profile
    for e in (0.05, 0.10):
        alt = {**rule, "drawdown_enter": e}
        rs = design_of(build_rows(reports, spx, vix, alt)[0])
        out[f"drawdown_{e}"] = fit(rs, scaling(rs)) if len(rs) > 10 else None
    all_weeks = design_of(build_rows(reports, spx, vix, rule, in_drawdown_only=False)[0])
    out["all_weeks"] = fit(all_weeks, scaling(all_weeks)) if len(all_weeks) > 10 else None
    out["same_week"] = {
        "corr_doi_week_return": corr([r["doi"] for r in design], [r["week_ret"] for r in design]),
        "n": len(design),
    }
    by_year: dict[str, int] = {}
    for r in rows:
        by_year[r["date"][:4]] = by_year.get(r["date"][:4], 0) + 1
    out["rows_by_year"] = by_year
    return out


def fingerprint(reports: list[dict], spx: list, vix: dict) -> dict:
    lines = "\n".join(
        [f"cot:{r['d']}" for r in reports] + [f"spx:{d}" for d, _ in spx] + [f"vix:{d}" for d in sorted(vix)]
    )
    return {
        "reports": len(reports),
        "spx_sessions": len(spx),
        "vix_sessions": len(vix),
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- files


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item106"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vix_path() -> Path:
    from cherrypick.core import home

    return home.data_dir() / "market-files" / "cboe" / "VIX.csv"


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    with core_progress.Reporter("futures_oi_pace_item106-study") as rep:
        rep.split("inputs", ["cot", "spx", "vix"], unit="files")
        reports = i105.load_reports()
        rep.advance("inputs", key="cot", current="cot")
        spx = i105.parse_spx(i105.spx_path().read_text(encoding="utf-8"))
        rep.advance("inputs", key="spx", current="spx")
        vix = parse_vix(vix_path().read_text(encoding="utf-8"))
        rep.advance("inputs", key="vix", current="vix")
        if not reports or not spx or not vix:
            print("missing inputs: run item 105's fetch step and fetch_market_files", file=sys.stderr)
            return 1
        rows, dropped = build_rows(reports, spx, vix, rule)
        block = judge(rows, rule, resamples=args.resamples, rep=rep)
        rep.at("inputs", "reported cuts")
        doc = {
            "data": "public CFTC and Cboe data, personal use: never commit or publish",
            "item": 106,
            "rule": rule,
            "window": {
                "from": rows[0]["date"] if rows else None,
                "through": rule["through"],
                "splits": rule["splits"],
            },
            "rows": len(rows),
            "dropped": dropped,
            **block,
            "reported": reported(reports, spx, vix, rows, rule),
            "inputs": fingerprint(reports, spx, vix),
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
