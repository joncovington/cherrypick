"""Item 64, defined-risk bullish structures on slow-moving large caps (docs/edge-hypotheses-plan.md):
a call backspread, two front call credit spreads with a back-month long call, a 1-3-2 put broken-wing
butterfly, and the 1-3-2 calendarized; each judged against holding the same delta in shares.

    python scripts/backspread_bwb_item64.py requests [--out PATH]
    python scripts/backspread_bwb_item64.py study [--start 2016-01-01] [--end 2021-12-31]
        [--allow-holdout] [--json PATH]

`requests` writes the path fetch: for each name and each standard monthly expiry, its daily chain from
85 days out to the expiry. `study` reads only `eod_chains.db` (the fetched paths). Results go under the
backtest data dir (results/research/item64/), never into the repo: the inputs are licensed.

**The cycle.** For each name and each standard monthly expiry F (the third Friday, the session before
when that is a holiday), with B the next standard monthly: enter on the session nearest 45 calendar
days before F, 42 to 48 days out. Strikes come from F's parity forward: K1 the listed strike nearest
it; calls K2 the first listed strike at least 3% of the forward above K1, K3 the same above K2; puts
P2 the first at least 3% below K1 and P3 the same below P2 (P1 = K1).

**The four structures** (signed contracts; + bought):
- A backspread: B +2 K1 call, B -1 K2 call.
- B spreads + back long: B +1 K1 call; F -2 K2 call, F +2 K3 call.
- C 1-3-2: F +1 P1 put, F -3 P2 put, F +2 P3 put.
- D 1-3-2 calendarized: B +1 P1 put, B -1 P2 put; F -2 P2 put, F +2 P3 put.

Every leg opens and closes at the working price (halfway from mid to natural). Capital is what the
broker held in the 2026-11-07 dry runs: the debit for A and B; the expiry's maximum loss for C; for D,
twice the lower width less the net credit. A structure is closed whole on the first session whose
gross gain reaches 25% of its capital, else on the first session 21 days or fewer before F. A session
on which a leg does not price is passed over; no priced exit is skipped and counted.

**The drift control.** Each trade's shares: the structure's entry delta (Black-76 at r = 0 from each
leg's own mid and chain forward) times the change in F's parity forward from entry to exit. Excess is
the trade's net less that. One observation is one cycle month: the equal-weight mean over the names
that entered.

**The verdict**, per structure, on the cycle means of excess return on capital: one-sided t above 2
and Holm at 5% across five tests (the four structures and D less C); positive in both time-halves;
and the raw return on capital still positive at 1.5x costs. The fifth test is D's return on capital
less C's, per cycle, over names that entered both: does calendarizing pay for its extra capital?
Fewer than 50 cycles, or more than 5% of entries without an exit mark, is no verdict. The holdout
(entries after 2021-12-31) is refused without --allow-holdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.core import calendar as cal
from cherrypick.core import fees, home, legbars, robustness
from cherrypick.core import stats as core_stats

_ITEM59 = Path(__file__).resolve().parent / "iwm_condor_item59.py"


def _load_item59():
    spec = importlib.util.spec_from_file_location("iwm_condor_item59", _ITEM59)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ic = _load_item59()

NAMES = ("WMT", "KMB", "PG", "KO", "PEP", "JNJ", "MCD", "COST", "TJX", "CL")
DESIGN = ("2016-01-01", "2021-12-31")
LAST_EXPIRY = date(2026, 12, 18)
PATH_DAYS = 85
ENTRY_DTE = 45
ENTRY_BAND = (42, 48)
GAP = 0.03
TARGET = 0.25
TIME_EXIT_DTE = 21
MIN_CYCLES = 50
MAX_NO_EXIT_SHARE = 0.05
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
STRUCTURES = ("A", "B", "C", "D")
TESTS = (*STRUCTURES, "D-C")


# --------------------------------------------------------------------------- calendar
def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def monthly_expiry(year: int, month: int) -> date:
    d = third_friday(year, month)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def monthlies(first: date, last: date) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def requests(
    names=NAMES, first: date = date(2016, 1, 1), last: date = LAST_EXPIRY, today: date | None = None
):
    today = today or date.today()
    out = []
    for name in names:
        for e in monthlies(first, last):
            out.append(
                {
                    "symbol": name,
                    "expiration": e.isoformat(),
                    "start": (e - timedelta(days=PATH_DAYS)).isoformat(),
                    "through": min(e, today).isoformat(),
                }
            )
    return out


# --------------------------------------------------------------------------- strikes and legs
def _listed(chain, right: str) -> list[float]:
    return sorted(chain.by[right])


def pick_strikes(front, back) -> dict | None:
    """K1 nearest F's forward; calls step up and puts step down by at least GAP of the forward. Every
    strike a structure needs must be listed in the expiry it is used in, else None."""
    fwd = front.forward
    if not fwd:
        return None
    ks = _listed(front, "C")
    if not ks:
        return None
    k1 = min(ks, key=lambda k: (abs(k - fwd), k))
    gap = GAP * fwd

    def up(k):
        c = [x for x in ks if x >= k + gap - 1e-9]
        return c[0] if c else None

    def down(k):
        c = [x for x in _listed(front, "P") if x <= k - gap + 1e-9]
        return c[-1] if c else None

    k2 = up(k1)
    k3 = up(k2) if k2 else None
    p2 = down(k1)
    p3 = down(p2) if p2 else None
    if None in (k2, k3, p2, p3):
        return None
    return {"K1": k1, "K2": k2, "K3": k3, "P1": k1, "P2": p2, "P3": p3}


def structure_legs(s: dict) -> dict[str, list]:
    """Each structure as [(expiry tag, strike, right, signed contracts)]."""
    return {
        "A": [("B", s["K1"], "C", 2), ("B", s["K2"], "C", -1)],
        "B": [("B", s["K1"], "C", 1), ("F", s["K2"], "C", -2), ("F", s["K3"], "C", 2)],
        "C": [("F", s["P1"], "P", 1), ("F", s["P2"], "P", -3), ("F", s["P3"], "P", 2)],
        "D": [
            ("B", s["P1"], "P", 1),
            ("B", s["P2"], "P", -1),
            ("F", s["P2"], "P", -2),
            ("F", s["P3"], "P", 2),
        ],
    }


def quotes(chains: dict, legs: list, *, close: bool = False) -> list | None:
    out = []
    for tag, k, r, n in legs:
        q = chains[tag].by[r].get(k) if chains.get(tag) else None
        if q is None:
            return None
        out.append((q, -n if close else n))
    return out


def price(chains: dict, legs: list, fill: str, *, close: bool = False) -> float | None:
    """Per share, a credit +: what opening (or, close=True, closing) the structure brings in."""
    qs = quotes(chains, legs, close=close)
    p = legbars.structure_prices(qs) if qs else None
    return None if p is None else p[fill]


def contracts(legs: list) -> tuple[int, int]:
    return sum(abs(n) for *_, n in legs), sum(-n for *_, n in legs if n < 0)


def open_fee(symbol: str, legs: list) -> float:
    total, sold = contracts(legs)
    return fees.ic_open_fee(symbol, 1, legs=total, sell_legs=sold)


def close_fee(symbol: str, legs: list) -> float:
    total, sold = contracts(legs)
    return fees.ic_close_fee(symbol, 1, legs=total, sell_legs=sold)


def expiry_value(legs: list, s: float) -> float:
    """Per share, the single-expiry structure's value at expiry with the underlying at s."""
    v = 0.0
    for _, k, r, n in legs:
        v += n * (max(s - k, 0.0) if r == "C" else max(k - s, 0.0))
    return v


