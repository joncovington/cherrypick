"""Item 40, legged flies without the strand tail (docs/edge-hypotheses-plan.md): a fixed stop on an
uncompleted vertical, and the whole fly bought at once, each against flies' `control`.

    python scripts/flies_item40.py study --window design|holdout [--workers 8] [--json PATH]
    python scripts/flies_item40.py report [--json PATH]

Runs the backtest's own flies replay (`cherrypick.backtest.runner`, flies' own rules over the 60 s
vendor store), provisionally because flies history cannot unlock: its validation stopped at five
sessions when paper did. The holdout is spent by this run, once per arm (`unvalidated_holdout`), and
only for the arms that passed the design window. Everything is written under the backtest data dir
(results/research/item40/), never into the repo: the inputs are licensed.

**The arms, as declared.** `strand-stop` is `control` plus: a vertical not yet completed is bought back
at the natural, with the module's fees, on the first tick its loss reaches half its maximum loss.
`whole-fly` is `control`, but where it would sell its vertical it buys the butterfly it would complete
to (same body and wings) in one order at the module's entry price, held to settlement. Both are
registered in `cherrypick.flies.engine.ARMS` and set in a scratch copy of the machine's config.

**The verdict**, per arm, the observation one session (every replayed session; a session with no entry
nets zero): the mean paired difference against `control` above zero with a one-sided 95% lower bound
above zero (stationary bootstrap of the sessions, mean block 5, 20,000 resamples, seeded); Holm across
the two arms; the arm's own mean net above zero; the paired difference's lower bound and the own mean
above zero in each time-half; the same at 1.5x fees and slippage; then the holdout, with the mean
paired difference and the own mean above zero. Fewer than 300 sessions with an entry in an arm is no
verdict. The rivals (entries per arm, the result without control's five worst sessions, the stop's
fires and how many of its verticals control completed) are reported beside the figures, never judged.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
from datetime import date
from pathlib import Path

from cherrypick.core import home, robustness
from cherrypick.core import stats as core_stats

ARMS = ("strand-stop", "whole-fly")
STOP_FRACTION = 0.5
CADENCE = 60
MIN_ENTRY_SESSIONS = 300
BLOCK = 5
RESAMPLES = 20000
SEED = 40
ALPHA = 0.05
PASS_STRESS = 1.5
WORST = 5
HOLDOUT_PLANNED_SESSIONS = 320


# ------------------------------------------------------------------------------------------------
# The arms.


def study_config(base: dict) -> dict:
    """The machine's flies config with the two arms added, each `control` plus its one lever."""
    arms = dict(base.get("arms") or {})
    if not isinstance(arms.get("control"), dict):
        raise SystemExit("the flies config has no 'control' arm to build on")
    control = {k: v for k, v in arms["control"].items() if not k.startswith("_")}
    arms["strand-stop"] = {**control, "enabled": True, "strand_stop": {"max_loss_fraction": STOP_FRACTION}}
    arms["whole-fly"] = {**control, "enabled": True, "whole_fly": True}
    return {**base, "arms": arms}


# ------------------------------------------------------------------------------------------------
# Pure: the bootstrap and the window's figures.


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def bootstrap(
    series: dict[str, list[float]], *, resamples: int = RESAMPLES, block: float = BLOCK, seed: int = SEED
) -> dict[str, dict]:
    """Per series, the stationary bootstrap of its mean: `p`, the add-one share of resample means at or
    below zero, and `lower`, the one-sided 95% lower bound (the 5th percentile). Series of one length
    share their resamples, so a paired difference and its cost-stressed twin move together."""
    by_len: dict[int, list[str]] = {}
    for key, values in series.items():
        by_len.setdefault(len(values), []).append(key)
    out: dict[str, dict] = {}
    for n, keys in by_len.items():
        if n < 2:
            out.update({k: {"p": 1.0, "lower": None} for k in keys})
            continue
        rng = random.Random(seed)
        draws: dict[str, list[float]] = {k: [] for k in keys}
        for _ in range(resamples):
            idx = core_stats.stationary_indices(n, block, rng)
            for k in keys:
                v = series[k]
                draws[k].append(sum(v[j] for j in idx) / n)
        for k in keys:
            d = sorted(draws[k])
            out[k] = {
                "p": (sum(1 for m in d if m <= 0) + 1) / (resamples + 1),
                "lower": d[int(ALPHA * resamples)],
            }
    return out


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def stressed(row: dict, multiple: float = PASS_STRESS) -> float:
    """A session's net with fees and slippage scaled: net - (m - 1) x (fees + slippage). Slippage is
    already inside gross (the fill concedes it), so the stress charges the extra share."""
    return row["net"] - (multiple - 1.0) * (row["fees"] + row["slippage"])


