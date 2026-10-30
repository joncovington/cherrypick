"""Item 28, the share ladder before 2011 and on single stocks (docs/edge-hypotheses-plan.md): item 26's
arms on daily-reset funds rebuilt from their 1x history back to 1999, and on single-stock 2x funds
rebuilt from their stocks.

    python scripts/share_ladder_item28.py fetch [--force]
    python scripts/share_ladder_item28.py study [--json PATH] [--names A,B]

Writes under `~/.cherrypick/data/research/item28/`, never into the repo.

**Names** (`fetch`, saved to `names.json` for `study`):
- Funds: item 26's judged names (`share_ladder_item26.names_in` on item 26's `bars.db`, one fund
  per counterpart and leverage) that have a 1x counterpart and a leverage in `fetch_ladder_bars`.
- Single stocks: every long 2x fund in `leveraged_funds.json` whose name carries its stock's
  ticker ("2X Long NVDA", "Daily TSLA Bull 2X") at item 26's dollar-volume floor on the same file,
  one fund per stock (the most traded), kept if Yahoo calls the stock an equity. A fund whose name
  spells the company out ("2X Long Apple") is not matched; its stock is reached through another
  fund when one clears the floor.

**Data** (`fetch`): `bars.db` beside `fetch_ladder_bars`' own schema, plus `adjclose` (Yahoo's
split- and dividend-adjusted close) and `instruments` (Yahoo's instrument type). Broker daily
candles first, Yahoo's daily bars for every session (the merge is item 26's); Treasury's yearly
par-yield-curve files from 1999 in `treasury/`. A past year's file already there is kept.

**The rebuilt fund** (`rebuild`), from `REBUILD_FROM` or the 1x series' first bar: the first
session at `BASE`; then each close is the previous rebuilt close times 1 + leverage x the 1x total
return (adjusted closes; the raw close where either is missing) less (`FEE` + (leverage - 1) x the
3-month bill rate of the last curve before the session) / 252. Open, high and low are the previous
rebuilt close times 1 + leverage x the 1x bar's own move from its previous close, widened to hold
the open and close. A factor under `FLOOR` is held at it and counted.

**Validation** (`validate`), on the sessions the real fund and the rebuilt one share (from
`LEVERAGE_FROM` for a fund whose leverage changed): daily returns (Yahoo adjusted closes for the
real fund) correlate at `MIN_CORR` or more, and the calendar years with `MIN_YEAR_SESSIONS` or more
shared sessions differ by `MAX_YEAR_DIFF` points or less on average. Reported beside: the slope of
the real fund's returns on the rebuilt ones, and whether the rebuilt series ends the overlap
richer. A name that fails is reported and not used.

**Measured.** Funds: item 26's `measure` (arm, random-entry control, edge) per arm in the pre-2011
holdout and the two stress reads; item 27's `describe` for its base and arms in the holdout and
over the whole rebuilt span; item 26's replay without a control over the whole span for the
default-list rule. Single stocks: item 26's `measure` and item 27's `describe` over `SINGLE`.
Windows start no earlier than the rebuilt series' `HISTORY + 1`th session.

**The verdict**, per item 26 arm that passed item 26 (its `results.json`): in the holdout, the
funds' mean edge above zero and positive on more than half; under item 26's name or closed-lot
floor, no verdict. Other arms are reported. **Default list**, per arm: a name leaves it with a lot
held over `LONG_HOLD_DAYS` or a drawdown over `MAX_DRAWDOWN_PCT` of its budget (funds: over the
whole rebuilt span; stocks: over `SINGLE`, where the edge must also be above zero).
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import treasury
from cherrypick.core.home import data_dir

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fetch_ladder_bars as fbars  # noqa: E402 - beside this script


def _item27():
    spec = importlib.util.spec_from_file_location("share_ladder_item27", HERE / "share_ladder_item27.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["share_ladder_item27"] = module
    spec.loader.exec_module(module)
    return module


s27 = _item27()
sl = s27.sl

REBUILD_FROM = "1999-01-01"
BASE = 100.0
FEE = 0.0095
FLOOR = 0.01
HISTORY = 20
HOLDOUT_END = "2010-12-31"
STRESS = {"stress-2000": ("2000-03-01", "2002-12-31"), "stress-2008": ("2007-10-01", "2009-12-31")}
SINGLE = ("2000-01-03", "2026-10-28")
FULL_END = "2026-10-28"
SINGLE_LEVERAGE = 2
# Leverage changed to today's on this date (Direxion's 3x-to-2x funds, 2020); validated from it.
LEVERAGE_FROM = {"ERX": "2020-04-01", "NUGT": "2020-04-01"}
MIN_CORR = 0.99
MAX_YEAR_DIFF = 3.0
MIN_YEAR_SESSIONS = 20
LONG_HOLD_DAYS = 1826  # 5 years
MAX_DRAWDOWN_PCT = 90.0
SEED = 28
TREASURY_FROM = 1999

DIR = data_dir("research") / "item28"
STOCK_IN_NAME = (
    re.compile(r"\b2[xX] Long ([A-Z][A-Z0-9]{0,5}) "),
    re.compile(r"\bDaily ([A-Z][A-Z0-9]{0,5}) Bull 2X\b"),
)


# ---- names ----


def stock_in_name(name: str) -> str | None:
    """The ticker a single-stock long 2x fund's name carries, or None."""
    for pattern in STOCK_IN_NAME:
        m = pattern.search(name)
        if m:
            return m.group(1)
    return None