def capital(name: str, legs: list, credit: float, s: dict) -> float | None:
    """Dollars held, as the broker held them in the 2026-11-07 dry runs. None when not positive."""
    if name in ("A", "B"):
        cap = -credit * 100
    elif name == "C":
        points = [0.0, *(k for _, k, _, _ in legs), 10 * max(k for _, k, _, _ in legs)]
        worst = min(expiry_value(legs, x) for x in points)
        cap = -(worst + credit) * 100
    else:
        cap = (2 * (s["P2"] - s["P3"]) - credit) * 100
    return round(cap, 2) if cap > 0 else None


def entry_delta(chains: dict, legs: list) -> float | None:
    """Share-equivalent delta of the structure: sum of signed contracts x 100 x each leg's delta."""
    total = 0.0
    for tag, k, r, n in legs:
        q = chains[tag].by[r].get(k)
        d = chains[tag].delta(q) if q else None
        if d is None:
            return None
        total += n * 100 * d
    return total


# --------------------------------------------------------------------------- one trade
def run_trade(
    symbol: str,
    name: str,
    legs: list,
    strikes: dict,
    entry: dict,
    path: list,
    front_expiry: str,
    *,
    fill: str = "working",
    target: float = TARGET,
) -> dict:
    """`entry` is {"F": chain, "B": chain}; `path` is [(session, {"F": chain, "B": chain})]."""
    credit = price(entry, legs, fill)
    if credit is None:
        return {"skip": "entry does not price"}
    cap = capital(name, legs, credit, strikes)
    if cap is None:
        return {"skip": "no capital"}
    delta = entry_delta(entry, legs)
    if delta is None or not entry["F"].forward:
        return {"skip": "no entry delta"}
    fee_open, fee_close = open_fee(symbol, legs), close_fee(symbol, legs)
    for session, chains in path:
        proceeds = price(chains, legs, fill, close=True)
        if proceeds is None or not chains["F"].forward:
            continue
        gross = round((credit + proceeds) * 100, 2)
        dte = ic.days_between(session, front_expiry)
        how = "target" if gross >= target * cap else ("time" if dte <= TIME_EXIT_DTE else None)
        if how is None:
            continue
        cost = round(fee_open + fee_close, 2)
        net = round(gross - cost, 2)
        shares = round(delta * (chains["F"].forward - entry["F"].forward), 2)
        return {
            "credit": credit,
            "proceeds": proceeds,
            "exit": session,
            "how": how,
            "gross": gross,
            "cost": cost,
            "net": net,
            "capital": cap,
            "delta": round(delta, 2),
            "shares": shares,
            "excess": round(net - shares, 2),
        }
    return {"skip": "no exit mark"}


