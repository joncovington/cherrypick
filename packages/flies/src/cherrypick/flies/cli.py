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
import pathlib
import sys

# Package root (holds config.json / config.example.json): src/cherrypick/flies/cli.py -> four
# parents up. Was _HERE/".." when the modules lived flat in src/. This one fails LOUDLY if wrong
# (SystemExit "no config found"), unlike gex's equivalent, but fix it for the same reason.
_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import config as _cfg  # noqa: E402
from cherrypick.core import home as _core_home  # noqa: E402

from cherrypick.flies import book as bookmod  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import engine  # noqa: E402


def load_config(path: str | None = None) -> dict:
    """This module's config, by the suite's precedence — see
    `cherrypick.core.home.load_module_config`, which three modules had written out identically."""
    return _core_home.load_module_config("flies", _PKG_ROOT, path)


def enabled_arms(config: dict) -> list[str]:
    arms = _cfg.registry(config, label="flies")
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


def cmd_bands(args) -> int:
    """Where each book's band sat relative to the range the session actually printed.

    Asked for by the advisor on 2026-08-18, repeated 08-19 and 08-20, and sharpened on 08-21 after
    its own single-edge classifier was contradicted by the wing experiment. See
    `analytics.band_placement` for the metric and why it reads BOTH edges.

    The session range comes from the shared stream cache (`day_high`/`day_low`), not this module's
    own tick record: the 08-21 breach was 0.11 points and `fly_iterations` samples the underlying,
    so a sampled extreme cannot measure a margin that fine.
    """
    import sqlite3

    from cherrypick.flies import analytics, paper_loop

    config = load_config(args.config)
    conn = dbmod.connect(args.db)
    sessions = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM fly_books ORDER BY 1")]
    symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM fly_books")]

    cache_path = args.stream_cache or paper_loop.stream_cache_path(config)
    cache = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        ranges = analytics.session_ranges_from_cache(cache, sessions, symbols)
    finally:
        cache.close()

    placements = analytics.band_placement(
        conn, ranges, start=args.start, end=args.end, arm=args.arm, symbol=args.symbol
    )
    out = {
        "ok": True,
        # The classifier first: a placement table is only readable next to whether the margin it
        # reports actually predicts the outcome it is meant to explain.
        "classifier": analytics.band_placement_classifier(placements),
        "placements": placements,
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_replay_gates(args) -> int:
    """Replay the miss-stop and trend-bucket entry gates over recorded rows (read-only)."""
    from cherrypick.flies import replay_gates

    conn = dbmod.connect(args.db)
    rows = replay_gates.load_rows(conn, start=args.start, end=args.end, arm=args.arm, symbol=args.symbol)
    out = replay_gates.sweep(rows)
    if not args.per_day:
        for block in (out["base"], *out["miss_stop"].values(), *out["trend_bucket"].values()):
            block.pop("per_day", None)
    print(
        json.dumps(
            {"ok": True, "arm": args.arm, "symbol": args.symbol, "start": args.start, "end": args.end, **out},
            indent=2,
        )
    )
    return 0


def cmd_hedge_overlay(args) -> int:
    """Settled legged positions repriced with and without the far-OTM hedge recorded at entry."""
    from cherrypick.flies import analytics

    conn = dbmod.connect(args.db)
    out = analytics.hedge_overlay(conn, start=args.start, end=args.end, symbol=args.symbol, arm=args.arm)
    print(json.dumps({"ok": True, **out}, indent=2))
    return 0


def cmd_reversal_book(args) -> int:
    """control paired with the same-side debit-first entry nearest in time: the two-fly book."""
    from cherrypick.flies import analytics

    conn = dbmod.connect(args.db)
    out = analytics.reversal_book(
        conn,
        start=args.start,
        end=args.end,
        symbol=args.symbol,
        base_arm=args.arm,
        window_minutes=args.window,
    )
    if not args.detail:
        out.pop("pairs_detail", None)
    print(json.dumps({"ok": True, **out}, indent=2))
    return 0


def regime_cuts_dir() -> str:
    """Where the artifact lands: beside advice_active.json, resolved the way paper_loop resolves it."""
    from cherrypick.flies import paper_loop

    return paper_loop._paper_data_dir()


def _gate_replay_scope(config: dict) -> dict:
    """What the artifact's `gate_replay` replays, read off what the config itself declares: the
    advisor's base arm, and the window choices of the `entry_windows` advice bound -- never a
    second hand-kept list of them."""
    advice = config.get("advice") or {}
    base = _cfg.first_present(advice, *_cfg.BASE_ARM_KEYS) or "control"
    rule = (advice.get("bounds") or {}).get("entry_windows") or {}
    return {"replay_arm": base, "replay_windows": list(rule.get("choices") or [])}


def cmd_regime_cuts(args) -> int:
    """Print (and with --write, persist) the regime-cuts artifact. `--backfill --since D` writes one
    dated artifact per settled session from D; the latest copy is only replaced by a newer session
    (see cherrypick.core.regimecuts.write_artifact)."""
    from cherrypick.core import regimecuts as _rc

    from cherrypick.flies import analytics, clock

    conn = dbmod.connect(args.db)
    replay = _gate_replay_scope(load_config(args.config))
    if args.backfill:
        if not args.write:
            print(json.dumps({"ok": False, "error": "--backfill needs --write"}))
            return 2
        since = args.since or "0000-00-00"
        sessions = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_date FROM fly_positions WHERE status = 'settled' AND trade_date >= ? "
                "ORDER BY trade_date",
                (since,),
            )
        ]
        written = []
        for day in sessions:
            doc = analytics.regime_cuts(conn, session=day, symbol=args.symbol, **replay)
            written.append(_rc.write_artifact(regime_cuts_dir(), doc))
        print(json.dumps({"ok": True, "sessions": sessions, "written": written}, indent=2))
        return 0
    session = args.session or clock.today_iso()
    doc = analytics.regime_cuts(conn, session=session, symbol=args.symbol, **replay)
    out = {"ok": True, "written": _rc.write_artifact(regime_cuts_dir(), doc) if args.write else None, **doc}
    print(json.dumps(out, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    from cherrypick.flies import analytics as _analytics

    ap = argparse.ArgumentParser(prog="flies", description="0DTE net-credit butterfly paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    p_hedge = sub.add_parser(
        "hedge-overlay",
        help="settled legged spreads repriced with and without the ~5-delta hedge recorded at entry",
    )
    p_hedge.add_argument("--start")
    p_hedge.add_argument("--end")
    p_hedge.add_argument("--arm", default="control")
    p_hedge.add_argument("--symbol")
    p_hedge.set_defaults(func=cmd_hedge_overlay)

    p_rev = sub.add_parser(
        "reversal-book", help="control paired with the same-side debit-first entry nearest in time"
    )
    p_rev.add_argument("--start")
    p_rev.add_argument("--end")
    p_rev.add_argument("--arm", default="control", help="the legged base arm")
    p_rev.add_argument("--symbol")
    p_rev.add_argument("--window", type=float, default=10.0, help="max minutes between the two entries")
    p_rev.add_argument("--detail", action="store_true", help="include every pair")
    p_rev.set_defaults(func=cmd_reversal_book)

    p_bands = sub.add_parser(
        "bands", help="band placement against the session's realized range, and whether it predicts the floor"
    )
    p_bands.add_argument("--start")
    p_bands.add_argument("--end")
    p_bands.add_argument("--arm")
    p_bands.add_argument("--symbol")
    p_bands.add_argument("--stream-cache", dest="stream_cache", help="override the shared cache path")
    p_bands.set_defaults(func=cmd_bands)

    p_replay = sub.add_parser(
        "replay-gates",
        help="replay the miss-stop and trend-bucket entry gates over recorded rows (read-only)",
    )
    p_replay.add_argument(
        "--start", default="2026-08-21", help="trade_date >= (default: the advisor-era cutover)"
    )
    p_replay.add_argument("--end")
    p_replay.add_argument("--arm", default="control")
    p_replay.add_argument("--symbol", default="SPX")
    p_replay.add_argument(
        "--per-day", dest="per_day", action="store_true", help="include per-day P&L for each rule"
    )
    p_replay.set_defaults(func=cmd_replay_gates)

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
    p_regime.add_argument("--dimension", choices=sorted(_analytics.REGIME_DIMENSIONS), help="default: all")
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

    p_cuts = sub.add_parser(
        "regime-cuts",
        help="the regime-cuts artifact: every arm x every regime dimension, era-scoped by the "
        "measurement_breaks journal (cherrypick.core.regimecuts)",
    )
    p_cuts.add_argument("--session", help="trade date to cut as of (YYYY-MM-DD); default today (ET)")
    p_cuts.add_argument("--symbol", default="SPX")
    p_cuts.add_argument(
        "--write", action="store_true", help="write data/flies/regime_cuts-<session>.json (+ latest)"
    )
    p_cuts.add_argument(
        "--backfill", action="store_true", help="with --write: one artifact per settled session"
    )
    p_cuts.add_argument("--since", help="with --backfill: first session (YYYY-MM-DD)")
    p_cuts.set_defaults(func=cmd_regime_cuts)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
