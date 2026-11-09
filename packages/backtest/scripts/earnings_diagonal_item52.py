"""Item 52, diagonals across an earnings report (docs/edge-hypotheses-plan.md): the request files and
the study.

    python packages/backtest/scripts/earnings_diagonal_item52.py requests [--out-dir DIR]
    python packages/backtest/scripts/earnings_diagonal_item52.py study [--host H] [--port P] [--out-dir DIR]

Both read item 31's event list and calendar (results/research/item31/events.json, calendar.json) and
import item 31's and item 47's functions (the events, timing, entry and reaction sessions, the paired
control, the monthly expiry, the expected move, the clustered test, the name halves), so the three
studies cannot drift apart.

**The structure.** For an event, the *report weekly* is the first weekly (a Friday, the Thursday when
that Friday is a holiday) on or after the reaction session and within 9 calendar days of the report
that the vendor lists; the *monthly* is the first standard monthly at least 14 calendar days after the
weekly. The control is item 31's control session (`control_for`) under the same rule, closed the
session after.

**Which weeklies exist** is read from the store, never fetched to find out: the end-of-day request for
(name, entry session, weekly) is answered `complete` when the vendor lists it and `empty` when it does
not (the way item 36's listing probes work). `requests` walks the candidate Fridays in order: a
candidate the store holds as empty is skipped, the first one not yet answered is requested whole, and
the study uses the first one answered complete. So a fetch followed by a second `requests` run settles
any name whose first Friday turned out to be unlisted; the study refuses while either run would still
write something.

`requests` writes, to results/research/item52/:

- `eod-chain-requests.json` (`fetch-chains`): per event the weekly on the entry, reaction and next
  session (the day-2 variant; not once the weekly has expired) and the monthly on those three and on
  the weekly's expiration session (the hold-short-to-expiry variant's long exit); per control the
  weekly and the monthly on the entry and the next session. Less what the store already answers.
- `oi-plan.json` (`fetch-oi`): the entry session of each event, less the name-days the open-interest
  store already holds. The end-of-day chain store carries no open interest and the declaration's floor
  needs it.

`study` reads the stores and Dolt and writes `trades.json` and `results.json` to the same directory.
It refuses, with a count of what is missing and a non-zero exit, while any request above is unanswered.

**Deltas.** The end-of-day chain store holds bid, ask, last, volume and sizes: no greeks. So every
delta is solved from the mid with `core.american` (Bjerksund-Stensland, rate 0 as the broker's greeks
are read elsewhere in the suite, the name's trailing-year dividend yield from the broker's dividend
history over the entry close), the central difference of the American value in the stock. The solve
is slow, so a strike's Black-Scholes delta first ranks the strikes and only the four nearest a target
get the American solve; the listed strike nearest the target delta is the American one's.

**The 25-delta risk reversal** is the 2026-10-13 expected-move fly study's `rr25` (scripts/
emfly_history.py): the call implied volatility at the delta nearest 0.25 less the put's at the delta
nearest 0.25 (each within 0.10 of it), over the at-the-money implied volatility (the mean of the call
and put at the strike nearest spot). Here it is read on the report weekly's entry chain with the same
solver, because Dolt samples most pre-2024 sessions on three days a week and the entry session is
often not among them.

**The cheap back month** is the monthly's at-the-money implied volatility from Dolt `options.option_chain`
(the standard monthly nearest 30 days out within 14 to 60, the strike nearest the close, the mean of
the call's and put's `vol`), on the latest Dolt day up to two sessions before the entry session,
against the same measure on the name's trailing 252 sessions before the entry, leaving out the five
sessions either side of every report of the name up to this one. The reading is the share of those
days at or below the entry's; fewer than 60 days of history is no reading (the event keeps its whole
arms and leaves the cheap cuts, counted). `cheap` is the bottom tercile of the design window's readings.

The fills, the stress, the variants, the tests and the verdicts follow the declaration; the choices the
declaration leaves open are named at their constants below. Everything written goes under the backtest
data dir, never into the repo: the inputs and everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import american, bs, fees, robustness
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


e31 = _load("earnings_item31", "earnings_item31.py")
e47 = _load("earnings_strangle_item47", "earnings_strangle_item47.py")

WEEKLY_DAYS = 9  # the weekly expires within this many calendar days of the report
MONTHLY_GAP = 14  # the monthly is at least this many calendar days after the weekly
MIN_CLOSE = 10.0
MAX_LEG_SPREAD = 0.15  # of the leg's mid
MIN_EM_DOLLARS = 0.90
OI_FLOOR = 2000  # the earnings module's min_combined_open_interest (config.example.json), both rights
DELTAS = {"long": 0.40, "short": 0.20, "wing": 0.10}
RR_DELTA, RR_TOLERANCE = 0.25, 0.10
RATE = 0.0  # the suite's broker-greeks convention (docs: memory "broker greeks: zero carry")
BS_SHORTLIST = 4  # strikes per target that get the American solve
DELTA_STEP = 0.01  # the central difference, as a share of spot
HISTORY_SESSIONS, EXCLUDE_AROUND = 252, 5
MIN_HISTORY = 60  # fewer days than this and the cheap reading is None (our floor; declaration silent)
MEASURE_DTE = (14, 60, 30)  # the monthly the history reads: within the band, nearest the target
DESIGN, HOLDOUT = e47.DESIGN, e47.HOLDOUT
HALF_START, MIN_TRADES, T_BAR = e47.HALF_START, e47.MIN_TRADES, e47.T_BAR
ALPHA = e31.ALPHA
#: The replay's corrections, oldest first (`orchestrator.studyversion`). A result written before the
#: last of them is stale on the studies board.
REPLAY_CORRECTIONS = (
    {"on": "2026-11-07", "what": "an open-interest day the vendor answers 'no data' counts as answered, "
     "so the study runs and drops that report as no_open_interest_day, as it drops any report with no "
     "open-interest day", "why": "the study refused to run while any planned day was missing, and a few "
     "name-days come back 'no data' on every fetch (most of them one ticker the vendor does not list "
     "under its dotted spelling), so it could never run", "decided": "user, 2026-11-07: re-queue item 52 "
     "until it runs", "before_read": True},
)  # fmt: skip
STRESS_FEES = 1.5
# Half the spread, uncapped: the 2026-11-05 review found the 15%-of-mid cap light on wide wings, and a
# capped half-spread would not be half the spread there.
STRESS_COSTS = {"tastytrade_costs": {"slippage_frac_of_spread": 0.5, "slippage_cap_frac_of_mid": None}}
BASE_ARMS = ("A-call", "A-put", "B1", "B2", "C")
ARMS = tuple(a + suffix for a in BASE_ARMS for suffix in ("", "|cheap"))
# (side, expiry, right, delta role): +1 long, -1 short; "m" the monthly, "w" the report weekly
SPECS = {
    "A-call": [(1, "m", "C", "long"), (-1, "w", "C", "short")],
    "A-put": [(1, "m", "P", "long"), (-1, "w", "P", "short")],
    "B2": [(-1, "w", "C", "short"), (-1, "w", "P", "short"), (1, "m", "C", "wing"), (1, "m", "P", "wing")],
}
SPECS["B1"] = SPECS["A-call"] + SPECS["A-put"]
VARIANTS = ("day2", "expiry")


# ------------------------------------------------------------------------------------------------
# Pure: which expiries, which requests.


def grid_candidates(entry: date, reaction: date, report: date) -> list[date]:
    """The weekly-grid expiries (Fridays, the Thursday before a holiday Friday) on or after the
    reaction session and within WEEKLY_DAYS of the report, nearest first."""
    return [d for d in e31.weekly_candidates(entry) if d >= reaction and (d - report).days <= WEEKLY_DAYS]


def choose_weekly(candidates: list[date], status_of) -> tuple[date | None, bool]:
    """(the weekly, whether the store has not answered it yet): the first candidate the store does not
    hold as empty. None when every candidate is empty."""
    for d in candidates:
        st = status_of(d)
        if st == "empty":
            continue
        return d, st is None
    return None, False


def needs(
    entry: date, close: date, weekly: date, monthly: date, *, day2: date | None
) -> list[tuple[date, date]]:
    """(session, expiration) pairs: an event passes `day2` and gets the day-2 and expiry-session
    chains; a control (`day2=None`) only the entry and the closing session."""
    out: dict[tuple[date, date], None] = {}
    for s in (entry, close):
        out[(s, weekly)] = None
        out[(s, monthly)] = None
    if day2 is not None:
        if weekly >= day2:
            out[(day2, weekly)] = None
        out[(day2, monthly)] = None
        out[(weekly, monthly)] = None
    return list(out)


def _structure_needs(sym, entry, report, timing, status, day2_wanted):
    """(weekly, monthly, [(session, expiration)], why) for one entry session."""
    reaction = e31.reaction_session(report, timing)
    cands = grid_candidates(entry, reaction, report)
    if not cands:
        return None, None, [], "no_weekly_candidate"
    weekly, _pending = choose_weekly(cands, lambda d: status.get((sym, entry.isoformat(), d.isoformat())))
    if weekly is None:
        return None, None, [], "weekly_unlisted"
    monthly = e47.monthly_expiry(weekly, MONTHLY_GAP)
    close = reaction if day2_wanted else cal.next_trading_day(entry)
    day2 = cal.next_trading_day(reaction) if day2_wanted else None
    return weekly, monthly, needs(entry, close, weekly, monthly, day2=day2), None


def plan(events: list[dict], reports: dict, span, status: dict) -> dict:
    """What the study reads: {"chains": [(SYM, session, expiration)], "oi": {session: [SYM]},
    "counts": {...}}. `status` maps (SYM, session, expiration) to the store's answer."""
    chains: dict[tuple, None] = {}
    oi: dict[str, list[str]] = defaultdict(list)
    counts: Counter = Counter()
    for e in events:
        sym = e["symbol"].upper()
        entry = date.fromisoformat(e["entry_session"])
        report = date.fromisoformat(e["report_date"])
        oi[e["entry_session"]].append(sym)
        _w, _m, pairs, why = _structure_needs(sym, entry, report, e["timing"], status, True)
        if why:
            counts[f"event_{why}"] += 1
        for s, x in pairs:
            chains.setdefault((sym, s.isoformat(), x.isoformat()), None)
        c = e31.control_for(e, reports.get(e["symbol"], []), span)
        if c is None:
            counts["no_control_session"] += 1
            continue
        c_entry, c_report = date.fromisoformat(c["entry_session"]), date.fromisoformat(c["report_date"])
        _w, _m, pairs, why = _structure_needs(sym, c_entry, c_report, c["timing"], status, False)
        if why:
            counts[f"control_{why}"] += 1
        for s, x in pairs:
            chains.setdefault((sym, s.isoformat(), x.isoformat()), None)
    return {
        "chains": list(chains),
        "oi": {d: sorted(set(v)) for d, v in sorted(oi.items())},
        "counts": counts,
    }