def single_stock_funds() -> dict[str, str]:
    """{stock: its most traded long 2x fund} at item 26's dollar-volume floor."""
    from cherrypick.technicals import dollarvolume
    from cherrypick.technicals.skate import leveraged_funds

    dv_names, _ = dollarvolume.latest(sl.DOLLAR_VOLUME_FILE)
    med = dollarvolume.medians(dv_names, 30)
    best: dict[str, str] = {}
    for fund, f in leveraged_funds().items():
        stock = stock_in_name(str(f.get("name") or ""))
        if str(f.get("kind") or "leveraged") != "leveraged" or not stock or stock == fund:
            continue
        if med.get(fund, 0) < sl.MIN_DOLLAR_VOLUME:
            continue
        if stock not in best or med.get(fund, 0) > med.get(best[stock], 0):
            best[stock] = fund
    return dict(sorted(best.items()))


# ---- fetch ----


def connect(path: Path) -> sqlite3.Connection:
    conn = fbars.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS adjclose (symbol TEXT, date TEXT, adjclose REAL,
            PRIMARY KEY (symbol, date));
        CREATE TABLE IF NOT EXISTS instruments (symbol TEXT PRIMARY KEY, instrument_type TEXT,
            first_trade TEXT);
        """
    )
    return conn


def yahoo_extra(body: dict) -> tuple[list[tuple[str, float]], dict]:
    """([(session, adjusted close)], {instrument_type, first_trade}) from a daily chart response."""
    results = ((body or {}).get("chart") or {}).get("result") or []
    if not results:
        return [], {}
    r = results[0]
    adj = (((r.get("indicators") or {}).get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    rows = []
    for i, ts in enumerate(r.get("timestamp") or []):
        v = adj[i] if i < len(adj) else None
        if isinstance(v, (int, float)) and v > 0:
            rows.append(
                (fbars.stamp(datetime.fromtimestamp(int(ts), UTC).astimezone(fbars.NY), "1d"), float(v))
            )
    meta = r.get("meta") or {}
    first = meta.get("firstTradeDate")
    return rows, {
        "instrument_type": meta.get("instrumentType"),
        "first_trade": datetime.fromtimestamp(int(first), UTC).date().isoformat() if first else None,
    }


def fetch_yahoo(conn, symbols: list[str], today: str, force: bool) -> dict:
    counts: dict = {"ok": 0, "missing": [], "skipped": 0}
    done = {
        s
        for (s,) in conn.execute(
            "SELECT symbol FROM fetched WHERE source = 'yahoo' AND fetched_on = ?", (today,)
        )
    }
    try:
        for symbol in symbols:
            if symbol in done and not force:
                counts["skipped"] += 1
                continue
            body = fbars.yahoo_get(fbars.YAHOO_DAILY.format(symbol, int(time.time()))) or {}
            parsed = fbars.yahoo_parse(body, "1d")
            adj, meta = yahoo_extra(body)
            with conn:
                conn.execute("DELETE FROM adjclose WHERE symbol = ?", (symbol,))
                conn.executemany(
                    "INSERT OR REPLACE INTO adjclose VALUES (?, ?, ?)", [(symbol, *a) for a in adj]
                )
                conn.execute(
                    "INSERT OR REPLACE INTO instruments VALUES (?, ?, ?)",
                    (symbol, meta.get("instrument_type"), meta.get("first_trade")),
                )
                if parsed:
                    conn.execute("DELETE FROM splits WHERE symbol = ?", (symbol,))
                    conn.executemany(
                        "INSERT INTO splits (symbol, date, numerator, denominator) VALUES (?, ?, ?, ?)",
                        [(symbol, *s) for s in parsed["splits"]],
                    )
            if fbars.save(conn, symbol, "yahoo", "1d", parsed["rows"] if parsed else [], today):
                counts["ok"] += 1
            else:
                counts["missing"].append(symbol)
            time.sleep(fbars.PAUSE_S)
    except fbars.Blocked as e:
        counts["stopped"] = str(e)
    return counts


def fetch_treasury(force: bool, this_year: int) -> dict:
    out: dict = {"fetched": [], "kept": 0, "problems": []}
    folder = DIR / "treasury"
    folder.mkdir(parents=True, exist_ok=True)
    for year in range(TREASURY_FROM, this_year + 1):
        path = folder / f"{year}.csv"
        if path.exists() and year < this_year and not force:
            out["kept"] += 1
            continue
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
            f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
        )
        try:
            req = urllib.request.Request(url, headers=fbars.HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            out["problems"].append(f"{year}: {exc}")
            continue
        if not treasury.parse(text):
            out["problems"].append(f"{year}: no curve rows")
            continue
        path.write_text(text, encoding="utf-8")
        out["fetched"].append(year)
        time.sleep(fbars.PAUSE_S)
    return out


def fetch(force: bool) -> dict:
    conn26 = sqlite3.connect(sl.DIR / "bars.db")
    judged, universe = sl.names_in(conn26)
    funds = [f for f in judged if f in fbars.COUNTERPART and f in fbars.LEVERAGE]
    stocks = single_stock_funds()
    symbols = sorted(set(funds) | {fbars.COUNTERPART[f] for f in funds} | set(stocks) | set(stocks.values()))
    today = date.today().isoformat()
    conn = connect(DIR / "bars.db")
    out: dict = {"db": str(DIR / "bars.db"), "symbols": len(symbols)}
    out["yahoo"] = fetch_yahoo(conn, symbols, today, force)
    types = dict(conn.execute("SELECT symbol, instrument_type FROM instruments"))
    not_equity = {s: types.get(s) for s in stocks if types.get(s) != "EQUITY"}
    stocks = {s: f for s, f in stocks.items() if s not in not_equity}
    keep = sorted(set(funds) | {fbars.COUNTERPART[f] for f in funds} | set(stocks) | set(stocks.values()))
    out["broker"] = fbars.fetch_broker(conn, keep, [], today, force)
    out["treasury"] = fetch_treasury(force, date.today().year)
    names = {
        "funds": funds,
        "not_rebuilt": [f for f in judged if f not in funds],
        "single_stock": stocks,
        "not_equity": not_equity,
        "universe": universe,
    }
    (DIR / "names.json").write_text(json.dumps(names, indent=1), encoding="utf-8")
    out["names"] = {"funds": len(funds), "single_stock": len(stocks), "not_equity": len(not_equity)}
    return out


# ---- the rebuild ----


def load_rates(folder: Path | None = None) -> list[tuple[str, float]]:
    """[(session, 3-month bill %)] oldest first, from the stored yearly files."""
    rows = []
    for path in sorted((folder or DIR / "treasury").glob("*.csv")):
        rows.extend(
            (d.isoformat(), c["3m"]) for d, c in treasury.parse(path.read_text(encoding="utf-8")) if "3m" in c
        )
    return sorted(rows)


def rate_before(rates: list[tuple[str, float]], session: str) -> float:
    """The 3-month rate of the last curve strictly before `session`; the first curve's before any."""
    k = bisect.bisect_left(rates, (session,))
    return rates[k - 1][1] if k else rates[0][1]


