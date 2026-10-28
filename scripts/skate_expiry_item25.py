"""Item 25, the skate put's expiry and management (docs/edge-hypotheses-plan.md): a cash-secured put at
the fixed skate band on the 14-day weekly against the 45-60 day monthly, each held with the $0.03
buy-back resting, closed at half credit, or rolled net-zero when tested.

    python scripts/skate_expiry_item25.py requests [--out FILE]     # the path requests still to fetch
    python scripts/skate_expiry_item25.py reports                   # the report dates, once (network)
    python scripts/skate_expiry_item25.py study [--json PATH] [--names A,B]

`requests` writes, for each name the confirmation fetched, every weekly expiry the two windows can
enter or roll into that the store does not hold yet (its path from 18 days before), and the monthlies
a late roll reaches (from 66 days before): the input of `fetch-chain-paths`. `study` replays both
windows and states the verdict. Both write under the backtest data dir (results/research/item25/),
never into the repo: the inputs are licensed.

**The arms, as declared.** The strike is the first listed put at or below the fixed band on the entry
day's close (`technicals.skate`'s band on split-adjusted bars, as a ratio to the close, applied to the
day's own close, so the strike is on the chain's share basis). `w14` takes the expiry 12-16 calendar
days out, `m45` the standard monthly 45-60 out; neither falls back. Management: `hold` closes at
$0.03 on the first close whose ask is at or under it; `tp50` buys back at half the credit; `roll`
keeps the $0.03 buy-back and, when a close is at or below the strike, rolls net-zero to the same
arm's next expiry at the highest strike at or below the old one whose working price covers the
buyback, at most twice. The base is `m45-tp50`.

**Entries refused:** a stock reporting from the entry day through the expiry, or whose report date is
unknown (`earnings_state`); a span holding a split; a leg failing `core.legbars`' spread bar (the
store has no open interest); no band (under the bars it needs). Funds are exempt from earnings.

**Costs:** `premium_cycle_replay`'s fills (mid conceding a quarter of the bid-ask, capped at 15% of
the mid), fees and $5 settlement; 3/8 for the stress. Early assignment is not modelled.

**Measured:** per name and arm, edge = net minus the delta-equivalent stock position over each trade,
summed over the window, as a percentage of the mean capital (strike x 100) a year, idle days in. A
position open at the run end is marked at its working buy-back on the last session.

**The verdict**, per judged arm, on the stocks: the per-name paired difference against `m45-tp50`
with its mean above zero and a one-sided t above 2, Holm across the five; positive on more than half
the names; the arm's own median edge above zero, and at the 3/8 stress; then in 2022 the paired mean
and the own median above zero. Under 30 names or 300 trades in an arm in a window is no verdict.
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
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from cherrypick.core import calendar as cal
from cherrypick.core import legbars
from cherrypick.core import stats as core_stats
from cherrypick.technicals import skate

_spec = importlib.util.spec_from_file_location("pcr", Path(__file__).with_name("premium_cycle_replay.py"))
pcr = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("pcr", pcr)
_spec.loader.exec_module(pcr)

ARMS = [f"{e}-{m}" for e in ("w14", "m45") for m in ("hold", "tp50", "roll")]
BASE = "m45-tp50"
JUDGED = [a for a in ARMS if a != BASE]
EXPIRY_DAYS = {"w14": (12, 16), "m45": (45, 60)}
FUNDS = {"SPY", "QQQ", "IWM", "GLD", "XLE", "SLV"}
# (entries from, entries through, run end): the run end is the last session the monthly paths reach.
WINDOWS = {
    "design": (date(2025, 10, 1), date(2026, 10, 16), date(2026, 10, 22)),
    "holdout": (date(2022, 1, 1), date(2022, 12, 31), date(2023, 2, 17)),
}
HAIRCUT = 0.75
SMOOTHING = 10
GTC = 0.03
MAX_ROLLS = 2
FRAC = 0.25
STRESS_FRAC = 0.375
WEEKLY_LEAD = 18
MONTHLY_LEAD = 66
WEEKLY_COVERAGE = 0.90
# A stock reports about every 91 days: no listed report within this many days of the last one is a
# report the calendar has not reached, and two listed reports further apart than the gap's limit
# have one missing between them.
REPORT_DUE_DAYS = 80
REPORT_GAP_DAYS = 120
# Report dates: Dolt's calendar, a name's old ticker included, and Nasdaq's public calendar for the
# days Dolt holds nothing at all (`dolt_holes`).
REPORT_SPAN = (date(2021, 6, 1), date(2027, 6, 30))
ALIASES = {"META": ("FB",)}  # Facebook's ticker until 2022-06-09
MIN_NAMES = 30
MIN_TRADES = 300
T_BAR = 2.0
ALPHA = 0.05
EPS = 1e-9


# ------------------------------------------------------------------------------------------------
# Pure: the calendar and the entry rules.


def fridays(a: date, b: date) -> list[date]:
    """Each week's expiry from a through b: the Friday, or the trading day before a holiday Friday."""
    out, d = [], a + timedelta(days=(4 - a.weekday()) % 7)
    while d <= b:
        out.append(d if cal.is_trading_day(d) else cal.previous_trading_day(d))
        d += timedelta(days=7)
    return out


