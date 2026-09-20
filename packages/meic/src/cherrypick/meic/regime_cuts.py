"""`python -m cherrypick.meic.regime_cuts [--write] [--session D | --backfill --since D]`

The regime-cuts artifact writer (2026-09-19): every profile x every regime dimension, era-scoped
by this ledger's `measurement_breaks`, in the shape `cherrypick.core.regimecuts` defines. Its own
entry point rather than a `cli.py` verb on purpose: `cli.py` is pinned by its tests to be a read
surface that cannot write anything, and this writes `data/meic/regime_cuts-<session>.json` (plus
the latest copy). The ledger itself is opened read-only through `cli._connect`.

The orchestrator runs it nightly at `paper.regime_cuts_at` via `paper.regime_cuts_argv`.
"""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="meic regime-cuts", description=__doc__.splitlines()[2])
    ap.add_argument("--db", help="ledger path; default the module's paper ledger")
    ap.add_argument("--session", help="trade date to cut as of (YYYY-MM-DD); default today (ET)")
    ap.add_argument("--symbol", help="one symbol, or omit for every symbol")
    ap.add_argument("--era", help='sampling era; default the current era, "ALL" for none')
    ap.add_argument("--write", action="store_true", help="write the dated artifact (+ latest)")
    ap.add_argument("--backfill", action="store_true", help="with --write: one artifact per resolved session")
    ap.add_argument("--since", help="with --backfill: first session (YYYY-MM-DD)")
    return ap


def main(argv=None) -> int:
    from cherrypick.core import clock as _clock
    from cherrypick.core import regimecuts as _rc

    from cherrypick.meic import analytics, cli, paths

    args = build_parser().parse_args(argv)
    era = args.era or analytics.CURRENT_ERA
    out_dir = paths.data_dir()
    with cli._connect(args.db) as conn:
        if args.backfill:
            if not args.write:
                print(json.dumps({"ok": False, "error": "--backfill needs --write"}))
                return 2
            sessions = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT trade_date FROM ic_trades "
                    "WHERE status IN ('stopped', 'expired', 'force_closed') AND trade_date >= ? ORDER BY trade_date",
                    (args.since or "0000-00-00",),
                )
            ]
            written = []
            for day in sessions:
                doc = analytics.regime_cuts(conn, session=day, symbol=args.symbol, era=era)
                written.append(_rc.write_artifact(out_dir, doc))
            print(json.dumps({"ok": True, "sessions": sessions, "written": written}, indent=2))
            return 0
        session = args.session or _clock.today_iso()
        doc = analytics.regime_cuts(conn, session=session, symbol=args.symbol, era=era)
    written = _rc.write_artifact(out_dir, doc) if args.write else None
    print(json.dumps({"ok": True, "written": written, **doc}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
