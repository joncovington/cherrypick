"""Item 33, MEIC on Fed decision days (docs/edge-hypotheses-plan.md): does the live rule net less on a
scheduled FOMC decision day than on other sessions, enough that skipping those days raises its net?

    python scripts/meic_fomc_item33.py calendar [--cpi-file FILE]
    python scripts/meic_fomc_item33.py plan [--provisional]
    python scripts/meic_fomc_item33.py study [--provisional] [--arm ARM] [--json PATH]

`calendar` fetches the Fed's published meeting calendar (and the BLS CPI schedule) by script into
`~/.cherrypick/data/public/item33/calendar.json`, with the source URLs. The dates are public and
personal-use: fetched by this script, never committed. The fetch reads no outcome. The BLS site refuses
scripted requests (403 at the time of writing); the script does not work around that. CPI days are
reported, never judged, so a missing CPI list is said so in the results; a hand-saved list of ISO dates,
one per line, loads with `--cpi-file`.

`plan` says which stored replay runs supply the per-session nets of the arm for each window, or the
command that produces them. `study` reads those nets (never the runner: no new replay, no holdout
spent) and writes `results.json` and `results.md` under the backtest data dir
(results/research/item33/), never into the repo: the inputs are licensed.

**The arm.** `live-1400`, item 23's reference arm: the registry's `pm-200` entering once a day,
14:00-14:30, gates and all, which is the rule MEIC trades live. Its stored runs cover both windows
(`scripts/meic_delta_grid_item23.py`, 60-second store). `--arm pm-200` reads the registry's own
`pm-200` (entries every 30 minutes 12:00-14:30) instead, where a run holds it; the plan's wording
("entering from 14:00") is the reason for the default.

**The days.** A scheduled decision day is the second day of a scheduled FOMC meeting, the day the
statement is released at 14:00 ET. Unscheduled meetings and notation votes are not decision days;
they are listed and reported. A day with no session in the run is not counted.

**The verdict,** design window: at least 20 Fed days (fewer is no verdict); the Fed-day mean net below
zero and below the other sessions' mean, with a one-sided permutation p under 0.05 (the Fed-day label
redrawn at random over the window's sessions, 10,000 draws, seeded); below the other sessions' mean in
each half of the window. Then the holdout: the Fed-day mean below the other sessions' mean. The holdout
is read whatever the design result, and a design fail stays a fail. Reported, not judged: the gated
rule's whole-window net against the ungated, the five sessions either side of each Fed day, the result
without the worst Fed day, CPI days, unscheduled-action days.

**Provisional.** MEIC's replay is provisional until the latest MEIC validation report unlocks history.
Until then `plan` and `study` refuse unless run with `--provisional`, and the result is marked so.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

ARM = "live-1400"
MODULE = "meic"
SYMBOL = "SPX"
CADENCE = 60
MIN_FED_DAYS = 20
DRAWS = 10_000
SEED = 33
ALPHA = 0.05
NEIGHBOURS = 5
USER_AGENT = "cherrypick personal research"
FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
CPI_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "January February March April May June July August September October November December".split()
    )
}


# ------------------------------------------------------------------------------------------------
# Pure: the calendar.


def parse_fomc(html: str) -> dict:
    """The Fed's calendar page: {"scheduled": [ISO decision days], "unscheduled": [ISO dates]}.
    A meeting listed as `27-28` in January is decided on the 28th; `31-1` under `Jan/Feb` on 1 February.
    A date with no day range (a notation vote, an unscheduled meeting) is unscheduled."""
    scheduled: set[str] = set()
    unscheduled: set[str] = set()
    heads = list(re.finditer(r"(\d{4}) FOMC Meetings", html))
    for i, h in enumerate(heads):
        year = int(h.group(1))
        block = html[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        for m in re.finditer(
            r"<strong>([A-Za-z/]+)</strong></div>\s*<div class=\"[^\"]*fomc-meeting__date[^\"]*\">([^<]*)<",
            block,
        ):
            # "Jan/Feb" heads a meeting that straddles two months: its decision is in the second.
            first_name, _, second_name = m.group(1).partition("/")
            month = next(
                (v for k, v in MONTHS.items() if k.startswith((second_name or first_name)[:3])), None
            )
            text = m.group(2).strip()
            if month is None:
                continue
            rng = re.match(r"(\d{1,2})-(\d{1,2})\*?$", text)
            if rng and "unscheduled" not in text.lower():
                y = year + 1 if second_name and month == 1 else year
                scheduled.add(date(y, month, int(rng.group(2))).isoformat())
                continue
            day = re.match(r"(\d{1,2})", text)
            if day:
                unscheduled.add(date(year, month, int(day.group(1))).isoformat())
    return {"scheduled": sorted(scheduled), "unscheduled": sorted(unscheduled)}


def parse_cpi(html: str) -> list[str]:
    """Every full date ("Jan. 12, 2023", "January 12, 2023") on the BLS CPI schedule page."""
    out = set()
    for m in re.finditer(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}), (\d{4})\b", html):
        month = MONTHS.get(next((k for k in MONTHS if k.startswith(m.group(1)[:3])), ""))
        if month:
            try:
                out.add(date(int(m.group(3)), month, int(m.group(2))).isoformat())
            except ValueError:
                continue
    return sorted(out)


def read_dates(text: str) -> list[str]:
    """A hand-saved date list: one ISO date per line; blank lines and `#` comments ignored."""
    out = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(date.fromisoformat(line).isoformat())
    return sorted(out)


# ------------------------------------------------------------------------------------------------
# Pure: the reading.


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def split(nets: dict[str, float], fed: set[str]) -> tuple[list[float], list[float]]:
    """(Fed-day nets, other sessions' nets) over the sessions in `nets`."""
    return [v for d, v in nets.items() if d in fed], [v for d, v in nets.items() if d not in fed]


def gap(nets: dict[str, float], fed: set[str]) -> float | None:
    """Mean Fed-day net less the other sessions' mean, or None when either side is empty."""
    f, o = split(nets, fed)
    return _mean(f) - _mean(o) if f and o else None


def permutation_p(
    nets: dict[str, float], fed: set[str], draws: int = DRAWS, seed: int = SEED
) -> float | None:
    """One-sided: the share of random labellings (as many sessions as there are Fed days, drawn from
    the same sessions) whose Fed-minus-other gap is at or below the observed one."""
    days = sorted(nets)
    k = sum(d in fed for d in days)
    observed = gap(nets, fed)
    if observed is None or k == len(days):
        return None
    values = [nets[d] for d in days]
    total = sum(values)
    n = len(values)
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        s = sum(rng.sample(values, k))
        if s / k - (total - s) / (n - k) <= observed + 1e-12:
            hits += 1
    return (hits + 1) / (draws + 1)


def neighbours(nets: dict[str, float], fed: set[str], width: int = NEIGHBOURS) -> dict:
    """The Fed days against the sessions within `width` sessions either side of any Fed day (Fed days
    themselves excluded): the volatile-period rival."""
    days = sorted(nets)
    near: set[str] = set()
    for i, d in enumerate(days):
        if d in fed:
            near.update(days[max(0, i - width) : i] + days[i + 1 : i + 1 + width])
    near -= fed
    f = [nets[d] for d in days if d in fed]
    n = [nets[d] for d in days if d in near]
    return {
        "fed_mean": _mean(f),
        "near_mean": _mean(n),
        "near_sessions": len(n),
        "gap": None if not f or not n else _mean(f) - _mean(n),
    }


def without_worst(nets: dict[str, float], fed: set[str]) -> dict:
    """The gap with the lowest-net Fed day removed (it joins nobody: it is dropped)."""
    present = sorted(d for d in fed if d in nets)
    if len(present) < 2:
        return {"dropped": None, "fed_mean": None, "gap": None}
    worst = min(present, key=lambda d: nets[d])
    rest = {d: v for d, v in nets.items() if d != worst}
    f, _ = split(rest, fed)
    return {"dropped": worst, "fed_mean": _mean(f), "gap": gap(rest, fed)}


def labelled(nets: dict[str, float], days: set[str]) -> dict:
    """A reported day set against the sessions outside it."""
    f, o = split(nets, days)
    return {"days": len(f), "mean": _mean(f), "other_mean": _mean(o), "gap": gap(nets, days)}


def judge(nets: dict[str, float], fed_dates: set[str], start: str, end: str) -> dict:
    """One window's reading and whether every design condition holds."""
    fed = {d for d in fed_dates if d in nets}
    mid = midpoint(start, end)
    first = {d: v for d, v in nets.items() if d < mid}
    second = {d: v for d, v in nets.items() if d >= mid}
    f, o = split(nets, fed)
    g, p = gap(nets, fed), permutation_p(nets, fed)
    halves = [gap(first, fed), gap(second, fed)]
    ungated = sum(nets.values())
    gated = sum(o)
    conditions = {
        "fed_mean_below_zero": bool(f) and _mean(f) < 0,
        "fed_below_other": g is not None and g < 0,
        "permutation_p_under_alpha": p is not None and p < ALPHA,
        "below_other_each_half": all(h is not None and h < 0 for h in halves),
    }
    return {
        "sessions": len(nets),
        "fed_days": len(fed),
        "fed_mean": _mean(f),
        "other_mean": _mean(o),
        "gap": g,
        "p": p,
        "halves": halves,
        "fed_days_by_half": [sum(d in fed for d in first), sum(d in fed for d in second)],
        "net_ungated": ungated,
        "net_gated": gated,
        "gate_effect": gated - ungated,
        "conditions": conditions,
        "passes": all(conditions.values()),
        "fed_session_dates": sorted(fed),
    }


def verdict(design: dict, holdout: dict | None) -> str:
    if design["fed_days"] < MIN_FED_DAYS:
        return "NO VERDICT"
    if not design["passes"]:
        return "FAIL"
    if holdout is None or holdout["gap"] is None:
        return "NO VERDICT"
    return "PASS" if holdout["gap"] < 0 else "FAIL"


def check_unlock(unlocked: bool, provisional: bool) -> bool:
    """Whether the result is provisional. History locked and no `--provisional`: refuse."""
    if unlocked:
        return False
    if not provisional:
        raise SystemExit(
            "MEIC history has not unlocked (the latest MEIC validation report). Run with --provisional "
            "to read it as provisional, as the declaration allows, or wait for the unlock."
        )
    return True


# ------------------------------------------------------------------------------------------------
# The stores.


def _public_dir() -> Path:
    from cherrypick.core import home

    d = home.data_dir() / "public" / "item33"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item33"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def cmd_calendar(args) -> int:
    fomc = parse_fomc(_fetch(FED_URL))
    cpi_source, cpi, cpi_status = CPI_URL, [], "fetched"
    if args.cpi_file:
        cpi_source, cpi = (
            f"file:{Path(args.cpi_file).name}",
            read_dates(Path(args.cpi_file).read_text("utf-8")),
        )
    else:
        try:
            cpi = parse_cpi(_fetch(CPI_URL))
        except urllib.error.URLError as exc:
            cpi_status = f"unavailable: {exc}"
        if not cpi and cpi_status == "fetched":
            cpi_status = "unavailable: no dates on the page"
    doc = {
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "sources": {"fomc": FED_URL, "cpi": cpi_source},
        "fomc": fomc,
        "cpi": {"status": cpi_status, "dates": cpi},
    }
    path = _public_dir() / "calendar.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({"file": str(path), "fomc_scheduled": len(fomc["scheduled"]),
                      "fomc_unscheduled": len(fomc["unscheduled"]), "cpi": cpi_status,
                      "cpi_dates": len(cpi)}))  # fmt: skip
    return 0


