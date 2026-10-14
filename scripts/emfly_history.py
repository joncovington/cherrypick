"""Replay the expected-move butterfly over every earnings event in the local Dolt clones.

    python scripts/emfly_history.py [--start 2020-01-01] [--end 2026-10-02] [--workers 8]
        [--min-dollar-volume 100e6] [--min-price 10] [--cache FILE] [--json FILE] [--limit N]

**The question.** A debit butterfly whose body sits one expected move from spot (the SCHW example:
spot 96.31, EM 3.51, buy 97 / sell 2x 100 / buy 103 calls for 0.51) is priced roughly fairly by the
market's own distribution, so it earns only if (1) the body is placed where moves actually land
better than the market expects, or (2) the side -- call fly above, put fly below -- can be called
better than a coin. This answers both from history before any strategy code is written, and
compares closing rules on the same entries.

**Data.** Read-only throughout:

- Dolt `earnings.earnings_calendar` (date, before/after market) for the events, `eps_estimate`
  ("Current Quarter" consensus snapshots, weekly) for revisions, `eps_history` for the beat record.
- Dolt `options.option_chain` end-of-day quotes with IV and delta. Sampled Mon/Wed/Fri until 2024,
  most weekdays since, and only a few expirations per day -- often not the nearest weekly. So the
  entry is the latest chain day on or up to two days before the pre-event close, the expiration is
  the nearest one listed that day on or after the reaction day (within `--max-dte` of the entry, the
  earnings module's front-expiration rule), and a position can only be marked on days its
  expiration is listed. Both lags are counted and printed.
- The technicals `history.db` (Dolt's bars from 2011, raw, adjusted on read) for spot, the reaction
  and expiry closes, the dollar-volume screen and the trend / momentum signals. Strikes are raw, so
  moves and payoffs use raw closes; an event whose split/dividend adjustment changes between entry
  and expiry by more than 1% is dropped.

**The trade.** The expected move is 0.85 x the ATM straddle mid on the chosen expiration (the
suite's `STRADDLE_TO_EM_FACTOR`). For a body multiple k the call fly's body is the listed strike
nearest spot + k x EM, the put fly's nearest spot - k x EM; the wings are the symmetric listed
distance closest to `--width-mult` x EM (0.85, the example's 3 on 3.51), refused when no listed pair
is within half to one and a half times that. One fly of each, 1-2-1, opened at the legs' mid.

**Costs** (whole-position dollars, net = gross - fees - slippage - settlement, the earnings basis
where fills are at mid and slippage is charged as a cost): `cherrypick.core.fees.apply_entry_costs`
/ `apply_exit_costs` with the suite's defaults -- $1/contract to open on 4 contracts, $0 to close,
clearing and regulatory per contract both ways, 0.125 of each leg's spread capped at 15% of its
mid. Held to expiry: intrinsic on the expiration day's close, $5 per in-the-money strike (the
earnings settlement model). Early assignment is not modelled.

**Exits** compared on the same entries: `react` the first chain day on or after the reaction day,
at mid; `react_far` the same at the far touch (what earnings paper does today); `day2` the next
chain day after that; `expiry` held to the close; `tp2x` the first chain day from the reaction on
whose mark is at least twice the debit, else expiry; `tpw` the same at half the width.

**Signals,** all known at the entry close: `rr25` call minus put IV at |delta| 0.25 over ATM IV;
`atm_spread` ATM call minus put IV; `trend` the technicals short-term trend score; `mom` 0.5 x the
21-day return + the 126-day (the RS rank's input); `drift5` the 5-day return; `revision` the change
in the current quarter's consensus over 28 days; `beat` the beat rate of the last 8 reported
quarters (period ends at least 100 days old, so the quarter being reported is never in it);
`past_dir` the mean sign of the symbol's earlier reactions. Each signal's direction (which end means
up) and its tercile cuts are chosen on 2020-2023 alone and scored on 2024 onwards.

Deterministic: the same clones and arguments give the same tables. Nothing is written except the
optional `--cache` and `--json` files.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import fees as _fees
from cherrypick.core.structures import STRADDLE_TO_EM_FACTOR
from cherrypick.technicals import store as _store
from cherrypick.technicals import trend as _trend

sys.path.insert(0, str(Path(__file__).resolve().parent))
import premium_cycle_replay as _pcr  # noqa: E402 -- the smile model, shared rather than copied

MULT = 100
K_GRID = (0.5, 0.75, 1.0, 1.25)
EXITS = ("react", "react_far", "day2", "expiry", "tp2x", "tpw")
SIGNALS = ("rr25", "atm_spread", "trend", "mom", "drift5", "revision", "beat", "past_dir")
SPLIT_SPLIT = date(2024, 1, 1)  # train before, test on and after
SETTLEMENT_PER_ITM_STRIKE = 5.00
FLY_ORDER = {"order": {"legs": [{"quantity": 1}, {"quantity": 2}, {"quantity": 1}]}}
HISTORY_DB = Path.home() / ".cherrypick" / "data" / "technicals" / "history.db"


# --------------------------------------------------------------------------- data access

_tls = threading.local()


def _dolt(host: str, port: int):
    conn = getattr(_tls, "conn", None)
    if conn is None:
        import mysql.connector

        conn = mysql.connector.connect(host=host, port=port, user="root", connection_timeout=30)
        _tls.conn = conn
    return conn


class Chains:
    """End-of-day chain rows per (symbol, date), cached to a JSON-lines file so a re-run is local.
    A row is (expiration, strike, is_call, bid, ask, iv, delta); an empty list means no chain."""

    def __init__(self, host: str, port: int, cache: Path | None):
        self.host, self.port, self.cache = host, port, cache
        self.rows: dict[str, list] = {}
        self.lock = threading.Lock()
        self.fresh: list[str] = []
        if cache and cache.exists():
            for line in cache.read_text(encoding="utf-8").splitlines():
                k, v = json.loads(line)
                self.rows[k] = v

    def day(self, symbol: str, d: date) -> list:
        key = f"{symbol}|{d.isoformat()}"
        hit = self.rows.get(key)
        if hit is not None:
            return hit
        cur = _dolt(self.host, self.port).cursor()
        cur.execute(
            "SELECT expiration, strike, call_put, bid, ask, vol, delta FROM options.option_chain "
            "WHERE date = %s AND act_symbol = %s",
            (d, symbol),
        )
        rows = [
            (
                e.isoformat(),
                float(k),
                cp == "Call",
                None if b is None else float(b),
                None if a is None else float(a),
                None if v is None else float(v),
                None if dl is None else float(dl),
            )
            for e, k, cp, b, a, v, dl in cur.fetchall()
        ]
        cur.close()
        with self.lock:
            self.rows[key] = rows
            self.fresh.append(key)
        return rows

    def flush(self) -> None:
        if not self.cache or not self.fresh:
            return
        with self.cache.open("a", encoding="utf-8") as fh:
            for key in self.fresh:
                fh.write(json.dumps([key, self.rows[key]]) + "\n")
        self.fresh = []


def option_symbols(host: str, port: int, start: date, end: date) -> set[str]:
    """Symbols the chain clone holds: the union over two sampled days a year (a seek each)."""
    cur = _dolt(host, port).cursor()
    out: set[str] = set()
    for year in range(start.year, end.year + 1):
        for month in (2, 8):
            d = date(year, month, 1)
            for _ in range(7):
                if start - timedelta(days=40) <= d <= end:
                    cur.execute("SELECT DISTINCT act_symbol FROM options.option_chain WHERE date = %s", (d,))
                    got = {r[0] for r in cur.fetchall()}
                    if got:
                        out |= got
                        break
                d += timedelta(days=1)
    cur.close()
    return out


def load_events(host: str, port: int, start: date, end: date) -> list[dict]:
    cur = _dolt(host, port).cursor()
    cur.execute(
        "SELECT act_symbol, date, `when` FROM earnings.earnings_calendar WHERE date BETWEEN %s AND %s "
        "AND `when` IN ('After market close', 'Before market open')",
        (start, end),
    )
    rows = [{"symbol": s, "date": d, "amc": w == "After market close"} for s, d, w in cur.fetchall()]
    cur.close()
    return rows


def load_estimates(host: str, port: int, symbols: set[str]) -> dict[str, list[tuple]]:
    """symbol -> [(snapshot date, period_end, consensus)], date order; current quarter only."""
    cur = _dolt(host, port).cursor()
    cur.execute(
        "SELECT act_symbol, date, period_end_date, consensus FROM earnings.eps_estimate "
        "WHERE period = 'Current Quarter' AND consensus IS NOT NULL"
    )
    out: dict[str, list] = defaultdict(list)
    for s, d, pe, c in cur.fetchall():
        if s in symbols:
            out[s].append((d, pe, float(c)))
    cur.close()
    for v in out.values():
        v.sort()
    return out


def load_eps_history(host: str, port: int, symbols: set[str]) -> dict[str, list[tuple]]:
    cur = _dolt(host, port).cursor()
    cur.execute(
        "SELECT act_symbol, period_end_date, reported, estimate FROM earnings.eps_history "
        "WHERE reported IS NOT NULL AND estimate IS NOT NULL"
    )
    out: dict[str, list] = defaultdict(list)
    for s, pe, r, e in cur.fetchall():
        if s in symbols:
            out[s].append((pe, float(r), float(e)))
    cur.close()
    for v in out.values():
        v.sort()
    return out


class Bars:
    """Raw and adjusted closes per symbol from the technicals history store, opened read-only."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def series(self, symbol: str) -> dict | None:
        adj = _store.adjusted_bars(self.conn, symbol)
        if not adj:
            return None
        raw = {b.date: b for b in _store.raw_bars(self.conn, symbol)}
        dates, rc, ac, dv = [], [], [], []
        for b in adj:
            r = raw.get(b.date)
            if r is None or not r.close or not b.close:
                continue
            dates.append(date.fromisoformat(b.date))
            rc.append(r.close)
            ac.append(b.close)
            dv.append(r.close * (r.volume or 0.0))
        return {"dates": dates, "raw": rc, "adj": ac, "dollar_volume": dv}


