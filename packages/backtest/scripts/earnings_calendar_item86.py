"""Item 86: an at-the-money calendar across an earnings report, held until the stock returns to its strike.

The declaration is research/edge-hypotheses-plan.md section 86; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A published trader's rule: on liquid names whose past reports moved little and came back quickly,
buy a calendar at the money in the session before the report (front two to four weeks out, back three
to four months out), and close it when the stock trades back through the strike, within about ten
sessions, or at a 25% stop on the spread. The front leg takes the post-report crush; the back holds.

- `plan`: the events (item 31's, on the 35 declared names), each one's control session (item 31's
  `control_for`), their expiries and holding windows, and the chain paths to fetch
  (`{results}/path_requests.json`, newest first so the sample fetch covers recent reports).
- `study`: one run over every event, judged by RULE (the study kit's three bars) and EVENT_RULE (the
  no-report control, the halves, the stress, the best five; `quiet` also against the unselected
  reports), cut at the split. `--smoke` reads the sample and writes counts only.

Item 87 (the biased, out-of-the-money calendar) loads this script's pricing and reads the same paths.
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
from cherrypick.core import fees, progress, treasury

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

_spec = importlib.util.spec_from_file_location(
    "earnings_item31", Path(__file__).with_name("earnings_item31.py")
)
e31 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_item31", e31)
_spec.loader.exec_module(e31)

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = (
    {
        "on": "2026-11-14",
        "what": "`returned` reads any close in the 10 sessions after the reaction, not the 10th close alone",
        "why": "the trader's words are 'within five to 10 trading days'; the 10th-close reading selected 14 "
        "design reports, under the 100-trade floor, so quiet could only be no verdict. Selector counts only "
        "were read, no trade outcome; the threshold (6 of 8) is unchanged and the old reading is reported",
        "decided": "user, 2026-11-14",
        "before_read": True,
    },
)

#: The 35 names: per sector, the five with the most options dollar volume in technicals' liquidity
#: verdict of 2026-11-13 (`chain_volume_usd`), among item 31's names with at least 20 of its events
#: and an options reading; GOOG left out for GOOGL (one report). Frozen here so the sample cannot move
#: with a later verdict.
NAMES = {
    "Technology": ("NVDA", "AAPL", "AMD", "MSFT", "AVGO"),
    "Communication Services": ("GOOGL", "TTWO", "NFLX", "APP", "DIS"),
    "Consumer Discretionary": ("AMZN", "TSLA", "BABA", "MCD", "HD"),
    "Financials": ("COIN", "JPM", "V", "BAC", "C"),
    "Healthcare": ("UNH", "JNJ", "MRNA", "MRK", "PFE"),
    "Energy": ("CVX", "VLO", "XOM", "CCJ", "OXY"),
    "Consumer Staples": ("WMT", "KO", "PEP", "PG", "TGT"),
}
SECTOR = {n: s for s, ns in NAMES.items() for n in ns}

#: The trade as declared.
SPEC = {
    "front_days": [14, 28],  # calendar days from entry; the Friday expiry nearest the target
    "front_target_days": 21,
    "front_min_sessions_after_reaction": 7,
    "back_days": [84, 126],  # the standard monthly nearest the target (monthlies can sit 35 days apart)
    "back_target_days": 105,
    "hold_sessions": 10,  # sessions after the reaction session
    "front_buffer_sessions": 2,  # out by two sessions before the front expires
    "stop_frac": 0.25,  # of the entry spread's mid
    "right": "C",
    "quiet_prior": 8,
    "quiet_needed": 6,
    "return_sessions": 10,  # back in the pre-report range at any close within 10 sessions of the reaction
    "range_sessions": 10,  # the pre-report range: the 10 sessions ending at the entry session
}

#: The kit's pass rule, per arm, on one row per trade. t 2.3 keeps Holm across the two arms from
#: binding.
RULE = {
    "t_bar": 2.3,
    "min_n": 100,
    "holdout_min_n": 40,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 86},
    "delta": False,
    "splits": ["2024-07-01"],
}

#: The second rule: the report must be what earns.
EVENT_RULE = {
    "t_bar": 2.3,
    "halves": "2022-07-01",
    "best_removed": 5,
    "stress": {"fees": 1.5, "slippage_frac_of_spread": 0.25},
    "quiet_beats_unselected": True,
}

ARMS = ("all", "quiet")

#: The research-queue chain. A sample of the paths is fetched and smoke-read first; the full fetch
#: runs twice so the second pass retries what the first could not get.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
        "repeat": 2,
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10


# --------------------------------------------------------------------------- pure: dates


def sessions_after(day: date, n: int) -> date:
    """The n-th trading session after `day`."""
    for _ in range(n):
        day = cal.next_trading_day(day)
    return day


def sessions_before(day: date, n: int) -> date:
    for _ in range(n):
        day = cal.previous_trading_day(day)
    return day


def session_count(a: date, b: date) -> int:
    """Trading sessions strictly after `a` up to and including `b`."""
    n, d = 0, a
    while d < b:
        d = cal.next_trading_day(d)
        n += 1
    return n


def front_expiry(entry: date, reaction: date, spec: dict = SPEC) -> date | None:
    """The weekly expiry nearest `front_target_days` after entry, within `front_days`, with at least
    `front_min_sessions_after_reaction` sessions after the reaction; the earlier on a tie."""
    lo, hi = spec["front_days"]
    ok = [
        e
        for e in e31.weekly_candidates(entry, hi)
        if lo <= (e - entry).days <= hi
        and session_count(reaction, e) >= spec["front_min_sessions_after_reaction"]
    ]
    return min(ok, key=lambda e: (abs((e - entry).days - spec["front_target_days"]), e)) if ok else None


def monthly_expiries(start: date, months: int = 6) -> list[date]:
    """The standard monthlies (the third Friday, the Thursday before when it is a holiday) from
    `start`'s month on."""
    out, y, m = [], start.year, start.month
    for _ in range(months):
        first = date(y, m, 1)
        fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        out.append(fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def back_expiry(entry: date, spec: dict = SPEC) -> date | None:
    lo, hi = spec["back_days"]
    ok = [e for e in monthly_expiries(entry) if lo <= (e - entry).days <= hi]
    return min(ok, key=lambda e: (abs((e - entry).days - spec["back_target_days"]), e)) if ok else None


def horizon(reaction: date, front: date, spec: dict = SPEC) -> date:
    """The last session the trade may be held: `hold_sessions` after the reaction, or
    `front_buffer_sessions` before the front expires, whichever is first."""
    return min(
        sessions_after(reaction, spec["hold_sessions"]), sessions_before(front, spec["front_buffer_sessions"])
    )


def trade_dates(entry: date, reaction: date, spec: dict = SPEC) -> dict | None:
    front = front_expiry(entry, reaction, spec)
    back = back_expiry(entry, spec)
    if front is None or back is None:
        return None
    return {
        "entry": entry,
        "reaction": reaction,
        "front": front,
        "back": back,
        "last": horizon(reaction, front, spec),
    }


def path_requests(symbol: str, d: dict) -> list[dict]:
    """The two paths one trade reads: the front and the back, from entry through its last session."""
    return [
        {
            "symbol": symbol,
            "expiration": x.isoformat(),
            "start": d["entry"].isoformat(),
            "through": d["last"].isoformat(),
        }
        for x in (d["front"], d["back"])
    ]


# --------------------------------------------------------------------------- pure: selection


def returned(
    bars: dict[date, tuple], entry: date, reaction: date, spec: dict = SPEC, *, at_last_only: bool = False
) -> bool | None:
    """Whether any close in the `return_sessions` sessions after the reaction is inside the range (low
    to high) of the `range_sessions` sessions ending at the entry session (the trader's "back where you
    would expect it within five to 10 trading days"). `at_last_only` reads the last of those closes
    alone, the first declaration's reading, reported beside. None without the bars."""
    days, d = [], entry
    for _ in range(spec["range_sessions"]):
        days.append(d)
        d = cal.previous_trading_day(d)
    pre = [bars.get(x) for x in days]
    later, d = [], reaction
    for _ in range(spec["return_sessions"]):
        d = cal.next_trading_day(d)
        later.append(bars.get(d))
    if at_last_only:
        later = later[-1:]
    if any(b is None for b in pre) or any(b is None for b in later):
        return None
    lo, hi = min(b[2] for b in pre), max(b[1] for b in pre)
    return any(lo <= b[3] <= hi for b in later)


def quiet_status(history: list[bool], spec: dict = SPEC) -> bool | None:
    """`quiet` from the name's earlier reports (oldest first, each whether it returned): at least
    `quiet_needed` of the last `quiet_prior`. None with fewer than `quiet_prior`."""
    if len(history) < spec["quiet_prior"]:
        return None
    return sum(history[-spec["quiet_prior"] :]) >= spec["quiet_needed"]


def prior_returns(
    report_rows: list[tuple[date, str | None]],
    report: date,
    bars: dict,
    spec: dict = SPEC,
    *,
    at_last_only: bool = False,
) -> list[bool]:
    """Each earlier stated-timing report's `returned`, oldest first; reports without bars left out."""
    out = []
    for d, when in sorted(report_rows):
        timing = e31.TIMINGS.get(when or "")
        if timing is None or d >= report:
            continue
        r = returned(
            bars,
            e31.entry_session(d, timing),
            e31.reaction_session(d, timing),
            spec,
            at_last_only=at_last_only,
        )
        if r is not None:
            out.append(r)
    return out


# --------------------------------------------------------------------------- pure: the calendar


def two_sided(q: dict | None) -> bool:
    return bool(q) and q["bid"] is not None and q["ask"] is not None and q["bid"] > 0 and q["ask"] >= q["bid"]


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def legs(front, back, right: str, strike: float) -> tuple[dict, dict] | None:
    """The front and back quotes at `strike` on `right`, both two-sided, or None."""
    qf, qb = front.by[right].get(strike), back.by[right].get(strike)
    return (qf, qb) if two_sided(qf) and two_sided(qb) else None


def strike_near(front, back, right: str, target: float) -> float | None:
    """The listed strike nearest `target` quoted two-sided in both expiries; the lower on a tie."""
    ks = [k for k in front.by[right] if legs(front, back, right, k)]
    return min(ks, key=lambda k: (abs(k - target), k)) if ks else None


def costs_cfg(stress: dict | None) -> tuple[dict, float]:
    if not stress:
        return {}, 1.0
    return {"tastytrade_costs": {"slippage_frac_of_spread": stress["slippage_frac_of_spread"]}}, stress[
        "fees"
    ]


def price_trade(
    symbol: str,
    right: str,
    strike: float,
    marks: dict[date, tuple[dict, dict] | None],
    bars: dict[date, tuple],
    d: dict,
    *,
    stop_frac: float | None,
    touch: bool = True,
    last: date | None = None,
    stress: dict | None = None,
) -> dict:
    """One calendar, bought at the entry session's mids plus slippage and fees, and sold at the first
    of: a session from the reaction on whose range holds the strike (`touch`), the spread's mid at or
    under (1 - stop_frac) of its entry mid, or the last session. An exit due on a session with no
    two-sided mark waits for the next one; with none by the last session it is {"skip": ...}.
    `marks` holds each session's (front, back) quotes at the strike, None where not two-sided."""
    cfg, fee_x = costs_cfg(stress)
    first = marks.get(d["entry"])
    if first is None:
        return {"skip": "entry_unquoted"}
    m0 = mid(first[1]) - mid(first[0])
    if m0 <= 0:
        return {"skip": "no_debit"}
    open_cost = fees.slippage_dollars([first[0], first[1]], 1, cfg) + fee_x * fees.ic_open_fee(
        symbol, 1, legs=2, sell_legs=1, ndigits=4
    )
    paid = 100.0 * m0 + open_cost
    last = last or d["last"]
    why = None
    day = d["reaction"]
    while day <= last:
        q = marks.get(day)
        if why is None:
            b = bars.get(day)
            if touch and b is not None and b[2] <= strike <= b[1]:
                why = "touch"
            elif q is not None and stop_frac is not None and mid(q[1]) - mid(q[0]) <= (1.0 - stop_frac) * m0:
                why = "stop"
            elif day == last:
                why = "time"
        if why is not None and q is not None:
            got = 100.0 * (mid(q[1]) - mid(q[0]))
            close_cost = fees.slippage_dollars([q[0], q[1]], 1, cfg) + fee_x * fees.ic_close_fee(
                symbol, 1, legs=2, sell_legs=1, ndigits=4
            )
            net = got - close_cost - paid
            return {
                "why": why,
                "exit": day.isoformat(),
                "paid": paid,
                "net": net,
                "ret": net / paid,
                "costs": open_cost + close_cost,
                "days": max((day - d["entry"]).days, 1),
            }
        day = cal.next_trading_day(day)
    return {"skip": "exit_unpriced"}


def path_marks(chains: dict[date, tuple], right: str, strike: float) -> dict[date, tuple[dict, dict] | None]:
    """Each session's (front, back) quotes at the strike from {session: (front Chain, back Chain)}."""
    return {s: (legs(f, b, right, strike) if f and b else None) for s, (f, b) in chains.items()}


# --------------------------------------------------------------------------- pure: the rule


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def _pos(x) -> bool:
    return x is not None and x > 0


def arm_part(trades: list[dict], rule: dict) -> dict:
    """One arm in one part: the paired test against the control (clustered by entry session), the
    halves, the stress and the mean without the best five, all on return per dollar paid."""
    rets = [t["ret"] for t in trades]
    pairs = [t for t in trades if t.get("control_ret") is not None]
    return {
        "trades": len(trades),
        "pairs": len(pairs),
        "mean_ret": _mean(rets),
        "pair": e31.cluster_t(
            [t["ret"] - t["control_ret"] for t in pairs], [t["entry"] for t in pairs], resamples=0
        ),
        "halves": [
            _mean(t["ret"] for t in trades if t["entry"] < rule["halves"]),
            _mean(t["ret"] for t in trades if t["entry"] >= rule["halves"]),
        ],
        "stress_mean": _mean(t["stress_ret"] for t in trades),
        "without_best": _mean(
            sorted(rets)[: -rule["best_removed"]] if len(rets) > rule["best_removed"] else []
        ),
    }


def judge_event(
    design: dict, holdout: dict, rule: dict, unselected_mean: float | None, *, min_n: int, holdout_min_n: int
) -> dict:
    """EVENT_RULE for one arm: the design checks, then the holdout after a design pass."""
    if design["trades"] < min_n:
        return {"verdict": NO_VERDICT, "why": f"{design['trades']} design trades < {min_n}"}
    checks = {
        "pair_t": _pos(design["pair"]["mean"]) and (design["pair"]["t"] or 0) >= rule["t_bar"],
        "halves": all(_pos(h) for h in design["halves"]),
        "stress": _pos(design["stress_mean"]),
        "without_best": _pos(design["without_best"]),
    }
    if unselected_mean is not None:
        checks["beats_unselected"] = design["mean_ret"] is not None and design["mean_ret"] > unselected_mean
    if not all(checks.values()):
        return {"verdict": FAIL, "checks": checks, "holdout": NOT_READ}
    held = _pos(holdout["mean_ret"]) and _pos(holdout["pair"]["mean"])
    return {
        "verdict": PASS if held else FAIL,
        "checks": checks,
        "holdout": "positive" if held else "not positive",
        "provisional": held and holdout["trades"] < holdout_min_n,
    }


def both(a: str, b: str) -> str:
    if a == PASS and b == PASS:
        return PASS
    if FAIL in (a, b):
        return FAIL
    return NO_VERDICT


def kit_rows(trades: list[dict], rate) -> list[dict]:
    return [
        {"date": t["entry"], "net": t["net"], "bp": t["paid"], "days": t["days"], "rate": rate(t["entry"])}
        for t in trades
    ]


def judge(
    trades: dict[str, list[dict]],
    unselected: list[dict],
    rate,
    rule: dict = RULE,
    event_rule: dict = EVENT_RULE,
) -> dict:
    """Both rules for every arm, from one run cut at the split."""
    split = rule["splits"][0]
    bars = kit_rule.evaluate(rule, {a: kit_rows(ts, rate) for a, ts in trades.items()})
    unsel = _mean(t["ret"] for t in unselected if t["entry"] < split)
    event = {}
    for a, ts in trades.items():
        d = arm_part([t for t in ts if t["entry"] < split], event_rule)
        h = arm_part([t for t in ts if t["entry"] >= split], event_rule)
        u = unsel if a == "quiet" and event_rule["quiet_beats_unselected"] else None
        event[a] = {
            "design": d,
            "holdout": h,
            **judge_event(d, h, event_rule, u, min_n=rule["min_n"], holdout_min_n=rule["holdout_min_n"]),
        }
    return {
        **bars,
        "event": event,
        "verdict": {a: both(bars["verdict"][a], event[a]["verdict"]) for a in trades},
    }


# --------------------------------------------------------------------------- the store and Dolt


def results_dir(item: str = "item86") -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / item
    d.mkdir(parents=True, exist_ok=True)
    return d


def chain_store():
    from cherrypick.backtest import chainfetch, settings

    store = chainfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)


