"""cherrypick-scout CLI: ``serve`` / ``watchlist`` / ``cache``."""

from __future__ import annotations

import argparse
import json

from . import config as _config
from . import serve as _serve
from .services import cache as _cache
from .services import watchlist as _watchlist


def _cmd_serve(cfg: dict, args: argparse.Namespace) -> int:
    result = _serve.serve(cfg, host=args.host, port=args.port)
    if not result.get("ok"):
        print(result.get("error"))
        return 1
    return 0


def _cmd_watchlist(cfg: dict, args: argparse.Namespace) -> int:
    path = _config.watchlist_path()
    if args.wl_command == "list":
        symbols = _watchlist.load(path)
    elif args.wl_command == "add":
        symbols = _watchlist.add(path, args.symbols)
    elif args.wl_command == "remove":
        symbols = _watchlist.remove(path, args.symbols)
    else:
        return 2
    print(json.dumps({"ok": True, "symbols": symbols}))
    return 0


def _cmd_cache(cfg: dict, args: argparse.Namespace) -> int:
    path = _config.cache_db_path()
    if args.cache_command == "clear":
        if path.exists():
            path.unlink()
            for suffix in ("-wal", "-shm"):
                extra = path.with_name(path.name + suffix)
                if extra.exists():
                    extra.unlink()
        print(json.dumps({"ok": True, "cleared": str(path)}))
        return 0
    if args.cache_command == "stats":
        conn = _cache.open_db(path)
        try:
            counts = {}
            for table in ("kv_cache", "candles", "candle_meta", "symbol_meta", "staged_orders"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
        print(json.dumps({"ok": True, "path": str(path), "counts": counts}))
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick-scout")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the scout web app")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)

    w = sub.add_parser("watchlist", help="view/edit the watchlist")
    wsub = w.add_subparsers(dest="wl_command", required=True)
    wsub.add_parser("list")
    w_add = wsub.add_parser("add")
    w_add.add_argument("symbols", nargs="+")
    w_remove = wsub.add_parser("remove")
    w_remove.add_argument("symbols", nargs="+")

    c = sub.add_parser("cache", help="inspect/clear the local cache")
    csub = c.add_subparsers(dest="cache_command", required=True)
    csub.add_parser("stats")
    csub.add_parser("clear")

    args = parser.parse_args(argv)
    cfg = _config.load()
    if args.command == "serve":
        return _cmd_serve(cfg, args)
    if args.command == "watchlist":
        return _cmd_watchlist(cfg, args)
    if args.command == "cache":
        return _cmd_cache(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
