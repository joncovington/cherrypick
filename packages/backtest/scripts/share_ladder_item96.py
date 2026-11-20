"""Item 96: the ladder's paper arms with overnight hours, 2024-09 on.

The declaration is research/edge-hypotheses-plan.md section 96; this script runs it. Its inputs are
public bars (Alpaca's one-minute bars: the consolidated tape in item 95's store and Blue Ocean's
overnight session in this item's, both from `scripts/fetch_minute_bars.py`; item 26's daily series
and split events) and the 3-month bill, so nothing here is licensed vendor data. Results stay
under `~/.cherrypick/data/research/item96/`, never the repo.

**Item 95's replay, copied.** The tape, fills, exits, splits and money are item 95's script's own
code, copied as the backtest package asks (a study keeps its own copy, never loading another's
script), driving `cherrypick.ladder.rules` in `paper_loop.run_once`'s order. What is new here is
the hours: each session can also hold the overnight bars before it (20:00 the evening before to
04:00, Blue Ocean) and the pre-market from 04:00, and every arm runs twice over the same sessions,
once in paper's hours and once in all of them.

- `smoke`: two funds over 2024-09 to 2024-12, judged by nothing (`smoke.json`).
- `study`: the declared run (`results.json`): both rules on the `session` arm, the rest reported.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
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

#: The kit's bars on the all-hours `session` arm, one row per entry session (lots across the judged
#: funds pooled). Too short a window to split: a pass is provisional.
RULE = {
    "t_bar": 2.0,
    "min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 96},
    "delta": False,
    "splits": [],
}

#: The hours' own test: per judged fund, the `session` arm's % of budget a year in all hours less
#: the same in paper's hours, over the same sessions.
PAIRED_RULE = {
    "arm": "session",
    "t_bar": 2.0,
    "min_funds": 8,
    "min_lots": 60,
    "min_night_share": 0.5,
    "cost_half_spread": 0.015,
}

SPEC = {
    "lot_dollars": 1000.0,
    "min_shares": 3,
    "cap": 3,
    "drop": rules.DROP,
    "gain": rules.GAIN,
    "paper_hours": ("08:15", "20:00"),
    "all_hours_day": ("04:00", "20:00"),
    "night": ("20:00", "04:00"),
    "regular_close": "16:00",
    "half_spread": 0.005,
    "daily_disagree": 0.03,
    "first_session": "2024-09-20",
    "rate_stale_days": 10,
    "sessions_a_year": 252,
}

#: arm: (entry rule, target rule), paper's four.
ARMS = {
    "session": ("session", "fixed"),
    "chain": ("chain", "fixed"),
    "once": ("once", "fixed"),
    "decay": ("once", "decay"),
}

#: Item 26's 17 judged funds; judged here only with overnight bars on half the window's nights.
FUNDS = (
    "AGQ", "ERX", "FAS", "NUGT", "QLD", "SOXL", "SPXL", "SSO", "TECL",
    "TMF", "TNA", "TQQQ", "UCO", "UDOW", "UGL", "USD", "YINN",
)  # fmt: skip

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`, after both
#: stores are fetched by hand (`scripts/fetch_minute_bars.py fetch [--feed boats]`).
CHAIN = (
    {
        "step": "smoke",
        "kind": "script",
        "args": ["smoke"],
        "output": "{home}/data/research/item96/smoke.json",
    },
    {
        "step": "study",
        "kind": "script",
        "args": ["study"],
        "output": "{home}/data/research/item96/results.json",
    },
)

INF = float("inf")
NY = ZoneInfo("America/New_York")


def out_dir() -> Path:
    d = home.home() / "data" / "research" / "item96"
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
    tags: list = field(default_factory=list)  # per quote: night, pre or day (item 96's addition)
    _table: list | None = None

    def add_session(
        self,
        day: str,
        ref: float,
        bars: list[list[tuple[float, float]]],
        f: float = 1.0,
        bar_tags: list[str] | None = None,
    ) -> None:
        """`bars`: per bar, its quotes as (bid, ask) in path order, in the session's raw prices; `f`
        the session's split factor (raw over adjusted), so a price carries across a split as x f'/f;
        `bar_tags` each bar's part of the session (default `day`)."""
        a = len(self.bid)
        spans = []
        for n, quotes in enumerate(bars):
            s = len(self.bid)
            for bd, ak in quotes:
                self.bid.append(bd)
                self.ask.append(ak)
            e = len(self.bid)
            self.bar_end.extend([e] * (e - s))
            self.tags.extend([bar_tags[n] if bar_tags else "day"] * (e - s))
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


def minute_rows(conn: sqlite3.Connection, symbol: str):
    return conn.execute(
        "SELECT start, open, high, low, close FROM bars WHERE symbol = ? ORDER BY start", (symbol,)
    ).fetchall()


def fingerprint(counts: dict) -> dict:
    blob = json.dumps(counts, sort_keys=True).encode()
    return {"counts": counts, "sha256": hashlib.sha256(blob).hexdigest()}


