"""Item 83: a 3-minute EMA 9 crossing the session VWAP, bought as a 0DTE SPX option.

The declaration is research/edge-hypotheses-plan.md section 83; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

An indicator rule decides every entry and exit. It is read from SPX's one-minute stamps in the
licensed store, which hold the index and its 0DTE quotes each minute:

- 3-minute bars from 09:30, each from the stamps inside it (open, high, low and close of the stamps).
- EMA 9 of the bar closes, carried across consecutive full sessions. VWAP anchored to the session
  with OHLC4 as its source; the index has no volume, so every bar weighs the same (a time-weighted
  mean, declared).
- Entry: a bar whose EMA closes across the VWAP (both bars in the same session). Up buys the 0DTE
  call nearest 50 delta, down the put, at the next stamp, one position at a time.
- Exit, first of: a bar closing back across the EMA (at the next stamp); the option's mid at or
  under 70% of its entry mid (at that stamp); 15:55.

Two arms by the entry window (`allday`, signal bars closing by 15:30; `first60`, by 10:30). Each is
judged by RULE (the kit's three bars on per-session rows, delta not applicable) and CONTROL_RULE
(each session against a random-entry placebo of the same trades). A pass needs both.

- `study`: one run over the whole history, cut at the split, written to `{results}/results.json`
  with a top-level `verdict` (`{"allday": word, "first60": word}`).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import fees, progress, treasury

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's pass rule, per arm, on one row per session that traded. Holm across the two arms at
#: one-sided 0.05 never binds above t 2.24.
RULE = {
    "t_bar": 2.3,
    "min_n": 200,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 83},
    "delta": False,
    "splits": ["2025-01-01"],
}

#: The second rule: each session's net less its placebo's (the same trades, entered at random).
CONTROL_RULE = {
    "placebos": 20,
    "seed": 83,
    "t_bar": 2.3,
    "min_n": 200,
    "holdout_min_n": 60,
    "split": "2025-01-01",
    "halves": "2023-09-01",
}

#: The replay as declared.
SPEC = {
    "symbol": "SPX",
    "through": "2026-11-13",
    "min_samples": 380,
    "max_gap_days": 4,
    "bar_minutes": 3,
    "open": "09:30",
    "ema": 9,
    "target_delta": 0.50,
    "stop_frac": 0.30,
    "last_exit": "15:55",
    "arms": {"allday": "15:30", "first60": "10:30"},
    "points_cost": 0.50,
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched:
#: the study reads the SPX minutes and 0DTE quotes the store already holds.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
PUT, CALL = 0, 1
RATE_STALE_DAYS = 10


# --------------------------------------------------------------------------- pure pieces


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def bars_of(samples: list[tuple[float, float]], spec: dict) -> list[dict]:
    """The session's 3-minute bars from its stamps. A stamp at minute m closes the minute before it,
    so 09:31-09:33 make the first bar. A bar's close is its last stamp, the time the bar is known."""
    start, width = hhmm(spec["open"]), spec["bar_minutes"]
    out: dict[int, list[tuple[float, float]]] = {}
    for ts, px in samples:
        m = minute_of(ts)
        if m <= start:
            continue
        out.setdefault((m - start - 1) // width, []).append((ts, px))
    bars = []
    for k in sorted(out):
        pts = out[k]
        px = [p for _, p in pts]
        o, h, lo, c = px[0], max(px), min(px), px[-1]
        bars.append({"k": k, "ts": pts[-1][0], "open": o, "high": h, "low": lo, "close": c,
                     "ohlc4": (o + h + lo + c) / 4.0})  # fmt: skip
    return bars


def ema_step(prev: float | None, x: float, n: int) -> float:
    return x if prev is None else prev + (2.0 / (n + 1)) * (x - prev)


def annotate(bars: list[dict], ema_prev: float | None, n: int) -> float | None:
    """Each bar's EMA (carried in from the prior session's `ema_prev`) and session VWAP (the running
    mean of OHLC4: equal weights, the index having no volume). Returns the EMA after the last bar."""
    total = 0.0
    for i, b in enumerate(bars):
        ema_prev = ema_step(ema_prev, b["close"], n)
        total += b["ohlc4"]
        b["ema"] = ema_prev
        b["vwap"] = total / (i + 1)
    return ema_prev


def cross(prev: dict, cur: dict) -> int | None:
    """CALL when the EMA closes above the VWAP after a bar at or below it, PUT for the reverse."""
    if prev["ema"] <= prev["vwap"] and cur["ema"] > cur["vwap"]:
        return CALL
    if prev["ema"] >= prev["vwap"] and cur["ema"] < cur["vwap"]:
        return PUT
    return None


def next_after(stamps: list[float], ts: float) -> float | None:
    """The first quoted stamp after `ts`: when a bar's close is known, the next quote is the fill."""
    i = bisect.bisect_right(stamps, ts)
    return stamps[i] if i < len(stamps) else None


def against(side: int, bar: dict) -> bool:
    """The trend exit: a call's bar closing under the EMA, a put's closing over it."""
    return bar["close"] < bar["ema"] if side == CALL else bar["close"] > bar["ema"]


def pick(quotes: dict, side: int, target: float) -> tuple[float, dict] | None:
    """The strike on `side` whose |delta| is nearest `target` with a bid and an ask; ties to the lower."""
    best = None
    for (s, k), q in quotes.items():
        if s != side or q["delta"] is None or q["bid"] <= 0 or q["ask"] < q["bid"]:
            continue
        key = (abs(abs(q["delta"]) - target), k)
        if best is None or key < best[0]:
            best = (key, k, q)
    return (best[1], best[2]) if best else None


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def entry_cost(q: dict) -> dict:
    """Buy one contract at mid plus the suite's slippage, with the open fee schedule (one leg)."""
    slip = fees.slippage_dollars([q], 1, {}, symbol="SPX")
    fee = fees.ic_open_fee("SPX", 1, legs=1, sell_legs=0, ndigits=4)
    return {"paid": 100.0 * _mid(q), "slip": slip, "fee": fee}


def exit_value(q: dict) -> dict:
    """Sell one contract at mid less the suite's slippage, with the close fee schedule (one sell)."""
    slip = fees.slippage_dollars([q], 1, {}, symbol="SPX")
    fee = fees.ic_close_fee("SPX", 1, legs=1, sell_legs=1, ndigits=4)
    return {"got": 100.0 * _mid(q), "slip": slip, "fee": fee}


def settle_value(side: int, strike: float, spot: float) -> dict:
    """Held to the close with no quote: cash settled at the last stamp, the fee when in the money."""
    owed = max(spot - strike, 0.0) if side == CALL else max(strike - spot, 0.0)
    return {"got": 100.0 * owed, "slip": 0.0, "fee": fees.ic_expire_fee(1 if owed > 0 else 0)}


def hold(
    side: int,
    strike: float,
    q0: dict,
    t0: float,
    stamps: list[float],
    quotes: dict[float, dict],
    spot: dict[float, float],
    *,
    stop_frac: float | None,
    exit_at: float,
    trend_exits: set[float],
) -> dict:
    """Hold one contract from stamp `t0` to the first of: a stamp in `trend_exits` (the stamp after
    a bar closing across the EMA), the mid at or under (1 - stop_frac) of the entry mid, or `exit_at`.
    A stamp with no quote for the strike skips the stop; the exit takes the next stamp quoted, and
    settles at the last stamp's spot when none is."""
    floor = None if stop_frac is None else (1.0 - stop_frac) * _mid(q0)
    why, t_out, q_out = None, None, None
    for t in stamps:
        if t <= t0:
            continue
        q = quotes.get(t, {}).get((side, strike))
        if why is None:
            if t >= exit_at:
                why = "time"
            elif t in trend_exits:
                why = "trend"
            elif q is not None and floor is not None and _mid(q) <= floor:
                why = "stop"
        if why is not None and q is not None and q["bid"] >= 0 and q["ask"] >= q["bid"]:
            t_out, q_out = t, q
            break
    if q_out is None:
        last = stamps[-1]
        return {"why": why or "close", "exit_ts": last, "settled": True,
                **settle_value(side, strike, spot[last])}  # fmt: skip
    return {"why": why, "exit_ts": t_out, "settled": False, **exit_value(q_out)}


def trade_of(side: int, strike: float, q0: dict, t0: float, out: dict, spot: dict) -> dict:
    e = entry_cost(q0)
    net = out["got"] - e["paid"] - e["slip"] - e["fee"] - out["slip"] - out["fee"]
    move = spot[out["exit_ts"]] - spot[t0]
    return {
        "side": "call" if side == CALL else "put",
        "strike": strike,
        "entry_ts": t0,
        "exit_ts": out["exit_ts"],
        "minutes": round((out["exit_ts"] - t0) / 60.0),
        "why": out["why"],
        "settled": out["settled"],
        "debit": e["paid"] + e["slip"] + e["fee"],
        "costs": e["slip"] + e["fee"] + out["slip"] + out["fee"],
        "net": net,
        "points": move if side == CALL else -move,
    }


def day_trades(
    bars: list[dict],
    stamps: list[float],
    quotes: dict[float, dict],
    spot: dict[float, float],
    spec: dict,
    *,
    last_signal: int,
    stop_frac: float | None,
) -> tuple[list[dict], dict]:
    """One session's trades for an arm whose signal bars close by minute `last_signal`, and the
    signals skipped (held already, no stamp after, no strike)."""
    after = {b["ts"]: t for b in bars if (t := next_after(stamps, b["ts"])) is not None}
    exit_at = next((t for t in stamps if minute_of(t) >= hhmm(spec["last_exit"])), stamps[-1])
    skipped = {"held": 0, "no_stamp": 0, "no_strike": 0, "late": 0}
    trades: list[dict] = []
    busy_until = None
    for i in range(1, len(bars)):
        bar = bars[i]
        side = cross(bars[i - 1], bar)
        if side is None:
            continue
        if minute_of(bar["ts"]) > last_signal:
            skipped["late"] += 1
            continue
        t0 = after.get(bar["ts"])
        if t0 is None or t0 >= exit_at:
            skipped["no_stamp"] += 1
            continue
        if busy_until is not None and t0 < busy_until:
            skipped["held"] += 1
            continue
        got = pick(quotes.get(t0, {}), side, spec["target_delta"])
        if got is None:
            skipped["no_strike"] += 1
            continue
        strike, q0 = got
        trend = {after[b["ts"]] for b in bars[i + 1 :] if against(side, b) and b["ts"] in after}
        out = hold(side, strike, q0, t0, stamps, quotes, spot,
                   stop_frac=stop_frac, exit_at=exit_at, trend_exits=trend)  # fmt: skip
        trades.append(trade_of(side, strike, q0, t0, out, spot))
        busy_until = out["exit_ts"]
    return trades, skipped


def placebo(
    trade: dict,
    stamps: list[float],
    quotes: dict[float, dict],
    spot: dict[float, float],
    spec: dict,
    *,
    first_entry: float,
    last_entry: float,
    rng: random.Random,
    stop_frac: float | None,
) -> dict | None:
    """The same side and minutes held, entered at a random stamp of the arm's window (and early
    enough to hold them by the last exit), with the same strike rule, stop and costs."""
    exit_at = next((t for t in stamps if minute_of(t) >= hhmm(spec["last_exit"])), stamps[-1])
    span = 60.0 * trade["minutes"]
    pool = [t for t in stamps if first_entry <= t <= last_entry and t + span <= exit_at]
    if not pool:
        return None
    t0 = rng.choice(pool)
    side = CALL if trade["side"] == "call" else PUT
    got = pick(quotes.get(t0, {}), side, spec["target_delta"])
    if got is None:
        return None
    strike, q0 = got
    end = next((t for t in stamps if t >= t0 + span), exit_at)
    out = hold(side, strike, q0, t0, stamps, quotes, spot,
               stop_frac=stop_frac, exit_at=min(end, exit_at), trend_exits=set())  # fmt: skip
    return trade_of(side, strike, q0, t0, out, spot)


def session_row(day: str, trades: list[dict], rate: float) -> dict:
    """The kit's row for one session: the day's nets summed, the largest debit held (one position at
    a time), one day."""
    return {
        "date": day,
        "net": sum(t["net"] for t in trades),
        "bp": max(t["debit"] for t in trades),
        "days": 1,
        "rate": rate,
        "trades": len(trades),
    }


def _mean_t(xs: list[float]) -> dict:
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": statistics.fmean(xs) if xs else None, "t": None}
    m, sd = statistics.fmean(xs), statistics.stdev(xs)
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if sd else None}