def analyse(
    per_arm: dict[str, dict[str, dict]],
    start: str,
    end: str,
    *,
    resamples: int = RESAMPLES,
    arms: tuple[str, ...] = ARMS,
) -> dict:
    """One window's figures per arm from `per_arm[arm][day] = {net, fees, slippage, trades}` (control
    included). No judgement here: `judge` applies the declared bars."""
    control = per_arm.get("control", {})
    mid = midpoint(start, end)
    out: dict = {
        "control": {
            "sessions": len(control),
            "entry_sessions": sum(1 for r in control.values() if r["trades"] > 0),
            "trades": sum(r["trades"] for r in control.values()),
            "mean_net": _r(mean(r["net"] for r in control.values())),
        },
        "arms": {},
    }
    for arm in arms:
        mine = per_arm.get(arm, {})
        days = sorted(set(mine) & set(control))
        own = [mine[d]["net"] for d in days]
        diff = [mine[d]["net"] - control[d]["net"] for d in days]
        diff_stress = [stressed(mine[d]) - stressed(control[d]) for d in days]
        own_stress = [stressed(mine[d]) for d in days]
        first = [i for i, d in enumerate(days) if d < mid]
        second = [i for i, d in enumerate(days) if d >= mid]
        worst = set(sorted(days, key=lambda d: control[d]["net"])[:WORST])
        kept = [i for i, d in enumerate(days) if d not in worst]
        boot = bootstrap(
            {
                "full": diff,
                "stress": diff_stress,
                "first": [diff[i] for i in first],
                "second": [diff[i] for i in second],
            },
            resamples=resamples,
        )
        half = {}
        for name, idx in (("first", first), ("second", second)):
            half[name] = {
                "sessions": len(idx),
                "mean_net": _r(mean(own[i] for i in idx)),
                "mean_diff": _r(mean(diff[i] for i in idx)),
                "diff_lower": _r(boot[name]["lower"]),
            }
        out["arms"][arm] = {
            "sessions": len(days),
            "entry_sessions": sum(1 for d in days if mine[d]["trades"] > 0),
            "trades": sum(mine[d]["trades"] for d in days),
            "mean_net": _r(mean(own)),
            "mean_diff": _r(mean(diff)),
            "diff_lower": _r(boot["full"]["lower"]),
            "p": boot["full"]["p"],
            "halves": half,
            "stress": {
                "multiple": PASS_STRESS,
                "mean_net": _r(mean(own_stress)),
                "mean_diff": _r(mean(diff_stress)),
                "diff_lower": _r(boot["stress"]["lower"]),
            },
            "without_worst_control": {
                "dropped": sorted(worst),
                "mean_net": _r(mean(own[i] for i in kept)),
                "mean_diff": _r(mean(diff[i] for i in kept)),
            },
            "mde": {
                "design_n": robustness.planned_mde(diff, len(diff)),
                "holdout_planned_n": robustness.planned_mde(diff, HOLDOUT_PLANNED_SESSIONS),
            },
            "fees": _r(sum(mine[d]["fees"] for d in days)),
            "slippage": _r(sum(mine[d]["slippage"] for d in days)),
        }
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