# --------------------------------------------------------------------------- pure pieces


def mid(bid, ask):
    if bid is None or ask is None or ask <= 0 or bid < 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def quotes_by_strike(rows: list, expiration: str, is_call: bool) -> dict[float, tuple]:
    out = {}
    for e, k, c, b, a, iv, dl in rows:
        if e == expiration and c == is_call and mid(b, a) is not None:
            out[k] = (b, a, iv, dl)
    return out


def grid_step(spot: float) -> float:
    """A single stock's usual strike spacing near the money on its weeklies."""
    return 0.5 if spot < 25 else 1.0 if spot < 150 else 2.5 if spot < 500 else 5.0


def fly_legs(spot: float, offset: float, width: float, step: float) -> tuple[float, float, float]:
    """(lower, body, upper) on the strike grid: body nearest spot + offset, wings `width` rounded to
    the grid (at least one step) either side, so the fly is always symmetric."""
    body = round((spot + offset) / step) * step
    d = max(step, round(width / step) * step)
    return body - d, body, body + d


def make_slice(rows: list, expiration: str, day: date, spot: float):
    """One day's quotes for `expiration` as a smile that prices any strike (`premium_cycle_replay.
    Slice`: listed quotes as quoted, unlisted strikes from the interpolated implied vol)."""
    picked = [
        (k, "C" if c else "P", b, a)
        for e, k, c, b, a, _, _ in rows
        if e == expiration and b is not None and a is not None
    ]
    return _pcr.Slice(day, date.fromisoformat(expiration), picked, spot) if picked else None


