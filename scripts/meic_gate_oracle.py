"""The gates-off oracle on one MEIC arm (`cherrypick.backtest.gateoracle`): what each gate it has on adds.

    python scripts/meic_gate_oracle.py --arm pm-200 [--window design|validation] [--workers 8]

Replays the arm, the arm with every listed gate off (`<arm>-open`) and the arm without each gate in
turn (`<arm>-no-<gate>`) through the backtest's own MEIC replay over the 60 s vendor store,
provisionally (MEIC history has not unlocked, so no run records a trial). The holdout is refused: a
read must not spend what a declared study will need. Writes `<arm>-<window>.json` and `.md` under the
backtest data dir (results/research/gate-oracle/), never into the repo: the inputs are licensed.

It is a read, not a verdict. A gate whose leave-one-out difference is negative helps the arm; one
whose attributed trades net positive kept winners out. Neither moves a live gate: that is a declared
study's job.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cherrypick.backtest import gateoracle, paths, runner
from cherrypick.backtest import settings as _settings
from cherrypick.core import home

CADENCE = 60
WINDOWS = ("design", "validation")


def out_dir() -> Path:
    d = paths.results_dir() / "research" / "gate-oracle"
    d.mkdir(parents=True, exist_ok=True)
    return d


def registry_with_oracle(registry: dict, base_config: dict, arm: str) -> tuple[dict, list[str]]:
    """The machine's MEIC registry with the oracle's arms over `arm`, and the gates it has on."""
    profiles = dict(registry.get("profiles") or {})
    profile = profiles.get(arm)
    if not isinstance(profile, dict):
        raise SystemExit(f"the MEIC registry has no {arm!r} arm")
    params = {**base_config, **profile}
    on = [g.name for g in gateoracle.active(params)]
    if not on:
        raise SystemExit(f"{arm!r} has none of the listed gates on: nothing to read")
    added = gateoracle.oracle_profiles(arm, profile, params)
    clash = sorted(set(added) & set(profiles))
    if clash:
        raise SystemExit(f"the registry already declares {clash}: rename them before running the oracle")
    return {**registry, "profiles": {**profiles, **added}}, on


def read_run(run_id: str, arm: str) -> tuple[dict, list[str], list[dict], dict]:
    ledger_dir = paths.results_dir() / "runs" / run_id
    by_session: dict[str, dict[str, float]] = {}
    sessions, fills, ticks = [], [], {}
    for marker in sorted(ledger_dir.glob("*.ok.json")):
        day = marker.name.removesuffix(".ok.json")
        db = ledger_dir / f"{day}.db"
        if not db.is_file():
            continue
        net, f, t = gateoracle.read_session(db, (arm, gateoracle.arm_name(arm)))
        sessions.append(day)
        for a, v in net.items():
            by_session.setdefault(a, {})[day] = v
        fills += f
        ticks.update(t)
    return by_session, sessions, fills, ticks


def _money(x) -> str:
    return "n/a" if x is None else f"{x:+,.0f}"


def _t(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def report(doc: dict) -> str:
    s = doc["summary"]
    lines = [
        f"# Gates-off oracle: {s['base']} ({doc['window']}, provisional)",
        "",
        "A read, not a verdict. **Leave-one-out** is the arm without that gate minus the arm, paired per",
        "session: negative means the gate helps. **Kept out** is the net of the bare trigger's trades the",
        "arm refused for that gate on the same tick: negative means it kept losers out. A tick two gates",
        "would refuse is credited to the one MEIC checks first.",
        "",
        f"Sessions: {s['sessions']}. Bare trigger minus the arm: mean {_money(s['open_vs_base']['mean'])} a "
        f"session (t {_t(s['open_vs_base']['t'])}).",
        "",
        "| gate | leave-one-out mean / session | t | sessions differing | kept out: trades | kept out: net |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for g, r in s["gates"].items():
        loo = r["leave_one_out"]
        lines.append(
            f"| {g} | {_money(loo['mean'])} | {_t(loo['t'])} | {loo['sessions_differing']} | "
            f"{r['blocked_trades']} | {_money(r['blocked_net'])} |"
        )
    u = s["unattributed"]
    lines += [
        "",
        f"Bare-trigger trades the arm also took: {u[gateoracle.BOTH]['trades']}; refused for spacing, "
        f"capacity or window (the books had diverged): {u[gateoracle.PATH]['trades']} "
        f"(net {_money(u[gateoracle.PATH]['net'])}); no matching tick: {u[gateoracle.NO_ROW]['trades']}.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arm", required=True)
    p.add_argument("--window", choices=WINDOWS, default="design")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args(argv)

    settings = _settings.load(None)
    registry = json.loads((home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    base_config = json.loads(home.config_path("meic").read_text(encoding="utf-8"))
    oracle_registry, on = registry_with_oracle(registry, base_config, args.arm)
    reg_path = out_dir() / f"meic.risk.{args.arm}.json"
    reg_path.write_text(json.dumps(oracle_registry, indent=1), encoding="utf-8")
    os.environ["MEIC_RISK_CONFIG"] = str(reg_path)

    arms = [args.arm, gateoracle.arm_name(args.arm), *(gateoracle.arm_name(args.arm, g) for g in on)]
    run = runner.run(
        settings,
        arms,
        args.window,
        module="meic",
        cadence=CADENCE,
        provisional=True,
        workers=args.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    by_session, sessions, fills, ticks = read_run(run["run_id"], args.arm)
    doc = {
        "kind": "gate-oracle",
        "arm": args.arm,
        "window": args.window,
        "run_id": run["run_id"],
        "gates_on": on,
        "summary": gateoracle.summary(args.arm, on, by_session, sessions, gateoracle.attribute(fills, ticks)),
    }
    stem = out_dir() / f"{args.arm}-{args.window}"
    stem.with_suffix(".json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    stem.with_suffix(".md").write_text(report(doc), encoding="utf-8")
    print(
        json.dumps({"arm": args.arm, "gates_on": on, "sessions": len(sessions), "file": str(stem) + ".json"})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
