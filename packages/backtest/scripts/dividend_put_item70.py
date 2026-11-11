"""Item 70, the dividend-yield put (research/edge-hypotheses-plan.md): a cash-secured put on a
screened dividend payer at the strike where its forward dividend would yield a target, against our
16-delta put on the same names and months.

    python packages/backtest/scripts/dividend_put_item70.py requests [--out FILE]   # screen + paths to fetch
    python packages/backtest/scripts/dividend_put_item70.py study [--json PATH] [--names A,B]

`requests` builds each window's monthly screen from local files (history.db's bars and reconciled
dividends, fundamentals.db's annual statements) and writes it with every (name, monthly) path the
store does not hold yet: the input of `fetch-chain-paths`. `study` replays the arms on the screen
and states the verdict. Both write under the backtest data dir (results/research/item70/), never
into the repo: the chain inputs are licensed.

**The screen**, on the first trading day of each month, over the liquidity verdict's tradable names
(today's verdict: a declared survivorship limit), each read as of that day:

- the trailing dividend: dividends with an ex-date in the 365 days to the day; a payer has one;
- the forward dividend: the latest dividend times the count in that trailing year (the run rate);
- earnings per share: the latest annual statement whose period ended at least `STATEMENT_LAG` days
  before, net income over shares; it must be positive;
- the payout ratio, trailing dividend over earnings per share, under 100%;
- the P/E, close over earnings per share, at or under the median of the names that pass so far;
- a split from that statement's period end through the day makes the name unknown (per-share
  dividends and the statement's share count would sit on different bases), never passing.

The `SCREEN_N` highest trailing yields that pass make the month's list. A name may enter only on days
of a month it is listed.

**The target**, per name, read on the screen day: the name's own usual yield, the median of its
trailing yield (trailing dividend over close) on the first trading day of each of the `NORM_MONTHS`
months before it, a month with a split in its trailing year skipped, at least `NORM_MIN` months
needed; the target price is the forward dividend over that yield. The educator's own rule (a bill
yield plus a premium for the stock's risk) was read on the screen inputs before this was declared:
at 2025-26's bill rates it put the median listed name's target 57% under its close, where no put is
bid, and at 2022's at or over the close on 80% of listings, so it was not declared.

**The arms**, each one position at a time per name on the 45-60 day standard monthly, entered on a
listed day while flat:

- `y-strike`: the highest listed put at or under the lower of the target price and the close; no
  lower strike is tried when it fails the spread bar; held to expiry.
- `y-net`: the highest listed put at or under the close whose strike less its working credit is at
  or under the target price, the first passing the spread bar going down; held to expiry.
- `d16-tp50` (the base, our short-put list's rule): the listed put whose delta is nearest -0.16,
  bought back at half the credit, else held.
- `d16-hold` (reported, never judged): the same put held, to separate the strike from the exit.

No earnings refusal: the strategy owns the stock through reports, and every arm crosses the same
ones. A span holding a split is refused. A leg must pass `core.legbars`' spread bar.

**Costs:** `premium_cycle_replay`'s fills (mid conceding a quarter of the bid-ask), fees and $5
settlement; 3/8 for the stress. An assigned put is booked at its intrinsic value at the expiry
close, as if the shares were sold at that close. Early assignment is not modelled.

**Measured:** per name and arm, edge = net minus the delta-equivalent stock position over each
trade, its dividends with an ex-date in the trade included, as a percentage of the mean capital
(strike x 100) per year the name was listed. A position open at the run end is marked at its
working buy-back on the last session.

**The verdict**, per judged arm (`y-strike`, `y-net`) against `d16-tp50`, on the design window: the
per-name paired difference's mean above zero with a one-sided t above 2, Holm across the two;
positive on more than half the names; the arm's own median edge above zero, and at the 3/8 stress;
then on the holdout the paired mean and the own median above zero. Under `MIN_NAMES` traded names
or `MIN_TRADES` trades in an arm in a window is no verdict.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from cherrypick.core import calendar as cal
from cherrypick.core import legbars
from cherrypick.core import stats as core_stats

_spec = importlib.util.spec_from_file_location(
    "pcr", Path(__file__).resolve().parents[3] / "scripts" / "premium_cycle_replay.py"
)
pcr = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("pcr", pcr)
_spec.loader.exec_module(pcr)

ARMS = ["y-strike", "y-net", "d16-tp50", "d16-hold"]
BASE = "d16-tp50"
JUDGED = ["y-strike", "y-net"]
EXPIRY_DAYS = (45, 60)
# (entries from, entries through, run end): every entry's monthly expires by the run end.
WINDOWS = {
    "design": (date(2025, 9, 1), date(2026, 8, 31), date(2026, 10, 16)),
    "holdout": (date(2022, 1, 1), date(2022, 12, 31), date(2023, 2, 17)),
}
SCREEN_N = 30
MAX_PAYOUT = 1.0
STATEMENT_LAG = 90
NORM_MONTHS = 60
NORM_MIN = 36
TARGET_DELTA = -0.16
MONTHLY_LEAD = 66
FRAC = 0.25
STRESS_FRAC = 0.375
MIN_NAMES = 20
MIN_TRADES = 150
T_BAR = 2.0
ALPHA = 0.05
EPS = 1e-9


# ------------------------------------------------------------------------------------------------
# Pure: the calendar, the screen and the strikes.


def fridays(a: date, b: date) -> list[date]:
    """Each week's expiry from a through b: the Friday, or the trading day before a holiday Friday."""
    out, d = [], a + timedelta(days=(4 - a.weekday()) % 7)
    while d <= b:
        out.append(d if cal.is_trading_day(d) else cal.previous_trading_day(d))
        d += timedelta(days=7)
    return out