def is_monthly(d: date) -> bool:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    return d == skate.monthly_expiry(d.year, d.month)


def in_range(arm: str, day: date, exp: date) -> bool:
    lo, hi = EXPIRY_DAYS[arm.split("-")[0]]
    if not lo <= (exp - day).days <= hi:
        return False
    return arm.startswith("w14") or is_monthly(exp)


def earnings_state(reports: list[date], start: date, end: date) -> str:
    """`crosses` when a report falls from start through end; `clear` when none does and the calendar
    shows the quarter around the span; else `unknown`, which is never `clear`."""
    if any(start <= r <= end for r in reports):
        return "crosses"
    prev = max((r for r in reports if r < start), default=None)
    nxt = min((r for r in reports if r > end), default=None)
    if prev is None:
        return "unknown"
    if nxt is not None:
        return "clear" if (nxt - prev).days <= REPORT_GAP_DAYS else "unknown"
    return "clear" if (end - prev).days < REPORT_DUE_DAYS else "unknown"


def spread_ok(bid: float, ask: float) -> bool:
    """`core.legbars`' spread bar on one leg: two-sided, within 10% of the mid or a nickel."""
    if not legbars.two_sided({"bid": bid, "ask": ask}):
        return False
    bars, width, mid = legbars.LEG_BARS, ask - bid, (ask + bid) / 2
    return width <= bars["max_spread_abs"] + EPS or width / mid <= bars["max_spread_pct"] + EPS


def band_strike(band: float, strikes: list[float]) -> float | None:
    """The first listed put at or below the band."""
    return max((k for k in strikes if k <= band + EPS), default=None)


# ------------------------------------------------------------------------------------------------
# One name's data.


@dataclass
class NameData:
    symbol: str
    fund: bool
    chains: dict[str, dict[str, list]]  # session -> expiration -> [[strike, "C"|"P", bid, ask]]
    closes: dict[date, float]  # the day's own close, the chain's share basis
    ratios: dict[int, dict[date, float]]  # n sessions -> day -> band / close
    reports: list[date]
    splits: list[date]
    dividends: dict[date, float]

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

    def band(self, d: date, e: date) -> float | None:
        n = cal.trading_days_between(d, e)
        r = self.ratios.get(n, {}).get(d)
        return None if r is None or d not in self.closes else self.closes[d] * r

    def blocked(self, d: date, e: date) -> str | None:
        if any(d < s <= e for s in self.splits):
            return "split"
        if not self.fund and earnings_state(self.reports, d, e) != "clear":
            return "earnings"
        return None


# ------------------------------------------------------------------------------------------------
# The replay.


@dataclass
class Pos:
    strike: float
    exp: date
    entry_day: date
    capital: float
    cash: float  # premium received less premium paid, dollars
    fees: float
    first_credit: float  # per share
    entry_delta: float
    spot_entry: float
    rolls: int = 0
    last_action: date | None = None


def _leg(sl, k: float, frac: float) -> tuple[float, float] | None:
    """(working sell price, working buy price) per share at a strike, or None if unpriced."""
    mid, spr = sl.quote("P", k)
    if mid <= 0:
        return None
    s = pcr.slip(mid, spr, frac)
    return mid - s, mid + s