def judge(block: dict, window: str) -> None:
    """The declared bars on one window's figures, in place: `checks` and `verdict` on each arm. The
    design window carries the full rule; the holdout only the sign of the paired difference and of the
    arm's own mean."""
    arms = block["arms"]
    holm = core_stats.holm({a: v["p"] for a, v in arms.items()}, alpha=ALPHA)
    for arm, v in arms.items():
        if v["entry_sessions"] < MIN_ENTRY_SESSIONS:
            v["verdict"] = "no verdict"
            v["why"] = f"{v['entry_sessions']} sessions with an entry < {MIN_ENTRY_SESSIONS}"
            continue
        if window == "design":
            checks = {
                "diff_lower_positive": _pos(v["diff_lower"]) and _pos(v["mean_diff"]),
                "holm": holm[arm],
                "own_net_positive": _pos(v["mean_net"]),
                "both_halves": all(
                    _pos(h["diff_lower"]) and _pos(h["mean_net"]) for h in v["halves"].values()
                ),
                "survives_1_5x": _pos(v["stress"]["diff_lower"]) and _pos(v["stress"]["mean_net"]),
            }
        else:
            checks = {"diff_positive": _pos(v["mean_diff"]), "own_net_positive": _pos(v["mean_net"])}
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def final_verdicts(windows: dict) -> dict[str, str]:
    """Each arm's word. No verdict is no verdict; a design fail is a fail whatever the holdout shows;
    a design pass waits for its holdout (no verdict until it is read), then stands or falls on it."""
    design = windows.get("design")
    out = {}
    for arm in ARMS:
        d = (design or {}).get("arms", {}).get(arm, {}).get("verdict")
        if d != "pass":
            out[arm] = d or "no verdict"
            continue
        h = windows.get("holdout", {}).get("arms", {}).get(arm, {}).get("verdict")
        out[arm] = "no verdict" if h is None else h if h in ("pass", "fail") else "no verdict"
    return out


def holdout_arms(results: dict | None) -> list[str]:
    """The arms whose design window passed: the only ones the holdout is read for."""
    design = (results or {}).get("windows", {}).get("design", {}).get("arms", {})
    return [a for a in ARMS if design.get(a, {}).get("verdict") == "pass"]


# ------------------------------------------------------------------------------------------------
# Pure: the rivals read off the replayed ledgers.


