"""Item 92: IV rank and realised-under-implied vol as entry filters for monthly short premium.

The declaration is research/edge-hypotheses-plan.md section 92; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Declared, not yet built: `requests` and `trades` raise until the replay is written in a later change,
before any run is queued. Only the filters' cycle counts were computed before declaring, to set the
floors (no trade, price path or outcome was read).

- `plan`: write the chain-path requests this study needs (`{results}/path_requests.json`).
- `study`: one run over the whole history, judged by the declared RULE (the study kit's three bars,
  cut at its splits), written to `{results}/results.json` as the kit's verdict block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, filled by the declaration before any run. Every None must become a value:
#: the kit refuses a rule with one, so nothing is judged until the declaration says how.
#: Rows are one per monthly cycle: the entries the arm took that cycle, the funds pooled.
RULE = {
    "t_bar": 2.4,
    "min_n": 30,
    "holdout_min_n": 30,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 92},
    "delta": True,
    "splits": ["2021-06-01"],
}

#: The second rule, the filter's own information: per entry, net over buying power, the entries the
#: filter keeps against the ones it skips on the same structure. One-sided t on the difference of
#: means, standard errors clustered by cycle. Design: t >= t_bar. Holdout, read only after a design
#: pass of both rules: the difference above zero. Either side under its floor is no verdict.
FILTER_RULE = {
    "t_bar": 2.4,
    "min_entries": 20,
    "holdout_min_entries": 20,
    "cluster": "cycle",
    "split": "2021-06-01",
}

#: The base trade, declared (the plan's section 92 states each in words).
SPEC = {
    "funds": {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX", "GLD": "GVZ"},
    "expiry": "the monthly (third-Friday) expiry",
    "entry": "the first trading session 60 calendar days before the expiry, or the next; none under 45",
    "first_expiry": "2016-03-18",
    "last_expiry": "2026-10-16",
    "structures": {
        "put": {"short_delta": 0.16},
        "spread": {"short_delta": 0.20, "long_delta": 0.10},
    },
    "exit": {
        "profit_target_pct_of_credit": 50,
        "stop_loss_multiple_of_credit": 1.0,
        "spread_stop_pct_of_max_loss_when_credit_stop_cannot_fire": 50,
        "close_days_to_expiry": 21,
        "marks": "each session's close; a session without a mark holds",
    },
    "fills": "mid with core's slippage model (verdict), core option fees",
    "buying_power": {"put": "strike x 100 (cash-secured)", "spread": "width x 100 less the credit"},
    "ivr_lookback": 252,
    "hv_returns": 30,
}

#: The judged arms: each structure with each filter, read on the session before entry.
FILTERS = {"ivr30": "vol index IV rank >= 30", "hv_lt_iv": "30-day realised vol < the vol index"}
ARMS = tuple(f"{s}-{f}" for s in SPEC["structures"] for f in FILTERS)
#: Reported, never judged: the base (no filter), the recipe's own thresholds, and the low-rank side.
REPORTED = ("put-base", "spread-base", "put-ivr50", "put-ivr70", "spread-ivr50", "spread-ivr70")

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
#: A sample of the paths is fetched first and the study runs on it (`smoke`), so a crash or an arm that
#: judges no trades shows in minutes, not after the whole fetch (#1072); the queue's study-first
#: rule runs the smoke as soon as the sample is in.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item92"
    d.mkdir(parents=True, exist_ok=True)
    return d


def requests() -> list[dict]:
    """The chain paths the study reads: one request per expiry path (see `fetch-chain-paths`)."""
    raise NotImplementedError("item 92: list the paths the declaration needs")


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def trades(inputs: Inputs, conn) -> dict[str, list[dict]]:
    """Each arm's trades from one run over the whole history, each with its entry `date`, `net`,
    `bp`, `days`, `rate` and, when the delta bar applies, `financed` (see the kit's `rule`). Read
    the store only through `inputs` (`inputs.sessions`, `inputs.chain`): the paths this study asked
    for, never another study's."""
    raise NotImplementedError("item 92: replay the declared rule")


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": requests()}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path)}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    try:
        block = kit_rule.evaluate(RULE, trades(inputs, conn))
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 92, **block}
    doc["inputs"] = read
    if args.smoke:
        # A look at the sample, never a verdict: the queue fails it on an arm with no trades.
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