def open_put(
    nd: NameData,
    arm: str,
    day: date,
    frac: float,
    stats: dict,
    below: float | None = None,
    after: date | None = None,
):
    """The arm's put opened today, or None with the reason counted. A roll passes `below`, the old
    strike (its new strike is the highest at or under it), and `after`, the old expiry."""
    exps = [e for e in nd.expiries(day) if in_range(arm, day, e) and (after is None or e > after)]
    if not exps:
        stats["no_expiry"] += 1
        return None
    e = exps[0]
    why = nd.blocked(day, e)
    if why:
        stats[why] += 1
        return None
    sl = nd.slice(day, e)
    if sl is None:
        stats["no_chain"] += 1
        return None
    listed = sorted(k for k, (bid, ask) in sl.quotes["P"].items() if bid > 0)
    if below is None:
        band = nd.band(day, e)
        if band is None:
            stats["no_band"] += 1
            return None
        k = band_strike(band, listed)
        cands = [k] if k is not None else []
    else:
        cands = sorted((k for k in listed if k <= below + EPS), reverse=True)
    for k in cands:
        bid, ask = sl.quotes["P"][k]
        if not spread_ok(bid, ask):
            stats["spread"] += 1
            continue
        px = _leg(sl, k, frac)
        if px is None or px[0] <= 0:
            continue
        return e, k, px[0], sl
    stats["no_strike"] += 1
    return None


def run_arm(
    nd: NameData, arm: str, w0: date, w1: date, run_end: date, frac: float
) -> tuple[list[dict], dict]:
    mgmt = arm.split("-")[1]
    stats: dict = defaultdict(int)
    trades: list[dict] = []
    pos: Pos | None = None
    last_closed: date | None = None

    def finish(p: Pos, day: date, paid: float, fee: float, settle: float, how: str):
        nonlocal last_closed
        net = p.cash - paid - p.fees - fee - settle
        trades.append(
            {
                "entry": p.entry_day.isoformat(),
                "exit": day.isoformat(),
                "expiry": p.exp.isoformat(),
                "how": how,
                "rolls": p.rolls,
                "net": round(net, 2),
                "fees": round(p.fees + fee + settle, 2),
                "capital": p.capital,
                "pos_delta": -p.entry_delta,
                "spot_entry": p.spot_entry,
                "spot_exit": nd.close(day),
            }
        )
        last_closed = day - timedelta(days=1) if how in ("expired", "assigned") else day

    for day in (d for d in nd.days if w0 <= d <= run_end):
        if pos is not None and pos.exp <= day:
            xc = nd.close(pos.exp)
            paid, fee = pcr.settle(SimpleNamespace(legs=[pcr.Leg("P", pos.strike, -1, 0.0)]), xc)
            out_day = pos.exp if pos.exp in nd.closes else day
            finish(pos, out_day, paid, 0.0, fee, "assigned" if paid > 0 else "expired")
            pos = None
        if pos is not None and day > pos.last_action:
            sl = nd.slice(day, pos.exp)
            if sl is not None:
                q = sl.quotes["P"].get(pos.strike)
                if mgmt in ("hold", "roll") and q is not None and q[1] <= GTC + EPS:
                    finish(pos, day, GTC * pcr.MULT, pcr.close_fee(1, 0), 0.0, "gtc")
                    pos = None
                elif mgmt == "tp50":
                    px = _leg(sl, pos.strike, frac)
                    if px is not None and px[1] <= 0.5 * pos.first_credit + EPS:
                        finish(pos, day, px[1] * pcr.MULT, pcr.close_fee(1, 0), 0.0, "tp50")
                        pos = None
                if (
                    pos is not None
                    and mgmt == "roll"
                    and pos.rolls < MAX_ROLLS
                    and nd.closes.get(day, math.inf) <= pos.strike + EPS
                ):
                    px = _leg(sl, pos.strike, frac)
                    # A put's premium falls with its strike, so if the highest strike at or under the old
                    # one does not cover the buyback, none below it does.
                    got = open_put(nd, arm, day, frac, stats, below=pos.strike, after=pos.exp) if px else None
                    if got is not None and got[2] >= px[1] - EPS:
                        e, k, fill, _ = got
                        pos.cash += (fill - px[1]) * pcr.MULT
                        pos.fees += pcr.close_fee(1, 0) + pcr.open_fee(1, 1)
                        pos.strike, pos.exp, pos.rolls, pos.last_action = k, e, pos.rolls + 1, day
                        stats["rolls"] += 1
                    else:
                        stats["roll_refused"] += 1
        if pos is None and w0 <= day <= w1 and (last_closed is None or day > last_closed):
            got = open_put(nd, arm, day, frac, stats)
            if got is not None:
                e, k, fill, sl = got
                d = sl.delta("P", k)
                if d is None:
                    stats["no_delta"] += 1
                else:
                    pos = Pos(k, e, day, k * pcr.MULT, fill * pcr.MULT, pcr.open_fee(1, 1), fill, d,
                              nd.closes[day], last_action=day)  # fmt: skip
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


