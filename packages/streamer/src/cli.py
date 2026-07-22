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

import config as _config
import credentials as _credentials
import daemon as _daemon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cherrypick-streamer",
        description="Standalone DXLink option-chain streamer daemon (canonical shared cache producer).",
    )
    parser.add_argument("--status", action="store_true", help="print status JSON and exit")
    parser.add_argument("--stop", action="store_true", help="stop a running daemon")
    parser.add_argument("--symbol", action="append", default=None,
                        help="underlying to stream (repeatable; default: config 'symbols')")
    parser.add_argument("--secrets-set", action="store_true",
                        help="store the shared tastytrade OAuth bearer secrets (hidden input) and exit")
    parser.add_argument("--secrets-status", action="store_true",
                        help="print which shared OAuth secrets are present and exit")
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
        print(json.dumps({
            "ok": False,
            "error": f"Streamer already running (pid {existing}). Run 'python run.py --stop' first, "
                     f"or --status to inspect it.",
        }))
        return 1

    syms = _config.symbols(cfg, cli_override=args.symbol)
    return _daemon.run_daemon(cfg, symbols=syms)


if __name__ == "__main__":
    raise SystemExit(main())