def missing(planned: dict, status: dict, oi_done: set) -> dict:
    """The part of the plan the stores do not answer."""
    chains = [k for k in planned["chains"] if k not in status]
    oi = {d: [s for s in syms if (s, d) not in oi_done] for d, syms in planned["oi"].items()}
    return {"chains": chains, "oi": {d: v for d, v in oi.items() if v}}


# ------------------------------------------------------------------------------------------------
# Pure: deltas and the risk reversal.


def trailing_yield(dividends: list, day: date, spot: float) -> float:
    """Dividends gone ex in the year to `day` over the close (the yield the American solve carries)."""
    since = (day - timedelta(days=365)).isoformat()
    paid = sum(float(a) for ex, a in dividends if since < str(ex)[:10] <= day.isoformat())
    return paid / spot if spot > 0 else 0.0


def _mid(q) -> float:
    return (q[0] + q[1]) / 2.0


class DeltaBook:
    """One chain's deltas at the entry close, computed lazily. `nearest` ranks a right's strikes by the
    Black-Scholes delta of the mid (cheap) and gives the BS_SHORTLIST nearest the target the American
    solve; the answer is the American delta's nearest."""

    def __init__(self, chain: dict, spot: float, years: float, div: float):
        self.chain, self.spot, self.years, self.div = chain, spot, years, div
        self._bs: dict = {}
        self._am: dict = {}

    def strikes(self, right: str) -> list[float]:
        return sorted(k for (k, r), q in self.chain.items() if r == right and e47._two_sided(q))

    def _bs_delta(self, right: str, k: float) -> float | None:
        key = (right, k)
        if key not in self._bs:
            iv = bs.implied_vol(self.spot, k, self.years, _mid(self.chain[(k, right)]), right, RATE, self.div)
            d = bs.delta(self.spot, k, self.years, iv, right, RATE, self.div) if iv and iv > 0.0005 else None
            self._bs[key] = d
        return self._bs[key]

    def am(self, right: str, k: float) -> tuple[float, float] | None:
        """(|delta|, implied vol) by the American model, or None when no vol prices the mid."""
        key = (right, k)
        if key not in self._am:
            out = None
            iv = american.implied_vol(
                self.spot, k, self.years, _mid(self.chain[(k, right)]), right, RATE, self.div
            )
            if iv and iv > 0.0005:
                h = DELTA_STEP * self.spot
                up = american.price(self.spot + h, k, self.years, iv, right, RATE, self.div)
                dn = american.price(self.spot - h, k, self.years, iv, right, RATE, self.div)
                out = (abs((up - dn) / (2.0 * h)), iv)
            self._am[key] = out
        return self._am[key]

    def nearest(self, right: str, target: float) -> tuple[float, float, float] | None:
        """(strike, |delta|, iv): the listed strike whose American |delta| is nearest `target`, the
        lower strike on a tie."""
        ranked = []
        for k in self.strikes(right):
            d = self._bs_delta(right, k)
            if d is not None:
                ranked.append((abs(abs(d) - target), k))
        best = None
        for _gap, k in sorted(ranked)[:BS_SHORTLIST]:
            got = self.am(right, k)
            if got is None:
                continue
            cand = (abs(got[0] - target), k, got[0], got[1])
            if best is None or cand[:2] < best[:2]:
                best = cand
        return None if best is None else (best[1], best[2], best[3])

    def atm_iv(self) -> float | None:
        """The mean of the call's and put's implied volatility at the strike nearest spot with both
        sides two-sided."""
        both = sorted(set(self.strikes("C")) & set(self.strikes("P")))
        if not both:
            return None
        k = min(both, key=lambda s: (abs(s - self.spot), s))
        c, p = self.am("C", k), self.am("P", k)
        return None if c is None or p is None else (c[1] + p[1]) / 2.0

    def rr25(self) -> float | None:
        """emfly_history's `rr25`: (call IV at |delta| nearest 0.25 - put IV likewise) / ATM IV."""
        c, p = self.nearest("C", RR_DELTA), self.nearest("P", RR_DELTA)
        atm = self.atm_iv()
        if c is None or p is None or not atm:
            return None
        if abs(c[1] - RR_DELTA) > RR_TOLERANCE or abs(p[1] - RR_DELTA) > RR_TOLERANCE:
            return None
        return (c[2] - p[2]) / atm