# --------------------------------------------------------------------------- the hours
def session_of_night(stamp: str, trading_days: list[str]) -> str | None:
    """The session an overnight bar belongs to: one from 20:00 on day d opens the next trading day
    after d; one before 04:00 on day d belongs to the first trading day on or after d."""
    d, hhmm = stamp[:10], stamp[11:16]
    if hhmm >= SPEC["night"][0]:
        j = bisect.bisect_right(trading_days, d)
    elif hhmm < SPEC["night"][1]:
        j = bisect.bisect_left(trading_days, d)
    else:
        return None
    return trading_days[j] if j < len(trading_days) else None


def merged_rows(day_rows, night_rows, trading_days: list[str]) -> list[tuple]:
    """(session, is_day, start, o, h, l, c) in time order within each session: its night bars,
    then its day bars."""
    out = [(r[0][:10], 1, *r) for r in day_rows]
    for r in night_rows:
        s = session_of_night(r[0], trading_days)
        if s is not None:
            out.append((s, 0, *r))
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return out


def tape_from_rows(
    rows: list[tuple],
    closes: dict[str, float],
    half: float,
    first_day: str,
    last_day: str | None,
    splits: dict[str, float],
    all_hours: bool,
) -> Tape:
    """Item 95's `tape_from_bars` over merged rows: paper's hours take the day bars from 08:15 to
    20:00; all hours take the session's night bars and its day bars from 04:00. Each bar is tagged
    night, pre (04:00-08:15) or day. The reference, split factor and daily guard are item 95's."""
    lo, hi = SPEC["all_hours_day"] if all_hours else SPEC["paper_hours"]
    days = sorted(closes)
    split_days = sorted(splits)

    def factor(day: str) -> float:
        f = 1.0
        for d in split_days[bisect.bisect_right(split_days, day) :]:
            f *= splits[d]
        return f

    tape = Tape()
    skipped = {"no_reference": 0, "no_daily_close": 0, "disagrees": 0}
    current, bars, tags, last_regular = None, [], [], None

    def flush():
        if current is None or not bars or current < first_day or (last_day and current > last_day):
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
        tape.add_session(current, round(closes[days[j - 1]] * f, 4), bars, f, tags)

    for session, is_day, start, o, h, low, c in rows:
        if session != current:
            flush()
            current, bars, tags, last_regular = session, [], [], None
        hhmm = start[11:16]
        if is_day:
            if "09:30" <= hhmm < SPEC["regular_close"]:
                last_regular = c
            if lo <= hhmm < hi:
                bars.append(quotes_of(path(o, h, low, c), half))
                tags.append("pre" if hhmm < SPEC["paper_hours"][0] else "day")
        elif all_hours:
            bars.append(quotes_of(path(o, h, low, c), half))
            tags.append("night")
    flush()
    tape.skipped = skipped
    return tape


# --------------------------------------------------------------------------- the verdicts
def judge_paired(per_fund: dict[str, dict], judged: list[str]) -> dict:
    arm = PAIRED_RULE["arm"]
    diffs = [per_fund[f][arm]["diff"] for f in judged]
    cost = [per_fund[f][arm]["cost_diff"] for f in judged]
    lots = sum(per_fund[f][arm]["all_hours"]["lots"] for f in judged)
    s = stat(diffs)
    checks = {
        "mean_positive": s["mean"] > 0,
        "t_above_bar": s["t"] > PAIRED_RULE["t_bar"],
        "more_than_half_positive": s["positive_share"] > 0.5,
        "cost_positive": stat(cost)["mean"] > 0 if cost else False,
    }
    enough = len(judged) >= PAIRED_RULE["min_funds"] and lots >= PAIRED_RULE["min_lots"]
    verdict = "no verdict" if not enough else ("pass" if all(checks.values()) else "fail")
    return {"stat": s, "checks": checks, "lots": lots, "funds": judged, "verdict": verdict}


def combined(kit_word: str, paired_word: str) -> str:
    both = {kit_word, paired_word}
    return "fail" if "fail" in both else ("pass" if both == {"pass"} else "no verdict")


# --------------------------------------------------------------------------- the run
def by_hours(tape: Tape, lots: list[Lot]) -> dict:
    """Lots by where in the session they were bought: night, pre-market, paper's day."""
    out: dict[str, int] = {}
    for x in lots:
        tag = tape.tags[x.i]
        out[tag] = out.get(tag, 0) + 1
    return out


def one(tape: Tape, entry: str, target_rule: str) -> tuple[dict, list[dict], list[Lot]]:
    n = len(tape.sessions)
    lots = run_arm(tape, entry, target_rule, 0, n)
    rows = [lot_money(tape, x, n) for x in lots]
    closed = [r for r in rows if r["closed"]]
    return (
        {
            "pct_year": pct_year(rows, n),
            "lots": len(rows),
            "closed": len(closed),
            "same_session_share": (sum(r["same_session"] for r in closed) / len(closed)) if closed else None,
            "by_hours": by_hours(tape, lots),
            "net": round(sum(r["net"] for r in rows), 2),
        },
        rows,
        lots,
    )


