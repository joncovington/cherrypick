"""Item 42, the break-even condor at its own wing widths (docs/edge-hypotheses-plan.md): `be-hourly-1x`
exactly as docs/theta-profits-be-condor.md gives it, 25-40-point wings with credit equalized, with and
without the stabilization gate, over the wide SPX store (`SPX-60s-wide.db`, 40 strikes each side).

    python scripts/meic_be_condor_item42.py --step coverage
    python scripts/meic_be_condor_item42.py --step study --window design|holdout [--workers 8]

Runs the backtest's own MEIC replay (`cherrypick.backtest.runner`) over the wide store, provisionally:
that store is never validated. Writes under the backtest data dir (results/research/item42/), never
into the repo: the inputs are licensed.

**The coverage step** reads quotes only, no P&L, and must come first: the wide store's complete
sessions in each window against the plain store's, and, at each hour 10:00-15:00, the share of 12-delta
shorts (put and call) whose 40-point wing has a quote in the same sample. A wing the store cannot quote
is a condor the replay cannot open, so this is what the store is good for before anything is judged.

**The arms, as declared.** `be` is the profile as documented. Judged: `be` on its own net, and
`be-multi` and `be-perday` against `be`. If an item 41 `be10` gated arm passed its design window, the
passing variant replaces the one it matches (`be10-multi-3b` passing makes `be-multi` three bars).
Reported: `be-skip` (the random-skip placebo at item 41's measured rate; the spot is the same in both
stores) and `be10` (the same rule at 10-point wings, over the same sessions: the wings or the rule).

**The verdict**, one session one observation (a session with no entry counts at zero). `be`: own mean
net above zero with a one-sided t above 2, positive in both halves, still positive at 1.5x fees.
`be-multi`, `be-perday`: item 41's rule against `be` (paired mean above zero, t above 2, both halves,
own net above zero and at 1.5x fees). Holm across the three. The holdout: `be`'s own mean and t, the
gated arms' paired mean and t. At least 100 sessions.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest import paths, runner, store
from cherrypick.backtest import settings as _settings
from cherrypick.core import home
from cherrypick.core import stats as core_stats

ITEM = 42
TAG = "wide"
SYMBOL = "SPX"
CADENCE = 60
ET = ZoneInfo("America/New_York")
SHORT_DELTA = 0.12
WIDEST = 40
HOURS = (10, 11, 12, 13, 14, 15)

# Item 41's arms and statistics, one definition: this item is that one at the documented widths.
_spec = importlib.util.spec_from_file_location(
    "meic_stabilization_item41", Path(__file__).resolve().parent / "meic_stabilization_item41.py"
)
i41 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i41)

BE = {**i41.BE10, "wing_widths_by_symbol": {"SPX": [25, 30, 35, 40]}, "equalize_credit": True}
BASE = "be"
PLACEBO = "be-skip"
REFERENCE = "be10"
# the gated arm -> the item 41 arm it matches, and that arm's oracle and bars
GATED = {"be-multi": ("be10-multi", "multi", 2), "be-perday": ("be10-perday", "perday", 2)}


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wide(settings: dict) -> dict:
    return store.tagged(settings, TAG)


def gated_variants(item41_design: dict | None) -> dict[str, tuple[str, int]]:
    """Each gated arm's oracle and bar count. An item 41 variant that passed replaces the arm it
    matches: `be10-multi-3b` passing (and `be10-multi` not) makes `be-multi` three bars."""
    out = {arm: (oracle, bars) for arm, (_, oracle, bars) in GATED.items()}
    verdicts = {a: v.get("verdict") for a, v in ((item41_design or {}).get("arms") or {}).items()}
    if verdicts.get("be10-multi-3b") == "pass" and verdicts.get("be10-multi") != "pass":
        out["be-multi"] = ("multi", 3)
    return out


def study_registry(registry: dict, placebo_rate: float, variants: dict[str, tuple[str, int]]) -> dict:
    """The machine's MEIC arm registry with item 42's arms added."""
    profiles = dict(registry.get("profiles") or {})
    profiles[BASE] = dict(BE)
    for arm, (oracle, bars) in variants.items():
        profiles[arm] = {**BE, **i41.GATE, "stabilization_oracle": oracle, "stabilization_bars": bars}
    profiles[PLACEBO] = {
        **BE,
        "stabilization_placebo_rate": placebo_rate,
        "stabilization_placebo_seed": ITEM,
    }
    profiles[REFERENCE] = dict(i41.BE10)
    return {**registry, "profiles": profiles}


# ── coverage step ────────────────────────────────────────────────────────────


def wing_inside(quotes: list[tuple[int, float, float | None]]) -> dict[str, bool | None]:
    """For one sample's quotes (side, strike, delta): whether the 12-delta put's and call's 40-point
    wings are quoted in it. None for a side with no delta to pick a short by."""
    out: dict[str, bool | None] = {}
    for name, side, sign in (("put", store.SIDES["PUT"], -1), ("call", store.SIDES["CALL"], 1)):
        legs = [(k, d) for s, k, d in quotes if s == side and d is not None]
        if not legs:
            out[name] = None
            continue
        short = min(legs, key=lambda kd: abs(abs(kd[1]) - SHORT_DELTA))[0]
        have = {k for s, k, _ in quotes if s == side}
        out[name] = (short + sign * WIDEST) in have
    return out


def _session_coverage(conn: sqlite3.Connection, day: str) -> list[dict[str, bool | None]]:
    samples = conn.execute(
        "SELECT id, ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? ORDER BY ts", (SYMBOL, day)
    ).fetchall()
    out = []
    for hh in HOURS:
        target = datetime.fromisoformat(day).replace(hour=hh, tzinfo=ET).timestamp()
        sid = next((i for i, ts in samples if ts >= target), None)
        if sid is None:
            continue
        quotes = conn.execute(
            "SELECT side, strike, delta FROM gex_quotes WHERE sample_id = ?", (sid,)
        ).fetchall()
        out.append(wing_inside(quotes))
    return out


def _share(xs: list[bool | None]) -> float | None:
    known = [x for x in xs if x is not None]
    return round(sum(known) / len(known), 4) if known else None


def coverage(settings: dict) -> dict:
    doc: dict = {
        "item": ITEM,
        "step": "coverage",
        "store": str(store.db_path(wide(settings), SYMBOL, CADENCE)),
    }
    for window, (start, end) in settings["windows"].items():
        row: dict = {"span": [start, end]}
        for label, s in (("plain", settings), ("wide", wide(settings))):
            path = store.db_path(s, SYMBOL, CADENCE)
            if not path.exists():
                row[f"{label}_sessions"] = 0
                continue
            conn = store.connect(s, SYMBOL, CADENCE)
            try:
                days = [d for d, _ in runner.sessions_in(conn, SYMBOL, start, end)]
                row[f"{label}_sessions"] = len(days)
                # Both stores: what the wide fetch adds over the plain one's 20 strikes.
                marks = [m for d in days for m in _session_coverage(conn, d)]
                row[f"{label}_hourly_marks"] = len(marks)
                row[f"{label}_put_wing_inside"] = _share([m["put"] for m in marks])
                row[f"{label}_call_wing_inside"] = _share([m["call"] for m in marks])
            finally:
                conn.close()
        doc[window] = row
    doc["note"] = (
        f"share of hourly {SHORT_DELTA:g}-delta shorts ({HOURS[0]}:00-{HOURS[-1]}:00) whose {WIDEST}-point "
        "wing is quoted in the same sample; quotes only, no P&L"
    )
    (out_dir() / "coverage.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


# ── study step ───────────────────────────────────────────────────────────────


def judge(tests: dict[str, dict], holdout: bool) -> None:
    """`be` on its own net, the gated arms against it; Holm across all three."""
    pvals = {
        k: (i41.p_one(v["own"]["t"], v["own"]["n"]) if k == BASE else v["vs_base"]["p"])
        for k, v in tests.items()
    }
    holm = core_stats.holm(pvals, alpha=i41.ALPHA)
    for k, v in tests.items():
        o = v["own"]
        v["holm_ok"] = holm[k]
        if k != BASE:
            i41.judge({k: v}, holdout)  # item 41's rule; its Holm (over one arm) becomes this item's
            v["holm_ok"] = holm[k]
            if "holm" in v.get("checks", {}):
                v["checks"]["holm"] = holm[k]
                v["verdict"] = "pass" if all(v["checks"].values()) else "fail"
            continue
        if o["n"] < i41.MIN_SESSIONS:
            v["verdict"], v["why"] = "no verdict", f"{o['n']} sessions < {i41.MIN_SESSIONS}"
            continue
        checks = {
            "own_mean_positive": (o["mean_net"] or 0) > 0,
            "own_t_above_bar": (o["t"] or 0) > i41.T_BAR,
        }
        if not holdout:
            checks |= {
                "holm": holm[k],
                "own_both_halves_positive": all((x or 0) > 0 for x in o["halves"].values()),
                "own_survives_1_5x_fees": o["cost_stress"]["survives"],
            }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def _item41(window: str) -> dict | None:
    files = sorted((paths.results_dir() / "research" / "item41").glob(f"study-{window}-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def coverage_refusal(doc: dict | None, window: str) -> str | None:
    """Why the study may not run yet: no coverage read, or a wide store with no session in the
    window (a run over it would replay the plain store's absence as a flat arm)."""
    if doc is None:
        return "run --step coverage first: the store's reach is reported before any P&L"
    if not (doc.get(window) or {}).get("wide_sessions"):
        return f"the wide store has no {window} session in the coverage read: fetch it, then re-run coverage"
    return None


def study(settings: dict, window: str, workers: int) -> dict:
    i41.require_gate()
    cov = out_dir() / "coverage.json"
    why = coverage_refusal(json.loads(cov.read_text(encoding="utf-8")) if cov.exists() else None, window)
    if why:
        raise SystemExit(why)
    plan41 = paths.results_dir() / "research" / "item41" / "plan.json"
    if not plan41.exists():
        raise SystemExit("item 41's plan step has not run: the placebo's rate comes from it")
    rate = json.loads(plan41.read_text(encoding="utf-8"))["refusal_rate"]
    variants = gated_variants(_item41("design"))
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    reg_path = out_dir() / "meic.risk.json"
    reg_path.write_text(json.dumps(study_registry(registry, rate, variants), indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    holdout = window == "holdout"
    # be10's holdout was spent by item 41; its design sessions here are the same rule over this store.
    arms = [BASE, *variants, PLACEBO] + ([] if holdout else [REFERENCE])
    report = runner.run(
        wide(settings),
        arms,
        window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = settings["windows"][window]
    per_arm = i41.session_rows(report["run_id"])
    base = per_arm.get(BASE, {})
    tests = {BASE: {"own": i41.own(base, start, end)}}
    for arm in variants:
        tests[arm] = {
            "base": BASE,
            "own": i41.own(per_arm.get(arm, {}), start, end),
            "vs_base": i41.paired(per_arm.get(arm, {}), base, start, end),
        }
    judge(tests, holdout)
    reference = {
        PLACEBO: i41.own(per_arm.get(PLACEBO, {}), start, end),
        f"{PLACEBO}_vs_{BASE}": i41.paired(per_arm.get(PLACEBO, {}), base, start, end),
        **{
            f"{a}_vs_{PLACEBO}": i41.paired(per_arm.get(a, {}), per_arm.get(PLACEBO, {}), start, end)
            for a in variants
        },
    }
    if not holdout:
        reference[REFERENCE] = i41.own(per_arm.get(REFERENCE, {}), start, end)
        reference[f"{BASE}_vs_{REFERENCE}"] = i41.paired(base, per_arm.get(REFERENCE, {}), start, end)
    doc = {
        "item": ITEM,
        "window": window,
        "span": [start, end],
        "store": str(store.db_path(wide(settings), SYMBOL, CADENCE)),
        "run_id": report["run_id"],
        "provisional": True,
        "placebo_rate": rate,
        "gated_variants": {a: {"oracle": o, "bars": b} for a, (o, b) in variants.items()},
        "refused_sessions": len(report.get("refused") or {}),
        "reference": reference,
        "arms": tests,
        "verdict": "pass" if any(v.get("verdict") == "pass" for v in tests.values()) else "fail",
    }
    path = out_dir() / f"study-{window}-{start}-{end}.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return {
        "written": str(path),
        "verdict": doc["verdict"],
        **{a: v.get("verdict") for a, v in tests.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", required=True, choices=("coverage", "study"))
    ap.add_argument("--window", choices=("design", "holdout"), default="design")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    s = _settings.load(None)
    out = coverage(s) if args.step == "coverage" else study(s, args.window, args.workers)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