def adjusted(conn: sqlite3.Connection, symbol: str) -> dict[str, float]:
    return dict(conn.execute("SELECT date, adjclose FROM adjclose WHERE symbol = ?", (symbol,)))


def rebuild(
    under: list,
    adj: dict[str, float],
    leverage: float,
    rates: list[tuple[str, float]],
    *,
    start: str = REBUILD_FROM,
    fee: float = FEE,
) -> tuple[list, int]:
    """(rebuilt daily bars, sessions held at the floor) from the 1x series' merged bars."""
    out: list = []
    floored = 0
    prev = None
    for b in under:
        if b.start < start:
            prev = b
            continue
        if not out:
            out.append(sl.Bar(b.start, BASE, BASE, BASE, BASE))
            prev = b
            continue
        a0, a1 = adj.get(prev.start), adj.get(b.start)
        total = a1 / a0 - 1 if a0 and a1 else b.c / prev.c - 1
        carry = (fee + (leverage - 1) * rate_before(rates, b.start) / 100) / sl.SESSIONS_A_YEAR
        factors = [1 + leverage * (x / prev.c - 1) for x in (b.o, b.h, b.l)] + [1 + leverage * total - carry]
        if min(factors) < FLOOR:
            floored += 1
        pc = out[-1].c
        o, h, low, c = (pc * max(f, FLOOR) for f in factors)
        out.append(sl.Bar(b.start, o, max(h, o, c), min(low, o, c), c))
        prev = b
    return out, floored


