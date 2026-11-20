"""Item 95: the ladder's paper rules at one-minute resolution: the four paper arms, 2016 on.

The declaration is research/edge-hypotheses-plan.md section 95; this script runs it. Its inputs are
public bars (Alpaca's one-minute bars, `scripts/fetch_minute_bars.py`; item 26's daily series) and
the 3-month bill, so nothing here is licensed vendor data. Results stay under
`~/.cherrypick/data/research/item95/`, never the repo.

**The rules are paper's, imported.** Every level, fill, target, size and money figure comes from
`cherrypick.ladder.rules`, the module the paper loop runs (pure: it imports `core.fees` alone). This
script only drives them in `paper_loop.run_once`'s order: at each quote, sells first, then buys,
`chain` and `session` buying on while the next level is through and a slot is free, `once` one lot
a session. `test_share_ladder_item95_script.py` replays the paper ledger's own recorded ticks
through `run_arm` and requires the lots paper recorded.

**Speed without numpy.** A level only moves at an event, so a session whose lowest ask stays over
it is passed in one comparison, and a bar likewise. A lot's exit does not depend on other lots, so
it is found once, when it is bought: the first session whose highest bid reaches its target, by a
sparse table over the sessions' highest bids, then the bar and the quote inside it.

- `smoke`: two funds over 2016, judged by nothing (`smoke.json`), so a crash shows in minutes.
- `study`: the declared run (`results.json`): both rules per arm, and what the plan reports.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import random
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home, progress, treasury
from cherrypick.core import stats as core_stats
from cherrypick.ladder import rules

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's bars, on one row per entry session per arm (lots across funds pooled).
RULE = {
    "t_bar": 2.0,
    "min_n": 300,
    "holdout_min_n": 100,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 95},
    "delta": False,
    "splits": ["2022-01-01"],
}

#: Item 26's timing test at minute resolution: per-fund edge over random-entry draws.
CONTROL_RULE = {
    "draws": 200,
    "seed": 95,
    "t_bar": 2.0,
    "alpha": 0.05,
    "min_funds": 8,
    "min_lots": 300,
    "split": "2022-01-01",
    "halves": [["2016-01-04", "2019-01-01"], ["2019-01-01", "2022-01-01"]],
    "cost_half_spread": 0.015,
}

#: Paper's settings (the ladder's config.example.json) and the replay's declared constants.
SPEC = {
    "lot_dollars": 1000.0,
    "min_shares": 3,
    "cap": 3,
    "drop": rules.DROP,
    "gain": rules.GAIN,
    "hours": ("08:15", "20:00"),
    "regular_close": "16:00",
    "half_spread": 0.005,
    "daily_disagree": 0.03,
    "first_session": "2016-01-04",
    "rate_stale_days": 10,
    "sessions_a_year": 252,
    "hospital_days": 18,
}

#: arm: (entry rule, target rule), paper's four.
ARMS = {
    "session": ("session", "fixed"),
    "chain": ("chain", "fixed"),
    "once": ("once", "fixed"),
    "decay": ("once", "decay"),
}

#: Item 26's 17 judged funds (one per index and leverage).
FUNDS = (
    "AGQ", "ERX", "FAS", "NUGT", "QLD", "SOXL", "SPXL", "SSO", "TECL",
    "TMF", "TNA", "TQQQ", "UCO", "UDOW", "UGL", "USD", "YINN",
)  # fmt: skip

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`, after the
#: minute bars are fetched by hand (`scripts/fetch_minute_bars.py fetch`).
CHAIN = (
    {
        "step": "smoke",
        "kind": "script",
        "args": ["smoke"],
        "output": "{home}/data/research/item95/smoke.json",
    },
    {
        "step": "study",
        "kind": "script",
        "args": ["study"],
        "output": "{home}/data/research/item95/results.json",
    },
)

INF = float("inf")
NY = ZoneInfo("America/New_York")


def out_dir() -> Path:
    d = home.home() / "data" / "research" / "item95"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- the tape
@dataclass
class Session:
    day: str
    ref: float
    a: int  # first quote
    b: int  # one past the last quote
    min_ask: float
    max_bid: float
    bars: list  # (start, end, min_ask, max_bid) per bar, in order
    last_bid: float
    f: float = 1.0  # the raw price over the split-adjusted one: every later split's ratio multiplied


@dataclass
class Tape:
    """One fund's quotes: flat bid/ask lists, each quote's bar end (the first quote of the next bar),
    and the sessions over them. Built from bars (`from_bars`) or recorded ticks (tests)."""

    bid: list = field(default_factory=list)
    ask: list = field(default_factory=list)
    bar_end: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    _table: list | None = None

    def add_session(
        self, day: str, ref: float, bars: list[list[tuple[float, float]]], f: float = 1.0
    ) -> None:
        """`bars`: per bar, its quotes as (bid, ask) in path order, in the session's raw prices; `f`
        the session's split factor (raw over adjusted), so a price carries across a split as x f'/f."""
        a = len(self.bid)
        spans = []
        for quotes in bars:
            s = len(self.bid)
            for bd, ak in quotes:
                self.bid.append(bd)
                self.ask.append(ak)
            e = len(self.bid)
            self.bar_end.extend([e] * (e - s))
            spans.append((s, e, min(q[1] for q in quotes), max(q[0] for q in quotes)))
        b = len(self.bid)
        if b == a:
            return
        self.sessions.append(
            Session(
                day,
                ref,
                a,
                b,
                min(x[2] for x in spans),
                max(x[3] for x in spans),
                spans,
                self.bid[b - 1],
                f,
            )
        )
        self._table = None
        self._changes = None

    def next_change(self, k: int) -> int:
        """The first session index after `k` whose split factor differs from session k's (or the end)."""
        if getattr(self, "_changes", None) is None:
            n = len(self.sessions)
            out = [n] * n
            for j in range(n - 2, -1, -1):
                out[j] = j + 1 if self.sessions[j + 1].f != self.sessions[j].f else out[j + 1]
            self._changes = out
        return self._changes[k]

    # sparse table over the sessions' highest bids: the first session from `k` reaching `t`
    def _build(self) -> None:
        level = [s.max_bid for s in self.sessions]
        table = [level]
        width = 1
        while width * 2 <= len(level):
            prev = table[-1]
            table.append([max(prev[i], prev[i + width]) for i in range(len(prev) - width)])
            width *= 2
        self._table = table

    def first_session_reaching(self, k: int, stop: int, t: float) -> int | None:
        """The first session index in [k, stop) whose highest bid is at or over `t`: one descending
        pass, skipping each power-of-two block whose highest bid stays under `t`."""
        if self._table is None:
            self._build()
        pos = k
        for j in range(len(self._table) - 1, -1, -1):
            width = 1 << j
            if pos + width <= stop and self._table[j][pos] < t:
                pos += width
        return pos if pos < stop else None

    @property
    def days(self) -> list[str]:
        if getattr(self, "_days", None) is None or len(self._days) != len(self.sessions):
            self._days = [s.day for s in self.sessions]
        return self._days


