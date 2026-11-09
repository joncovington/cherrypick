"""Item 54, does the front/back IV ratio predict a time spread's profit? (research/edge-hypotheses-plan.md)

    python packages/backtest/scripts/time_spread_ratio_item54.py plan [--out-dir DIR]      # universe,
    entries, requests python packages/backtest/scripts/time_spread_ratio_item54.py design [--out-dir DIR]    #
    2016-2020 python packages/backtest/scripts/time_spread_ratio_item54.py holdout [--out-dir DIR]   #
    2021-2026, read whatever

The time-spread scanner orders its calendars and diagonals by the
front week's IV over the back month's, on the hypothesis that a rich front week is a premium the stock
does not then earn. It also builds a "loses with the stock unchanged" verdict per structure. Neither has
been tested. This study builds the scanner's own structures, with its own builder
(`cherrypick.manage.timespreads.structures`), on past chains, holds each to the short's expiry, and
asks two things per structure (put and call calendar at the money, put and call diagonal):

- **ratio** (read from the at-the-money put calendar's legs, the IVs solved from their mids; an entry whose
  at-the-money calendar fails its gates has no ratio and no trade, counted): does a higher ratio mean a
  better result after costs? (Spearman rank correlation of the
  ratio with the net, one-sided above zero), and is the top third by ratio profitable after costs on
  its own? (mean net above zero, sessions as clusters);
- **verdict:** do the structures the builder calls "loses unchanged" do worse than the rest?

**Data.** End-of-day chains from the licensed store (`eod_chains.db`, fetched by this plan's request
file through the research queue's `fetch-chains`), the stock's daily closes from the technicals history
store. The universe is the 40 names the liquidity verdict calls tradable with the highest median
dollar volume TODAY: a hindsight label, declared (B11). Entries every second Monday (the next trading
day after a holiday), 2016-01-04 to 2026-09-28; the front is the weekly expiry 5 to 16 days out nearest
11 days, the back the standard monthly 30 to 75 days out nearest the middle. A name with no weekly
listed then simply has no trade that session (counted).

**Pricing.** Entry at each leg's own mid with the suite's slippage (`fees.slippage_dollars`) and per-leg
fees (`fees.ic_open_fee` for the legs opened, `fees.ic_close_fee` at the exit), the IV solved from the
mid at the stock's close (Black-Scholes, the scanner's rate). At the short's expiry the short is worth
its intrinsic on that day's close (bought back there, early assignment not modelled: a declared limit)
and the long is sold at the stored back chain's mid that session less the same slippage. The store
carries no open interest, so the leg bars' open-interest test is not applied; the 15%-of-debit cost
gate and two-sided quotes are. A split inside the trade drops it. The net is in percent of the debit
(a time spread's whole risk).

**Rule.** 12 tests (4 structures x ratio-rank, top-third, verdict), Holm at 0.05 on the one-sided p in
the design; a test passes the design only above zero there, at 1.5x fees and a quarter of the spread
too, and with its best five sessions removed. A design pass is confirmed only by the holdout's same
sign at p < 0.05. Under 40 trades in a test's window is no verdict. A confirmed ratio test makes the
ratio order a ranking; anything less keeps it labelled untested. Everything written goes under the
backtest results dir (results/research/item54/), never the repo.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees
from cherrypick.core import stats as core_stats

NAMES = 40
START, END = date(2016, 1, 4), date(2026, 9, 28)
CADENCE_DAYS = 14
FRONT_DAYS, FRONT_TARGET = (5, 16), 11
BACK_DAYS = (30, 75)
DESIGN = (date(2016, 1, 1), date(2020, 12, 31))
HOLDOUT = (date(2021, 1, 1), date(2026, 12, 31))
STRUCTURES = ("put calendar", "call calendar", "put diagonal", "call diagonal")
TESTS = ("ratio-rank", "top-third", "verdict")
LONG_DELTA_TARGET = 0.65
MIN_TRADES = 40
ALPHA = 0.05
BEST_REMOVED = 5
STRESS_FEES = 1.5
STRESS_COSTS = {"tastytrade_costs": {"slippage_frac_of_spread": 0.25}}
OPEN_INTEREST_UNKNOWN = 10**9  # the store has none: the leg bars' OI test is not applied (declared)
#: The replay's corrections, oldest first (`orchestrator.studyversion`). A result written before the
#: last of them is stale on the studies board.
REPLAY_CORRECTIONS = (
    {"on": "2026-11-07", "what": "the store's CALL and PUT are read as the builder's C and P, for the "
     "chains and for the exit quotes", "why": "the builder keeps only options whose right is C or P, so "
     "no stored option reached it and every entry dropped with no at-the-money calendar; the first run "
     "held no trades", "decided": "user, 2026-11-07: fix the data gaps behind items 47 and 54",
     "before_read": True},
)  # fmt: skip


# ------------------------------------------------------------------------------------------------
# Pure.


def monthly(y: int, m: int) -> date:
    first = date(y, m, 1)
    fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)


def friday_expiry(day: date) -> date:
    """The week's Friday expiry, the trading day before it when the Friday is a holiday."""
    fri = day + timedelta(days=(4 - day.weekday()) % 7)
    return fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)


