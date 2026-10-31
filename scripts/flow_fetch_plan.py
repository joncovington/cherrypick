"""Write the flow fetch's plan: which names on which sessions, as `docs/flow-signal-plan.md` declares.

**Sessions.** A backtest window, thinned on a fixed rule: every 5th session counted from the design
window's first. The holdout carries that count on (item 32), so its grid is the same rule and the
two never overlap an outcome window. Plus, for the earnings family, the 10 sessions before each
report's reaction day (the report session itself for one before the open, the next for one after
the close), from the local Dolt earnings calendar. Reports are the window's own, and so are the
sessions before them: a report in the holdout's first fortnight loses the sessions that fall before
it, as the design window's first reports do.

**Names.** On each session, the stocks (Dolt lists them as operating companies, not funds) whose raw
close was at least $5 and whose 50-session median dollar volume was at least $100M, both up to the
session before. The technicals universe rule at the options-tradable bar, so 2022 is not chosen
from 2026's survivors. An earnings window takes a name only on the sessions it passes the same bar.

**The file is the declaration.** The grid is counted on the calendar as it stands when the plan is
written, so a calendar fix moves later grid sessions (the 2025-01-09 closure did, on 2026-10-25).
The study therefore reads its grid sessions from the plan file, never from the calendar.

Local reads only (the technicals history store and the Dolt clone), deterministic: the same stores
give the same file. The fetch reads it with `python -m cherrypick.backtest fetch-flow --plan FILE`.
With no output path the file goes under `~/.cherrypick/data/backtest/flow/`.

    python scripts/flow_fetch_plan.py [--window design|holdout] [FILE]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from cherrypick.backtest import paths as bt_paths
from cherrypick.backtest import settings as bt_settings
from cherrypick.core import calendar as cal
from cherrypick.technicals import earnings_calendar as ec
from cherrypick.technicals import history, store, universe
from cherrypick.technicals import land as _land

MIN_PRICE = universe.MIN_PRICE
FLOOR = 100e6  # the options-tradable stock bar
STEP = 5  # every 5th session, counted from the design window's first
PRE_SESSIONS = 10  # sessions before a report's reaction day
FILES = {"design": "flow-plan.json", "holdout": "flow-plan-holdout.json"}


def trading_days(start: str, end: str) -> list[str]:
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= last:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out


def members(conn, start: str, end: str) -> dict[str, set[str]]:
    """Each stock's sessions in [start, end] on which it passes the bar, judged from the session before."""
    out: dict[str, set[str]] = {}
    for sym in store.stocks(conn, history.names_peaking_at_least(conn, FLOOR)):
        bars = store.raw_bars(conn, sym)
        med = universe.rolling_median([b.close * (b.volume or 0.0) for b in bars])
        days = {
            bars[i].date
            for i in range(1, len(bars))
            if start <= bars[i].date <= end
            and med[i - 1] is not None
            and med[i - 1] >= FLOOR
            and bars[i - 1].close >= MIN_PRICE
        }
        if days:
            out[sym] = days
    return out


def grid_sessions(count_from: str, lo: str, hi: str) -> list[str]:
    """Every STEP-th trading day counted from `count_from`, kept when it falls in [lo, hi]. The design
    window counts from its own first session; the holdout carries the same count on from the
    design's first, so the two grids are one rule whatever session the holdout opens on."""
    return [d for i, d in enumerate(trading_days(count_from, hi)) if i % STEP == 0 and lo <= d <= hi]


def build(span: list[str], days: list[str], member: dict[str, set[str]], reports, grid: list[str]) -> dict:
    """The plan: pure over the window's sessions, the calendar, the membership, the (symbol, date,
    timing) reports and the grid sessions."""
    lo, hi = span[0], span[-1]
    plan: dict[str, set[str]] = {d: {s for s, m in member.items() if d in m} for d in grid}
    idx = {d: i for i, d in enumerate(days)}
    events = 0
    for sym, d, timing in reports:
        if sym not in member:
            continue
        react = ec.reaction_day(d, timing).isoformat()
        if react not in idx:
            continue
        pre = [days[j] for j in range(max(0, idx[react] - PRE_SESSIONS), idx[react])]
        pre = [p for p in pre if lo <= p <= hi and p in member[sym]]
        events += bool(pre)
        for p in pre:
            plan.setdefault(p, set()).add(sym)
    sessions = {d: sorted(v) for d, v in sorted(plan.items()) if v}
    return {
        "rule": {
            "floor": FLOOR,
            "min_price": MIN_PRICE,
            "step": STEP,
            "pre_sessions": PRE_SESSIONS,
            "window": [lo, hi],
        },  # fmt: skip
        "grid_sessions": grid,
        "earnings_events": events,
        "sessions": sessions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", help="the plan file to write (default: under the backtest flow dir)")
    ap.add_argument("--window", choices=sorted(FILES), default="design")
    args = ap.parse_args()
    windows = bt_settings.load()["windows"]
    lo, hi = windows[args.window]
    first = windows["design"][0]
    span = trading_days(lo, hi)
    grid = grid_sessions(first, lo, hi)
    # Reaction days fall up to a session past the window, and windows reach back before it.
    days = trading_days(f"{int(lo[:4]) - 1}-01-01", f"{hi[:4]}-12-31")
    member = members(history.connect(), lo, hi)
    dolt = _land._connect({**_land.DEFAULTS}, "earnings")
    try:
        cur = dolt.cursor()
        cur.execute(
            "SELECT act_symbol, date, `when` FROM earnings_calendar WHERE date BETWEEN %s AND %s", (lo, hi)
        )
        reports = [
            (s, d if isinstance(d, date) else date.fromisoformat(str(d)), w) for s, d, w in cur.fetchall()
        ]
    finally:
        dolt.close()
    plan = build(span, days, member, reports, grid)
    out = Path(args.out).expanduser() if args.out else bt_paths.data_dir() / "flow" / FILES[args.window]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan), encoding="utf-8")
    pairs = sum(len(v) for v in plan["sessions"].values())
    on_grid = sum(len(plan["sessions"].get(d, [])) for d in plan["grid_sessions"])
    print(
        f"{args.window}: {len(member)} names ever in; {len(plan['grid_sessions'])} grid sessions, "
        f"{on_grid} name-days; {plan['earnings_events']} reports; {len(plan['sessions'])} sessions, "
        f"{pairs} name-days in all -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