class Borrowed:
    """A mark for an expiration the clone did not list that day: each strike's implied vol and
    spread from the nearest listed expiration's smile, priced with the held expiration's time left.
    Only after the event, when the term structure the event put in has gone."""

    def __init__(self, sl, day: date, expiration: date):
        self.sl, self.t = sl, max((expiration - day).days, 0) / 365.0

    def quote(self, cp: str, k: float) -> tuple[float, float]:
        m, spr = self.sl.quote(cp, k)
        v = self.sl.vol(cp, k)
        if v is None:
            return m, spr
        return _pcr.black76(cp, self.sl.forward, k, self.t, v, 1.0), spr


def mark_slice(rows: list, expiration: str, day: date, spot: float):
    """(smile, borrowed?) for marking `expiration` on `day`, or (None, None)."""
    sl = make_slice(rows, expiration, day, spot)
    if sl is not None:
        return sl, False
    target = date.fromisoformat(expiration)
    others = sorted(
        {r[0] for r in rows if date.fromisoformat(r[0]) > day + timedelta(days=1)},
        key=lambda e: (abs((date.fromisoformat(e) - target).days), e),
    )
    if not others or abs((date.fromisoformat(others[0]) - target).days) > 14:
        return None, None
    near = make_slice(rows, others[0], day, spot)
    return (Borrowed(near, day, target), True) if near is not None else (None, None)


def in_range(sl, cp: str, legs: tuple) -> bool:
    ks = sl.quotes[cp]
    return bool(ks) and min(ks) <= legs[0] and legs[2] <= max(ks)


def leg_quotes(sl, cp: str, legs: tuple) -> list[dict]:
    out = []
    for k in legs:
        m, spr = sl.quote(cp, k)
        out.append({"bid": max(m - spr / 2, 0.0), "ask": m + spr / 2})
    return out


def fly_value(sl, cp: str, legs: tuple, far: bool = False) -> float:
    lo, b, hi = leg_quotes(sl, cp, legs)
    if far:
        return lo["bid"] - 2 * b["ask"] + hi["bid"]
    return (lo["bid"] + lo["ask"]) / 2 - (b["bid"] + b["ask"]) + (hi["bid"] + hi["ask"]) / 2


def intrinsic(legs: tuple, s: float, is_call: bool) -> tuple[float, int]:
    def one(k):
        return max(0.0, s - k) if is_call else max(0.0, k - s)

    lo, b, hi = legs
    value = one(lo) - 2 * one(b) + one(hi)
    itm = sum(1 for k in legs if one(k) > 0)
    return value, itm


def nearest_delta_iv(q: dict, target: float):
    best = None
    for _, _, iv, dl in q.values():
        if iv is None or dl is None or iv <= 0:
            continue
        gap = abs(abs(dl) - target)
        if best is None or gap < best[0]:
            best = (gap, iv)
    return None if best is None or best[0] > 0.1 else best[1]


# --------------------------------------------------------------------------- one event