def tercile_cuts(values: list[float]) -> tuple[float, float] | None:
    vals = sorted(v for v in values if v is not None)
    if len(vals) < 3:
        return None
    lo, hi = statistics.quantiles(vals, n=3, method="inclusive")
    return lo, hi


def c_side(rr: float | None, cuts: tuple[float, float] | None) -> str | None:
    """Arm C's side: the call diagonal when puts are priced richer (the risk reversal in the design
    window's bottom tercile), the put diagonal when calls are (the top); the middle takes no trade."""
    if rr is None or cuts is None:
        return None
    if rr <= cuts[0]:
        return "C"
    if rr >= cuts[1]:
        return "P"
    return None


def arm_spec(arm: str, side: str | None = None) -> list[tuple] | None:
    if arm == "C":
        return None if side is None else SPECS["A-call" if side == "C" else "A-put"]
    return SPECS[arm]


def build_legs(book_w: DeltaBook, book_m: DeltaBook, spec: list[tuple], weekly: date, monthly: date):
    """(legs, why): each leg (side, expiry, right, strike) at the listed strike nearest its delta; the
    entry quotes must be two-sided with a spread of at most 15% of the mid."""
    legs = []
    for side, which, right, role in spec:
        book, exp = (book_m, monthly) if which == "m" else (book_w, weekly)
        got = book.nearest(right, DELTAS[role])
        if got is None:
            return None, "no_delta_strike"
        q = book.chain[(got[0], right)]
        if not e47._two_sided(q):
            return None, "leg_unquoted"
        if (q[1] - q[0]) > MAX_LEG_SPREAD * _mid(q):
            return None, "leg_spread_over_15pct"
        legs.append((side, exp, right, got[0]))
    return legs, None


# ------------------------------------------------------------------------------------------------
# Pure: the fills.