def validate(rebuilt: list, real: dict[str, float], since: str | None = None) -> dict:
    """The rebuilt series against the real fund's adjusted closes over the sessions both hold."""
    rc = {b.start: b.c for b in rebuilt}
    days = sorted(d for d in real if d in rc and (since is None or d >= since))
    pairs = [
        (d1, real[d1] / real[d0] - 1, rc[d1] / rc[d0] - 1) for d0, d1 in zip(days, days[1:], strict=False)
    ]
    out: dict = {"since": since, "sessions": len(pairs), "first": days[0] if days else None}
    if len(pairs) < MIN_YEAR_SESSIONS:
        return {**out, "ok": False, "why": "too short"}
    xs, ys = [p[1] for p in pairs], [p[2] for p in pairs]
    years: dict[str, list] = {}
    for d, x, y in pairs:
        years.setdefault(d[:4], []).append((x, y))
    diffs = {}
    for yr, v in years.items():
        if len(v) >= MIN_YEAR_SESSIONS:
            diffs[yr] = 100.0 * (_compound(y for _, y in v) - _compound(x for x, _ in v))
    corr = statistics.correlation(xs, ys) if statistics.pstdev(xs) and statistics.pstdev(ys) else 0.0
    mean_diff = statistics.fmean(abs(d) for d in diffs.values()) if diffs else None
    ok = corr >= MIN_CORR and mean_diff is not None and mean_diff <= MAX_YEAR_DIFF
    return {
        **out,
        "corr": corr,
        "mean_year_diff": mean_diff,
        "year_diff": diffs,
        "slope": statistics.linear_regression(ys, xs).slope if statistics.pstdev(ys) else None,
        "richer": _compound(ys) > _compound(xs),
        "ok": ok,
        "why": None if ok else ("correlation" if corr < MIN_CORR else "annual difference"),
    }


