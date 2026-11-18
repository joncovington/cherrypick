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
import sqlite3
import sys
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import clock

from cherrypick.backtest import fullset, paths
from cherrypick.backtest import settings as _settings

STUDY = "fullset"
SCRIPT_REL = "packages/backtest/scripts/fetch_full_set.py"
INTRADAY_LEDGER = (
    'SELECT symbol, session, expiration, start, "end", interval, status FROM intraday_chain_requests'
)

#: The queue's rows, in order (`queue-chain --study fullset`). The probe runs first: a tier or
#: entitlement the set cannot use fails it, and every row after it is held.
CHAIN = (
    {"step": "probe", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/sample.json", "workers": 2}},
    {"step": "zero-SPX", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "SPX", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-QQQ", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "QQQ", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-XSP", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "XSP", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-SPY", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "SPY", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-IWM", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "IWM", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-GLD", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "GLD", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "zero-TLT", "kind": "fetch-sessions",
     "task_args": {"store_tag": "full", "dataset": "TLT", "cadence": 60, "from": "2020-01-01",
                   "to": "{last_session}", "workers": 2}},
    {"step": "eod-paths", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/paths.json", "workers": 2}, "repeat": 2},
    {"step": "oi", "kind": "fetch-oi", "task_args": {"plan": "{results}/oi_plan.json", "workers": 2}},
    {"step": "near-SPX", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_SPX.json", "workers": 2}, "repeat": 2},
    {"step": "near-QQQ", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_QQQ.json", "workers": 2}, "repeat": 2},
    {"step": "near-XSP", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_XSP.json", "workers": 2}, "repeat": 2},
    {"step": "near-SPY", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_SPY.json", "workers": 2}, "repeat": 2},
    {"step": "near-IWM", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_IWM.json", "workers": 2}, "repeat": 2},
    {"step": "near-GLD", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_GLD.json", "workers": 2}, "repeat": 2},
    {"step": "near-TLT", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/near_TLT.json", "workers": 2}, "repeat": 2},
    {"step": "mid-SPX", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_SPX.json", "workers": 2}, "repeat": 2},
    {"step": "mid-QQQ", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_QQQ.json", "workers": 2}, "repeat": 2},
    {"step": "mid-XSP", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_XSP.json", "workers": 2}, "repeat": 2},
    {"step": "mid-SPY", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_SPY.json", "workers": 2}, "repeat": 2},
    {"step": "mid-IWM", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_IWM.json", "workers": 2}, "repeat": 2},
    {"step": "mid-GLD", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_GLD.json", "workers": 2}, "repeat": 2},
    {"step": "mid-TLT", "kind": "fetch-intraday-chains",
     "task_args": {"requests": "{results}/mid_TLT.json", "workers": 2}, "repeat": 2},
)  # fmt: skip

#: Bytes a stored quote row takes with its index, measured on the 60 s stores; an estimate only.
ROW_BYTES = 66


def out_dir() -> Path:
    return paths.results_dir() / "research" / STUDY


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
    files = fullset.plan(args.start, through)
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


#: A request is finished once its store records it complete or empty (the vendor held nothing); a
#: refused one is asked again by the chain's second pass.
FINISHED = ("complete", "empty")
PARTS = ("zero", "eod", "oi", "near", "mid")


def _rows(path: Path, sql: str) -> list[tuple]:
    """A read-only query of a store; nothing when it is absent or not yet that shape."""
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def part(done: int, total: int, refused: int = 0) -> dict:
    return {"done": done, "total": total, "refused": refused,
            "share": round(done / total, 4) if total else None}  # fmt: skip


def tally(planned: list[tuple], ledger: list[tuple]) -> dict:
    """How many planned keys the ledger records finished, and refused. `ledger` rows are the key's
    fields then the status. Pure."""
    status = {tuple(r[:-1]): r[-1] for r in ledger}
    done = sum(status.get(k) in FINISHED for k in planned)
    refused = sum(status.get(k) == "refused" for k in planned)
    return part(done, len(planned), refused)


def roll_up(parts: dict[str, dict]) -> dict:
    """One total over several parts (each counted by its own requests or sessions). Pure."""
    done = sum(p["done"] for p in parts.values())
    total = sum(p["total"] for p in parts.values())
    return part(done, total, sum(p["refused"] for p in parts.values()))


def _load(name: str) -> dict | None:
    p = out_dir() / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def progress(vendor: Path) -> dict:
    """Overall, per underlying, and per part within each, against the planned files. Counts only.
    The same-day part has no request file (the fetch lists the sessions an expiry fell on), so its
    total is the sessions in the span, a ceiling for underlyings without an expiry every day."""
    span = _load("oi_plan.json") or {"sessions": {}}
    days = sorted(span["sessions"])
    paths_doc = _load("paths.json") or {"requests": []}
    eod_ledger = _rows(vendor / "eod_chains.db",
                       "SELECT symbol, expiration, start, through, status FROM eod_chain_paths")  # fmt: skip
    oi_ledger = _rows(vendor / "open_interest.db", "SELECT symbol, session, status FROM oi_days")
    per: dict[str, dict] = {}
    for sym in fullset.SYMBOLS:
        spec = fullset.SPEC[sym]
        parts: dict[str, dict] = {}
        zero = _rows(vendor / fullset.zero_store(sym), "SELECT trade_date, status FROM vendor_sessions")
        parts["zero"] = tally([(d,) for d in days], zero)
        planned_eod = [(r["symbol"], r["expiration"], r["start"], r["through"])
                       for r in paths_doc["requests"] if r["symbol"] in spec["eod"]]  # fmt: skip
        parts["eod"] = tally(planned_eod, eod_ledger)
        parts["oi"] = tally([(spec["root"], d) for d in days], oi_ledger)
        for name, store in (("near", fullset.near_store(sym)), ("mid", fullset.mid_store(sym))):
            doc = _load(f"{name}_{sym}.json") or {"requests": []}
            planned = [(r["symbol"], r["session"], r["expiration"], r["start"], r["end"], r["interval"])
                       for r in doc["requests"]]  # fmt: skip
            ledger = _rows(vendor / store, INTRADAY_LEDGER)
            parts[name] = tally(planned, ledger)
        per[sym] = {"all": roll_up(parts), **parts}
    by_part = {p: roll_up({s: per[s][p] for s in per}) for p in PARTS}
    return {"overall": roll_up(by_part), "by_part": by_part, "by_symbol": per,
            "through": days[-1] if days else None}  # fmt: skip


def cmd_status(args) -> int:
    """Overall, per-part and per-underlying progress against the plan. Counts only, read-only."""
    print(json.dumps(progress(paths.vendor_dir(_settings.load(None))), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="write the request files the chain reads")
    pl.add_argument("--from", dest="start", default=fullset.START)
    pl.add_argument("--through", default=None, help="last session (default: the last finished one, ET)")
    pl.set_defaults(fn=cmd_plan)
    sub.add_parser("status", help="how far each store has got").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