def load_bars(names: list[str], host: str, port: int) -> dict[str, dict[date, tuple]]:
    """Dolt's daily (open, high, low, close) for the names, under any of their spellings."""
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


def path_chains(inputs: Inputs, conn, symbol: str, d: dict) -> dict[date, tuple]:
    """{session: (front Chain, back Chain)} over the trade's window, either None where unstored."""
    out = {}
    day = d["entry"]
    while day <= d["last"]:
        s = day.isoformat()
        out[day] = (
            inputs.chain(conn, symbol, s, d["front"].isoformat()),
            inputs.chain(conn, symbol, s, d["back"].isoformat()),
        )
        day = cal.next_trading_day(day)
    return out


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


# --------------------------------------------------------------------------- the plan


def build_plan(events: list[dict], reports: dict, span) -> tuple[list[dict], dict]:
    """The declared events with their dates and controls, and the counts dropped by reason."""
    rows, dropped = [], defaultdict(int)
    for e in events:
        if e["symbol"] not in SECTOR:
            continue
        d = trade_dates(date.fromisoformat(e["entry_session"]), date.fromisoformat(e["reaction_session"]))
        if d is None:
            dropped["no_expiry"] += 1
            continue
        row = {
            **e,
            "sector": SECTOR[e["symbol"]],
            "dates": {k: v.isoformat() for k, v in d.items()},
            "control": None,
        }
        c = e31.control_for(e, reports.get(e["symbol"], []), span)
        if c is not None:
            c_entry = date.fromisoformat(c["entry_session"])
            c_reaction = e31.reaction_session(date.fromisoformat(c["report_date"]), c["timing"])
            cd = trade_dates(c_entry, c_reaction)
            if cd is None:
                dropped["control_no_expiry"] += 1
            elif any(c_entry <= r <= cd["last"] for r in reports.get(e["symbol"], [])):
                dropped["control_report_inside"] += 1
            else:
                row["control"] = {**c, "dates": {k: v.isoformat() for k, v in cd.items()}}
        else:
            dropped["no_control"] += 1
        rows.append(row)
    return rows, dict(dropped)


