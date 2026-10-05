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
    if args.run_k:
        ks = tuple(int(k) for k in args.run_k.split(",") if k.strip())
        out["run_hedge"] = analytics.run_hedge_overlay(
            conn, start=args.start, end=args.end, symbol=args.symbol, arm=args.arm, ks=ks
        )
    print(json.dumps({"ok": True, **out}, indent=2))
    return 0


def cmd_debit_first_offsets(args) -> int:
    """Settled debit-first rows cut by how far out of the money the centre sat, in strikes and in
    delta, side by side (read-only)."""
    from cherrypick.flies import analytics

    conn = dbmod.connect(args.db)
    kwargs = {"start": args.start, "end": args.end, "symbol": args.symbol}
    out = {
        "ok": True,
        "strikes": analytics.debit_first_by_offset(conn, unit="strikes", **kwargs),
        "delta": analytics.debit_first_by_offset(conn, unit="delta", **kwargs),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_debit_ladder(args) -> int:
    """The settled debit-first shadow ladder per (direction, k), with the k = 0 anchor and the
    calibration against real delta-arm fills (read-only)."""
    from cherrypick.flies import analytics

    conn = dbmod.connect(args.db)
    out = analytics.debit_ladder(conn, start=args.start, end=args.end, symbol=args.symbol, arm=args.arm)
    if not args.detail:
        out["calibration"].pop("detail", None)
    print(json.dumps({"ok": True, **out}, indent=2))
    return 0


def cmd_selector_fit(args) -> int:
    """Fit the selector's model for a session from every settled row before it, each source scoped
    to its era by the ledger's own breaks. Prints it; with --write, persists it where the paper loop
    reads it at that session's first tick (read-only otherwise).

    A no-op that says so while the config declares no selector arm. Declared but disabled still
    fits, so the model is ready on the morning the arm is switched on."""
    from cherrypick.core import jsonio as _jsonio

    from cherrypick.flies import engine, paper_loop, selector, selector_replay

    config = load_config(args.config)
    params = engine.merged_params(config, args.arm)
    if args.arm not in _cfg.registry(config, label="flies") or not params.get("selector"):
        print(json.dumps({"ok": True, "skipped": f"no `{args.arm}` arm with a selector block declared"}))
        return 0
    cfg = selector.settings(params)
    session = args.session or next_session()
    conn = dbmod.connect(args.db)
    rows = selector_replay.load_rows(
        conn, start="0000-00-00", end=None, arms=cfg["sources"], symbol=args.symbol
    )
    model = selector.fit(
        [r for r in rows if not r.get("void_reason")],
        through=session,
        sources=cfg["sources"],
        arm_starts=selector.arm_starts(dbmod.measurement_breaks(conn), session, cfg["sources"]),
        min_sessions=cfg["min_sessions"],
        margin=cfg["margin"],
    )
    if args.write:
        path = _jsonio.write_json_atomic(paper_loop.selector_model_path(session), model, default=None)
        print(json.dumps({"ok": True, "written": str(path), "model_id": selector.model_id(model)}, indent=2))
    else:
        print(json.dumps({"ok": True, "model": model}, indent=2))
    return 0


def next_session(now=None) -> str:
    """The session a model fitted now is for: today when run before today's open on a trading day,
    otherwise the next trading day. The nightly job always lands in the second case; a hand run at
    08:00 lands in the first, and must not write tomorrow's model by mistake."""
    from cherrypick.core import calendar as _cal

    from cherrypick.flies import clock

    now = now or clock.now_et()
    today = now.date()
    if _cal.is_trading_day(today) and now.hour * 60 + now.minute < 9 * 60 + 30:
        return today.isoformat()
    return _cal.next_trading_day(today).isoformat()


def cmd_selector_replay(args) -> int:
    """Walk the selector forward over recorded sessions, out of sample, beside every benchmark it
    has to beat (read-only)."""
    from cherrypick.flies import selector_replay

    conn = dbmod.connect(args.db)
    out = selector_replay.run(
        conn,
        start=args.start,
        end=args.end,
        symbol=args.symbol,
        history_start=args.history_start,
        min_sessions=args.min_sessions,
        margin=args.margin,
    )
    if not args.folds:
        out["take_skip"]["selector"].pop("folds", None)
        out["two_structure"]["selector"].pop("folds", None)
    print(json.dumps(out, indent=2))
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


def cmd_fill_model(args) -> int:
    """Live fill realism (the LIVE ledger) beside the paper shadow (the paper ledger): what live
    orders needed from the market, how well each first-touch rule reproduces them, and what the
    paper books would have done under that rule. Read-only on both ledgers."""
    from cherrypick.flies import analytics

    live = dbmod.connect(args.live_db or dbmod.live_db_path())
    paper = dbmod.connect(args.db)
    out = {
        "ok": True,
        "live": analytics.fill_realism(live, start=args.start, end=args.end),
        "shadow": analytics.shadow_completion(
            paper,
            start=args.start,
            end=args.end,
            symbol=args.symbol,
            arm=args.arm,
            basis=args.basis,
            cutoff=args.cutoff,
        ),
    }
    if not args.grid:
        out["live"].pop("rule_fit", None)
    print(json.dumps(out, indent=2, default=str))
    return 0


def backfill_events(conn, *, write: bool, root=None, restamp: bool = False) -> dict:
    """Stamp `entry_event_*` / `completion_event_*` on rows recorded before the event tag existed,
    from the calendar store alone (`cherrypick.core.events`). Only rows with no tag yet -- or, with
    `restamp`, every row, for when the calendar itself is corrected (a calendar tag is re-derivable
    exactly, so correcting one is a re-run, not a measurement break). Only days every calendar
    source can speak for: a day it cannot is left as it was, never stamped 'unknown' as if the loop
    had looked. Dry run unless `write`."""
    from datetime import date

    from cherrypick.core import events as _events

    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT position_id, trade_date, entry_time, completed_at FROM fly_positions "
            "WHERE entry_time IS NOT NULL "
            + ("" if restamp else "AND entry_event_bucket IS NULL ")
            + "ORDER BY trade_date"
        )
    ]
    days: dict = {}
    counts: dict = {"rows": len(rows), "stamped": 0, "unknown_day": 0, "buckets": {}}
    for r in rows:
        day = r["trade_date"]
        if day not in days:
            days[day] = _events.day_events(date.fromisoformat(day), root=root)
        doc = days[day]
        if not doc["known"]:
            counts["unknown_day"] += 1
            continue
        update = {"position_id": r["position_id"]}
        for phase, stamp in (("entry", r["entry_time"]), ("completion", r["completed_at"])):
            if not stamp:
                continue
            bucket, value, labels = _events.phase(doc, bookmod._minute_of_day(stamp))
            update.update(
                {
                    f"{phase}_event_bucket": bucket,
                    f"{phase}_event_value": value,
                    f"{phase}_event_labels": labels,
                }
            )
        counts["buckets"][update["entry_event_bucket"]] = (
            counts["buckets"].get(update["entry_event_bucket"], 0) + 1
        )
        counts["stamped"] += 1
        if write:
            dbmod.save_position(conn, update)
    counts["unknown_days"] = sorted(d for d, doc in days.items() if not doc["known"])
    return counts


