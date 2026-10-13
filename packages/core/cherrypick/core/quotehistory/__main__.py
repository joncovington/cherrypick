"""`python -m cherrypick.core.quotehistory coverage` -- what the recorder's quote history holds.

    coverage [--symbol SPX ...] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--db PATH] [--gap-seconds 60]

One row per trading session from `--since` (default: the first recorded session, 2026-10-13) through
`--until` (default: today, ET): whether anything was kept, how many samples, the first and last
sample (ET clock), how many samples had no spot joined (and why), and every gap between consecutive
samples longer than `--gap-seconds` inside the session. A session with no samples is listed, never
skipped: a missing day is the thing a coverage read exists to show.

Reads ONLY through `session()`, this package's API, never the table: the table's layout is the
recorder's to change, and a replay reads the same function. Read-only; prints JSON; exit 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import calendar as _cal
from cherrypick.core import clock as _clock
from cherrypick.core import quotehistory as _qh

# The first session the gex recorder kept chain quotes for (flies CLAUDE.md, core.quotehistory).
RECORDED_FROM = "2026-10-13"
DEFAULT_GAP_SECONDS = 60.0


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, _clock.ET).strftime("%H:%M:%S")


def session_coverage(samples: list[dict], *, gap_seconds: float = DEFAULT_GAP_SECONDS) -> dict:
    """One session's coverage from its `session()` samples. Pure."""
    if not samples:
        return {"recorded": False, "samples": 0, "first": None, "last": None, "gaps": [], "max_gap_s": None,
                "no_spot": 0, "no_spot_reasons": {}, "strikes_median": None}  # fmt: skip
    ts = [float(s["ts"]) for s in samples]
    gaps = [
        {"from": _hhmm(a), "to": _hhmm(b), "seconds": round(b - a, 1)}
        for a, b in zip(ts, ts[1:], strict=False)
        if b - a > gap_seconds
    ]
    reasons: dict[str, int] = {}
    for s in samples:
        if s.get("spot_reason"):
            reasons[s["spot_reason"]] = reasons.get(s["spot_reason"], 0) + 1
    strikes = sorted(len(s.get("puts") or {}) + len(s.get("calls") or {}) for s in samples)
    return {
        "recorded": True,
        "samples": len(samples),
        "first": _hhmm(ts[0]),
        "last": _hhmm(ts[-1]),
        "gaps": gaps,
        "max_gap_s": round(max((b - a for a, b in zip(ts, ts[1:], strict=False)), default=0.0), 1),
        "no_spot": sum(reasons.values()),
        "no_spot_reasons": reasons,
        "strikes_median": strikes[len(strikes) // 2],
    }


def coverage(
    symbols: list[str],
    since: str,
    until: str,
    *,
    history_db: Path | str | None = None,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    reader=_qh.session,
) -> dict:
    """Every trading session in [since, until] for each symbol, through `reader` (`session()`)."""
    days: list[str] = []
    d = date.fromisoformat(since)
    end = date.fromisoformat(until)
    while d <= end:
        if _cal.is_trading_day(d):
            days.append(d.isoformat())
        d += timedelta(days=1)
    out = {}
    for symbol in symbols:
        rows = []
        for day in days:
            samples = reader(symbol, day, history_db=history_db)
            rows.append({"session": day, **session_coverage(samples, gap_seconds=gap_seconds)})
        out[symbol] = {
            "sessions": rows,
            "recorded": sum(1 for r in rows if r["recorded"]),
            "missing": [r["session"] for r in rows if not r["recorded"]],
        }
    return {"since": since, "until": until, "gap_seconds": gap_seconds, "symbols": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.core.quotehistory", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("coverage")
    p.add_argument("--symbol", action="append", dest="symbols")
    p.add_argument("--since", default=RECORDED_FROM)
    p.add_argument("--until", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--gap-seconds", dest="gap_seconds", type=float, default=DEFAULT_GAP_SECONDS)
    args = parser.parse_args(argv)

    path = Path(args.db) if args.db else _qh.default_history_db()
    until = args.until or datetime.now(_clock.ET).date().isoformat()
    head = {"ok": True, "db": str(path), "exists": path.exists(), "recorded_from": RECORDED_FROM}
    if until < args.since:
        print(json.dumps({**head, "since": args.since, "until": until, "symbols": {}}))
        return 0
    out = coverage(args.symbols or ["SPX"], args.since, until, history_db=path, gap_seconds=args.gap_seconds)
    print(json.dumps({**head, **out}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