def _dates(x: dict) -> dict:
    return {k: date.fromisoformat(v) for k, v in x.items()}


def requests_of(plan: list[dict]) -> list[dict]:
    """Every path the plan reads, the newest events first, each event's control after it."""
    out, seen = [], set()
    for row in sorted(plan, key=lambda r: r["entry_session"], reverse=True):
        for who in (row, row.get("control")):
            if not who:
                continue
            for r in path_requests(row["symbol"], _dates(who["dates"])):
                k = tuple(r.values())
                if k not in seen:
                    seen.add(k)
                    out.append(r)
    return out


def cmd_plan(args) -> int:
    src = results_dir("item31")
    events = json.loads((src / "events.json").read_text(encoding="utf-8"))["events"]
    doc = json.loads((src / "calendar.json").read_text(encoding="utf-8"))
    reports, span = e31.reports_from_document(doc)
    plan, dropped = build_plan(events, reports, span)
    out = results_dir()
    (out / "plan.json").write_text(
        json.dumps({"events": plan, "dropped": dropped}, indent=1), encoding="utf-8"
    )
    reqs = requests_of(plan)
    path = Path(args.out) if args.out else out / "path_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "events": len(plan), "requests": len(reqs), "dropped": dropped}))
    return 0


# --------------------------------------------------------------------------- the study