def load_calendar() -> dict:
    path = _public_dir() / "calendar.json"
    if not path.exists():
        raise SystemExit("no calendar yet: run `calendar` first")
    return json.loads(path.read_text(encoding="utf-8"))


def find_run(conn: sqlite3.Connection, arm: str, window: str, start: str, end: str) -> str | None:
    """The newest stored MEIC run of `window` over exactly [start, end] at the store's cadence whose
    arms include `arm`. Metadata only; no net is read."""
    rows = conn.execute(
        "SELECT run_id, arms FROM runs WHERE module = ? AND window_name = ? AND start = ? AND end = ? "
        "AND cadence = ? ORDER BY created_at DESC",
        (MODULE, window, start, end, CADENCE),
    ).fetchall()
    for run_id, arms in rows:
        if arm in json.loads(arms):
            return run_id
    return None


def run_nets(conn: sqlite3.Connection, run_id: str, arm: str) -> dict[str, float]:
    return {
        d: n
        for d, n in conn.execute(
            "SELECT session, net FROM session_results WHERE run_id = ? AND arm = ? AND net IS NOT NULL",
            (run_id, arm),
        )
    }


def _context(provisional: bool):
    from cherrypick.backtest import results, runner
    from cherrypick.backtest import settings as _settings

    s = _settings.load(None)
    prov = check_unlock(runner.unlocked(s, SYMBOL, CADENCE, module=MODULE), provisional)
    return s, results.connect(), prov


