"""Which names are judged illiquid, and so not worth the broker's time or a place in a listed view.

Decided 2026-10-07 (the user): once a name is found not liquid, nothing spends broker calls
re-checking it and no chart, watchlist or report list shows it -- until a liquidity sweep looks
again. A sweep is the first trading day of each month: that day every script measures every name
as before, and tonight's verdict re-judges the ones held illiquid.

**The test is the options-tradable one** (`tradable.py`): weekly options and $100M a day of stock.
The spread bar the user asked for joins it once it is chosen on complete readings. It is NOT the
universe builder's membership rule, which is far stricter (32 members of 600 candidates).

**Kept, not deleted.** An illiquid name's daily bars stay in the store: they cost no broker call
(local Dolt) and the market-report engines are scored against the vendor's tables, whose names are
mostly illiquid. Breadth, sector net and rank therefore still count every name; only the names a
view *lists* leave out the illiquid ones.

**Sticky.** Between sweeps an illiquid verdict stands, because its inputs are no longer refreshed
(the IV-rank fetch skips the name). A tradable name is judged afresh every night and can turn
illiquid any day. A name with no data has no verdict and is treated as tradable.

`python -m cherrypick.technicals liquidity` writes `market-report/liquidity/verdicts.json` from
local data only. Network-free, like the rest of the package.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from . import paths, symbols, tradable

ILLIQUID = "illiquid"
TRADABLE = "tradable"

# Reference series, never judged: the benchmarks the engines measure against, the rotation funds the
# report's rotation section places, and the cash indexes. They are not trade candidates, and hiding
# AGG or a sector fund for thin options would break the report, not tidy it.
REFERENCE = frozenset({*symbols.BENCHMARKS, *symbols.ROTATION_ETFS, *symbols.INDEXES})


def verdicts_path() -> Path:
    return paths.market_report_dir() / "liquidity" / "verdicts.json"


# ------------------------------------------------------------------------------------------------
# Pure: the judgement, the sweep day, the sticky update.


def judge(symbol: str, expiries_35d: int | None, dollar_volume: float | None) -> tuple[str | None, list[str]]:
    """(verdict, reasons). None when the data to judge is missing, or for a reference series."""
    if symbol in REFERENCE:
        return None, []
    if expiries_35d is None or dollar_volume is None:
        return None, []
    reasons = []
    if expiries_35d < tradable.WEEKLY_EXPIRIES:
        reasons.append(f"no weekly options ({expiries_35d} expiries in 35 days)")
    if dollar_volume < tradable.MIN_DOLLAR_VOLUME:
        reasons.append(f"${dollar_volume / 1e6:.0f}M a day, under ${tradable.MIN_DOLLAR_VOLUME / 1e6:.0f}M")
    return (ILLIQUID, reasons) if reasons else (TRADABLE, [])


def is_sweep_day(day: date) -> bool:
    """The first trading day of the month: the day illiquid names are measured and judged again."""
    from cherrypick.core import calendar as cal

    if not cal.is_trading_day(day):
        return False
    first = day.replace(day=1)
    while not cal.is_trading_day(first):
        first = cal.next_trading_day(first)
    return day == first


def update(
    previous: dict[str, dict], fresh: dict[str, tuple[str | None, list[str]]], today: str, sweep: bool
) -> dict[str, dict]:
    """Tonight's verdicts. An illiquid verdict holds until a sweep; everything else is judged afresh;
    a name tonight cannot judge keeps what it had."""
    out = dict(previous)
    for sym, (verdict, reasons) in fresh.items():
        held = previous.get(sym)
        if held and held.get("verdict") == ILLIQUID and not sweep:
            continue
        if verdict is None:
            continue
        out[sym] = {"verdict": verdict, "reasons": reasons, "judged_on": today}
    return out


# ------------------------------------------------------------------------------------------------
# Readers: what every consumer asks.


def load() -> dict[str, dict]:
    try:
        return json.loads(verdicts_path().read_text(encoding="utf-8")).get("names") or {}
    except (OSError, ValueError):
        return {}


def illiquid(names: dict[str, dict] | None = None) -> set[str]:
    """The names held illiquid now. Spelled as the store spells them (BRK.B)."""
    names = load() if names is None else names
    return {s for s, v in names.items() if v.get("verdict") == ILLIQUID}


def skip(today: date | None = None) -> set[str]:
    """What a broker-spending script leaves out today: the illiquid names, except on a sweep day."""
    today = today or date.today()
    return set() if is_sweep_day(today) else illiquid()


def listed(syms, hidden: set[str] | None = None) -> list[str]:
    """`syms` without the illiquid ones, order kept: the filter every listed view applies."""
    hidden = illiquid() if hidden is None else hidden
    return [s for s in syms if s not in hidden]


# ------------------------------------------------------------------------------------------------
# The nightly verdict.


def screener_names() -> list[str]:
    """Every name on the latest saved income screener lists, in the store's spelling."""
    root = paths.market_report_dir() / "vendor-screeners"
    days = sorted(p for p in root.glob("????-??-??") if p.is_dir())
    if not days:
        return []
    out = set()
    for f in days[-1].glob("*.json"):
        if f.name.endswith(".rejected.json"):
            continue
        body = json.loads(f.read_text(encoding="utf-8")).get("body") or {}
        for rows in body.values():
            if isinstance(rows, list):
                out.update(r["symbol"].replace("/", ".") for r in rows if isinstance(r.get("symbol"), str))
    return sorted(out)