def path(o: float, h: float, low: float, c: float) -> list[float]:
    """A bar's prices in the declared order: open, the extreme nearer the open, the other, close."""
    first, second = (low, h) if (o - low) <= (h - o) else (h, low)
    return [o, first, second, c]


def quotes_of(prices: list[float], half: float) -> list[tuple[float, float]]:
    return [(round(p - half, 4), round(p + half, 4)) for p in prices]


# --------------------------------------------------------------------------- the arm
@dataclass
class Lot:
    session: int
    day: str
    i: int
    level: float
    price: float
    shares: int
    target_rule: str
    f: float = 1.0  # its buy session's split factor: the lot's price and shares are in those terms
    exit_i: int | None = None
    exit_session: int | None = None
    sell: float | None = None  # the fill, in the exit session's raw price
    open: bool = True

    def in_buy_terms(self, price: float, f: float) -> float:
        """A raw price of a session with split factor `f`, in the lot's own (buy-session) terms."""
        return price * self.f / f


def _scan(tape: Tape, s: Session, start: int, test) -> int | None:
    """The first quote index >= start in session `s` passing `test(bid, ask)`, bar by bar."""
    for bs, be, bmin, bmax in s.bars:
        if be <= start:
            continue
        if not test(None, None, bmin, bmax):
            continue
        for k in range(max(bs, start), be):
            if test(tape.bid[k], tape.ask[k], None, None):
                return k
    return None


def _target(lot: Lot, s: Session) -> float:
    """The lot's sell limit in session `s`: paper's target for its age, in s's raw prices. Across a
    split the resting order is restated at the split's ratio, rounded to the cent the safe way."""
    age = rules.age_days(date.fromisoformat(lot.day), date.fromisoformat(s.day))
    t = rules.target(lot.target_rule, cost=lot.price, shares=lot.shares, age=age, gain=SPEC["gain"])
    return t if s.f == lot.f else rules.ceil_cent(t * s.f / lot.f)