# --------------------------------------------------------------------------- the store
def load_chain(db: sqlite3.Connection, symbol: str, session: str, expiry: str):
    rows = db.execute(
        "SELECT strike, right, bid, ask FROM eod_chain_quotes "
        "WHERE symbol = ? AND session = ? AND expiration = ?",
        (symbol, session, expiry),
    ).fetchall()
    return ic.Chain(rows, session, expiry) if rows else None


def sessions_for(db: sqlite3.Connection, symbol: str, expiry: str) -> list[str]:
    return [
        s
        for (s,) in db.execute(
            "SELECT DISTINCT session FROM eod_chain_quotes "
            "WHERE symbol = ? AND expiration = ? ORDER BY session",
            (symbol, expiry),
        )
    ]


def trades(db: sqlite3.Connection, start: str, end: str, *, fill: str = "working") -> tuple[list, dict]:
    """Every (name, front expiry) cycle in the window, all four structures: (rows, skips by structure)."""
    rows, skips = [], {k: Counter() for k in STRUCTURES}
    exps = monthlies(date(2015, 12, 1), LAST_EXPIRY + timedelta(days=40))
    for symbol in NAMES:
        for f, b in zip(exps, exps[1:], strict=False):
            planned = (f - timedelta(days=ENTRY_DTE)).isoformat()
            if not (start <= planned <= end):
                continue
            fs, bs_ = f.isoformat(), b.isoformat()
            sess = sessions_for(db, symbol, fs)
            s0 = ic.entry_session(sess, fs, ENTRY_DTE, ENTRY_BAND)
            if s0 is None:
                for k in STRUCTURES:
                    skips[k]["no entry chain"] += 1
                continue
            entry = {"F": load_chain(db, symbol, s0, fs), "B": load_chain(db, symbol, s0, bs_)}
            strikes = pick_strikes(entry["F"], entry["B"]) if entry["F"] and entry["B"] else None
            if strikes is None:
                for k in STRUCTURES:
                    skips[k]["no strikes"] += 1
                continue
            path = [
                (s, {"F": load_chain(db, symbol, s, fs), "B": load_chain(db, symbol, s, bs_)})
                for s in sess
                if s > s0
            ]
            path = [(s, c) for s, c in path if c["F"] is not None]
            for k, legs in structure_legs(strikes).items():
                r = run_trade(symbol, k, legs, strikes, entry, path, fs, fill=fill)
                if "skip" in r:
                    skips[k][r["skip"]] += 1
                    continue
                rows.append({"symbol": symbol, "structure": k, "front": fs, "back": bs_, "entry": s0, **r})
    return rows, skips


