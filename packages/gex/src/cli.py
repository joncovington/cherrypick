"""cherrypick-gex CLI.

Two commands:
  gex        one-shot GEX payload for a symbol. `--json` prints the raw payload (this is what the
             umbrella's `dashboard --serve` subprocesses to embed a GEX card); otherwise a summary.
  dashboard  --serve runs the module's own localhost live GEX view.
"""

from __future__ import annotations

import argparse
import json
import sys

import config as _config
import section as _section
import serve as _serve
import service as _service
import streamer as _streamer


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
    print(f"  net GEX {t['net_gex']:>14,}   flip {t['zero_gamma']}   "
          f"call wall {t['call_wall']}   put wall {t['put_wall']}   ({len(payload['series'])} strikes)")
    return 0


def _cmd_section(cfg: dict, args: argparse.Namespace) -> int:
    payload = _section.build_section(cfg, args.symbol)
    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


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


def _cmd_dashboard(cfg: dict, args: argparse.Namespace) -> int:
    if not args.serve:
        print("cherrypick-gex dashboard is serve-only; pass --serve.", file=sys.stderr)
        return 2
    _serve.serve(cfg, symbol=args.symbol, host=args.host, port=args.port,
                 open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick-gex")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gex", help="one-shot GEX profile for a symbol")
    g.add_argument("--symbol", default=None, help="underlying symbol (default: config.symbols[0])")
    g.add_argument("--json", action="store_true", help="emit the raw payload as JSON")

    se = sub.add_parser("section", help="emit a cherrypick.core.viz section payload (for the umbrella)")
    se.add_argument("--symbol", default=None, help="underlying symbol (default: config.symbols[0])")
    se.add_argument("--json", action="store_true", help="(payload is always JSON; accepted for symmetry)")

    st = sub.add_parser("stream", help="run the streamer to populate this module's own cache")
    st.add_argument("--symbol", action="append", default=None,
                    help="underlying to stream (repeatable; default: config.symbols)")

    rec = sub.add_parser("record", help="always-on spot-trail recorder (run alongside the streamer)")
    rec.add_argument("--once", action="store_true", help="sample one tick and exit")
    rec.add_argument("--interval", type=int, default=None,
                     help="seconds between samples (default: serve.refresh_seconds)")
    rec.add_argument("--status", action="store_true", help="print {running,pid} JSON and exit")
    rec.add_argument("--stop", action="store_true", help="stop a running recorder daemon")

    d = sub.add_parser("dashboard", help="live GEX dashboard")
    d.add_argument("--serve", action="store_true", help="run the localhost live view")
    d.add_argument("--symbol", default=None)
    d.add_argument("--host", default=None)
    d.add_argument("--port", type=int, default=None)
    d.add_argument("--no-browser", action="store_true", help="do not auto-open a browser")

    args = parser.parse_args(argv)
    cfg = _config.load()
    if args.command == "gex":
        return _cmd_gex(cfg, args)
    if args.command == "section":
        return _cmd_section(cfg, args)
    if args.command == "stream":
        return _cmd_stream(cfg, args)
    if args.command == "record":
        return _cmd_record(cfg, args)
    if args.command == "dashboard":
        return _cmd_dashboard(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