def find_exit(tape: Tape, lot: Lot, eligible: int, stop: int) -> None:
    """Set the lot's exit: the first quote from `eligible` (a later bar than its buy) whose bid
    reaches its target for that session's age, among sessions before `stop`; else it stays open.
    Between one session and the next step of the decay schedule or the next split the target is
    constant, so the sessions in between are searched in one go."""
    sessions = tape.sessions
    k = lot.session
    while k < stop:
        s = sessions[k]
        start = eligible if k == lot.session else s.a
        if start < s.b:
            t = _target(lot, s)
            if s.max_bid >= t:
                hit = _scan(
                    tape, s, start, lambda bd, ak, bmin, bmax, t=t: (bmax >= t) if bd is None else bd >= t
                )
                if hit is not None:
                    lot.exit_i, lot.exit_session = hit, k
                    prev = tape.bid[hit - 1] if hit > s.a else None
                    lot.sell = rules.sell_fill(t, tape.bid[hit], prev)
                    return
        k += 1
        if k >= stop:
            return
        seg_stop = min(stop, tape.next_change(k))
        if lot.target_rule != "fixed":
            age = rules.age_days(date.fromisoformat(lot.day), date.fromisoformat(sessions[k].day))
            edge = next((d for d in (rules.DECAY_HOLD_DAYS, rules.DECAY_BREAKEVEN_DAYS) if age <= d), None)
            if edge is not None:
                limit = date.fromordinal(date.fromisoformat(lot.day).toordinal() + edge).isoformat()
                seg_stop = min(seg_stop, bisect.bisect_right(tape.days, limit, lo=k))
        nxt = tape.first_session_reaching(k, seg_stop, _target(lot, sessions[k]))
        k = nxt if nxt is not None else seg_stop


def run_arm(tape: Tape, entry: str, target_rule: str, first: int = 0, stop: int | None = None) -> list[Lot]:
    """Paper's order over sessions [first, stop): at each quote, the lots whose exit it is sell
    first, then buys while the next level is through and a slot is free."""
    stop = len(tape.sessions) if stop is None else stop
    lots: list[Lot] = []
    open_: list[Lot] = []
    anchor = None  # chain's last lot bought, split-adjusted (its fill over its session's factor)
    for k in range(first, stop):
        s = tape.sessions[k]
        bought_today = 0
        i = s.a
        while True:
            ex = min((x.exit_i for x in open_ if x.exit_session == k), default=INF)
            bi = INF
            blocked = (entry == "once" and bought_today) or len(open_) >= SPEC["cap"]
            if not blocked:
                last_buy = None if anchor is None else round(anchor * s.f, 6)
                level = rules.buy_level(
                    entry, ref_close=s.ref, last_buy=last_buy, drop=SPEC["drop"], bought_today=bought_today
                )
                if level is not None and rules.lot_shares(SPEC["lot_dollars"], level) >= SPEC["min_shares"]:
                    if s.min_ask <= level:
                        hit = _scan(
                            tape,
                            s,
                            i,
                            lambda bd, ak, bmin, bmax, lv=level: (bmin <= lv) if bd is None else ak <= lv,
                        )
                        bi = INF if hit is None else hit
            if ex <= bi and ex < INF:
                for x in [x for x in open_ if x.exit_i == ex]:
                    x.open = False
                    open_.remove(x)
                if not open_:
                    anchor = None
                i = ex
                continue
            if bi < INF:
                prev = tape.ask[bi - 1] if bi > s.a else None
                fill = rules.buy_fill(level, tape.ask[bi], prev)
                lot = Lot(
                    k, s.day, bi, level, fill, rules.lot_shares(SPEC["lot_dollars"], level), target_rule, s.f
                )
                find_exit(tape, lot, tape.bar_end[bi], stop)
                lots.append(lot)
                open_.append(lot)
                bought_today += 1
                if entry == "chain":
                    anchor = fill / s.f
                i = bi
                continue
            break
    return lots