# --------------------------------------------------------------------------- statistics
def cycle_means(rows: list, key) -> dict[str, float]:
    """One observation per cycle month: the equal-weight mean of key(row) over the names entered."""
    g: dict = defaultdict(list)
    for r in rows:
        g[r["front"][:7]].append(key(r))
    return {m: statistics.fmean(v) for m, v in sorted(g.items())}


def diff_cycles(rows: list, a: str = "D", b: str = "C") -> dict[str, float]:
    """Per cycle, the mean over names that entered both of a's return on capital less b's."""
    by: dict = defaultdict(dict)
    for r in rows:
        by[(r["front"], r["symbol"])][r["structure"]] = r["net"] / r["capital"]
    g: dict = defaultdict(list)
    for (front, _), v in by.items():
        if a in v and b in v:
            g[front[:7]].append(v[a] - v[b])
    return {m: statistics.fmean(v) for m, v in sorted(g.items())}


def _test(series: dict[str, float], start: str, end: str) -> dict:
    xs = list(series.values())
    t = ic.t_stat(xs)
    mid = ic.midpoint(start, end)[:7]
    halves = {h: [v for m, v in series.items() if (m < mid) == (h == "first")] for h in ("first", "second")}
    return {
        "cycles": len(xs),
        "mean": round(statistics.fmean(xs), 5) if xs else None,
        "t": None if t is None else round(t, 3),
        "p": ic.p_one(t, len(xs)),
        "halves": {h: round(statistics.fmean(v), 5) if v else None for h, v in halves.items()},
    }


def score(rows: list, skips: dict, start: str, end: str) -> dict:
    out: dict = {}
    for k in STRUCTURES:
        rs = [r for r in rows if r["structure"] == k]
        test = _test(cycle_means(rs, lambda r: r["excess"] / r["capital"]), start, end)
        roc = cycle_means(rs, lambda r: r["net"] / r["capital"])
        cost = cycle_means(rs, lambda r: r["cost"] / r["capital"])
        months = sorted(roc)
        test["raw_roc"] = round(statistics.fmean(roc.values()), 5) if roc else None
        test["cost_stress"] = (
            robustness.cost_stress(
                [roc[m] for m in months], [cost[m] for m in months], pass_stress=PASS_STRESS
            )
            if months
            else None
        )
        no_exit = skips[k].get("no exit mark", 0)
        entered = len(rs) + no_exit
        test["trades"] = len(rs)
        test["no_exit_share"] = round(no_exit / entered, 3) if entered else None
        test["win_rate"] = round(sum(r["net"] > 0 for r in rs) / len(rs), 3) if rs else None
        test["by_exit"] = dict(Counter(r["how"] for r in rs))
        out[k] = test
    out["D-C"] = _test(diff_cycles(rows), start, end)
    return out


