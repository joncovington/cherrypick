"""Fetch the full option data set for SPX, QQQ, XSP, SPY, IWM, GLD and TLT from 2020 into the licensed store.

    python packages/backtest/scripts/fetch_full_set.py plan      # write the request files
    python packages/orchestrator/run.py queue-chain --study fullset \
        --script packages/backtest/scripts/fetch_full_set.py     # queue the fetches
    python packages/backtest/scripts/fetch_full_set.py status    # overall, per part, per underlying

What is asked for, and why each part has a store of its own, is `cherrypick.backtest.fullset`. This
script writes that module's request files into the set's result folder
(`results/research/fullset/` under the backtest data directory) and declares the chain the research
queue runs over them: a ten-request probe first, then the same-day stores, the end-of-day paths, open
interest, the near expiries at one minute and the middle expiries at five minutes, one underlying at
a time. Every fetch goes through the queue, so it keeps clear of the nightly fetch and of a held
vendor session; nothing here calls the vendor.

Needs the vendor configured and its key set (`python -m cherrypick.backtest status`). On a tier that
starts later than 2020 the vendor answers the earlier requests with nothing, recorded as `empty`.
Resumable: every fetch skips what its store already holds. To extend the set later, run `plan` again
(it plans through today) and queue the chain again.

Writes only the request files, and the stores the queued fetches write, under the backtest data
directory (the stores in the marked vendor directory). Prints counts only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import clock

from cherrypick.backtest import fullset, fullsetadd, fullsetprogress, paths
from cherrypick.backtest import settings as _settings
from cherrypick.backtest.fullsetprogress import progress

STUDY = "fullset"
SCRIPT_REL = "packages/backtest/scripts/fetch_full_set.py"

#: The queue's rows, in order (`queue-chain --study fullset`). The probe runs first: a tier or
#: entitlement the set cannot use fails it, and every row after it is held.
CHAIN = (
    {"step": "probe", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/sample.json", "workers": 2}},
    {"step": "zero-spx", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "SPX", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-qqq", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "QQQ", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-xsp", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "XSP", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-spy", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "SPY", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-iwm", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "IWM", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-gld", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "GLD", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-tlt", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "TLT", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "eod-paths", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/paths.json", "workers": 2}, "repeat": 2},
    {"step": "oi", "kind": "fetch-oi", "task_args": {"plan": "{results}/oi_plan.json", "workers": 2}},
    {"step": "near-spx", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_SPX.json", "workers": 2}, "repeat": 2},
    {"step": "near-qqq", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_QQQ.json", "workers": 2}, "repeat": 2},
    {"step": "near-xsp", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_XSP.json", "workers": 2}, "repeat": 2},
    {"step": "near-spy", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_SPY.json", "workers": 2}, "repeat": 2},
    {"step": "near-iwm", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_IWM.json", "workers": 2}, "repeat": 2},
    {"step": "near-gld", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_GLD.json", "workers": 2}, "repeat": 2},
    {"step": "near-tlt", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_TLT.json", "workers": 2}, "repeat": 2},
    {"step": "mid-spx", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_SPX.json", "workers": 2}, "repeat": 2},
    {"step": "mid-qqq", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_QQQ.json", "workers": 2}, "repeat": 2},
    {"step": "mid-xsp", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_XSP.json", "workers": 2}, "repeat": 2},
    {"step": "mid-spy", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_SPY.json", "workers": 2}, "repeat": 2},
    {"step": "mid-iwm", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_IWM.json", "workers": 2}, "repeat": 2},
    {"step": "mid-gld", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_GLD.json", "workers": 2}, "repeat": 2},
    {"step": "mid-tlt", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_TLT.json", "workers": 2}, "repeat": 2},
)  # fmt: skip

#: One added underlying's rows (`add`, then the orchestrator's `fullset_add`), `{symbol}` its symbol,
#: `{sym}` the same in lower case and `{from}` its first session: the same-day store under the shared
#: `added` tag, its end-of-day paths, open interest, then the near and middle expiries. No probe: the
#: set's own probe has passed by the time anything is added.
ADD_CHAIN = (
    {"step": "add-{sym}-zero", "kind": "fetch-sessions",
     "task_args": {"store_tag": "added", "dataset": "{symbol}", "cadence": 60, "from": "{from}",
                   "to": "{last_session}", "workers": 2}},
    {"step": "add-{sym}-eod", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/paths_{symbol}.json", "workers": 2}, "repeat": 2},
    {"step": "add-{sym}-oi", "kind": "fetch-oi",
     "task_args": {"plan": "{results}/oi_{symbol}.json", "workers": 2}},
    {"step": "add-{sym}-near", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_{symbol}.json", "workers": 2}, "repeat": 2},
    {"step": "add-{sym}-mid", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_{symbol}.json", "workers": 2}, "repeat": 2},
)  # fmt: skip

#: Bytes a stored quote row takes with its index, measured on the 60 s stores; an estimate only.
ROW_BYTES = 66


def out_dir() -> Path:
    return fullsetprogress.folder()


def bars(interval: str, start: str, end: str) -> int:
    """Bars in an HH:MM window at a 1m or 5m interval."""
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return minutes // int(interval.removesuffix("m")) + 1


def estimate(doc: dict) -> int:
    """Rows a request file would store at most (every candidate listed, every strike quoted)."""
    return sum(bars(r["interval"], r["start"], r["end"]) * 2 * (2 * r["strike_range"] + 1)
               for r in doc.get("requests", []) if "interval" in r)  # fmt: skip


def last_session() -> str:
    """The last finished session: the trading day before today, ET (the chain's `{last_session}`)."""
    return cal.previous_trading_day(clock.now_et().date()).isoformat()


def cmd_plan(args) -> int:
    through = args.through or last_session()
    files = fullset.plan(args.start, through, fullsetadd.load(out_dir()))
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    counts, gb = {}, 0.0
    for name, doc in files.items():
        (out / name).write_text(json.dumps(doc), encoding="utf-8")
        n = len(doc.get("requests", doc.get("sessions", {})))
        counts[name] = n
        if name != "sample.json":
            gb += estimate(doc) * ROW_BYTES / 1e9
    nxt = "python packages/orchestrator/run.py queue-chain --study fullset --script " + SCRIPT_REL
    print(json.dumps({"from": args.start, "through": through, "folder": str(out), "requests": counts,
                      "intraday_gb_at_most": round(gb, 1), "next": nxt}, indent=1))  # fmt: skip
    return 0


def cmd_add(args) -> int:
    """Add an underlying from `--from` (default a year before the last session) through the last
    session: record it, write its request files, and print their size. `--dry-run` writes nothing.
    Prints counts only, and what queues it: the orchestrator's `fullset_add`."""
    symbol = args.symbol.upper()
    through = args.through or last_session()
    start = args.start or (date.fromisoformat(through) - timedelta(days=365)).isoformat()
    out = out_dir()
    try:
        additions = fullsetadd.merged(fullsetadd.load(out), symbol, start, clock.now_et().date())
        files = fullset.plan_added(symbol, start, through)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    size = fullset.plan_size(files)
    doc = {"ok": True, "symbol": symbol, "from": start, "through": through, "requests": size,
           "dry_run": args.dry_run, "concurrent": _settings.concurrency(_settings.load(None))}  # fmt: skip
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        fullsetadd.add(symbol, start, clock.now_et().date(), out)
        for name, body in files.items():
            (out / name).write_text(json.dumps(body), encoding="utf-8")
        doc["additions"] = sorted(additions)
    print(json.dumps(doc))
    return 0


def cmd_status(args) -> int:
    """Overall, per-part and per-underlying progress against the plan. Counts only, read-only."""
    print(json.dumps(progress(paths.vendor_dir(_settings.load(None)), out_dir()), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="write the request files the chain reads")
    pl.add_argument("--from", dest="start", default=fullset.START)
    pl.add_argument("--through", default=None, help="last session (default: the last finished one, ET)")
    pl.set_defaults(fn=cmd_plan)
    ad = sub.add_parser("add", help="add an underlying to the set: record it and write its request files")
    ad.add_argument("symbol")
    ad.add_argument("--from", dest="start", default=None, help="first session (default: a year back)")
    ad.add_argument("--through", default=None, help="last session (default: the last finished one, ET)")
    ad.add_argument("--dry-run", action="store_true", help="print the plan's size, write nothing")
    ad.set_defaults(fn=cmd_add)
    sub.add_parser("status", help="how far each store has got").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