def is_monthly(d: date) -> bool:
    third = pcr.third_friday(d.year, d.month)
    return d == (third if cal.is_trading_day(third) else cal.previous_trading_day(third))


def in_range(day: date, exp: date) -> bool:
    return EXPIRY_DAYS[0] <= (exp - day).days <= EXPIRY_DAYS[1] and is_monthly(exp)


def month_screen_day(day: date) -> date:
    """The first trading day of `day`'s month: the day its screen is read."""
    first = date(day.year, day.month, 1)
    return first if cal.is_trading_day(first) else cal.next_trading_day(first)


def screen_days(w0: date, w1: date) -> list[date]:
    """The first trading day of each month from w0 through w1."""
    out, d = [], date(w0.year, w0.month, 1)
    while d <= w1:
        first = month_screen_day(d)
        if w0 <= first <= w1:
            out.append(first)
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return out


def trailing(divs: dict[date, float], day: date) -> list[tuple[date, float]]:
    """Dividends with an ex-date in the 365 days to `day`, oldest first."""
    return sorted((d, a) for d, a in divs.items() if day - timedelta(days=365) < d <= day and a > 0)


def forward_dividend(divs: dict[date, float], day: date) -> float:
    """The run rate: the latest dividend times the count in the trailing year."""
    tr = trailing(divs, day)
    return tr[-1][1] * len(tr) if tr else 0.0


def months_before(day: date, n: int) -> list[date]:
    """The first trading day of each of the n months before `day`'s month, oldest first."""
    out, y, m = [], day.year, day.month
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append(month_screen_day(date(y, m, 1)))
    return out[::-1]


def norm_yield(
    closes: dict[date, float], divs: dict[date, float], splits: list[date], day: date
) -> float | None:
    """The name's usual yield: the median trailing yield over the `NORM_MONTHS` months before `day`,
    a month whose trailing year holds a split skipped; None under `NORM_MIN` months."""
    ys = []
    for m in months_before(day, NORM_MONTHS):
        c = closes.get(m)
        if not c or any(m - timedelta(days=365) < s <= m for s in splits):
            continue
        ys.append(sum(a for _, a in trailing(divs, m)) / c)
    return statistics.median(ys) if len(ys) >= NORM_MIN else None