def build_event(ev: dict, bars: dict, chains: Chains, args, est, epsh, past) -> dict | None:
    """Everything one event contributes: the move, the expected move, signals, and per (k, side)
    each exit's net. None (with a reason in ev['drop']) when it cannot be priced."""
    dates = bars["dates"]
    d0 = ev["date"]
    i = bisect.bisect_left(dates, d0)
    if ev["amc"]:
        if i >= len(dates) or dates[i] != d0:
            ev["drop"] = "no_bar_on_date"
            return None
        ip, ir = i, i + 1
    else:
        ip, ir = i - 1, (i if i < len(dates) and dates[i] == d0 else None)
        if ir is None:
            ev["drop"] = "no_bar_on_date"
            return None
    if ip < 200 or ir >= len(dates):
        ev["drop"] = "short_history"
        return None
    P, R = dates[ip], dates[ir]
    spot, react_close = bars["raw"][ip], bars["raw"][ir]
    if spot < args.min_price:
        ev["drop"] = "price"
        return None
    dv = statistics.fmean(bars["dollar_volume"][ip - 19 : ip + 1])
    if dv < args.min_dollar_volume:
        ev["drop"] = "dollar_volume"
        return None

    c0, rows = None, []
    for back in range(0, 3):
        d = P - timedelta(days=back)
        rows = chains.day(ev["symbol"], d)
        if rows:
            c0 = d
            break
    if c0 is None:
        ev["drop"] = "no_entry_chain"
        return None
    if c0 != P:  # spot and signals follow the chain day the trade is actually opened on
        j = bisect.bisect_right(dates, c0) - 1
        if j < 200 or dates[j] != c0:
            ev["drop"] = "no_entry_chain"
            return None
        ip, spot = j, bars["raw"][j]
    exps = sorted({r[0] for r in rows if date.fromisoformat(r[0]) >= R})
    if not exps or (date.fromisoformat(exps[0]) - P).days > args.max_dte:
        ev["drop"] = "no_front_expiration"
        return None
    E = exps[0]
    Ed = date.fromisoformat(E)
    ie = bisect.bisect_right(dates, Ed) - 1
    if ie < ir or dates[-1] < Ed:
        ev["drop"] = "expiry_after_bars"
        return None
    ratio = [bars["raw"][x] / bars["adj"][x] for x in (ip, ie)]
    if abs(ratio[0] / ratio[1] - 1) > 0.01:
        ev["drop"] = "split_in_window"
        return None
    expiry_close = bars["raw"][ie]

    calls, puts = quotes_by_strike(rows, E, True), quotes_by_strike(rows, E, False)
    both = sorted(set(calls) & set(puts))
    if not both:
        ev["drop"] = "no_atm"
        return None
    atm = min(both, key=lambda k: (abs(k - spot), k))
    c_atm, p_atm = calls[atm], puts[atm]
    straddle = mid(c_atm[0], c_atm[1]) + mid(p_atm[0], p_atm[1])
    straddle_spread = (c_atm[1] - c_atm[0]) + (p_atm[1] - p_atm[0])
    if straddle <= 0 or straddle_spread / straddle > args.max_straddle_spread:
        ev["drop"] = "straddle_spread"
        return None
    em = STRADDLE_TO_EM_FACTOR * straddle

    out = {
        "symbol": ev["symbol"],
        "date": d0.isoformat(),
        "entry": c0.isoformat(),
        "entry_lag": (P - c0).days,
        "react_day": R.isoformat(),
        "expiration": E,
        "dte": (Ed - P).days,
        "spot": spot,
        "em": em,
        "move": react_close - bars["raw"][ip],
        "move_ratio": (react_close - bars["raw"][ip]) / em,
        "expiry_move_ratio": (expiry_close - spot) / em,
    }
    out["signals"] = signals(ev["symbol"], ip, bars, calls, puts, atm, P, est, epsh, past)

    # The path: each chain day from the reaction day to the day before expiry with E listed, as a
    # smile on that day's close.
    path = []
    d = R
    while d < Ed:
        j = bisect.bisect_left(dates, d)
        if d.weekday() < 5 and j < len(dates) and dates[j] == d:
            sl, borrowed = mark_slice(chains.day(ev["symbol"], d), E, d, bars["raw"][j])
            if sl is not None:
                path.append((d, sl, borrowed))
        d += timedelta(days=1)
    out["react_lag"] = (path[0][0] - R).days if path else None
    out["react_borrowed"] = path[0][2] if path else None
    path = [(d, sl) for d, sl, _ in path]

    entry = make_slice(rows, E, c0, spot)
    step = grid_step(spot)
    flies = {}
    for k in K_GRID:
        for side, cp, sign in (("call", "C", 1), ("put", "P", -1)):
            legs = fly_legs(spot, sign * k * em, args.width_mult * em, step)
            if entry is None or not in_range(entry, cp, legs):
                continue
            debit = fly_value(entry, cp, legs)
            width = legs[1] - legs[0]
            if debit <= 0.01 or debit >= width:
                continue
            entry_cost = _fees.apply_entry_costs(FLY_ORDER, leg_quotes(entry, cp, legs), 1, {})["total_cost"]
            flies[f"{k}|{side}"] = price_exits(legs, cp, debit, width, entry_cost, path, expiry_close)
    if not flies:
        ev["drop"] = "no_fly"
        return None
    out["flies"] = flies
    return out


