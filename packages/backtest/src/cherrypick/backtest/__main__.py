"""cherrypick-backtest command line.

python -m cherrypick.backtest status              # config, key presence, store, licence clock
python -m cherrypick.backtest key status          # whether the vendor key is in the keyring
python -m cherrypick.backtest key set             # store it (hidden prompt; blank keeps the current one)
python -m cherrypick.backtest key import-env      # copy the vendor's env var into the keyring once
python -m cherrypick.backtest fetch --from 2026-10-13 --to 2026-10-16 [--dataset SPX] [--refetch]
python -m cherrypick.backtest coverage [--dataset SPX]
python -m cherrypick.backtest derive-regimes [--dataset SPX]   # regime rows from stored profiles
python -m cherrypick.backtest validate [--dates a,b,...]   # against the recorder; results stay local
python -m cherrypick.backtest run flies --arms range-high --window validation|design|holdout [--cadence 60]
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


def _fetch_refusal(s: dict) -> str | None:
    """Why a fetch may not run now, or None. Off is off; a lapsed licence forbids new data."""
    if not s["enabled"]:
        return "the backtest config is disabled (set enabled: true in ~/.cherrypick/config/backtest.json)"
    lic = licensing.status(s)
    if not lic["active"]:
        return f"the vendor licence ended on {lic['terminated_on']}; purge by {lic['purge_by']}"
    return None


def recent_span(n: int, today=None) -> tuple[str, str]:
    """The last `n` trading days through today (ET): the nightly job's span. The fetch skips any
    session already complete, so a span that overlaps last night's costs nothing."""
    from cherrypick.core import calendar as _cal
    from cherrypick.core import clock as _clock

    end = today or _clock.now_et().date()
    start = end if _cal.is_trading_day(end) else _cal.previous_trading_day(end)
    for _ in range(max(0, int(n) - 1)):
        start = _cal.previous_trading_day(start)
    return start.isoformat(), end.isoformat()


def cmd_fetch(args) -> int:
    from . import fetch
    from .vendor import VendorRefusal, client_for

    s = settings.load(args.config)
    refusal = _fetch_refusal(s)
    if refusal:
        print(f"fetch refused: {refusal}", file=sys.stderr)
        return 2
    if args.recent:
        args.start, args.end = recent_span(args.recent)
    if not (args.start and args.end):
        print("fetch: give --from and --to, or --recent N", file=sys.stderr)
        return 2
    try:
        client = client_for(s)
    except VendorRefusal as exc:
        print(f"fetch refused: {exc}", file=sys.stderr)
        return 2
    report = fetch.fetch(
        s,
        client,
        args.dataset,
        args.start,
        args.end,
        refetch=args.refetch,
        workers=args.workers,
        cadence=args.cadence,
        log=lambda m: print(m, file=sys.stderr),
    )
    _emit(report)
    return 1 if report.get("aborted") else 0


def cmd_coverage(args) -> int:
    from . import store

    s = settings.load(args.config)
    path = store.db_path(s, args.dataset, args.cadence)
    if not path.exists():
        _emit({"symbol": args.dataset.upper(), "complete": 0, "span": None, "refused": [], "store": None})
        return 0
    conn = store.connect(s, args.dataset, args.cadence)
    try:
        _emit({**store.coverage(conn, args.dataset), "store": str(path)})
    finally:
        conn.close()
    return 0


def cmd_derive_regimes(args) -> int:
    """Backfill `gex_regime_history` for every complete session from its stored profile (no vendor call)."""
    from . import regimes, store

    s = settings.load(args.config)
    if not store.db_path(s, args.dataset, args.cadence).exists():
        _emit({"sessions": 0, "rows": 0})
        return 0
    conn = store.connect(s, args.dataset, args.cadence)
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        days = [
            r["trade_date"]
            for r in conn.execute(
                "SELECT trade_date FROM vendor_sessions WHERE symbol = ? AND status = ? ORDER BY trade_date",
                (args.dataset.upper(), store.COMPLETE),
            )
        ]
        rows = 0
        for day in days:
            rows += regimes.derive(conn, args.dataset, day)
            conn.commit()
        _emit({"sessions": len(days), "rows": rows})
    finally:
        conn.close()
    return 0


def cmd_validate(args) -> int:
    from . import store, validate

    s = settings.load(args.config)
    recorder = args.recorder_db
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        from cherrypick.core import quotehistory

        dates = validate.recorded_days(
            recorder or quotehistory.default_history_db(),
            store.db_path(s, args.dataset, args.cadence),
            args.dataset,
        )
    if not dates:
        print("validate: no session is in both the recorder's history and the vendor store", file=sys.stderr)
        return 1
    report = validate.run(s, args.dataset, dates, recorder_db=recorder, cadence=args.cadence)
    _emit(report)
    return 0