def name_edge(trades: list[dict], years: float) -> float:
    """Edge over the delta-equivalent stock position, % of the mean capital a year; 0 with no trades."""
    if not trades:
        return 0.0
    cap = statistics.mean(t["capital"] for t in trades)
    edge = sum(t["net"] - t["pos_delta"] * pcr.MULT * (t["spot_exit"] - t["spot_entry"]) for t in trades)
    return 100.0 * edge / cap / years


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
    return len(w["edge"][arm]) >= MIN_NAMES and w["trades"][arm] >= MIN_TRADES


def judge(design: dict, holdout: dict) -> dict[str, dict]:
    """Each window: {"edge": {arm: {name: %/yr}}, "stress": {arm: {name: %/yr}}, "trades": {arm: n}},
    on the judged stocks only."""
    out = {}
    for arm in JUDGED:
        pd = paired(design["edge"][arm], design["edge"][BASE])
        ph = paired(holdout["edge"][arm], holdout["edge"][BASE])
        own = list(design["edge"][arm].values())
        stress = list(design["stress"][arm].values())
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
                "holdout_own_median_positive": bool(holdout["edge"][arm])
                and statistics.median(holdout["edge"][arm].values()) > 0,
            },
            "enough": {
                "design": _enough(design, arm) and _enough(design, BASE),
                "holdout": _enough(holdout, arm) and _enough(holdout, BASE),
            },  # fmt: skip
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
            v["verdict"] = (
                "pass" if c["holdout_paired_mean_positive"] and c["holdout_own_median_positive"] else "fail"
            )
    return out


# ------------------------------------------------------------------------------------------------
# The stores.


def _store():
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item25"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_requests(have: set[tuple[str, str]], names: list[str]) -> list[dict]:
    """Each weekly an arm can enter or roll into, and each monthly a late roll reaches, that the store
    does not hold for the name: (symbol, expiration, start, through)."""
    out = []
    for w0, _w1, end in WINDOWS.values():
        weeklies = fridays(
            w0 + timedelta(days=EXPIRY_DAYS["w14"][0]), end + timedelta(days=EXPIRY_DAYS["w14"][1])
        )
        monthlies = [e for e in fridays(end, end + timedelta(days=EXPIRY_DAYS["m45"][1])) if is_monthly(e)]
        for sym in names:
            for e, lead in [(e, WEEKLY_LEAD) for e in weeklies] + [(e, MONTHLY_LEAD) for e in monthlies]:
                if (sym, e.isoformat()) in have:
                    continue
                start = (e - timedelta(days=lead)).isoformat()
                out.append(
                    {
                        "symbol": sym,
                        "expiration": e.isoformat(),
                        "start": start,
                        "through": min(e, end).isoformat(),
                    }
                )
    return out


def weekly_coverage(status: dict[tuple[str, str], str], sym: str, w0: date, w1: date) -> float:
    """The share of the window's non-monthly weeklies the store holds a path for."""
    weeks = [e for e in fridays(w0 + timedelta(days=12), w1 + timedelta(days=16)) if not is_monthly(e)]
    return sum(status.get((sym, e.isoformat())) == "complete" for e in weeks) / len(weeks) if weeks else 0.0


