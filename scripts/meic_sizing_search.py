"""The MEIC sizing search: which arm of a finished study, at which risk per session, compounds best per
unit of drawdown from a given account, chosen on the design window and confirmed once on the holdout.

    python scripts/meic_sizing_search.py --capital DOLLARS [--study item23] [--arms a,b,...]
        [--risks 1,2,...] [--max-cap 90] [--json PATH]

A SIZING LENS, not a study verdict: it reads a study's existing runs (no replay) through the run cards'
own functions (`cards.arm_inputs`, `cards.compounded` with the `stop` unit, `cards.sizing`), and it never
changes live config or moves the study's verdict. MEIC history is provisional until it unlocks.

**The objective, declared 2026-10-31:** the compounded lens's whole-contract CAGR over its max drawdown
(CAGR / max drawdown), starting from `--capital`. Excluded: a combination whose implied 99th-percentile
drawdown cap (its starting units x one unit's p99 drawdown, as a share of the account) exceeds
`--max-cap` percent, one that ran the account to zero, and one that never sized a whole contract.

**One look at the holdout.** Every combination is scored on the design window only; the single pick is
then evaluated on the holdout, once, with the live rule at the same risk beside it as a reference. A
second look at other combinations there would turn the holdout into more search: a further check needs
new sessions.

Writes under the backtest data dir (results/research/sizing-search/), never into the repo: the inputs
are licensed.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

from cherrypick.backtest import cards, paths

SCHEMA = "meic_ic"
LIVE = "live-1400"
ARMS = (LIVE, "d12", "d16", "d20", "d25", "d30")
RISKS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0)
MAX_CAP = 90.0


def evaluate(series: list[dict], by_session: dict[str, list[dict]], capital: float, risk_pct: float) -> dict:
    """One arm at one risk per session: the lens's whole-contract figures and the cap that size implies."""
    lens = cards.compounded(series, by_session, capital, risk_pct, "stop")
    size = cards.sizing(series, by_session, capital, 100.0)
    if lens is None or size is None or size.get("insufficient"):
        return {"risk_pct": risk_pct, "eligible": False, "why": "too little history"}
    w = lens["whole"]
    unit_risk = size.get("unit_risk_stop_tail") or size.get("unit_risk_max_loss")
    units0 = math.floor(capital * risk_pct / 100 / unit_risk) if unit_risk else 0
    cap = 100 * units0 * size["drawdown_p99_per_unit"] / capital if units0 else 0.0
    mar = w["cagr"] / w["max_drawdown_pct"] if w["cagr"] is not None and w["max_drawdown_pct"] else None
    return {
        "risk_pct": risk_pct,
        "units_start": units0,
        "units_max": w["units_max"],
        "implied_cap_pct": round(cap, 1),
        "cagr": w["cagr"],
        "max_drawdown_pct": w["max_drawdown_pct"],
        "mar": None if mar is None else round(mar, 3),
        "stopped_on": w["stopped_on"],
    }


def eligible(row: dict, max_cap: float) -> tuple[bool, str | None]:
    """Whether a design row may be picked, and why not."""
    if row.get("why"):
        return False, row["why"]
    if row["stopped_on"]:
        return False, "ran the account to zero"
    if row["units_max"] == 0:
        return False, "never sized a whole contract"
    if row["implied_cap_pct"] > max_cap:
        return False, f"implied cap over {max_cap:g}%"
    if row["mar"] is None:
        return False, "no drawdown to divide by"
    return True, None


def pick(rows: list[dict], max_cap: float) -> dict | None:
    """The eligible design row with the highest CAGR / max drawdown; None when none is eligible."""
    ok = [r for r in rows if eligible(r, max_cap)[0]]
    return max(ok, key=lambda r: r["mar"]) if ok else None


