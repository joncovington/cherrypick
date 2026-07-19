"""Paper session driver — fetch a snapshot, run every arm, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything it
decides is decided by `engine.py`; this layer just supplies snapshots and persists what came back.
That split is what makes the strategy testable, and it is also the suite guardrail: no network, no MCP,
and no model call anywhere on a decision path.

Typical use is one scheduled `--once` per interval during RTH plus a `--settle` after the close, which
is how the orchestrator drives it (by subprocess, never by import). `--interval` runs the same loop
in-process for a manual session.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_CORE = os.path.join(_HERE, "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from cherrypick.core import calendar as _cal  # noqa: E402

import book as bookmod  # noqa: E402
import cli as climod  # noqa: E402
import db as dbmod  # noqa: E402
import eod as eodmod  # noqa: E402
import provider  # noqa: E402

# Regular trading hours, ET, as minutes of day. The engine's own entry windows sit inside this; the
# session gate exists so an out-of-hours run is a clean no-op rather than an iteration against a
# frozen cache full of yesterday's quotes.
RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60

# Settlement runs from the SAME recurring task rather than a second daily one. Two tasks can drift
# apart — one fires, the other is disabled or missed, and the books sit unsettled with nobody to
# notice. MEIC uses this same self-trigger for its EOD report.
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_TASK_NAME = "cherrypick-flies-paper-loop"
# Every 2 minutes, matching MEIC. This cadence is load-bearing for THIS strategy in a way it is not
# for MEIC: the completing spread of a legged fly can cheapen transiently, so a slower poll measures
# a lower completion rate — the module's headline number — for reasons that have nothing to do with
# the market. Note that any discrete poll underestimates what a resting limit order would catch live,
# so the completion rate measured here is a floor on that count and a ceiling on live fill quality.
_TASK_INTERVAL_MIN = 2
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def stream_cache_path(config: dict) -> str:
    """Where MEIC's streamer keeps its cache. Config first, then the managed home, and `~` expands —
    portable paths only, no machine-specific absolutes anywhere in the suite."""
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "meic", "stream_cache.db")


_LOG_FILE = eodmod.logs_dir() / "flies_paper.log"
_logger = logging.getLogger("flies_paper_loop")


def _setup_logging() -> None:
    """Log to a rotating file as well as stdout.

    The scheduled task runs under pythonw.exe with no console, so anything printed to stdout is
    discarded. Without a file the first live session would leave no trace of why it did or didn't
    trade — and the orchestrator's freshness check watches this exact file to tell "the loop is
    running quietly" from "the loop is dead", so its absence would also read as an outage.
    """
    if _logger.handlers:
        return
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    handler = RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(fmt)
    _logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    _logger.addHandler(stream)
    _logger.setLevel(logging.INFO)


def _log(message: str) -> None:
    _setup_logging()
    _logger.info(message)


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def settle_time_min(config: dict) -> int:
    at = config.get("defaults", {}).get("settle_time")
    if not at:
        return DEFAULT_SETTLE_MIN
    hours, minutes = at.split(":")
    return int(hours) * 60 + int(minutes)


def session_already_settled(day: str, directory=None) -> bool:
    """Has this session's EOD already been written? The report file is the marker, so a task firing
    every two minutes after the close settles once and then no-ops."""
    directory = directory or eodmod.logs_dir()
    return (directory / f"paper-eod-{day}.md").exists()


def run_once(config: dict, conn, *, cache_path: str, when=None, force: bool = False) -> dict:
    """One iteration across every configured symbol and every enabled arm.

    Also owns end-of-day settlement. The recurring task calls only this, so there is exactly one
    thing to schedule and one thing that can fail.
    """
    when = when or provider.now_et()
    now_min = provider.minute_of_day(when)
    day = when.date().isoformat()

    # Nothing happens on a non-trading day — not even settlement.
    #
    # The task fires every two minutes forever, so without this the Saturday-evening tick would find
    # the clock past the settle time and no report written for that date, "settle" a session that
    # never happened, and emit paper-eod-<saturday>.md. The suite digest discovers those files by
    # filename alone, so it would ingest weekends and holidays as real sessions.
    if not force and not _cal.is_trading_day(when.date()):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}

    # Settlement next, and deliberately BEFORE the RTH gate — the settle time is after the close, so
    # an RTH-gated check would never reach it.
    if now_min >= settle_time_min(config) and not session_already_settled(day):
        _log(f"past settle time ({now_min // 60:02d}:{now_min % 60:02d}) — settling {day}")
        return {"ok": True, "settled_session": True,
                **run_settle(config, conn, cache_path=cache_path, when=when)}

    if not force and not in_session(now_min):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}

    arms = climod.enabled_arms(config)
    results = []
    for symbol in config.get("symbols", ["SPX"]):
        snapshot = provider.build_snapshot(
            cache_path, symbol, when=when,
            max_quote_age_seconds=config.get("defaults", {}).get(
                "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS),
        )
        if not snapshot.get("ok"):
            # Not an error. A streamer still warming up, or a symbol with no fresh quotes, is an
            # ordinary condition — log it so a barren session is explicable afterwards.
            _log(f"{symbol}: no snapshot ({snapshot['reason']})")
            results.append({"symbol": symbol, "ok": False, "reason": snapshot["reason"]})
            continue

        stats = snapshot["quote_stats"]
        _log(f"{symbol}: spot {snapshot['underlying_price']:.2f} dte {snapshot['dte']} "
             f"quotes {stats['fresh']} fresh / {stats['rejected']} rejected")
        for arm in arms:
            outcome = bookmod.process_snapshot(snapshot, config, conn, arm)
            for action in outcome["actions"]:
                if action["action"] not in ("entry_skipped", "completion_skipped"):
                    _log(f"  [{arm}] {action}")
            results.append({"symbol": symbol, "arm": arm, "ok": True, **outcome})
    return {"ok": True, "iterations": len(results), "results": results}


def run_settle(config: dict, conn, *, cache_path: str, when=None,
               price: float | None = None) -> dict:
    """Settle every book for the session at the settlement price.

    Caveat worth knowing when reading the results: `price` defaults to the last streamed trade, which
    approximates but is not identical to the official settlement print. For 0DTE SPX that difference is
    usually small, and it is systematic rather than random — but a position centred within a point of
    spot can settle on the wrong side of its centre because of it. Pass `--price` with the official
    print for a book that matters.
    """
    when = when or provider.now_et()
    trade_date = when.date().isoformat()
    out = []
    for symbol in config.get("symbols", ["SPX"]):
        settlement = price if price is not None else provider.read_spot(cache_path, symbol)
        if settlement is None:
            _log(f"{symbol}: cannot settle — no price available")
            out.append({"symbol": symbol, "ok": False, "reason": "no_settlement_price"})
            continue
        source = "explicit" if price is not None else "last_trade"
        for arm in climod.enabled_arms(config):
            result = bookmod.settle_book(conn, trade_date, arm, symbol, settlement, config)
            _log(f"{symbol} [{arm}] settled at {settlement:.2f} ({source}): "
                 f"P&L {result['pnl']:+.2f}, stats {result['stats']}")
            out.append({"symbol": symbol, "arm": arm, "ok": True,
                        "settlement_source": source, **result})

    # Written here rather than on a separate schedule so the reports can never describe a session that
    # hasn't settled yet. The orchestrator's digest and insight pick them up by filename alone.
    reports = eodmod.write_reports(conn, trade_date)
    _log(f"wrote {reports['paper_eod']} and {reports['eod_analysis']}")
    return {"ok": True, "settled": len(out), "results": out, "reports": reports}


# --------------------------------------------------------------------------- scheduled task
def _pythonw() -> str:
    """pythonw.exe where available, so the every-2-minute run is genuinely headless — a console
    window flashing up 200 times a session would make the machine unusable."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def task_installed() -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(["schtasks", "/Query", "/TN", _TASK_NAME],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    return r.returncode == 0