def price_position(symbol: str, legs: list[tuple], entries: list[tuple], exits: list[dict]) -> dict:
    """One position's net in whole dollars. `legs` are (side, expiry, right, strike), side +1 long and
    -1 short; `entries` each leg's entry (bid, ask); `exits` each leg's exit, either
    {"mode": "quote", "quote": (bid, ask) or None, "spot": s} or {"mode": "settle", "spot": s}.

    Opened at mid, a long paying and a short receiving the suite's slippage on each leg; closed at mid
    plus the same where the leg has a two-sided quote. A leg without one is closed at its intrinsic
    value against the exit close plus its entry spread: a short bought back at intrinsic + spread, a
    long sold at intrinsic - spread (floored at zero), neither charged slippage, counted. A settled
    short pays its intrinsic value, no slippage, and `fees.ic_expire_fee` if it finished in the money.
    Fees per leg each way from core.fees; the stress takes 1.5x fees and half the spread."""
    e_quotes = [{"bid": b, "ask": a} for b, a in entries]
    entry_cash, exit_cash = 0.0, 0.0
    x_quotes, unquoted, active, active_longs, itm_settled = [], 0, 0, 0, 0
    for (side, _exp, right, strike), (b, a), x in zip(legs, entries, exits, strict=True):
        entry_cash -= side * 100.0 * (b + a) / 2.0
        intrinsic = e47.intrinsic(strike, right, x["spot"])
        if x["mode"] == "settle":
            value = intrinsic
            itm_settled += intrinsic > 0
        else:
            active += 1
            active_longs += side > 0
            q = x["quote"]
            if e47._two_sided(q):
                value = (q[0] + q[1]) / 2.0
                x_quotes.append({"bid": q[0], "ask": q[1]})
            else:
                unquoted += 1
                value = intrinsic + (a - b) if side < 0 else max(0.0, intrinsic - (a - b))
        exit_cash += side * 100.0 * value
    n_short = sum(1 for leg in legs if leg[0] < 0)
    fee = fees.ic_open_fee(symbol, 1, legs=len(legs), sell_legs=n_short, ndigits=4)
    if active:
        fee += fees.ic_close_fee(symbol, 1, legs=active, sell_legs=active_longs, ndigits=4)
    fee += fees.ic_expire_fee(itm_settled)
    out = {
        "debit": -entry_cash,
        "exit_cash": exit_cash,
        "fees": fee,
        "exit_unquoted_legs": unquoted,
    }
    for label, cfg, f in (("working", {}, 1.0), ("stress", STRESS_COSTS, STRESS_FEES)):
        slip = fees.slippage_dollars(e_quotes, 1, cfg) + (
            fees.slippage_dollars(x_quotes, 1, cfg) if x_quotes else 0.0
        )
        out[label] = {"net": entry_cash + exit_cash - slip - f * fee, "slippage": slip}
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the cheap back month.


_SESSIONS: list[date] = []


def _sessions() -> list[date]:
    if not _SESSIONS:
        _SESSIONS.extend(e31.sessions_between(date(2018, 1, 1), date(2027, 12, 31)))
    return _SESSIONS


def report_windows(reports: list[date], through: date) -> set[date]:
    """The sessions within EXCLUDE_AROUND sessions either side of each report up to `through`."""
    ss = _sessions()
    out: set[date] = set()
    for r in reports:
        if r > through:
            continue
        i = bisect.bisect_left(ss, r)
        out.update(ss[max(0, i - EXCLUDE_AROUND) : i + EXCLUDE_AROUND + 1])
    return out


def trailing_values(series: dict[date, float], entry: date, excluded: set[date]) -> list[float]:
    """The series' values on the HISTORY_SESSIONS sessions before `entry`, less the excluded ones."""
    ss = _sessions()
    i = bisect.bisect_left(ss, entry)
    window = set(ss[max(0, i - HISTORY_SESSIONS) : i])
    return [v for d, v in series.items() if d in window and d not in excluded]


def cheap_reading(value: float | None, history: list[float]) -> float | None:
    """The share of the history at or below `value`; None without a value or MIN_HISTORY days."""
    if value is None or len(history) < MIN_HISTORY:
        return None
    return sum(1 for h in history if h <= value) / len(history)


def entry_measure(series: dict[date, float], entry: date) -> float | None:
    """The measure on the latest day up to two sessions before the entry (Dolt samples some years on
    three days a week)."""
    ss = _sessions()
    i = bisect.bisect_right(ss, entry)
    for d in reversed(ss[max(0, i - 3) : i]):
        if d in series:
            return series[d]
    return None


def monthly_atm_iv(rows: list[tuple], spot: float, session: date) -> float | None:
    """Dolt rows (expiration, strike, 'Call'|'Put', vol) of one day: the standard monthly (third Friday)
    within the measure's band and nearest its target, the strike nearest `spot` with both vols, the
    mean."""
    lo, hi, target = MEASURE_DTE
    exps = sorted(
        {
            r[0]
            for r in rows
            if r[0].weekday() == 4 and 15 <= r[0].day <= 21 and lo <= (r[0] - session).days <= hi
        },
        key=lambda e: (abs((e - session).days - target), e),
    )
    if not exps:
        return None
    by_k: dict[float, dict[str, float]] = defaultdict(dict)
    for e, k, cp, vol in rows:
        if e == exps[0] and vol and vol > 0:
            by_k[float(k)][cp] = float(vol)
    both = [k for k, v in by_k.items() if "Call" in v and "Put" in v]
    if not both:
        return None
    k = min(both, key=lambda s: (abs(s - spot), s))
    if abs(k / spot - 1.0) > 0.1:
        return None
    return (by_k[k]["Call"] + by_k[k]["Put"]) / 2.0


