"""Command-line surface for cherrypick-pmcc.

Subcommands (all read-only over the module's own ledger):
    status     open positions, the current expiration plan, and keltner readiness
    headline   per-book, per-symbol results through the analytics layer
    worksheet  the live per-position worksheet (the user's spreadsheet, from the ledger)
    exposure   the early-assignment-exposure telemetry

The paper loop's own argv (`python -m cherrypick.pmcc.paper_loop --once|--interval|--settle|
--status`) is what the orchestrator drives; this CLI is the human read side.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib

# Package root (holds config.example.json): src/cherrypick/pmcc/cli.py -> three parents up.
_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])


def load_config(path: str | None = None) -> dict:
    """Explicit path, then PMCC_CONFIG, then the managed home, then the repo, then the example.
    The managed-home entry (`~/.cherrypick/config/pmcc.json`) is where the suite keeps per-module
    config and where `cherrypick doctor` looks."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    candidates = [
        path,
        os.environ.get("PMCC_CONFIG"),
        os.path.join(home, "config", "pmcc.json"),
        os.path.join(_PKG_ROOT, "config.json"),
        os.path.join(_PKG_ROOT, "config.example.json"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("no config found — copy config.example.json to config.json")


def cmd_status(args) -> int:
    from cherrypick.pmcc import analytics, clock, db

    config = load_config(args.config)
    conn = db.connect(args.db)
    symbols = [s.strip().upper() for s in (config.get("symbols") or [])]
    today = clock.today_iso()
    print(
        json.dumps(
            {
                "ok": True,
                "expiration_plan": clock.expiration_plan(clock.now_et().date(), config.get("defaults") or {}),
                "keltner_readiness": analytics.keltner_readiness(conn, symbols, today),
                "open_positions": db.open_positions(conn),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_headline(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "headline": analytics.headline(conn)}, indent=2, default=str))
    return 0


def cmd_worksheet(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "worksheet": analytics.worksheet(conn)}, indent=2, default=str))
    return 0


def cmd_exposure(args) -> int:
    from cherrypick.pmcc import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "exposure": analytics.exposure(conn)}, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pmcc", description="PMCC-99 deep-ITM covered-call paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions, expiration plan, keltner readiness").set_defaults(
        func=cmd_status
    )
    sub.add_parser("headline", help="per-book results through the analytics layer").set_defaults(
        func=cmd_headline
    )
    sub.add_parser("worksheet", help="the live per-position worksheet").set_defaults(func=cmd_worksheet)
    sub.add_parser("exposure", help="early-assignment-exposure telemetry").set_defaults(func=cmd_exposure)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