# --------------------------------------------------------------------------- money and measures
def lot_money(tape: Tape, lot: Lot, stop: int) -> dict:
    """Closed: paper's money at its sale. Open at the window's end: marked at the last bid. A sale
    or mark after a split is put back in the lot's own terms (same dollars, the buy's share count)."""
    end = tape.sessions[stop - 1]
    closed = lot.exit_i is not None
    if closed:
        sell = lot.in_buy_terms(lot.sell, tape.sessions[lot.exit_session].f)
    else:
        sell = lot.in_buy_terms(end.last_bid, end.f)
    m = rules.money(lot.shares, lot.price, sell)
    last_day = tape.sessions[lot.exit_session].day if closed else end.day
    days = rules.age_days(date.fromisoformat(lot.day), date.fromisoformat(last_day))
    return {
        "date": lot.day,
        "net": m["net_pnl"],
        "bp": round(lot.shares * lot.price, 2),
        "days": days,
        "closed": closed,
        "same_session": closed and lot.exit_session == lot.session,
    }


def pct_year(rows: list[dict], sessions: int) -> float:
    if sessions <= 0:
        return 0.0
    budget = SPEC["cap"] * SPEC["lot_dollars"]
    return 100.0 * sum(r["net"] for r in rows) / budget / (sessions / SPEC["sessions_a_year"])


def session_range(tape: Tape, lo: str | None, hi: str | None) -> tuple[int, int]:
    days = tape.days
    a = 0 if lo is None else bisect.bisect_left(days, lo)
    b = len(days) if hi is None else bisect.bisect_left(days, hi)
    return a, b


# --------------------------------------------------------------------------- the control
def control_lots(
    tape: Tape, counts: dict[str, int], target_rule: str, rng: random.Random, first: int, stop: int
):
    """One draw: per year, as many lots as the arm opened, at a random bar's close on distinct
    sessions, at the ask, sized at that price; the arm's exit; a draw whose cap is full is skipped."""
    by_year: dict[str, list[int]] = {}
    for k in range(first, stop):
        by_year.setdefault(tape.sessions[k].day[:4], []).append(k)
    picks = []
    for year, n in counts.items():
        pool = by_year.get(year, [])
        for k in sorted(rng.sample(pool, min(n, len(pool)))):
            s = tape.sessions[k]
            bs, be, _, _ = s.bars[rng.randrange(len(s.bars))]
            picks.append((k, be - 1))
    picks.sort(key=lambda p: p[1])
    lots, open_ = [], []
    for k, i in picks:
        open_ = [x for x in open_ if x.exit_i is None or x.exit_i > i]
        if len(open_) >= SPEC["cap"]:
            continue
        price = tape.ask[i]
        shares = rules.lot_shares(SPEC["lot_dollars"], price)
        if shares < SPEC["min_shares"]:
            continue
        lot = Lot(k, tape.sessions[k].day, i, price, price, shares, target_rule, tape.sessions[k].f)
        find_exit(tape, lot, tape.bar_end[i], stop)
        lots.append(lot)
        open_.append(lot)
    return lots


def measure(
    tape: Tape, entry: str, target_rule: str, lo: str, hi: str | None, parts: dict, rng_key: str
) -> dict:
    """One run over [lo, hi): the arm's lots and the control's mean, % of budget a year per part."""
    first, stop = session_range(tape, lo, hi)
    if stop <= first:
        return {}
    lots = run_arm(tape, entry, target_rule, first, stop)
    rows = [lot_money(tape, x, stop) for x in lots]
    counts: dict[str, int] = {}
    for x in lots:
        counts[x.day[:4]] = counts.get(x.day[:4], 0) + 1
    out = {}
    draws = {name: [] for name in parts}
    rng = random.Random(f"{CONTROL_RULE['seed']}:{rng_key}")
    for _ in range(CONTROL_RULE["draws"]):
        c_rows = [lot_money(tape, x, stop) for x in control_lots(tape, counts, target_rule, rng, first, stop)]
        for name, (plo, phi) in parts.items():
            a, b = session_range(tape, plo, phi)
            a, b = max(a, first), min(b, stop)
            sel = [
                r for r in c_rows if (plo is None or r["date"] >= plo) and (phi is None or r["date"] < phi)
            ]
            draws[name].append(pct_year(sel, b - a))
    for name, (plo, phi) in parts.items():
        a, b = session_range(tape, plo, phi)
        a, b = max(a, first), min(b, stop)
        sel = [r for r in rows if (plo is None or r["date"] >= plo) and (phi is None or r["date"] < phi)]
        arm = pct_year(sel, b - a)
        ctrl = statistics.mean(draws[name]) if draws[name] else 0.0
        out[name] = {
            "pct_year": arm,
            "control": ctrl,
            "edge": arm - ctrl,
            "lots": len(sel),
            "closed": sum(r["closed"] for r in sel),
            "sessions": b - a,
        }
    return {"parts": out, "rows": rows, "lots": lots}


