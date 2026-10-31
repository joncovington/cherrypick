"""Item 34, the option-to-stock premium ratio (docs/edge-hypotheses-plan.md): do stocks whose options
trade heavily relative to their shares underperform, over the next five sessions, those whose options
trade lightly?

    python scripts/option_stock_ratio_item34.py study --window design
    python scripts/option_stock_ratio_item34.py study --window holdout   # once, only after a design pass
    python scripts/option_stock_ratio_item34.py report --window design   # results-design.md from the json

The rules are `cherrypick.backtest.ratiostudy`'s (pure, tested on invented data); this file reads the
stores and writes the result. It shares item 32's fetched grid (the plan file's `grid_sessions`, never
recounted), universe, outcome and report test, imported from `flow_signal_item32.py`. Results stay
under the backtest data dir (results/research/item34/): the inputs are licensed, so no figure goes
into the repo.

The holdout is spent once: it refuses to run without a design result that carries a pass, writes a
spent marker before it reads anything, and refuses again once either file exists.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flow_signal_item32 as s32  # noqa: E402
from cherrypick.backtest import flowfetch as _ff  # noqa: E402
from cherrypick.backtest import paths, ratiostudy, settings  # noqa: E402
from cherrypick.backtest.flowstudy import ENTRY_LAG, HORIZON, spent  # noqa: E402

WINDOWS = s32.WINDOWS
PLAN_FILES = s32.PLAN_FILES
PRIOR_SESSIONS = 5


def _out_dir() -> Path:
    d = paths.results_dir() / "research" / "item34"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------------
# Reading the stores into a Scan.


def build_scan(window: str, plan: dict, flow_db: Path) -> tuple[ratiostudy.Scan, dict]:
    from cherrypick.technicals import earnings_calendar as ec
    from cherrypick.technicals import history, stage, store

    lo, hi = settings.load()["windows"][window]
    hist = history.connect()
    cal = [b.date for b in store.raw_bars(hist, "SPY")]
    index = {d: i for i, d in enumerate(cal)}
    bench_adj = {b.date: b.close for b in store.adjusted_bars(hist, stage.DEFAULT_RULE.benchmark)}
    bench = [bench_adj.get(d) for d in cal]
    grid = list(plan["grid_sessions"])
    grid_set = set(grid)

    react_idx: dict[str, set[int]] = {}
    for sym, d, w in s32._reports(lo, hi):
        r = ec.reaction_day(d, w).isoformat()
        if r in index:
            react_idx.setdefault(sym, set()).add(index[r])

    conn = sqlite3.connect(f"file:{flow_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    by_sym: dict[str, list] = {}
    for r in conn.execute(
        "SELECT symbol, session, histogram FROM flow_days WHERE status = 'complete' ORDER BY symbol, session"
    ):
        if r["session"] in grid_set:
            by_sym.setdefault(r["symbol"], []).append((r["session"], json.loads(r["histogram"])))
    conn.close()

    rows: dict[str, list] = {s: [] for s in grid}
    counts = {"names": len(by_sym), "grid_name_days": 0, "no_ratio": 0, "no_outcome": 0, "rows": 0}
    for sym, days in by_sym.items():
        raw = {b.date: b for b in store.raw_bars(hist, sym)}
        adj = {b.date: b.close for b in store.adjusted_bars(hist, sym)}
        closes = [adj.get(d) for d in cal]
        for s, h in days:
            counts["grid_name_days"] += 1
            bar = raw.get(s)
            ratio = ratiostudy.ratio_of(h, bar.close if bar else None, bar.volume if bar else None)
            if ratio is None:
                counts["no_ratio"] += 1
                continue
            i = index.get(s)
            ex = None if i is None else s32.excess_return(closes, bench, i)
            if ex is None:
                counts["no_outcome"] += 1
                continue
            prior = None
            if i >= PRIOR_SESSIONS and closes[i] and closes[i - PRIOR_SESSIONS]:
                prior = closes[i] / closes[i - PRIOR_SESSIONS] - 1.0
            rows[s].append(
                {
                    "sym": sym,
                    "ratio": ratio,
                    "ex": ex,
                    "dvol": bar.close * bar.volume,
                    "prior": prior,
                    "reported": any(i + 2 <= j <= i + HORIZON + ENTRY_LAG for j in react_idx.get(sym, ())),
                }
            )
            counts["rows"] += 1
    return ratiostudy.Scan(grid=grid, rows=rows), counts


# ------------------------------------------------------------------------------------------------
# The result.


def cmd_study(args) -> int:
    window = args.window
    out = Path(args.out_dir) if args.out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    why = spent(out, window)
    if why:
        print(why, file=sys.stderr)
        return 2
    if window == "holdout":
        carried = json.loads((out / "results-design.json").read_text(encoding="utf-8"))["passed"]
        (out / "holdout-spent.json").write_text(
            json.dumps({"spent_on": date.today().isoformat(), "carried": carried}), encoding="utf-8"
        )
    plan_path = Path(args.plan) if args.plan else paths.data_dir() / "flow" / PLAN_FILES[window]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    flow_db = Path(args.flow_db) if args.flow_db else _ff.db_path(settings.load())
    scan, counts = build_scan(window, plan, flow_db)
    res = ratiostudy.evaluate(scan) if window == "design" else ratiostudy.evaluate_holdout(scan)
    lo, hi = settings.load()["windows"][window]
    result = {
        "window": window,
        "span": [lo, hi],
        "run_id": f"item34-{window}-{date.today().isoformat()}",
        "counts": counts,
        "grid_sessions": len(scan.grid),
        "declared": {
            "cost_per_side": ratiostudy.COST_SIDE,
            "sides": ratiostudy.SIDES,
            "cost_stress": ratiostudy.COST_STRESS,
            "min_sessions": ratiostudy.MIN_SESSIONS,
            "min_holdout_sessions": ratiostudy.MIN_HOLDOUT_SESSIONS,
            "horizon": HORIZON,
            "entry_lag_sessions": ENTRY_LAG,
            "resamples": ratiostudy.RESAMPLES,
        },
        **res,
    }
    result["passed"] = ratiostudy.passed(res)
    path = out / f"results-{window}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"window": window, "verdict": result["verdict"], "file": str(path)}))
    return 0


def cmd_report(args) -> int:
    out = Path(args.out_dir) if args.out_dir else _out_dir()
    src = out / f"results-{args.window}.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------------------------------
# results-<window>.md beside the json, for the console's studies tab.


def _pct(x: float | None, nd: int = 2) -> str:
    """A return as signed percent points: 0.0123 -> +1.23."""
    if x is None:
        return ""
    v = round(100 * x, nd)
    return f"{'−' if v < 0 else '+' if v > 0 else ''}{abs(v):.{nd}f}"


CHECK_LABELS = {
    "mean_positive": "mean net spread above zero",
    "lower_bound_positive": "one-sided 95% lower bound above zero",
    "first_half_positive": "positive in the first time-half",
    "second_half_positive": "positive in the second time-half",
    "name_halves_positive": "positive in each name-half",
    "stressed_costs_positive": "positive at 1.5x the costs",
    "no_reports_positive": "positive with report windows removed",
}


def findings(result: dict) -> list[str]:
    out = []
    if result["window"] == "holdout":
        out.append(
            f"**The holdout** was read once: mean net spread {_pct(result['mean_net'])} points a session"
            f" over {result['sessions']} sessions. A fail here closes the item."
        )
        return out
    failed = [CHECK_LABELS[k] for k, ok in result["checks"].items() if not ok]
    out.append(
        f"**Spread:** the lowest decile of the ratio less the highest returned {_pct(result['mean_gross'])}"
        f" points a session before costs and {_pct(result['mean_net'])} after {_pct(result['cost'])} points"
        f" of costs, over {result['sessions']} grid sessions with both deciles filled."
    )
    out.append(
        "**Conditions:** "
        + ("all hold." if not failed else "not met: " + "; ".join(failed) + ".")
        + f" Lower bound {_pct(result['lower_bound'])}."
    )
    lo = result["long_only"]
    out.append(
        f"**Long only** (lowest decile against the session's mean, one leg of costs): {_pct(lo['mean_net'])}"
        " points a session. No borrow cost is charged on the short leg."
    )
    if result["verdict"] == "no verdict":
        out.append(f"**No verdict:** fewer than {ratiostudy.MIN_SESSIONS} sessions with both deciles filled.")
    return out


def report(result: dict) -> str:
    window = result["window"]
    lines = [
        f"# Item 34: the option-to-stock premium ratio ({window})",
        "",
        f"**Verdict:** {result['verdict']}. Window {result['span'][0]} to {result['span'][1]},"
        f" {result['grid_sessions']} grid sessions.",
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **spread**: the lowest decile of option premium over stock dollar volume, less the highest"
        " decile, of the five-session excess return over SPY (from the close after the day), in percent"
        " points, averaged over grid sessions; **net** is after 0.10% a side on each of the two legs.",
        "- **terciles**: the net spread read inside each third of the session by dollar volume or by the"
        " prior five sessions' return, lowest third first. Reported, not judged.",
    ]
    if window == "holdout":
        gross, netted = _pct(result["mean_gross"]), _pct(result["mean_net"])
        lines += ["", "## Holdout", "", "| sessions | gross | net |", "|---|---|---|"]
        lines.append(f"| {result['sessions']} | {gross} | {netted} |")
        return "\n".join(lines) + "\n"
    nh, st, nr, lo = result["name_halves"], result["stressed"], result["no_reports"], result["long_only"]
    lines += [
        "", "## Conditions", "",
        "| condition | holds |", "|---|---|",
        *[f"| {CHECK_LABELS[k]} | {'yes' if ok else 'no'} |" for k, ok in result["checks"].items()],
        "", "## Readings", "",
        "| reading | sessions | mean net |", "|---|---|---|",
        f"| all names | {result['sessions']} | {_pct(result['mean_net'])} |",
        f"| first half | | {_pct(result['halves']['first'])} |",
        f"| second half | | {_pct(result['halves']['second'])} |",
        *[f"| {k.replace('_', ' ')} | {v['sessions']} | {_pct(v['mean'])} |" for k, v in nh.items()],
        f"| costs x{st['multiple']} | {result['sessions']} | {_pct(st['mean'])} |",
        f"| report windows removed | {nr['sessions']} | {_pct(nr['mean'])} |",
        f"| long only | {lo['sessions']} | {_pct(lo['mean_net'])} |",
    ]  # fmt: skip
    for title, key in (
        ("Dollar volume", "dollar_volume_terciles"),
        ("Prior five sessions' return", "prior_return_terciles"),
    ):
        lines += [
            "",
            f"## Terciles of {title.lower()}",
            "",
            "| tercile | sessions | mean net |",
            "|---|---|---|",
        ]
        lines += [f"| {t['tercile']} | {t['sessions']} | {_pct(t['mean'])} |" for t in result[key]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("study", help="read the stores and write the window's result")
    st.add_argument("--window", choices=WINDOWS, required=True)
    st.add_argument("--plan")
    st.add_argument("--flow-db")
    st.add_argument("--out-dir")
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="the window's results .md from its .json")
    rp.add_argument("--window", choices=WINDOWS, required=True)
    rp.add_argument("--out-dir")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
