"""Item 67, the earnings module's rule with paper's spread screen (docs/edge-hypotheses-plan.md).

    python scripts/earnings_paper_screen_item67.py mde [--inputs-dir DIR] [--out-dir DIR]
    python scripts/earnings_paper_screen_item67.py study [--host H] [--port P] [--inputs-dir DIR]
        [--out-dir DIR]
    python scripts/earnings_paper_screen_item67.py report [--out-dir DIR]

Item 31's replay, imported so the two cannot drift apart, with two checks taken out and nothing else
changed:

- the study's own point-in-time cap on the at-the-money spread (`pit_spread`), by overriding
  `Study._pit_reasons` to drop that one reason and keep the dollar-volume ones;
- the module's own ceiling on the at-the-money spread (`bid_ask_spread_too_wide`), by setting each
  strategy's `max_bid_ask_spread_pct` to infinity in a copy of item 31's `study_config()`. That key is
  read in one place, `scanner.apply_liquidity_gates`, which still reports a missing spread as
  unverified, so a name with no spread at all stays out.

Every other criterion, the sizing, the management, the costs (the suite's 12.5% of the end-of-day
spread from mid on each side, which if anything overcharges the wider names) and the paired control are
item 31's. The arms are the three strategies ungated, with Holm across three. Each arm is judged by
item 31's `judge_arm`.

`study` reads item 31's `events.json`, `calendar.json`, `pilot.json` and optional `macro_calendar.json`
from results/research/item31/ (read only). Item 31's request plan covered every event whether or not it
was admitted, so the chains this study reads are already in the store; no fetch is planned. `mde` reads
item 31's `trades.json` (read only) and writes the minimum detectable effect before this study is run.

Reported, never judged: each arm split by item 31's admission (events item 31 admitted, which should
reproduce item 31, against events only this screen admits) and by the at-the-money spread's terciles.

Results go under the backtest data dir (results/research/item67/), never into the repo: the inputs and
everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

from cherrypick.core import robustness
from cherrypick.core import stats as core_stats

_spec = importlib.util.spec_from_file_location(
    "earnings_item31", Path(__file__).with_name("earnings_item31.py")
)
e31 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_item31", e31)
_spec.loader.exec_module(e31)

REPLAY_CORRECTIONS = ()
STRATEGIES = e31.STRATEGIES
ARMS = STRATEGIES  # ungated only: G1 and G2 are item 31's and failed
SPREAD_REASONS = ("pit_spread", "bid_ask_spread_too_wide")
GROUPS = ("item31_admitted", "only_this_screen")
WINDOW_TEXT = e31.WINDOW_TEXT


def study_config() -> dict:
    """Item 31's config with the module's spread ceiling off in every strategy's block. Item 31's own
    dicts are copied, never changed."""
    cfg = e31.study_config()
    cfg["strategies"] = {
        name: {**block, "max_bid_ask_spread_pct": math.inf} for name, block in cfg["strategies"].items()
    }
    return cfg


def spread_checks_off(cfg: dict) -> bool:
    """True when every strategy's spread ceiling is off."""
    return all(b.get("max_bid_ask_spread_pct") == math.inf for b in cfg["strategies"].values())


class PaperScreenStudy(e31.Study):
    """Item 31's replay without its point-in-time spread cap. The dollar-volume reasons stay."""

    def _pit_reasons(self, symbol, session, crit) -> list[str]:
        return [r for r in super()._pit_reasons(symbol, session, crit) if r != "pit_spread"]


def item31_admits(rec: dict) -> bool:
    """Whether item 31's screen would have admitted this event: this screen admitted it and its
    at-the-money spread is within both of item 31's caps (the study's and the module's)."""
    spread = rec.get("atm_spread_pct")
    return bool(
        rec.get("admitted")
        and spread is not None
        and spread <= e31.PIT_MAX_SPREAD_PCT
        and spread <= e31.DEFAULTS["max_bid_ask_spread_pct"]
    )


def split_by_admission(
    events: list[dict], pairs: list[tuple[dict, dict]]
) -> dict[str, tuple[list[dict], list[tuple[dict, dict]]]]:
    """An arm's events and pairs cut by item 31's admission."""
    out = {g: ([], []) for g in GROUPS}
    for e in events:
        out[GROUPS[0] if item31_admits(e) else GROUPS[1]][0].append(e)
    for e, c in pairs:
        out[GROUPS[0] if item31_admits(e) else GROUPS[1]][1].append((e, c))
    return out


def group_means(events: list[dict], pairs: list[tuple[dict, dict]]) -> dict:
    """Counts and means for one group; no test, it is reported and not judged."""
    return {
        "events": len(events),
        "pairs": len(pairs),
        "mean_net": e31._mean(e31.share(e) for e in events),
        "mean_paired": e31._mean(e31.share(e) - e31.share(c) for e, c in pairs),
        "mean_atm_spread_pct": e31._mean(
            e["atm_spread_pct"] for e in events if e.get("atm_spread_pct") is not None
        ),
    }