def target_price(fwd: float, norm: float | None) -> float | None:
    return fwd / norm if fwd > 0 and norm else None


def screen_row(
    closes: dict[date, float],
    divs: dict[date, float],
    statements: list[tuple[date, float, float]],
    splits: list[date],
    day: date,
) -> dict:
    """One name's reading on a screen day: `state` is `pass`, `unknown` or why it fails; P/E is judged
    later, across names. `statements` is [(period_end, net_income, shares)]."""
    close = closes.get(day)
    tr = trailing(divs, day)
    if not close or close <= 0:
        return {"state": "no_price"}
    if not tr:
        return {"state": "no_dividend"}
    usable = [s for s in statements if s[0] <= day - timedelta(days=STATEMENT_LAG)]
    if not usable:
        return {"state": "unknown"}
    end, income, shares = max(usable)
    if any(end < s <= day for s in splits) or not shares:
        return {"state": "unknown"}
    eps = income / shares
    ttm = sum(a for _, a in tr)
    fwd, norm = forward_dividend(divs, day), norm_yield(closes, divs, splits, day)
    row = {"close": close, "ttm": ttm, "yield": ttm / close, "fwd": fwd, "eps": eps,
           "norm_yield": norm, "target": target_price(fwd, norm)}  # fmt: skip
    if eps <= 0:
        return {**row, "state": "no_earnings"}
    row["payout"], row["pe"] = ttm / eps, close / eps
    if row["payout"] >= MAX_PAYOUT:
        return {**row, "state": "payout"}
    return {**row, "state": "pass"}


def screen_list(rows: dict[str, dict]) -> list[str]:
    """The month's list: names that pass with a P/E at or under the passing names' median, the
    `SCREEN_N` highest trailing yields."""
    ok = {s: r for s, r in rows.items() if r.get("state") == "pass"}
    if not ok:
        return []
    med = statistics.median(r["pe"] for r in ok.values())
    cheap = [s for s, r in ok.items() if r["pe"] <= med + EPS]
    return sorted(cheap, key=lambda s: (-ok[s]["yield"], s))[:SCREEN_N]


def spread_ok(bid: float, ask: float) -> bool:
    """`core.legbars`' spread bar on one leg: two-sided, within 10% of the mid or a nickel."""
    if not legbars.two_sided({"bid": bid, "ask": ask}):
        return False
    bars, width, mid = legbars.LEG_BARS, ask - bid, (ask + bid) / 2
    return width <= bars["max_spread_abs"] + EPS or width / mid <= bars["max_spread_pct"] + EPS


def _leg(sl, k: float, frac: float) -> tuple[float, float] | None:
    """(working sell price, working buy price) per share at a strike, or None if unpriced."""
    mid, spr = sl.quote("P", k)
    if mid <= 0:
        return None
    s = pcr.slip(mid, spr, frac)
    return mid - s, mid + s


def pick_strike(arm: str, sl, close: float, target: float | None, frac: float) -> tuple[float | None, str]:
    """(strike, "") or (None, the reason none was taken)."""
    listed = sorted((k for k, (bid, _ask) in sl.quotes["P"].items() if bid > 0), reverse=True)
    if arm.startswith("d16"):
        priced = [(abs(d - TARGET_DELTA), k) for k in listed if (d := sl.delta("P", k)) is not None]
        if not priced:
            return None, "no_delta"
        k = min(priced)[1]
        return (k, "") if spread_ok(*sl.quotes["P"][k]) else (None, "spread")
    if target is None:
        return None, "no_target"
    if arm == "y-strike":
        k = next((k for k in listed if k <= min(target, close) + EPS), None)
        if k is None:
            return None, "no_strike"
        return (k, "") if spread_ok(*sl.quotes["P"][k]) else (None, "spread")
    for k in (k for k in listed if k <= close + EPS):  # y-net: highest first
        px = _leg(sl, k, frac)
        if px is None or k - px[0] > target + EPS:
            continue
        if spread_ok(*sl.quotes["P"][k]):
            return k, ""
    return None, "no_strike"