def judge_control(diffs: list[dict], rule: dict) -> dict:
    """Each session's net less its placebo's: the design mean above zero at the t bar and above zero
    in each design half; then, after a design pass, the holdout mean above zero."""
    design = [d["diff"] for d in diffs if d["date"] < rule["split"]]
    holdout = [d["diff"] for d in diffs if d["date"] >= rule["split"]]
    out: dict = {"design": _mean_t(design), "holdout": {"n": len(holdout)}}
    if len(design) < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": NO_VERDICT}
    halves = {
        "first": _mean_t([d["diff"] for d in diffs if d["date"] < rule["halves"]])["mean"],
        "second": _mean_t([d["diff"] for d in diffs if rule["halves"] <= d["date"] < rule["split"]])["mean"],
    }
    d = out["design"]
    checks = {
        "mean": d["mean"] is not None and d["mean"] > 0,
        "t": d["t"] is not None and d["t"] >= rule["t_bar"],
        "halves": all(v is not None and v > 0 for v in halves.values()),
    }
    d.update({"halves": halves, "checks": checks})
    if not all(checks.values()):
        d["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": FAIL}
    d["verdict"] = PASS
    h = _mean_t(holdout)
    word = PASS if h["mean"] is not None and h["mean"] > 0 else FAIL
    provisional = word == PASS and len(holdout) < rule["holdout_min_n"]
    out["holdout"] = {**h, "verdict": word, "provisional": provisional}
    return {**out, "verdict": word, "provisional": provisional}


