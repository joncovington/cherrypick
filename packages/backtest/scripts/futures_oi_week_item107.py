"""Item 107: forward only. In an S&P drawdown, does a week of rising E-mini open interest come before
a deeper fall over the next five sessions?

The declaration is research/edge-hypotheses-plan.md section 107; this script runs it. Item 106 judged
this relationship at 20 sessions and failed; its reported time profile showed the 5-session slope
alike in its design (2006-2018) and its holdout (2019 on), both read after the fact. With all the
history used, this item reads only reports after its declaration. It reuses item 106's rows and fit
(and, through it, item 105's reports, roll calendar and drawdowns) by loading those scripts, with the
horizon at five sessions and the feature scaling frozen at item 106's design values.

- `fetch`: item 105's CFTC files (the current year again) and Cboe's SPX history into
  `{results}/SPX.csv`, both public, personal use, never the repo.
- `study`: every row from `since` on, judged by `FORECAST_RULE`; until `min_n` rows have
  accumulated the verdict is `no verdict`, with the count.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

from cherrypick.core import progress as core_progress

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any forward row exists.
FORECAST_RULE = {
    "kind": "forecast",
    "since": "2026-11-24",
    "history_from": "2006-06-13",
    "high_window": 252,
    "drawdown_enter": 0.07,
    "drawdown_exit": 0.03,
    "swell_days": 13,
    "max_gap_days": 10,
    "horizon": 5,
    "prior_sessions": 5,
    "scale": {
        "doi": [0.019736945417177347, 0.03906658171718021],
        "vix": [27.35153846153846, 11.127751933577514],
        "prior": [-0.0034377877479054224, 0.034073950507600675],
        "depth": [0.15860084530495439, 0.12504317465154213],
    },
    "statistic": "oi_slope_5",
    "max_slope": -0.5,
    "t_bar": -2.0,
    "min_n": 40,
    "bootstrap": {"block": 4, "resamples": 10000, "seed": 107},
    "splits": [],
}

#: The research-queue chain, queued again whenever a fresh read is wanted.
CHAIN = (
    {"step": "fetch", "kind": "script", "args": ["fetch"], "output": "{results}/SPX.csv"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT = "pass", "fail", "no verdict"
SPX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv"


def _load(name: str):
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


i106 = _load("futures_oi_pace_item106")
i105 = i106.i105


# --------------------------------------------------------------------------- pure pieces


def rows_since(reports, spx, vix, rule: dict) -> tuple[list[dict], dict]:
    """Item 106's rows at this horizon, built from the whole history (so a drawdown already under way
    at `since` is in its right state), kept from `since` on."""
    built = {**rule, "from": rule["history_from"], "through": "9999-12-31"}
    rows, dropped = i106.build_rows(reports, spx, vix, built)
    return [r for r in rows if r["date"] >= rule["since"]], dropped


def judge(rows: list[dict], rule: dict, *, resamples: int | None = None, rep=None) -> dict:
    """The forward verdict: no verdict until `min_n` rows; then the slope (frozen scaling) at most
    `max_slope` with bootstrap t at most `t_bar` is a pass, anything else a fail."""
    stat = rule["statistic"]
    scale = {k: tuple(v) for k, v in rule["scale"].items()}
    out: dict = {
        "n": len(rows),
        "first": rows[0]["date"] if rows else None,
        "last": rows[-1]["date"] if rows else None,
    }
    if len(rows) < rule["min_n"]:
        return {"forward": out, "verdict": {stat: NO_VERDICT}}
    f = i106.fit(rows, scale)
    if f is None:
        return {"forward": {**out, "singular": True}, "verdict": {stat: NO_VERDICT}}
    slope = f["coef"]["doi"]
    n_draws = resamples or rule["bootstrap"]["resamples"]
    if rep is not None:
        rep.phase("bootstrap", total=n_draws, unit="draws")
    se = i106.boot_se(rows, scale, rule, resamples=n_draws, rep=rep)
    t = slope / se if se else None
    checks = {"effect": slope <= rule["max_slope"], "t": t is not None and t <= rule["t_bar"]}
    word = PASS if all(checks.values()) else FAIL
    return {"forward": {**out, "coef": f["coef"], "slope": slope, "se": se, "t": t, "checks": checks},
            "verdict": {stat: word}}  # fmt: skip


def fingerprint(reports, spx, vix) -> dict:
    lines = "\n".join(
        [f"cot:{r['d']}" for r in reports] + [f"spx:{d}" for d, _ in spx] + [f"vix:{d}" for d in sorted(vix)]
    )
    return {
        "reports": len(reports),
        "last_report": reports[-1]["d"] if reports else None,
        "last_spx": spx[-1][0] if spx else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- files


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item107"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spx_path() -> Path:
    return out_dir() / "SPX.csv"


def cmd_fetch(args) -> int:
    """Item 105's CFTC files (its fetch keeps the years it has and fetches the current one again), then
    Cboe's SPX history, refused if it parses to fewer sessions than the file it would replace."""
    with core_progress.Reporter("futures_oi_week_item107-fetch") as rep:
        rep.split("sources", ["cot", "spx"], unit="sources")
        code = i105.cmd_fetch(argparse.Namespace(refresh=False))
        rep.advance("sources", key="cot", current="cot")
        req = urllib.request.Request(SPX_URL, headers={"User-Agent": i105.UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        new = i105.parse_spx(text)
        old = i105.parse_spx(spx_path().read_text(encoding="utf-8")) if spx_path().exists() else []
        if len(new) < len(old) or not new:
            print(f"SPX history refused: {len(new)} sessions against {len(old)} kept", file=sys.stderr)
            return 1
        spx_path().write_text(text, encoding="utf-8")
        rep.advance("sources", key="spx", current="spx")
    print(json.dumps({"written": str(spx_path()), "sessions": len(new), "last": new[-1][0]}))
    return code


def cmd_study(args) -> int:
    rule = FORECAST_RULE
    with core_progress.Reporter("futures_oi_week_item107-study") as rep:
        rep.split("inputs", ["cot", "spx", "vix"], unit="files")
        reports = i105.load_reports()
        rep.advance("inputs", key="cot", current="cot")
        spx = i105.parse_spx(spx_path().read_text(encoding="utf-8"))
        rep.advance("inputs", key="spx", current="spx")
        vix = i106.parse_vix(i106.vix_path().read_text(encoding="utf-8"))
        rep.advance("inputs", key="vix", current="vix")
        rows, dropped = rows_since(reports, spx, vix, rule)
        block = judge(rows, rule, resamples=args.resamples, rep=rep)
        doc = {
            "data": "public CFTC and Cboe data, personal use: never commit or publish",
            "item": 107,
            "rule": rule,
            "dropped_over_history": dropped,
            **block,
            "inputs": fingerprint(reports, spx, vix),
        }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"], "n": block["forward"]["n"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    f = sub.add_parser("fetch")
    f.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--resamples", type=int, default=None, help="bootstrap draws (default: the rule's)")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
