"""Plan item 14's (or 17's) trades: which credit spreads each selector and baselines take, the chains to fetch.

    python scripts/credit_spread_plan.py [--item 14|17] [--workers 14] [--out DIR] [--limit-names N]

**Item 17** (`--item 17`, 2026-10-24; our own credit-spread list) is the same planner with its own
declared spec (`ITEMS`): every one of the four chart setups and its short mirror as a selector of its
own (a long setup a put spread, a mirror a call spread), the short-put screener's $100M universe
rather than $300M, no index reference, the declared leveraged, inverse and volatility funds left out,
and its own seed. Everything below -- entry, expiry, exclusions, baselines -- is shared, as item 17
declares. The structure (short, widths) is the study's, not the plan's: a trade's chain carries them all.

Step 2 of hypothesis 14 in docs/edge-hypotheses-plan.md (selecting credit spreads). It decides, from
local data alone, every trade the declared test will price, and writes the end-of-day chain requests
`python -m cherrypick.backtest fetch-chains` reads. Read-only throughout; no vendor data is read here.

**Selectors** (B, the signal board, waits for its historical builder):
- **A**, a put spread: the study's `reversion` setup (mean reversion, long) on a name trading at least
  $300M a day on the signal session (`mr-300m`).
- **C**, a call spread: `pullback-short` and `reversion-short`, under the same $300M filter.
- **D**, the reference: SPY, QQQ and IWM on every 5th session, a put spread and a call spread, with
  no baselines.

Signals come from `cherrypick.technicals.setups.run` on `study.load`'s readings, counted by
`study.counted` (the universe, the shadow of a suspected corporate action), exactly as the study does.

**A trade** enters at the end of the session after its signal, on the latest standard monthly 30-45
days out (the third Friday, or the trading day before it when that Friday is a holiday). It is dropped,
and the reason counted, when:
- no monthly falls 30-45 days out;
- the expiry is after the design window's end (`cherrypick.backtest.settings`' holdout starts the
  next day): a trade judged on holdout prices would spend the holdout;
- an earnings report falls between entry and expiry. From 2020-01-22 that comes from Dolt's
  `earnings_calendar`, exactly; before it, from `eps_history` quarter ends, a report assumed 14-60
  days after each (the declared approximation; those years are reported, not decided);
- for a call spread, an ex-dividend date falls after entry and on or before expiry (early assignment
  is not modelled, so the spread that risks it is left out);
- a suspected unadjusted split falls between entry and expiry.

**Baselines:** five for each selector trade, the same name, a session drawn at random from the same
calendar year, on which the name was in the universe at $300M a day the session before, with the same
side and every exclusion above applied. Drawn without replacement from a `random.Random` seeded by
`SEED`, the trade's id and the name, so the plan reproduces exactly; a year with fewer eligible
sessions gives fewer, and says so.

**Writes** `trades.json` (every trade and baseline: id, kind, of, selector, setup, side, symbol,
signal, entry, expiry, era -- `deciding` from 2020-01-22, else `reported`) and `requests.json` (each
distinct symbol, entry and expiry once, the deciding era first), under
`~/.cherrypick/data/backtest/results/research/item14/` by default.
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import home

SEED = 20261020
BASELINES = 5
START = "2016-01-04"
EXACT_EARNINGS_FROM = "2020-01-22"
DOLLAR_FLOOR = 300e6
DTE_RANGE = (30, 45)
REPORT_LAG_DAYS = (14, 60)
SELECTORS = {"A": (("reversion", "put"),), "C": (("pullback-short", "call"), ("reversion-short", "call"))}
REFERENCE = ("SPY", "QQQ", "IWM")
REFERENCE_EVERY = 5

# Each item's declared spec. Item 14's is the constants above, unchanged, so its plan reproduces.
_SETUPS17 = ("trend", "pullback", "reversion", "breakout")
ITEMS: dict[int, dict] = {
    14: {
        "seed": SEED,
        "floor": DOLLAR_FLOOR,
        "selectors": SELECTORS,
        "reference": REFERENCE,
        "exclude_leveraged": False,
        "out": "item14",
    },
    17: {
        "seed": 20261024,
        "floor": 100e6,
        # One selector per setup-side, named by its setup id: 8 in all.
        "selectors": {
            **{s: ((s, "put"),) for s in _SETUPS17},
            **{f"{s}-short": ((f"{s}-short", "call"),) for s in _SETUPS17},
        },
        "reference": (),
        "exclude_leveraged": True,
        "out": "item17",
    },
}


def monthly(y: int, m: int) -> date:
    d = date(y, m, 15)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


MONTHLIES = sorted(monthly(y, m) for y in range(2015, 2028) for m in range(1, 13))


def expiry_for(entry: str) -> str | None:
    """The latest standard monthly 30-45 days after `entry`, or None."""
    d = date.fromisoformat(entry)
    inside = [e for e in MONTHLIES if DTE_RANGE[0] <= (e - d).days <= DTE_RANGE[1]]
    return inside[-1].isoformat() if inside else None


def any_between(sorted_days: list[str], lo: str, hi: str, *, include_lo: bool = True) -> bool:
    """Whether a day in `sorted_days` falls in [lo, hi] (or (lo, hi] without `include_lo`)."""
    i = bisect.bisect_left(sorted_days, lo) if include_lo else bisect.bisect_right(sorted_days, lo)
    return i < len(sorted_days) and sorted_days[i] <= hi


def report_in_trade(sym: str, entry: str, expiry: str, reports: dict, quarters: dict) -> bool:
    if entry >= EXACT_EARNINGS_FROM:
        return any_between(reports.get(sym, []), entry, expiry)
    lo, hi = REPORT_LAG_DAYS
    for q in quarters.get(sym, []):
        qd = date.fromisoformat(q)
        a, b = (qd + timedelta(days=lo)).isoformat(), (qd + timedelta(days=hi)).isoformat()
        if a <= expiry and b >= entry:
            return True
    return False


# ------------------------------------------------------------------------------------------------
# Per-name work (in a worker process)

_ctx: dict = {}


def _init(design_end: str, reports: dict, quarters: dict, spec: dict | None = None) -> None:
    from cherrypick.technicals import paths, store

    conn = store.connect(paths.history_db())
    _ctx.update(
        conn=conn,
        data_end=conn.execute("SELECT MAX(date) FROM bars").fetchone()[0],
        design_end=design_end,
        reports=reports,
        quarters=quarters,
        spec=spec or ITEMS[14],
    )


def _exdates(sym: str) -> list[str]:
    rows = _ctx["conn"].execute("SELECT ex_date FROM dividends WHERE symbol = ? ORDER BY ex_date", (sym,))
    return [r[0] for r in rows]


def _why_not(nm, j: int, side: str, exdates: list[str]) -> tuple[str | None, str | None]:
    """(expiry, None) when a trade can be entered at session index `j`, else (None, reason)."""
    entry = nm.dates[j]
    exp = expiry_for(entry)
    if exp is None:
        return None, "no monthly 30-45 days out"
    if exp > _ctx["design_end"]:
        return None, "expiry after the design window"
    if report_in_trade(nm.symbol, entry, exp, _ctx["reports"], _ctx["quarters"]):
        return None, "earnings report inside the trade"
    if side == "call" and any_between(exdates, entry, exp, include_lo=False):
        return None, "ex-dividend date inside a call spread"
    if nm.suspects:
        k = bisect.bisect_right(nm.dates, exp)
        if any(j <= s < k for s in nm.suspects):
            return None, "suspected unadjusted split inside the trade"
    return exp, None


def _eligible_session(nm, j: int) -> bool:
    """In the universe at the item's floor ($300M a day for 14) the session before entry, and outside
    a corporate-action shadow."""
    return (
        j >= 1
        and nm.member[j - 1]
        and (nm.dollar_volume[j - 1] or 0.0) >= _ctx["spec"]["floor"]
        and not (nm.shadow and nm.shadow[j])
    )


def plan_name(sym: str) -> dict:
    from cherrypick.technicals import setups, study

    spec = _ctx["spec"]
    nm = study.load(_ctx["conn"], sym, _ctx["data_end"])
    out = {"trades": [], "dropped": Counter(), "short": 0}
    if nm is None:
        return out
    exdates = _exdates(sym)
    floor = spec["floor"]
    allow = lambda i: (nm.dollar_volume[i] or 0.0) >= floor  # noqa: E731
    by_year: dict[str, list[int]] = {}
    for j in range(1, len(nm.dates)):
        if START <= nm.dates[j] <= _ctx["design_end"] and _eligible_session(nm, j):
            by_year.setdefault(nm.dates[j][:4], []).append(j)
    for selector, specs in spec["selectors"].items():
        for setup_id, side in specs:
            for t in setups.run(setup_id, nm.readings, allow=allow):
                i = t.entry
                j = i + 1
                if j >= len(nm.dates) or not (START <= nm.dates[j] <= _ctx["design_end"]):
                    continue
                if not study.counted(nm, setup_id, i):
                    continue
                exp, why = _why_not(nm, j, side, exdates)
                if why:
                    out["dropped"][f"{selector} {setup_id}: {why}"] += 1
                    continue
                tid = f"{selector}:{setup_id}:{sym}:{nm.dates[i]}"
                entry = nm.dates[j]
                out["trades"].append(
                    _row(tid, "trade", None, selector, setup_id, side, sym, nm.dates[i], entry, exp)
                )
                rng = random.Random(f"{spec['seed']}:{tid}")
                pool = [k for k in by_year.get(entry[:4], []) if k != j]
                rng.shuffle(pool)
                drawn = 0
                for k in pool:
                    if drawn == BASELINES:
                        break
                    bexp, bwhy = _why_not(nm, k, side, exdates)
                    if bwhy:
                        continue
                    drawn += 1
                    out["trades"].append(
                        _row(
                            f"{tid}#b{drawn}",
                            "baseline",
                            tid,
                            selector,
                            setup_id,
                            side,
                            sym,
                            None,
                            nm.dates[k],
                            bexp,
                        )
                    )
                if drawn < BASELINES:
                    out["short"] += 1
    if sym in spec["reference"]:
        sessions = [j for j in range(1, len(nm.dates)) if START <= nm.dates[j] <= _ctx["design_end"]]
        for n, j in enumerate(sessions):
            if n % REFERENCE_EVERY:
                continue
            for side in ("put", "call"):
                exp, why = _why_not(nm, j, side, exdates)
                if why:
                    out["dropped"][f"D {side}: {why}"] += 1
                    continue
                tid = f"D:{side}:{sym}:{nm.dates[j]}"
                out["trades"].append(
                    _row(tid, "reference", None, "D", None, side, sym, None, nm.dates[j], exp)
                )
    return out


def _row(tid, kind, of, selector, setup_id, side, sym, signal, entry, exp) -> dict:
    return {
        "id": tid,
        "kind": kind,
        "of": of,
        "selector": selector,
        "setup": setup_id,
        "side": side,
        "symbol": sym,
        "signal": signal,
        "entry": entry,
        "expiry": exp,
        "era": "deciding" if entry >= EXACT_EARNINGS_FROM else "reported",
    }


def _plan_many(symbols: list[str]) -> list[dict]:
    return [plan_name(s) for s in symbols]


# ------------------------------------------------------------------------------------------------
# Inputs read once, in the main process


def _earnings() -> tuple[dict, dict]:
    """({symbol: sorted report dates}, {symbol: sorted quarter ends}) from the local Dolt clone."""
    from cherrypick.technicals import land

    db = land._connect(land.DEFAULTS, "earnings")
    try:
        cur = db.cursor()
        cur.execute("SELECT act_symbol, date FROM earnings_calendar")
        reports: dict[str, list[str]] = {}
        for sym, d in cur.fetchall():
            reports.setdefault(str(sym).replace("/", "."), []).append(str(d)[:10])
        cur.execute("SELECT act_symbol, period_end_date FROM eps_history")
        quarters: dict[str, list[str]] = {}
        for sym, d in cur.fetchall():
            quarters.setdefault(str(sym).replace("/", "."), []).append(str(d)[:10])
    finally:
        db.close()
    return {k: sorted(v) for k, v in reports.items()}, {k: sorted(v) for k, v in quarters.items()}


def universe_names(spec: dict, history_db: Path, leveraged: set[str] | None = None) -> list[str]:
    """Every name that ever traded the item's floor a day (`peaks`), plus its reference funds, less
    the declared leveraged, inverse and volatility funds when the item leaves them out (item 17; a
    long setup on an inverse fund is a short)."""
    c = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True)
    try:
        rows = c.execute(
            "SELECT symbol FROM peaks WHERE max_dollar_volume >= ? ORDER BY symbol", (spec["floor"],)
        )
        names = {r[0] for r in rows} | set(spec["reference"])
    finally:
        c.close()
    if spec["exclude_leveraged"]:
        if leveraged is None:
            from cherrypick.technicals.skate import leveraged_funds

            leveraged = {s.replace("/", ".") for s in leveraged_funds()}
        names -= leveraged
    return sorted(names)


def _design_end() -> str:
    from cherrypick.backtest import settings

    holdout_start = settings.load()["windows"]["holdout"][0]
    return (date.fromisoformat(holdout_start) - timedelta(days=1)).isoformat()


def main(argv: list[str] | None = None) -> int:
    from cherrypick.technicals import paths

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", type=int, choices=sorted(ITEMS), default=14)
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--out", type=Path, help="default: results/research/item<N> under the backtest data dir")
    ap.add_argument("--limit-names", type=int, help="plan only the first N names (a trial)")
    args = ap.parse_args(argv)
    spec = ITEMS[args.item]
    out_dir = args.out or home.data_dir("backtest") / "results" / "research" / spec["out"]

    design_end = _design_end()
    reports, quarters = _earnings()
    names = universe_names(spec, paths.history_db())
    if args.limit_names:
        names = names[: args.limit_names]
    chunks = [names[k :: args.workers] for k in range(args.workers)]
    trades: list[dict] = []
    dropped: Counter = Counter()
    short = 0
    with ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(design_end, reports, quarters, spec)
    ) as ex:
        for part in ex.map(_plan_many, chunks):
            for p in part:
                trades.extend(p["trades"])
                dropped.update(p["dropped"])
                short += p["short"]
    trades.sort(key=lambda t: (t["era"] != "deciding", t["entry"], t["id"]))
    seen: dict[tuple[str, str, str], None] = {}
    for t in trades:
        seen.setdefault((t["symbol"], t["entry"], t["expiry"]), None)
    requests = [{"symbol": s, "session": d, "expiration": e} for s, d, e in seen]

    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        **({} if args.item == 14 else {"item": args.item, "floor": spec["floor"]}),
        "seed": spec["seed"],
        "baselines": BASELINES,
        "start": START,
        "design_end": design_end,
        "exact_earnings_from": EXACT_EARNINGS_FROM,
        "names": len(names),
    }
    (out_dir / "trades.json").write_text(json.dumps({**meta, "trades": trades}), encoding="utf-8")
    (out_dir / "requests.json").write_text(json.dumps({**meta, "requests": requests}), encoding="utf-8")

    summary = Counter((t["selector"], t["setup"], t["kind"], t["era"]) for t in trades)
    print(json.dumps({**meta, "out": str(out_dir)}, indent=1))
    for key in sorted(summary, key=str):
        print(f"  {key[0]} {str(key[1]):16s} {key[2]:9s} {key[3]:8s} {summary[key]:7d}")
    print(f"  trades short of {BASELINES} baselines: {short}")
    deciding = sum(1 for t in trades if t["era"] == "deciding")
    print(f"  requests: {len(requests)} ({deciding} trade rows deciding)")
    print("  dropped:")
    for why, n in sorted(dropped.items()):
        print(f"    {why}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