def admission_split(idx: dict) -> dict:
    """{arm: {window: {group: counts and means}}}."""
    out = {}
    for strategy in STRATEGIES:
        out[strategy] = {}
        for w in ("design", "holdout"):
            events, pairs = e31.arm_sample(idx, strategy, "", w)
            cut = split_by_admission(events, pairs)
            out[strategy][w] = {g: group_means(*cut[g]) for g in GROUPS}
    return out


def evaluate_arms(idx: dict) -> dict:
    """The three arms' design and holdout statistics and verdicts, Holm across the three (a family for
    the net test and one for the paired test; an arm under the minimum counts p = 1)."""
    stats = {}
    for strategy in STRATEGIES:
        samples = {w: e31.arm_sample(idx, strategy, "", w) for w in ("design", "holdout")}
        stats[strategy] = {w: e31.window_stats(*samples[w], bootstrap=w == "design") for w in samples}
    p_net, p_pair = {}, {}
    for arm, s in stats.items():
        enough = s["design"]["pairs"] >= e31.MIN_TRADES["design"]
        p_net[arm] = s["design"]["net"]["p"] if enough else 1.0
        p_pair[arm] = s["design"]["pair"]["p"] if enough else 1.0
    h_net, h_pair = core_stats.holm(p_net, alpha=e31.ALPHA), core_stats.holm(p_pair, alpha=e31.ALPHA)
    return {
        arm: {
            **s,
            **e31.judge_arm(s["design"], s["holdout"], h_net[arm], h_pair[arm]),
            "p_net": p_net[arm],
            "p_pair": p_pair[arm],
        }
        for arm, s in stats.items()
    }


def summarise_results(records: list[dict], fomc: set[date], cpi: set[date] | None) -> dict:
    """results.json: `verdict` per arm and `window`, as the studies board reads them, the arms'
    statistics, the admission and ATM-spread splits, and what dropped out."""
    idx = e31.index_records(records)
    arms = evaluate_arms(idx)
    splits = admission_split(idx)
    out_arms = {}
    for arm, a in arms.items():
        reported = {}
        for w in ("design", "holdout"):
            events, pairs = e31.arm_sample(idx, arm, "", w)
            reported[w] = e31.reported_readings(events, pairs, fomc, cpi)
            if w == "design":
                a["mde"] = e31.mde_line(events)
        out_arms[arm] = {**a, "reported": reported, "by_admission": splits[arm]}
    return {
        "verdict": {arm: {"verdict": a["verdict"], "reading": a["reading"]} for arm, a in arms.items()},
        "window": WINDOW_TEXT,
        "run_id": f"item67-{date.today().isoformat()}",
        "basis": "end_of_day",
        "arms": out_arms,
        "admission_split": splits,
        "atm_spread_terciles": {
            arm: {w: a["reported"][w].get("by_atm_spread_tercile") for w in ("design", "holdout")}
            for arm, a in out_arms.items()
        },
        "drops": e31.drop_summary(records),
        "counts": {
            "records": len(records),
            "event_trades_closed": sum(1 for r in records if r["role"] == "event" and r["ok"]),
            "control_trades_closed": sum(1 for r in records if r["role"] == "control" and r["ok"]),
            "event_trades_closed_item31_admitted": sum(
                1 for r in records if r["role"] == "event" and r["ok"] and item31_admits(r)
            ),
        },
        "macro_calendar": {"fomc_dates": len(fomc), "cpi": None if cpi is None else len(cpi)},
        "declared_limits": LIMITS,
    }


LIMITS = (
    "Item 31's limits, all of them: the end-of-day basis, today's liquid list (hindsight), the open "
    "interest and option volume floors not reproduced, the weekly Friday front expiry, no early "
    "assignment, stale marks counted.",
    "Closing spreads run wider than the intraday quotes paper's live screen reads, so this screen "
    "admits on end-of-day spreads and charges the end-of-day spread: the cost is overstated for the "
    "wider names, not understated.",
    "The windows' newly admitted events were never read before this run; reports after 2026-11-06 are "
    "the clean forward check and are read once 100 trades per arm are stored.",
)


def _dir(override: str | None, item: str) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / item
    return d


def cmd_mde(args) -> int:
    """B12, before any of this study's P&L: item 31's own per-trade spread of each arm's net share in
    the design window, at the declared minimum number of design trades."""
    src = _dir(args.inputs_dir, "item31")
    out_dir = _dir(args.out_dir, "item67")
    out_dir.mkdir(parents=True, exist_ok=True)
    records = json.loads((src / "trades.json").read_text(encoding="utf-8"))
    idx = e31.index_records(records)
    out = {}
    for strategy in STRATEGIES:
        events, _ = e31.arm_sample(idx, strategy, "", "design")
        vals = [e31.share(e) for e in events]
        out[strategy] = (
            robustness.planned_mde(vals, e31.MIN_TRADES["design"], paired=False) if len(vals) > 1 else None
        )
    doc = {"planned_n": e31.MIN_TRADES["design"], "source": "item 31 per-trade net share", "arms": out}
    (out_dir / "mde.json").write_text(json.dumps(e31.clean(doc), indent=1), encoding="utf-8")
    print(json.dumps({"file": str(out_dir / "mde.json")}))
    return 0