def price_exits(legs, cp, debit, width, entry_cost, path, expiry_close) -> dict:
    def close_at(sl, far=False):
        v = fly_value(sl, cp, legs, far)
        costs = _fees.apply_exit_costs(FLY_ORDER, leg_quotes(sl, cp, legs), 1, {})
        # The far touch already concedes the whole spread, so only the fees stay a cost there.
        exit_cost = costs["total_cost"] - (costs["slippage"] if far else 0.0)
        return round((v - debit) * MULT - entry_cost - exit_cost, 2)

    value, itm = intrinsic(legs, expiry_close, cp == "C")
    at_expiry = round((value - debit) * MULT - entry_cost - SETTLEMENT_PER_ITM_STRIKE * itm, 2)
    marks = path
    res = {
        "legs": legs,
        "debit": round(debit, 4),
        "entry_cost": entry_cost,
        "risk": round(debit * MULT + entry_cost, 2),
        "expiry": at_expiry,
        "react": close_at(marks[0][1]) if marks else None,
        "react_far": close_at(marks[0][1], far=True) if marks else None,
        "day2": close_at(marks[1][1]) if len(marks) > 1 else (at_expiry if marks else None),
    }
    for name, trigger in (("tp2x", 2 * debit), ("tpw", 0.5 * width)):
        res[name] = at_expiry
        for _, sl in marks:
            if fly_value(sl, cp, legs) >= trigger:
                res[name] = close_at(sl)
                break
    return res


def signals(symbol, ip, bars, calls, puts, atm, P, est, epsh, past) -> dict:
    adj = bars["adj"][: ip + 1]
    civ, piv = calls[atm][2], puts[atm][2]
    atm_iv = (civ + piv) / 2 if civ and piv else None
    c25, p25 = nearest_delta_iv(calls, 0.25), nearest_delta_iv(puts, 0.25)
    s = {
        "rr25": (c25 - p25) / atm_iv if (c25 and p25 and atm_iv) else None,
        "atm_spread": civ - piv if civ and piv else None,
        "trend": _trend.last_score(adj),
        "mom": 0.5 * (adj[-1] / adj[-22] - 1) + (adj[-1] / adj[-127] - 1),
        "drift5": adj[-1] / adj[-6] - 1,
        "revision": None,
        "beat": None,
        "past_dir": None,
    }
    snaps = est.get(symbol) or []
    j = bisect.bisect_right([x[0] for x in snaps], P) - 1
    if j >= 0 and (P - snaps[j][0]).days <= 10:
        now = snaps[j]
        then = [x for x in snaps[: j + 1] if x[1] == now[1] and 21 <= (P - x[0]).days <= 42]
        if then:
            base = then[-1][2]
            s["revision"] = (now[2] - base) / max(abs(base), 0.05)
    hist = [h for h in (epsh.get(symbol) or []) if (P - h[0]).days >= 100][-8:]
    if len(hist) >= 4:
        s["beat"] = sum(1 for _, r, e in hist if r > e) / len(hist)
    prior = [m for d, m in past.get(symbol, []) if d < P][-8:]
    if len(prior) >= 4:
        s["past_dir"] = statistics.fmean(1.0 if m > 0 else -1.0 for m in prior)
    return s


# --------------------------------------------------------------------------- analysis


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summarize(nets: list[float], risks: list[float]) -> dict:
    if not nets:
        return {"n": 0}
    return {
        "n": len(nets),
        "mean": round(statistics.fmean(nets), 2),
        "total": round(sum(nets), 0),
        "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
        "ror": round(sum(nets) / sum(risks), 4) if sum(risks) else None,
    }


def train(ev):
    return date.fromisoformat(ev["date"]) < SPLIT_SPLIT


def move_section(events: list[dict]) -> dict:
    out = {}
    for name, sel in (
        ("all", events),
        ("train", [e for e in events if train(e)]),
        ("test", [e for e in events if not train(e)]),
    ):
        r = [abs(e["move_ratio"]) for e in sel]
        out[name] = {
            "n": len(r),
            "abs_ratio_q": {p: round(q(r, p), 3) for p in (0.1, 0.25, 0.5, 0.75, 0.9)} if r else {},
            "inside_em": round(sum(1 for x in r if x < 1) / len(r), 3) if r else None,
            "up": round(sum(1 for e in sel if e["move"] > 0) / len(sel), 3) if sel else None,
        }
    return out