def _compound(rets) -> float:
    total = 1.0
    for r in rets:
        total *= 1 + r
    return total - 1


def window_from(rebuilt: list, start: str, end: str) -> tuple[str, str] | None:
    """(start, end) clipped to start no earlier than the rebuilt series' HISTORY + 1th session."""
    if len(rebuilt) <= HISTORY:
        return None
    s = max(start, rebuilt[HISTORY].start)
    return (s, end) if s <= end else None


# ---- the verdict ----


def judge(per_name: dict[str, dict], item26: dict[str, str]) -> dict:
    """per_name: {name: {"arms": {arm: {window: measure}}}}; item26: {arm: item 26's verdict}."""
    out = {}
    for arm in sl.ARMS:
        rows = [v["arms"][arm] for v in per_name.values() if "holdout" in v["arms"].get(arm, {})]
        h = sl.stat([r["holdout"]["edge"] for r in rows])
        lots = sum(r["holdout"]["closed"] for r in rows)
        stress = {w: sl.stat([r[w]["edge"] for r in rows if w in r]) for w in STRESS}
        v = {"item26": item26.get(arm), "holdout": h, "lots": lots, "stress": stress}
        if item26.get(arm) != "pass":
            v["verdict"] = "reported only"
        elif h["n"] < sl.MIN_NAMES or lots < sl.MIN_LOTS:
            v["verdict"] = "no verdict"
        else:
            v["verdict"] = "pass" if h["mean"] > 0 and h["positive_share"] > 0.5 else "fail"
        out[arm] = v
    return out


def off_default(longest_days: int, drawdown_pct: float) -> bool:
    return longest_days > LONG_HOLD_DAYS or drawdown_pct > MAX_DRAWDOWN_PCT


def _measures(rebuilt: list, under: list, windows: dict, seed: int) -> dict:
    arms = {
        arm: {w: sl.measure(rebuilt, s, e, arm, sl.COST, seed + a) for w, (s, e) in windows.items()}
        for a, arm in enumerate(sl.ARMS)
    }
    holds = {}
    for arm in (s27.BASE, *s27.ARMS):
        cap = s27.SPEC[arm]["cap"]
        holds[arm] = {
            w: s27.describe(s27.simulate(rebuilt, s, e, arm, under=under), cap, s, e)
            for w, (s, e) in windows.items()
        }
    return {"arms": arms, "item27": holds}


