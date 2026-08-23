"""Command-line surface for cherrypick-curve.

Subcommands (all read-only):
    status          open positions, the target expiration, today's regime read
    regime          the regime series (curve_regime) plus the module's own read of it
    worksheet       the live per-position worksheet
    exposure        the early-assignment-exposure telemetry
    regime-history  the VIX/VIX3M regime replay over stored history — a SEPARATION BENCHMARK for
                    the signal, never suite P&L; see `regime_history.py`.

The paper loop's own argv (`python -m cherrypick.curve.paper_loop --once|--interval|--settle|
--status`) is what the orchestrator drives; this CLI is the human read side.
"""

from __future__ import annotations

import argparse
import json
import pathlib

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    return _core_home.load_module_config("curve", _PKG_ROOT, path)


def cmd_status(args) -> int:
    from cherrypick.curve import db, paper_loop

    config = load_config(args.config)
    conn = db.connect(args.db)
    print(
        json.dumps(
            paper_loop.run_status(config, conn, cache_path=paper_loop.stream_cache_path(config)),
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_regime(args) -> int:
    from cherrypick.curve import analytics, db

    conn = db.connect(args.db)
    print(
        json.dumps(
            {"ok": True, "regime_series": analytics.regime_series(conn, limit=args.limit)},
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_worksheet(args) -> int:
    from cherrypick.curve import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "worksheet": analytics.worksheet(conn)}, indent=2, default=str))
    return 0


def cmd_exposure(args) -> int:
    from cherrypick.curve import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "exposure": analytics.exposure(conn)}, indent=2, default=str))
    return 0


def cmd_headline(args) -> int:
    from cherrypick.curve import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "headline": analytics.headline(conn)}, indent=2, default=str))
    return 0


def cmd_regime_history(args) -> int:
    from cherrypick.curve import regime_history

    config = load_config(args.config)
    result = regime_history.build(config)
    if not args.no_write:
        regime_history.write(result)
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="curve", description="VXX call-credit-spread paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions, target expiration, today's regime read").set_defaults(
        func=cmd_status
    )
    p_regime = sub.add_parser("regime", help="the stored daily regime series")
    p_regime.add_argument("--limit", type=int, default=60)
    p_regime.set_defaults(func=cmd_regime)
    sub.add_parser("worksheet", help="the live per-position worksheet").set_defaults(func=cmd_worksheet)
    sub.add_parser("exposure", help="early-assignment-exposure telemetry").set_defaults(func=cmd_exposure)
    sub.add_parser("headline", help="per-book results through the analytics layer").set_defaults(
        func=cmd_headline
    )
    p_hist = sub.add_parser(
        "regime-history",
        help="replay the VIX/VIX3M regime classification over stored history — a separation "
        "benchmark for the signal, never suite P&L",
    )
    p_hist.add_argument(
        "--no-write", action="store_true", help="print only; do not write regime-history.json"
    )
    p_hist.set_defaults(func=cmd_regime_history)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