def control_section(events: list[dict]) -> dict:
    """Both sides, half size each, per k and exit; and each side alone, and the perfect-direction
    oracle (the call fly on up moves, the put fly on down) as the ceiling on what a side call buys."""
    out = {}
    for k in K_GRID:
        for ex in EXITS:
            for period, sel in (
                ("train", [e for e in events if train(e)]),
                ("test", [e for e in events if not train(e)]),
            ):
                both, both_r, call, call_r, put, put_r, orc, orc_r = ([] for _ in range(8))
                for e in sel:
                    c, p = e["flies"].get(f"{k}|call"), e["flies"].get(f"{k}|put")
                    if not c or not p or c[ex] is None or p[ex] is None:
                        continue
                    both.append((c[ex] + p[ex]) / 2)
                    both_r.append((c["risk"] + p["risk"]) / 2)
                    call.append(c[ex])
                    call_r.append(c["risk"])
                    put.append(p[ex])
                    put_r.append(p["risk"])
                    o = c if e["move"] > 0 else p
                    orc.append(o[ex])
                    orc_r.append(o["risk"])
                out[f"{k}|{ex}|{period}"] = {
                    "both": summarize(both, both_r),
                    "call": summarize(call, call_r),
                    "put": summarize(put, put_r),
                    "oracle": summarize(orc, orc_r),
                }
    return out


DTE_BUCKETS = (("all", 0, 99), ("1-4", 1, 4), ("7-11", 7, 11), ("14-17", 14, 17))


def cost_section(events: list[dict]) -> dict:
    """Held to expiry, per k and DTE bucket: what the fly paid back per dollar of debit at mid, what
    the costs took, and the hit rate a side call needs to break even -- for each side alone, the
    perfect-direction side and the wrong one. The split says whether a loss is the price or the
    costs, and the break-even says how good a signal would have to be."""
    out = {}
    for k in K_GRID:
        for name, lo, hi in DTE_BUCKETS:
            acc = {s: [0.0, 0.0, 0.0, 0.0, 0] for s in ("call", "put", "right", "wrong")}
            for e in events:
                if not lo <= e["dte"] <= hi:
                    continue
                c, p = e["flies"].get(f"{k}|call"), e["flies"].get(f"{k}|put")
                if not c or not p:
                    continue
                s_exp = e["spot"] + e["expiry_move_ratio"] * e["em"]
                up = e["move"] > 0
                for label, f, is_call in (
                    ("call", c, True),
                    ("put", p, False),
                    ("right", c if up else p, up),
                    ("wrong", p if up else c, not up),
                ):
                    v, itm = intrinsic(f["legs"], s_exp, is_call)
                    a = acc[label]
                    a[0] += v * MULT
                    a[1] += f["debit"] * MULT
                    a[2] += f["entry_cost"]
                    a[3] += SETTLEMENT_PER_ITM_STRIKE * itm
                    a[4] += 1
            row = {}
            for label, (pay, deb, cost, settle, n) in acc.items():
                if not n:
                    continue
                row[label] = {
                    "n": n,
                    "payoff_per_debit": round(pay / deb, 3),
                    "cost_per_debit": round(cost / deb, 3),
                    "settle_per_debit": round(settle / deb, 3),
                    "net_per_risk": round((pay - deb - cost - settle) / (deb + cost), 3),
                }
            if "right" in row:
                r, w = row["right"], row["wrong"]
                costs = (
                    r["cost_per_debit"] + r["settle_per_debit"] + w["cost_per_debit"] + w["settle_per_debit"]
                )
                need = 1 + costs / 2
                span = r["payoff_per_debit"] - w["payoff_per_debit"]
                row["breakeven_hit"] = round((need - w["payoff_per_debit"]) / span, 3) if span > 0 else None
            out[f"{k}|{name}"] = row
    return out


def signal_years(events: list[dict], fitted: dict) -> dict:
    """Each fitted signal's extreme-tercile hit rate by year, with the train cuts and sign."""
    out = {}
    for sig, s in fitted.items():
        if "cuts" not in s:
            continue
        lo, hi = s["cuts"]
        years: dict[str, list] = defaultdict(list)
        for e in events:
            v = e["signals"].get(sig)
            if v is None or lo <= v <= hi:
                continue
            bull = (v > hi) if s["sign"] > 0 else (v < lo)
            years[e["date"][:4]].append(bull == (e["move"] > 0))
        out[sig] = {y: [len(v), round(sum(v) / len(v), 3)] for y, v in sorted(years.items())}
    return out


