"""How many sessions a CVaR needs before it means anything, calibrated on the modelled bwb history.

    python scripts/bwb_cvar_calibration.py --cboe-dir .tmp/cboe            # validates first
    python scripts/bwb_cvar_calibration.py --cboe-dir .tmp/cboe --json .tmp/cvar.json

Read-only. Needs VIX9D.csv and SPX.csv (`bwb_history_backtest.py fetch DIR`).

**The question** (docs/metrics-plan.md, open questions): `core.metrics.CVAR_MIN_SESSIONS = 20` was
proposed, not measured, and the plan said to revisit it once a module had ~20 sessions inside one
era. Fifteen years of modelled bwb sessions answer the sampling half of that now: for a window of n
sessions, how far is the CVaR it reads from the long-run tail?

**Method.** Per-settlement-session nets (bwb's `closed_session` is the expiration Friday, so one bwb
session is one week), from `bwb_history_backtest` scaled to one index level. For every contiguous
window of n sessions, `core.metrics.cvar` (worst `CVAR_QUANTILE` of sessions, at least one) is
compared with two references:
- the whole history's CVaR (stationary assumption: one long-run tail);
- the CVaR of the 52 sessions that FOLLOW the window: does the number read after n sessions
  predict next year's tail?

**The criterion, declared before the run:** the smallest n at which at least 80% of windows read
within +-50% of the whole-history CVaR. `understated` is the share of windows reading less than half
the long-run loss (a window that has not met its tail yet), which is the error that matters for a
gate: it lets a book through.

The windows overlap, so the shares are descriptive, not independent draws. The series is a model
(`bwb_history_backtest`'s docstring), so this calibrates the sampling behaviour of a weekly
short-premium ladder, not any module's true tail.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bwb_history_backtest as bt  # noqa: E402
from cherrypick.core import metrics  # noqa: E402

WINDOWS = (10, 14, 20, 26, 30, 40, 52, 78, 104, 156)
FORWARD = 52
WITHIN = 0.50
CRITERION_SHARE = 0.80


def _cvar(values: list[float]) -> float:
    return metrics.cvar(values, min_n=1)


def calibrate(series: list[float], windows=WINDOWS) -> dict:
    ref = _cvar(series)
    out = {"sessions": len(series), "cvar_all": ref, "k_at": {}, "windows": {}}
    for n in windows:
        if n + FORWARD > len(series):
            continue
        ratios, fwd_ratios, nonneg = [], [], 0
        for i in range(len(series) - n + 1):
            w = series[i : i + n]
            c = _cvar(w)
            ratios.append(c / ref)
            nonneg += c >= 0
            if i + n + FORWARD <= len(series):
                f = _cvar(series[i + n : i + n + FORWARD])
                if c < 0:
                    fwd_ratios.append(f / c)
        m = len(ratios)
        out["k_at"][n] = max(1, int(n * metrics.CVAR_QUANTILE))
        out["windows"][n] = {
            "count": m,
            "median_ratio": round(statistics.median(ratios), 2),
            "within_50pct": round(sum(abs(r - 1) <= WITHIN for r in ratios) / m, 3),
            "understated": round(sum(r < 0.5 for r in ratios) / m, 3),
            "overstated": round(sum(r > 1.5 for r in ratios) / m, 3),
            "no_loss_tail": round(nonneg / m, 3),
            "next_year_worse_by_half": round(sum(r > 1.5 for r in fwd_ratios) / len(fwd_ratios), 3)
            if fwd_ratios
            else None,
            "next_year_median_ratio": round(statistics.median(fwd_ratios), 2) if fwd_ratios else None,
        }
    passing = [n for n, w in out["windows"].items() if w["within_50pct"] >= CRITERION_SHARE]
    out["criterion"] = f">= {CRITERION_SHARE:.0%} of windows within +-{WITHIN:.0%} of the whole-history CVaR"
    out["min_sessions_meeting_criterion"] = min(passing) if passing else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cboe-dir")
    ap.add_argument("--ledger")
    ap.add_argument("--params")
    ap.add_argument("--scale", type=float, default=7000.0)
    ap.add_argument("--start", default="2011-01-03")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    if args.skip_validate:
        print("UNVALIDATED: the model was not checked against the paper ledger on this run")
    else:
        bt.require_validation(bt.context("spx", args.cboe_dir, args.params), args.ledger)
    rows = [
        r
        for r in bt.backtest(bt.context("auto", args.cboe_dir, args.params), args.start, args.end, args.scale)
        if not r.get("refused")
    ]
    res = {"quantile": metrics.CVAR_QUANTILE, "current_min_sessions": metrics.CVAR_MIN_SESSIONS, "arms": {}}
    for arm in bt.ARMS:
        series = list(bt.session_nets(rows, arm).values())
        res["arms"][arm] = calibrate(series)
        # How close the tail sits to the structure's own cap: each session's summed worst case
        # (BWB plus a fired add-on), median over sessions.
        caps: dict[str, float] = {}
        for r in rows:
            caps[r["exp"]] = caps.get(r["exp"], 0.0) + r["risk_" + arm]
        res["arms"][arm]["median_session_worst_case"] = round(statistics.median(caps.values()))
    for arm, c in res["arms"].items():
        print(
            f"\n{arm}: {c['sessions']} weekly sessions, whole-history CVaR ${c['cvar_all']:,.0f}"
            f" (median session worst case ${c['median_session_worst_case']:,});"
            f" smallest n meeting the criterion: {c['min_sessions_meeting_criterion']}"
        )
        print(
            "     n   k  median  within50  understated  overstated  no-loss-tail"
            "  next-yr worse>50%  next-yr median"
        )
        for n, w in c["windows"].items():
            print(
                f"  {n:>4} {c['k_at'][n]:>3}  {w['median_ratio']:>6}  {w['within_50pct']:>8.0%}"
                f"  {w['understated']:>11.0%}  {w['overstated']:>10.0%}  {w['no_loss_tail']:>12.0%}"
                f"  {w['next_year_worse_by_half'] or 0:>17.0%}  {w['next_year_median_ratio']}"
            )
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
