"""cherrypick-technicals command line.

python -m cherrypick.technicals land [--symbols A B ...]
python -m cherrypick.technicals status
python -m cherrypick.technicals bars SYMBOL [--raw] [--last N]
python -m cherrypick.technicals check-vendor [--all]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.core import home as _home

from . import land as _land
from . import store, vendor_check

PKG_ROOT = Path(__file__).resolve().parents[3]


def _config() -> dict:
    return _home.load_module_config("technicals", PKG_ROOT)


def cmd_land(args) -> int:
    report = _land.land(_config().get("dolt") or {}, wanted=args.symbols or None)
    missing = report.get("missing") or []
    if len(missing) > 20:
        report["missing"] = missing[:20] + [f"... {len(missing) - 20} more"]
    print(json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


def cmd_status(_args) -> int:
    conn = store.connect()
    row = conn.execute(
        "SELECT COUNT(DISTINCT symbol) s, COUNT(*) n, MIN(date) lo, MAX(date) hi FROM bars"
    ).fetchone()
    last = conn.execute(
        "SELECT landed_at, through, symbols, bars FROM landings ORDER BY landed_at DESC LIMIT 1"
    ).fetchone()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("splits", "dividends", "iv")
    }
    print(
        json.dumps(
            {
                "symbols": row["s"],
                "bars": row["n"],
                "span": [row["lo"], row["hi"]],
                **counts,
                "last_landing": dict(last) if last else None,
            },
            indent=1,
        )
    )
    return 0


def cmd_bars(args) -> int:
    conn = store.connect()
    sym = args.symbol.upper()
    rows = store.raw_bars(conn, sym) if args.raw else store.adjusted_bars(conn, sym)
    for b in rows[-args.last :]:
        print(
            f"{b.date}  {b.open:10.2f} {b.high:10.2f} {b.low:10.2f} {b.close:10.2f} {b.volume:14.0f}"
            + (f"  x{b.price_factor:.6f}" if not args.raw else "")
        )
    print(json.dumps({"iv_rank": store.iv_rank(conn, sym)}))
    return 0


def cmd_check_vendor(args) -> int:
    result = vendor_check.check()
    if not args.all:
        result["by_symbol"] = {s: r for s, r in result["by_symbol"].items() if r["agree"] < r["prices"]}
    print(json.dumps(result, indent=1))
    return 0 if result["prices"] and result["agree"] == result["prices"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.technicals", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    la = sub.add_parser("land", help="land bars, splits, dividends and IV from the local Dolt clones")
    la.add_argument(
        "--symbols", nargs="*", help="only these symbols (default: candidates + ETFs + benchmarks)"
    )
    la.set_defaults(fn=cmd_land)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    ba = sub.add_parser("bars")
    ba.add_argument("symbol")
    ba.add_argument("--raw", action="store_true")
    ba.add_argument("--last", type=int, default=10)
    ba.set_defaults(fn=cmd_bars)
    cv = sub.add_parser("check-vendor", help="our adjusted bars against every vendor chart capture")
    cv.add_argument("--all", action="store_true", help="list every symbol, not only disagreements")
    cv.set_defaults(fn=cmd_check_vendor)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
