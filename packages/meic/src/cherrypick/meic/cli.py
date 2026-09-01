"""Command-line surface for cherrypick-meic — the human read side.

Subcommands (all read-only):
    headline           per-arm results plus what is still open — the one-glance answer
    arms               the per-stream comparison, with period/symbol/era filters
    regime             outcomes grouped by the regime an entry was tagged with
    coverage           how much of the resolved book is regime-tagged at all
    exits              every resolved trade's outcome, expiries split OTM/ITM
    stops              the stop_trigger_ratio curve, or the per-session stop rollup
    gate-blocks        per-stream block reasons for one session
    settlement-audit   does the ledger reproduce the settlement convention it claims?
    gex-gate           what the negative-GEX entry gate refused, on the book's own rows

This module was the last in the suite without a CLI, which is why two of the verbs above name
questions that had been asked repeatedly and never run: `settlement-audit` (asked on five separate
dates) and `gex-gate` (four). `analytics.py` has carried both kinds of answer for a while; nothing
could invoke it without writing Python, so nothing did.

**Deliberately NOT here: anything that runs or writes.** The paper loop, the streamer, the ledger
writer and the broker client keep their own argv, and they are load-bearing exactly as they are —
`paper_loop` shells out to `python -m cherrypick.meic.db` and `...meic.tt` on every tick, and the
orchestrator's jobspec, onboarding and the suite's skills all name those module paths. Folding them
in would repoint the live loop to buy nothing a reader wanted. This CLI is additive.

Everything opens `?mode=ro`, matching `experiment.py` and `gate_health.py`: a read surface must not
be able to write the trading ledger. (Note the trade-off `streamer.py` documents for the SHARED
cache — a read-only handle can serve an un-checkpointed WAL snapshot. It is the right call here
anyway: the console reads this ledger read-only too, so the mirror test compares like with like.)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3


@contextlib.contextmanager
def _connect(db_path: str | None):
    """Read-only handle on the paper ledger (or an explicit `--db`), closed on the way out.

    `contextlib.closing` rather than `with sqlite3.connect(...)`: the connection's own context
    manager commits a transaction and leaves the handle open, which is not what the `with` here
    reads as and would hold the WAL for the life of the process.
    """
    from cherrypick.meic import paths as _paths

    path = db_path or str(_paths.paper_db_path())
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    with contextlib.closing(conn):
        yield conn


def _emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _scope(args) -> dict:
    """The filters every read here shares. `era` defaults to the module's own evidence window."""
    return {
        "start": getattr(args, "start", None),
        "end": getattr(args, "end", None),
        "symbol": getattr(args, "symbol", None),
        "era": getattr(args, "era", None) or _current_era(),
    }


def _current_era() -> str:
    from cherrypick.meic import analytics

    return analytics.CURRENT_ERA


def cmd_headline(args) -> int:
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit({"ok": True, "headline": analytics.headline(conn, **_scope(args))})


def cmd_arms(args) -> int:
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit({"ok": True, "arms": analytics.by_arm(conn, **_scope(args))})


def cmd_regime(args) -> int:
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        scope = _scope(args)
        return _emit(
            {
                "ok": True,
                "dimension": args.dimension,
                "buckets": analytics.by_regime(conn, args.dimension, arm=args.arm, **scope),
            }
        )


def cmd_coverage(args) -> int:
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit({"ok": True, "coverage": analytics.regime_coverage(conn, arm=args.arm, **_scope(args))})


def cmd_exits(args) -> int:
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit({"ok": True, "exits": analytics.by_exit_detail(conn, arm=args.arm, **_scope(args))})


def cmd_stops(args) -> int:
    """The stop question two ways: the whole ratio curve, or what each session actually paid."""
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        scope = _scope(args)
        if args.sessions:
            return _emit(
                {"ok": True, "by_session": analytics.stop_session_rollup(conn, arm=args.arm, **scope)}
            )
        return _emit({"ok": True, "grid": analytics.stop_grid(conn, arm=args.arm, **scope)})


