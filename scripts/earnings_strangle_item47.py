"""Item 47, a short strangle across an earnings report picked by implied against past moves
(docs/edge-hypotheses-plan.md): the request file and the study.

    python scripts/earnings_strangle_item47.py requests [--out-dir DIR]
    python scripts/earnings_strangle_item47.py study [--host H] [--port P] [--out-dir DIR]

Both read item 31's event list and calendar (results/research/item31/events.json, calendar.json):
the same 158 names, the same stated-timing reports, the same entry and reaction sessions, and item
31's paired control (`control_for`). Item 31's functions are imported, not copied, so the two studies
cannot drift apart.

`requests` writes the end-of-day singles the study needs that the store does not already answer: for
each event and its control, the strangle's monthly expiry on the entry session and on the reaction
session (`fetch-chains`). The front weekly's entry chain, which the expected move is read from, is
item 31's and is already stored.

`study` reads, per event:

- *the past moves* (Dolt `earnings_calendar` and `stocks.ohlcv`): for each earlier stated-timing report
  of the name, the reaction session's close against the entry session's (`close move`), and its worst
  extreme, the larger of the reaction session's high and low against that close (`extreme`). A pair
  of closes whose ratio looks like a split is left out, as item 31 does;
- *the expected move*: 0.85 times the front weekly's at-the-money straddle mid at the entry session's
  close, over the stock's close (the module's definition), the strike nearest the close with both
  sides two-sided;
- *the selector* (`rich`): with at least `MIN_PRIOR` earlier reports, the expected move above both the
  mean close move of the last `SHORT_PRIOR` and the mean close move of every earlier report;
- *the strangle*: the first standard monthly at least `MIN_DTE` calendar days after the entry session;
  the call at the lowest listed strike at or above close x (1 + W), the put at the highest at or
  below close x (1 - W), W the largest extreme of the last `SHORT_PRIOR` reports; both legs need a
  two-sided quote at entry;
- *the fills*: sold at the entry session's close at each leg's mid less the suite's slippage
  (`fees.slippage_dollars`, 0.125 of the spread), bought back at the reaction session's close at mid
  plus the same, `fees.ic_open_fee` and `fees.ic_close_fee` for two legs. A leg without a two-sided
  exit quote is bought back at its intrinsic value against the reaction close plus its entry spread,
  and counted. The stress reading takes 1.5x fees and a quarter of the spread.

The measure is the net in basis points of the stock's notional (100 x the entry close). The control is
the same strangle (the event's W, its own monthly and strikes) on item 31's control session, closed the
session after. Everything written goes under the backtest data dir (results/research/item47/), never
into the repo: the inputs and everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees, robustness

_spec = importlib.util.spec_from_file_location(
    "earnings_item31", Path(__file__).with_name("earnings_item31.py")
)
e31 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_item31", e31)
_spec.loader.exec_module(e31)

MIN_PRIOR = 4
SHORT_PRIOR = 8
MIN_DTE = 14
EM_FACTOR = 0.85
DESIGN, HOLDOUT = e31.DESIGN, e31.HOLDOUT
HALF_START = date(2022, 7, 1)  # the design's second time-half starts here
MIN_TRADES = {"design": 150, "holdout": 100}
T_BAR = 2.0
STRESS_FEES = 1.5
STRESS_COSTS = {"tastytrade_costs": {"slippage_frac_of_spread": 0.25}}
ARMS = ("rich", "unselected", "all")


# ------------------------------------------------------------------------------------------------
# Pure.


def monthly_expiry(entry: date, min_days: int = MIN_DTE) -> date:
    """The first standard monthly (the third Friday, the Thursday before when it is a holiday) at
    least `min_days` calendar days after `entry`."""
    y, m = entry.year, entry.month
    while True:
        first = date(y, m, 1)
        fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        exp = fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)
        if (exp - entry).days >= min_days:
            return exp
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def move_table(
    rows: list[tuple[date, str | None]], bars: dict[date, tuple]
) -> list[tuple[date, float, float]]:
    """(report date, close move, extreme) for each stated-timing report with both sessions' bars.
    `bars` maps a session to (open, high, low, close)."""
    out = []
    for d, when in sorted(rows):
        timing = e31.TIMINGS.get(when or "")
        if timing is None:
            continue
        a = bars.get(e31.entry_session(d, timing))
        b = bars.get(e31.reaction_session(d, timing))
        if not a or not b or not a[3] or not b[3]:
            continue
        ratio = b[3] / a[3]
        if not (e31.SPLIT_RATIO[0] < ratio < e31.SPLIT_RATIO[1]):
            continue
        extreme = max(b[1] / a[3] - 1.0, 1.0 - b[2] / a[3])
        out.append((d, abs(ratio - 1.0), extreme))
    return out


def selection(moves: list[tuple[date, float, float]], report: date, em_pct: float | None) -> dict | None:
    """The selector and W for one report, or None with fewer than MIN_PRIOR earlier reports."""
    prior = [m for m in moves if m[0] < report]
    if len(prior) < MIN_PRIOR:
        return None
    last = prior[-SHORT_PRIOR:]
    short = statistics.fmean(m[1] for m in last)
    long = statistics.fmean(m[1] for m in prior)
    width = max(m[2] for m in last)
    rich = em_pct is not None and em_pct > short and em_pct > long
    return {"prior": len(prior), "short_mean": short, "long_mean": long, "width": width, "rich": rich}


def _two_sided(q) -> bool:
    return q is not None and q[0] is not None and q[1] is not None and q[0] > 0 and q[1] >= q[0]


def expected_move(chain: dict[tuple[float, str], tuple], spot: float) -> float | None:
    """0.85 x the at-the-money straddle mid over spot, the strike nearest spot with both sides
    two-sided."""
    strikes = sorted(
        {k for k, r in chain if _two_sided(chain.get((k, "C"))) and _two_sided(chain.get((k, "P")))}
    )
    if not strikes or spot <= 0:
        return None
    k = min(strikes, key=lambda s: (abs(s - spot), s))
    mid = sum((chain[(k, r)][0] + chain[(k, r)][1]) / 2 for r in "CP")
    return EM_FACTOR * mid / spot


def strangle_strikes(strikes: list[float], spot: float, width: float) -> tuple[float, float] | None:
    """(put, call): the highest strike at or below spot x (1 - W) and the lowest at or above
    spot x (1 + W)."""
    puts = [k for k in strikes if k <= spot * (1 - width) + 1e-9]
    calls = [k for k in strikes if k >= spot * (1 + width) - 1e-9]
    return (max(puts), min(calls)) if puts and calls else None


def intrinsic(strike: float, right: str, spot: float) -> float:
    return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)


def price(
    symbol: str, legs: list[tuple[float, str]], entry: dict, exit_: dict, exit_spot: float, spot: float
) -> dict:
    """One strangle's net, sold at entry and bought back at exit, in dollars and in basis points of
    100 x spot, at the working costs and at the stress. A leg without a two-sided exit quote is bought
    back at intrinsic plus its entry spread."""
    e_quotes = [{"bid": entry[k][0], "ask": entry[k][1]} for k in legs]
    credit = 100.0 * sum((q["bid"] + q["ask"]) / 2 for q in e_quotes)
    x_quotes, x_value, filled = [], 0.0, 0
    for k, q in zip(legs, e_quotes, strict=True):
        x = exit_.get(k)
        if _two_sided(x):
            x_quotes.append({"bid": x[0], "ask": x[1]})
            x_value += 100.0 * (x[0] + x[1]) / 2
        else:
            filled += 1
            x_value += 100.0 * (intrinsic(k[0], k[1], exit_spot) + (q["ask"] - q["bid"]))
    fee = fees.ic_open_fee(symbol, 1, legs=2, sell_legs=2, ndigits=4) + fees.ic_close_fee(
        symbol, 1, legs=2, sell_legs=0, ndigits=4
    )
    out = {"credit": credit, "exit_value": x_value, "fees": fee, "exit_unquoted_legs": filled}
    for label, cfg, f in (("working", {}, 1.0), ("stress", STRESS_COSTS, STRESS_FEES)):
        slip = fees.slippage_dollars(e_quotes, 1, cfg) + (
            fees.slippage_dollars(x_quotes, 1, cfg) if x_quotes else 0.0
        )
        net = credit - x_value - slip - f * fee
        out[label] = {"net": net, "bp": 1e4 * net / (100.0 * spot)}
    return out


def window_of(report: date) -> str | None:
    if DESIGN[0] <= report <= DESIGN[1]:
        return "design"
    if HOLDOUT[0] <= report <= HOLDOUT[1]:
        return "holdout"
    return None


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def _pos(x) -> bool:
    return x is not None and x > 0


def arm_stats(trades: list[dict], *, bootstrap: bool) -> dict:
    """One arm in one window: the clustered tests of the net and of the paired difference, the halves,
    the stress and the net without the best five."""
    n = None if bootstrap else 0
    nets = [t["bp"] for t in trades]
    pairs = [t for t in trades if t.get("control_bp") is not None]
    return {
        "trades": len(trades),
        "pairs": len(pairs),
        "net": e31.cluster_t(nets, [t["entry_session"] for t in trades], resamples=n),
        "pair": e31.cluster_t(
            [t["bp"] - t["control_bp"] for t in pairs], [t["entry_session"] for t in pairs], resamples=n
        ),
        "net_by_time_half": [_mean(t["bp"] for t in trades if t["time_half"] == h) for h in (0, 1)],
        "net_by_name_half": [_mean(t["bp"] for t in trades if t["name_half"] == h) for h in (0, 1)],
        "stress_mean": _mean(t["stress_bp"] for t in trades),
        "without_best_five": _mean(e31.drop_best(nets)),
        "worst_bp": min(nets) if nets else None,
        "mean_net_dollars": _mean(t["net"] for t in trades),
        "mean_credit": _mean(t["credit"] for t in trades),
    }


def verdict(design: dict, holdout: dict, unselected_mean: float | None) -> dict:
    """The declared rule for `rich`."""
    if design["trades"] < MIN_TRADES["design"]:
        return {"verdict": "no verdict", "why": f"{design['trades']} design trades < {MIN_TRADES['design']}"}
    checks = {
        "net_t": _pos(design["net"]["mean"]) and design["net"]["t"] > T_BAR,
        "pair_t": _pos(design["pair"]["mean"]) and design["pair"]["t"] > T_BAR,
        "net_each_time_half": all(_pos(x) for x in design["net_by_time_half"]),
        "net_each_name_half": all(_pos(x) for x in design["net_by_name_half"]),
        "stress_positive": _pos(design["stress_mean"]),
        "best_five_removed_positive": _pos(design["without_best_five"]),
        "beats_unselected": unselected_mean is not None
        and design["net"]["mean"] is not None
        and design["net"]["mean"] > unselected_mean,
    }
    if not all(checks.values()):
        return {"verdict": "fail", "design_checks": checks}
    if holdout["trades"] < MIN_TRADES["holdout"]:
        return {"verdict": "no verdict", "design_checks": checks, "why": "holdout under its trade floor"}
    held = _pos(holdout["net"]["mean"]) and _pos(holdout["pair"]["mean"])
    return {"verdict": "pass" if held else "fail", "design_checks": checks, "holdout_positive": held}


# ------------------------------------------------------------------------------------------------
# The steps.


def _dir(override: str | None, item: str) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / item
    d.mkdir(parents=True, exist_ok=True)
    return d


def _inputs(item31_dir: Path):
    events = json.loads((item31_dir / "events.json").read_text(encoding="utf-8"))["events"]
    doc = json.loads((item31_dir / "calendar.json").read_text(encoding="utf-8"))
    reports, span = e31.reports_from_document(doc)
    return events, doc, reports, span


def legs_needed(event: dict, reports: dict, span) -> dict:
    """The event's and its control's (session, monthly) needs: entry and reaction for each."""
    entry = date.fromisoformat(event["entry_session"])
    reaction = date.fromisoformat(event["reaction_session"])
    exp = monthly_expiry(entry)
    out = {"event": (entry, reaction, exp), "control": None}
    c = e31.control_for(event, reports.get(event["symbol"], []), span)
    if c is not None:
        c_entry = date.fromisoformat(c["entry_session"])
        c_reaction = e31.reaction_session(date.fromisoformat(c["report_date"]), c["timing"])
        out["control"] = (c_entry, c_reaction, monthly_expiry(c_entry))
    return out


def cmd_requests(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    events, _doc, reports, span = _inputs(_dir(None, "item31"))
    want: dict[tuple, None] = {}
    for e in events:
        sym = e["symbol"].upper()
        for need in legs_needed(e, reports, span).values():
            if need is None:
                continue
            a, b, exp = need
            want.setdefault((sym, a.isoformat(), exp.isoformat()), None)
            want.setdefault((sym, b.isoformat(), exp.isoformat()), None)
    store = chainfetch.db_path(settings.load(None))
    stored = set()
    if store.exists():
        conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
        stored = e31.stored_requests(conn)["singles"]
        conn.close()
    todo = [k for k in want if k not in stored]
    d = _dir(args.out_dir, "item47")
    path = d / "eod-chain-requests.json"
    path.write_text(
        json.dumps({"requests": [{"symbol": s, "session": a, "expiration": x} for s, a, x in todo]}),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "planned": len(want),
                "already_stored": len(want) - len(todo),
                "requests": len(todo),
                "file": str(path),
            }
        )
    )
    return 0


def load_bars(names: list[str], host: str, port: int) -> dict[str, dict[date, tuple]]:
    import mysql.connector

    spelled = {sp: n for n in names for sp in e31._spellings(n)}
    marks = ",".join(["%s"] * len(spelled))
    conn = mysql.connector.connect(
        host=host, port=port, user="root", database="stocks", connection_timeout=10
    )
    out: dict[str, dict] = defaultdict(dict)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT act_symbol, date, open, high, low, close FROM ohlcv "
            f"WHERE date >= %s AND act_symbol IN ({marks})",
            ("2019-12-01", *spelled),
        )
        for sym, day, o, h, lo, c in cur.fetchall():
            if None not in (o, h, lo, c):
                out[spelled[sym]][day] = (float(o), float(h), float(lo), float(c))
    finally:
        conn.close()
    return dict(out)


def chain(conn, symbol: str, session: date, expiration: date) -> dict[tuple[float, str], tuple]:
    return {
        (k, r): (b, a)
        for k, r, b, a in conn.execute(
            "SELECT strike, right, bid, ask FROM eod_chain_quotes "
            "WHERE symbol = ? AND session = ? AND expiration = ?",
            (symbol, session.isoformat(), expiration.isoformat()),
        )
    }


def trade(conn, symbol: str, need: tuple, width: float, bars: dict) -> tuple[dict | None, str | None]:
    """The strangle on one (entry, reaction, expiry), or (None, why)."""
    entry, reaction, exp = need
    spot_bar, exit_bar = bars.get(entry), bars.get(reaction)
    if not spot_bar or not exit_bar:
        return None, "no_bars"
    spot, exit_spot = spot_bar[3], exit_bar[3]
    at_entry = chain(conn, symbol, entry, exp)
    if not at_entry:
        return None, "no_entry_chain"
    ks = strangle_strikes(sorted({k for k, _ in at_entry}), spot, width)
    if ks is None:
        return None, "no_strike_beyond_width"
    legs = [(ks[0], "P"), (ks[1], "C")]
    if not all(_two_sided(at_entry.get(k)) for k in legs):
        return None, "entry_leg_unquoted"
    at_exit = chain(conn, symbol, reaction, exp)
    if not at_exit:
        return None, "no_exit_chain"
    p = price(symbol, legs, at_entry, at_exit, exit_spot, spot)
    return {
        "credit": p["credit"],
        "net": p["working"]["net"],
        "bp": p["working"]["bp"],
        "stress_bp": p["stress"]["bp"],
        "exit_unquoted_legs": p["exit_unquoted_legs"],
        "expiration": exp.isoformat(),
    }, None


def cmd_study(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    events, doc, reports, span = _inputs(_dir(None, "item31"))
    names = sorted({e["symbol"] for e in events})
    bars = load_bars(names, args.host, args.port)
    rows = {s: [(date.fromisoformat(d), w) for d, w in v] for s, v in doc["reports"].items()}
    moves = {s: move_table(rows.get(s, []), bars.get(s, {})) for s in names}
    store = chainfetch.db_path(settings.load(None))
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    drops: dict[str, int] = defaultdict(int)
    trades: list[dict] = []
    for e in events:
        sym = e["symbol"]
        report = date.fromisoformat(e["report_date"])
        window = window_of(report)
        if window is None:
            drops["outside_windows"] += 1
            continue
        b = bars.get(sym, {})
        d = e31.event_dates(e)
        spot_bar = b.get(d["entry"])
        em = None
        if d["front"] is not None and spot_bar:
            em = expected_move(chain(conn, sym.upper(), d["entry"], d["front"]), spot_bar[3])
        sel = selection(moves.get(sym, []), report, em)
        if sel is None:
            drops["under_min_prior"] += 1
            continue
        if em is None:
            drops["no_expected_move"] += 1
            continue
        need = legs_needed(e, reports, span)
        t, why = trade(conn, sym.upper(), need["event"], sel["width"], b)
        if t is None:
            drops[why] += 1
            continue
        c = None
        if need["control"] is not None:
            c, cwhy = trade(conn, sym.upper(), need["control"], sel["width"], b)
            if c is None:
                drops[f"control_{cwhy}"] += 1
        else:
            drops["no_control_session"] += 1
        trades.append(
            {
                **t,
                "symbol": sym,
                "report_date": e["report_date"],
                "entry_session": e["entry_session"],
                "window": window,
                "rich": sel["rich"],
                "em_over_short": em / sel["short_mean"] if sel["short_mean"] else None,
                "width": sel["width"],
                "control_bp": c["bp"] if c else None,
                "time_half": 0 if report < HALF_START else 1,
                "name_half": robustness.name_half(sym),
            }
        )
    conn.close()
    result: dict = {"drops": dict(drops), "arms": {}}
    for window in ("design", "holdout"):
        w = [t for t in trades if t["window"] == window]
        for arm, pick in (
            ("rich", lambda t: t["rich"]),
            ("unselected", lambda t: not t["rich"]),
            ("all", lambda t: True),
        ):
            result["arms"].setdefault(arm, {})[window] = arm_stats(
                [t for t in w if pick(t)], bootstrap=(window == "design" and arm == "rich")
            )
    rich = result["arms"]["rich"]
    result["verdict"] = verdict(
        rich["design"], rich["holdout"], result["arms"]["unselected"]["design"]["net"]["mean"]
    )
    result["exit_unquoted_legs"] = sum(t["exit_unquoted_legs"] for t in trades)
    d = _dir(args.out_dir, "item47")
    (d / "trades.json").write_text(json.dumps(trades), encoding="utf-8")
    (d / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {"verdict": result["verdict"]["verdict"], "trades": len(trades), "file": str(d / "results.json")}
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("requests")
    sp.add_argument("--out-dir")
    sp.set_defaults(fn=cmd_requests)
    sp = sub.add_parser("study")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=3306)
    sp.add_argument("--out-dir")
    sp.set_defaults(fn=cmd_study)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