def cmd_plan(args) -> int:
    s, conn, prov = _context(args.provisional)
    try:
        out = {"arm": args.arm, "provisional": prov, "windows": {}}
        for w in ("design", "holdout"):
            start, end = s["windows"][w]
            run_id = find_run(conn, args.arm, w, start, end)
            if run_id:
                how = None
            elif args.arm == ARM:
                how = f"python scripts/meic_delta_grid_item23.py --window {w} (its live-1400 arm)"
            else:
                how = (
                    f"python -m cherrypick.backtest run meic --arms {args.arm} --window {w}"
                    f" --cadence {CADENCE} --provisional"
                )
            out["windows"][w] = {"span": [start, end], "run_id": run_id, "produce_with": how}
    finally:
        conn.close()
    print(json.dumps(out, indent=1))
    return 0


def cmd_study(args) -> int:
    cal = load_calendar()
    s, conn, prov = _context(args.provisional)
    fed = set(cal["fomc"]["scheduled"])
    unsched = set(cal["fomc"]["unscheduled"])
    cpi = set(cal["cpi"]["dates"])
    result: dict = {"item": 33, "arm": args.arm, "provisional": prov, "windows": {}}
    try:
        for w in ("design", "holdout"):
            start, end = s["windows"][w]
            run_id = find_run(conn, args.arm, w, start, end)
            if run_id is None:
                result["windows"][w] = {"span": [start, end], "run_id": None}
                continue
            nets = run_nets(conn, run_id, args.arm)
            j = judge(nets, fed, start, end)
            j.update(
                span=[start, end],
                run_id=run_id,
                near=neighbours(nets, {d for d in fed if d in nets}),
                without_worst=without_worst(nets, fed),
                cpi=labelled(nets, cpi) if cal["cpi"]["dates"] else None,
                unscheduled=labelled(nets, unsched),
                worst_fed_net=min((nets[d] for d in j["fed_session_dates"]), default=None),
            )
            result["windows"][w] = j
    finally:
        conn.close()
    design, holdout = result["windows"]["design"], result["windows"]["holdout"]
    if design.get("run_id") is None:
        result["verdict"] = "NO VERDICT"
    else:
        result["verdict"] = verdict(design, holdout if holdout.get("run_id") else None)
    result["window"] = (
        f"{design['span'][0]}..{design['span'][1]}; holdout {holdout['span'][0]}..{holdout['span'][1]}"
    )
    result["calendar"] = {
        "fetched": cal["fetched"],
        "sources": cal["sources"],
        "cpi_status": cal["cpi"]["status"],
        "scheduled": len(cal["fomc"]["scheduled"]),
        "unscheduled": cal["fomc"]["unscheduled"],
        "untouched_check": [d for d in cal["fomc"]["scheduled"] if d >= "2026-11-01"],
    }
    result["run_id"] = f"item33-{date.today().isoformat()}"
    path = Path(args.json) if args.json else _out_dir() / "results.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "file": str(path)}))
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.

