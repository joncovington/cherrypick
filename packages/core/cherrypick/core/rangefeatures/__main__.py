"""`python -m cherrypick.core.rangefeatures study` — run the declared range-features study once.

Reads SPX daily bars from the shared stream cache (`streamcache.daily_bars`, both close routes) and
prints the result as JSON. `--bars-through` pins the last bar evaluation may read, so a past run
reproduces exactly however many bars have arrived since; it can never be later than the declared
evaluation end.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.core import db as _db
from cherrypick.core import home as _home
from cherrypick.core import rangefeatures as _rf
from cherrypick.core import streamcache as _sc
from cherrypick.core.clock import ET as _ET


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.core.rangefeatures", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("study")
    p.add_argument("--cache", default=None)
    p.add_argument("--bars-through", default=_rf.EVALUATION_THROUGH)
    args = parser.parse_args(argv)

    cache = Path(args.cache) if args.cache else _home.data_dir("marketdata") / "stream_cache.db"
    try:
        conn = _db.connect_ro(cache)
    except Exception as exc:  # noqa: BLE001 -- an unreadable store is a reported state
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    try:
        today = datetime.now(_ET).date().isoformat()
        bars = _sc.daily_bars(conn, "SPX", through=today)
    finally:
        conn.close()
    if not bars:
        print(json.dumps({"ok": False, "error": "no SPX daily bars in the cache"}))
        return 2
    print(json.dumps({"ok": True, **_rf.study(bars, bars_through=args.bars_through)}, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
