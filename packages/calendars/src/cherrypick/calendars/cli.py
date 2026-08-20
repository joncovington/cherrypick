"""Command-line surface for cherrypick-calendars.

Subcommands (all read-only over the module's own ledger):
    status     open positions and the current week plan
    headline   per-book, per-structure results through the analytics layer
    policies   the derived exit-policy comparison table — the module's whole point
    validate   the derivation checked against the control book's real recorded results

The paper loop's own argv (`python -m cherrypick.calendars.paper_loop --once|--interval|--settle|
--status`) is what the orchestrator drives; this CLI is the human read side.
"""

from __future__ import annotations

import argparse
import json
import pathlib

# Package root (holds config.example.json): src/cherrypick/calendars/cli.py -> parents[3] — the package root.
_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    """This module's config, by the suite's precedence — see
    `cherrypick.core.home.load_module_config`, which three modules had written out identically."""
    return _core_home.load_module_config("calendars", _PKG_ROOT, path)


def cmd_status(args) -> int:
    from cherrypick.calendars import clock, db

    conn = db.connect(args.db)
    print(
        json.dumps(
            {
                "ok": True,
                "week_plan": clock.week_plan(clock.now_et().date()),
                "open_positions": db.open_positions(conn),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_headline(args) -> int:
    from cherrypick.calendars import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "headline": analytics.headline(conn)}, indent=2, default=str))
    return 0


def cmd_policies(args) -> int:
    from cherrypick.calendars import db, exit_policies

    config = load_config(args.config)
    conn = db.connect(args.db)
    print(
        json.dumps(
            {"ok": True, "policies": exit_policies.comparison_table(conn, config)}, indent=2, default=str
        )
    )
    return 0


def cmd_validate(args) -> int:
    from cherrypick.calendars import db, exit_policies

    config = load_config(args.config)
    conn = db.connect(args.db)
    print(
        json.dumps(
            {"ok": True, "validation": exit_policies.validate_against_control(conn, config)},
            indent=2,
            default=str,
        )
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="calendars", description="weekly SPX double-calendar paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions and the current week plan").set_defaults(func=cmd_status)
    sub.add_parser("headline", help="per-book results through the analytics layer").set_defaults(
        func=cmd_headline
    )
    sub.add_parser("policies", help="the derived exit-policy comparison table").set_defaults(
        func=cmd_policies
    )
    sub.add_parser(
        "validate", help="derivation checked against the control book's real results"
    ).set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