# ------------------------------------------------------------------------------------------------
# One name's data and the replay.


@dataclass
class NameData:
    symbol: str
    chains: dict[str, dict[str, list]]  # session -> expiration -> [[strike, "C"|"P", bid, ask]]
    closes: dict[date, float]  # the day's own close, the chain's share basis
    splits: list[date]
    dividends: dict[date, float]
    listed: set[tuple[int, int]]  # (year, month) the name is on the screen
    targets: dict[date, float] = field(default_factory=dict)  # day -> target price

    def __post_init__(self):
        self._slices: dict = {}
        self.days = sorted(self.closes)

    def close(self, d: date) -> float | None:
        if d in self.closes:
            return self.closes[d]
        i = bisect.bisect_right(self.days, d)
        return self.closes[self.days[i - 1]] if i else None

    def expiries(self, d: date) -> list[date]:
        return sorted(date.fromisoformat(e) for e in self.chains.get(d.isoformat(), {}))

    def slice(self, d: date, e: date):
        key = (d, e)
        if key not in self._slices:
            rows = self.chains.get(d.isoformat(), {}).get(e.isoformat())
            due = sum(a for ex, a in self.dividends.items() if d < ex <= e)
            self._slices[key] = (
                pcr.Slice(d, e, rows, self.closes[d], due) if rows and d in self.closes else None
            )
        return self._slices[key]

    def divs_between(self, a: date, b: date) -> float:
        return sum(x for ex, x in self.dividends.items() if a < ex <= b)


@dataclass
class Pos:
    strike: float
    exp: date
    entry_day: date
    cash: float
    fees: float
    credit: float
    entry_delta: float
    spot_entry: float


def run_arm(
    nd: NameData, arm: str, w0: date, w1: date, run_end: date, frac: float
) -> tuple[list[dict], dict]:
    stats: dict = defaultdict(int)
    trades: list[dict] = []
    pos: Pos | None = None
    last_closed: date | None = None

    def finish(p: Pos, day: date, paid: float, fee: float, settle: float, how: str):
        nonlocal last_closed
        trades.append(
            {
                "entry": p.entry_day.isoformat(),
                "exit": day.isoformat(),
                "expiry": p.exp.isoformat(),
                "strike": p.strike,
                "how": how,
                "net": round(p.cash - paid - p.fees - fee - settle, 2),
                "fees": round(p.fees + fee + settle, 2),
                "capital": p.strike * pcr.MULT,
                "pos_delta": -p.entry_delta,
                "spot_entry": p.spot_entry,
                "spot_exit": nd.close(day),
                "divs": nd.divs_between(p.entry_day, day),
                "below_close": 1.0 - p.strike / p.spot_entry,
            }
        )
        last_closed = day - timedelta(days=1) if how in ("expired", "assigned") else day

    for day in (d for d in nd.days if w0 <= d <= run_end):
        if pos is not None and pos.exp <= day:
            xc = nd.close(pos.exp)
            paid, fee = pcr.settle(SimpleNamespace(legs=[pcr.Leg("P", pos.strike, -1, 0.0)]), xc)
            finish(
                pos,
                pos.exp if pos.exp in nd.closes else day,
                paid,
                0.0,
                fee,
                "assigned" if paid > 0 else "expired",
            )
            pos = None
        if pos is not None and arm.endswith("tp50") and day > pos.entry_day:
            sl = nd.slice(day, pos.exp)
            px = _leg(sl, pos.strike, frac) if sl is not None else None
            if px is not None and px[1] <= 0.5 * pos.credit + EPS:
                finish(pos, day, px[1] * pcr.MULT, pcr.close_fee(1, 0), 0.0, "tp50")
                pos = None
        if (
            pos is None
            and w0 <= day <= w1
            and (day.year, day.month) in nd.listed
            and (last_closed is None or day > last_closed)
        ):
            exps = [e for e in nd.expiries(day) if in_range(day, e)]
            if not exps:
                stats["no_expiry"] += 1
                continue
            e = exps[0]
            if any(day < s <= e for s in nd.splits):
                stats["split"] += 1
                continue
            sl = nd.slice(day, e)
            if sl is None:
                stats["no_chain"] += 1
                continue
            k, why = pick_strike(arm, sl, nd.closes[day], nd.targets.get(day), frac)
            if k is None:
                stats[why] += 1
                continue
            px, d = _leg(sl, k, frac), sl.delta("P", k)
            if px is None or px[0] <= 0 or d is None:
                stats["unpriced"] += 1
                continue
            pos = Pos(k, e, day, px[0] * pcr.MULT, pcr.open_fee(1, 1), px[0], d, nd.closes[day])
            stats["entries"] += 1
    if pos is not None:
        last = max(d for d in nd.days if d <= run_end)
        sl = nd.slice(last, pos.exp)
        px = _leg(sl, pos.strike, frac) if sl is not None else None
        paid = px[1] * pcr.MULT if px else max(pos.strike - nd.close(last), 0.0) * pcr.MULT
        finish(pos, last, paid, pcr.close_fee(1, 0), 0.0, "open")
    return trades, dict(stats)