def study_runs(study: str) -> dict[str, str]:
    """{"design": run_id, "holdout": run_id} from a study's result files."""
    folder = paths.results_dir() / "research" / study
    runs = {}
    for f in sorted(folder.glob("study-*.json")):
        window = json.loads(f.read_text(encoding="utf-8")).get("window") or f.name.split("-")[1]
        runs[window] = json.loads(f.read_text(encoding="utf-8"))["run_id"]
    missing = {"design", "holdout"} - set(runs)
    if missing:
        raise SystemExit(f"{study} has no {' or '.join(sorted(missing))} result under {folder}")
    return runs


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--capital", required=True, type=float, help="the starting account, in dollars")
    ap.add_argument("--study", default="item23", help="the study whose runs to read (default item23)")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--risks", default=",".join(f"{r:g}" for r in RISKS), help="risk per session, percent")
    ap.add_argument(
        "--max-cap", type=float, default=MAX_CAP, help="the largest implied p99 drawdown cap, percent"
    )
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if args.capital <= 0:
        ap.error("--capital must be positive")
    arms = [a for a in args.arms.split(",") if a]
    risks = [float(r) for r in args.risks.split(",") if r]

    runs = study_runs(args.study)
    series, trades = cards.arm_inputs(runs["design"], SCHEMA, arms)
    design = []
    for arm in arms:
        for r in risks:
            row = {"arm": arm, **evaluate(series[arm], trades.get(arm, {}), args.capital, r)}
            row["eligible"], row["why_not"] = eligible(row, args.max_cap)
            design.append(row)
    best = pick(design, args.max_cap)

    print(f"DESIGN window ({runs['design']}): choose here. cap = implied p99 drawdown, % of the account")
    print(f"{'arm':>10} {'risk':>5} {'units':>5} {'cap':>5} {'CAGR':>8} {'maxDD':>7} {'CAGR/DD':>7}  note")
    for r in design:
        if r.get("why"):
            print(f"{r['arm']:>10} {r['risk_pct']:>4g}%  {r['why']}")
            continue
        mar = "-" if r["mar"] is None else f"{r['mar']:.2f}"
        print(
            f"{r['arm']:>10} {r['risk_pct']:>4g}% {r['units_start']:>5} {r['implied_cap_pct']:>4.0f}% "
            f"{_pct(r['cagr']):>8} {_pct(r['max_drawdown_pct']):>7} {mar:>7}  {r['why_not'] or ''}"
        )

    holdout = None
    if best is None:
        print("\nNo eligible combination: nothing to confirm on the holdout.")
    else:
        h_series, h_trades = cards.arm_inputs(runs["holdout"], SCHEMA, sorted({best["arm"], LIVE}))
        holdout = {
            "pick": evaluate(
                h_series[best["arm"]], h_trades.get(best["arm"], {}), args.capital, best["risk_pct"]
            ),
            "live_reference": evaluate(
                h_series[LIVE], h_trades.get(LIVE, {}), args.capital, best["risk_pct"]
            ),
        }
        print(
            f"\nPICK (design): {best['arm']} at {best['risk_pct']:g}% risk/session, CAGR/DD {best['mar']:.2f}"
        )
        print(f"HOLDOUT ({runs['holdout']}), the pick only, evaluated once:")
        for name, e in (("pick", holdout["pick"]), (f"{LIVE} (reference)", holdout["live_reference"])):
            print(
                f"  {name}: units {e.get('units_start')}, CAGR {_pct(e.get('cagr'))}, "
                f"max DD {_pct(e.get('max_drawdown_pct'))}, CAGR/DD {e.get('mar')}, "
                f"stopped {e.get('stopped_on') or '-'}"
            )

    doc = {
        "study": args.study,
        "runs": runs,
        "capital": args.capital,
        "max_cap_pct": args.max_cap,
        "objective": "whole-contract CAGR / max drawdown of the compounded lens (stop unit)",
        "design": design,
        "pick": best,
        "holdout": holdout,
        "lens_not_verdict": True,
    }
    out_dir = paths.results_dir() / "research" / "sizing-search"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = Path(args.json) if args.json else out_dir / f"{args.study}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(f"\nwritten {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