# ------------------------------------------------------------------------------------------------
# Pure: statistics and the verdict.


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def arm_stats(trades: list[dict], *, bootstrap: bool) -> dict:
    """One arm in one window: the date-clustered tests of the net and of the paired difference, the
    halves, the stress, the net without the best five trades, the worst trade, the debit and the net
    as a share of the debit."""
    n = None if bootstrap else 0
    nets = [t["net"] for t in trades]
    pairs = [t for t in trades if t.get("control_net") is not None]
    debits = [t for t in trades if t["debit"] > 0]
    return {
        "trades": len(trades),
        "pairs": len(pairs),
        "net": e31.cluster_t(nets, [t["entry_session"] for t in trades], resamples=n),
        "pair": e31.cluster_t(
            [t["net"] - t["control_net"] for t in pairs], [t["entry_session"] for t in pairs], resamples=n
        ),
        "net_by_time_half": [_mean(t["net"] for t in trades if t["time_half"] == h) for h in (0, 1)],
        "net_by_name_half": [_mean(t["net"] for t in trades if t["name_half"] == h) for h in (0, 1)],
        "stress_mean": _mean(t["stress_net"] for t in trades),
        "without_best_five": _mean(e31.drop_best(nets)),
        "worst": min(nets) if nets else None,
        "mean_debit": _mean(t["debit"] for t in trades),
        "debit_trades": len(debits),
        "mean_share_of_debit": _mean(t["net"] / t["debit"] for t in debits),
        "exit_unquoted_legs": sum(t["exit_unquoted_legs"] for t in trades),
        "variants": {
            v: {
                "trades": sum(1 for t in trades if t.get(f"net_{v}") is not None),
                "mean_net": _mean(t[f"net_{v}"] for t in trades if t.get(f"net_{v}") is not None),
            }
            for v in VARIANTS
        },
    }


def holm_flags(stats: dict[str, dict]) -> tuple[dict, dict, dict, dict]:
    """(p_net, p_pair, holm_net, holm_pair) across every judged arm: an arm under the design floor
    counts p = 1."""
    p_net, p_pair = {}, {}
    for arm, s in stats.items():
        enough = s["design"]["trades"] >= MIN_TRADES["design"]
        p_net[arm] = s["design"]["net"]["p"] if enough else 1.0
        p_pair[arm] = s["design"]["pair"]["p"] if enough else 1.0
    return p_net, p_pair, core_stats.holm(p_net, alpha=ALPHA), core_stats.holm(p_pair, alpha=ALPHA)


def _pos(x) -> bool:
    return x is not None and x > 0


def verdict(design: dict, holdout: dict, holm_net: bool, holm_pair: bool) -> dict:
    """The declared rule. Fewer than 150 design trades is no verdict. A design pass needs every check;
    then the holdout's mean net and paired difference both above zero, and with fewer than 100 holdout
    trades that is no verdict. Holm gates the net test only, as declared; the paired test is t > 2
    unadjusted. The stricter reading (Holm on both) is reported as `verdict_if_holm_on_both`."""
    if design["trades"] < MIN_TRADES["design"]:
        return {"verdict": "no verdict", "why": f"{design['trades']} design trades < {MIN_TRADES['design']}"}
    t_net = _pos(design["net"]["mean"]) and design["net"]["t"] > T_BAR
    t_pair = _pos(design["pair"]["mean"]) and design["pair"]["t"] > T_BAR
    common = {
        "net_each_time_half": all(_pos(x) for x in design["net_by_time_half"]),
        "net_each_name_half": all(_pos(x) for x in design["net_by_name_half"]),
        "stress_positive": _pos(design["stress_mean"]),
        "best_five_removed_positive": _pos(design["without_best_five"]),
    }
    checks = {"net_t": t_net, "net_holm": holm_net, "pair_t": t_pair, **common}
    strict = {**checks, "pair_holm": holm_pair}
    out = {"design_checks": checks}
    held = _pos(holdout["net"]["mean"]) and _pos(holdout["pair"]["mean"])

    def read(c: dict) -> str:
        if not all(c.values()):
            return "fail"
        if holdout["trades"] < MIN_TRADES["holdout"]:
            return "no verdict"
        return "pass" if held else "fail"

    out["verdict_if_holm_on_both"] = read(strict)
    if holdout["trades"] < MIN_TRADES["holdout"] and all(checks.values()):
        return {"verdict": "no verdict", "why": "holdout under its trade floor", **out}
    return {"verdict": read(checks), "holdout_positive": held, **out}


def evaluate(trades: list[dict]) -> dict:
    """Every judged arm (the five and their cheap cuts): both windows' statistics, Holm across all ten
    arms, the verdict."""
    stats: dict[str, dict] = {}
    for arm in ARMS:
        base, cheap = arm.partition("|")[0], arm.endswith("|cheap")
        ts = [t for t in trades if t["arm"] == base and (not cheap or t["cheap"])]
        stats[arm] = {
            w: arm_stats([t for t in ts if t["window"] == w], bootstrap=(w == "design"))
            for w in ("design", "holdout")
        }
    p_net, p_pair, h_net, h_pair = holm_flags(stats)
    return {
        arm: {
            **s,
            "p_net": p_net[arm],
            "p_pair": p_pair[arm],
            **verdict(s["design"], s["holdout"], h_net[arm], h_pair[arm]),
        }
        for arm, s in stats.items()
    }


# ------------------------------------------------------------------------------------------------
# The steps.


def _inputs():
    return e47._inputs(e47._dir(None, "item31"))


