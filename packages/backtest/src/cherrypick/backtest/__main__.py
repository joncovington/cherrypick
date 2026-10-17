"""cherrypick-backtest command line.

python -m cherrypick.backtest status              # config, key presence, store, licence clock
python -m cherrypick.backtest key status          # whether the vendor key is in the keyring
python -m cherrypick.backtest key set             # store it (hidden prompt; blank keeps the current one)
python -m cherrypick.backtest key import-env      # copy the vendor's env var into the keyring once
python -m cherrypick.backtest purge [--derived] [--apply]   # expunge the vendor's data (dry run by default)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import credential, licensing, paths, purge, settings


def _emit(obj: dict) -> None:
    print(json.dumps(obj, indent=1, default=str))


def _vendor_sessions(s: dict) -> int:
    """How many vendor month files exist. A count of files, not of validated sessions: the store's
    own coverage table arrives with the fetch."""
    d = paths.vendor_dir(s)
    return len(list(d.glob("*.db"))) if d.exists() else 0


def cmd_status(args) -> int:
    s = settings.load(args.config)
    vendor = s["vendor"]
    _emit(
        {
            "enabled": s["enabled"],
            "config_file": settings.config_file_present(),
            "vendor": vendor["name"],
            "subscription": vendor["subscription"],
            "transport": vendor["transport"],
            "key_present": credential.key_present(s),
            "key_hint": credential.PORTAL_HINT[vendor["name"]],
            "store": str(paths.vendor_dir(s)),
            "store_files": _vendor_sessions(s),
            "results": str(paths.results_db()),
            "windows": s["windows"],
            "license": licensing.status(s),
        }
    )
    return 0


def cmd_key(args) -> int:
    s = settings.load(args.config)
    if args.action == "status":
        present = credential.key_present(s)
        _emit(
            {
                "present": present,
                "keyring_service": s["vendor"]["credential"]["keyring_service"],
                "where_to_get_it": credential.PORTAL_HINT[s["vendor"]["name"]],
                "set_with": "python -m cherrypick.backtest key set",
            }
        )
        return 0 if present else 1
    if args.action == "set":
        written = credential.set_key(s)
        _emit({"written": written})
        return 0
    res = credential.import_env(s)
    _emit(res)
    return 0 if res["ok"] else 1


def cmd_purge(args) -> int:
    s = settings.load(args.config)
    if args.apply:
        report = purge.apply(s, derived=args.derived)
    else:
        report = purge.plan(s, derived=args.derived)
        report["dry_run"] = True
    if len(report["listing"]) > 20:
        report["listing"] = report["listing"][:20] + [f"... {len(report['listing']) - 20} more"]
    _emit(report)
    return 0 if not report.get("remaining") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m cherrypick.backtest")
    p.add_argument("--config", default=None, help="a config file to use instead of the usual search")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    k = sub.add_parser("key")
    k.add_argument("action", choices=("status", "set", "import-env"))
    k.set_defaults(fn=cmd_key)
    pg = sub.add_parser("purge")
    pg.add_argument("--derived", action="store_true", help="also remove results derived from the data")
    pg.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    pg.set_defaults(fn=cmd_purge)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except settings.SettingsError as exc:
        print(f"backtest config: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