def both(bars_word: str, control_word: str) -> str:
    """An arm passes only when both rules pass; a fail in either fails it; else no verdict."""
    if bars_word == PASS and control_word == PASS:
        return PASS
    if FAIL in (bars_word, control_word):
        return FAIL
    return NO_VERDICT


def summary(trades: list[dict]) -> dict:
    """Reported per arm: counts, win rate, mean net and costs, exits by reason, the points version."""
    if not trades:
        return {"trades": 0}
    pts = [t["points"] for t in trades]
    return {
        "trades": len(trades),
        "win_rate": sum(1 for t in trades if t["net"] > 0) / len(trades),
        "mean_net": statistics.fmean(t["net"] for t in trades),
        "mean_costs": statistics.fmean(t["costs"] for t in trades),
        "mean_debit": statistics.fmean(t["debit"] for t in trades),
        "mean_minutes": statistics.fmean(t["minutes"] for t in trades),
        "exits": {w: sum(1 for t in trades if t["why"] == w) for w in sorted({t["why"] for t in trades})},
        "settled": sum(1 for t in trades if t["settled"]),
        "points_gross": statistics.fmean(pts),
        "points_net": statistics.fmean(p - SPEC["points_cost"] for p in pts),
        "by_side": {
            s: statistics.fmean(t["net"] for t in trades if t["side"] == s)
            for s in ("call", "put")
            if any(t["side"] == s for t in trades)
        },  # fmt: skip
    }