def atm_trade(inputs, conn, symbol: str, d: dict, bars: dict, right: str, **kw) -> dict:
    """The at-the-money calendar for one window: the strike nearest the entry close."""
    chains = path_chains(inputs, conn, symbol, d)
    f0, b0 = chains.get(d["entry"], (None, None))
    spot = bars.get(d["entry"])
    if not f0 or not b0:
        return {"skip": "no_entry_chain"}
    if spot is None:
        return {"skip": "no_bars"}
    k = strike_near(f0, b0, right, spot[3])
    if k is None:
        return {"skip": "no_strike"}
    t = price_trade(symbol, right, k, path_marks(chains, right, k), bars, d, **kw)
    if "skip" not in t:
        t["strike"] = k
        f_iv, b_iv = f0.iv(f0.by[right][k]), b0.iv(b0.by[right][k])
        t["iv_ratio"] = f_iv / b_iv if f_iv and b_iv else None
    return t


VARIANTS = {
    "put": {"right": "P"},
    "no_stop": {"stop_frac": None},
    "reaction_exit": {"touch": False, "stop_frac": None, "last_is_reaction": True},
    "no_touch": {"touch": False},
}


def run(
    inputs, conn, plan: list[dict], bars: dict, doc: dict, reporter: progress.Reporter | None = None
) -> dict:
    """Every event's trade, its control, its stress and the reported variants."""
    rows_by = {s: [(date.fromisoformat(d), w) for d, w in v] for s, v in doc["reports"].items()}
    trades, unselected, variants = {a: [] for a in ARMS}, [], defaultdict(list)
    skips: dict[str, int] = defaultdict(int)
    events = plan
    if reporter is not None:
        events = progress.track(
            plan,
            job="earnings_calendar_item86-study",
            phase="events",
            unit="events",
            key=lambda e: e["symbol"],
            current=lambda e: e["entry_session"],
            reporter=reporter,
        )
    for e in events:
        sym = e["symbol"]
        b = bars.get(sym, {})
        d = _dates(e["dates"])
        base = {"stop_frac": SPEC["stop_frac"]}
        t = atm_trade(inputs, conn, sym, d, b, SPEC["right"], **base)
        if "skip" in t:
            skips[t["skip"]] += 1
            continue
        s = atm_trade(inputs, conn, sym, d, b, SPEC["right"], stress=EVENT_RULE["stress"], **base)
        t.update(
            {
                "symbol": sym,
                "sector": e["sector"],
                "entry": e["entry_session"],
                "stress_ret": s.get("ret"),
                "control_ret": None,
            }
        )
        if e.get("control"):
            c = atm_trade(inputs, conn, sym, _dates(e["control"]["dates"]), b, SPEC["right"], **base)
            if "skip" in c:
                skips["control_" + c["skip"]] += 1
            else:
                t["control_ret"] = c["ret"]
        report = date.fromisoformat(e["report_date"])
        q = quiet_status(prior_returns(rows_by.get(sym, []), report, b))
        t["quiet"] = q
        t["quiet_tenth"] = quiet_status(prior_returns(rows_by.get(sym, []), report, b, at_last_only=True))
        trades["all"].append(t)
        if q:
            trades["quiet"].append(t)
        elif q is False:
            unselected.append(t)
        for name, v in VARIANTS.items():
            kw = {
                "right": v.get("right", SPEC["right"]),
                "stop_frac": v.get("stop_frac", SPEC["stop_frac"]),
                "touch": v.get("touch", True),
            }
            if v.get("last_is_reaction"):
                kw["last"] = d["reaction"]
            r = atm_trade(inputs, conn, sym, d, b, kw.pop("right"), **kw)
            if "skip" not in r:
                variants[name].append({**r, "entry": e["entry_session"], "quiet": q})
    return {"trades": trades, "unselected": unselected, "variants": dict(variants), "skips": dict(skips)}