def _stores():
    """(eod chain connection, open-interest connection or None), read-only."""
    from cherrypick.backtest import chainfetch, oifetch, settings

    cfg = settings.load(None)
    chains = sqlite3.connect(f"file:{chainfetch.db_path(cfg).as_posix()}?mode=ro", uri=True)
    oi_path = oifetch.db_path(cfg)
    oi = sqlite3.connect(f"file:{oi_path.as_posix()}?mode=ro", uri=True) if oi_path.exists() else None
    return chains, oi


def _status(conn) -> dict:
    return {
        (s, d, e): st
        for s, d, e, st in conn.execute(
            "SELECT symbol, session, expiration, status FROM eod_chain_requests "
            "WHERE status IN ('complete', 'empty')"
        )
    }


def _oi_done(conn) -> set:
    """The name-days the open-interest store answers: complete, empty, or refused with the vendor's
    'no data' (permanent; a retry asks again and gets the same). A day refused for any other reason
    (a server error, a timeout) is not answered, so the study waits for it."""
    if conn is None:
        return set()
    return {
        (s, d)
        for s, d in conn.execute(
            "SELECT symbol, session FROM oi_days WHERE status IN ('complete', 'empty') "
            "OR (status = 'refused' AND reason LIKE 'no_data%')"
        )
    }


def _read_stores():
    chains, oi = _stores()
    try:
        return _status(chains), _oi_done(oi)
    finally:
        chains.close()
        if oi is not None:
            oi.close()


def cmd_requests(args) -> int:
    events, _doc, reports, span = _inputs()
    status, oi_done = _read_stores()
    planned = plan(events, reports, span, status)
    todo = missing(planned, status, oi_done)
    d = e47._dir(args.out_dir, "item52")
    path, oi_path = d / "eod-chain-requests.json", d / "oi-plan.json"
    path.write_text(
        json.dumps(
            {"requests": [{"symbol": s, "session": a, "expiration": x} for s, a, x in todo["chains"]]}
        ),
        encoding="utf-8",
    )
    oi_path.write_text(json.dumps({"sessions": todo["oi"]}), encoding="utf-8")
    print(
        json.dumps(
            {
                "events": len(events),
                "planned": len(planned["chains"]),
                "already_stored": len(planned["chains"]) - len(todo["chains"]),
                "requests": len(todo["chains"]),
                "oi_name_days": sum(len(v) for v in todo["oi"].values()),
                "drops": dict(planned["counts"]),
                "files": [str(path), str(oi_path)],
            }
        )
    )
    return 0


def load_dividends() -> dict[str, list]:
    from cherrypick.technicals import paths as tpaths

    doc = json.loads(tpaths.tastytrade_dividends().read_text(encoding="utf-8"))
    return {s: v.get("dividends") or [] for s, v in (doc.get("symbols") or {}).items()}


def load_iv_history(names: list[str], bars: dict, host: str, port: int) -> dict[str, dict[date, float]]:
    """Per name, the monthly ATM IV on each Dolt day from 2018-12 on (Dolt `options.option_chain`).

    The table's key leads with the date, so a name-only filter scans the whole table: this asks one
    day at a time for all the names at once (a seek per name inside the day)."""
    import mysql.connector

    spelled = {sp: n for n in names for sp in e31._spellings(n)}
    marks = ",".join(["%s"] * len(spelled))
    days = sorted({d for b in bars.values() for d in b if d >= date(2018, 12, 1)})
    out: dict[str, dict[date, float]] = {n: {} for n in names}
    conn = mysql.connector.connect(host=host, port=port, user="root", connection_timeout=30)
    try:
        for day in days:
            cur = conn.cursor()
            cur.execute(
                "SELECT act_symbol, expiration, strike, call_put, vol FROM options.option_chain "
                f"WHERE date = %s AND act_symbol IN ({marks}) AND vol IS NOT NULL "
                "AND DAYOFWEEK(expiration) = 6 AND DAYOFMONTH(expiration) BETWEEN 15 AND 21",
                (day, *spelled),
            )
            by_name: dict[str, list] = defaultdict(list)
            for sym, exp, k, cp, vol in cur.fetchall():
                by_name[spelled[sym]].append((exp, k, cp, vol))
            cur.close()
            for name, rows in by_name.items():
                bar = bars.get(name, {}).get(day)
                iv = monthly_atm_iv(rows, bar[3], day) if bar else None
                if iv is not None:
                    out[name][day] = iv
    finally:
        conn.close()
    return out


def oi_total(oi_conn, sym: str, session: date, expiration: date) -> int | None:
    """The expiry's combined open interest (calls and puts) on the entry session, None without a
    complete day."""
    if oi_conn is None:
        return None
    day = oi_conn.execute(
        "SELECT status FROM oi_days WHERE symbol = ? AND session = ?", (sym, session.isoformat())
    ).fetchone()
    if day is None:
        return None
    row = oi_conn.execute(
        "SELECT SUM(open_interest) FROM oi_contracts WHERE symbol = ? AND session = ? AND expiration = ?",
        (sym, session.isoformat(), expiration.isoformat()),
    ).fetchone()
    return int(row[0] or 0)


def setup(conn, sym: str, entry: date, report: date, timing: str, status: dict, bars: dict, divs):
    """The structure at one entry session, or (None, why): the weekly, the monthly, their entry
    chains, the spot and the delta books."""
    reaction = e31.reaction_session(report, timing)
    cands = grid_candidates(entry, reaction, report)
    if not cands:
        return None, "no_weekly_candidate"
    weekly, _ = choose_weekly(cands, lambda d: status.get((sym, entry.isoformat(), d.isoformat())))
    if weekly is None:
        return None, "weekly_unlisted"
    monthly = e47.monthly_expiry(weekly, MONTHLY_GAP)
    bar = bars.get(entry)
    if not bar:
        return None, "no_bars"
    spot = bar[3]
    cw, cm = e47.chain(conn, sym, entry, weekly), e47.chain(conn, sym, entry, monthly)
    if not cw:
        return None, "weekly_unlisted"
    if not cm:
        return None, "monthly_unlisted"
    if divs is None:
        return None, "no_dividend_record"
    q = trailing_yield(divs, entry, spot)
    return {
        "sym": sym, "entry": entry, "report": report, "reaction": reaction, "weekly": weekly,
        "monthly": monthly, "spot": spot, "cw": cw, "cm": cm, "div": q,
        "bw": DeltaBook(cw, spot, (weekly - entry).days / 365.0, q),
        "bm": DeltaBook(cm, spot, (monthly - entry).days / 365.0, q),
    }, None  # fmt: skip


