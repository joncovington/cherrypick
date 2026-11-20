"""The settings grid for one saved automation's Builder run (#1294): exit and width settings, each a
full replay, guarded against overfitting.

    python packages/backtest/scripts/exit_grid.py run --name iron-fly-1000 \\
        --run builder-iron-fly-1000-2611180930 --split 2026-03-02 --out grid-iron-fly-1000-2611181000

The rules are `cherrypick.backtest.exitgrid`; this script replays and writes the result:

- **The run** is one Builder result of the automation (`ideafiles.plan` names the newest long enough,
  and the split, as for the idea search). The automation replayed is the one that run stored, so the
  base here is the run itself: `base_matches_run` says whether the replay gave its trades again.
- **The replay** reads that run's own store over its whole span (`automation_backtest.prepare`, the
  Builder run's own choice) and replays the base and every setting `exitgrid.variants` names in one pass
  of `cherrypick.automations.driver`, each setting under its own name with its own group cap. A setting
  the module's check refuses is left out, named, before anything is replayed. A licensed store is read
  only while the licence allows new work.
- **The result** (`--out`, `grid-<automation>-<YYMMDDHHMM>`) goes beside the run it read
  (`paths.automations_dir`): licensed when the run was. Never into the repo. It stores every setting's
  trades over the whole span; the search reads the design part only, and the held-out part is read once
  per declared setting (`gridfiles.holdout`).

Progress is reported overall and per symbol (`core.progress`): the replay by session, then the
placebo's draws.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import automation_backtest as ab  # noqa: E402  (the Builder backtest's own store choice)
from cherrypick.automations import arms as ens_arms  # noqa: E402
from cherrypick.automations import config as ens_config  # noqa: E402
from cherrypick.automations import driver, events  # noqa: E402
from cherrypick.core import jsonio, progress  # noqa: E402
from cherrypick.core.clock import ET  # noqa: E402

from cherrypick.backtest import exitgrid, ideafiles, licensing, paths, settings  # noqa: E402

JOB = "exit_grid-run"


def checked(grid: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """The settings the module's own check accepts, and why each other was left out."""
    kept, refused = {}, {}
    for vid, row in grid.items():
        arm = {k: v for k, v in row["arm"].items() if k != "budget"}
        problems = ens_arms.validate(arm)
        if problems:
            refused[vid] = problems[0]
        else:
            kept[vid] = row
    return kept, refused


def _trade_key(t: dict) -> tuple:
    net = t.get("net")
    return (
        str(t.get("session")),
        round(float((net or {}).get("verdict") or 0.0), 2) if isinstance(net, dict) else net,
    )


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
    base = (doc.get("arms") or {}).get(args.name)
    if not isinstance(base, dict):
        print(json.dumps({"ok": False, "error": f"the run does not hold {args.name} as it ran"}))
        return 1
    symbol = str(doc["symbol"]).upper()
    start, end = (doc.get("sessions") or [None, None])[0], (doc.get("sessions") or [None, None])[-1]
    if not start or not start < args.split <= end:
        print(json.dumps({"ok": False, "error": f"the split {args.split} is not inside the run's span"}))
        return 1
    grid, refused = checked(exitgrid.variants(base))
    if not grid:
        print(json.dumps({"ok": False, "error": "no setting of the grid applies to this automation"}))
        return 1
    chosen = {args.name: {**base, "enabled": True}}
    chosen.update({row["arm"]["name"]: {**row["arm"], "enabled": True} for row in grid.values()})
    try:
        source, _store, h, vix_daily, volumes = ab.prepare(
            {args.name: chosen[args.name]}, symbol, start, end, doc.get("source"), s, min_density=0.0
        )
    except ab.Refused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    cfg = ens_config.load_config()
    calendar = events.load_calendar((cfg.get("events") or {}).get("path"))
    sessions = [d for d in h.sessions if start <= d <= end]
    book = driver.Book()
    with progress.Reporter(JOB) as rep:
        rep.split("sessions", [symbol] * len(sessions), unit="days")
        for session in sessions:
            book = driver.run(
                chosen,
                h,
                [session],
                events=calendar,
                costs=cfg,
                vix_daily=vix_daily,
                volumes=volumes,
                book=book,
            )
            rep.advance("sessions", key=symbol, current=session)
        by_arm: dict[str, list[dict]] = {n: [] for n in chosen}
        for t in book.trades:
            by_arm.setdefault(t["arm"], []).append(t)
        base_trades = by_arm.pop(args.name, [])
        settings_trades = {vid: by_arm.get(row["arm"]["name"], []) for vid, row in grid.items()}
        rep.split("draws", [symbol] * args.draws, unit="draws")
        searched = exitgrid.search(
            base_trades,
            settings_trades,
            grid,
            base,
            sessions,
            args.split,
            draws=args.draws,
            tick=lambda _done: rep.advance("draws", key=symbol),
        )
    matches = sorted(map(_trade_key, base_trades)) == sorted(map(_trade_key, doc.get("trades") or []))
    result = {
        "name": args.out,
        "kind": "settings_grid",
        "automation": args.name,
        "run": args.run,
        "symbol": symbol,
        "source": source.name,
        "licensed": licensed or source.licensed,
        "sessions": [start, end],
        "replayed_sessions": sessions,
        "base_matches_run": matches,
        "grid": {vid: {k: row[k] for k in ("axis", "value", "words")} for vid, row in grid.items()},
        "refused": refused,
        **searched,
        "trades": {"base": base_trades, **settings_trades},
        "declared": [],
        "held_out_uses": [],
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
    }
    out = paths.automations_dir(result["licensed"]) / f"{args.out}.json"
    jsonio.write_json_atomic(out, result)
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(out),
                "tried": searched["tried"],
                "survivors": searched["survivors"],
                "base_matches_run": matches,
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="replay the grid over one Builder run's span and write the result")
    rn.add_argument("--name", required=True, help="the saved automation")
    rn.add_argument("--run", required=True, help="the Builder run's result name")
    rn.add_argument("--split", required=True, help="the first held-out day, fixed when queued")
    rn.add_argument("--out", required=True, help="the result's file name")
    rn.add_argument("--draws", type=int, default=exitgrid.DRAWS)
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