def summary(ts: list[dict]) -> dict:
    if not ts:
        return {"trades": 0}
    return {
        "trades": len(ts),
        "mean_ret": _mean(t["ret"] for t in ts),
        "win_rate": sum(t["net"] > 0 for t in ts) / len(ts),
        "mean_paid": _mean(t["paid"] for t in ts),
        "mean_costs": _mean(t["costs"] for t in ts),
        "exits": {w: sum(t["why"] == w for t in ts) for w in sorted({t["why"] for t in ts})},
    }


def reported(res: dict, split: str) -> dict:
    out: dict = {"variants": {}, "sectors": {}, "years": {}, "iv_ratio_terciles": {}}
    for name, ts in res["variants"].items():
        out["variants"][name] = {
            "all": summary([t for t in ts if t["entry"] < split]),
            "quiet": summary([t for t in ts if t["quiet"] and t["entry"] < split]),
        }
    alls = res["trades"]["all"]
    for s in NAMES:
        out["sectors"][s] = summary([t for t in alls if t["sector"] == s and t["entry"] < split])
    for y in sorted({t["entry"][:4] for t in alls}):
        out["years"][y] = summary([t for t in alls if t["entry"][:4] == y])
    rs = sorted((t["iv_ratio"], t["ret"]) for t in alls if t["entry"] < split and t.get("iv_ratio"))
    if len(rs) >= 30:
        n = len(rs)
        out["iv_ratio_terciles"] = [_mean(r for _, r in rs[i * n // 3 : (i + 1) * n // 3]) for i in range(3)]
    out["unselected"] = summary([t for t in res["unselected"] if t["entry"] < split])
    # The first declaration's reading of `returned` (the 10th close alone), reported, never judged.
    out["quiet_tenth_close"] = {
        "selected": summary([t for t in alls if t.get("quiet_tenth") and t["entry"] < split]),
        "unselected": summary([t for t in alls if t.get("quiet_tenth") is False and t["entry"] < split]),
    }
    out["skips"] = res["skips"]
    return out


def cmd_study(args) -> int:
    out = results_dir()
    plan_doc = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    doc = json.loads((results_dir("item31") / "calendar.json").read_text(encoding="utf-8"))
    inputs = Inputs(out / "path_requests.json")
    bars = load_bars(sorted(SECTOR), args.host, args.port)
    conn = chain_store()
    try:
        plan = plan_doc["events"]
        if args.smoke:
            have = {(s, x) for s, x, _, _ in inputs.keys if inputs.sessions(conn, s, x)}
            plan = [
                e
                for e in plan
                if (e["symbol"], e["dates"]["front"]) in have and (e["symbol"], e["dates"]["back"]) in have
            ]
        with progress.Reporter("earnings_calendar_item86-study") as rep:
            res = run(inputs, conn, plan, bars, doc, reporter=rep)
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    split = RULE["splits"][0]
    if args.smoke:
        # Counts only, never a verdict: the sample is a handful of recent reports.
        body = {
            "smoke": True,
            "verdict": "smoke",
            "events_read": len(plan),
            "trades": {a: len(ts) for a, ts in res["trades"].items()},
            "skips": res["skips"],
        }
        name = "smoke.json"
    else:
        body = {**judge(res["trades"], res["unselected"], rate_reader()), "reported": reported(res, split)}
        name = "results.json"
    doc_out = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 86,
        "spec": SPEC,
        "event_rule": EVENT_RULE,
        "plan_dropped": plan_doc["dropped"],
        **body,
        "corrections": list(REPLAY_CORRECTIONS),
        "inputs": read,
    }
    path = Path(args.out) if args.out else out / name
    path.write_text(json.dumps(doc_out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc_out["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--smoke", action="store_true", help="the sample fetched so far: counts only")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
