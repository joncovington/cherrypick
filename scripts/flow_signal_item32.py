"""Item 32, large options orders as a signal (docs/flow-signal-plan.md, docs/edge-hypotheses-plan.md):
does a large, opened order (a call bought, a put sold, a put bought) predict the stock's next five
sessions against same-stage stocks without one?

    python scripts/flow_signal_item32.py study --window design
    python scripts/flow_signal_item32.py study --window holdout     # once, only for what passed design
    python scripts/flow_signal_item32.py report --window design     # results-design.md from the json

The rules are `cherrypick.backtest.flowstudy`'s (pure, tested on invented data); this file reads the
stores and writes the result. It reads the flow store, the local technicals bars and the earnings
calendar, and the plan file for the window (`scripts/flow_fetch_plan.py`), whose `grid_sessions` are
the sessions fetched: the grid is never recounted from the calendar, which a calendar fix has moved
once already. Results stay under the backtest data dir (results/research/item32/): the inputs are
licensed, so no figure goes into the repo.

The holdout is spent once: it refuses to run without a design result that carries a pass, writes a
spent marker before it reads anything, and refuses again once either file exists.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.backtest import flow, flowstudy, paths, settings
from cherrypick.backtest import flowfetch as _ff
from cherrypick.backtest.flowstudy import (
    CUTS,
    EARN_COST,
    EARN_CUT,
    EARN_SESSIONS,
    ENTRY_LAG,
    FIRST_TRIALS,
    HORIZON,
    MIN_EVENTS,
    MIN_SESSIONS,
    SECOND_TRIALS,
    TAGS,
)

WINDOWS = ("design", "holdout")
PLAN_FILES = {"design": "flow-plan.json", "holdout": "flow-plan-holdout.json"}


def _out_dir() -> Path:
    d = paths.results_dir() / "research" / "item32"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------------
# Reading the stores into a Scan.


def _reports(lo: str, hi: str) -> list[tuple[str, date, str | None]]:
    """(symbol, report date, timing) from the local Dolt earnings calendar, a month either side."""
    from cherrypick.technicals import land as _land

    a = (date.fromisoformat(lo) - timedelta(days=45)).isoformat()
    b = (date.fromisoformat(hi) + timedelta(days=45)).isoformat()
    dolt = _land._connect({**_land.DEFAULTS}, "earnings")
    try:
        cur = dolt.cursor()
        cur.execute(
            "SELECT act_symbol, date, `when` FROM earnings_calendar WHERE date BETWEEN %s AND %s", (a, b)
        )
        return [
            (s, d if isinstance(d, date) else date.fromisoformat(str(d)), w) for s, d, w in cur.fetchall()
        ]
    finally:
        dolt.close()


def stage_key(st) -> str:
    return "none" if st is None else f"{st.side}/{st.stage}"


def excess_return(closes: list, bench: list, i: int) -> float | None:
    """The stock's return over HORIZON sessions from the close ENTRY_LAG sessions after index i, less the
    benchmark's; None where any close is missing."""
    a, b = i + ENTRY_LAG, i + ENTRY_LAG + HORIZON
    if b >= len(closes) or None in (closes[a], closes[b], bench[a], bench[b]):
        return None
    return (closes[b] / closes[a] - 1.0) - (bench[b] / bench[a] - 1.0)