def cmd_study(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    src = _dir(args.inputs_dir, "item31")
    out_dir = _dir(args.out_dir, "item67")
    out_dir.mkdir(parents=True, exist_ok=True)
    pilot = json.loads((src / "pilot.json").read_text(encoding="utf-8"))
    if not pilot.get("decided") or pilot.get("basis") != "end_of_day":
        raise SystemExit("item 31's pilot did not choose the end-of-day basis; this study is that basis only")
    events = json.loads((src / "events.json").read_text(encoding="utf-8"))["events"]
    doc = json.loads((src / "calendar.json").read_text(encoding="utf-8"))
    reports, span = e31.reports_from_document(doc)
    names = sorted({e["symbol"] for e in events})
    data = e31.load_market_data(names, args.host, args.port)
    moves = {
        s: e31.reaction_moves(s, [(date.fromisoformat(x), w) for x, w in v], data)
        for s, v in doc["reports"].items()
    }
    cfg = study_config()
    if not spread_checks_off(cfg):
        raise SystemExit("the spread ceiling is not off in the study config")
    store = chainfetch.db_path(settings.load(None))
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    try:
        records = e31.run_study(events, reports, span, PaperScreenStudy(data, e31.Chains(conn), cfg, moves))
    finally:
        conn.close()
    fomc, cpi = e31.macro_calendar(src / "macro_calendar.json")
    result = summarise_results(records, fomc, cpi)
    (out_dir / "trades.json").write_text(json.dumps(e31.clean(records)), encoding="utf-8")
    path = out_dir / "results.json"
    path.write_text(json.dumps(e31.clean(result), indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(e31.clean(result)), encoding="utf-8")
    print(json.dumps({"verdict": {k: v["verdict"] for k, v in result["verdict"].items()}, "file": str(path)}))
    return 0


def cmd_report(args) -> int:
    d = _dir(args.out_dir, "item67")
    src = d / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def report(result: dict) -> str:
    """results.md beside results.json, for the console's studies tab."""
    arms = result["arms"]
    lines = [
        "# Item 67: the earnings module's rule with paper's spread screen",
        "",
        f"End-of-day basis, {result['window']}. Item 31's replay with its two spread checks off. Net and "
        "paired figures are points of capital at risk.",
        "",
        "## Verdicts",
        "",
        "| arm | verdict | design pairs | mean net | t | mean paired | t | holdout net | holdout paired |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, a in arms.items():
        d, h = a["design"], a["holdout"]
        lines.append(
            f"| {arm} | {a['verdict'].lower()} | {d['pairs']} | {e31._pts(d['net']['mean'])}"
            f" | {e31._num(d['net']['t'])} | {e31._pts(d['pair']['mean'])} | {e31._num(d['pair']['t'])}"
            f" | {e31._pts(h['net']['mean'])} | {e31._pts(h['pair']['mean'])} |"
        )
    lines += [
        "",
        "## By item 31's admission (reported, not judged)",
        "",
        "| arm | window | group | events | pairs | mean net | mean paired | mean ATM spread |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm, ws in result["admission_split"].items():
        for w, groups in ws.items():
            for g, m in groups.items():
                lines.append(
                    f"| {arm} | {w} | {g} | {m['events']} | {m['pairs']} | {e31._pts(m['mean_net'])}"
                    f" | {e31._pts(m['mean_paired'])} | {e31._num(m['mean_atm_spread_pct'], 3)} |"
                )
    lines += [
        "",
        "## By ATM-spread tercile, narrowest to widest (mean net, reported, not judged)",
        "",
        "| arm | window | low | middle | high |",
        "|---|---|---|---|---|",
    ]
    for arm, ws in result["atm_spread_terciles"].items():
        for w, t in ws.items():
            cells = " | ".join(e31._pts(x) for x in (t or [None, None, None]))
            lines.append(f"| {arm} | {w} | {cells} |")
    lines += ["", "## Declared limits", "", *[f"- {x}" for x in result["declared_limits"]]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, text in (
        ("mde", cmd_mde, "the minimum detectable effect from item 31's per-trade spread (local)"),
        ("study", cmd_study, "the replay with paper's spread screen; writes results.json and results.md"),
    ):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=3306)
        sp.add_argument("--inputs-dir", help="item 31's results directory (read only)")
        sp.add_argument("--out-dir")
        sp.set_defaults(fn=fn)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--out-dir")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
