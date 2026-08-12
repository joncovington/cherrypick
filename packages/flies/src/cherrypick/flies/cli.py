"""Command-line surface for cherrypick-flies.

Subcommands:
    once      run one iteration of every enabled arm against a snapshot (JSON on stdin or --snapshot)
    settle    cash-settle a session's books at the settlement print
    status    print the current books

The snapshot is supplied by the caller rather than fetched here, keeping this package's decision path
free of network I/O — the same split MEIC uses between `paper_loop.py` (fetch) and `paper.py` (decide).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Package root (holds config.json / config.example.json): src/cherrypick/flies/cli.py -> four
# parents up. Was _HERE/".." when the modules lived flat in src/. This one fails LOUDLY if wrong
# (SystemExit "no config found"), unlike gex's equivalent, but fix it for the same reason.
_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.flies import book as bookmod  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import engine  # noqa: E402


def load_config(path: str | None = None) -> dict:
    """Explicit path, then FLIES_CONFIG, then the managed home, then the repo, then the example.

    The managed-home entry (`~/.cherrypick/config/flies.json`) matters: it is where the suite keeps
    per-module config and where `cherrypick doctor` looks. Without it, doctor reports the module as
    unconfigured while the module happily runs off its in-repo copy — the two disagreeing about
    where configuration lives is exactly how a machine ends up running settings nobody can find.
    """
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    candidates = [
        path,
        os.environ.get("FLIES_CONFIG"),
        os.path.join(home, "config", "flies.json"),
        os.path.join(_PKG_ROOT, "config.json"),
        os.path.join(_PKG_ROOT, "config.example.json"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("no config found — copy config.example.json to config.json")


def enabled_arms(config: dict) -> list[str]:
    arms = config.get("arms", {})
    return [a for a in engine.ARMS if arms.get(a, {}).get("enabled", True) and a in arms]


def _read_snapshot(args) -> dict:
    if args.snapshot:
        with open(args.snapshot, encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


def cmd_once(args) -> int:
    config = load_config(args.config)
    snapshot = _read_snapshot(args)
    conn = dbmod.connect(args.db)
    out = [bookmod.process_snapshot(snapshot, config, conn, arm) for arm in enabled_arms(config)]
    print(json.dumps({"ok": True, "books": out}, indent=2, default=str))
    return 0


def cmd_settle(args) -> int:
    config = load_config(args.config)
    conn = dbmod.connect(args.db)
    out = [
        bookmod.settle_book(conn, args.date, arm, args.symbol, args.price, config)
        for arm in enabled_arms(config)
    ]
    print(json.dumps({"ok": True, "books": out}, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    conn = dbmod.connect(args.db)
    q = "SELECT * FROM fly_books"
    params: list = []
    if args.date:
        q += " WHERE trade_date = ?"
        params.append(args.date)
    q += " ORDER BY id DESC LIMIT 50"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    print(json.dumps({"ok": True, "books": rows}, indent=2, default=str))
    return 0


def cmd_regime(args) -> int:
    """Regime-conditioned outcomes, plus the coverage guard that says whether to believe them."""
    from cherrypick.flies import analytics

    conn = dbmod.connect(args.db)
    edges = [float(e) for e in args.bucket_edges.split(",")] if args.bucket_edges else None
    coverage = analytics.regime_coverage(conn, args.start, args.end, args.symbol)
    dimensions = [args.dimension] if args.dimension else sorted(analytics.REGIME_DIMENSIONS)
    out = {
        "ok": True,
        # Printed first, and deliberately: a regime table is only readable next to how much of the
        # book carries the tag and whether the tag ever took more than one value.
        "coverage": coverage,
        "regimes": {
            dim: analytics.by_regime(
                conn,
                dim,
                start=args.start,
                end=args.end,
                symbol=args.symbol,
                bucket_edges=edges,
                phase=args.phase,
            )
            for dim in dimensions
        },
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flies", description="0DTE net-credit butterfly paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("once", help="one iteration of every enabled arm")
    p_once.add_argument("--snapshot", help="snapshot JSON file (default: stdin)")
    p_once.set_defaults(func=cmd_once)

    p_settle = sub.add_parser("settle", help="cash-settle a session's books")
    p_settle.add_argument("--date", required=True)
    p_settle.add_argument("--symbol", required=True)
    p_settle.add_argument("--price", type=float, required=True)
    p_settle.set_defaults(func=cmd_settle)

    p_status = sub.add_parser("status", help="print books")
    p_status.add_argument("--date")
    p_status.set_defaults(func=cmd_status)

    p_regime = sub.add_parser(
        "regime", help="outcomes grouped by the regime entered into, with a coverage guard"
    )
    p_regime.add_argument("--dimension", choices=["vol", "gex", "time", "skew"], help="default: all")
    p_regime.add_argument("--start", help="trade_date >= (YYYY-MM-DD)")
    p_regime.add_argument("--end", help="trade_date <= (YYYY-MM-DD)")
    p_regime.add_argument("--symbol", help="narrow to one underlying")
    p_regime.add_argument("--phase", choices=["entry", "completion"], default="entry")
    p_regime.add_argument(
        "--bucket-edges",
        dest="bucket_edges",
        help="comma-separated cuts applied to the RECORDED float instead of the stored bucket "
        "(e.g. 0.4,0.6,0.8) — re-derives a threshold from history without re-running sessions",
    )
    p_regime.set_defaults(func=cmd_regime)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