def cmd_gate_blocks(args) -> int:
    # `cherrypick.core.clock.today_iso` rather than a fourth private copy: this module already
    # carries three ("today in ET" in db.py, gate_health.py and paper_loop.py), and gate_health's
    # own docstring records what the last divergence cost.
    from cherrypick.core import clock as _clock
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        session = args.date or _clock.today_iso()
        return _emit(
            {
                "ok": True,
                "session": session,
                "blocks": analytics.gate_blocks(conn, session, symbol=args.symbol),
            }
        )


def cmd_settlement_audit(args) -> int:
    """Asked for on 2026-08-17, 08-18, 08-19, 08-20 and 08-21. Defaults to era=ALL: the question is
    whether the LEDGER reproduces its own convention, and scoping that to the current era would hide
    exactly the historical rows an audit exists to find."""
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit(
            {
                "ok": True,
                "audit": analytics.settlement_audit(
                    conn, start=args.start, end=args.end, symbol=args.symbol, era=args.era or "ALL"
                ),
            }
        )


def cmd_gex_gate(args) -> int:
    """Asked for on 2026-08-18, 08-20, 08-21 and 08-25. Era "ALL" by default for the reason stated
    in `gex_gate_counterfactual`: `open` was renamed `control` at the cutover and the registry
    records them as one continuous stream."""
    from cherrypick.meic import analytics

    with _connect(args.db) as conn:
        return _emit(
            {
                "ok": True,
                "counterfactual": analytics.gex_gate_counterfactual(
                    conn, start=args.start, end=args.end, symbol=args.symbol, era=args.era or "ALL"
                ),
            }
        )


def _add_scope(parser, *, symbol=True) -> None:
    parser.add_argument("--start", help="ISO date, inclusive")
    parser.add_argument("--end", help="ISO date, inclusive")
    if symbol:
        parser.add_argument("--symbol", help='one symbol, or "ALL"')
    parser.add_argument("--era", help='sampling era; "ALL" for an explicit cross-era read')


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="meic", description="MEIC 0DTE iron-condor module — read side")
    ap.add_argument("--db", help="paper ledger path (default: the resolved paper DB)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("headline", help="per-arm results plus what is still open")
    _add_scope(p)
    p.set_defaults(func=cmd_headline)

    p = sub.add_parser("arms", help="the per-stream comparison")
    _add_scope(p)
    p.set_defaults(func=cmd_arms)

    p = sub.add_parser("regime", help="outcomes grouped by the regime an entry was tagged with")
    p.add_argument("dimension", help="one of analytics.REGIME_DIMENSIONS")
    p.add_argument("--arm")
    _add_scope(p)
    p.set_defaults(func=cmd_regime)

    p = sub.add_parser("coverage", help="how much of the resolved book is regime-tagged at all")
    p.add_argument("--arm")
    _add_scope(p)
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("exits", help="resolved outcomes, expiries split OTM/ITM")
    p.add_argument("--arm")
    _add_scope(p)
    p.set_defaults(func=cmd_exits)

    p = sub.add_parser("stops", help="the stop_trigger_ratio curve, or the per-session rollup")
    p.add_argument("--arm")
    p.add_argument("--sessions", action="store_true", help="per-session rollup instead of the grid")
    _add_scope(p)
    p.set_defaults(func=cmd_stops)

    p = sub.add_parser("gate-blocks", help="per-stream block reasons for one session")
    p.add_argument("--date", help="ISO date; default today (ET)")
    p.add_argument("--symbol")
    p.set_defaults(func=cmd_gate_blocks)

    p = sub.add_parser("settlement-audit", help="does the ledger reproduce its own settlement convention?")
    _add_scope(p)
    p.set_defaults(func=cmd_settlement_audit)

    p = sub.add_parser("gex-gate", help="what the negative-GEX entry gate refused")
    _add_scope(p)
    p.set_defaults(func=cmd_gex_gate)

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
