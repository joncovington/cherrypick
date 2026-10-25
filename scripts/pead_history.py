"""Post-earnings drift over the earnings events in the local Dolt clones: is there a drift to sell into?

    python scripts/pead_history.py [--start 2020-01-01] [--end 2026-10-02] [--json FILE] [--limit N]
        [--symbols A,B]

Step 1 of hypothesis 1 in docs/edge-hypotheses-plan.md. The trade under test (a ~20-delta put credit
spread sold under the pre-report price after a stock gaps up and holds) can only earn if stocks that
gap up on earnings keep drifting the same way, beyond the market, for the weeks the spread is held.
That is post-earnings announcement drift, one of the best-documented anomalies. This measures the
drift itself, from daily bars alone, before any option is priced. Read-only throughout.

**Events.** Dolt `earnings.earnings_calendar` rows timed before or after the open, for symbols the
chain clone lists (`emfly_history.option_symbols`): an optionable name, as the trade needs.

**Dates,** in the symbol's own sessions: P is the last close before the report (the report day for
after the close, the day before for before the open); R is the reaction day (the next session for
after the close, the report day for before the open). `react` is close(R) / close(P) - 1 and `gap`
open(R) / close(P) - 1. Entry is the close of R: what a trader acting after the report sees.

**Forward returns** from close(R) to close(R + h) for h in 5, 8, 14, 21 and 30 sessions; abnormal is
that minus SPY over the same two dates. `above_pre_h` is whether close(R + h) is above close(P): the
spread's short strike sits under the pre-report price, so this is the trade's winning side.
`touch_pre_h` is whether any close from R + 1 to R + h fell below close(P): the stop's side.

**Screens** (each known at close(R)):
- `liquid`: mean dollar volume over the 20 sessions to P at least `--min-dollar-volume` ($100M),
  close(P) at least `--min-price` ($10). The EM-fly study's stand-in for "very liquid".
- `uptrend`: close(P) above its 50-session mean, and the 50 above the 200 (adjusted closes).
- `held`: the symbol's own earlier events (R + 21 at or before P, so already known) with a positive
  reaction held it, i.e. close(R + 21) at or above close(R), on at least 75% of the last 8 such, with
  at least 4 of them. The video's "8 past reports show the stock holding its gap".

**Groups:** every event; by reaction bucket; `rule` = liquid, uptrend, 0 < react < 10%, held (the
video's screen as close as daily bars allow); `rule_no_hist` = the same without `held` (does the
history screen add anything?); `gap_up` = liquid and react > 0 (the plain anomaly).

**Statistics.** Events cluster in earnings season and share a market, so the t-statistic is taken
over calendar-week means of the abnormal return (events whose R falls in the same ISO week averaged
first), not over events.

**Declared pass test (2026-10-17, before any run).** Step 2 (pricing the put spread from the chains)
is built only if the `rule` group's mean 21-session abnormal return is positive with a week-clustered
t of at least 2.0, positive in at least two-thirds of the calendar years holding 30 or more events,
and positive in both halves (R before 2023, and from 2023). Otherwise the hypothesis stops here, and
the result is recorded in docs/history-studies-2026-10-c.md either way.

Deterministic: the same clones and arguments give the same tables.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emfly_history as _em  # noqa: E402 -- events and the optionable universe, shared not copied

HORIZONS = (5, 8, 14, 21, 30)
HEAD_H = 21
HELD_LOOKBACK = 8
HELD_MIN_EVENTS = 4
HELD_MIN_SHARE = 0.75
BUCKETS = (
    (-1.0, -0.10),
    (-0.10, -0.05),
    (-0.05, -0.02),
    (-0.02, 0.0),
    (0.0, 0.02),
    (0.02, 0.05),
    (0.05, 0.10),
    (0.10, 9.0),
)
SPLIT = date(2023, 1, 1)


# --------------------------------------------------------------------------- pure pieces


def sma(xs: list[float], i: int, n: int) -> float | None:
    """Mean of xs[i - n + 1 .. i], or None without n values."""
    if i + 1 < n:
        return None
    return sum(xs[i - n + 1 : i + 1]) / n


def locate(dates: list[date], report: date, amc: bool) -> tuple[int, int] | None:
    """(P, R) indices into a symbol's sessions for a report on `report`, or None if either is missing.
    After the close: P is the report day itself, R the next session. Before the open: P is the last
    session before the report day, R the report day."""
    import bisect

    if amc:
        i = bisect.bisect_left(dates, report)
        if i >= len(dates) or dates[i] != report or i + 1 >= len(dates):
            return None
        return i, i + 1
    i = bisect.bisect_left(dates, report)
    if i >= len(dates) or dates[i] != report or i == 0:
        return None
    return i - 1, i


def event_row(s: dict, p: int, r: int, spy: dict[date, float], args) -> dict | None:
    """Everything known about one event from the symbol's series `s` (dates, open, close, dollar
    volume, all adjusted) and SPY closes by date. Forward fields are None past the data's end."""
    close, opn, dates = s["close"], s["open"], s["dates"]
    if close[p] <= 0 or close[r] <= 0:
        return None
    dv = s["dollar_volume"][max(0, p - 19) : p + 1]
    ma50, ma200 = sma(close, p, 50), sma(close, p, 200)
    row = {
        "react": close[r] / close[p] - 1.0,
        "gap": opn[r] / close[p] - 1.0 if opn[r] else None,
        "r_date": dates[r].isoformat(),
        "liquid": bool(dv) and sum(dv) / len(dv) >= args.min_dollar_volume and close[p] >= args.min_price,
        "uptrend": ma50 is not None and ma200 is not None and close[p] > ma50 > ma200,
    }
    spy_r = spy.get(dates[r])
    for h in HORIZONS:
        j = r + h
        if j >= len(close):
            row[f"fwd_{h}"] = row[f"abn_{h}"] = row[f"above_pre_{h}"] = row[f"touch_pre_{h}"] = None
            continue
        fwd = close[j] / close[r] - 1.0
        spy_j = spy.get(dates[j])
        row[f"fwd_{h}"] = fwd
        row[f"abn_{h}"] = fwd - (spy_j / spy_r - 1.0) if spy_r and spy_j else None
        row[f"above_pre_{h}"] = close[j] > close[p]
        row[f"touch_pre_{h}"] = min(close[r + 1 : j + 1]) < close[p]
    return row


