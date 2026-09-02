"""cherrypick-streamer CLI — run the daemon, or ``--status`` / ``--stop`` it.

Flat args (no subcommand) so the orchestrator drives it exactly like MEIC's streamer:

  python run.py                              # run in the foreground (orchestrator launches this detached)
  python run.py --status                     # print one JSON status object and exit
  python run.py --stop                       # SIGTERM a running daemon
  python run.py --symbol SPX --symbol XSP    # override the configured symbols
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from cherrypick.streamer import config as _config
from cherrypick.streamer import credentials as _credentials
from cherrypick.streamer import daemon as _daemon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cherrypick-streamer",
        description="Standalone DXLink option-chain streamer daemon (canonical shared cache producer).",
    )
    parser.add_argument("--status", action="store_true", help="print status JSON and exit")
    parser.add_argument("--stop", action="store_true", help="stop a running daemon")
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="underlying to stream (repeatable; default: config 'symbols')",
    )
    parser.add_argument(
        "--secrets-set",
        action="store_true",
        help="store the shared tastytrade OAuth bearer secrets (hidden input) and exit",
    )
    parser.add_argument(
        "--secrets-status", action="store_true", help="print which shared OAuth secrets are present and exit"
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="report the dead chain rows in the shared cache (passed expirations, retired "
        "underlyings) and exit. DRY unless --apply.",
    )
    parser.add_argument("--apply", action="store_true", help="with --prune: actually delete what it reports")
    args = parser.parse_args(argv)

    cfg = _config.load()

    # Credential entry writes only the OS keyring (no broker, no daemon); emit JSON and exit.
    if args.secrets_status:
        print(json.dumps(_credentials.status()))
        return 0
    if args.secrets_set:
        written = _credentials.set_secrets()
        print(json.dumps({"ok": True, "set": written}))
        return 0

    # Maintenance, run against the cache this process owns -- there is exactly one writer by
    # invariant, so a standalone prune script would be a second. Dry by default: it deletes rows
    # every module reads, and the suite's convention for that (settle-expired, migrate-home) is
    # that a human sees the plan before it runs.
    if args.prune:
        from cherrypick.core import streamcache as _sc
        from cherrypick.core import streamrequests as _sr
        from cherrypick.core.clock import ET as _ET

        conn = _sc.connect(_config.cache_path(cfg))
        try:
            report = _sc.prune_cache(
                conn,
                declared_underlyings=_sr.union_symbols(cfg.get("symbols")),
                today=datetime.now(tz=_ET).date().isoformat(),
                apply=args.apply,
            )
        finally:
            conn.close()
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1

    # --status / --stop emit pure JSON on stdout (no logging setup) so the watchdog can parse it cleanly.
    if args.status:
        print(json.dumps(_daemon.status(cfg), default=str))
        return 0
    if args.stop:
        result = _daemon.stop(cfg)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    existing = _daemon.running_pid(cfg)
    if existing is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Streamer already running (pid {existing}). Run 'python run.py --stop' first, "
                    f"or --status to inspect it.",
                }
            )
        )
        return 1

    syms = _config.symbols(cfg, cli_override=args.symbol)
    return _daemon.run_daemon(cfg, symbols=syms)


if __name__ == "__main__":
    raise SystemExit(main())