def study(only: list[str] | None) -> dict:
    conn = sqlite3.connect(DIR / "bars.db")
    names = json.loads((DIR / "names.json").read_text(encoding="utf-8"))
    r26 = json.loads((sl.DIR / "results.json").read_text(encoding="utf-8"))
    item26 = {a: v["verdict"] for a, v in r26["verdict"].items()}
    rates = load_rates()
    funds = [f for f in names["funds"] if not only or f in only]
    stocks = {s: f for s, f in names["single_stock"].items() if not only or s in only or f in only}
    per_fund: dict[str, dict] = {}
    rejected: dict[str, dict] = {}
    for k, fund in enumerate(funds):
        one_x = fbars.COUNTERPART[fund]
        under = sl.daily_bars(conn, one_x)
        rebuilt, floored = rebuild(under, adjusted(conn, one_x), fbars.LEVERAGE[fund], rates)
        check = validate(rebuilt, adjusted(conn, fund), LEVERAGE_FROM.get(fund))
        check["floored"] = floored
        if not check["ok"]:
            rejected[fund] = check
            continue
        windows = {"holdout": window_from(rebuilt, REBUILD_FROM, HOLDOUT_END)}
        windows.update({w: window_from(rebuilt, s, e) for w, (s, e) in STRESS.items()})
        windows = {w: v for w, v in windows.items() if v}
        row = _measures(rebuilt, under, windows, SEED * 1000 + k * 37)
        full = window_from(rebuilt, REBUILD_FROM, FULL_END)
        row["full"] = {"span": full}
        for arm in sl.ARMS:
            rule, cap = sl.arm_parts(arm)
            run = sl.simulate(rebuilt, *full, rule, cap)
            dd = 100.0 * run.max_drawdown / (cap * sl.TRANCHE)
            row["full"][arm] = {
                "pct_year": sl.pct_year(run, cap),
                "longest_days": run.longest_days,
                "max_drawdown_pct": dd,
                "open_at_end": run.open_at_end,
                "off_default": off_default(run.longest_days, dd),
            }
        row["full"]["item27"] = {
            arm: s27.describe(s27.simulate(rebuilt, *full, arm, under=under), s27.SPEC[arm]["cap"], *full)
            for arm in (s27.BASE, *s27.ARMS)
        }
        per_fund[fund] = {
            "counterpart": one_x,
            "leverage": fbars.LEVERAGE[fund],
            "windows": windows,
            "validation": check,
            **row,
        }
        print(f"{fund} done ({k + 1}/{len(funds)})", file=sys.stderr, flush=True)
    per_stock: dict[str, dict] = {}
    stock_rejected: dict[str, dict] = {}
    for k, (stock, fund) in enumerate(stocks.items()):
        under = sl.daily_bars(conn, stock)
        rebuilt, floored = rebuild(under, adjusted(conn, stock), SINGLE_LEVERAGE, rates)
        check = validate(rebuilt, adjusted(conn, fund))
        check["floored"] = floored
        if not check["ok"]:
            stock_rejected[stock] = {"fund": fund, **check}
            continue
        span = window_from(rebuilt, *SINGLE)
        if not span:
            stock_rejected[stock] = {"fund": fund, "ok": False, "why": "no span"}
            continue
        row = _measures(rebuilt, under, {"span": span}, SEED * 1000 + 500_000 + k * 37)
        row["off_default"] = {
            arm: off_default(m["span"]["longest_days"], m["span"]["max_drawdown_pct"])
            or m["span"]["edge"] <= 0
            for arm, m in row["arms"].items()
        }
        per_stock[stock] = {"fund": fund, "span": span, "validation": check, **row}
        print(f"{stock} ({fund}) done ({k + 1}/{len(stocks)})", file=sys.stderr, flush=True)
    return {
        "item": 28,
        "constants": {
            "fee": FEE,
            "floor": FLOOR,
            "rebuild_from": REBUILD_FROM,
            "single_leverage": SINGLE_LEVERAGE,
        },
        "names": names,
        "names_match_item26": sorted(r26.get("names", [])) == sorted(names["funds"] + names["not_rebuilt"]),
        "verdict": judge(per_fund, item26),
        "default_list": {
            arm: {
                "funds": sorted(f for f, v in per_fund.items() if not v["full"][arm]["off_default"]),
                "stocks": sorted(s for s, v in per_stock.items() if not v["off_default"][arm]),
            }
            for arm in sl.ARMS
        },
        "single_stock_summary": {
            arm: sl.stat([v["arms"][arm]["span"]["edge"] for v in per_stock.values()]) for arm in sl.ARMS
        },
        "rejected": {"funds": rejected, "stocks": stock_rejected},
        "per_fund": per_fund,
        "per_stock": per_stock,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("fetch", "study"))
    ap.add_argument("--force", action="store_true", help="fetch: refetch what was fetched today")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--names", default=None, help="comma-separated subset (a trial run, not a verdict)")
    args = ap.parse_args(argv)
    DIR.mkdir(parents=True, exist_ok=True)
    if args.step == "fetch":
        out = fetch(args.force)
        print(json.dumps(out, default=str))
        return 1 if "stopped" in out["yahoo"] else 0
    result = study(args.names.split(",") if args.names else None)
    path = args.json or DIR / "results.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    summary = {a: v["verdict"] for a, v in result["verdict"].items()}
    print(
        json.dumps(
            {
                "written": str(path),
                "summary": summary,
                "rejected": {k: sorted(v) for k, v in result["rejected"].items()},
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