# ------------------------------------------------------------------------------------------------
# Pure: the measure and the verdict.


def trade_edge(t: dict) -> float:
    """Net less the delta-equivalent stock position, its dividends included, dollars."""
    return t["net"] - t["pos_delta"] * pcr.MULT * (t["spot_exit"] - t["spot_entry"] + t["divs"])


def name_edge(trades: list[dict], years: float) -> float:
    """Edge, % of the mean capital per listed year; 0 with no trades."""
    if not trades or years <= 0:
        return 0.0
    cap = statistics.mean(t["capital"] for t in trades)
    return 100.0 * sum(trade_edge(t) for t in trades) / cap / years


def paired(arm: dict[str, float], base: dict[str, float]) -> dict:
    names = sorted(set(arm) & set(base))
    d = [arm[n] - base[n] for n in names]
    n = len(d)
    mean = statistics.mean(d) if d else 0.0
    sd = statistics.stdev(d) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else (math.inf if mean > 0 else 0.0)
    p = 1.0 - core_stats.t_cdf(t, n - 1) if n > 1 and math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return {"n": n, "mean": mean, "t": t, "p": p, "positive_share": sum(x > 0 for x in d) / n if n else 0.0}


def _enough(w: dict, arm: str) -> bool:
    return w["traded"][arm] >= MIN_NAMES and w["trades"][arm] >= MIN_TRADES


def judge(design: dict, holdout: dict) -> dict[str, dict]:
    """Each window: {"edge": {arm: {name: %/yr}}, "stress": {...}, "trades": {arm: n},
    "traded": {arm: names with a trade}}."""
    out = {}
    for arm in JUDGED:
        pd = paired(design["edge"][arm], design["edge"][BASE])
        ph = paired(holdout["edge"][arm], holdout["edge"][BASE])
        own, stress = list(design["edge"][arm].values()), list(design["stress"][arm].values())
        hown = list(holdout["edge"][arm].values())
        out[arm] = {
            "design": pd,
            "holdout": ph,
            "checks": {
                "paired_mean_positive": pd["mean"] > 0,
                "paired_t_above_bar": pd["t"] > T_BAR,
                "more_than_half_positive": pd["positive_share"] > 0.5,
                "own_median_positive": bool(own) and statistics.median(own) > 0,
                "stress_median_positive": bool(stress) and statistics.median(stress) > 0,
                "holdout_paired_mean_positive": ph["mean"] > 0,
                "holdout_own_median_positive": bool(hown) and statistics.median(hown) > 0,
            },
            "enough": {
                "design": _enough(design, arm) and _enough(design, BASE),
                "holdout": _enough(holdout, arm) and _enough(holdout, BASE),
            },
        }
    holm = core_stats.holm({a: v["design"]["p"] for a, v in out.items()}, ALPHA)
    for arm, v in out.items():
        v["holm_ok"] = holm[arm]
        c = v["checks"]
        design_ok = all(c[k] for k in c if not k.startswith("holdout")) and v["holm_ok"]
        if not v["enough"]["design"]:
            v["verdict"] = "no verdict"
        elif not design_ok:
            v["verdict"] = "fail"
        elif not v["enough"]["holdout"]:
            v["verdict"] = "no verdict"
        else:
            ok = c["holdout_paired_mean_positive"] and c["holdout_own_median_positive"]
            v["verdict"] = "pass" if ok else "fail"
    return out