def _exit_leg(conn, st: dict, leg: tuple, session: date, bars: dict, *, settle_short: bool):
    """The exit dict of one leg at `session`, or (None, why). A short whose expiry is before `session`
    (or the expiry variant) has settled at that expiry's close."""
    side, exp, right, _k = leg
    sym = st["sym"]
    if side < 0 and (settle_short or exp < session):
        bar = bars.get(exp)
        return ({"mode": "settle", "spot": bar[3]}, None) if bar else (None, "no_exit_bar")
    bar = bars.get(session)
    if not bar:
        return None, "no_exit_bar"
    ch = e47.chain(conn, sym, session, exp)
    if not ch:
        return None, "no_exit_chain"
    return {"mode": "quote", "quote": ch.get((leg[3], right)), "spot": bar[3]}, None


def run_position(conn, st: dict, legs: list[tuple], bars: dict, exit_session: date, *, variants: bool):
    """The position's pricing at `exit_session` (the judged exit), and the two variants."""
    entries = [(st["cw"] if leg[1] == st["weekly"] else st["cm"])[(leg[3], leg[2])] for leg in legs]
    exits = []
    for leg in legs:
        x, why = _exit_leg(conn, st, leg, exit_session, bars, settle_short=False)
        if x is None:
            return None, why
        exits.append(x)
    p = price_position(st["sym"], legs, entries, exits)
    out = {
        "net": p["working"]["net"],
        "stress_net": p["stress"]["net"],
        "debit": p["debit"],
        "exit_unquoted_legs": p["exit_unquoted_legs"],
    }
    if variants:
        day2 = cal.next_trading_day(exit_session)
        for name, session, settle in (("day2", day2, False), ("expiry", st["weekly"], True)):
            xs = [_exit_leg(conn, st, leg, session, bars, settle_short=settle)[0] for leg in legs]
            if all(xs):
                out[f"net_{name}"] = price_position(st["sym"], legs, entries, xs)["working"]["net"]
    return out, None


def _split_free(bars: dict, sessions: list[date]) -> bool:
    closes = [bars[s][3] for s in sessions if s in bars]
    return all(
        e31.SPLIT_RATIO[0] < b / a < e31.SPLIT_RATIO[1] for a, b in zip(closes, closes[1:], strict=False)
    )


def study(conn, oi_conn, events, reports, span, status, bars, divs, iv_hist):
    """Every event's record, then every arm's trade with its control. Pure of the stores' paths."""
    drops: Counter = Counter()
    arm_drops: dict[str, Counter] = defaultdict(Counter)
    control_drops: dict[str, Counter] = defaultdict(Counter)
    recs = []
    for e in events:
        sym = e["symbol"]
        key = sym.upper()
        report = date.fromisoformat(e["report_date"])
        window = e47.window_of(report)
        if window is None:
            drops["outside_windows"] += 1
            continue
        entry = date.fromisoformat(e["entry_session"])
        b = bars.get(sym, {})
        if entry not in b:
            drops["no_bars"] += 1
            continue
        if b[entry][3] < MIN_CLOSE:
            drops["close_under_10"] += 1
            continue
        st, why = setup(conn, key, entry, report, e["timing"], status, b, divs.get(sym))
        if st is None:
            drops[why] += 1
            continue
        em_pct = e47.expected_move(st["cw"], st["spot"])
        if em_pct is None:
            drops["no_expected_move"] += 1
            continue
        if em_pct * st["spot"] < MIN_EM_DOLLARS:
            drops["expected_move_under_90c"] += 1
            continue
        oi = [oi_total(oi_conn, key, entry, st[x]) for x in ("weekly", "monthly")]
        if None in oi:
            drops["no_open_interest_day"] += 1
            continue
        if min(oi) < OI_FLOOR:
            drops["open_interest_under_floor"] += 1
            continue
        reaction = st["reaction"]
        day2, ex_day = cal.next_trading_day(reaction), st["weekly"]
        if not _split_free(b, sorted({entry, reaction, day2, ex_day})):
            drops["split_in_window"] += 1
            continue
        rr = st["bw"].rr25()
        hist = trailing_values(iv_hist.get(sym, {}), entry, report_windows(reports.get(sym, []), report))
        measure = entry_measure(iv_hist.get(sym, {}), entry)
        recs.append(
            {"e": e, "st": st, "window": window, "rr25": rr, "cheap_reading": cheap_reading(measure, hist)}
        )
    design = [r for r in recs if r["window"] == "design"]
    cuts = tercile_cuts([r["rr25"] for r in design if r["rr25"] is not None])
    cheap_cut = None
    readings = [r["cheap_reading"] for r in design if r["cheap_reading"] is not None]
    if len(readings) >= 3:
        cheap_cut = statistics.quantiles(readings, n=3, method="inclusive")[0]
    trades = []
    for r in recs:
        e, st = r["e"], r["st"]
        sym, key, b = e["symbol"], st["sym"], bars[e["symbol"]]
        report = st["report"]
        c = e31.control_for(e, reports.get(sym, []), span)
        cst, cwhy = None, "no_control_session"
        if c is not None:
            cst, cwhy = setup(
                conn, key, date.fromisoformat(c["entry_session"]), date.fromisoformat(c["report_date"]),
                c["timing"], status, b, divs.get(sym),
            )  # fmt: skip
        cheap = None if r["cheap_reading"] is None or cheap_cut is None else r["cheap_reading"] <= cheap_cut
        for arm in BASE_ARMS:
            side = c_side(r["rr25"], cuts) if arm == "C" else None
            spec = arm_spec(arm, side)
            if spec is None:
                arm_drops[arm]["risk_reversal_middle_or_missing"] += 1
                continue
            legs, why = build_legs(st["bw"], st["bm"], spec, st["weekly"], st["monthly"])
            if legs is None:
                arm_drops[arm][why] += 1
                continue
            t, why = run_position(conn, st, legs, b, st["reaction"], variants=True)
            if t is None:
                arm_drops[arm][why] += 1
                continue
            control_net = None
            if cst is None:
                control_drops[arm][cwhy] += 1
            else:
                cside = c_side(cst["bw"].rr25(), cuts) if arm == "C" else None
                cspec = arm_spec(arm, cside)
                cl, cwhy2 = (None, "risk_reversal_middle_or_missing") if cspec is None else build_legs(
                    cst["bw"], cst["bm"], cspec, cst["weekly"], cst["monthly"]
                )  # fmt: skip
                ct = None
                if cl is not None:
                    ct, cwhy2 = run_position(
                        conn, cst, cl, b, cal.next_trading_day(cst["entry"]), variants=False
                    )
                if ct is None:
                    control_drops[arm][cwhy2] += 1
                else:
                    control_net = ct["net"]
            trades.append(
                {
                    **t,
                    "symbol": sym,
                    "arm": arm,
                    "report_date": e["report_date"],
                    "entry_session": e["entry_session"],
                    "window": r["window"],
                    "cheap": cheap,
                    "control_net": control_net,
                    "time_half": 0 if report < HALF_START else 1,
                    "name_half": robustness.name_half(sym),
                }  # fmt: skip
            )
    return (
        trades,
        recs,
        {
            "drops": dict(drops),
            "arm_drops": {a: dict(v) for a, v in arm_drops.items()},
            "control_drops": {a: dict(v) for a, v in control_drops.items()},
            "cuts": {"rr25_terciles": cuts, "cheap_reading": cheap_cut},
        },
    )


