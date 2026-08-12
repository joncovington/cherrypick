"""cherrypick-gex CLI.

  gex     one-shot GEX payload for a symbol. `--json` prints the raw payload; otherwise a summary.
  stream  run the streamer to populate this module's own cache.
  record  the always-on spot-trail recorder.

The read surface for GEX is the console (packages/console) — this module computes and records; it
does not serve.
"""

from __future__ import annotations

import argparse
import json

from cherrypick.gex import config as _config
from cherrypick.gex import service as _service
from cherrypick.gex import stream_request as _stream_request
from cherrypick.gex import streamer as _streamer


def _cmd_gex(cfg: dict, args: argparse.Namespace) -> int:
    payload = _service.build_gex(cfg, args.symbol)
    if args.json:
        print(json.dumps(payload))
        return 0 if payload.get("ok") else 1
    if not payload.get("ok"):
        print(f"GEX unavailable for {payload.get('symbol')}: {payload.get('error')}")
        return 1
    t = payload["totals"]
    print(f"{payload['symbol']}  exp {payload['expiration']}  spot {payload['underlying_price']}")
    print(
        f"  net GEX {t['net_gex']:>14,}   flip {t['zero_gamma']}   "
        f"call wall {t['call_wall']}   put wall {t['put_wall']}   ({len(payload['series'])} strikes)"
    )
    return 0


def _cmd_stream(cfg: dict, args: argparse.Namespace) -> int:
    syms = args.symbol if args.symbol else None
    _streamer.run(cfg, symbols=syms)
    return 0


def _cmd_record(cfg: dict, args: argparse.Namespace) -> int:
    if args.status:
        print(json.dumps(_service.recorder_status(cfg)))
        return 0
    if args.stop:
        print(json.dumps(_service.stop_recorder(cfg)))
        return 0
    return _service.run_recorder(cfg, interval=args.interval, once=args.once)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick-gex")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gex", help="one-shot GEX profile for a symbol")
    g.add_argument("--symbol", default=None, help="underlying symbol (default: config.symbols[0])")
    g.add_argument("--json", action="store_true", help="emit the raw payload as JSON")

    st = sub.add_parser("stream", help="run the streamer to populate this module's own cache")
    st.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="underlying to stream (repeatable; default: config.symbols)",
    )

    rec = sub.add_parser("record", help="always-on spot-trail recorder (run alongside the streamer)")
    rec.add_argument("--once", action="store_true", help="sample one tick and exit")
    rec.add_argument(
        "--interval", type=int, default=None, help="seconds between samples (default: serve.refresh_seconds)"
    )
    rec.add_argument("--status", action="store_true", help="print {running,pid} JSON and exit")
    rec.add_argument("--stop", action="store_true", help="stop a running recorder daemon")

    args = parser.parse_args(argv)
    cfg = _config.load()
    # Tell the streamer which underlyings we need kept fresh in the shared cache (best-effort).
    _stream_request.register(cfg)
    if args.command == "gex":
        return _cmd_gex(cfg, args)
    if args.command == "stream":
        return _cmd_stream(cfg, args)
    if args.command == "record":
        return _cmd_record(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