# ------------------------------------------------------------------------------------------------
# The stores (local files only; the chain store is licensed and read-only).


def _store():
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item70"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _history():
    from cherrypick.technicals import adjust, store
    from cherrypick.technicals import paths as tpaths

    hist = store.connect(tpaths.history_db())

    def name(sym: str):
        raw = store.raw_bars(hist, sym)
        closes = {date.fromisoformat(b.date): b.close for b in raw if b.close}
        splits = [date.fromisoformat(s.ex_date) for s in adjust.dedupe_splits(raw, store.splits(hist, sym))]
        divs, _ = store.dividends(hist, sym)
        return closes, splits, {date.fromisoformat(d.ex_date): d.amount for d in divs}

    return name


def _statements() -> dict[str, list[tuple[date, float, float]]]:
    from cherrypick.fundamentals import paths as fpaths

    conn = sqlite3.connect(f"file:{fpaths.db().as_posix()}?mode=ro", uri=True)
    out: dict[str, list] = defaultdict(list)
    for sym, end, income, shares in conn.execute(
        "SELECT symbol, period_end, net_income, shares FROM statements_annual "
        "WHERE net_income IS NOT NULL AND shares IS NOT NULL"
    ):
        out[sym].append((date.fromisoformat(end), income, shares))
    return out


def build_screens(names: list[str], history, statements) -> dict:
    """{window: {"days": {screen day: {"list": [...], "rows": {sym: row}}}}}."""
    loaded = {s: history(s) for s in names}
    out = {}
    for wname, (w0, w1, _end) in WINDOWS.items():
        days = {}
        for d in screen_days(w0, w1):
            rows = {}
            for s in names:
                closes, splits, divs = loaded[s]
                rows[s] = screen_row(closes, divs, statements.get(s, []), splits, d)
            days[d.isoformat()] = {"list": screen_list(rows), "rows": rows}
        out[wname] = {"days": days}
    return out


def entry_expiries(day: date) -> list[date]:
    """The standard monthlies 45-60 days out from any day of `day`'s month."""
    end = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    days = [day + timedelta(days=i) for i in range((end - day).days)]
    ex = fridays(day + timedelta(days=EXPIRY_DAYS[0]), end + timedelta(days=EXPIRY_DAYS[1]))
    return [e for e in ex if is_monthly(e) and any(in_range(d, e) for d in days)]


def path_requests(have: set[tuple[str, str]], screens: dict) -> list[dict]:
    out, seen = [], set()
    for wname, w in screens.items():
        run_end = WINDOWS[wname][2]
        for d, s in w["days"].items():
            for e in entry_expiries(date.fromisoformat(d)):
                for sym in s["list"]:
                    key = (sym, e.isoformat())
                    if key in have or key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        {
                            "symbol": sym,
                            "expiration": e.isoformat(),
                            "start": (e - timedelta(days=MONTHLY_LEAD)).isoformat(),
                            "through": min(e, run_end).isoformat(),
                        }
                    )
    return out


def _screens() -> dict:
    from cherrypick.technicals import liquidity

    names = sorted(liquidity.tradable_names())
    return build_screens(names, _history(), _statements())