def signal_section(events: list[dict], k: float, exits=("react", "expiry")) -> dict:
    """Per signal: direction and tercile cuts fitted on train; on each period the hit rate of the
    extreme terciles on the reaction's sign, and the directional fly's net vs both-sides on the
    same events."""
    out = {}
    tr = [e for e in events if train(e)]
    for sig in SIGNALS:
        vals = sorted(e["signals"][sig] for e in tr if e["signals"].get(sig) is not None)
        if len(vals) < 300:
            out[sig] = {"n_train": len(vals)}
            continue
        lo, hi = q(vals, 1 / 3), q(vals, 2 / 3)
        top = [e for e in tr if (e["signals"].get(sig) is not None and e["signals"][sig] > hi)]
        bot = [e for e in tr if (e["signals"].get(sig) is not None and e["signals"][sig] < lo)]
        up_top = sum(e["move"] > 0 for e in top) / max(len(top), 1)
        up_bot = sum(e["move"] > 0 for e in bot) / max(len(bot), 1)
        sign = 1 if up_top >= up_bot else -1
        res = {"n_train": len(vals), "cuts": [lo, hi], "sign": sign}
        for period, sel in (("train", tr), ("test", [e for e in events if not train(e)])):
            calls_, puts_ = [], []
            for e in sel:
                v = e["signals"].get(sig)
                if v is None:
                    continue
                bull = (v > hi) if sign > 0 else (v < lo)
                bear = (v < lo) if sign > 0 else (v > hi)
                if bull:
                    calls_.append(e)
                elif bear:
                    puts_.append(e)
            picked = [(e, "call") for e in calls_] + [(e, "put") for e in puts_]
            hit = sum((e["move"] > 0) == (s == "call") for e, s in picked)
            r = {"n": len(picked), "hit": round(hit / len(picked), 3) if picked else None}
            for ex in exits:
                d, dr, b, br = [], [], [], []
                for e, s in picked:
                    f = e["flies"].get(f"{k}|{s}")
                    o = e["flies"].get(f"{k}|{'put' if s == 'call' else 'call'}")
                    if not f or not o or f[ex] is None or o[ex] is None:
                        continue
                    d.append(f[ex])
                    dr.append(f["risk"])
                    b.append((f[ex] + o[ex]) / 2)
                    br.append((f["risk"] + o["risk"]) / 2)
                r[ex] = {"directional": summarize(d, dr), "both_same_events": summarize(b, br)}
            res[period] = r
        out[sig] = res
    return out


# --------------------------------------------------------------------------- driver


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-10-02")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-dollar-volume", type=float, default=100e6)
    ap.add_argument("--min-price", type=float, default=10.0)
    ap.add_argument("--max-dte", type=int, default=17)
    ap.add_argument("--max-straddle-spread", type=float, default=0.15)
    ap.add_argument("--width-mult", type=float, default=0.85)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbols", help="comma-separated, for a quick run")
    ap.add_argument("--from-json", type=Path, help="re-analyse the events a previous --json run saved")
    args = ap.parse_args(argv)
    if args.from_json:
        saved = json.loads(args.from_json.read_text(encoding="utf-8"))
        head = {k: saved["report"][k] for k in ("args", "drops")}
        report = analyse(saved["events"], head)
        print_report(report)
        if args.json:
            body = json.dumps({"report": report, "events": saved["events"]}, default=str)
            args.json.write_text(body, encoding="utf-8")
        return 0
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    syms_opt = option_symbols(args.host, args.port, start, end)
    events = [e for e in load_events(args.host, args.port, start, end) if e["symbol"] in syms_opt]
    if args.symbols:
        keep = set(args.symbols.split(","))
        events = [e for e in events if e["symbol"] in keep]
    by_sym: dict[str, list] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)
    syms = sorted(by_sym)
    print(f"events with a listed chain symbol: {len(events)} over {len(syms)} symbols", file=sys.stderr)

    est = load_estimates(args.host, args.port, set(syms))
    epsh = load_eps_history(args.host, args.port, set(syms))
    bars_db = Bars(HISTORY_DB)
    chains = Chains(args.host, args.port, args.cache)

    # Past reaction signs come from every event of the symbol, screened or not.
    series: dict[str, dict] = {}
    past: dict[str, list] = defaultdict(list)
    for s in syms:
        b = bars_db.series(s)
        if b is None:
            continue
        series[s] = b
        for e in sorted(by_sym[s], key=lambda x: x["date"]):
            i = bisect.bisect_left(b["dates"], e["date"])
            ip, ir = (i, i + 1) if e["amc"] else (i - 1, i)
            if 0 <= ip and ir < len(b["dates"]) and (not e["amc"] or b["dates"][i] == e["date"]):
                past[s].append((b["dates"][ip], b["raw"][ir] - b["raw"][ip]))

    todo = [e for s in syms if s in series for e in by_sym[s]]
    todo.sort(key=lambda e: (e["date"], e["symbol"]))
    if args.limit:
        todo = todo[: args.limit]
    print(f"events to price: {len(todo)}", file=sys.stderr)

    results: list[dict] = []
    drops: dict[str, int] = defaultdict(int)

    def work(e):
        try:
            return build_event(e, series[e["symbol"]], chains, args, est, epsh, past)
        except Exception as exc:  # noqa: BLE001 -- one bad row is a drop, never the run
            e["drop"] = f"error:{type(exc).__name__}"
            return None

    with ThreadPoolExecutor(args.workers) as pool:
        for n, (e, r) in enumerate(zip(todo, pool.map(work, todo), strict=True), 1):
            if r is None:
                drops[e.get("drop", "unknown")] += 1
            else:
                results.append(r)
            if n % 500 == 0:
                chains.flush()
                print(f"  {n}/{len(todo)} priced {len(results)}", file=sys.stderr)
    chains.flush()

    head = {
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "drops": dict(sorted(drops.items(), key=lambda kv: -kv[1])),
    }
    report = analyse(results, head)
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps({"report": report, "events": results}, default=str), encoding="utf-8")
    return 0