def cmd_run(args) -> int:
    from . import runner

    s = settings.load(args.config)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    try:
        report = runner.run(
            s,
            arms,
            args.window,
            symbol=args.dataset,
            cadence=args.cadence,
            start=args.start,
            end=args.end,
            spend_holdout=args.spend_holdout,
            provisional=args.provisional,
            workers=args.workers,
            log=lambda m: print(m, file=sys.stderr),
        )
    except runner.RunRefused as exc:
        print(f"run refused: {exc}", file=sys.stderr)
        return 2
    _emit(report)
    return 0


def cmd_run_advised(args) -> int:
    """The advisor's queued backtest specs over the design window (advised.py)."""
    from . import advised, runner

    try:
        out = advised.run(
            settings.load(args.config),
            cadence=args.cadence,
            workers=args.workers,
            log=lambda m: print(m, file=sys.stderr),
        )
    except runner.RunRefused as exc:
        print(f"run refused: {exc}", file=sys.stderr)
        return 2
    _emit(out)
    return 0


def cmd_read(args) -> int:
    """The console's read surface (readout.py): JSON on stdout, read-only."""
    from . import readout

    if args.what == "overview":
        _emit(readout.overview(settings.load(args.config)))
        return 0
    out = readout.run_report(args.id or "")
    _emit(out)
    return 0 if out["ok"] else 1


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
    f = sub.add_parser("fetch", help="fetch vendor sessions into the store (the only network verb)")
    f.add_argument("--dataset", default="SPX")
    f.add_argument("--from", dest="start", default=None)
    f.add_argument("--to", dest="end", default=None)
    f.add_argument(
        "--recent", type=int, default=None, help="the last N trading days through today (the nightly job)"
    )
    f.add_argument("--refetch", action="store_true", help="re-fetch sessions already complete")
    f.add_argument(
        "--workers", type=int, default=1, help="sessions pulled at a time (default 1; see fetch.fetch)"
    )
    f.set_defaults(fn=cmd_fetch)
    cv = sub.add_parser("coverage", help="sessions in the store, complete and refused")
    cv.add_argument("--dataset", default="SPX")
    cv.set_defaults(fn=cmd_coverage)
    dr = sub.add_parser("derive-regimes", help="backfill regime rows from stored profiles (no vendor call)")
    dr.add_argument("--dataset", default="SPX")
    dr.set_defaults(fn=cmd_derive_regimes)
    va = sub.add_parser("validate", help="vendor sessions against the recorder's on the days both exist")
    va.add_argument("--dataset", default="SPX")
    rn = sub.add_parser("run", help="replay flies' arms over the store's sessions (results stay local)")
    rn.add_argument("module", choices=("flies",))
    rn.add_argument("--arms", default="control", help="comma-separated; control always runs beside them")
    rn.add_argument("--window", required=True, choices=("validation", "design", "holdout"))
    rn.add_argument("--dataset", default="SPX")
    rn.add_argument("--from", dest="start", default=None, help="a sub-span of the window")
    rn.add_argument("--to", dest="end", default=None)
    rn.add_argument("--spend-holdout", dest="spend_holdout", action="store_true")
    rn.add_argument(
        "--provisional",
        action="store_true",
        help="design window while history is still locked: no trial, marked provisional everywhere",
    )
    rn.add_argument("--workers", type=int, default=4)
    rn.set_defaults(fn=cmd_run)
    ra = sub.add_parser("run-advised", help="the advisor's queued backtest specs, over the design window")
    ra.add_argument("--workers", type=int, default=4)
    ra.set_defaults(fn=cmd_run_advised)
    rd = sub.add_parser("read", help="the console's read surface: JSON, read-only")
    rd.add_argument("what", choices=("overview", "run"))
    rd.add_argument("--id", default=None, help="with `run`: the run id")
    rd.set_defaults(fn=cmd_read)
    for sp in (f, cv, dr, va, rn, ra):
        sp.add_argument(
            "--cadence",
            type=int,
            default=None,
            help="seconds between samples (default store.cadence_seconds); each cadence is its own file",
        )
    va.add_argument("--dates", default=None, help="comma-separated; default: every session in both")
    va.add_argument(
        "--recorder-db", dest="recorder_db", default=None, help="default: the gex recorder's history"
    )
    va.set_defaults(fn=cmd_validate)
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