def cmd_backfill_events(args) -> int:
    conn = dbmod.connect(args.db)
    out = backfill_events(conn, write=args.write, restamp=args.restamp)
    print(json.dumps({"ok": True, "write": args.write, **out}, indent=2))
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
    p_hedge.add_argument(
        "--run-k",
        help="also replay one hedge per (session, side) run of k open spreads, e.g. 2,3 (adds run_hedge)",
    )
    p_hedge.set_defaults(func=cmd_hedge_overlay)

    p_dfo = sub.add_parser(
        "debit-first-offsets",
        help="settled debit-first rows cut by strikes and by delta out of the money, side by side",
    )
    p_dfo.add_argument("--start")
    p_dfo.add_argument("--end")
    p_dfo.add_argument("--symbol")
    p_dfo.set_defaults(func=cmd_debit_first_offsets)

    p_ladder = sub.add_parser(
        "debit-ladder",
        help="the debit-first shadow ladder per strikes out, with its calibration against real fills",
    )
    p_ladder.add_argument("--start")
    p_ladder.add_argument("--end")
    p_ladder.add_argument("--symbol")
    p_ladder.add_argument("--arm", default="debit-first-atm")
    p_ladder.add_argument("--detail", action="store_true", help="include every calibration pair")
    p_ladder.set_defaults(func=cmd_debit_ladder)

    p_sfit = sub.add_parser(
        "selector-fit", help="fit the selector's frozen model for a session (prints; --write persists)"
    )
    p_sfit.add_argument("--session", help="the session the model is for (default: the next trading day)")
    p_sfit.add_argument("--arm", default="selector")
    p_sfit.add_argument("--symbol", default="SPX")
    p_sfit.add_argument("--write", action="store_true")
    p_sfit.set_defaults(func=cmd_selector_fit)

    p_srep = sub.add_parser(
        "selector-replay", help="walk the selector forward over recorded sessions, out of sample (read-only)"
    )
    p_srep.add_argument("--start", default="2026-08-21", help="first session replayed")
    p_srep.add_argument("--end")
    p_srep.add_argument("--symbol", default="SPX")
    p_srep.add_argument("--history-start", dest="history_start", default="2026-08-21")
    p_srep.add_argument("--min-sessions", dest="min_sessions", type=int, default=5)
    p_srep.add_argument("--margin", type=float, default=0.0)
    p_srep.add_argument("--folds", action="store_true", help="include each session's choice reasons")
    p_srep.set_defaults(func=cmd_selector_replay)

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

    p_events = sub.add_parser(
        "backfill-events", help="stamp the day's scheduled releases on rows recorded before the tag"
    )
    p_events.add_argument("--write", action="store_true", help="write (default: dry run)")
    p_events.add_argument("--restamp", action="store_true", help="re-stamp every row, not only untagged ones")
    p_events.set_defaults(func=cmd_backfill_events)

    p_fill = sub.add_parser(
        "fill-model",
        help="live fill realism (distances, gaps, rule fit) beside the paper live-like completion shadow",
    )
    p_fill.add_argument("--start")
    p_fill.add_argument("--end")
    p_fill.add_argument("--arm", help="paper arm for the shadow (default: every legged arm)")
    p_fill.add_argument("--symbol")
    p_fill.add_argument("--basis", choices=("mid", "natural", "dist"), default="mid")
    p_fill.add_argument("--cutoff", default="15:30", help="the shadow's completion cutoff, HH:MM ET")
    p_fill.add_argument("--live-db", help="the live ledger (default: the live ledger's own path)")
    p_fill.add_argument("--grid", action="store_true", help="include the per-value rule fit")
    p_fill.set_defaults(func=cmd_fill_model)

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