def analyse(results: list[dict], head: dict) -> dict:
    report = {
        **head,
        "n_events": len(results),
        "entry_lag": dict(sorted(_count(r["entry_lag"] for r in results).items())),
        "react_lag": dict(sorted(_count(r["react_lag"] for r in results).items(), key=str)),
        "dte": dict(sorted(_count(r["dte"] for r in results).items())),
        "moves": move_section(results),
        "control": control_section(results),
        "costs": cost_section(results),
    }
    best_k = max(K_GRID, key=lambda k: report["control"][f"{k}|react|train"]["both"].get("mean") or -1e9)
    report["signal_k"] = best_k
    report["signals"] = signal_section(results, best_k)
    report["signals_k1"] = signal_section(results, 1.0) if best_k != 1.0 else report["signals"]
    report["signal_years"] = signal_years(results, report["signals_k1"])
    return report


def _count(xs):
    out: dict = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "n 0"
    return (
        f"n {s['n']:>5}  mean {s['mean']:>8.2f}  total {s['total']:>9.0f}  "
        f"win {s['win']:.2f}  ror {s['ror']:+.3f}"
    )


def print_report(r: dict) -> None:
    print(f"\npriced events: {r['n_events']}")
    print("drops:", r["drops"])
    print("entry lag (days before the pre-event close):", r["entry_lag"])
    print("reaction mark lag (days after the reaction day):", r["react_lag"])
    print("expiration DTE from the pre-event close:", r["dte"])
    print("\n== where moves land (|move| / expected move) ==")
    for name, m in r["moves"].items():
        print(
            f"  {name:5} n {m['n']:>5}  quantiles {m['abs_ratio_q']}  "
            f"inside EM {m['inside_em']}  up {m['up']}"
        )
    print("\n== both sides, half size each (and each side, and the perfect-direction ceiling) ==")
    for k in K_GRID:
        for ex in EXITS:
            for period in ("train", "test"):
                c = r["control"][f"{k}|{ex}|{period}"]
                print(f"  k {k:<4} {ex:9} {period:5} both   {_fmt(c['both'])}")
            c = r["control"][f"{k}|{ex}|test"]
            print(f"  {'':16} test  call   {_fmt(c['call'])}")
            print(f"  {'':16} test  put    {_fmt(c['put'])}")
            print(f"  {'':16} test  oracle {_fmt(c['oracle'])}")
    print(
        "\n== held to expiry: price paid back vs costs, per dollar of debit (right/wrong = the side "
        "the move favoured / the other) =="
    )
    for k in K_GRID:
        for name, _, _ in DTE_BUCKETS:
            row = r["costs"][f"{k}|{name}"]
            parts = [
                f"{lab} pay {v['payoff_per_debit']:.3f} cost {v['cost_per_debit']:.3f} "
                f"settle {v['settle_per_debit']:.3f} net/risk {v['net_per_risk']:+.3f}"
                for lab, v in row.items()
                if isinstance(v, dict)
            ]
            n = row.get("call", {}).get("n", 0)
            print(f"  k {k:<4} dte {name:5} n {n:>5}  break-even hit {row.get('breakeven_hit')}")
            for p in parts:
                print(f"      {p}")
    for label, key in (("best-k (train, react)", "signals"), ("k 1.0", "signals_k1")):
        k = r["signal_k"] if key == "signals" else 1.0
        print(f"\n== direction signals at {label} = {k}; terciles and sign fitted on train ==")
        for sig, s in r[key].items():
            if "train" not in s:
                print(f"  {sig:10} too few train values ({s['n_train']})")
                continue
            for period in ("train", "test"):
                p = s[period]
                print(f"  {sig:10} {period:5} sign {s['sign']:+d}  picks {p['n']:>5}  hit {p['hit']}")
                for ex in ("react", "expiry"):
                    print(f"  {'':10} {'':5} {ex:7} dir  {_fmt(p[ex]['directional'])}")
                    print(f"  {'':10} {'':5} {ex:7} both {_fmt(p[ex]['both_same_events'])}")
    print("\n== each signal's extreme-tercile hit by year [picks, hit] (cuts and sign from train) ==")
    for sig, years in r["signal_years"].items():
        print(f"  {sig:10} {years}")


if __name__ == "__main__":
    raise SystemExit(main())