def fingerprint(sessions: dict[str, list]) -> dict:
    """What the run read, as counts and a hash of them: never a price."""
    lines = "\n".join(f"{d}:{len(s)}" for d, s in sorted(sessions.items()))
    return {
        "sessions": len(sessions),
        "stamps": sum(len(s) for s in sessions.values()),
        "first": min(sessions) if sessions else None,
        "last": max(sessions) if sessions else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- the store (read-only)


def store_path() -> Path:
    from cherrypick.backtest import paths, settings

    return paths.vendor_dir(settings.load()) / "SPX-60s.db"


def load_sessions(
    conn: sqlite3.Connection, symbol: str, through: str
) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for d, ts, spot in conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date <= ? AND spot > 0 "
        "ORDER BY trade_date, ts",
        (symbol, through),
    ):
        out.setdefault(d, []).append((float(ts), float(spot)))
    return out


def load_quotes(conn: sqlite3.Connection, symbol: str, day: str) -> dict[float, dict]:
    out: dict[float, dict] = {}
    for ts, side, strike, bid, ask, delta in conn.execute(
        "SELECT s.ts, q.side, q.strike, q.bid, q.ask, q.delta FROM gex_quote_samples s "
        "JOIN gex_quotes q ON q.sample_id = s.id "
        "WHERE s.symbol = ? AND s.trade_date = ? AND s.expiration = ?",
        (symbol, day, day),
    ):
        out.setdefault(float(ts), {})[(int(side), float(strike))] = {"bid": bid, "ask": ask, "delta": delta}
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


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item83"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- the run

#: The cuts reported beside each arm, never judged: the stop at 25% and none.
VARIANTS = {"stop25": 0.25, "no_stop": None}