def entry_sessions(start: date = START, end: date = END, every: int = CADENCE_DAYS) -> list[date]:
    """Every `every` days from `start`, moved to the next trading day when it is a holiday."""
    out, day = [], start
    while day <= end:
        d = day
        while not cal.is_trading_day(d):
            d += timedelta(days=1)
        out.append(d)
        day += timedelta(days=every)
    return out


def front_expiry(entry: date) -> date | None:
    """The weekly expiry FRONT_DAYS out nearest FRONT_TARGET days."""
    cands = {friday_expiry(entry + timedelta(days=k)) for k in range(FRONT_DAYS[0], FRONT_DAYS[1] + 1)}
    cands = [c for c in cands if FRONT_DAYS[0] <= (c - entry).days <= FRONT_DAYS[1]]
    return min(cands, key=lambda c: (abs((c - entry).days - FRONT_TARGET), c)) if cands else None


def back_expiry(entry: date) -> date | None:
    """The standard monthly BACK_DAYS out nearest the middle."""
    lo, hi = BACK_DAYS
    mid = (lo + hi) / 2
    cands = []
    y, m = entry.year, entry.month
    for _ in range(4):
        e = monthly(y, m)
        if lo <= (e - entry).days <= hi:
            cands.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return min(cands, key=lambda e: (abs((e - entry).days - mid), e)) if cands else None


def requests_for(names: list[str], entries: list[date]) -> list[dict]:
    """The chains a trade needs: the front and back at entry, and the back on the front's expiry."""
    out, seen = [], set()
    for sym in names:
        for e in entries:
            f, b = front_expiry(e), back_expiry(e)
            if f is None or b is None:
                continue
            for session, exp in ((e, f), (e, b), (f, b)):
                key = (sym, session.isoformat(), exp.isoformat())
                if key not in seen:
                    seen.add(key)
                    out.append({"symbol": sym, "session": key[1], "expiration": key[2]})
    return out


def window_of(entry: date) -> str | None:
    if DESIGN[0] <= entry <= DESIGN[1]:
        return "design"
    if HOLDOUT[0] <= entry <= HOLDOUT[1]:
        return "holdout"
    return None


def right_of(stored: str) -> str:
    """The store's right (CALL, PUT) as the scanner's builder spells it (C, P)."""
    return stored[:1].upper()


def view(rows: list[tuple], dte: int) -> dict:
    """A stored chain as the scanner's builder reads one expiry."""
    return {
        "dte": dte,
        "options": [
            {
                "strike": float(k),
                "right": right_of(r),
                "bid": b,
                "ask": a,
                "volume": 0.0,
                "open_interest": OPEN_INTEREST_UNKNOWN,
            }
            for k, r, b, a in rows
            if b is not None and a is not None
        ],
    }


def pick(rows: list[dict], kind: str, spot: float) -> dict | None:
    """The one structure of a kind the study holds: a calendar at the strike nearest the stock, a
    diagonal whose long is nearest LONG_DELTA_TARGET (the builder already chose its short)."""
    of = [r for r in rows if r["kind"] == kind]
    if not of:
        return None
    if "calendar" in kind:
        return min(of, key=lambda r: (abs(r["long"]["strike"] - spot), r["long"]["strike"]))
    return min(of, key=lambda r: (abs(abs(r["long_delta"]) - LONG_DELTA_TARGET), r["long"]["strike"]))


def intrinsic(spot: float, strike: float, right: str) -> float:
    return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)