def install_task() -> dict:
    """Register the recurring loop.

    One task, running `--once` every couple of minutes. `--once` is internally gated — out of hours
    it is a clean no-op, after the close it settles once — so the schedule carries no session logic
    of its own and cannot disagree with the engine about when the day starts or ends.
    """
    if os.name != "nt":
        return {"ok": False, "error": "scheduled-task install is Windows-only; elsewhere run "
                                      "`python src/paper_loop.py --interval 120` or use cron"}
    tr = f'"{_pythonw()}" "{os.path.abspath(__file__)}" --once'
    r = subprocess.run(["schtasks", "/Create", "/TN", _TASK_NAME, "/TR", tr,
                        "/SC", "MINUTE", "/MO", str(_TASK_INTERVAL_MIN), "/F", "/IT"],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    ok = r.returncode == 0
    if ok:  # fire once now so the first tick isn't up to two minutes away
        subprocess.run(["schtasks", "/Run", "/TN", _TASK_NAME],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    return {"ok": ok, "task": _TASK_NAME, "cadence": f"every {_TASK_INTERVAL_MIN} min",
            "detail": (r.stdout or r.stderr).strip()}


def uninstall_task() -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "Windows-only"}
    subprocess.run(["schtasks", "/End", "/TN", _TASK_NAME],
                   capture_output=True, text=True, creationflags=_NO_WINDOW)
    r = subprocess.run(["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    return {"ok": r.returncode == 0, "task": _TASK_NAME, "detail": (r.stdout or r.stderr).strip()}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """Health view for the orchestrator: is the upstream cache there, and what has this module done
    today? Deliberately file-only — no broker, no network — so it stays safe on a watchdog path."""
    when = provider.now_et()
    today = when.date().isoformat()
    books = [dict(r) for r in conn.execute(
        "SELECT * FROM fly_books WHERE trade_date = ?", (today,)).fetchall()]
    positions = conn.execute(
        "SELECT COUNT(*) FROM fly_positions WHERE trade_date = ?", (today,)).fetchone()[0]
    return {
        "ok": True,
        "date": today,
        "in_session": in_session(provider.minute_of_day(when)),
        # The orchestrator's watchdog reads this to tell "the loop is registered and quiet" from
        # "nothing is scheduled at all" — which look identical in an empty paper DB.
        "scheduled_task": task_installed(),
        "task_name": _TASK_NAME,
        "session_settled": session_already_settled(today),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        "books": books,
        "positions_today": positions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-flies paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache", help="override MEIC's stream cache path")
    ap.add_argument("--once", action="store_true", help="run a single iteration")
    ap.add_argument("--interval", type=int, metavar="SECONDS",
                    help="run continuously until the close")
    ap.add_argument("--settle", action="store_true", help="cash-settle today's books")
    ap.add_argument("--price", type=float, help="explicit settlement price (see --settle)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--install-task", action="store_true",
                    help=f"register the recurring {_TASK_NAME} task (every "
                         f"{_TASK_INTERVAL_MIN} min; Windows)")
    ap.add_argument("--uninstall-task", action="store_true")
    ap.add_argument("--eod-reports", action="store_true",
                    help="rewrite paper-eod / eod-analysis for a day without re-settling")
    ap.add_argument("--date", help="with --eod-reports, the day (YYYY-MM-DD); default today")
    ap.add_argument("--force", action="store_true", help="ignore the RTH session gate")
    args = ap.parse_args(argv)

    # Task registration touches no config and no database, so handle it before either is opened —
    # `--install-task` must work on a machine that has not been configured yet.
    if args.install_task:
        print(json.dumps(install_task(), indent=2))
        return 0
    if args.uninstall_task:
        print(json.dumps(uninstall_task(), indent=2))
        return 0

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    conn = dbmod.connect(args.db)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if args.eod_reports:
        day = args.date or provider.now_et().date().isoformat()
        print(json.dumps(eodmod.write_reports(conn, day), indent=2, default=str))
        return 0
    if args.settle:
        print(json.dumps(run_settle(config, conn, cache_path=cache_path, price=args.price),
                         indent=2, default=str))
        return 0
    if args.interval:
        _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
        while args.force or in_session(provider.minute_of_day(provider.now_et())):
            run_once(config, conn, cache_path=cache_path, force=args.force)
            time.sleep(args.interval)
        _log("session closed")
        return 0
    if args.once:
        print(json.dumps(run_once(config, conn, cache_path=cache_path, force=args.force),
                         indent=2, default=str))
        return 0

    ap.error("choose one of --once, --interval, --settle, --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