def replay(
    conn,
    sessions: dict,
    spec: dict,
    rate,
    *,
    limit: int | None = None,
    reporter: progress.Reporter | None = None,
) -> dict:
    """Every arm's trades, placebos and reported variants over every full session, in one pass."""
    arms = spec["arms"]
    trades = {a: [] for a in arms}
    variants = {f"{a}/{v}": [] for a in arms for v in VARIANTS}
    diffs: dict[str, list[dict]] = {a: [] for a in arms}
    rows: dict[str, list[dict]] = {a: [] for a in arms}
    skipped = {a: {} for a in arms}
    dropped = {"short": 0, "no_quotes": 0}
    ema, prev = None, None
    days = sorted(sessions)
    if limit:
        days = days[-limit:]
    for d in (
        days
        if reporter is None
        else progress.track(
            days,
            job="ema_vwap_cross_item83-study",
            phase="sessions",
            unit="days",
            key=lambda _: spec["symbol"],
            current=lambda d: d,
            reporter=reporter,
        )
    ):
        samples = sessions[d]
        if len(samples) < spec["min_samples"]:
            dropped["short"] += 1
            ema, prev = None, None
            continue
        if (
            prev is not None
            and (date.fromisoformat(d) - date.fromisoformat(prev)).days > spec["max_gap_days"]
        ):
            ema = None
        prev = d
        bars = bars_of(samples, spec)
        ema = annotate(bars, ema, spec["ema"])
        quotes = load_quotes(conn, spec["symbol"], d)
        if not quotes:
            dropped["no_quotes"] += 1
            continue
        stamps = sorted(set(quotes) & {ts for ts, _ in samples})
        spot = dict(samples)
        r = rate(d)
        for a, last in arms.items():
            ts_, skip = day_trades(bars, stamps, quotes, spot, spec, last_signal=hhmm(last),
                                   stop_frac=spec["stop_frac"])  # fmt: skip
            for k, v in skip.items():
                skipped[a][k] = skipped[a].get(k, 0) + v
            for v, frac in VARIANTS.items():
                got = day_trades(bars, stamps, quotes, spot, spec, last_signal=hhmm(last), stop_frac=frac)[0]
                variants[f"{a}/{v}"] += [{**t, "date": d} for t in got]
            if not ts_:
                continue
            for t in ts_:
                t["date"] = d
            trades[a] += ts_
            row = session_row(d, ts_, r)
            rows[a].append(row)
            first_entry = (next_after(stamps, bars[1]["ts"]) if len(bars) > 1 else None) or stamps[0]
            last_entry = next((t for t in stamps if minute_of(t) > hhmm(last)), stamps[-1])
            rng = random.Random(f"{CONTROL_RULE['seed']}:{a}:{d}")
            draws = []
            for _ in range(CONTROL_RULE["placebos"]):
                got = [placebo(t, stamps, quotes, spot, spec, first_entry=first_entry, last_entry=last_entry,
                               rng=rng, stop_frac=spec["stop_frac"]) for t in ts_]  # fmt: skip
                if all(g is not None for g in got):
                    draws.append(sum(g["net"] for g in got))
            if draws:
                diffs[a].append(
                    {"date": d, "diff": row["net"] - statistics.fmean(draws), "draws": len(draws)}
                )
    return {"trades": trades, "variants": variants, "diffs": diffs, "rows": rows, "skipped": skipped,
            "dropped": dropped, "days": days}  # fmt: skip


def study(
    conn, sessions: dict, rate, *, limit: int | None = None, reporter: progress.Reporter | None = None
) -> dict:
    run = replay(conn, sessions, SPEC, rate, limit=limit, reporter=reporter)
    bars_block = kit_rule.evaluate(RULE, run["rows"])
    control = {a: judge_control(run["diffs"][a], CONTROL_RULE) for a in SPEC["arms"]}
    split = RULE["splits"][0]

    def parts(ts: list[dict]) -> dict:
        return {"design": summary([t for t in ts if t["date"] < split]),
                "holdout": summary([t for t in ts if t["date"] >= split])}  # fmt: skip

    years = {
        a: {y: summary([t for t in run["trades"][a] if t["date"][:4] == y])
            for y in sorted({t["date"][:4] for t in run["trades"][a]})}
        for a in SPEC["arms"]
    }  # fmt: skip
    return {
        **bars_block,
        "control": control,
        "verdict": {a: both(bars_block["verdict"][a], control[a]["verdict"]) for a in SPEC["arms"]},
        "reported": {
            "arms": {a: parts(run["trades"][a]) for a in SPEC["arms"]},
            "variants": {k: parts(v) for k, v in run["variants"].items()},
            "years": years,
            "skipped": run["skipped"],
        },
        "dropped": run["dropped"],
        "window": {
            "from": run["days"][0] if run["days"] else None,
            "through": SPEC["through"],
            "splits": RULE["splits"],
        },  # fmt: skip
        "corrections": list(REPLAY_CORRECTIONS),
    }


def cmd_study(args) -> int:
    conn = sqlite3.connect(f"file:{store_path()}?mode=ro", uri=True)
    try:
        sessions = load_sessions(conn, SPEC["symbol"], SPEC["through"])
        with progress.Reporter("ema_vwap_cross_item83-study") as rep:
            block = study(conn, sessions, rate_reader(), limit=args.sessions, reporter=rep)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 83,
           "spec": SPEC, "control_rule": CONTROL_RULE, **block, "inputs": fingerprint(sessions)}  # fmt: skip
    if args.sessions:
        # A look at the last few sessions, never a verdict.
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.sessions else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--sessions", type=int, default=None, help="smoke: only the last N sessions, no verdict")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
