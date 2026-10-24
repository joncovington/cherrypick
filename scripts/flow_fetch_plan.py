"""Write the flow fetch's plan: which names on which sessions, as `docs/flow-signal-plan.md` declares.

**Sessions.** The backtest design window, thinned on a fixed rule: every 5th session counted from
the window's first. Plus, for the earnings family, the 10 sessions before each report's reaction
day (the report session itself for one before the open, the next for one after the close), from
the local Dolt earnings calendar.

**Names.** On each session, the stocks (Dolt lists them as operating companies, not funds) whose raw
close was at least $5 and whose 50-session median dollar volume was at least $100M, both up to the
session before. The technicals universe rule at the options-tradable bar, so 2022 is not chosen
from 2026's survivors. An earnings window takes a name only on the sessions it passes the same bar.

Local reads only (the technicals history store and the Dolt clone), deterministic: the same stores
give the same file. The fetch reads it with `python -m cherrypick.backtest fetch-flow --plan FILE`.

    python scripts/flow_fetch_plan.py ~/.cherrypick/data/backtest/flow-plan.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from cherrypick.backtest import settings as bt_settings
from cherrypick.core import calendar as cal
from cherrypick.technicals import earnings_calendar as ec
from cherrypick.technicals import history, store, universe
from cherrypick.technicals import land as _land

MIN_PRICE = universe.MIN_PRICE
FLOOR = 100e6  # the options-tradable stock bar
STEP = 5  # every 5th design session
PRE_SESSIONS = 10  # sessions before a report's reaction day


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


def build(design: list[str], days: list[str], member: dict[str, set[str]], reports) -> dict:
    """The plan: pure over the session lists, the membership and the (symbol, date, timing) reports."""
    lo, hi = design[0], design[-1]
    grid = design[::STEP]
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
            "design": [lo, hi],
        },  # fmt: skip
        "grid_sessions": grid,
        "earnings_events": events,
        "sessions": sessions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", help="the plan file to write")
    args = ap.parse_args()
    lo, hi = bt_settings.load()["windows"]["design"]
    design = trading_days(lo, hi)
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
    plan = build(design, days, member, reports)
    Path(args.out).expanduser().write_text(json.dumps(plan), encoding="utf-8")
    pairs = sum(len(v) for v in plan["sessions"].values())
    grid = sum(len(plan["sessions"].get(d, [])) for d in plan["grid_sessions"])
    print(
        f"{len(member)} names ever in; {len(plan['grid_sessions'])} grid sessions, {grid} name-days; "
        f"{plan['earnings_events']} reports; {len(plan['sessions'])} sessions, {pairs} name-days in all"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