def dolt_holes(counts: dict[date, int], a: date, b: date) -> list[date]:
    """Trading days from a through b on which Dolt lists no report for any company: a hole in the
    calendar (2022-01-24 to 01-28 has none, the week big tech reported), not a quiet day."""
    out, d = [], a
    while d <= b:
        if cal.is_trading_day(d) and not counts.get(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def merge_reports(
    dolt: dict[str, set[date]], filled: dict[date, set[str]], names: list[str]
) -> dict[str, list[date]]:
    """Each name's report dates: Dolt's under the name or an old ticker (`ALIASES`), plus the hole days
    Nasdaq lists the name on."""
    out = {}
    for sym in names:
        spellings = (sym, *ALIASES.get(sym, ()))
        got = set().union(*(dolt.get(s, set()) for s in spellings))
        got |= {d for d, syms in filled.items() if any(s in syms for s in spellings)}
        out[sym] = sorted(got)
    return out


def load_reports(names: list[str], cache: Path) -> dict[str, list[date]]:
    if not cache.exists():
        raise SystemExit(f"no report dates at {cache}: run `skate_expiry_item25.py reports` first")
    raw = json.loads(cache.read_text(encoding="utf-8"))["dates"]
    return {s: [date.fromisoformat(d) for d in raw.get(s, [])] for s in names}


def load_name(vend, hist, sym: str, reports: list[date], w0: date, run_end: date) -> NameData:
    from cherrypick.technicals import adjust, skate, store

    chains: dict = defaultdict(lambda: defaultdict(list))
    for day, exp, k, right, bid, ask in vend.execute(
        "SELECT session, expiration, strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? "
        "AND session BETWEEN ? AND ? AND bid IS NOT NULL AND ask IS NOT NULL",
        (sym, (w0 - timedelta(days=7)).isoformat(), run_end.isoformat()),
    ):
        chains[day][exp].append([k, right[0], bid, ask])
    raw = store.raw_bars(hist, sym)
    closes = {date.fromisoformat(b.date): b.close for b in raw if b.close}
    bars = skate.price_bars(hist, sym)
    highs, lows = [b.high for b in bars], [b.low for b in bars]
    ratios: dict[int, dict[date, float]] = {}
    for n in range(3, 46):  # every session count a 12-16 or 45-60 day expiry spans
        sr = skate.smoothed_range(highs, lows, n, SMOOTHING)
        ratios[n] = {
            date.fromisoformat(b.date): (b.close - HAIRCUT * r) / b.close
            for b, r in zip(bars, sr, strict=True)
            if r is not None and b.close and w0 - timedelta(days=7) <= date.fromisoformat(b.date) <= run_end
        }
    splits = [date.fromisoformat(s.ex_date) for s in adjust.dedupe_splits(raw, store.splits(hist, sym))]
    divs, _ = store.dividends(hist, sym)
    return NameData(
        sym, sym in FUNDS, {d: dict(v) for d, v in chains.items()}, closes, ratios, reports, splits,
        {date.fromisoformat(d.ex_date): d.amount for d in divs},
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------
# The commands.


def cmd_requests(args) -> int:
    vend = _store()
    have = {(s, e) for s, e in vend.execute("SELECT symbol, expiration FROM eod_chain_paths")}
    names = sorted({s for (s,) in vend.execute("SELECT DISTINCT symbol FROM eod_chain_paths")})
    reqs = path_requests(have, names)
    out = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    out.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    print(json.dumps({"names": len(names), "requests": len(reqs), "file": str(out)}))
    return 0


def cmd_reports(args) -> int:
    """The report-date cache the study reads: Dolt, and Nasdaq for Dolt's empty trading days. Run once,
    before the study, so the study needs neither the Dolt server nor the network."""
    import time
    import urllib.error

    from cherrypick.technicals import land

    spec = importlib.util.spec_from_file_location(
        "fetch_earnings_times", Path(__file__).with_name("fetch_earnings_times.py")
    )
    fet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fet)
    vend = _store()
    names = sorted({s for (s,) in vend.execute("SELECT DISTINCT symbol FROM eod_chain_paths")})
    spellings = sorted({s for n in names for s in (n, *ALIASES.get(n, ()))})
    conn = land._connect(land.DEFAULTS, "earnings")
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(spellings))
        cur.execute(
            f"SELECT act_symbol, date FROM earnings_calendar WHERE act_symbol IN ({marks}) "
            "AND date BETWEEN %s AND %s",
            [*spellings, *REPORT_SPAN],
        )
        dolt: dict[str, set[date]] = defaultdict(set)
        for sym, d in cur.fetchall():
            dolt[sym].add(d)
        cur.execute(
            "SELECT date, COUNT(*) FROM earnings_calendar WHERE date BETWEEN %s AND %s GROUP BY date",
            REPORT_SPAN,
        )
        counts = dict(cur.fetchall())
    finally:
        conn.close()
    holes = dolt_holes(counts, REPORT_SPAN[0], min(REPORT_SPAN[1], date.today() - timedelta(days=1)))
    filled: dict[date, set[str]] = {}
    for d in holes:
        try:
            filled[d] = set(fet.parse(fet.fetch_day(d)))
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                print(f"Nasdaq refused ({exc.code}) at {d}: no cache written", file=sys.stderr)
                return 1
            raise
        time.sleep(fet.PAUSE_S)
    dates = merge_reports(dolt, filled, names)
    out = _out_dir() / "earnings_dates.json"
    body = {
        "sources": "Dolt earnings_calendar; Nasdaq's public calendar for Dolt's empty trading days",
        "holes": [d.isoformat() for d in holes],
        "dates": {s: [d.isoformat() for d in v] for s, v in dates.items()},
    }
    out.write_text(json.dumps(body), encoding="utf-8")
    hit = sum(1 for d, syms in filled.items() for s in names if s in syms)
    print(json.dumps({"names": len(names), "holes": len(holes), "filled_from_nasdaq": hit, "file": str(out)}))
    return 0