# --------------------------------------------------------------------------- the verdicts
def stat(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.mean(values) if values else 0.0
    sd = statistics.stdev(values) if n > 1 else 0.0
    t = mean / (sd / n**0.5) if sd > 0 else (INF if mean > 0 else 0.0)
    p = 1.0 - core_stats.t_cdf(t, n - 1) if n > 1 and t != INF else (0.0 if t > 0 else 1.0)
    return {
        "n": n,
        "mean": mean,
        "t": t,
        "p": p,
        "positive_share": sum(v > 0 for v in values) / n if n else 0.0,
    }


def judge_control(per_fund: dict[str, dict]) -> dict:
    """per_fund: {fund: {arm: {part: measure}}}, parts design, holdout, half1, half2, cost."""
    out = {}
    for arm in ARMS:

        def edges(part: str, arm: str = arm) -> list[float]:
            return [v[arm][part]["edge"] for v in per_fund.values() if part in v.get(arm, {})]

        def closed(part: str, arm: str = arm) -> int:
            return sum(v[arm][part]["closed"] for v in per_fund.values() if part in v.get(arm, {}))

        d, h = stat(edges("design")), stat(edges("holdout"))
        checks = {
            "mean_positive": d["mean"] > 0,
            "t_above_bar": d["t"] > CONTROL_RULE["t_bar"],
            "more_than_half_positive": d["positive_share"] > 0.5,
            "first_half_positive": stat(edges("half1"))["mean"] > 0,
            "second_half_positive": stat(edges("half2"))["mean"] > 0,
            "cost_positive": stat(edges("cost"))["mean"] > 0,
            "holdout_mean_positive": h["mean"] > 0,
            "holdout_more_than_half_positive": h["positive_share"] > 0.5,
        }
        out[arm] = {
            "design": d,
            "holdout": h,
            "checks": checks,
            "closed": {"design": closed("design"), "holdout": closed("holdout")},
            "enough": d["n"] >= CONTROL_RULE["min_funds"] and closed("design") >= CONTROL_RULE["min_lots"],
        }
    holm = core_stats.holm({a: v["design"]["p"] for a, v in out.items()}, CONTROL_RULE["alpha"])
    for arm, v in out.items():
        v["holm_ok"] = holm[arm]
        c = v["checks"]
        design_ok = all(c[k] for k in c if not k.startswith("holdout")) and v["holm_ok"]
        if not v["enough"]:
            v["verdict"] = "no verdict"
        elif not design_ok:
            v["verdict"] = "fail"
        else:
            v["verdict"] = (
                "pass" if c["holdout_mean_positive"] and c["holdout_more_than_half_positive"] else "fail"
            )
    return out


def kit_rows(rows: list[dict], rate) -> list[dict]:
    """The kit's rows: one per entry session, lots across funds pooled; days weighted by cost."""
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    out = []
    for d, rs in sorted(by_day.items()):
        bp = sum(r["bp"] for r in rs)
        out.append(
            {
                "date": d,
                "net": round(sum(r["net"] for r in rs), 2),
                "bp": bp,
                "days": sum(r["bp"] * r["days"] for r in rs) / bp,
                "rate": rate(d),
                "trades": len(rs),
            }
        )
    return out


def combined(kit: dict, control: dict) -> dict:
    """An arm passes only when both rules pass; a no verdict on either, and no fail, is no verdict."""
    out = {}
    for arm in ARMS:
        both = {kit["verdict"][arm], control[arm]["verdict"]}
        out[arm] = "fail" if "fail" in both else ("pass" if both == {"pass"} else "no verdict")
    return out


# --------------------------------------------------------------------------- inputs
def bill_reader(fred_path: Path | None = None):
    """The 3-month bill on a session: `core.treasury` before it, else FRED's DGS3MO; stale past
    `rate_stale_days`, the run stops rather than guess."""
    fred_path = fred_path or home.data_dir("public") / "fred" / "DGS3MO.csv"
    fred: list[tuple[str, float]] = []
    if fred_path.exists():
        with fred_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    fred.append((row["observation_date"], float(row["DGS3MO"])))
                except (KeyError, ValueError):
                    continue
    days = [d for d, _ in fred]
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session in seen:
            return seen[session]
        got = treasury.yield_before(session, "3m")
        if got is not None and (date.fromisoformat(session) - got[0]).days <= SPEC["rate_stale_days"]:
            seen[session] = got[1] / 100.0
            return seen[session]
        k = bisect.bisect_left(days, session) - 1
        if (
            k < 0
            or (date.fromisoformat(session) - date.fromisoformat(days[k])).days > SPEC["rate_stale_days"]
        ):
            raise SystemExit(f"no 3-month bill within {SPEC['rate_stale_days']} days before {session}")
        seen[session] = fred[k][1] / 100.0
        return seen[session]

    return rate


def daily_closes(conn: sqlite3.Connection, symbol: str) -> dict[str, float]:
    """Item 26's daily series: the broker's close unless missing or over 1% from Yahoo's."""

    def load(source: str) -> dict[str, float]:
        return {
            d[:10]: c
            for d, c in conn.execute(
                "SELECT start, close FROM bars WHERE symbol = ? AND source = ? AND interval = '1d'",
                (symbol, source),
            )
        }

    broker, yahoo = load("broker"), load("yahoo")
    out = {}
    for d in sorted(set(broker) | set(yahoo)):
        b, y = broker.get(d), yahoo.get(d)
        out[d] = b if b is not None and (y is None or abs(b / y - 1) <= 0.01) else y
    return out


def split_ratios(conn: sqlite3.Connection, symbol: str) -> dict[str, float]:
    """Item 26's split events, {ex-date: new shares per old} (2-for-1 is 2, 1-for-10 is 0.1)."""
    return {
        d[:10]: num / den
        for d, num, den in conn.execute(
            "SELECT date, numerator, denominator FROM splits WHERE symbol = ?", (symbol,)
        )
        if num and den
    }


def tape_from_bars(
    rows,
    closes: dict[str, float],
    half: float,
    first_day: str,
    last_day: str | None = None,
    hours: tuple[str, str] | None = None,
    splits: dict[str, float] | None = None,
) -> Tape:
    """A fund's tape from raw (start 'YYYY-MM-DDTHH:MM', o, h, l, c) rows in time order, inside the
    hours. `closes` are split-adjusted (item 26's series) and `splits` {ex-date: ratio} turn them raw:
    session D's factor f is every later split's ratio multiplied, its reference the previous close x
    f (the close as the exchange posts it on D, adjusted only on a split's own day). A session whose
    last regular-hours bar closes over `daily_disagree` from its raw daily close is skipped and
    counted. With no `splits` the rows are taken as split-adjusted and every factor is 1."""
    lo, hi = hours or SPEC["hours"]
    days = sorted(closes)
    split_days = sorted(splits or {})

    def factor(day: str) -> float:
        f = 1.0
        for d in split_days[bisect.bisect_right(split_days, day) :]:
            f *= splits[d]
        return f

    tape = Tape()
    skipped = {"no_reference": 0, "no_daily_close": 0, "disagrees": 0}
    current, bars, last_regular = None, [], None

    def flush():
        if current is None or not bars:
            return
        if current < first_day or (last_day and current > last_day):
            return
        j = bisect.bisect_left(days, current)
        if j >= len(days) or days[j] != current:
            skipped["no_daily_close"] += 1
            return
        if j == 0:
            skipped["no_reference"] += 1
            return
        f = factor(current)
        if last_regular is None or abs(last_regular / (closes[current] * f) - 1) > SPEC["daily_disagree"]:
            skipped["disagrees"] += 1
            return
        tape.add_session(current, round(closes[days[j - 1]] * f, 4), bars, f)

    for start, o, h, low, c in rows:
        day, hhmm = start[:10], start[11:16]
        if day != current:
            flush()
            current, bars, last_regular = day, [], None
        if hhmm < SPEC["regular_close"] and hhmm >= "09:30":
            last_regular = c
        if lo <= hhmm < hi:
            bars.append(quotes_of(path(o, h, low, c), half))
    flush()
    tape.skipped = skipped
    return tape


def minute_rows(conn: sqlite3.Connection, symbol: str):
    return conn.execute(
        "SELECT start, open, high, low, close FROM bars WHERE symbol = ? ORDER BY start", (symbol,)
    ).fetchall()


def hourly_rows(conn: sqlite3.Connection, symbol: str):
    """Item 26's hourly bars: the broker's where it has one, else Yahoo's, a Yahoo bar outside
    09:30-16:00 clipped to 3% beyond its open and close (item 26's rule for its bad prints)."""
    got = {}
    sql = "SELECT start, open, high, low, close FROM bars WHERE symbol = ? AND source = ? AND interval = '1h'"
    for start, o, h, low, c in conn.execute(sql, (symbol, "yahoo")):
        if not ("09:30" <= start[11:16] < "16:00"):
            h = min(h, max(o, c) * 1.03)
            low = max(low, min(o, c) * 0.97)
        got[start] = (start, o, h, low, c)
    for start, o, h, low, c in conn.execute(sql, (symbol, "broker")):
        got[start] = (start, o, h, low, c)
    return [got[k] for k in sorted(got)]


def fingerprint(counts: dict) -> dict:
    blob = json.dumps(counts, sort_keys=True).encode()
    return {"counts": counts, "sha256": hashlib.sha256(blob).hexdigest()}


# --------------------------------------------------------------------------- reported
def reported(tape: Tape, lots: list[Lot], rows: list[dict], stop: int) -> dict:
    """Never judged: drawdown marked at each session's last bid, longest open, most capital tied,
    same-session share, the group's 18-day terms, net by year."""
    n = stop
    mark = [0.0] * n
    realized = [0.0] * n
    tied = [0.0] * n
    for lot, r in zip(lots, rows, strict=True):
        end = lot.exit_session if r["closed"] else n
        for k in range(lot.session, min(end, n)):
            s = tape.sessions[k]
            mark[k] += lot.shares * (lot.in_buy_terms(s.last_bid, s.f) - lot.price)
            tied[k] += lot.shares * lot.price
        if r["closed"]:
            realized[lot.exit_session] += r["net"]
    equity, peak, dd, run = [], 0.0, 0.0, 0.0
    for k in range(n):
        run += realized[k]
        e = run + mark[k]
        equity.append(e)
        peak = max(peak, e)
        dd = min(dd, e - peak)
    end_day = tape.sessions[n - 1].day
    by_year: dict[str, float] = {}
    for r in rows:
        by_year[r["date"][:4]] = round(by_year.get(r["date"][:4], 0.0) + r["net"], 2)
    closed = [r for r in rows if r["closed"]]
    return {
        "lots": len(rows),
        "closed": len(closed),
        "open_at_end": len(rows) - len(closed),
        "max_drawdown": round(dd, 2),
        "longest_hold_days": max((r["days"] for r in rows), default=0),
        "most_capital_tied": round(max(tied, default=0.0), 2),
        "same_session_share": (sum(r["same_session"] for r in closed) / len(closed)) if closed else None,
        "sold_within_18d": sum(1 for r in closed if r["days"] <= SPEC["hospital_days"]),
        "open_past_18d": sum(
            1
            for r in rows
            if not r["closed"]
            and rules.age_days(date.fromisoformat(r["date"]), date.fromisoformat(end_day))
            > SPEC["hospital_days"]
        ),
        "net_by_year": by_year,
    }


def buy_and_hold(closes: dict[str, float], lo: str, hi: str | None) -> float | None:
    days = [d for d in sorted(closes) if d >= lo and (hi is None or d < hi)]
    if len(days) < 2:
        return None
    years = len(days) / SPEC["sessions_a_year"]
    return 100.0 * ((closes[days[-1]] / closes[days[0]]) ** (1 / years) - 1)


# --------------------------------------------------------------------------- the run
def run(
    funds: list[str], minute_db: Path, daily_db: Path, rate, rep: progress.Reporter, *, last_day=None
) -> dict:
    split = CONTROL_RULE["split"]
    first = SPEC["first_session"]
    (h1lo, h1hi), (h2lo, h2hi) = CONTROL_RULE["halves"]
    per_fund: dict[str, dict] = {}
    pooled: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    reports: dict[str, dict] = {}
    counts: dict[str, dict] = {}
    missing = []
    mconn = sqlite3.connect(f"file:{minute_db}?mode=ro", uri=True)
    dconn = sqlite3.connect(f"file:{daily_db}?mode=ro", uri=True)
    rep.split("funds", funds, unit="funds")
    for fund in funds:
        rep.at("funds", fund)
        closes = daily_closes(dconn, fund)
        rows = minute_rows(mconn, fund)
        if not rows or rows[0][0][:10] > first[:7] + "-31":
            missing.append(fund)
            rep.advance("funds", key=fund, current=fund)
            continue
        splits = split_ratios(dconn, fund)
        tape = tape_from_bars(rows, closes, SPEC["half_spread"], first, last_day, splits=splits)
        wide = tape_from_bars(rows, closes, CONTROL_RULE["cost_half_spread"], first, last_day, splits=splits)
        counts[fund] = {
            "bars": len(rows),
            "first": rows[0][0],
            "last": rows[-1][0],
            "sessions": len(tape.sessions),
            "skipped": tape.skipped,
        }
        del rows
        per_fund[fund], reports[fund] = {}, {}
        for arm, (entry, target_rule) in ARMS.items():
            rep.at("funds", f"{fund} {arm}")
            full = measure(
                tape,
                entry,
                target_rule,
                first,
                None,
                {"design": (None, split), "holdout": (split, None)},
                f"{fund}:{arm}",
            )
            h1 = measure(tape, entry, target_rule, h1lo, h1hi, {"half1": (None, None)}, f"{fund}:{arm}:h1")
            h2 = measure(tape, entry, target_rule, h2lo, h2hi, {"half2": (None, None)}, f"{fund}:{arm}:h2")
            cost = measure(
                wide, entry, target_rule, first, split, {"cost": (None, None)}, f"{fund}:{arm}:cost"
            )
            per_fund[fund][arm] = {
                **full["parts"],
                **h1.get("parts", {}),
                **h2.get("parts", {}),
                **cost.get("parts", {}),
            }
            pooled[arm] += full["rows"]
            reports[fund][arm] = reported(tape, full["lots"], full["rows"], len(tape.sessions))
        reports[fund]["buy_and_hold_pct_year"] = {
            "design": buy_and_hold(closes, first, split),
            "holdout": buy_and_hold(closes, split, None),
        }
        rep.advance("funds", key=fund, current=fund)
    mconn.close()
    dconn.close()
    kit = kit_rule.evaluate(RULE, {arm: kit_rows(rs, rate) for arm, rs in pooled.items()})
    control = judge_control(per_fund)
    return {
        "verdict": combined(kit, control),
        "kit": kit,
        "control": control,
        "per_fund": per_fund,
        "reported": reports,
        "funds_missing": missing,
        "inputs": fingerprint(counts),
    }


def hourly_check(daily_db: Path, rep: progress.Reporter) -> dict:
    """Reported, never judged: the same arms on item 26's hourly bars over their span."""
    conn = sqlite3.connect(f"file:{daily_db}?mode=ro", uri=True)
    out = {}
    names = ("SOXL", "TECL", "TQQQ")
    rep.split("hourly", names, unit="funds")
    for fund in names:
        rows = hourly_rows(conn, fund)
        if not rows:
            rep.advance("hourly", key=fund)
            continue
        # an hourly bar starting 08:00 holds 08:15, so item 26's 08:00-20:00 cut
        tape = tape_from_bars(
            rows, daily_closes(conn, fund), SPEC["half_spread"], rows[0][0][:10], hours=("08:00", "20:00")
        )
        out[fund] = {"span": [tape.sessions[0].day, tape.sessions[-1].day] if tape.sessions else None}
        for arm, (entry, target_rule) in ARMS.items():
            lots = run_arm(tape, entry, target_rule)
            rows_ = [lot_money(tape, x, len(tape.sessions)) for x in lots]
            out[fund][arm] = {"pct_year": pct_year(rows_, len(tape.sessions)), "lots": len(rows_)}
        rep.advance("hourly", key=fund)
    conn.close()
    return out


def default_paths() -> tuple[Path, Path]:
    return out_dir() / "minute_bars.db", home.home() / "data" / "research" / "item26" / "bars.db"


def last_whole_month_end(today: date) -> str:
    """The declared window's end: the last day of the month before `today`'s (New York date), so a
    month still being traded is never read."""
    return date.fromordinal(today.replace(day=1).toordinal() - 1).isoformat()


def cmd_study(args) -> int:
    minute_db, daily_db = default_paths()
    smoke = args.step == "smoke"
    funds = ["SOXL", "TQQQ"] if smoke else list(FUNDS)
    job = "item95-smoke" if smoke else "item95-study"
    last_day = "2016-12-31" if smoke else last_whole_month_end(datetime.now(NY).date())
    with progress.Reporter(job) as rep:
        block = run(funds, minute_db, daily_db, bill_reader(), rep, last_day=last_day)
        if not smoke:
            block["hourly_check"] = hourly_check(daily_db, rep)
    doc = {
        "item": 95,
        "window": [SPEC["first_session"], last_day],
        "spec": {**SPEC, "hours": list(SPEC["hours"])},
        "rule": RULE,
        "control_rule": CONTROL_RULE,
        **block,
    }
    if smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    path = Path(args.out) if args.out else out_dir() / ("smoke.json" if smoke else "results.json")
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name in ("smoke", "study"):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