def settle(row: dict, s_exp: float, long_quote: tuple | None, cfg: dict, symbol: str) -> dict | None:
    """The trade's net in dollars and in percent of the debit, at the short's expiry. `long_quote` is
    the long's (bid, ask) on that session; None when it is not two-sided (the trade is dropped)."""
    if long_quote is None or long_quote[0] is None or long_quote[1] is None or long_quote[1] <= 0:
        return None
    lb, la = long_quote
    short = row["short"]
    short_value = intrinsic(s_exp, short["strike"], short["right"])
    long_mid = (lb + la) / 2.0
    debit = row["entry_debit"] * 100.0
    gross = (long_mid - short_value) * 100.0 - debit
    open_fee = fees.ic_open_fee(symbol, 1, legs=2, sell_legs=1, ndigits=4)
    close_legs = 2 if short_value > 0 else 1
    close_fee = fees.ic_close_fee(symbol, 1, legs=close_legs, sell_legs=1, ndigits=4)
    entry_q = [{"bid": q[0], "ask": q[1]} for q in row["entry_quotes"]]
    exit_q = [{"bid": lb, "ask": la}]

    def net_at(c: dict, fee_mult: float) -> float:
        slip = fees.slippage_dollars(entry_q, 1, c) + fees.slippage_dollars(exit_q, 1, c)
        return gross - slip - fee_mult * (open_fee + close_fee)

    net = net_at(cfg, 1.0)
    stress = net_at({**cfg, **STRESS_COSTS}, STRESS_FEES)
    return {
        "net": round(net, 2),
        "net_pct": round(100.0 * net / debit, 3) if debit > 0 else None,
        "stress_pct": round(100.0 * stress / debit, 3) if debit > 0 else None,
    }


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def spearman_p(rho: float | None, n: int) -> float:
    """One-sided p for rho above zero (t approximation)."""
    if rho is None or n < 4 or abs(rho) >= 1:
        return 1.0 if rho is None or rho <= 0 else 0.0
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    return 1.0 - core_stats.t_cdf(t, n - 2)


def cluster_mean_p(values: list[float], clusters: list[str]) -> tuple[float | None, float]:
    """(mean, one-sided p above zero) with sessions as clusters: the t on the cluster means."""
    by: dict[str, list[float]] = defaultdict(list)
    for v, c in zip(values, clusters, strict=True):
        by[c].append(v)
    means = [statistics.fmean(v) for v in by.values()]
    if len(means) < 3:
        return (statistics.fmean(values) if values else None), 1.0
    m, sd = statistics.fmean(means), statistics.stdev(means)
    if sd == 0:
        return m, 0.0 if m > 0 else 1.0
    t = m / (sd / math.sqrt(len(means)))
    return statistics.fmean(values), 1.0 - core_stats.t_cdf(t, len(means) - 1)


def diff_p(a: list[float], b: list[float]) -> tuple[float | None, float]:
    """(mean(a) - mean(b), one-sided p above zero), Welch."""
    if len(a) < 3 or len(b) < 3:
        return None, 1.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return ma - mb, 0.0 if ma > mb else 1.0
    t = (ma - mb) / se
    dof = (va + vb) ** 2 / ((va**2) / (len(a) - 1) + (vb**2) / (len(b) - 1))
    return ma - mb, 1.0 - core_stats.t_cdf(t, dof)


def without_best(trades: list[dict], key: str, n: int = BEST_REMOVED) -> list[dict]:
    """The trades less those of the `n` sessions with the best mean `key`."""
    by: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by[t["entry"]].append(t[key])
    best = set(sorted(by, key=lambda s: -statistics.fmean(by[s]))[:n])
    return [t for t in trades if t["entry"] not in best]


