import argparse
import json
import sys
from pathlib import Path

from cherrypick.core import db as _db
from cherrypick.core import home as _home
from cherrypick.core import openingrange as _or


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.core.openingrange", description=_or.__doc__)
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--session", default=None, help="one session; omit for the whole trail")
    parser.add_argument("--in-sample-through", default=None, help="the declared holdout boundary")
    parser.add_argument("--feature", default="or_atr")
    parser.add_argument("--study", action="store_true", help="run the declared split instead of the rows")
    parser.add_argument("--gex", default=None)
    parser.add_argument("--cache", default=None)
    args = parser.parse_args(argv)

    gex = Path(args.gex) if args.gex else _home.data_dir("gex") / "gex_history.db"
    cache = Path(args.cache) if args.cache else _home.data_dir("marketdata") / "stream_cache.db"
    try:
        spot_conn, cache_conn = _db.connect_ro(gex), _db.connect_ro(cache)
    except Exception as exc:  # noqa: BLE001 -- an unreadable store is a reported state
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2

    if args.session:
        one = _or.build(spot_conn, cache_conn, session=args.session, symbol=args.symbol)
        print(json.dumps({"ok": True, **one}, indent=2, default=str))
        return 0

    sessions = [
        r[0]
        for r in spot_conn.execute(
            "SELECT DISTINCT trade_date FROM gex_spot_history WHERE symbol = ? ORDER BY trade_date",
            (args.symbol,),
        )
    ]
    rows = _or.series(spot_conn, cache_conn, sessions=sessions, symbol=args.symbol)
    if args.study:
        out = _or.study(rows, feature=args.feature, in_sample_through=args.in_sample_through)
    else:
        out = {"sessions": len(rows), "rows": rows}
    print(json.dumps({"ok": True, **out}, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