CHECKS = (
    ("fed_mean_below_zero", "Fed-day mean net", "below zero"),
    ("fed_below_other", "Fed days against other sessions", "lower"),
    ("permutation_p_under_alpha", "One-sided permutation p", "under 0.05"),
    ("below_other_each_half", "Each half of the window", "both lower"),
)


def _usd(x: float | None) -> str:
    if x is None:
        return ""
    v = round(x)
    return f"{'âˆ’' if v < 0 else ''}${abs(v):,}"


def _p(x: float | None) -> str:
    return "" if x is None else f"{x:.4f}"


def findings(result: dict) -> list[str]:
    out = []
    d = result["windows"]["design"]
    if d.get("run_id") is None:
        return ["No stored run holds the arm over the design window; see `plan`."]
    if d["fed_days"] < MIN_FED_DAYS:
        out.append(f"**Too few Fed days:** {d['fed_days']} in the design window; {MIN_FED_DAYS} are needed.")
    failed = [label.lower() for key, label, _ in CHECKS if not d["conditions"][key]]
    out.append(
        f"**The judged reading** (design window): Fed days netted {_usd(d['fed_mean'])} a session against"
        f" {_usd(d['other_mean'])} on the others (gap {_usd(d['gap'])}, p {_p(d['p'])}). "
        + (f"It misses on: {'; '.join(failed)}." if failed else "Every design check holds.")
    )
    out.append(
        f"**Gate, reported:** removing the Fed days changes the window's net by {_usd(d['gate_effect'])}"
        f" ({_usd(d['net_ungated'])} ungated, {_usd(d['net_gated'])} gated)."
    )
    h = result["windows"]["holdout"]
    if h.get("run_id"):
        out.append(
            f"**Holdout** (read whatever the design result; the design having"
            f" {'passed' if d['passes'] else 'failed'}): Fed days {_usd(h['fed_mean'])} against"
            f" {_usd(h['other_mean'])}, gap {_usd(h['gap'])}, {h['fed_days']} Fed days."
        )
    nb, ww = d["near"], d["without_worst"]
    out.append(
        f"**Rivals, reported:** against the {NEIGHBOURS} sessions either side the Fed days' gap is"
        f" {_usd(nb['gap'])}; without the worst Fed day ({ww['dropped']}) the gap is {_usd(ww['gap'])}."
    )
    out.append(
        "**What the sample could see:** only a Fed-day loss about the size of a typical losing session."
        " A fail reads as no Fed-day cost that large, not that Fed days are safe."
    )
    if result["provisional"]:
        out.append("**Provisional:** MEIC's replay had not unlocked history when this ran.")
    if result["calendar"]["cpi_status"] != "fetched":
        out.append(f"**CPI days not read:** {result['calendar']['cpi_status']}.")
    return out