def _dollar_volumes(conn, names: list[str], dolt_cfg: dict) -> dict[str, float]:
    """50-session median of close x volume: the store's bars, else Dolt's for names it does not hold
    (most screener names). A Dolt outage leaves those names unjudged, never guessed."""
    from . import store, universe

    out: dict[str, float] = {}
    missing = []
    for s in names:
        bars = store.raw_bars(conn, s)
        if len(bars) >= universe.WINDOW:
            out[s] = statistics.median(b.close * (b.volume or 0) for b in bars[-universe.WINDOW :])
        else:
            missing.append(s)
    if missing:
        try:
            out.update(_dolt_dollar_volumes(missing, dolt_cfg))
        except Exception:  # noqa: BLE001 -- reported as unjudged by the caller
            pass
    return out


def _dolt_dollar_volumes(names: list[str], cfg: dict) -> dict[str, float]:
    from . import land, universe

    cfg = {**land.DEFAULTS, **cfg}
    db = land._connect(cfg, cfg["stocks_db"])
    try:
        cur = db.cursor()
        marks = ",".join(["%s"] * len(names))
        cur.execute(
            "SELECT act_symbol, close, volume FROM ohlcv "
            f"WHERE date > DATE_SUB(CURDATE(), INTERVAL 100 DAY) AND act_symbol IN ({marks}) "
            "ORDER BY act_symbol, date",
            names,
        )
        by: dict[str, list[float]] = {}
        for sym, close, volume in cur.fetchall():
            by.setdefault(sym, []).append(float(close) * float(volume or 0))
    finally:
        db.close()
    return {s: statistics.median(v[-universe.WINDOW :]) for s, v in by.items() if len(v) >= universe.WINDOW}


def build(dolt_cfg: dict | None = None, today: date | None = None) -> dict[str, Any]:
    """Judge every name the store holds and every listed screener name; write the verdicts."""
    from . import store

    today = today or date.today()
    sweep = is_sweep_day(today)
    names = sorted({*symbols.all_symbols(), *screener_names()} - REFERENCE)
    rows, label_day = tradable.latest_rows()
    conn = store.connect()
    try:
        dv = _dollar_volumes(conn, names, dolt_cfg or {})
    finally:
        conn.close()
    fresh = {s: judge(s, (rows.get(s) or {}).get("expiries_35d"), dv.get(s)) for s in names}
    previous = load()
    verdicts = update(previous, fresh, today.isoformat(), sweep)
    doc = {"updated_on": today.isoformat(), "sweep": sweep, "label_day": label_day, "names": verdicts}
    path = verdicts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)
    held = illiquid(verdicts)
    return {
        "ok": True,
        "sweep": sweep,
        "names": len(names),
        "illiquid": len(held),
        "tradable": sum(1 for v in verdicts.values() if v.get("verdict") == TRADABLE),
        "unjudged": sum(1 for s in names if s not in verdicts),
        "newly_illiquid": sorted(s for s in held if (previous.get(s) or {}).get("verdict") != ILLIQUID),
        "readmitted": sorted(
            s for s, v in previous.items() if v.get("verdict") == ILLIQUID and s not in held
        ),
        "file": str(path),
    }