def judge(tests: dict) -> None:
    adjusted = core_stats.holm({k: tests[k]["p"] for k in TESTS}, alpha=ALPHA)
    for k in TESTS:
        t = tests[k]
        if t["cycles"] < MIN_CYCLES:
            t["verdict"], t["why"] = "no verdict", f"{t['cycles']} cycles < {MIN_CYCLES}"
            continue
        if (t.get("no_exit_share") or 0) > MAX_NO_EXIT_SHARE:
            t["verdict"], t["why"] = "no verdict", f"{t['no_exit_share']} of entries had no exit mark"
            continue
        checks = {
            "mean_positive": (t["mean"] or 0) > 0,
            "t_above_bar": (t["t"] or 0) > T_BAR,
            "holm": bool(adjusted.get(k)),
            "both_halves_positive": all((x or 0) > 0 for x in t["halves"].values()),
        }
        if k != "D-C":
            checks["raw_positive_at_1_5x_costs"] = bool(t["cost_stress"] and t["cost_stress"]["survives"])
        t["checks"] = checks
        t["verdict"] = "pass" if all(checks.values()) else "fail"


def describe(rows: list) -> dict:
    def m(xs):
        xs = list(xs)
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    out = {}
    for k in STRUCTURES:
        rs = [r for r in rows if r["structure"] == k]
        out[k] = {
            "net": m(r["net"] for r in rs),
            "shares": m(r["shares"] for r in rs),
            "capital": m(r["capital"] for r in rs),
            "by_name": {s: m(r["net"] for r in rs if r["symbol"] == s) for s in NAMES},
            "by_year": {
                y: m(r["net"] for r in rs if r["entry"][:4] == y)
                for y in sorted({r["entry"][:4] for r in rs})
            },
            "worst": [
                {k2: r[k2] for k2 in ("symbol", "entry", "net")}
                for r in sorted(rs, key=lambda r: r["net"])[:5]
            ],
        }
    return out


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests")
    rq.add_argument("--out", type=Path)
    st = sub.add_parser("study")
    st.add_argument("--start", default=DESIGN[0])
    st.add_argument("--end", default=DESIGN[1])
    st.add_argument("--allow-holdout", action="store_true")
    st.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    base = home.data_dir("backtest") / "results" / "research" / "item64"

    if args.cmd == "requests":
        target = args.out or base / "requests-paths.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        reqs = requests()
        target.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
        print(json.dumps({"requests": len(reqs), "written": str(target)}))
        return 0

    if args.end > DESIGN[1] and not args.allow_holdout:
        print(
            f"refused: --end {args.end} is past the design window's end {DESIGN[1]}, into the holdout; "
            "pass --allow-holdout",
            file=sys.stderr,
        )
        return 2
    cfg = _settings.load()
    vendor = paths.ensure_vendor_dir(cfg)
    db = sqlite3.connect(f"file:{vendor / 'eod_chains.db'}?mode=ro", uri=True)
    rows, skips = trades(db, args.start, args.end)
    tests = score(rows, skips, args.start, args.end)
    judge(tests)
    nets_by = {k: [r["net"] for r in rows if r["structure"] == k] for k in STRUCTURES}
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 64,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "verdicts": {k: tests[k].get("verdict") for k in TESTS},
        "tests": tests,
        "skips": {k: dict(v) for k, v in skips.items()},
        "describe": describe(rows),
        "planned_mde_B12": {k: robustness.planned_mde(v, len(v)) if v else None for k, v in nets_by.items()},
    }
    target = args.json or base / f"study-{args.start}-{args.end}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"trades-{args.start}-{args.end}.json").write_text(
        json.dumps(rows, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdicts": out["verdicts"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