def run(funds: list[str], paths: dict[str, Path], rate, rep: progress.Reporter, *, last_day=None) -> dict:
    day_conn = sqlite3.connect(f"file:{paths['day']}?mode=ro", uri=True)
    night_conn = sqlite3.connect(f"file:{paths['night']}?mode=ro", uri=True)
    dconn = sqlite3.connect(f"file:{paths['daily']}?mode=ro", uri=True)
    first = SPEC["first_session"]
    per_fund: dict[str, dict] = {}
    coverage: dict[str, dict] = {}
    pooled: list[dict] = []
    judged, counts = [], {}
    rep.split("funds", funds, unit="funds")
    for fund in funds:
        rep.at("funds", fund)
        closes = daily_closes(dconn, fund)
        splits = split_ratios(dconn, fund)
        trading = sorted(closes)
        day_rows = [r for r in minute_rows(day_conn, fund) if r[0][:10] >= "2024-09-19"]
        night_rows = minute_rows(night_conn, fund)
        rows = merged_rows(day_rows, night_rows, trading)
        paper = tape_from_rows(rows, closes, SPEC["half_spread"], first, last_day, splits, False)
        allh = tape_from_rows(rows, closes, SPEC["half_spread"], first, last_day, splits, True)
        wide_p = tape_from_rows(rows, closes, PAIRED_RULE["cost_half_spread"], first, last_day, splits, False)
        wide_a = tape_from_rows(rows, closes, PAIRED_RULE["cost_half_spread"], first, last_day, splits, True)
        nights = sum(1 for s in allh.sessions if any(allh.tags[k] == "night" for k in range(s.a, s.b)))
        share = nights / len(allh.sessions) if allh.sessions else 0.0
        same = [s.day for s in paper.sessions] == [s.day for s in allh.sessions]
        coverage[fund] = {
            "sessions": len(allh.sessions),
            "nights": nights,
            "share": share,
            "same_sessions": same,
        }
        counts[fund] = {
            "day_bars": len(day_rows),
            "night_bars": len(night_rows),
            "sessions": len(allh.sessions),
            "skipped": allh.skipped,
        }
        if not allh.sessions or not same:
            rep.advance("funds", key=fund, current=fund)
            continue
        if share >= PAIRED_RULE["min_night_share"]:
            judged.append(fund)
        per_fund[fund] = {}
        for arm, (entry, target_rule) in ARMS.items():
            a, a_rows, _ = one(allh, entry, target_rule)
            p, _, _ = one(paper, entry, target_rule)
            wa, _, _ = one(wide_a, entry, target_rule)
            wp, _, _ = one(wide_p, entry, target_rule)
            per_fund[fund][arm] = {
                "all_hours": a,
                "paper_hours": p,
                "diff": a["pct_year"] - p["pct_year"],
                "cost_diff": wa["pct_year"] - wp["pct_year"],
            }
            if arm == PAIRED_RULE["arm"] and fund in judged:
                pooled += a_rows
        rep.advance("funds", key=fund, current=fund)
    for c in (day_conn, night_conn, dconn):
        c.close()
    kit = kit_rule.evaluate(RULE, {PAIRED_RULE["arm"]: kit_rows(pooled, rate)})
    paired = judge_paired(per_fund, judged)
    return {
        "verdict": {PAIRED_RULE["arm"]: combined(kit["verdict"][PAIRED_RULE["arm"]], paired["verdict"])},
        "provisional": True,
        "kit": kit,
        "paired": paired,
        "per_fund": per_fund,
        "coverage": coverage,
        "inputs": fingerprint(counts),
    }


def default_paths() -> dict[str, Path]:
    research = home.home() / "data" / "research"
    return {
        "day": research / "item95" / "minute_bars.db",
        "night": research / "item96" / "overnight_bars.db",
        "daily": research / "item26" / "bars.db",
    }


def last_whole_month_end(today: date) -> str:
    """The declared window's end: the last day of the month before `today`'s (New York date), so a
    month still being traded is never read."""
    return date.fromordinal(today.replace(day=1).toordinal() - 1).isoformat()


def cmd_study(args) -> int:
    smoke = args.step == "smoke"
    funds = ["SOXL", "TQQQ"] if smoke else list(FUNDS)
    last_day = "2024-12-31" if smoke else last_whole_month_end(datetime.now(NY).date())
    with progress.Reporter("item96-smoke" if smoke else "item96-study") as rep:
        block = run(funds, default_paths(), bill_reader(), rep, last_day=last_day)
    doc = {
        "item": 96,
        "window": [SPEC["first_session"], last_day],
        "spec": SPEC,
        "rule": RULE,
        "paired_rule": PAIRED_RULE,
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