def cmd_requests(args) -> int:
    screens = _screens()
    vend = _store()
    have = {(s, e) for s, e in vend.execute("SELECT symbol, expiration FROM eod_chain_paths")}
    reqs = path_requests(have, screens)
    d = _out_dir()
    (d / "screen.json").write_text(json.dumps(screens, default=str), encoding="utf-8")
    out = Path(args.out) if args.out else d / "path_requests.json"
    out.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    listed = {s for w in screens.values() for v in w["days"].values() for s in v["list"]}
    print(json.dumps({"listed_names": len(listed), "requests": len(reqs), "file": str(out)}))
    return 0


def load_chains(vend, sym: str, w0: date, run_end: date) -> dict:
    chains: dict = defaultdict(lambda: defaultdict(list))
    for day, exp, k, right, bid, ask in vend.execute(
        "SELECT session, expiration, strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? "
        "AND session BETWEEN ? AND ? AND bid IS NOT NULL AND ask IS NOT NULL",
        (sym, (w0 - timedelta(days=7)).isoformat(), run_end.isoformat()),
    ):
        chains[day][exp].append([k, right[0], bid, ask])
    return {d: dict(v) for d, v in chains.items()}


def cmd_study(args) -> int:
    path = _out_dir() / "screen.json"
    if not path.exists():
        raise SystemExit(f"no screen at {path}: run `dividend_put_item70.py requests` first")
    screens = json.loads(path.read_text(encoding="utf-8"))
    history, vend = _history(), _store()
    result: dict = {"windows": {}}
    for wname, (w0, w1, end) in WINDOWS.items():
        sw = screens[wname]
        listed: dict[str, set] = defaultdict(set)
        for d, s in sw["days"].items():
            for sym in s["list"]:
                listed[sym].add((int(d[:4]), int(d[5:7])))
        names = sorted(listed)
        if args.names:
            names = [n for n in names if n in set(args.names.split(","))]
        w = {"edge": {a: {} for a in ARMS}, "stress": {a: {} for a in ARMS}, "trades": dict.fromkeys(ARMS, 0),
             "traded": dict.fromkeys(ARMS, 0), "names": {}}  # fmt: skip
        for sym in names:
            closes, splits, divs = history(sym)
            nd = NameData(sym, load_chains(vend, sym, w0, end), closes, splits, divs, listed[sym])
            for day in (d for d in nd.days if w0 <= d <= w1):
                key = month_screen_day(day).isoformat()
                t = sw["days"].get(key, {}).get("rows", {}).get(sym, {}).get("target")
                if t is not None:
                    nd.targets[day] = t
            pcr.SYMBOL = sym
            years = len(listed[sym]) / 12.0
            row = {}
            for arm in ARMS:
                trades, stats = run_arm(nd, arm, w0, w1, end, FRAC)
                stressed, _ = run_arm(nd, arm, w0, w1, end, STRESS_FRAC)
                e, s = name_edge(trades, years), name_edge(stressed, years)
                w["edge"][arm][sym], w["stress"][arm][sym] = e, s
                w["trades"][arm] += len(trades)
                w["traded"][arm] += bool(trades)
                row[arm] = {"edge": e, "stress": s, "trades": len(trades), "stats": stats,
                            "below_close": [round(t["below_close"], 4) for t in trades],
                            "how": dict(statistics_count(t["how"] for t in trades))}  # fmt: skip
            w["names"][sym] = row
            print(wname, sym, {a: (row[a]["trades"], round(row[a]["edge"], 2)) for a in ARMS}, flush=True)
        result["windows"][wname] = w
    result["verdict"] = judge(result["windows"]["design"], result["windows"]["holdout"])
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    for arm, v in result["verdict"].items():
        fails = {k: x for k, x in v["checks"].items() if not x}
        print(arm, v["verdict"], fails, "holm" if v["holm_ok"] else "")
    return 0


def statistics_count(xs) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("requests")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_requests)
    s = sub.add_parser("study")
    s.add_argument("--json")
    s.add_argument("--names", help="comma-separated, for a quick look; the verdict needs every name")
    s.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