def ledger_facts(conn) -> dict[str, dict]:
    """One replayed session's ledger, per arm: the slippage its fills conceded (dollars), the
    verticals the stop closed, and the verticals that completed. Settled, unvoided rows only."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fly_positions)")}
    void = " AND void_reason IS NULL" if "void_reason" in cols else ""
    facts: dict[str, dict] = {}
    for arm, entry, center, slip, source, completed in conn.execute(
        "SELECT arm, entry_time, center, slippage_dollars, close_tag_source, completed_at "
        f"FROM fly_positions WHERE status = 'settled'{void}"
    ):
        f = facts.setdefault(arm, {"slippage": 0.0, "stops": [], "completed": []})
        f["slippage"] += float(slip or 0.0)
        if source == "stop":
            f["stops"].append((entry, center))
        if completed is not None:
            f["completed"].append((entry, center))
    return facts


def stop_reading(stops: list[tuple], control_completed: set[tuple]) -> dict:
    """The stop's fires and how many of the stopped verticals control (same session, same entry tick,
    same centre) went on to complete."""
    done = sum(1 for s in stops if s in control_completed)
    return {
        "fires": len(stops),
        "control_completed": done,
        "share_control_completed": None if not stops else round(done / len(stops), 3),
    }


# ------------------------------------------------------------------------------------------------
# The run.


def session_rows(run_id: str) -> dict[str, dict[str, dict]]:
    from cherrypick.backtest import results

    conn = results.connect()
    try:
        out: dict[str, dict[str, dict]] = {}
        for arm, day, net, fees, trades in conn.execute(
            "SELECT arm, session, net, fees, trades FROM session_results WHERE run_id = ?", (run_id,)
        ):
            out.setdefault(arm, {})[day] = {
                "net": net,
                "fees": fees or 0.0,
                "trades": trades or 0,
                "slippage": 0.0,
            }
        return out
    finally:
        conn.close()


def read_ledgers(run_id: str, per_arm: dict[str, dict[str, dict]]) -> dict:
    """Fold each replayed session's slippage into `per_arm` and collect the stop's fires. Returns the
    stop reading and the count of sessions whose ledger could not be read (their slippage stays zero,
    which can only make the 1.5x stress too kind: reported)."""
    from cherrypick.backtest import paths

    stops: list[tuple] = []
    completed: set[tuple] = set()
    missing = 0
    for day in sorted(per_arm.get("control", {})):
        db = paths.results_dir() / "runs" / run_id / f"{day}.db"
        if not db.is_file():
            missing += 1
            continue
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            facts = ledger_facts(conn)
        finally:
            conn.close()
        for arm, f in facts.items():
            if day in per_arm.get(arm, {}):
                per_arm[arm][day]["slippage"] = f["slippage"]
        stops += [(day, *s) for s in facts.get("strand-stop", {}).get("stops", [])]
        completed |= {(day, *c) for c in facts.get("control", {}).get("completed", [])}
    return {"stop": stop_reading(stops, completed), "ledgers_missing": missing}


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item40"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_study(args) -> int:
    from cherrypick.backtest import runner
    from cherrypick.backtest import settings as _settings

    d = out_dir()
    path = Path(args.json) if args.json else d / "results.json"
    holdout = args.window == "holdout"
    # A design run starts the file over: a holdout read for an earlier design is not this design's.
    prior = json.loads(path.read_text(encoding="utf-8")) if holdout and path.is_file() else None
    arms = holdout_arms(prior) if holdout else list(ARMS)
    if not arms:
        print(
            json.dumps({"written": None, "note": "no arm passed the design window: the holdout is not read"})
        )
        return 0
    base = json.loads(home.config_path("flies").read_text(encoding="utf-8"))
    cfg_path = d / "flies-config.json"
    cfg_path.write_text(json.dumps(study_config(base), indent=1), encoding="utf-8")
    os.environ["FLIES_CONFIG"] = str(cfg_path)

    s = _settings.load(None)
    report_ = runner.run(
        s,
        arms,
        args.window,
        cadence=CADENCE,
        provisional=True,
        spend_holdout=holdout,
        unvalidated_holdout=holdout,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    start, end = s["windows"][args.window]
    per_arm = session_rows(report_["run_id"])
    rivals = read_ledgers(report_["run_id"], per_arm)
    block = analyse(per_arm, start, end, arms=tuple(arms))
    judge(block, args.window)
    block.update(
        {
            "span": [start, end],
            "run_id": report_["run_id"],
            "refused_sessions": len(report_.get("refused") or {}),
            "rivals": rivals,
        }
    )
    doc = prior or {"item": 40}
    doc.setdefault("windows", {})[args.window] = block
    spans = {w: b["span"] for w, b in doc["windows"].items()}
    doc.update(
        {
            "provisional": True,
            "verdict": final_verdicts(doc["windows"]),
            "window": "; ".join(f"{w} {a}..{b}" for w, (a, b) in spans.items()),
            "run_id": report_["run_id"],
        }
    )
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(doc), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.

CHECKS = {
    "diff_lower_positive": ("Paired difference against control", "above zero, 95% lower bound above zero"),
    "holm": ("Holm across the two arms", "rejects"),
    "own_net_positive": ("Own mean net per session", "above zero"),
    "both_halves": ("Each half of the window", "difference lower bound and own mean above zero in both"),
    "survives_1_5x": ("At 1.5x fees and slippage", "difference lower bound and own mean above zero"),
    "diff_positive": ("Paired difference against control", "above zero"),
}


def _d(x: float | None) -> str:
    if x is None:
        return ""
    v = round(x, 2)
    return f"{'−' if v < 0 else '+' if v > 0 else ''}${abs(v):,.2f}"


def _word(v: str) -> str:
    return v if v in ("pass", "fail") else "no verdict"


def findings(doc: dict) -> list[str]:
    out = []
    design = doc.get("windows", {}).get("design")
    if not design:
        return out
    for arm, v in design["arms"].items():
        if v["verdict"] == "no verdict":
            out.append(f"**{arm}:** no verdict, {v['why']}.")
            continue
        failed = [CHECKS[k][0].lower() for k, ok in v["checks"].items() if not ok]
        out.append(
            f"**{arm}** (design): own mean net {_d(v['mean_net'])} a session, {_d(v['mean_diff'])} against"
            f" control (95% lower bound {_d(v['diff_lower'])}). "
            + (f"It misses on: {'; '.join(failed)}." if failed else "Every design check holds.")
        )
    stop = design.get("rivals", {}).get("stop")
    if stop:
        share = stop["share_control_completed"]
        out.append(
            f"**The stop:** fired {stop['fires']} times"
            + (
                f"; control completed {stop['control_completed']} of those verticals ({share:.0%}), which the"
                " stop turned into closed losses."
                if share is not None
                else "."
            )
        )
        if stop["fires"] == 0:
            out.append("A stop that never fired leaves `strand-stop` identical to control, and says nothing.")
    hold = doc.get("windows", {}).get("holdout")
    if hold:
        for arm, v in hold["arms"].items():
            out.append(
                f"**{arm}** (holdout, read once after a design pass): own mean net {_d(v['mean_net'])},"
                f" {_d(v['mean_diff'])} against control: {_word(v['verdict'])}."
            )
    return out


def report(doc: dict) -> str:
    verdicts = doc.get("verdict", {})
    lines = [
        "# Item 40: legged flies without the strand tail",
        "",
        "**Verdict:** " + "; ".join(f"{a} {_word(verdicts.get(a, 'no verdict'))}" for a in ARMS) + ".",
        "",
        "Two levers on flies' control, each beside it on the same sessions. **strand-stop** buys back a"
        f" vertical still uncompleted once its loss reaches {STOP_FRACTION:g} of its maximum loss."
        " **whole-fly** buys the butterfly control would complete to, in one order, and holds it. The runs"
        " are provisional: flies' replay cannot be validated, so they carry the vendor data's own"
        " unchecked error.",
        "",
        "Every figure is dollars at one contract, per session; a session with no entry nets zero. The"
        " paired difference is the arm's net less control's on the same session; its lower bound is a"
        f" one-sided 95% stationary bootstrap (mean block {BLOCK}, {RESAMPLES:,} resamples).",
    ]
    for wname, win in doc.get("windows", {}).items():
        a, b = win["span"]
        lines += [
            "",
            f"## {wname.capitalize()}: {a} to {b}",
            "",
            f"Control: {win['control']['sessions']} sessions, {win['control']['entry_sessions']} with an"
            f" entry, mean net {_d(win['control']['mean_net'])}. Sessions the replay refused:"
            f" {win['refused_sessions']}.",
            "",
            "| arm | verdict | sessions | with entry | trades | mean net | vs control | lower bound"
            " | half 1 | half 2 | at 1.5x | without worst 5 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for arm, v in win["arms"].items():
            h1, h2 = v["halves"]["first"], v["halves"]["second"]
            lines.append(
                f"| {arm} | {_word(v['verdict'])} | {v['sessions']} | {v['entry_sessions']} | {v['trades']}"
                f" | {_d(v['mean_net'])} | {_d(v['mean_diff'])} | {_d(v['diff_lower'])}"
                f" | {_d(h1['mean_diff'])} | {_d(h2['mean_diff'])} | {_d(v['stress']['mean_diff'])}"
                f" | {_d(v['without_worst_control']['mean_diff'])} |"
            )
        for arm, v in win["arms"].items():
            if "checks" not in v:
                continue
            lines += ["", f"### {arm}: the checks", "", "| check | needed | |", "|---|---|---|"]
            for key, ok in v["checks"].items():
                label, needed = CHECKS[key]
                lines.append(f"| {label} | {needed} | {'pass' if ok else 'fail'} |")
        mde = {a: v["mde"]["design_n"]["mde"] for a, v in win["arms"].items()}
        lines += [
            "",
            "Smallest paired difference this window could see (80% power, from its own spread): "
            + ", ".join(f"{a} {_d(m)}" for a, m in mde.items())
            + ".",
        ]
        rivals = win.get("rivals", {})
        if rivals.get("ledgers_missing"):
            lines += ["", f"{rivals['ledgers_missing']} sessions' ledgers could not be read for slippage."]
    lines += ["", "## What it says", "", *[f"- {x}" for x in findings(doc)]]
    lines += [
        "",
        "## How to read the tables",
        "",
        "- **with entry**: sessions in which the arm held at least one trade. Under "
        f"{MIN_ENTRY_SESSIONS} is no verdict.",
        "- **vs control**: the mean paired difference in dollars per session. **lower bound**: its 95%"
        " one-sided bound. **half 1 / half 2**: the mean difference in each time-half. **at 1.5x**: the"
        " mean difference with fees and slippage scaled by one and a half.",
        "- **without worst 5**: the mean difference excluding control's five worst sessions.",
        "- **Declared limits:** 60-second ticks; fills under the runner's fill model; a provisional"
        " replay; the whole fly fills at its modelled price, not a resting order's path.",
    ]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("study", help="run a window and state the verdict")
    st.add_argument("--window", required=True, choices=("design", "holdout"))
    st.add_argument("--workers", type=int, default=8)
    st.add_argument("--json", default=None)
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--json", default=None)
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
