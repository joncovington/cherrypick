"""Item 109: the book's caps on one income rule's trades: does each cut the tail more than it cuts the size?

The declaration is research/edge-hypotheses-plan.md section 109; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Only coverage counts (which monthly paths the store holds) were read before declaring, to set the
universe's floor. No price, trade or outcome was read.

- `plan`: the universe by coverage, the chain paths it reads (`path_requests.json`), the ones still
  to fetch from 2021 on (`fetch_requests.json`), and each name's report dates from Dolt
  (`earnings.json`, read by item 108 and the reported `earnings10` arm).
- `study`: one pass over the chains builds every candidate condor and its daily per-contract figures
  (`candidates.json`, item 108's input too). Each arm then runs as a book over them, and the RULE
  judges each against `none`, cut at the declared split (`results.json`).

The cap figures are manage's own: `cherrypick.manage.portfolio.book_pnl` and `leg_greeks`, and the
caps' values from `cherrypick.manage.rules.DEFAULT_RULES`, imported, never copied. Each leg is priced
Black-76 at r = 0 off the chain's parity forward, as the kit's `Chain` solves its IV, so the figures
are priced on the model the IV came from.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs, fees
from cherrypick.core import calendar as cal
from cherrypick.core import progress as core_progress
from cherrypick.core.stats import stationary_indices

from cherrypick.backtest.study import calendars as kc
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = (
    {
        "on": "2026-11-26",
        "what": "The none-scaled table leaves out a part where the none book held no risk, "
        "instead of dividing by it.",
        "why": "The study crashed with a division by zero before writing any result, "
        "so no arm or outcome was read.",
        "decided": "user asked for the fix; leaving the part out follows how the script reads a book "
        "with no risk",
        "before_read": True,
    },
)

#: The pass rule as data, fixed by the declaration before any run. Not the kit's three bars (this
#: judges books, not trades), so the queue leaves its verdict to be recorded by hand.
RULE = {
    "kind": "book_caps",
    "statistic": "tail_ratio",
    "tail_share": 0.05,
    "max_ratio": 0.80,
    "t_bar": 2.0,
    "holm": True,
    "return_t_bar": -2.0,
    "holdout_max_ratio": 1.0,
    "min_n": 100,
    "min_binds": 3,
    "block_weeks": 4,
    "resamples": 10_000,
    "seed": 109,
    "splits": ["2021-01-01"],
}

#: The trade, the universe and the book, declared (the plan's section 109 states each in words).
SPEC = {
    "funds": ["SPY", "QQQ", "IWM", "GLD", "TLT", "SLV"],
    "left_out": ["TQQQ", "SOXL", "SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "VIX", "VXX"],
    "coverage": {"from": "2016-02-01", "through": "2019-12-31", "min_share": 0.90, "days": [45, 20]},
    "first_expiry": "2016-02-19",
    "last_expiry": "2026-11-20",
    "path_days": 46,
    "fetch_from": "2021-01-01",
    "entry_max_days": 45,
    "entry_min_days": 38,
    "short_delta": 0.20,
    "credit_share": 1.0 / 3.0,
    "credit_band": [0.15, 0.45],
    "width_band": [0.01, 0.10],
    "start_nlv": 100_000.0,
    "beta_returns": 252,
    "split_return": -0.45,
    "earnings_from": "2020-02-01",
}

#: The caps, each at the value the declaration names. `shipped()` checks the shipped ones against
#: manage's DEFAULT_RULES, so a later change to manage cannot quietly move what this item tests.
ARMS: dict[str, dict[str, float]] = {
    "none": {},
    "bp25": {"spread_risk": 0.25},
    "bp35": {"spread_risk": 0.35},
    "bp50": {"spread_risk": 0.50},
    "theta3": {"theta": 0.003},
    "theta5": {"theta": 0.005},
    "stress20": {"stress": 0.20},
    "volexp10": {"volexp": 0.10},
    "expiry20": {"expiry": 0.20},
    "all": {"spread_risk": 0.35, "theta": 0.003, "stress": 0.20, "volexp": 0.10, "expiry": 0.20},
}
JUDGED = tuple(a for a in ARMS if a != "none")
#: Reported, never judged.
REPORTED = {
    "none-symbol-order": ({}, "symbol", "beta"),
    "all-symbol-order": (ARMS["all"], "symbol", "beta"),
    "stress20-beta3y": (ARMS["stress20"], "rich", "beta3y"),
    "volexp10-beta3y": (ARMS["volexp10"], "rich", "beta3y"),
    "all-beta3y": (ARMS["all"], "rich", "beta3y"),
}
EARNINGS_ARMS = {"none-2020": {}, "earnings10": {"earnings": 0.10}}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. The smoke runs on
#: what the store already holds (most of the paths), so a crash shows before the fetch.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/fetch_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)


#: manage's shipped values as the declaration names them; `shipped()` refuses to run if manage's differ.
DECLARED = {
    "spread_risk": 0.35, "theta": 0.003, "front_dte": 2, "stress": 0.20, "stress_move": -0.20,
    "stress_iv": 0.10, "volexp": 0.10, "volexp_move": -0.05, "volexp_iv": 0.10, "expiry": 0.20,
    "earnings": 0.10, "risk_pct": 0.02, "capture": 0.50, "stop_multiple": 1.0, "stop_fallback": 0.50,
    "exit_dte": 21,
}  # fmt: skip


def manage_rules() -> dict:
    from cherrypick.manage import rules

    return rules.DEFAULT_RULES


def shipped(rules: dict | None = None) -> dict:
    """The shipped values this item reads from manage, refusing if any differs from the declaration."""
    r = rules or manage_rules()
    p, c = r["portfolio"], r["credit"]
    cash = p["buying_power"]["cash"]
    got = {
        "spread_risk": cash["spread_risk_pct_nlv"],
        "theta": p["theta"]["max_pct_nlv"],
        "front_dte": p["theta"]["front_dte"],
        "stress": cash["stress"]["max_loss_pct_nlv"],
        "stress_move": cash["stress"]["move"],
        "stress_iv": cash["stress"]["iv_shift"],
        "volexp": p["vol_expansion"]["max_loss_pct_nlv"],
        "volexp_move": p["vol_expansion"]["move"],
        "volexp_iv": p["vol_expansion"]["iv_shift"],
        "expiry": p["expiry_concentration"]["max_pct_nlv"],
        "earnings": p["earnings_exposure"]["max_pct_nlv"],
        "risk_pct": r["risk"]["max_risk_pct"],
        "capture": c["take_profit"]["capture"],
        "stop_multiple": c["stop"]["loss_multiple_of_credit"],
        "stop_fallback": c["stop"]["fallback_max_loss_fraction"],
        "exit_dte": c["time_exit"]["dte"],
    }
    moved = {k: (DECLARED[k], got[k]) for k in DECLARED if not math.isclose(float(got[k]), DECLARED[k])}
    if moved:
        raise SystemExit(f"manage's shipped values differ from item 109's declaration: {moved}")
    return got


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item109"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------------
# Pure: the universe, the entry, the condor, the exit.


def monthlies() -> list[str]:
    a, b = date.fromisoformat(SPEC["first_expiry"]), date.fromisoformat(SPEC["last_expiry"])
    return [e.isoformat() for e in kc.monthlies_between(a, b)]


def coverage_days(expiry: str) -> list[str]:
    """The trading sessions from 45 to 20 calendar days before `expiry`, both included."""
    hi, lo = SPEC["coverage"]["days"]
    d, last, out = date.fromisoformat(expiry) - timedelta(days=hi), date.fromisoformat(expiry), []
    while (last - d).days >= lo:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def coverage(have: dict[tuple[str, str], set[str]], expiries: list[str]) -> dict[str, float]:
    """Each symbol's share of the coverage window's monthlies whose sessions 45 to 20 days out are all
    stored. `have` maps (symbol, expiry) to its stored sessions."""
    cov = SPEC["coverage"]
    inside = [e for e in expiries if cov["from"] <= e <= cov["through"]]
    out = {}
    for sym in sorted({s for s, _ in have}):
        ok = sum(1 for e in inside if set(coverage_days(e)) <= have.get((sym, e), set()))
        out[sym] = ok / len(inside) if inside else 0.0
    return out


def universe(cov: dict[str, float]) -> list[str]:
    return sorted(
        s for s, v in cov.items() if v >= SPEC["coverage"]["min_share"] and s not in SPEC["left_out"]
    )


def path_requests(names: list[str], expiries: list[str]) -> list[dict]:
    out = []
    for e in expiries:
        start = (date.fromisoformat(e) - timedelta(days=SPEC["path_days"])).isoformat()
        out += [{"symbol": s, "expiration": e, "start": start, "through": e} for s in names]
    return out


def entry_session(sessions: list[str], expiry: str) -> str | None:
    """The first stored session at or under `entry_max_days` calendar days to the expiry; None when
    that session is under `entry_min_days` (the path's early sessions are missing)."""
    for s in sessions:
        days = kc.days_between(s, expiry)
        if days <= SPEC["entry_max_days"]:
            return s if days >= SPEC["entry_min_days"] else None
    return None


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def _two_sided(q: dict | None, need_bid: bool = False) -> bool:
    if (
        q is None
        or q["bid"] is None
        or q["ask"] is None
        or q["ask"] <= 0
        or q["bid"] < 0
        or q["bid"] > q["ask"]
    ):
        return False
    return q["bid"] > 0 if need_bid else True


def condor(chain) -> dict | str:
    """The declared iron condor on one entry chain, or why there is none: shorts nearest 0.20 delta,
    both wings one width, the listed width whose credit at mid is nearest a third of it, from 1% to
    10% of the forward, the credit between 15% and 45% of the width."""
    if chain is None or not chain.forward:
        return "no entry forward"
    sp, sc = chain.nearest("P", SPEC["short_delta"]), chain.nearest("C", SPEC["short_delta"])
    if not _two_sided(sp, True) or not _two_sided(sc, True):
        return "no short leg"
    lo, hi = (w * chain.forward for w in SPEC["width_band"])
    puts, calls = chain.by["P"], chain.by["C"]
    best = None
    for k in sorted(puts):
        w = round(sp["strike"] - k, 6)
        if not lo <= w <= hi:
            continue
        lp, lc = puts.get(k), calls.get(round(sc["strike"] + w, 6))
        if not _two_sided(lp) or not _two_sided(lc):
            continue
        credit = _mid(sp) + _mid(sc) - _mid(lp) - _mid(lc)
        share = credit / w
        key = (abs(share - SPEC["credit_share"]), w)
        if best is None or key < best[0]:
            best = (key, w, lp, lc, credit, share)
    if best is None:
        return "no width"
    _, w, lp, lc, credit, share = best
    if not SPEC["credit_band"][0] <= share <= SPEC["credit_band"][1]:
        return "credit outside band"
    legs = [(sp, -1), (lp, 1), (sc, -1), (lc, 1)]
    return {"legs": legs, "width": w, "credit": 100.0 * credit}


def close_cost(chain, legs: list[tuple[dict, int]]) -> float | None:
    """Buying the condor back at mid, per contract, dollars; None without a two-sided quote on every leg."""
    if chain is None:
        return None
    total = 0.0
    for q, side in legs:
        now = chain.by[q["right"]].get(q["strike"])
        if not _two_sided(now):
            return None
        total += -side * _mid(now) * 100.0
    return total


def exit_rule(
    credit: float, width: float, cost: float, days_left: int, m: dict, stop: bool = True
) -> str | None:
    """manage's credit rules on one mark, per contract: the stop first, then the target, then the time."""
    loss = cost - credit
    stop_at = m["stop_multiple"] * credit
    if credit + stop_at > width:  # the credit stop cannot fire on this condor
        stop_at = m["stop_fallback"] * (width - credit)
    if stop and loss >= stop_at:
        return "stop"
    if cost <= (1 - m["capture"]) * credit:
        return "target"
    if days_left <= m["exit_dte"]:
        return "time"
    return None


def closing_costs(symbol: str, quotes: list[dict]) -> float:
    """Slippage and fees to buy one condor back."""
    return fees.slippage_dollars(quotes, 1, {}, symbol=symbol) + fees.ic_close_fee(
        symbol, 1, legs=4, sell_legs=2, ndigits=4
    )


def settle_forward(chain) -> float | None:
    """The expiry close's parity forward (item 92's rule)."""
    if chain is None:
        return None
    pairs = []
    for k, c in chain.by["C"].items():
        p = chain.by["P"].get(k)
        if _two_sided(c) and _two_sided(p):
            pairs.append((abs(_mid(c) - _mid(p)), k, k + _mid(c) - _mid(p)))
    return min(pairs)[2] if pairs else None


def leg_dict(q: dict, side: int, chain, iv: float | None) -> dict:
    """A leg as manage's portfolio functions take it, one contract."""
    dte = max(kc.days_between(chain.session, chain.expiry), 0)
    return {
        "quantity": side,
        "right": q["right"],
        "strike": q["strike"],
        "dte": dte,
        "years": max(dte, 1) / 365.0,
        "iv": iv,
        "mid": None,
        "extrinsic": None,
        "div_yield": 0.0,
    }


def figures(
    chain, legs, ivs: list[float | None], betas: dict[str, float | None], m: dict, cost: float, width: float
) -> dict:
    """One contract's cap figures on one session: theta (legs over `front_dte` days), what it can
    still lose, and the stress and vol-expansion P&L by manage's `book_pnl` at each beta reading
    (`stress:beta`, `volexp:beta3y`, ...)."""
    from cherrypick.manage import portfolio as mp

    rows = [leg_dict(q, side, chain, iv) for (q, side), iv in zip(legs, ivs, strict=True)]
    theta = 0.0
    for leg in rows:
        if leg["dte"] > m["front_dte"] and leg["iv"]:
            g = mp.leg_greeks(leg, chain.forward, 0.0)
            theta += g["theta"] if g else 0.0
    out = {"theta": theta, "remaining": max(width - cost, 0.0)}
    for reading, beta in betas.items():
        # The benchmark spot is only checked for presence: each name moves by its own beta x the move.
        name = [{"underlying": "x", "spot": chain.forward, "beta": beta, "legs": rows}]
        out[f"stress:{reading}"] = mp.book_pnl(name, 1.0, 0.0, m["stress_move"], m["stress_iv"], 0.0, True)[0]
        out[f"volexp:{reading}"] = mp.book_pnl(name, 1.0, 0.0, m["volexp_move"], m["volexp_iv"], 0.0, True)[0]
    return out


def returns_against(name: list[tuple[str, float]], bench: dict[str, float]) -> list[tuple[str, float, float]]:
    """(date, the name's daily return, SPY's) over the days both closed. A day the name fell more
    than 45% is a split, left out with SPY's same day."""
    out = []
    for (d0, c0), (d1, c1) in zip(name, name[1:], strict=False):
        b0, b1 = bench.get(d0), bench.get(d1)
        if not (c0 and c1 and b0 and b1):
            continue
        r = c1 / c0 - 1.0
        if r <= SPEC["split_return"]:
            continue
        out.append((d1, r, b1 / b0 - 1.0))
    return out


def beta_at(rets: list[tuple[str, float, float]], session: str, returns: int) -> float | None:
    """The OLS beta of the last `returns` of `rets` dated before `session`; None under 90% of them."""
    import bisect

    i = bisect.bisect_left(rets, (session,))
    rs = rets[max(0, i - returns) : i]
    if len(rs) < returns * 0.9:
        return None
    mx, mb = statistics.fmean(r for _, r, _ in rs), statistics.fmean(b for _, _, b in rs)
    var = sum((b - mb) ** 2 for _, _, b in rs)
    return sum((r - mx) * (b - mb) for _, r, b in rs) / var if var else None


def trade(symbol: str, expiry: str, path: list[tuple[str, object]], betas: dict, m: dict) -> dict:
    """One candidate condor from its entry to its exit, per contract, or {"skip": why}. `path` is
    [(session, Chain)] from the entry through the expiry. `betas` are the name's beta per session by
    each reading (`beta`, `beta3y`); a session's figures use that session's beta."""
    entry, c0 = path[0]
    built = condor(c0)
    if isinstance(built, str):
        return {"skip": built}
    legs, w, credit = built["legs"], 100.0 * built["width"], built["credit"]
    quotes = [q for q, _ in legs]
    slip_open = fees.slippage_dollars(quotes, 1, {}, symbol=symbol)
    fee_open = fees.ic_open_fee(symbol, 1, legs=4, sell_legs=2, ndigits=4)
    ivs = [c0.iv(q) for q in quotes]
    series: dict[str, dict] = {}
    exit_s = kind = cost = close_costs = None
    no_stop = no_stop_costs = None
    last_cost = credit
    for session, chain in path:
        if session >= expiry:
            break
        mark = close_cost(chain, legs) if session != entry else credit
        fresh = mark is not None
        if fresh:
            last_cost = mark
            ivs = [
                (chain.iv(chain.by[q["right"]][q["strike"]]) or iv) for q, iv in zip(quotes, ivs, strict=True)
            ]
        else:
            mark = last_cost
        if exit_s is None:
            # The book reads figures only while the trade is held.
            if chain is not None and chain.forward:
                here = {r: betas[r].get(session) for r in betas}
                series[session] = {"mark": mark, **figures(chain, legs, ivs, here, m, mark, w)}
            else:
                series[session] = {"mark": mark}
        if session == entry or not fresh:
            continue
        days_left = kc.days_between(session, expiry)
        quotes_now = [chain.by[q["right"]][q["strike"]] for q in quotes]
        if no_stop is None and exit_rule(credit, w, mark, days_left, m, stop=False):
            no_stop = mark
            no_stop_costs = closing_costs(symbol, quotes_now)
        if exit_s is None and exit_rule(credit, w, mark, days_left, m):
            exit_s, kind, cost = session, exit_rule(credit, w, mark, days_left, m), mark
            close_costs = closing_costs(symbol, quotes_now)
        if exit_s is not None and no_stop is not None:
            break
    if exit_s is None or no_stop is None:
        last_session, last_chain = path[-1]
        f1 = settle_forward(last_chain) if last_session == expiry else None
        if f1 is None:
            return {"skip": "no exit mark and no expiry forward"}
        owed = sum(-side * bs.intrinsic(f1, q["strike"], q["right"]) for q, side in legs) * 100.0
        itm = [q for q, _ in legs if bs.intrinsic(f1, q["strike"], q["right"]) > 0]
        settlement = sum(
            fees.assignment_round_trip_fee(100, q["strike"], f1, direction="long", ndigits=4) for q in itm
        )
        if exit_s is None:
            exit_s, kind, cost, close_costs = expiry, "settled", owed, settlement
        if no_stop is None:
            no_stop, no_stop_costs = owed, settlement
    open_costs = slip_open + fee_open
    net = credit - cost - open_costs - close_costs
    return {
        "symbol": symbol,
        "expiry": expiry,
        "entry": entry,
        "exit": exit_s,
        "exit_kind": kind,
        "strikes": [q["strike"] for q in quotes],
        "width": w,
        "credit": credit,
        "max_loss": w - credit,
        "open_costs": open_costs,
        "close_costs": close_costs,
        "exit_cost": cost,
        "net": net,
        "net_no_stop": credit - no_stop - open_costs - no_stop_costs,
        "entry_spread": sum((q["ask"] - q["bid"]) * 100.0 for q in quotes) / credit,
        "series": series,
    }


# ------------------------------------------------------------------------------------------------
# Pure: the book.


def _fig(t: dict, session: str, key: str) -> float:
    """A trade's per-contract figure on `session`, else its latest before it."""
    best = None
    for s in t["_sessions"]:
        if s > session:
            break
        v = t["series"][s].get(key)
        if v is not None:
            best = v
    return best or 0.0


def spans(t: dict, reports: dict[str, list[list[str]]]) -> bool:
    """A report after the entry (or on the entry day after the close) on or before the expiry."""
    for d, when in reports.get(t["symbol"], []):
        if t["entry"] < d <= t["expiry"] or (d == t["entry"] and when == "After market close"):
            return True
    return False


def book(
    cands: list[dict],
    caps: dict[str, float],
    m: dict,
    *,
    order: str = "rich",
    beta: str = "beta",
    start: str | None = None,
    reports: dict | None = None,
) -> dict:
    """One arm's book over the candidates: daily net liq, weekly closes, trades taken, sessions a cap
    bound. A candidate enters only if the book with it stays inside every cap; caps gate entries only."""
    cands = [c for c in cands if start is None or c["entry"] >= start]
    for c in cands:
        c.setdefault("_sessions", sorted(c["series"]))
    by_entry = defaultdict(list)
    for c in cands:
        by_entry[c["entry"]].append(c)
    sessions = sorted({s for c in cands for s in c["series"]} | {c["exit"] for c in cands})
    cash, open_, taken, binds, daily = SPEC["start_nlv"], [], [], set(), {}
    for s in sessions:
        for t in [t for t in open_ if t["c"]["exit"] == s]:
            cash -= t["q"] * (t["c"]["exit_cost"] + t["c"]["close_costs"])
            open_.remove(t)
        nlv = cash - sum(t["q"] * _fig(t["c"], s, "mark") for t in open_)
        key = (
            (lambda c: (-c["credit"] / c["width"], c["symbol"]))
            if order == "rich"
            else (lambda c: c["symbol"])
        )
        for c in sorted(by_entry.get(s, []), key=key):
            q = math.floor(m["risk_pct"] * nlv / c["max_loss"]) if c["max_loss"] > 0 else 0
            if q < 1:
                continue
            if not inside(open_, c, q, s, nlv, caps, beta, reports):
                binds.add(s)
                continue
            cash += q * (c["credit"] - c["open_costs"])
            open_.append({"c": c, "q": q})
            taken.append({"symbol": c["symbol"], "entry": s, "q": q, "net": q * c["net"]})
        nlv = cash - sum(t["q"] * _fig(t["c"], s, "mark") for t in open_)
        daily[s] = {"nlv": nlv, "risk": sum(t["q"] * t["c"]["max_loss"] for t in open_) / nlv}
    return {"daily": daily, "taken": taken, "binds": sorted(binds)}


def inside(open_, c, q, s, nlv, caps, beta, reports) -> bool:
    """Whether the book with `q` of candidate `c` stays inside each cap on session `s`."""
    held = [*open_, {"c": c, "q": q}]
    for cap, limit in caps.items():
        if cap == "spread_risk":
            v = sum(t["q"] * t["c"]["max_loss"] for t in held) / nlv
            ok = v <= limit
        elif cap == "theta":
            v = sum(t["q"] * _fig(t["c"], s, "theta") for t in held) / nlv
            ok = v <= limit
        elif cap in ("stress", "volexp"):
            v = sum(t["q"] * _fig(t["c"], s, f"{cap}:{beta}") for t in held) / nlv
            ok = v >= -limit
        elif cap == "expiry":
            v = (
                sum(t["q"] * _fig(t["c"], s, "remaining") for t in held if t["c"]["expiry"] == c["expiry"])
                / nlv
            )
            ok = v <= limit
        elif cap == "earnings":
            v = sum(t["q"] * _fig(t["c"], s, "remaining") for t in held if spans(t["c"], reports or {})) / nlv
            ok = v <= limit
        else:
            raise ValueError(f"unknown cap {cap}")
        if not ok:
            return False
    return True


def weekly(daily: dict[str, dict]) -> list[dict]:
    """The last session of each week: its date, net liq, the week's return and the risk held."""
    last: dict[tuple[int, int], str] = {}
    for s in sorted(daily):
        last[date.fromisoformat(s).isocalendar()[:2]] = s
    out, prev = [], None
    for wk in sorted(last):
        s = last[wk]
        nlv = daily[s]["nlv"]
        if prev is not None:
            out.append({"date": s, "ret": nlv / prev - 1.0, "risk": daily[s]["risk"]})
        prev = nlv
    return out


def es(rets: list[float], share: float) -> float:
    """Expected shortfall: the mean loss of the worst `share` of returns, positive for a loss."""
    k = max(1, math.ceil(share * len(rets)))
    return -statistics.fmean(sorted(rets)[:k])


def tail_per_risk(weeks: list[dict], share: float) -> float | None:
    risk = statistics.fmean(w["risk"] for w in weeks) if weeks else 0.0
    return es([w["ret"] for w in weeks], share) / risk if risk > 0 else None


def return_per_risk(weeks: list[dict]) -> float | None:
    risk = statistics.fmean(w["risk"] for w in weeks) if weeks else 0.0
    return statistics.fmean(w["ret"] for w in weeks) / risk if risk > 0 else None


def compare(arm: list[dict], base: list[dict], rule: dict, *, resamples: int | None = None) -> dict:
    """The arm's tail per dollar at risk over `none`'s on the same weeks, with a stationary block
    bootstrap of its log (and of the return-per-risk difference), both books drawn on the same weeks."""
    share = rule["tail_share"]
    by = {w["date"]: w for w in base}
    pairs = [(a, by[a["date"]]) for a in arm if a["date"] in by]
    if len(pairs) < 20:
        return {"weeks": len(pairs), "tail_ratio": None}
    a_w, b_w = [p[0] for p in pairs], [p[1] for p in pairs]
    ta, tb = tail_per_risk(a_w, share), tail_per_risk(b_w, share)
    ra, rb = return_per_risk(a_w), return_per_risk(b_w)
    if not ta or not tb or ta <= 0 or tb <= 0:
        return {"weeks": len(pairs), "tail_ratio": None}
    ratio = ta / tb
    rng = random.Random(rule["seed"])
    logs, diffs = [], []
    for _ in range(resamples or rule["resamples"]):
        idx = stationary_indices(len(pairs), rule["block_weeks"], rng)
        xa, xb = [a_w[i] for i in idx], [b_w[i] for i in idx]
        sa, sb = tail_per_risk(xa, share), tail_per_risk(xb, share)
        if sa and sb and sa > 0 and sb > 0:
            logs.append(math.log(sa / sb))
        pa, pb = return_per_risk(xa), return_per_risk(xb)
        if pa is not None and pb is not None:
            diffs.append(pa - pb)
    se = statistics.stdev(logs) if len(logs) > 1 else None
    se_r = statistics.stdev(diffs) if len(diffs) > 1 else None
    return {
        "weeks": len(pairs),
        "tail_ratio": ratio,
        "t": (-math.log(ratio) / se) if se else None,
        "return_per_risk": ra,
        "return_per_risk_none": rb,
        "return_t": ((ra - rb) / se_r) if se_r and ra is not None and rb is not None else None,
    }


def _p_one_sided(t: float | None) -> float:
    return 1.0 if t is None else 0.5 * math.erfc(t / math.sqrt(2.0))


def holm(ps: dict[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values."""
    order = sorted(ps, key=lambda k: ps[k])
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * ps[k]))
        out[k] = run
    return out


PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no_verdict", "not_read"


def judge(parts: dict[str, dict[str, dict]], rule: dict) -> dict:
    """RULE over every judged arm. `parts[arm]` holds `design` and `holdout`, each a `compare` result
    plus `n` (trades) and `binds` (sessions a cap bound)."""
    alpha = _p_one_sided(rule["t_bar"])
    ps = {a: _p_one_sided(parts[a]["design"].get("t")) for a in parts}
    adj = holm(ps) if rule["holm"] else ps
    arms, verdict = {}, {}
    for a, p in parts.items():
        d, h = dict(p["design"]), dict(p["holdout"])
        if d["n"] < rule["min_n"] or len(d["binds"]) < rule["min_binds"] or d.get("tail_ratio") is None:
            d["verdict"] = NO_VERDICT
        else:
            bad_return = d.get("return_t") is not None and d["return_t"] <= rule["return_t_bar"]
            ok = d["tail_ratio"] <= rule["max_ratio"] and adj[a] <= alpha and not bad_return
            d["verdict"] = PASS if ok else FAIL
        d["p_holm"] = adj[a]
        n_holdout = h.get("n", 0)
        if d["verdict"] != PASS:
            h = {"verdict": NOT_READ}
        elif h["n"] < rule["min_n"] or len(h["binds"]) < rule["min_binds"] or h.get("tail_ratio") is None:
            h["verdict"] = NO_VERDICT
        else:
            h["verdict"] = PASS if h["tail_ratio"] < rule["holdout_max_ratio"] else FAIL
        word = h["verdict"] if d["verdict"] == PASS else d["verdict"]
        arms[a] = {"n": d["n"] + n_holdout, "parts": [d, h], "verdict": word}
        verdict[a] = word
    return {"arms": arms, "verdict": verdict}


def drawdown(daily: dict[str, dict]) -> dict:
    peak, worst, worst_at, start, length, longest = None, 0.0, None, None, 0, 0
    for s in sorted(daily):
        v = daily[s]["nlv"]
        if peak is None or v >= peak:
            peak, start = v, s
            length = 0
            continue
        length += 1
        longest = max(longest, length)
        dd = v / peak - 1.0
        if dd < worst:
            worst, worst_at = dd, (start, s)
    return {"worst": worst, "from_to": worst_at, "longest_sessions_under_peak": longest}


EPISODES = {
    "2018-02": ("2018-01-22", "2018-03-30"),
    "2018-Q4": ("2018-09-20", "2018-12-31"),
    "2020-03": ("2020-02-14", "2020-04-30"),
    "2022": ("2022-01-03", "2022-12-30"),
    "2025-04": ("2025-03-24", "2025-05-30"),
}


def calibration(res: dict, split: str) -> dict:
    """The reported table for one book: per part, its return, drawdown, worst week, tail and risk,
    how often the cap bound, and each named episode's drawdown."""
    out = {}
    for name, keep in (("design", lambda s: s < split), ("holdout", lambda s: s >= split)):
        daily = {s: v for s, v in res["daily"].items() if keep(s)}
        weeks = [w for w in weekly(res["daily"]) if keep(w["date"])]
        if not daily or not weeks:
            out[name] = {}
            continue
        first, last = min(daily), max(daily)
        out[name] = {
            "return": daily[last]["nlv"] / daily[first]["nlv"] - 1.0,
            "drawdown": drawdown(daily),
            "worst_week": min(w["ret"] for w in weeks),
            "es5": es([w["ret"] for w in weeks], 0.05),
            "mean_risk": statistics.fmean(w["risk"] for w in weeks),
            "peak_risk": max(w["risk"] for w in weeks),
            "trades": sum(1 for t in res["taken"] if keep(t["entry"])),
            "bound_sessions": sum(1 for b in res["binds"] if keep(b)),
        }
    out["episodes"] = {
        k: drawdown({s: v for s, v in res["daily"].items() if a <= s <= b}) for k, (a, b) in EPISODES.items()
    }
    return out


# ------------------------------------------------------------------------------------------------
# The run.


def closes(symbol: str) -> list[tuple[str, float]]:
    import sqlite3

    from cherrypick.core import home

    db = home.data_dir() / "technicals" / "history.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT date, close FROM bars WHERE symbol = ? ORDER BY date", (symbol,)
        ).fetchall()
    finally:
        conn.close()


def candidates(
    inputs: Inputs, conn, names: list[str], m: dict, rep=None, *, through: str | None = None
) -> tuple[list[dict], dict]:
    """Every candidate condor, per contract, with its daily figures; and the skips by reason.
    `through` stops at that expiry (the smoke's sample)."""
    bench = dict(closes("SPY"))
    keys = sorted(
        {(s, e) for s, e, _, _ in inputs.keys if s in names and (through is None or e <= through)},
        key=lambda k: (k[1], k[0]),
    )
    if rep is not None:
        rep.split("paths", [s for s, _ in keys], unit="paths")
    rets = {s: returns_against(closes(s), bench) for s in names}
    beta_cache: dict[tuple[str, str, int], float | None] = {}

    def beta(sym: str, session: str, n: int) -> float | None:
        if sym == "SPY":
            return 1.0
        k = (sym, session, n)
        if k not in beta_cache:
            beta_cache[k] = beta_at(rets[sym], session, n)
        return beta_cache[k]

    out, skips = [], defaultdict(int)
    for sym, exp in keys:
        if rep is not None:
            rep.advance("paths", key=sym, current=exp)
        sessions = inputs.sessions(conn, sym, exp)
        day = entry_session(sessions, exp)
        if day is None:
            skips["no entry session"] += 1
            continue
        path = [(s, inputs.chain(conn, sym, s, exp)) for s in sessions if s >= day]
        betas: dict[str, dict[str, float | None]] = {"beta": {}, "beta3y": {}}
        for s, _ in path:
            for reading, n in (("beta", SPEC["beta_returns"]), ("beta3y", 3 * SPEC["beta_returns"])):
                betas[reading][s] = beta(sym, s, n)
        t = trade(sym, exp, path, betas, m)
        if "skip" in t:
            skips[t["skip"]] += 1
        else:
            out.append(t)
    return out, dict(skips)


def none_scaled(table: dict) -> dict:
    """Per judged arm and part, `none`'s tail and return put on the arm's mean risk. A part is left
    out where either book has no weeks or `none` held no risk in it: there is nothing to scale by,
    as `tail_per_risk` and `return_per_risk` read a book with no risk as no figure."""
    out = {}
    for a in JUDGED:
        ra, rn = table[a], table["none"]
        out[a] = {
            p: {
                "scale": ra[p]["mean_risk"] / rn[p]["mean_risk"],
                "es5": rn[p]["es5"] * ra[p]["mean_risk"] / rn[p]["mean_risk"],
                "return_per_risk_none": rn[p]["return"] / rn[p]["mean_risk"],
            }
            for p in ("design", "holdout")
            if ra.get(p) and rn.get(p) and rn[p]["mean_risk"] > 0
        }
    return out


def study(cands: list[dict], m: dict, reports: dict, *, resamples: int | None = None) -> dict:
    split = RULE["splits"][0]
    runs = {a: book(cands, caps, m) for a, caps in ARMS.items()}
    runs.update({a: book(cands, caps, m, order=o, beta=b) for a, (caps, o, b) in REPORTED.items()})
    start = SPEC["earnings_from"]
    runs.update({a: book(cands, caps, m, start=start, reports=reports) for a, caps in EARNINGS_ARMS.items()})
    weeks = {a: weekly(r["daily"]) for a, r in runs.items()}

    def part(a: str, base: str, keep) -> dict:
        c = compare([w for w in weeks[a] if keep(w["date"])], [w for w in weeks[base] if keep(w["date"])],
                    RULE, resamples=resamples)  # fmt: skip
        return {
            **c,
            "n": sum(1 for t in runs[a]["taken"] if keep(t["entry"])),
            "binds": [b for b in runs[a]["binds"] if keep(b)],
        }

    design, holdout = (lambda s: s < split), (lambda s: s >= split)
    parts = {a: {"design": part(a, "none", design), "holdout": part(a, "none", holdout)} for a in JUDGED}
    block = judge(parts, RULE)
    reported = {
        a: {"design": part(a, base, design), "holdout": part(a, base, holdout)}
        for a, base in [*((r, "none") for r in REPORTED), ("earnings10", "none-2020")]
    }
    table = {a: calibration(r, split) for a, r in runs.items()}
    scaled = none_scaled(table)
    return {
        "rule": RULE,
        **block,
        "reported": {"arms": reported, "calibration": table, "none_scaled": scaled},
    }


def cmd_plan(args) -> int:
    from cherrypick.backtest.chainfetch import read_path_requests  # noqa: F401  (the format it writes)

    conn = chain_store()
    try:
        have: dict[tuple[str, str], set[str]] = defaultdict(set)
        for sym, sess, exp in conn.execute(
            "SELECT symbol, session, expiration FROM eod_chain_requests WHERE status = 'complete'"
        ):
            if kc.is_listed_monthly(exp):
                have[(sym, exp)].add(sess)
    finally:
        conn.close()
    exps = monthlies()
    cov = coverage(have, exps)
    names = universe(cov)
    reqs = path_requests(names, exps)
    fetch = [r for r in reqs if r["start"] >= SPEC["fetch_from"]]
    d = out_dir()
    (d / "path_requests.json").write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    (d / "fetch_requests.json").write_text(json.dumps({"requests": fetch}, indent=1), encoding="utf-8")
    reports = earnings_dates([n for n in names if n not in SPEC["funds"]], args.host, args.port)
    (d / "earnings.json").write_text(json.dumps(reports, indent=1), encoding="utf-8")
    (d / "universe.json").write_text(
        json.dumps({"names": names, "coverage": cov, "spec": SPEC}, indent=1), encoding="utf-8"
    )
    print(json.dumps({"names": len(names), "paths": len(reqs), "to_fetch": len(fetch)}))
    return 0


def earnings_dates(names: list[str], host: str, port: int) -> dict[str, list[list[str]]]:
    """Each name's report dates and timing from Dolt's earnings calendar."""
    import mysql.connector

    conn = mysql.connector.connect(
        host=host, port=port, user="root", database="earnings", connection_timeout=10
    )
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(names))
        cur.execute(
            f"SELECT act_symbol, date, `when` FROM earnings_calendar WHERE act_symbol IN ({marks})",
            tuple(names),
        )
        out: dict[str, list[list[str]]] = defaultdict(list)
        for s, d, w in cur.fetchall():
            out[s].append([d.isoformat(), w or ""])
        return {s: sorted(v) for s, v in out.items()}
    finally:
        conn.close()


def cmd_study(args) -> int:
    d = out_dir()
    m = shipped()
    names = json.loads((d / "universe.json").read_text(encoding="utf-8"))["names"]
    reports = json.loads((d / "earnings.json").read_text(encoding="utf-8"))
    inputs = Inputs(d / "path_requests.json")
    conn = chain_store()
    try:
        with core_progress.Reporter("book_caps_item109-study") as rep:
            # The smoke reads four names through 2016, enough to run every arm end to end.
            sample = (names[:4], "2016-12-31") if args.smoke else (names, None)
            cands, skips = candidates(inputs, conn, sample[0], m, rep, through=sample[1])
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    lean = [{k: v for k, v in c.items() if k != "series"} for c in cands]
    if not args.smoke:
        (d / "candidates.json").write_text(json.dumps(lean, indent=0), encoding="utf-8")
    block = study(cands, m, reports, resamples=200 if args.smoke else None)
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 109,
        **block,
        "candidates": len(cands),
        "skips": skips,
        "inputs": read,
        "corrections": list(REPLAY_CORRECTIONS),
    }
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    path = Path(args.out) if args.out else d / ("smoke.json" if args.smoke else "results.json")
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.set_defaults(fn=cmd_plan)
    s = sub.add_parser("study")
    s.add_argument("--out", default=None)
    s.add_argument("--smoke", action="store_true", help="run on what the store holds so far, 200 draws")
    s.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
