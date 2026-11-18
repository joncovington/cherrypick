"""Search one saved automation's Builder run for entry filters worth declaring (#1294).

    python packages/backtest/scripts/idea_search.py run --name iron-fly-1000 \\
        --run builder-iron-fly-1000-2611180930 --split 2026-03-02 --out ideas-iron-fly-1000-2611181000

The search is `cherrypick.backtest.ideasearch`; this script gathers what it reads and writes the result:

- **The run** is one Builder result of the automation (`ideafiles.plan` names the newest long enough,
  and the split). A licensed run is read only while the licence allows new work.
- **Entry-time features** come from our own data: the symbol's daily bars (the stock history, or the
  recorder's index sessions for an index, as the Builder's backtest reads them), the VIX closes
  (`core.volindex`) and the automations module's event calendar.
- **The result** (`--out`, `ideas-<automation>-<YYMMDDHHMM>`) goes beside the run it read
  (`paths.automations_dir`): licensed when the run was. Never into the repo. It stores each session's
  features, so the one held-out test of a declared idea reads the same bins the search did.

Progress is reported overall and per symbol (`core.progress`): the features by session, then the
placebo's draws.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import automation_backtest as ab  # noqa: E402  (the Builder backtest's own bar loaders)
from cherrypick.automations import config as ens_config  # noqa: E402
from cherrypick.automations import events  # noqa: E402
from cherrypick.core import jsonio, progress, volindex  # noqa: E402
from cherrypick.core.clock import ET  # noqa: E402

from cherrypick.backtest import ideafiles, ideasearch, licensing, paths, settings  # noqa: E402

JOB = "idea_search-run"


def event_flags(calendar, sessions: list[str], symbol: str) -> dict[str, dict[str, bool | None]]:
    out = {}
    for s in sessions:
        d = date.fromisoformat(s)
        out[s] = {label: calendar.occurs(label, d, d, symbol) for label in ideasearch.EVENT_LABELS}
    return out


def cmd_run(args) -> int:
    s = settings.load(None)
    found = ideafiles._runs_by_name(args.name).get(args.run)
    if found is None:
        print(json.dumps({"ok": False, "error": f"no Builder run {args.run!r} of {args.name}"}))
        return 1
    doc, licensed = found
    if licensed and not licensing.status(s)["active"]:
        print(json.dumps({"ok": False, "error": "the licence does not allow new work on this licensed run"}))
        return 2
    symbol = str(doc["symbol"]).upper()
    trades = doc.get("trades") or []
    sessions = ideafiles._entry_sessions(doc)
    if not sessions or not sessions[0] < args.split <= sessions[-1]:
        print(json.dumps({"ok": False, "error": f"the split {args.split} is not inside the run's sessions"}))
        return 1
    bars = ab.stock_daily(symbol) or ab.index_daily(symbol)
    if not bars:
        print(json.dumps({"ok": False, "error": f"no daily bars for {symbol}"}))
        return 2
    cfg = ens_config.load_config()
    calendar = events.load_calendar((cfg.get("events") or {}).get("path"))
    vix = volindex.closes("VIX")
    with progress.Reporter(JOB) as rep:
        rep.split("features", [symbol] * len(sessions), unit="sessions")
        flags = {}
        for sess in sessions:
            flags.update(event_flags(calendar, [sess], symbol))
            rep.advance("features", key=symbol, current=sess)
        feats = ideasearch.features(sessions, bars, vix, flags)
        rep.split("draws", [symbol] * args.draws, unit="draws")
        searched = ideasearch.search(
            trades, feats, args.split, draws=args.draws, tick=lambda _done: rep.advance("draws", key=symbol)
        )
    result = {
        "name": args.out,
        "kind": "idea_search",
        "automation": args.name,
        "run": args.run,
        "symbol": symbol,
        "source": doc.get("source"),
        "licensed": licensed,
        "sessions": doc.get("sessions") or [sessions[0], sessions[-1]],
        "menu": ideasearch.dimensions(),
        **searched,
        "features": feats,
        "declared": [],
        "held_out_uses": [],
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
    }
    out = paths.automations_dir(licensed) / f"{args.out}.json"
    jsonio.write_json_atomic(out, result)
    print(
        json.dumps(
            {"ok": True, "path": str(out), "tried": searched["tried"], "survivors": searched["survivors"]}
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="search one Builder run and write the result")
    rn.add_argument("--name", required=True, help="the saved automation")
    rn.add_argument("--run", required=True, help="the Builder run's result name")
    rn.add_argument("--split", required=True, help="the first held-out day, fixed when queued")
    rn.add_argument("--out", required=True, help="the result's file name")
    rn.add_argument("--draws", type=int, default=ideasearch.DRAWS)
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