def back_month_change(recs: list[dict], conn, bars: dict) -> dict:
    """The weekly's and the monthly's ATM implied volatility from the entry to the reaction close, per
    window: the rival check that the back month falls with the front."""
    out: dict[str, dict[str, list]] = {w: {"weekly": [], "monthly": []} for w in ("design", "holdout")}
    for r in recs:
        st = r["st"]
        bar = bars[r["e"]["symbol"]].get(st["reaction"])
        if not bar:
            continue
        for which, exp, book in (("weekly", st["weekly"], st["bw"]), ("monthly", st["monthly"], st["bm"])):
            ch = e47.chain(conn, st["sym"], st["reaction"], exp)
            if not ch or exp <= st["reaction"]:
                continue
            after = DeltaBook(ch, bar[3], (exp - st["reaction"]).days / 365.0, st["div"]).atm_iv()
            before = book.atm_iv()
            if after is not None and before is not None:
                out[r["window"]][which].append(after - before)
    return {w: {k: {"n": len(v), "mean_change": _mean(v)} for k, v in d.items()} for w, d in out.items()}


def verdict_words(arms: dict) -> dict:
    """Each arm's word as `evaluate` wrote it, with the reason where it gave one: the result file's
    top-level `verdict`, which the studies board and the register read."""
    out = {}
    for name, arm in arms.items():
        why = arm.get("why")
        out[name] = {"verdict": arm.get("verdict"), **({"why": why} if why else {})}
    return out


def cmd_study(args) -> int:
    events, _doc, reports, span = _inputs()
    chains, oi = _stores()
    status, oi_done = _status(chains), _oi_done(oi)
    planned = plan(events, reports, span, status)
    todo = missing(planned, status, oi_done)
    n_oi = sum(len(v) for v in todo["oi"].values())
    if todo["chains"] or n_oi:
        print(
            json.dumps(
                {
                    "refused": "requests not fetched",
                    "missing_chain_requests": len(todo["chains"]),
                    "missing_oi_name_days": n_oi,
                    "next": "run requests, fetch both files, run requests again until it writes none",
                }
            )
        )
        return 1
    names = sorted({e["symbol"] for e in events})
    bars = e47.load_bars(names, args.host, args.port)
    raw = load_dividends()
    divs = {n: next((raw[s] for s in sorted(e31._spellings(n)) if s in raw), None) for n in names}
    iv_hist = load_iv_history(names, bars, args.host, args.port)
    trades, recs, info = study(chains, oi, events, reports, span, status, bars, divs, iv_hist)
    arms = evaluate(trades)
    result = {
        **info,
        "arms": arms,
        # The studies board reads a top-level word per arm (the declaration template, 2026-10-29).
        "verdict": verdict_words(arms),
        "back_month_iv_change": back_month_change(recs, chains, bars),
        "names_per_year": {
            str(y): len({r["e"]["symbol"] for r in recs if r["e"]["report_date"].startswith(str(y))})
            for y in range(2020, 2027)
        },
        "events_kept": len(recs),
        "trades": len(trades),
    }
    d = e47._dir(args.out_dir, "item52")
    (d / "trades.json").write_text(json.dumps(trades, default=str), encoding="utf-8")
    (d / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "events_kept": len(recs),
                "trades": len(trades),
                "drops": info["drops"],
                "file": str(d / "results.json"),
            }
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