def held_screen(history: list[dict], p_date: date) -> bool | None:
    """The 'held its gap' screen from the symbol's earlier events: among those with a positive
    reaction whose R + 21 close is at or before `p_date`, the last 8; None with fewer than 4."""
    known = [
        e for e in history if e["react"] > 0 and e.get("known_on") is not None and e["known_on"] <= p_date
    ]
    last = known[-HELD_LOOKBACK:]
    if len(last) < HELD_MIN_EVENTS:
        return None
    held = sum(1 for e in last if e["fwd_21"] is not None and e["fwd_21"] >= 0)
    return held / len(last) >= HELD_MIN_SHARE


def bucket_of(react: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= react < hi:
            return f"{lo:+.0%}..{hi:+.0%}" if hi < 9 else f">= {lo:+.0%}"
    return "?"


def clustered(rows: list[dict], key: str) -> dict:
    """Mean of `key` over events, and a t-statistic over ISO-week means (events in a week first
    averaged), so a crowded earnings season counts as the few independent draws it is."""
    vals = [(r["r_date"], r[key]) for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0}
    weeks: dict[tuple, list[float]] = defaultdict(list)
    for d, v in vals:
        weeks[date.fromisoformat(d).isocalendar()[:2]].append(v)
    wm = [sum(v) / len(v) for v in weeks.values()]
    out = {
        "n": len(vals),
        "weeks": len(wm),
        "mean": statistics.fmean(v for _, v in vals),
        "median": statistics.median(v for _, v in vals),
    }
    if len(wm) >= 3:
        sd = statistics.stdev(wm)
        out["t_week"] = statistics.fmean(wm) / (sd / math.sqrt(len(wm))) if sd > 0 else None
    return out


def rate(rows: list[dict], key: str) -> float | None:
    xs = [r[key] for r in rows if r.get(key) is not None]
    return sum(1 for x in xs if x) / len(xs) if xs else None


def group_stats(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    for h in HORIZONS:
        out[f"abn_{h}"] = clustered(rows, f"abn_{h}")
        out[f"above_pre_{h}"] = rate(rows, f"above_pre_{h}")
        out[f"touch_pre_{h}"] = rate(rows, f"touch_pre_{h}")
        out[f"fwd_pos_{h}"] = rate(
            [{"x": r[f"fwd_{h}"] > 0} if r.get(f"fwd_{h}") is not None else {} for r in rows], "x"
        )
    return out


def by_year(rows: list[dict], key: str) -> dict[int, dict]:
    years: dict[int, list] = defaultdict(list)
    for r in rows:
        years[int(r["r_date"][:4])].append(r)
    return {y: clustered(v, key) for y, v in sorted(years.items())}


def verdict(rule_rows: list[dict]) -> dict:
    """The declared pass test on the `rule` group's 21-session abnormal return."""
    key = f"abn_{HEAD_H}"
    head = clustered(rule_rows, key)
    years = {y: s for y, s in by_year(rule_rows, key).items() if s.get("n", 0) >= 30}
    early = clustered([r for r in rule_rows if date.fromisoformat(r["r_date"]) < SPLIT], key)
    late = clustered([r for r in rule_rows if date.fromisoformat(r["r_date"]) >= SPLIT], key)
    pos_years = sum(1 for s in years.values() if s["mean"] > 0)
    checks = {
        "mean_positive": head.get("n", 0) > 0 and head["mean"] > 0,
        "t_at_least_2": (head.get("t_week") or 0) >= 2.0,
        "two_thirds_of_years": bool(years) and pos_years >= math.ceil(2 * len(years) / 3),
        "both_halves": early.get("n", 0) > 0
        and late.get("n", 0) > 0
        and early["mean"] > 0
        and late["mean"] > 0,
    }
    return {
        "head": head,
        "years_counted": len(years),
        "years_positive": pos_years,
        "early": early,
        "late": late,
        "checks": checks,
        "pass": all(checks.values()),
    }


# --------------------------------------------------------------------------- data


def load_series(conn, symbol: str) -> dict | None:
    """Adjusted sessions for one symbol: dates, open, close, dollar volume (adjusted close x adjusted
    volume, which a split leaves unchanged)."""
    from cherrypick.technicals import store as _store

    bars = _store.adjusted_bars(conn, symbol)
    if not bars:
        return None
    out = {"dates": [], "open": [], "close": [], "dollar_volume": []}
    for b in bars:
        if not b.close:
            continue
        out["dates"].append(date.fromisoformat(b.date))
        out["open"].append(b.open or 0.0)
        out["close"].append(b.close)
        out["dollar_volume"].append(b.close * (b.volume or 0.0))
    return out


def build(args) -> list[dict]:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    optionable = _em.option_symbols(args.host, args.port, start, end)
    events = [e for e in _em.load_events(args.host, args.port, start, end) if e["symbol"] in optionable]
    if args.symbols:
        keep = set(args.symbols.split(","))
        events = [e for e in events if e["symbol"] in keep]
    by_sym: dict[str, list] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)
    syms = sorted(by_sym)[: args.limit] if args.limit else sorted(by_sym)
    print(
        f"events: {sum(len(by_sym[s]) for s in syms):,} over {len(syms):,} optionable symbols",
        file=sys.stderr,
    )

    conn = sqlite3.connect(f"file:{_em.HISTORY_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    spy_s = load_series(conn, "SPY")
    spy = dict(zip(spy_s["dates"], spy_s["close"], strict=True))
    rows: list[dict] = []
    drops: dict[str, int] = defaultdict(int)
    for n, sym in enumerate(syms, 1):
        s = load_series(conn, sym)
        if s is None or len(s["dates"]) < 60:
            drops["no_bars"] += len(by_sym[sym])
            continue
        history: list[dict] = []
        for ev in sorted(by_sym[sym], key=lambda e: e["date"]):
            loc = locate(s["dates"], ev["date"], ev["amc"])
            if loc is None:
                drops["no_session"] += 1
                continue
            p, r = loc
            row = event_row(s, p, r, spy, args)
            if row is None:
                drops["bad_price"] += 1
                continue
            row["symbol"] = sym
            row["held"] = held_screen(history, s["dates"][p])
            row["known_on"] = s["dates"][r + 21] if r + 21 < len(s["dates"]) else None
            history.append(row)
            rows.append(row)
        if n % 250 == 0:
            print(f"  {n:,}/{len(syms):,} symbols, {len(rows):,} events", file=sys.stderr)
    conn.close()
    print(f"built {len(rows):,} events; dropped {dict(drops)}", file=sys.stderr)
    for r in rows:
        r["known_on"] = r["known_on"].isoformat() if r["known_on"] else None
    return rows


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    liquid = [r for r in rows if r["liquid"]]
    out = {
        "all": rows,
        "liquid": liquid,
        "gap_up": [r for r in liquid if r["react"] > 0],
        "gap_down": [r for r in liquid if r["react"] < 0],
        "rule_no_hist": [r for r in liquid if r["uptrend"] and 0 < r["react"] < 0.10],
        "rule": [r for r in liquid if r["uptrend"] and 0 < r["react"] < 0.10 and r["held"] is True],
        "rule_failed_hist": [
            r for r in liquid if r["uptrend"] and 0 < r["react"] < 0.10 and r["held"] is False
        ],
    }
    for r in liquid:
        out.setdefault("bucket " + bucket_of(r["react"]), []).append(r)
    return out


def lost_to_data_end(rows: list[dict]) -> dict[str, int]:
    """Events per horizon with no forward return because the name's bars end first (B11): names that
    were acquired or failed leave the longer horizons, a lean toward survivors, so it is counted."""
    return {f"fwd_{h}": sum(1 for r in rows if r.get(f"fwd_{h}") is None) for h in HORIZONS}


def analyse(rows: list[dict]) -> dict:
    g = groups(rows)
    return {
        "lost_to_data_end": lost_to_data_end(rows),
        "groups": {k: group_stats(v) for k, v in g.items()},
        "rule_by_year": by_year(g["rule"], f"abn_{HEAD_H}"),
        "gap_up_by_year": by_year(g["gap_up"], f"abn_{HEAD_H}"),
        "verdict": verdict(g["rule"]),
    }


def _pct(x) -> str:
    return "    -" if x is None else f"{x * 100:+6.2f}%"


def print_report(rep: dict) -> None:
    print("\nAbnormal return (vs SPY) from the reaction close; t over ISO-week means\n")
    print(f"{'group':<26}{'n':>7}  " + "  ".join(f"{'abn_' + str(h):>16}" for h in HORIZONS))
    for name, st in rep["groups"].items():
        cells = []
        for h in HORIZONS:
            a = st[f"abn_{h}"]
            t = a.get("t_week")
            cells.append(
                f"{_pct(a.get('mean'))} t{t:+5.1f}" if t is not None else f"{_pct(a.get('mean'))}      "
            )
        print(f"{name:<26}{st['n']:>7}  " + "  ".join(f"{c:>16}" for c in cells))
    print("\nClose above the pre-report close at h / any close below it by h (the spread's two sides)\n")
    print(f"{'group':<26}" + "".join(f"{'above_' + str(h):>10}{'touch_' + str(h):>10}" for h in (8, 21)))
    for name, st in rep["groups"].items():
        print(
            f"{name:<26}"
            + "".join(f"{_pct(st[f'above_pre_{h}']):>10}{_pct(st[f'touch_pre_{h}']):>10}" for h in (8, 21))
        )
    print("\n`rule` 21-session abnormal return by year")
    for y, s in rep["rule_by_year"].items():
        print(f"  {y}  n={s.get('n', 0):>5}  mean {_pct(s.get('mean'))}  t {s.get('t_week') or 0:+.1f}")
    v = rep["verdict"]
    print("\nDeclared pass test:", "PASS" if v["pass"] else "FAIL", json.dumps(v["checks"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-10-02")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--min-dollar-volume", type=float, default=100e6)
    ap.add_argument("--min-price", type=float, default=10.0)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbols", help="comma-separated, for a quick run")
    ap.add_argument("--from-json", type=Path, help="re-analyse the events a previous --json run saved")
    args = ap.parse_args(argv)
    rows = json.loads(args.from_json.read_text(encoding="utf-8"))["events"] if args.from_json else build(args)
    rep = analyse(rows)
    print_report(rep)
    if args.json:
        args.json.write_text(json.dumps({"report": rep, "events": rows}, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