def build_scan(window: str, plan: dict, flow_db: Path) -> tuple[flowstudy.Scan, dict]:
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
    start = cal[max(0, next(i for i, d in enumerate(cal) if d >= lo) - 15)]

    reports = _reports(lo, hi)
    react_idx: dict[str, set[int]] = {}
    mine: dict[str, list[tuple[date, str | None]]] = {}
    for sym, d, w in reports:
        r = ec.reaction_day(d, w).isoformat()
        if r in index:
            react_idx.setdefault(sym, set()).add(index[r])
        if lo <= d.isoformat() <= hi:
            mine.setdefault(sym, []).append((d, w))

    conn = sqlite3.connect(f"file:{flow_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    by_sym: dict[str, list] = {}
    for r in conn.execute(
        "SELECT symbol, session, histogram FROM flow_days WHERE status = 'complete' ORDER BY symbol, session"
    ):
        by_sym.setdefault(r["symbol"], []).append((r["session"], json.loads(r["histogram"])))

    rows: dict[str, dict] = {s: {} for s in grid}
    cands: dict[str, dict] = {}
    complete = dict.fromkeys(grid, 0)
    reported: dict[str, set] = {s: set() for s in grid}
    events: list[dict] = []
    counts = {"names": len(by_sym), "grid_name_days": 0, "no_lookback": 0, "no_outcome": 0, "rows": 0}
    for sym, days in by_sym.items():
        cuts = flowstudy.lookback_cuts(days)
        want = [s for s, _ in days if start <= s <= hi]
        if not want:
            continue
        raw = {b.date: b.close for b in store.raw_bars(hist, sym)}
        adj = {b.date: b.close for b in store.adjusted_bars(hist, sym)}
        closes = [adj.get(d) for d in cal]
        orders: dict[str, list[dict]] = {}
        for o in conn.execute(
            "SELECT session, expiration, strike, right, side, first_ms, size, premium, oi_before, oi_after "
            "FROM flow_orders WHERE symbol = ? AND session >= ? AND session <= ?",
            (sym, start, hi),
        ):
            orders.setdefault(o["session"], []).append(dict(o))
        cands[sym] = {s: flowstudy.candidates(s, raw.get(s), orders.get(s, []), cuts[s]) for s in want}
        for s in want:
            if s not in grid_set:
                continue
            counts["grid_name_days"] += 1
            complete[s] += 1
            if cuts[s] is None:
                counts["no_lookback"] += 1
                continue
            i = index.get(s)
            ex = None if i is None else excess_return(closes, bench, i)
            if ex is None or closes[i] is None:
                counts["no_outcome"] += 1
                continue
            rows[s][sym] = (stage_key(stage.stage_at(closes, bench, i)), ex)
            counts["rows"] += 1
            if any(i + 2 <= j <= i + HORIZON + ENTRY_LAG for j in react_idx.get(sym, ())):
                reported[s].add(sym)
        for d, w in mine.get(sym, []):
            ri = index.get(ec.reaction_day(d, w).isoformat())
            if ri is None or ri < 1 or None in (closes[ri], closes[ri - 1], bench[ri], bench[ri - 1]):
                continue
            pre = [c for j in range(ri - EARN_SESSIONS, ri) for c in cands[sym].get(cal[j], ())]
            if pre:
                move = (closes[ri] / closes[ri - 1] - 1.0) - (bench[ri] / bench[ri - 1] - 1.0)
                events.append({"sym": sym, "react": cal[ri], "pre": pre, "excess": move})
    conn.close()
    scan = flowstudy.Scan(grid=grid, rows=rows, cands=cands, complete=complete, reported=reported,
                          index=index, events=events)  # fmt: skip
    return scan, counts


# ------------------------------------------------------------------------------------------------
# The result.


def verdicts(res: dict) -> dict[str, str]:
    return {k: c["verdict"] for fam in ("first", "second", "earnings") for k, c in res.get(fam, {}).items()}


def cmd_study(args) -> int:
    window = args.window
    out = Path(args.out_dir) if args.out_dir else _out_dir()
    out.mkdir(parents=True, exist_ok=True)
    why = flowstudy.spent(out, window)
    if why:
        print(why, file=sys.stderr)
        return 2
    carried = None
    if window == "holdout":
        carried = json.loads((out / "results-design.json").read_text(encoding="utf-8"))["passed"]
        (out / "holdout-spent.json").write_text(
            json.dumps({"spent_on": date.today().isoformat(), "carried": carried}), encoding="utf-8"
        )
    plan_path = Path(args.plan) if args.plan else paths.data_dir() / "flow" / PLAN_FILES[window]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    flow_db = Path(args.flow_db) if args.flow_db else _ff.db_path(settings.load())
    scan, counts = build_scan(window, plan, flow_db)
    res = flowstudy.evaluate(scan) if carried is None else flowstudy.evaluate_carried(scan, carried)
    lo, hi = settings.load()["windows"][window]
    result = {
        "window": window,
        "span": [lo, hi],
        "run_id": f"item32-{window}-{date.today().isoformat()}",
        "counts": counts,
        "grid_sessions": len(scan.grid),
        "declared": {
            "cuts": list(CUTS),
            "first_trials": FIRST_TRIALS,
            "second_trials": SECOND_TRIALS,
            "horizon": HORIZON,
            "entry_lag_sessions": ENTRY_LAG,
            "min_sessions": MIN_SESSIONS,
            "min_events": MIN_EVENTS,
            "earnings_cut": EARN_CUT,
            "earnings_cost": EARN_COST,
            "top_share": flow.TOP_SHARE,
            "lookback_sessions": flow.LOOKBACK_SESSIONS,
            "min_history": flowstudy.MIN_HISTORY,
        },  # fmt: skip
        **res,
    }
    result["verdict"] = verdicts(res)
    result["passed"] = flowstudy.passed(res)
    path = out / f"results-{window}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"window": window, "passed": result["passed"], "file": str(path)}))
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


def _iv(c: dict, key: str = "interval") -> str:
    iv = c.get(key)
    return "" if not iv else f"{_pct(iv[0])} to {_pct(iv[1])}"


def _cut(c: float) -> str:
    return f"${int(c) // 1000}k"


def _label(name: str, cell: dict) -> str:
    t = TAGS[cell["tag"]]["label"] if cell.get("tag") else name
    f = cell.get("filter")
    return t if not f else f"{f}: {t}" + ("" if f == "F3" else f" ({_cut(cell['cut'])})")


def findings(result: dict) -> list[str]:
    """The plain-English reading, worked out from the figures each time the study runs."""
    out = []
    first = result.get("first", {})
    passed = [k for k, c in first.items() if c["verdict"] == "pass"]
    nov = [k for k, c in first.items() if c["verdict"] == "no verdict"]
    if result["window"] == "holdout":
        out.append(
            f"**The holdout** was read once, for the {len(result['verdict'])} cells that passed design:"
            f" {sum(v == 'pass' for v in result['verdict'].values())} still pass. A cell that fails here"
            " does not enter the board."
        )
    else:
        out.append(
            f"**First family** ({FIRST_TRIALS} trials, deflated by {FIRST_TRIALS}): "
            + (
                f"{len(passed)} of {len(first)} cells pass ({', '.join(passed)})."
                if passed
                else "no cell passes."
            )
            + (
                f" {len(nov)} had fewer than {MIN_SESSIONS} sessions holding a tag and carry no verdict."
                if nov
                else ""
            )
        )
    if first:
        best = max(
            (c for c in first.values() if c["mean"] is not None), key=lambda c: c["mean"], default=None
        )
        if best:
            out.append(
                f"**Best mean** in the first family: {_label('', best)} at {_cut(best['cut'])}, "
                f"{_pct(best['mean'])} points a session over its matched baseline, deflated interval "
                f"{_iv(best)}. A mean can lead and still fail on the interval or on the larger-cut check."
            )
        broke = [
            k
            for k, c in first.items()
            if c["verdict"] == "fail"
            and c.get("interval")
            and c["interval"][0] > 0
            and not c.get("larger_ok")
        ]
        if broke:
            out.append(
                f"**Failed only on size:** {', '.join(broke)} excluded zero but a larger cut did worse."
            )
        wrong = [k for k, c in first.items() if c.get("interval") and c["interval"][1] < 0]
        if wrong:
            out.append(f"**Pointed the wrong way** (interval wholly below zero): {', '.join(wrong)}.")
    stale = [(k, c["readings"]["placebo"]) for k, c in first.items() if c.get("readings") and k in passed]
    if stale:
        out.append(
            "**Stale-tag placebo** for the passing cells (last grid session's tags against this one's outcomes; "  # noqa: E501
            "a real effect should not survive it): "
            + "; ".join(f"{k} {_pct(p['mean'])}" for k, p in stale)
            + "."
        )
    thin = result.get("thin_sessions") or []
    if thin:
        wt = result.get("first_without_thin") or {}
        agree = all(wt.get(k, {}).get("verdict") == c["verdict"] for k, c in first.items())
        out.append(
            f"**Thin sessions:** {len(thin)} grid sessions held under 80% of the usual names; read without"
            f" them the verdicts {'are the same' if agree else 'differ'}."
        )
    else:
        out.append("**Thin sessions:** none; every grid session holds a normal share of its names.")
    second = result.get("second", {})
    if second:
        sp = [k for k, c in second.items() if c["verdict"] == "pass"]
        out.append(
            f"**Second family** ({SECOND_TRIALS} trials, deflated together): "
            + (f"{len(sp)} pass ({', '.join(sp)})." if sp else "none pass.")
            + " A filter must also beat its unfiltered tag."
        )
    for k, c in result.get("earnings", {}).items():
        out.append(
            f"**{k}** ({c['reports']} reports): hit rate {c['hit_rate']:.0%}, interval {_iv(c)}; "
            f"mean signed excess {_pct(c['mean_signed_excess'])} points, "
            f"{_pct(c['mean_after_cost'])} after an assumed {int(EARN_COST * 100)}% round trip: {c['verdict']}."  # noqa: E501
            if c["hit_rate"] is not None
            else f"**{k}**: no reports."
        )
    out.append(
        "**What the sample could see:** a deflated interval needs an effect large against a session's noise;"
        " a fail reads as no effect that size on this sample, not proof of none."
    )
    return out


def report(result: dict) -> str:
    window = result["window"]
    lines = [
        f"# Item 32: large options orders as a signal ({window})",
        "",
        f"**Verdict:** {_summary(result)}. Window {result['span'][0]} to {result['span'][1]},"
        f" {result['grid_sessions']} grid sessions.",
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **tagged**: name-sessions holding the tag at that cut; **sessions**: grid sessions with a usable"
        " reading. **mean**: the tagged stocks' signed five-session excess return over SPY (from the close"
        " after the tag is known) less the same-stage untagged stocks', in percent points, averaged over sessions.",  # noqa: E501
        "- **interval**: stationary bootstrap over sessions, widened by the family's trial count"
        " (nine, forty-nine, two). A cell passes when it lies wholly above zero, the larger cuts do at"
        " least as well, and (second family) it beats its unfiltered tag.",
        "- **larger ok**: every larger cut's mean is at least this one's. **beats**: the filtered mean is above"  # noqa: E501
        " the unfiltered tag's.",
        "- **placebo**: the previous grid session's tags against this session's outcomes. **drop five**: the"
        " five most frequently tagged names removed. Reported, not judged. A stock reporting inside the"
        " outcome window is the second family's F7 filter.",
        "- **hit rate** (earnings): reports where the move went the tag's way; the test is its interval above 50%.",  # noqa: E501
    ]
    first = result.get("first", {})
    if first:
        lines += [
            "", "## First family", "",
            "| tag | cut | verdict | sessions | tagged | mean | interval | larger ok | placebo | drop five |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]  # fmt: skip
        for c in first.values():
            r = c.get("readings") or {}
            lines.append(
                f"| {TAGS[c['tag']]['label']} | {_cut(c['cut'])} | {c['verdict']} | {c['sessions']} | {c['tagged']}"  # noqa: E501
                f" | {_pct(c['mean'])} | {_iv(c)} | {'' if c.get('larger_ok') is None else 'yes' if c['larger_ok'] else 'no'}"  # noqa: E501
                f" | {_pct(r.get('placebo', {}).get('mean'))} | {_pct(r.get('drop_five', {}).get('mean'))} |"
            )
    wt = result.get("first_without_thin")
    if wt:
        lines += ["", "## First family without the thin sessions", "",
                  "| tag | cut | verdict | sessions | mean | interval |", "|---|---|---|---|---|---|"]  # fmt: skip # noqa: E501
        for c in wt.values():
            lines.append(
                f"| {TAGS[c['tag']]['label']} | {_cut(c['cut'])} | {c['verdict']} | {c['sessions']}"
                f" | {_pct(c['mean'])} | {_iv(c)} |"
            )
    second = result.get("second", {})
    if second:
        lines += ["", "## Second family", "",
                  "| filter | verdict | sessions | tagged | mean | unfiltered | interval | larger ok | beats |",  # noqa: E501
                  "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for k, c in second.items():
            lines.append(
                f"| {_label(k, c)} | {c['verdict']} | {c['sessions']} | {c['tagged']} | {_pct(c['mean'])}"
                f" | {_pct(c['unfiltered_mean'])} | {_iv(c)}"
                f" | {'' if c.get('larger_ok') is None else 'yes' if c['larger_ok'] else 'no'}"
                f" | {'yes' if c['beats_unfiltered'] else 'no'} |"
            )
    earn = result.get("earnings", {})
    if earn:
        lines += ["", "## Earnings", "",
                  "| direction | verdict | reports | hit rate | interval | mean signed excess | after cost |",
                  "|---|---|---|---|---|---|---|"]  # fmt: skip
        for c in earn.values():
            hit = "" if c["hit_rate"] is None else f"{c['hit_rate']:.1%}"
            lines.append(
                f"| {c['direction']} | {c['verdict']} | {c['reports']} | {hit} | {_iv(c)}"
                f" | {_pct(c['mean_signed_excess'])} | {_pct(c['mean_after_cost'])} |"
            )
    return "\n".join(lines) + "\n"


def _summary(result: dict) -> str:
    v = result["verdict"]
    return f"{sum(x == 'pass' for x in v.values())} of {len(v)} cells pass, {sum(x == 'no verdict' for x in v.values())} carry no verdict"  # noqa: E501


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