def report(result: dict) -> str:
    word = {"PASS": "pass", "FAIL": "fail"}.get(result["verdict"], "no verdict")
    cal = result["calendar"]
    lines = [
        "# Item 33: MEIC on Fed decision days",
        "",
        f"**Verdict: {word}.**{' Provisional.' if result['provisional'] else ''} Does the live rule"
        f" (`{result['arm']}`) net less on a scheduled FOMC decision day, the day the statement lands at"
        " 14:00, than on other sessions? Figures are dollars per session, net of fees.",
        "",
    ]
    d = result["windows"]["design"]
    if d.get("run_id") is not None:
        lines += [
            "## The judged checks",
            "",
            "| check | needed | result | |",
            "|---|---|---|---|",
        ]
        shown = {
            "fed_mean_below_zero": _usd(d["fed_mean"]),
            "fed_below_other": f"{_usd(d['fed_mean'])} against {_usd(d['other_mean'])}",
            "permutation_p_under_alpha": _p(d["p"]),
            "below_other_each_half": " / ".join(_usd(x) for x in d["halves"]),
        }
        for key, label, needed in CHECKS:
            ok = "pass" if d["conditions"][key] else "fail"
            lines.append(f"| {label} | {needed} | {shown[key]} | {ok} |")
        lines.append(f"| Fed days | at least {MIN_FED_DAYS} | {d['fed_days']} | "
                     f"{'pass' if d['fed_days'] >= MIN_FED_DAYS else 'fail'} |")  # fmt: skip
        lines.append("")
    lines += ["## What it says", "", *[f"- {x}" for x in findings(result)], ""]
    for wname, w in result["windows"].items():
        lines += [f"## {wname.capitalize()}: {w['span'][0]} to {w['span'][1]}", ""]
        if w.get("run_id") is None:
            lines += ["No stored run for the arm over this window.", ""]
            continue
        lines += [
            "| | sessions | Fed days | Fed mean | other mean | gap | p | half 1 gap | half 2 gap |",
            "|---|---|---|---|---|---|---|---|---|",
            f"| {wname} | {w['sessions']} | {w['fed_days']} | {_usd(w['fed_mean'])}"
            f" | {_usd(w['other_mean'])} | {_usd(w['gap'])} | {_p(w['p'])}"
            f" | {_usd(w['halves'][0])} | {_usd(w['halves'][1])} |",
            "",
            f"Run `{w['run_id']}`. Reported, not judged: CPI days "
            + (
                f"{w['cpi']['days']} at {_usd(w['cpi']['mean'])} (gap {_usd(w['cpi']['gap'])})"
                if w.get("cpi")
                else "not read"
            )
            + f"; unscheduled-action days {w['unscheduled']['days']}"
            + (f" at {_usd(w['unscheduled']['mean'])}" if w["unscheduled"]["days"] else "")
            + ".",
            "",
        ]
    lines += [
        "## Calendar",
        "",
        f"Fetched {cal['fetched']} from {cal['sources']['fomc']} (Fed) and {cal['sources']['cpi']} (CPI);"
        f" {cal['scheduled']} scheduled decision days; unscheduled: "
        + (", ".join(cal["unscheduled"]) or "none")
        + ". The untouched check, scheduled days from 2026-11-01 in the ledgers: "
        + (", ".join(cal["untouched_check"]) or "none")
        + ".",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calendar", help="fetch the FOMC and CPI dates (public, into ~/.cherrypick)")
    c.add_argument("--cpi-file")
    c.set_defaults(fn=cmd_calendar)
    for name, fn, hlp in (("plan", cmd_plan, "which stored runs supply the nets"),
                          ("study", cmd_study, "read the stored nets and state the verdict")):  # fmt: skip
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--provisional", action="store_true", help="read while MEIC history is locked")
        sp.add_argument("--arm", default=ARM)
        if name == "study":
            sp.add_argument("--json")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
