"""Item 103: a diagonal on TQQQ and SOXL: a year-long call, with 45-day calls sold against it only while
VIX is above VIX3M (research/edge-hypotheses-plan.md, section 103).

The declaration is section 103; this script runs it. Results are derived from licensed data and stay under
the backtest results directory, never the repo.

- `listing`: the one-session probes that say which year-long expiries the vendor listed on the first
  session of each month (`{results}/listing_requests.json`, a fetch-chain-paths file).
- `plan`: refuses until every probe is answered, then writes the chain paths the study reads
  (`{results}/path_requests.json`) and `plan.json` (the window).
- `study`: one run over the whole window, judged by the declared RULE and EXTRA_RULE, written to
  `{results}/results.json` as the kit's verdict block. `study --smoke` runs on the sample fetched so far.

**The long** (every arm the same): one call on the listed standard monthly 240 to 540 calendar days out
nearest 360 (the earlier on a tie), drawn from the monthlies the month's first session lists; the strike
whose delta is nearest 0.80, the higher strike on a tie, among calls whose quote passes the width bar.
Bought at the mid plus the suite's slippage, closed the same way on the first session 45 or fewer days
before its expiry; an unquoted close is retried the next session and a call reaching expiry settles at
intrinsic with the $5 event. The next long is bought the session after a close. A long open on a split
ex-date is dropped there unbooked, its weeks left out for every arm, and the next bought that session's
close on the new basis.

**The short:** one call at a time, on the listed expiry 35 to 60 days out nearest 45 that falls at least 46
days before the long's expiry (so it expires before the long closes), the out-of-the-money strike above the
long's whose delta is nearest the arm's target, the nearer the money on a tie, among calls passing the width
bar. Sold at the mid less slippage at a session's close when no short is open and, for a gated arm, that
close's VIX is above its VIX3M. Held to its expiry's session: under the strike it expires; at or over it,
it is bought back at the mid plus slippage, or settled at intrinsic with the $5 event when unquoted.

**Measured,** per name and week (Friday close to Friday close, the last session of each ISO week), the
position's change in value in dollars on one contract: cash flows with costs plus the legs' marks at the
mid. A week counts when a position was held at the previous Friday, every held leg was quoted at both
Fridays and neither Friday's position is a dropped one; otherwise it is counted, never zero. Deltas are the
kit's Black-76 at r = 0 off the chain's parity forward. Capital is the long's debit at entry.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import fees as core_fees
from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats
from cherrypick.core import treasury

from cherrypick.backtest.study import calendars as kit_calendars
from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs, Undeclared

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's three bars on the diagonal's weekly net, one run cut at one date: weeks ending before it are
#: the design, from it the holdout. The delta bar applies (the diagonal is long delta).
RULE = {
    "t_bar": 2.0,
    "min_n": 150,
    "holdout_min_n": 80,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 103},
    "delta": True,
    "splits": ["2024-01-01"],
}

#: What the paired test adds to the kit's bars, and this study's declared constants.
EXTRA_RULE = {
    "alpha": 0.05,  # Holm across the judged arms, on the paired test's one-sided p in the design
    "paired_t_bar": 2.0,  # the design's mean weekly (arm - long alone) above zero at this one-sided t
    "halves_split": "2022-01-01",  # design weeks before it, and from it
    "stress_scale": 1.5,  # fees and slippage at this multiple
    "min_shorts": 10,  # short calls sold in the design, every name together; fewer is no verdict
    "holdout_paired_mean": 0.0,  # the holdout's mean weekly paired difference must be above this
    "min_two_sided": 0.80,  # the coverage floor, per name and year, from the store's quality report
    "min_strikes": 25,
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
CHAIN = (
    {"step": "listing", "kind": "script", "args": ["listing"], "output": "{results}/listing_requests.json"},
    {
        "step": "fetch-listing",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/listing_requests.json"},
    },
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

NAMES = ("SOXL", "TQQQ")
START = "2020-01-02"  # the first session the vendor's current tier serves
LONG_BAND = (240, 540, 360)  # min, max, aim DTE
LONG_DELTA = 0.80
LONG_CLOSE_DTE = 45
SHORT_BAND = (35, 60, 45)
#: A quote passes the width bar when two-sided and its ask less its bid is at most the larger of these.
WIDTH_FRAC, WIDTH_MIN = 0.15, 0.06
LISTING_SLACK = 31  # a probe on the month's first session asks about every expiry a day of the month can pick
WAIT_SESSIONS = 30  # sessions after a skeleton entry whose long expiry the plan also requests
RATE_STALE_DAYS = 10


@dataclass(frozen=True)
class Arm:
    name: str
    short_delta: float | None  # None: the long alone
    gated: bool


ARMS = {
    "inv30": Arm("inv30", 0.30, True),
    "inv20": Arm("inv20", 0.20, True),
    "all30": Arm("all30", 0.30, False),
    "all20": Arm("all20", 0.20, False),
    "long": Arm("long", None, False),
}
JUDGED = ("inv30", "inv20")
REPORTED = ("all30", "all20", "long")


# ------------------------------------------------------------------------------------------------
# Pure: dates and expiries.


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _cal(a: str, b: str) -> int:
    return (_d(b) - _d(a)).days


def week_ends(sessions: list[str]) -> set[str]:
    """The last session of each ISO week."""
    out = set()
    for i, s in enumerate(sessions):
        if i + 1 == len(sessions) or _d(sessions[i + 1]).isocalendar()[:2] != _d(s).isocalendar()[:2]:
            out.add(s)
    return out


def month_firsts(sessions: list[str]) -> dict[str, str]:
    """{YYYY-MM: the month's first session} over `sessions`."""
    out: dict[str, str] = {}
    for s in sessions:
        out.setdefault(s[:7], s)
    return out


def listing_candidates(probe: str, expiries: list[str]) -> list[str]:
    """The standard monthlies a probe on `probe` asks about: each that a day of probe's month can pick. A
    long is drawn from the standard monthlies only (pmcc's leap rule): the expiry listing holds every weekly
    ever listed, and none is listed this far out."""
    lo, hi, _ = LONG_BAND
    monthlies = kit_calendars.listed_monthlies(expiries)
    return [e for e in monthlies if lo <= _cal(probe, e) <= hi + LISTING_SLACK]


def pick_long_expiry(session: str, listed: list[str]) -> str | None:
    """The listed expiry 240 to 540 days out nearest 360, the earlier on a tie."""
    lo, hi, aim = LONG_BAND
    fits = [e for e in listed if lo <= _cal(session, e) <= hi]
    return min(fits, key=lambda e: (abs(_cal(session, e) - aim), e)) if fits else None


def short_cap(long_exp: str) -> str:
    """The latest expiry a short may take: 46 days before the long's, so it expires before the long closes."""
    return (_d(long_exp) - timedelta(days=LONG_CLOSE_DTE + 1)).isoformat()


def pick_short_expiry(session: str, expiries: list[str], cap: str) -> str | None:
    """The listed expiry 35 to 60 days out nearest 45, on or before `cap`; the earlier on a tie."""
    lo, hi, aim = SHORT_BAND
    fits = [e for e in expiries if lo <= _cal(session, e) <= hi and e <= cap]
    return min(fits, key=lambda e: (abs(_cal(session, e) - aim), e)) if fits else None


def long_due(session: str, long_exp: str) -> bool:
    return _cal(session, long_exp) <= LONG_CLOSE_DTE


# ------------------------------------------------------------------------------------------------
# Pure: quotes and strikes. A chain is the kit's `Chain` (or any object with `by` and `delta(q)`).


def width_ok(q: dict | None) -> bool:
    """Two-sided, and the ask less the bid at most max(15% of the mid, $0.06)."""
    if q is None or q.get("bid") is None or q.get("ask") is None:
        return False
    b, a = q["bid"], q["ask"]
    if not (b > 0 and a >= b):
        return False
    return a - b <= max(WIDTH_FRAC * (a + b) / 2, WIDTH_MIN)


def markable(q: dict | None) -> bool:
    """A quote a held leg can be marked on: both sides present, not crossed, a positive ask (no width bar:
    the bar picks strikes, it does not unprice a held leg)."""
    return (
        q is not None
        and q.get("bid") is not None
        and q.get("ask") is not None
        and q["ask"] > 0
        and (q["ask"] >= q["bid"] >= 0)
    )


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2


def pick_long_strike(chain) -> float | None:
    """The call whose delta is nearest LONG_DELTA among those passing the width bar; the higher on a tie."""
    best = None
    for k, q in chain.by["C"].items():
        if not width_ok(q):
            continue
        d = chain.delta(q)
        if d is None:
            continue
        key = (round(abs(d - LONG_DELTA), 9), -k)
        if best is None or key < best[0]:
            best = (key, k)
    return None if best is None else best[1]


def pick_short_strike(chain, target: float, above: float) -> float | None:
    """The call over `above` and over the chain's forward whose delta is nearest `target`, among those
    passing the width bar; the nearer the money (lower strike) on a tie."""
    fwd = getattr(chain, "forward", None)
    best = None
    for k, q in chain.by["C"].items():
        if k <= above or (fwd is not None and k <= fwd) or not width_ok(q):
            continue
        d = chain.delta(q)
        if d is None:
            continue
        key = (round(abs(d - target), 9), k)
        if best is None or key < best[0]:
            best = (key, k)
    return None if best is None else best[1]


def gate_open(ratio: float | None) -> bool | None:
    """VIX over VIX3M at the close: the ratio above 1. None when unmeasured."""
    return None if ratio is None else ratio > 1.0


# ------------------------------------------------------------------------------------------------
# Pure: costs.


def slip(q: dict, scale: float) -> float:
    """The suite's slippage on one contract of the leg, in dollars, at `scale`."""
    return core_fees.slippage_dollars([q], 1, {}) * scale


def open_fee(sym: str, selling: bool, scale: float) -> float:
    return core_fees.ic_open_fee(sym, quantity=1, legs=1, sell_legs=int(selling), ndigits=4) * scale


def close_fee(sym: str, selling: bool, scale: float) -> float:
    return core_fees.ic_close_fee(sym, quantity=1, legs=1, sell_legs=int(selling), ndigits=4) * scale


def settle_fee(scale: float) -> float:
    return core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT * scale


# ------------------------------------------------------------------------------------------------
# The replay of one arm on one name.


@dataclass
class Market:
    """One name's inputs behind functions, so a test supplies invented ones."""

    name: str
    sessions: list[str]  # trading sessions inside the window, with a close
    closes: dict[str, float]  # raw closes: the basis the chain's strikes are quoted on
    chain_at: object  # (session, expiry) -> Chain | None
    listed_at: object  # (session) -> the long expiries listed on the session's month's first session
    expiries: list[str]  # every listed expiry (the shorts are drawn from these)
    ratio_at: object  # (session) -> VIX / VIX3M at its close, or None
    rate_at: object  # (session) -> the 3-month bill, a fraction
    split_days: list[str] = field(default_factory=list)


@dataclass
class Leg:
    strike: float
    exp: str
    opened: str


@dataclass
class Run:
    name: str
    arm: str
    snaps: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    shorts: list[dict] = field(default_factory=list)
    cycles: list[dict] = field(default_factory=list)
    dropped: set = field(default_factory=set)  # cycles open on a split ex-date

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def simulate(m: Market, arm: Arm, *, scale: float = 1.0) -> Run:
    """One arm on one name over its sessions, costs at `scale`."""
    run = Run(m.name, arm.name)
    ends = week_ends(m.sessions)
    splits = set(m.split_days)
    st = {"cash": 0.0, "long": None, "short": None, "capital": None, "cycle": 0, "closed_on": None,
          "flow": 0.0, "dropped": set()}  # fmt: skip

    def book(x: float) -> None:
        st["cash"] += x
        st["flow"] += x

    def quote(d: str, exp: str, k: float) -> dict | None:
        ch = m.chain_at(d, exp)
        return None if ch is None else ch.by["C"].get(k)

    def enter(d: str) -> None:
        exp = pick_long_expiry(d, m.listed_at(d))
        if exp is None:
            return run.count("refused_no_long_listed")
        ch = m.chain_at(d, exp)
        if ch is None:
            return run.count("refused_no_long_chain")
        k = pick_long_strike(ch)
        if k is None:
            return run.count("refused_no_long_strike")
        q = ch.by["C"][k]
        st["cycle"] += 1
        st["flow"] = 0.0
        book(-100.0 * mid(q) - slip(q, scale) - open_fee(m.name, False, scale))
        st["long"], st["capital"] = Leg(k, exp, d), 100.0 * mid(q)
        run.cycles.append(
            {"cycle": st["cycle"], "opened": d, "exp": exp, "strike": k, "capital": st["capital"]}
        )
        run.count("entries")

    def close_long(d: str) -> None:
        lg = st["long"]
        q = quote(d, lg.exp, lg.strike)
        if markable(q):
            book(100.0 * mid(q) - slip(q, scale) - close_fee(m.name, True, scale))
        elif d >= lg.exp:
            iv = max(0.0, m.closes[d] - lg.strike)
            book(100.0 * iv - (settle_fee(scale) if iv > 0 else 0.0))
            run.count("long_settled")
        else:
            return run.count("long_close_unquoted")
        run.cycles[-1].update({"closed": d})
        st["long"], st["capital"], st["closed_on"] = None, None, d

    def settle_short(d: str) -> None:
        sh = st["short"]
        s = m.closes[d]
        life = run.shorts[-1]
        if s < sh.strike:
            life["how"] = "expired"
        else:
            q = quote(d, sh.exp, sh.strike)
            if markable(q):
                book(-100.0 * mid(q) - slip(q, scale) - close_fee(m.name, False, scale))
                life["how"] = "bought back"
            else:
                book(-100.0 * (s - sh.strike) - settle_fee(scale))
                life["how"] = "settled"
        life["closed"] = d
        st["short"] = None

    def sell_short(d: str) -> None:
        lg = st["long"]
        if arm.gated:
            g = gate_open(m.ratio_at(d))
            if g is None:
                return run.count("gate_unmeasured")
            if not g:
                return run.count("gate_closed_sessions")
        exp = pick_short_expiry(d, m.expiries, short_cap(lg.exp))
        if exp is None:
            return run.count("no_short_expiry")
        ch = m.chain_at(d, exp)
        if ch is None:
            return run.count("no_short_chain")
        k = pick_short_strike(ch, arm.short_delta, lg.strike)
        if k is None:
            return run.count("no_short_strike")
        q = ch.by["C"][k]
        book(100.0 * mid(q) - slip(q, scale) - open_fee(m.name, True, scale))
        st["short"] = Leg(k, exp, d)
        run.shorts.append(
            {"sold": d, "exp": exp, "strike": k, "cycle": st["cycle"], "credit": 100.0 * mid(q)}
        )
        run.count("shorts_sold")

    def settles_on(exp: str) -> str:
        i = bisect.bisect_right(m.sessions, exp) - 1
        return m.sessions[max(i, 0)]

    def snapshot(d: str) -> None:
        lg, sh = st["long"], st["short"]
        row = {"end": d, "has_pos": lg is not None, "cycle": st["cycle"] if lg else None, "spot": m.closes[d]}
        if lg is None:
            run.snaps.append({**row, "priced": True, "value": st["cash"], "capital": None, "delta": 0.0})
            return
        lch = m.chain_at(d, lg.exp)
        lq = None if lch is None else lch.by["C"].get(lg.strike)
        sch = m.chain_at(d, sh.exp) if sh else None
        sq = None if sch is None else sch.by["C"].get(sh.strike) if sh else None
        if not markable(lq) or (sh is not None and not markable(sq)):
            run.count("unpriced_weeks")
            run.snaps.append({**row, "priced": False})
            return
        dl = lch.delta(lq)
        ds = sch.delta(sq) if sh else 0.0
        value = st["cash"] + 100.0 * mid(lq) - (100.0 * mid(sq) if sh else 0.0)
        run.snaps.append({**row, "priced": True, "value": value, "capital": st["capital"],
                          "delta": None if dl is None or ds is None else dl - ds})  # fmt: skip

    for d in m.sessions:
        if st["long"] is not None and d in splits:
            st["dropped"].add(st["cycle"])
            st["cash"] -= st["flow"]  # unbooked: the cycle's flows leave the series with it
            run.cycles[-1].update({"closed": d, "dropped": True})
            st["long"], st["short"], st["capital"] = None, None, None
            run.count("split_drops")
        if st["short"] is not None and d >= settles_on(st["short"].exp):
            settle_short(d)
        if st["long"] is not None and long_due(d, st["long"].exp):
            close_long(d)
        if st["long"] is None and (st["closed_on"] is None or d > st["closed_on"]):
            enter(d)
        if st["long"] is not None and st["short"] is None and arm.short_delta is not None:
            sell_short(d)
        if d in ends:
            snapshot(d)
    run.counts["dropped_cycles"] = len(st["dropped"])
    run.dropped = set(st["dropped"])
    return run


# ------------------------------------------------------------------------------------------------
# Pure: weeks, the kit's rows and the paired difference.


def weekly_rows(run: Run, rate_at) -> list[dict]:
    """The kit's rows, one per measured week: `date` (the week's end), `net` (dollars, one contract), `bp`
    (the long's debit at the previous Friday), `days`, `rate` and `financed` (the previous Friday's net
    delta on 100 shares, financed on the same capital), plus `name`, `start` and `spot`s."""
    out = []
    dropped = getattr(run, "dropped", set())
    for a, b in zip(run.snaps, run.snaps[1:], strict=False):
        if not a["has_pos"] or not a["priced"] or not b["priced"]:
            continue
        if a["cycle"] in dropped or (b["cycle"] is not None and b["cycle"] in dropped):
            continue
        if a["delta"] is None:
            continue
        days = _cal(a["end"], b["end"])
        rate = rate_at(a["end"])
        out.append({
            "name": run.name, "date": b["end"], "start": a["end"], "net": round(b["value"] - a["value"], 2),
            "bp": a["capital"], "days": days, "rate": rate,
            "financed": ck.financed_delta(100.0 * a["delta"], a["spot"], b["spot"], a["capital"], rate, days),
        })  # fmt: skip
    return out


def week_ledger(run: Run) -> dict:
    """How the run's weeks split: measured, flat, unpriced, dropped (a split cycle) or delta unsolved."""
    out = dict.fromkeys(("measured", "flat", "unpriced", "dropped", "no_delta"), 0)
    dropped = getattr(run, "dropped", set())
    for a, b in zip(run.snaps, run.snaps[1:], strict=False):
        if not a["has_pos"]:
            out["flat"] += 1
        elif a["cycle"] in dropped or (b["cycle"] is not None and b["cycle"] in dropped):
            out["dropped"] += 1
        elif not a["priced"] or not b["priced"]:
            out["unpriced"] += 1
        elif a["delta"] is None:
            out["no_delta"] += 1
        else:
            out["measured"] += 1
    return out


def paired(arm_rows: list[dict], long_rows: list[dict]) -> list[dict]:
    """Per (name, week) both measured: the arm's net less the long alone's."""
    base = {(r["name"], r["date"]): r["net"] for r in long_rows}
    return [
        {"name": r["name"], "date": r["date"], "diff": round(r["net"] - base[(r["name"], r["date"])], 2)}
        for r in arm_rows
        if (r["name"], r["date"]) in base
    ]


def summary(xs: list[float]) -> dict:
    n = len(xs)
    out = {"n": n, "mean": statistics.fmean(xs) if xs else None}
    if n >= 2:
        sd = statistics.stdev(xs)
        out["sd"] = sd
        if sd > 0:
            t = out["mean"] / (sd / math.sqrt(n))
            out["t"], out["p"] = t, 1 - core_stats.t_cdf(t, n - 1)
            out["mde"] = core_stats.minimum_detectable_effect(sd, n)
        else:  # every difference the same: certain in its sign (item 102's reading)
            out["t"] = math.inf if out["mean"] > 0 else -math.inf if out["mean"] < 0 else 0.0
            out["p"] = 0.0 if out["mean"] > 0 else 1.0
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


# ------------------------------------------------------------------------------------------------
# The verdict.


def judge_arm(rule: dict, rows: list[dict], pair: list[dict], pair_stress: list[dict], shorts: int,
              holm_ok: bool, coverage_short: list[str]) -> dict:  # fmt: skip
    """One judged arm: the kit's three bars on the diagonal's weeks and the paired test on the design part,
    the holdout read only after both pass."""
    split = rule["splits"][0]
    d_rows = [r for r in rows if r["date"] < split]
    h_rows = [r for r in rows if r["date"] >= split]
    d_pair = [p["diff"] for p in pair if p["date"] < split]
    h_pair = [p["diff"] for p in pair if p["date"] >= split]
    half = EXTRA_RULE["halves_split"]
    ps = summary(d_pair)
    extra = {
        "paired": _pos(ps["mean"]) and ps.get("t") is not None and ps["t"] >= EXTRA_RULE["paired_t_bar"],
        "holm": holm_ok,
        "halves": all(
            _pos(summary([p["diff"] for p in pair if p["date"] < split and (p["date"] < half) == lo])["mean"])
            for lo in (True, False)
        ),
        "names": all(
            _pos(summary([p["diff"] for p in pair if p["date"] < split and p["name"] == n])["mean"])
            for n in NAMES
        ),
        "stress": _pos(summary([p["diff"] for p in pair_stress if p["date"] < split])["mean"]),
    }
    kit = kit_rule.judge_part(rule, d_rows, rule["min_n"])
    parts = [{"from": None, "before": split, "n": len(d_rows), **kit, "extra": extra, "paired": ps,
              "shorts": shorts}]  # fmt: skip
    if coverage_short:
        word, why = ck.NO_VERDICT, "coverage: " + "; ".join(coverage_short)
    elif shorts < EXTRA_RULE["min_shorts"]:
        word, why = (
            ck.NO_VERDICT,
            f"{shorts} short calls sold in the design, under {EXTRA_RULE['min_shorts']}",
        )
    elif kit["verdict"] != ck.PASS:
        word, why = kit["verdict"], "the kit's bars: " + ", ".join(kit["failed"]) if kit["failed"] else None
    elif not all(extra.values()):
        word, why = ck.FAIL, "the paired test: " + ", ".join(k for k, v in extra.items() if not v)
    else:
        word, why = ck.PASS, None
    parts[0]["verdict"] = word
    if why:
        parts[0]["why"] = why
    if word != ck.PASS:
        parts.append({"from": split, "before": None, "n": len(h_rows), "verdict": kit_rule.NOT_READ})
    else:
        hk = kit_rule.judge_part(rule, h_rows, rule["holdout_min_n"], so_far=d_rows + h_rows)
        hp = summary(h_pair)
        ok_pair = hp["mean"] is not None and hp["mean"] > EXTRA_RULE["holdout_paired_mean"]
        word = hk["verdict"] if hk["verdict"] != ck.PASS else (ck.PASS if ok_pair else ck.FAIL)
        parts.append({"from": split, "before": None, "n": len(h_rows), **hk, "paired": hp, "verdict": word})
    return {"n": len(rows), "parts": parts, "verdict": word}


def judge(rule: dict, per_arm: dict, pairs: dict, pairs_stress: dict, shorts: dict,
          coverage_short: list[str]) -> dict:  # fmt: skip
    """The verdict block for the judged arms: Holm across them on the paired test's design p, then each."""
    split = rule["splits"][0]
    pvals = {}
    for a in JUDGED:
        s = summary([p["diff"] for p in pairs[a] if p["date"] < split])
        pvals[a] = s.get("p", 1.0) if s["n"] >= rule["min_n"] else 1.0
    holm = core_stats.holm(pvals, EXTRA_RULE["alpha"])
    arms = {
        a: judge_arm(rule, per_arm[a], pairs[a], pairs_stress[a], shorts[a], holm[a], coverage_short)
        for a in JUDGED
    }
    return {
        "rule": rule,
        "extra_rule": EXTRA_RULE,
        "arms": arms,
        "verdict": {a: b["verdict"] for a, b in arms.items()},
    }


def report_arm(rows: list[dict], rule: dict) -> dict:
    """An arm's figures, unjudged: the kit's bars on each part, and its weekly net by year."""
    split = rule["splits"][0]
    out = {}
    for label, part in (("design", [r for r in rows if r["date"] < split]),
                        ("holdout", [r for r in rows if r["date"] >= split])):  # fmt: skip
        out[label] = {"n": len(part), **(kit_rule.judge_part(rule, part, 1) if len(part) >= 2 else {})}
    years: dict[str, list[float]] = {}
    for r in rows:
        years.setdefault(r["date"][:4], []).append(r["net"])
    out["by_year"] = {y: {"weeks": len(v), "net": round(sum(v), 2)} for y, v in sorted(years.items())}
    return out


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item103"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_expiries() -> dict[str, list[str]]:
    """Item 29's listing of each name's expiries (its `expirations` step's file)."""
    from cherrypick.backtest import paths

    path = paths.results_dir() / "research" / "item29" / "expirations.json"
    if not path.exists():
        raise SystemExit(f"no expiries at {path}: item 29's expirations step has not run")
    return json.loads(path.read_text(encoding="utf-8"))["expirations"]


def load_bars(sym: str) -> tuple[dict[str, float], list[str]]:
    """Raw closes (the chain's basis) and the split ex-dates."""
    from cherrypick.technicals import adjust, store
    from cherrypick.technicals import paths as tpaths

    hist = store.connect(tpaths.history_db())
    try:
        raw = store.raw_bars(hist, sym)
        splits = adjust.dedupe_splits(raw, store.splits(hist, sym))
    finally:
        hist.close()
    return {b.date: b.close for b in raw if b.close}, sorted(s.ex_date for s in splits)


def load_ratios() -> dict[str, float]:
    """VIX / VIX3M at each Cboe close (the exchange's public daily files, as item 37 reads them)."""
    from cherrypick.overview import files

    def close(name):
        return {
            d.isoformat(): v for d, v in files.parse_cboe(files.cboe_path(name).read_text(encoding="utf-8"))
        }

    vix, v3m = close("VIX"), close("VIX3M")
    return {d: vix[d] / v3m[d] for d in vix.keys() & v3m.keys() if v3m[d]}


def bill_reader():
    """The 3-month bill on a session; stale past RATE_STALE_DAYS the run stops rather than guess."""
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (_d(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no 3-month bill within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def window_sessions(closes: dict[str, float], end: str) -> list[str]:
    return sorted(d for d in closes if START <= d <= end)


def listing_requests(expiries: dict[str, list[str]], end: str) -> list[dict]:
    """One one-session probe per name, month and candidate expiry, on the month's first session."""
    out = []
    for sym in NAMES:
        closes, _ = load_bars(sym)
        for probe in sorted(month_firsts(window_sessions(closes, end)).values()):
            for e in listing_candidates(probe, expiries[sym]):
                out.append({"symbol": sym, "expiration": e, "start": probe, "through": probe})
    return out


def answered(conn, reqs: list[dict]) -> list[dict]:
    """The requests the store has not answered (no complete or empty path row)."""
    done = {
        (s, e, a, b)
        for s, e, a, b in conn.execute(
            "SELECT symbol, expiration, start, through FROM eod_chain_paths "
            "WHERE status IN ('complete', 'empty')"
        )
    }
    return [r for r in reqs if (r["symbol"], r["expiration"], r["start"], r["through"]) not in done]


def make_listed_at(inputs: Inputs, conn, sym: str, sessions: list[str], expiries: list[str]):
    """listed_at(session) -> the long expiries the vendor listed on the session's month's first session."""
    firsts = month_firsts(sessions)
    cache: dict[str, list[str]] = {}

    def listed_at(session: str) -> list[str]:
        probe = firsts[session[:7]]
        if probe not in cache:
            got = []
            for e in listing_candidates(probe, expiries):
                try:
                    if inputs.chain(conn, sym, probe, e) is not None:
                        got.append(e)
                except Undeclared:
                    pass
            cache[probe] = got
        return cache[probe]

    return listed_at


def make_chain_at(inputs: Inputs, conn, sym: str, undeclared: list):
    """chain_at(session, expiry) -> the kit's Chain of a declared path, or None. A path the plan did not
    request is counted in `undeclared` and read as no chain."""
    cache: dict = {}

    def chain_at(session: str, expiry: str):
        key = (session, expiry)
        if key not in cache:
            if len(cache) > 20_000:
                cache.clear()
            try:
                cache[key] = inputs.chain(conn, sym, session, expiry)
            except Undeclared:
                undeclared.append((sym, session, expiry))
                cache[key] = None
        return cache[key]

    return chain_at


def skeleton(sessions: list[str], listed_at, split_days: list[str]) -> list[tuple[str, str, str]]:
    """The no-refusal long cycles: (entry, expiry, close), the close the first session at or under 45 DTE
    or a split ex-date, the next entry the session after a close (the split session itself)."""
    out, i = [], 0
    splits = set(split_days)
    while i < len(sessions):
        d = sessions[i]
        exp = pick_long_expiry(d, listed_at(d))
        if exp is None:
            i += 1
            continue
        j = i + 1
        while j < len(sessions) and not long_due(sessions[j], exp) and sessions[j] not in splits:
            j += 1
        if j >= len(sessions):
            out.append((d, exp, sessions[-1]))
            break
        out.append((d, exp, sessions[j]))
        i = j if sessions[j] in splits else j + 1
    return out


def path_requests(
    sym: str, sessions: list[str], listed_at, expiries: list[str], split_days: list[str]
) -> list[dict]:
    """Every path a name's arms can read: each skeleton long (and the long a wait of up to WAIT_SESSIONS
    would buy instead) through its close, and each short expiry from the first session that can pick it
    to its expiry."""
    span: dict[str, list[str]] = {}

    def add(e: str, a: str, b: str) -> None:
        if a > b:
            return
        have = span.setdefault(e, [a, b])
        have[0], have[1] = min(have[0], a), max(have[1], b)

    for d0, exp, close in skeleton(sessions, listed_at, split_days):
        i0 = sessions.index(d0)
        for s in sessions[i0 : i0 + WAIT_SESSIONS + 1]:
            e = pick_long_expiry(s, listed_at(s))
            if e is not None:
                add(e, s, next((x for x in sessions[i0:] if long_due(x, e)), sessions[-1]))
        add(exp, d0, close)
        for s in sessions[i0 : sessions.index(close) + 1]:
            se = pick_short_expiry(s, expiries, short_cap(exp))
            if se is not None:
                add(se, s, min(se, sessions[-1]))
    return [{"symbol": sym, "expiration": e, "start": a, "through": b} for e, (a, b) in sorted(span.items())]


def window_end() -> str:
    return min(max(load_bars(sym)[0]) for sym in NAMES)


def cmd_listing(args) -> int:
    end = window_end()
    reqs = listing_requests(load_expiries(), end)
    path = Path(args.out) if args.out else out_dir() / "listing_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(reqs), "end": end}))
    return 0


def cmd_plan(args) -> int:
    listing = out_dir() / "listing_requests.json"
    if not listing.exists():
        raise SystemExit(f"no listing probes at {listing}: run `leaps_diagonal_item103.py listing` first")
    probes = json.loads(listing.read_text(encoding="utf-8"))["requests"]
    conn = chain_store()
    try:
        gaps = answered(conn, probes)
        if gaps:
            print(json.dumps({"listing_unanswered": len(gaps), "next": "fetch the listing file, then plan"}))
            return 2
        end = max(r["through"] for r in probes)
        expiries = load_expiries()
        inputs = Inputs(listing)
        out, counts = [], {}
        for sym in core_progress.track(NAMES, job="item103-plan", phase="names", unit="names",
                                       key=lambda s: s, current=str):  # fmt: skip
            closes, split_days = load_bars(sym)
            sessions = window_sessions(closes, end)
            listed_at = make_listed_at(inputs, conn, sym, sessions, expiries[sym])
            reqs = path_requests(sym, sessions, listed_at, expiries[sym], split_days)
            counts[sym] = len(reqs)
            out += reqs
    finally:
        conn.close()
    (out_dir() / "plan.json").write_text(
        json.dumps({"start": START, "end": end, "paths": counts}), encoding="utf-8"
    )
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": out}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(out), "per_name": counts}))
    return 0


def coverage(years: list[str]) -> list[str]:
    """Why a name falls short of the declared floor in a year of the window, from the store's report."""
    from cherrypick.backtest import paths, storequality

    path = paths.results_dir() / "quality" / "eod_chains.json"
    if not path.exists():
        return [f"no store quality report at {path}"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    short = []
    for sym in NAMES:
        short += storequality.meets(
            doc, sym, years, min_two_sided=EXTRA_RULE["min_two_sided"], min_strikes=EXTRA_RULE["min_strikes"]
        )
    return short


def runs_for(inputs: Inputs, listing: Inputs, conn, rep: core_progress.Reporter) -> tuple[dict, dict, dict]:
    """Every arm on every name, at the declared costs and at the stress: ({arm: {name: Run}}, the same at
    the stress, counts)."""
    plan = json.loads((out_dir() / "plan.json").read_text(encoding="utf-8"))
    expiries = load_expiries()
    ratios = load_ratios()
    rate_at = bill_reader()
    undeclared: list = []
    runs = {a: {} for a in ARMS}
    stress = {a: {} for a in ARMS}
    markets = {}
    for sym in NAMES:
        closes, split_days = load_bars(sym)
        sessions = window_sessions(closes, plan["end"])
        markets[sym] = Market(
            sym, sessions, closes, make_chain_at(inputs, conn, sym, undeclared),
            make_listed_at(listing, conn, sym, sessions, expiries[sym]), expiries[sym], ratios.get, rate_at,
            split_days=[s for s in split_days if START <= s <= plan["end"]],
        )  # fmt: skip
    rep.split("runs", [sym for sym in NAMES for _ in range(2 * len(ARMS))], unit="runs")
    for sym, mk in markets.items():
        for a, arm in ARMS.items():
            runs[a][sym] = simulate(mk, arm)
            rep.advance("runs", key=sym, current=f"{sym} {a}")
            stress[a][sym] = simulate(mk, arm, scale=EXTRA_RULE["stress_scale"])
            rep.advance("runs", key=sym, current=f"{sym} {a} stress")
    counts = {"undeclared": len(undeclared), "window": [START, plan["end"]], "rate_at": rate_at}
    return runs, stress, counts


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    listing = Inputs(out_dir() / "listing_requests.json")
    conn = chain_store()
    job = "item103-smoke" if args.smoke else "item103-study"
    try:
        with core_progress.Reporter(job) as rep:
            runs, stress, counts = runs_for(inputs, listing, conn, rep)
            read = {"paths": inputs.fingerprint(conn), "listing": listing.fingerprint(conn)}
    finally:
        conn.close()
    rate_at = counts.pop("rate_at")
    rows = {a: [r for sym in NAMES for r in weekly_rows(runs[a][sym], rate_at)] for a in ARMS}
    rows_s = {a: [r for sym in NAMES for r in weekly_rows(stress[a][sym], rate_at)] for a in ARMS}
    pairs = {a: paired(rows[a], rows["long"]) for a in ARMS if a != "long"}
    pairs_s = {a: paired(rows_s[a], rows_s["long"]) for a in ARMS if a != "long"}
    split = RULE["splits"][0]
    shorts = {a: sum(1 for sym in NAMES for s in runs[a][sym].shorts if s["sold"] < split) for a in ARMS}
    years = [str(y) for y in range(int(counts["window"][0][:4]), int(counts["window"][1][:4]) + 1)]
    block = judge(RULE, {a: rows[a] for a in JUDGED}, pairs, pairs_s, shorts, coverage(years))
    reported = {a: report_arm(rows[a], RULE) for a in ARMS}
    for a in pairs:
        reported[a]["paired"] = {
            "design": summary([p["diff"] for p in pairs[a] if p["date"] < split]),
            "holdout": summary([p["diff"] for p in pairs[a] if p["date"] >= split]),
        }
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 103, **block}
    doc.update({
        "reported": reported,
        "counts": {**counts, "runs": {a: {s: runs[a][s].counts for s in NAMES} for a in ARMS},
                   "weeks": {a: {s: week_ledger(runs[a][s]) for s in NAMES} for a in ARMS}},
        "inputs": read,
        "shorts": {a: [s for sym in NAMES for s in runs[a][sym].shorts] for a in ARMS if a != "long"},
        "cycles": {sym: runs["long"][sym].cycles for sym in NAMES},
    })  # fmt: skip
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("listing", cmd_listing), ("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