def reading(trades: list[dict], test: str, key: str = "net_pct") -> dict:
    """One test's estimate and one-sided p on `trades` (one structure, one window)."""
    if test == "ratio-rank":
        rho = spearman([t["ratio"] for t in trades], [t[key] for t in trades])
        return {"n": len(trades), "estimate": rho, "p": spearman_p(rho, len(trades))}
    if test == "top-third":
        cut = sorted(t["ratio"] for t in trades)[(2 * len(trades)) // 3] if trades else None
        top = [t for t in trades if cut is not None and t["ratio"] >= cut]
        m, p = cluster_mean_p([t[key] for t in top], [t["entry"] for t in top])
        return {"n": len(top), "estimate": m, "p": p}
    good = [t[key] for t in trades if not t["loses_unchanged"]]
    bad = [t[key] for t in trades if t["loses_unchanged"]]
    d, p = diff_p(good, bad)
    return {"n": len(trades), "n_loses_unchanged": len(bad), "estimate": d, "p": p}


def judge_design(trades: list[dict], reject: bool, base: dict, test: str) -> dict:
    if base["n"] < MIN_TRADES:
        return {"decision": "no verdict", "why": f"under {MIN_TRADES} trades"}
    if not reject or base["estimate"] is None or base["estimate"] <= 0:
        return {"decision": "fail", "why": "not above zero under Holm"}
    stress = reading(trades, test, "stress_pct")
    if stress["estimate"] is None or stress["estimate"] <= 0:
        return {
            "decision": "fail",
            "why": "not above zero at 1.5x fees and a quarter of the spread",
            "stress": stress,
        }
    trimmed = reading(without_best(trades, "net_pct"), test)
    if trimmed["estimate"] is None or trimmed["estimate"] <= 0:
        return {
            "decision": "fail",
            "why": "not above zero with the best five sessions removed",
            "trimmed": trimmed,
        }
    return {"decision": "pass", "stress": stress, "trimmed": trimmed}


def judge_holdout(design: dict, hold: dict) -> dict:
    if design.get("decision") != "pass":
        return {"decision": "not tested", "why": "did not pass the design"}
    if hold["n"] < MIN_TRADES:
        return {"decision": "no verdict", "why": f"under {MIN_TRADES} trades"}
    ok = hold["estimate"] is not None and hold["estimate"] > 0 and hold["p"] < ALPHA
    return {"decision": "pass" if ok else "fail"}


# ------------------------------------------------------------------------------------------------
# Reads.


def _dir(override: str | None) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / "item54"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def universe() -> list[str]:
    """The NAMES tradable names with the highest median dollar volume on the newest reading."""
    from cherrypick.core import home
    from cherrypick.technicals import liquidity

    tradable = liquidity.tradable_names()
    folder = home.data_dir("market-report") / "dollar-volume"
    newest = max(folder.glob("????-??-??.json"))
    dv = json.loads(newest.read_text(encoding="utf-8")).get("names") or {}
    ranked = sorted((s for s in tradable if s in dv), key=lambda s: (-(dv[s].get("median") or 0), s))
    return ranked[:NAMES]


def run_all(out: Path) -> tuple[list[dict], dict]:
    from cherrypick.manage import rules as _rules
    from cherrypick.manage import timespreads as ts
    from cherrypick.technicals import paths as tpaths

    from cherrypick.backtest import paths
    from cherrypick.backtest import settings as _settings

    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    rules = _rules.effective(None)[0]
    cfg: dict = {}
    chains = _ro(paths.vendor_dir(_settings.load()) / "eod_chains.db")
    hist = _ro(tpaths.history_db())
    drops: Counter = Counter()
    trades: list[dict] = []
    try:
        for sym in plan["names"]:
            bars = hist.execute(
                "SELECT date, close FROM bars WHERE symbol = ? ORDER BY date", (sym,)
            ).fetchall()
            splits = [r[0] for r in hist.execute("SELECT ex_date FROM splits WHERE symbol = ?", (sym,))]
            ds, cs = [r[0] for r in bars], [r[1] for r in bars]

            def close(day: str, ds=ds, cs=cs) -> float | None:
                i = bisect.bisect_left(ds, day)
                return cs[i] if i < len(ds) and ds[i] == day else None

            def rows(session: str, exp: str, sym=sym) -> list[tuple]:
                return chains.execute(
                    "SELECT strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? AND session = ? "
                    "AND expiration = ?",
                    (sym, session, exp),
                ).fetchall()

            for e in (date.fromisoformat(x) for x in plan["entries"]):
                win = window_of(e)
                f, b = front_expiry(e), back_expiry(e)
                if win is None or f is None or b is None:
                    continue
                spot, s_exp = close(e.isoformat()), close(f.isoformat())
                if spot is None or s_exp is None:
                    drops["no close at entry or expiry"] += 1
                    continue
                if any(e.isoformat() < x <= f.isoformat() for x in splits):
                    drops["split inside the trade"] += 1
                    continue
                fr, br = rows(e.isoformat(), f.isoformat()), rows(e.isoformat(), b.isoformat())
                if not fr or not br:
                    drops["no front or back chain stored"] += 1
                    continue
                entry = {
                    "spot": spot,
                    "front": {"expiration": f.isoformat(), **view(fr, (f - e).days)},
                    "back": {"expiration": b.isoformat(), **view(br, (b - e).days)},
                }
                built, why = ts.structures(entry, e, rules)
                for k, v in why.items():
                    drops[f"builder: {k}"] += v
                ratio = atm_ratio(built, spot)
                if ratio is None:
                    drops["no at-the-money calendar to read the ratio from"] += 1
                    continue
                exit_rows = {
                    (float(k), right_of(r)): (bid, ask)
                    for k, r, bid, ask in rows(f.isoformat(), b.isoformat())
                }
                for kind in STRUCTURES:
                    row = pick(built, kind, spot)
                    if row is None:
                        drops[f"no {kind}"] += 1
                        continue
                    lg = row["long"]
                    res = settle(
                        {
                            "short": row["short"],
                            "entry_debit": row["debit_mid"],
                            "entry_quotes": row["entry_quotes"],
                        },
                        s_exp,
                        exit_rows.get((lg["strike"], lg["right"])),
                        cfg,
                        sym,
                    )
                    if res is None:
                        drops["long not quoted two-sided at the short's expiry"] += 1
                        continue
                    trades.append(
                        {
                            "symbol": sym,
                            "entry": e.isoformat(),
                            "window": win,
                            "kind": kind,
                            "ratio": ratio,
                            "loses_unchanged": row["loses_unchanged"],
                            **res,
                        }
                    )
    finally:
        chains.close()
        hist.close()
    return trades, dict(drops)


def atm_ratio(built: list[dict], spot: float) -> float | None:
    """The front IV over the back IV of the at-the-money put calendar (the scanner's own read)."""
    cals = [r for r in built if r["kind"] == "put calendar"]
    if not cals:
        return None
    r = min(cals, key=lambda r: (abs(r["long"]["strike"] - spot), r["long"]["strike"]))
    return round(r["front_iv"] / r["back_iv"], 4) if r["back_iv"] else None


# ------------------------------------------------------------------------------------------------
# Steps.


def cmd_plan(args) -> int:
    d = _dir(args.out_dir)
    names = universe()
    entries = entry_sessions()
    reqs = requests_for(names, entries)
    (d / "requests.json").write_text(json.dumps({"requests": reqs}), encoding="utf-8")
    plan = {
        "names": names,
        "entries": [e.isoformat() for e in entries],
        "requests": len(reqs),
        "tests": [f"{s}|{t}" for s in STRUCTURES for t in TESTS],
        "universe_label": "hindsight (B11): today's liquidity verdict and dollar volume",
    }
    (d / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(json.dumps({"names": len(names), "entries": len(entries), "requests": len(reqs), "dir": str(d)}))
    return 0


def _run_window(d: Path, window: str) -> dict:
    trades = json.loads((d / "trades.json").read_text(encoding="utf-8"))
    return {s: [t for t in trades if t["kind"] == s and t["window"] == window] for s in STRUCTURES}


def cmd_design(args) -> int:
    d = _dir(args.out_dir)
    trades, drops = run_all(d)
    (d / "trades.json").write_text(json.dumps(trades), encoding="utf-8")
    cells = _run_window(d, "design")
    base = {f"{s}|{t}": reading(cells[s], t) for s in STRUCTURES for t in TESTS}
    reject = core_stats.holm({k: v["p"] for k, v in base.items()}, ALPHA)
    adjusted = core_stats.holm_adjusted({k: v["p"] for k, v in base.items()})
    result = {"drops": drops, "trades": len(trades), "tests": {}}
    for s in STRUCTURES:
        for t in TESTS:
            k = f"{s}|{t}"
            result["tests"][k] = {
                **base[k],
                "holm_p": adjusted[k],
                **judge_design(cells[s], reject[k], base[k], t),
            }
    (d / "design.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    passed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    print(json.dumps({"design_passes": passed, "file": str(d / "design.json")}))
    return 0


def overall_verdict(design_tests: dict, confirmed: list) -> str:
    """The study's one word: passed when a test is confirmed; no verdict when every design test was
    no verdict (too few trades, nothing judged); failed otherwise. Before 2026-11-07 a study that
    judged nothing wrote failed, which read as a rejected rule."""
    if confirmed:
        return "passed"
    if design_tests and all(t.get("decision") == "no verdict" for t in design_tests.values()):
        return "no verdict"
    return "failed"


def cmd_holdout(args) -> int:
    d = _dir(args.out_dir)
    design = json.loads((d / "design.json").read_text(encoding="utf-8"))
    cells = _run_window(d, "holdout")
    result = {"tests": {}}
    for s in STRUCTURES:
        for t in TESTS:
            k = f"{s}|{t}"
            hold = reading(cells[s], t)
            result["tests"][k] = {**hold, **judge_holdout(design["tests"][k], hold)}
    confirmed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    result["verdict"] = overall_verdict(design["tests"], confirmed)
    result["confirmed"] = confirmed
    (d / "holdout.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "confirmed": confirmed, "file": str(d / "holdout.json")}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("design", cmd_design), ("holdout", cmd_holdout)):
        sp = sub.add_parser(name)
        sp.add_argument("--out-dir")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