def cmd_study(args) -> int:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    vend = _store()
    status = {
        (s, e): st for s, e, st in vend.execute("SELECT symbol, expiration, status FROM eod_chain_paths")
    }
    names = sorted({s for s, _ in status})
    if args.names:
        names = [n for n in names if n in set(args.names.split(","))]
    reports = load_reports(names, _out_dir() / "earnings_dates.json")
    hist = store.connect(tpaths.history_db())
    result: dict = {"windows": {}, "references": {}}
    for wname, (w0, w1, end) in WINDOWS.items():
        years = ((w1 - w0).days + 1) / 365.25
        w = {"edge": {a: {} for a in ARMS}, "stress": {a: {} for a in ARMS}, "trades": dict.fromkeys(ARMS, 0),
             "dropped": {}, "names": {}}  # fmt: skip
        for sym in names:
            cover = weekly_coverage(status, sym, w0, w1)
            if cover < WEEKLY_COVERAGE:
                w["dropped"][sym] = round(cover, 3)
                continue
            pcr.SYMBOL = sym
            nd = load_name(vend, hist, sym, reports[sym], w0, end)
            row = {}
            for arm in ARMS:
                trades, stats = run_arm(nd, arm, w0, w1, end, FRAC)
                stressed, _ = run_arm(nd, arm, w0, w1, end, STRESS_FRAC)
                e, s = name_edge(trades, years), name_edge(stressed, years)
                row[arm] = {"edge": e, "stress": s, "trades": len(trades), "stats": stats,
                            "how": dict(statistics_count(t["how"] for t in trades))}  # fmt: skip
                if sym not in FUNDS:
                    w["edge"][arm][sym], w["stress"][arm][sym] = e, s
                    w["trades"][arm] += len(trades)
            if sym in FUNDS:
                result["references"].setdefault(wname, {})[sym] = {a: row[a]["edge"] for a in ARMS}
            w["names"][sym] = row
            print(wname, sym, {a: (row[a]["trades"], round(row[a]["edge"], 2)) for a in ARMS}, flush=True)
        result["windows"][wname] = w
    result["verdict"] = judge(result["windows"]["design"], result["windows"]["holdout"])
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    for arm, v in result["verdict"].items():
        print(
            arm, v["verdict"], {k: x for k, x in v["checks"].items() if not x}, "holm" if v["holm_ok"] else ""
        )
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
    sub.add_parser("reports").set_defaults(fn=cmd_reports)
    s = sub.add_parser("study")
    s.add_argument("--json")
    s.add_argument("--names", help="comma-separated, for a quick look; the verdict needs every name")
    s.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
