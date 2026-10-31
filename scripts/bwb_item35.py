"""Item 35, the bwb module's whole rule at real quotes (docs/edge-hypotheses-plan.md, section 35): does the
SPX put broken-wing butterfly, alone or with an add-on put spread, earn after costs, and does it do so
if entries are kept to days when VIX9D is below VIX?

    python scripts/bwb_item35.py plan [--out DIR]       # the fetch request files, and what they cover
    python scripts/bwb_item35.py validate               # the paper checks; stops the study when they fail
    python scripts/bwb_item35.py study [--json PATH]    # validate, then every arm, then the verdicts
    python scripts/bwb_item35.py report [--json PATH]   # results.md from an existing results.json

`plan` writes two files under the backtest data dir (results/research/item35/), never into the repo, and
prints their counts: `addon_requests.json`, the input of `fetch-addon-weeklies` (the sessions the minute
store lacks: the seven holiday-Thursday expiries and the 8-day Thursday entries), and `oi_plan.json`, the
input of `fetch-oi` (SPXW open interest for every session, for the gamma flip). `validate` and `study`
read the stores. The inputs are licensed; nothing here is committed with a figure in it.

**The rule is the module's own.** Expiry from `clock.target_expiration`, the structure from
`engine.plan_entry` (expected move, strikes, the spread and credit gates), the add-on from
`engine.plan_addon`, arming and firing from `management.evaluate` over `triggers` (the module's latches,
a measured minute only), the spread gate on a fire from `management.execution_gate`, costs from
`engine.entry_cost`/`addon_entry_cost`/`settlement_fee` and `core.fees`. Only the chain readers are
replaced: a minute of the store stands for a tick of the cache.

**Six arms, two gate states, twelve tests.** `control`, `bounce`, `flip`, `delta`, `delta-dte3` (the delta
add-on only while 3 or more calendar days remain), `delta-shortstop` (the delta add-on alone bought back
at the first minute spot is at or below its short strike), each ungated and gated (VIX9D open over VIX
open below 1.0; a day missing either print is refused, as live refuses `regime_unmeasured`).
Per test, in the design window: mean net per trade above zero with a one-sided week-clustered bootstrap
p, Holm across all twelve; an add-on arm also above `control` on the same entries (paired, one-sided p
under 0.05); both time-halves positive; positive at halfway-to-natural fills and at 1.5 times fees (and,
for `delta-shortstop`, with its exit one minute later); positive with the five worst settlement weeks
removed; then the holdout mean above zero and, for an add-on arm, above `control`'s. `flip` is no verdict
(input) unless the recomputed flip matches what paper recorded.

**Readings chosen where the declaration is silent** (each is a constant or a function below, and the
report repeats them): a tick is evaluated from the entry minute to the last minute before 16:00 of the
expiry day; an add-on armed at one minute fires at the first later credit minute (the module arms and
fires on separate ticks); a one-minute-late trigger lags delta, spot and flip together while the add-on
is still priced at the real minute; a bracket is placeable only at exactly the far wing's plus and minus
one increment; "two-sided" is `core.streamcache.usable_quote`'s rule; a leg more than one increment off
its target, or a body target outside the stored strike band, drops the entry (`leg_unquoted`).
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import NormalDist, mean, pstdev
from zoneinfo import ZoneInfo

from cherrypick.bwb import clock, engine, management
from cherrypick.core import calendar as cal
from cherrypick.core import fees, streamcache
from cherrypick.core import gex as core_gex
from cherrypick.core import home as core_home
from cherrypick.core import stats as core_stats

ET = ZoneInfo("America/New_York")
SYMBOL, ROOT = "SPX", "SPXW"
ARMS = ("control", "bounce", "flip", "delta", "delta-dte3", "delta-shortstop")
ADDON_ARMS = ARMS[1:]
#: The trigger family each arm runs under (`triggers.evaluate`'s name for it).
BASE = {"control": "control", "bounce": "bounce", "flip": "flip", "delta": "delta"}
GATES = ("ungated", "gated")
GATE_BAR = 1.0  # the live `vix9d_vix_max`: VIX9D / VIX at or above it refuses
DTE3_DAYS = 3
ENTRY_AT = (10, 0)
INCREMENT = 5.0
RATE = 0.0  # the flip's gamma: no rate, no dividend (validated against paper's own flips)

WINDOWS = {
    "design": (date(2022, 5, 13), date(2024, 12, 31)),
    "h1": (date(2022, 5, 13), date(2023, 9, 29)),
    "h2": (date(2023, 10, 2), date(2024, 12, 31)),
    "holdout": (date(2025, 1, 2), date(2026, 8, 21)),
    "paper": (date(2026, 8, 24), date(2026, 10, 16)),
}
STORE_FIRST_ENTRY, STORE_LAST_EXPIRY = date(2022, 5, 13), date(2026, 10, 16)
MIN_TRADES = {"design": {"ungated": 300, "gated": 250}, "holdout": {"ungated": 150, "gated": 125}}
MIN_FIRE_WEEKS = 40
ALPHA, RESAMPLES, SEED = 0.05, 2000, 35
WORST_WEEKS = 5
POWER = 0.80
ENTRY_MATCH_MIN, ENTRY_CREDIT_TOL = 0.90, 0.05
FLIP_TOL, FLIP_MATCH_MIN, FLIP_MIN_TICKS = 5.0, 0.80, 500
BOUNDARY_DELTA = 0.99999  # the vendor's rounding of a delta of exactly minus one

#: Cost bases: slippage as a fraction of each leg's spread (and its cap on the mid), the fee multiple.
#: `verdict` is the paper ledger's own; the rest stress or report it.
BASES = {
    "verdict": {"frac": 0.125, "cap": 0.15, "fee": 1.0},
    "halfway": {"frac": 0.25, "cap": None, "fee": 1.0},
    "fees15": {"frac": 0.125, "cap": 0.15, "fee": 1.5},
    "natural": {"frac": 0.5, "cap": None, "fee": 1.0},
    "live": {"frac": 0.125, "cap": 0.15, "fee": 1.0, "credit_concession": 0.05},
}
STRESSES = ("halfway", "fees15")
CONFIG = {
    b: {"tastytrade_costs": {"slippage_frac_of_spread": v["frac"], "slippage_cap_frac_of_mid": v["cap"]}}
    for b, v in BASES.items()
}


# ------------------------------------------------------------------------------------------------
# Pure: the calendar.


def sessions(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def minute_ts(day: str, hh: int, mm: int) -> int:
    """The epoch second of hh:mm ET on `day`."""
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).timestamp())


def in_window(day: str, name: str) -> bool:
    a, b = WINDOWS[name]
    return a <= date.fromisoformat(day) <= b


def days_left(day: str, expiration: str) -> int:
    return (date.fromisoformat(expiration) - date.fromisoformat(day)).days


# ------------------------------------------------------------------------------------------------
# Pure: a minute of the store as the module's snapshot.


def quote_of(bid, ask) -> dict | None:
    """A quote worth pricing against: the suite's one definition (`streamcache.usable_quote`)."""
    return streamcache.usable_quote({"bid": bid, "ask": ask, "mid": None, "updated_at": 0.0}, 0.0, 300.0)


def leg_symbol(exp: str, right: str, strike: float) -> str:
    return f"{ROOT}|{exp}|{right}|{strike:g}"


@dataclass
class Minute:
    ts: int
    day: str
    spot: float | None
    puts: dict = field(default_factory=dict)  # strike -> (bid, ask, delta, iv)


class ExpiryData:
    """One expiry's minutes (puts at every stored strike) and its 10:00 call strip. Built from the store
    by `load_expiry`, or by hand in a test; everything else reads only this."""

    def __init__(self, expiration: str, minutes: list[Minute], calls: dict | None = None, oi_of=None):
        self.expiration = expiration
        self.minutes = sorted(minutes, key=lambda m: m.ts)
        self.ts_list = [m.ts for m in self.minutes]
        self.by_ts = {m.ts: m for m in self.minutes}
        self.calls = calls or {}  # ts -> {strike: (bid, ask)}
        self.oi_of = oi_of  # day -> {(right, strike): open interest}
        self.end_ts = minute_ts(expiration, 16, 0)
        self._snap: dict = {}
        self._addon: dict = {}
        self._flip: dict = {}

    def span(self, start_ts: int) -> list[Minute]:
        """The minutes from `start_ts` to the last before the expiry day's close."""
        lo, hi = bisect.bisect_left(self.ts_list, start_ts), bisect.bisect_left(self.ts_list, self.end_ts)
        return self.minutes[lo:hi]

    def snapshot(self, minute: Minute, dte: int, with_calls: bool = False) -> dict:
        """The module's snapshot of this minute: a chain, usable quotes, greeks, spot. Cached."""
        key = (minute.ts, with_calls)
        if key in self._snap:
            return self._snap[key]
        exp, chain, quotes, greeks = self.expiration, [], {}, {}
        for k, (bid, ask, delta, iv) in minute.puts.items():
            s = leg_symbol(exp, "P", k)
            chain.append({"option_type": "put", "strike_price": k, "streamer_symbol": s, "occ_symbol": s})
            q = quote_of(bid, ask)
            if q:
                quotes[s] = q
                greeks[s] = {"iv": iv, "delta": delta}
        if with_calls:
            for k, (bid, ask) in self.calls.get(minute.ts, {}).items():
                s = leg_symbol(exp, "C", k)
                chain.append(
                    {"option_type": "call", "strike_price": k, "streamer_symbol": s, "occ_symbol": s}
                )
                q = quote_of(bid, ask)
                if q:
                    quotes[s] = q
        snap = {"symbol": SYMBOL, "spot": minute.spot, "expiration": exp, "dte": dte, "chain": chain,
                "quotes": quotes, "greeks": greeks, "date": minute.day}  # fmt: skip
        self._snap[key] = snap
        return snap

    def addon_plan(self, minute: Minute, far: float, dte: int) -> dict | None:
        """The module's add-on plan at this minute, or None when it is not a credit or the bracket is
        not exactly the far wing's plus and minus one increment. Cached."""
        key = (minute.ts, far)
        if key not in self._addon:
            res = engine.plan_addon(self.snapshot(minute, dte), far, {})
            plan = res.get("plan") if res.get("ok") else None
            if plan and (plan["short_strike"] != far + INCREMENT or plan["long_strike"] != far - INCREMENT):
                plan = None
            self._addon[key] = plan
        return self._addon[key]

    def flip_at(self, minute: Minute) -> float | None:
        """The gamma flip of this expiry's own chain at this minute, from the put implied volatility's
        gamma and the morning's open interest (None when either is missing). Cached."""
        if minute.ts not in self._flip:
            oi = self.oi_of(minute.day) if self.oi_of else {}
            self._flip[minute.ts] = recompute_flip(minute, self.expiration, oi)
        return self._flip[minute.ts]


def load_expiry(conn: sqlite3.Connection, expiration: str, oi_of=None) -> ExpiryData | None:
    """One expiry from the minute store, or None when it holds nothing."""
    minutes: dict[int, Minute] = {}
    calls: dict[int, dict] = {}
    q = "SELECT day, ts, right, strike, bid, ask, delta, iv, spot FROM q WHERE expiration = ? ORDER BY ts"
    for day, ts, right, strike, bid, ask, delta, iv, spot in conn.execute(q, (expiration,)):
        m = minutes.get(ts)
        if right == "P":
            if m is None:
                m = minutes[ts] = Minute(ts, day, None)
            m.puts[strike] = (bid, ask, delta, iv)
        else:
            calls.setdefault(ts, {})[strike] = (bid, ask)
        if m is not None and m.spot is None and spot is not None:
            m.spot = spot
    if not minutes:
        return None
    return ExpiryData(expiration, list(minutes.values()), calls, oi_of)


def bs_gamma(spot: float, strike: float, years: float, vol: float) -> float:
    d1 = (math.log(spot / strike) + (RATE + vol * vol / 2.0) * years) / (vol * math.sqrt(years))
    return math.exp(-d1 * d1 / 2.0) / math.sqrt(2.0 * math.pi) / (spot * vol * math.sqrt(years))


def recompute_flip(minute: Minute, expiration: str, oi: dict) -> float | None:
    """The module's flip off one minute of the weekly chain: each strike's gamma from its put implied
    volatility (a call's equals the put's at that strike), the morning's open interest, and
    `core.gex.compute_gex`, the function the module's provider calls."""
    if minute.spot is None or not oi:
        return None
    years = max((minute_ts(expiration, 16, 0) - minute.ts) / (365.0 * 86400.0), 1.0 / (365.0 * 1440.0))
    chain, greeks, interest = [], {}, {}
    for k, (_b, _a, _d, iv) in minute.puts.items():
        if not iv or iv <= 0:
            continue
        gamma = bs_gamma(minute.spot, k, years, iv)
        for right, word in (("C", "call"), ("P", "put")):
            n = oi.get((right, k))
            if not n:
                continue
            s = leg_symbol(expiration, right, k)
            chain.append({"option_type": word, "strike_price": k, "streamer_symbol": s})
            greeks[s], interest[s] = {"gamma": gamma}, n
    res = core_gex.compute_gex(chain, greeks, interest, minute.spot) if chain else {"ok": False}
    return res.get("gamma_flip") if res.get("ok") else None


# ------------------------------------------------------------------------------------------------
# Pure: the entry.

DROPS = ("not_in_store", "no_entry_minute", "no_index_price", "no_atm_quote", "leg_unquoted", "no_settlement")
REFUSALS = ("spread_too_wide", "no_credit")


def plan_at_entry(data: ExpiryData | None, day: str, dte: int) -> dict:
    """The base structure at the 10:00 minute: `{"status": "entered", "plan": ...}`, or
    `{"status": "dropped"|"refused", "reason": ...}` (a drop is a minute the replay cannot read, a
    refusal is the module declining)."""
    if data is None:
        return {"status": "dropped", "reason": "not_in_store"}
    minute = data.by_ts.get(minute_ts(day, *ENTRY_AT))
    if minute is None:
        return {"status": "dropped", "reason": "no_entry_minute"}
    if minute.spot is None:
        return {"status": "dropped", "reason": "no_index_price"}
    res = engine.plan_entry(data.snapshot(minute, dte, with_calls=True), {})
    if not res["ok"]:
        reason = res["reason"]
        if reason in REFUSALS:
            return {"status": "refused", "reason": reason}
        return {
            "status": "dropped",
            "reason": {"no_expected_move": "no_atm_quote"}.get(reason, "leg_unquoted"),
        }
    plan = res["plan"]
    body, near, far = plan["body_strike"], plan["near_strike"], plan["far_strike"]
    # The module snaps each leg to the nearest listed strike; the store lists a band, so a body target
    # past its edge would be snapped to the edge. A leg off its target by more than an increment (or a
    # wing not exactly one and two increments from the body) is a minute the replay cannot read.
    off_target = abs(body - (plan["spot"] - plan["expected_move"])) > INCREMENT
    if off_target or near != body + INCREMENT or far != body - 2 * INCREMENT:
        return {"status": "dropped", "reason": "leg_unquoted"}
    return {"status": "entered", "plan": plan, "minute": minute}


@dataclass(slots=True)
class Tick:
    ts: int
    day: str
    spot: float | None
    abs_delta: float | None
    minute: Minute


def near_abs_delta(minute: Minute, near: float) -> float | None:
    """The near wing's |delta| when this minute measured it: a usable quote, a non-zero implied
    volatility and a delta that is not the boundary. Anything else is unmeasured, never carried."""
    row = minute.puts.get(near)
    if row is None:
        return None
    bid, ask, delta, iv = row
    if quote_of(bid, ask) is None or not iv or delta is None or abs(delta) >= BOUNDARY_DELTA:
        return None
    return abs(delta)


def ticks_for(data: ExpiryData, entry_day: str, near: float) -> list[Tick]:
    return [
        Tick(m.ts, m.day, m.spot, near_abs_delta(m, near), m)
        for m in data.span(minute_ts(entry_day, *ENTRY_AT))
    ]


def fly_mark(minute: Minute, plan: dict) -> dict:
    """The mark snapshot `management.execution_gate` reads for the four fly legs at this minute."""
    qs = [
        quote_of(*minute.puts.get(k, (None, None))[:2])
        for k in (plan["near_strike"], plan["body_strike"], plan["far_strike"])
    ]
    ok = minute.spot is not None and all(qs)
    spreads = [(q["ask"] - q["bid"]) / q["mid"] for q in qs if q and q["mid"] > 0]
    return {"ok": ok, "max_spread_pct": max(spreads) if spreads else None}


# ------------------------------------------------------------------------------------------------
# Pure: an arm's add-on over the minute series.


def run_trigger(
    arm: str, ticks: list[Tick], data: ExpiryData, plan: dict, dte: int, lag: int = 0
) -> dict | None:
    """When (if ever) the arm's add-on opens: the module's own `management.evaluate` over the series.

    The arm arms on the minute its trigger is met and fires on the first later minute that prices as a
    credit and passes the execution gate; the trigger is read `lag` minutes late (delta, spot and flip
    together). Returns the fire (`ts`, `day`, strikes, credit, the two legs' quotes) or None."""
    base = BASE[arm]
    params = {**management.PARAM_DEFAULTS, "arm": base}
    position = {"armed_at": None, "addon_fired_at": None}
    state = {"peak_abs_delta": None, "below_flip_seen": False}
    far = plan["far_strike"]
    for i, t in enumerate(ticks):
        src = ticks[i - lag] if i - lag >= 0 else None
        tick = {
            "abs_delta": src.abs_delta if src else None,
            "spot": src.spot if src else None,
            "gamma_flip": data.flip_at(src.minute) if (src and base == "flip") else None,
        }
        addon = None
        if position["armed_at"] is not None:
            addon = data.addon_plan(t.minute, far, dte)
        decision, state = management.evaluate(
            position, params, trigger_state=state, tick=tick, addon_credit=addon["credit"] if addon else None
        )
        if decision.action == "arm":
            position["armed_at"] = t.ts
        elif (
            decision.action == "fire_addon"
            and management.execution_gate(fly_mark(t.minute, plan), params, now=None) is None
        ):
            legs = {leg["leg_role"]: leg for leg in addon["legs"]}
            return {
                "ts": t.ts,
                "day": t.day,
                "short": addon["short_strike"],
                "long": addon["long_strike"],
                "credit": addon["credit"],
                "quotes": [
                    {"bid": legs["addon_short"]["bid"], "ask": legs["addon_short"]["ask"]},
                    {"bid": legs["addon_long"]["bid"], "ask": legs["addon_long"]["ask"]},
                ],
            }
    return None


def shortstop_exit(ticks: list[Tick], fire: dict, delay: int = 0) -> dict | None:
    """The delta add-on bought back alone: the first minute after the fire that spot is at or below its
    short strike, at the first minute from then with two-sided quotes on both legs (`delay` further
    minutes with such quotes for the stress). None when it never happens: held to settlement."""
    latched, waits = False, delay
    for t in ticks:
        if t.ts <= fire["ts"]:
            continue
        latched = latched or (t.spot is not None and t.spot <= fire["short"])
        if not latched:
            continue
        rows = [t.minute.puts.get(fire["short"]), t.minute.puts.get(fire["long"])]
        qs = [quote_of(r[0], r[1]) if r else None for r in rows]
        if not all(qs):
            continue
        if waits:
            waits -= 1
            continue
        return {
            "ts": t.ts,
            "cost": qs[0]["mid"] - qs[1]["mid"],
            "quotes": [{"bid": q["bid"], "ask": q["ask"]} for q in qs],
        }
    return None


def simulate_entry(data: ExpiryData | None, day: str, exp: str, dte: int) -> dict:
    """One entry, every arm at lag 0 and 1: the structure, and for each add-on arm when its add-on
    opened (and, for `delta-shortstop`, when it closed). No price of an outcome is read here."""
    entry = plan_at_entry(data, day, dte)
    rec = {"entry": day, "exp": exp, "dte": dte, "status": entry["status"], "reason": entry.get("reason")}
    if entry["status"] != "entered":
        return rec
    plan = entry["plan"]
    rec["plan"] = {
        k: plan[k] for k in ("spot", "expected_move", "body_strike", "near_strike", "far_strike", "credit")
    }
    rec["plan"]["quotes"] = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in plan["legs"]]
    ticks = ticks_for(data, day, plan["near_strike"])
    rec["arms"] = {"control": {0: None, 1: None}}
    for lag in (0, 1):
        for arm in ("bounce", "flip", "delta"):
            rec["arms"].setdefault(arm, {})[lag] = run_trigger(arm, ticks, data, plan, dte, lag)
        d = rec["arms"]["delta"][lag]
        # The delta family shares its trigger. The dte3 variant gives up for good once fewer than three
        # days remain, which is the same as the delta fire not counting when it falls inside them.
        keep = d is not None and days_left(d["day"], exp) >= DTE3_DAYS
        rec["arms"].setdefault("delta-dte3", {})[lag] = d if keep else None
        stopped = None
        if d is not None:
            after = [t for t in ticks if t.ts > d["ts"]]
            stopped = {
                "fire": d,
                "exit": shortstop_exit(after, d),
                "exit_late": shortstop_exit(after, d, delay=1),
            }
        rec["arms"].setdefault("delta-shortstop", {})[lag] = stopped
    return rec


# ------------------------------------------------------------------------------------------------
# Pure: what a position is worth.


def trade_parts(rec: dict, arm: str, lag: int, settle: float, exit_kind: str = "exit") -> dict:
    """The position's dollar parts at settlement `settle`: fly and add-on gross at mid, the legs to cost
    slippage on, and the fees by kind. `exit_kind` is `exit` or `exit_late` for `delta-shortstop`."""
    p = rec["plan"]
    body, near, far = p["body_strike"], p["near_strike"], p["far_strike"]
    owed = (
        2 * engine.settle_intrinsic(body, settle)
        - engine.settle_intrinsic(near, settle)
        - engine.settle_intrinsic(far, settle)
    )
    itm = {k for k in (body, near, far) if engine.settle_intrinsic(k, settle) > 0}
    parts = {"fly": (p["credit"] - owed) * 100, "addon": 0.0, "fly_quotes": p["quotes"], "addon_quotes": None,
             "exit_quotes": None, "addon_credit": None, "fired": False}  # fmt: skip
    fire = rec["arms"][arm][lag]
    exit_ = None
    if arm == "delta-shortstop" and fire is not None:
        exit_ = fire[exit_kind]
        fire = fire["fire"]
    if fire is not None:
        parts["fired"], parts["addon_credit"], parts["addon_quotes"] = True, fire["credit"], fire["quotes"]
        if exit_ is not None:
            parts["addon"] = (fire["credit"] - exit_["cost"]) * 100
            parts["exit_quotes"] = exit_["quotes"]
        else:
            s, l_ = fire["short"], fire["long"]
            parts["addon"] = (
                fire["credit"] - (engine.settle_intrinsic(s, settle) - engine.settle_intrinsic(l_, settle))
            ) * 100
            itm |= {k for k in (s, l_) if engine.settle_intrinsic(k, settle) > 0}
    parts["itm"] = len(itm)
    return parts


def net(parts: dict, basis: str) -> float:
    """Net dollars of one position on a cost basis: gross at mid less entry, add-on and exit slippage,
    and the open, close and settlement fees (the fee multiple scales all of them)."""
    b, cfg = BASES[basis], CONFIG[basis]
    fee_open = engine.entry_cost(SYMBOL, parts["fly_quotes"], 1, cfg)
    slip = 100 * b["credit_concession"] if "credit_concession" in b else fee_open["slippage"]
    fee = fee_open["fee"]
    if parts["addon_quotes"] is not None:
        add = engine.addon_entry_cost(SYMBOL, parts["addon_quotes"], 1, cfg)
        slip += 100 * b["credit_concession"] if "credit_concession" in b else add["slippage"]
        fee += add["fee"]
    if parts["exit_quotes"] is not None:
        slip += fees.slippage_dollars(parts["exit_quotes"], 1, cfg)
        fee += fees.ic_close_fee(SYMBOL, 1, legs=2, sell_legs=1, ndigits=2)
    fee += engine.settlement_fee(parts["itm"])
    return parts["fly"] + parts["addon"] - slip - b["fee"] * fee


def trade_nets(rec: dict, arm: str, lag: int, settle: float) -> dict[str, float]:
    parts = trade_parts(rec, arm, lag, settle)
    out = {basis: net(parts, basis) for basis in BASES}
    if arm == "delta-shortstop":
        out["exit_late"] = net(trade_parts(rec, arm, lag, settle, "exit_late"), "verdict")
    else:
        out["exit_late"] = out["verdict"]
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the gate.


def gate_state(v9: float | None, v: float | None) -> tuple[bool, str | None]:
    """(passes, refusal): VIX9D / VIX below the live bar, a missing print refused, never allowed."""
    if not v9 or not v:
        return False, "regime_unmeasured"
    return (True, None) if v9 / v < GATE_BAR else (False, "regime_inverted")


# ------------------------------------------------------------------------------------------------
# Pure: statistics.


def clusters(pairs: list[tuple[str, float]]) -> list[tuple[float, int]]:
    """(sum, count) per settlement week, weeks in date order."""
    by: dict[str, list[float]] = defaultdict(list)
    for week, x in pairs:
        by[week].append(x)
    return [(sum(v), len(v)) for _, v in sorted(by.items())]


def boot(cl: list[tuple[float, int]], seed: int = SEED, resamples: int = RESAMPLES) -> dict:
    """The mean per trade, its one-sided bootstrap p for 'not above zero', and a 95% interval, resampling
    whole settlement weeks (seeded)."""
    sums, counts = [c[0] for c in cl], [c[1] for c in cl]
    k = len(cl)
    if not k:
        return {"mean": None, "p": 1.0, "lo": None, "hi": None, "weeks": 0}
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        idx = rng.choices(range(k), k=k)
        n = sum(counts[i] for i in idx)
        means.append(sum(sums[i] for i in idx) / n)
    means.sort()
    below = sum(1 for m in means if m <= 0)
    return {
        "mean": sum(sums) / sum(counts),
        "p": (below + 1) / (resamples + 1),
        "lo": means[int(0.025 * resamples)],
        "hi": means[math.ceil(0.975 * resamples) - 1],
        "weeks": k,
    }


def mean_without_worst(cl: list[tuple[float, int]], n: int = WORST_WEEKS) -> float | None:
    """The mean per trade with the `n` worst settlement weeks removed."""
    kept = sorted(cl)[n:]
    return sum(c[0] for c in kept) / sum(c[1] for c in kept) if kept and sum(c[1] for c in kept) else None


def tail_share(cl: list[tuple[float, int]]) -> dict:
    """The worst 5% of weeks: their net, and its share of every losing week's."""
    k = max(1, math.ceil(0.05 * len(cl))) if cl else 0
    worst = sorted(c[0] for c in cl)[:k]
    losses = sum(c[0] for c in cl if c[0] < 0)
    return {"weeks": k, "net": sum(worst), "share_of_losses": (sum(worst) / losses) if losses else None}


def rows_in(rows: list[dict], window: str) -> list[dict]:
    return [r for r in rows if in_window(r["entry"], window)]


def basis_pairs(rows: list[dict], basis: str = "verdict") -> list[tuple[str, float]]:
    return [(r["exp"], r["net"][basis]) for r in rows]


def paired(arm_rows: list[dict], control_rows: list[dict], basis: str = "verdict") -> list[tuple[str, float]]:
    """Per entry, the arm's net less control's on the same entry."""
    ctrl = {r["entry"]: r["net"][basis] for r in control_rows}
    return [(r["exp"], r["net"][basis] - ctrl[r["entry"]]) for r in arm_rows if r["entry"] in ctrl]


def mean_of(pairs) -> float | None:
    return sum(x for _, x in pairs) / len(pairs) if pairs else None


def fire_weeks(rows: list[dict]) -> int:
    return len({r["exp"] for r in rows if r["fired"]})


def by_period(rows: list[dict], key) -> dict:
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r["net"]["verdict"])
    return {
        k: {"n": len(v), "net": round(sum(v), 2), "mean": round(sum(v) / len(v), 2)}
        for k, v in sorted(out.items())
    }


def quarter(r: dict) -> str:
    y, m = r["entry"][:4], int(r["entry"][5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def describe(rows: list[dict]) -> dict:
    """One arm-and-gate's reported figures over some rows (verdict basis): nothing here is judged."""
    pairs = basis_pairs(rows)
    xs = [x for _, x in pairs]
    cl = clusters(pairs)
    return {
        "n": len(rows),
        "mean": round(mean(xs), 2) if xs else None,
        "win_rate": round(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None,
        "worst_trade": round(min(xs), 2) if xs else None,
        "five_largest_losses": [round(x, 2) for x in sorted(xs)[:5]],
        "fires": sum(1 for r in rows if r["fired"]),
        "fire_weeks": fire_weeks(rows),
        "tail": tail_share(cl),
        "by_year": by_period(rows, lambda r: r["entry"][:4]),
        "by_quarter": by_period(rows, quarter),
        "mean_without_2022": mean_of(
            [p for r, p in zip(rows, pairs, strict=True) if r["entry"][:4] != "2022"]
        )
        if rows
        else None,
        "mean_without_2025": mean_of(
            [p for r, p in zip(rows, pairs, strict=True) if r["entry"][:4] != "2025"]
        )
        if rows
        else None,
    }


# ------------------------------------------------------------------------------------------------
# Pure: the judgement.


def cell_key(arm: str, gate: str) -> str:
    return f"{arm}/{gate}"


def design_figures(arm: str, rows: list[dict], control_rows: list[dict]) -> dict:
    """Everything the design window's rule reads for one arm and gate state, from that window's rows."""
    d, h1, h2 = rows_in(rows, "design"), rows_in(rows, "h1"), rows_in(rows, "h2")
    cl = clusters(basis_pairs(d))
    fig = {
        "n": len(d),
        "fire_weeks": fire_weeks(d),
        "boot": boot(cl),
        "halves": [mean_of(basis_pairs(h1)), mean_of(basis_pairs(h2))],
        "stress": {b: mean_of(basis_pairs(d, b)) for b in (*STRESSES, "exit_late")},
        "without_worst": mean_without_worst(cl),
    }
    if arm != "control":
        diff = paired(d, rows_in(control_rows, "design"))
        fig["vs_control"] = {**boot(clusters(diff)), "n": len(diff)}
    return fig


def holdout_figures(arm: str, rows: list[dict], control_rows: list[dict]) -> dict:
    h = rows_in(rows, "holdout")
    fig = {"n": len(h), "mean": mean_of(basis_pairs(h))}
    if arm != "control":
        c = rows_in(control_rows, "holdout")
        fig["control_mean"] = mean_of(basis_pairs(c))
        fig["vs_control"] = mean_of(paired(h, c))
    return fig


def decide(
    arm: str, gate: str, design: dict, holdout: dict, holm_reject: bool, flip_valid: bool
) -> tuple[str, dict]:
    """The verdict word and the conditions behind it. Pure over the figures."""
    cond: dict[str, bool | None] = {}
    if arm == "flip" and not flip_valid:
        return "NO VERDICT (INPUT)", cond
    thin = design["n"] < MIN_TRADES["design"][gate] or (
        arm != "control" and design["fire_weeks"] < MIN_FIRE_WEEKS
    )
    if thin:
        return "NO VERDICT", cond
    m = design["boot"]["mean"]
    pos = lambda x: x is not None and x > 0  # noqa: E731
    cond["mean_above_zero_holm"] = pos(m) and holm_reject
    cond["each_half_positive"] = all(pos(x) for x in design["halves"])
    cond["positive_halfway_fills"] = pos(design["stress"]["halfway"])
    cond["positive_1_5x_fees"] = pos(design["stress"]["fees15"])
    cond["positive_worst_weeks_removed"] = pos(design["without_worst"])
    if arm == "delta-shortstop":
        cond["positive_exit_a_minute_later"] = pos(design["stress"]["exit_late"])
    base_ok = all(cond.values())
    if arm != "control":
        vc = design["vs_control"]
        cond["above_control"] = bool(pos(vc["mean"]) and vc["p"] < ALPHA)
    if not base_ok:
        return "FAIL", cond
    if arm != "control" and not cond["above_control"]:
        return "PAYS, NOT FOR THE ADD-ON", cond
    if holdout["n"] < MIN_TRADES["holdout"][gate]:
        return "NO VERDICT", cond
    cond["holdout_mean_above_zero"] = pos(holdout["mean"])
    if arm != "control":
        cond["holdout_above_control"] = pos(holdout["vs_control"])
    return ("PASS" if all(cond.values()) else "FAIL"), cond


def minimum_detectable(design_rows: list[dict], tests: int = 12) -> dict:
    """The design window's own spread against the assumed one: the smallest mean per trade the Holm bar
    could see with 80% power, from the week-to-week spread of the mean (the ladder rows of one week
    share a tape). Reported, not judged."""
    cl = clusters(basis_pairs(design_rows))
    if len(cl) < 3:
        return {}
    per_week = [s / n for s, n in cl]
    nd = NormalDist()
    z = nd.inv_cdf(1 - ALPHA / tests) + nd.inv_cdf(POWER)
    sd_week = pstdev(per_week) * math.sqrt(len(per_week) / (len(per_week) - 1))
    return {
        "weeks": len(cl),
        "sd_of_weekly_mean": round(sd_week, 2),
        "mde_per_trade": round(z * sd_week / math.sqrt(len(cl)), 2),
    }


def build_result(rows: dict[str, list[dict]], flip_valid: bool = True) -> dict:
    """Verdicts, design and holdout figures for the twelve judged cells, and the reported cuts.

    `rows[arm]` is one dict per entered position: `entry`, `exp`, `fired`, `net` (by basis), `gate` and
    `gate_prior` (whether each passes), `tue`, `quarter_end`, `net_lag1`."""
    gated = {a: [r for r in rows[a] if r["gate"]] for a in ARMS}
    by_gate = {"ungated": rows, "gated": gated}
    cells, p = {}, {}
    for gate in GATES:
        for arm in ARMS:
            r, c = by_gate[gate][arm], by_gate[gate]["control"]
            design, hold = design_figures(arm, r, c), holdout_figures(arm, r, c)
            cells[cell_key(arm, gate)] = {"design": design, "holdout": hold}
            n_ok = design["n"] >= MIN_TRADES["design"][gate] and (
                arm == "control" or design["fire_weeks"] >= MIN_FIRE_WEEKS
            )
            p[cell_key(arm, gate)] = (
                design["boot"]["p"] if n_ok and not (arm == "flip" and not flip_valid) else 1.0
            )
    holm = core_stats.holm(p, ALPHA)
    adj = core_stats.holm_adjusted(p)
    verdict = {}
    for key, cell in cells.items():
        arm, gate = key.split("/")
        word, cond = decide(arm, gate, cell["design"], cell["holdout"], holm[key], flip_valid)
        cell.update(
            verdict=word,
            conditions=cond,
            p=round(p[key], 4),
            holm_adjusted_p=round(adj[key], 4),
            holm_reject=holm[key],
        )
        verdict[key] = word
    result = {"verdict": verdict, "cells": cells, "flip_valid": flip_valid}
    result["reading"] = partial_readings(verdict)
    result["reported"] = reported(rows, by_gate)
    return result


def partial_readings(verdict: dict) -> list[str]:
    """A gated arm passing while its ungated arm does not is said as that, never as the structure passing."""
    out = []
    for arm in ARMS:
        u, g = verdict[cell_key(arm, "ungated")], verdict[cell_key(arm, "gated")]
        if g == "PASS" and u != "PASS":
            out.append(f"{arm}: passes only when gated (ungated: {u.lower()}); the gate, not the structure")
        elif u == "PASS" and g != "PASS":
            out.append(f"{arm}: passes ungated, gated is {g.lower()}")
        elif u.startswith("PAYS") or g.startswith("PAYS"):
            out.append(f"{arm}: pays, not for the add-on")
    return out


def reported(rows: dict, by_gate: dict) -> dict:
    """Everything reported beside the judged tests: no number here decides a verdict."""
    out: dict = {"cells": {}, "versus_delta": {}, "live_pilot": {}, "prior_close_gate": {}, "mde": {}}
    for gate in GATES:
        for arm in ARMS:
            r = by_gate[gate][arm]
            cell = {}
            for w in ("design", "holdout"):
                sub = rows_in(r, w)
                cell[w] = describe(sub)
                cell[w]["by_basis"] = {b: mean_of(basis_pairs(sub, b)) for b in (*BASES, "exit_late")}
                cell[w]["lag1_mean"] = mean_of(
                    [(x["exp"], x["net_lag1"]) for x in sub if x.get("net_lag1") is not None]
                )
                cell[w]["lag1_fires"] = sum(1 for x in sub if x.get("fired_lag1"))
            out["cells"][cell_key(arm, gate)] = cell
        out["mde"][gate] = minimum_detectable(rows_in(by_gate[gate]["control"], "design"))
    for gate in GATES:
        for variant in ("delta-dte3", "delta-shortstop"):
            for w in ("design", "holdout"):
                diff = paired(rows_in(by_gate[gate][variant], w), rows_in(by_gate[gate]["delta"], w))
                out["versus_delta"][f"{variant}/{gate}/{w}"] = {
                    **{k: v for k, v in boot(clusters(diff)).items() if k in ("mean", "p")},
                    "n": len(diff),
                }
    pilot = [r for r in by_gate["gated"]["bounce"] if r.get("tue") and not r.get("quarter_end")]
    out["live_pilot"] = {w: describe(rows_in(pilot, w)) for w in ("design", "holdout")}
    for arm in ARMS:
        prior = [r for r in rows[arm] if r.get("gate_prior")]
        out["prior_close_gate"][arm] = {w: describe(rows_in(prior, w)) for w in ("design", "holdout")}
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the paper checks.


def validate_entries(records: dict[str, dict], paper: list[dict]) -> dict:
    """The replay's entries against the paper ledger's on the dates both cover: same expiry, same
    strikes and an entry credit within a tick. `records` is {entry day: simulate_entry's record}."""
    by_day: dict[str, dict] = {}
    for p in paper:
        by_day.setdefault(p["entry_session"], p)
    # Dates the store covers: a paper entry whose expiry the store does not hold is not comparable.
    overlap = {d: p for d, p in by_day.items() if d in records and records[d].get("reason") != "not_in_store"}
    matched, misses = 0, []
    for d, p in overlap.items():
        r = records[d]
        ok = (
            r["status"] == "entered"
            and r["exp"] == p["expiration"]
            and (r["plan"]["body_strike"], r["plan"]["near_strike"], r["plan"]["far_strike"])
            == (p["body_strike"], p["near_strike"], p["far_strike"])
            and abs(r["plan"]["credit"] - p["entry_credit"]) <= ENTRY_CREDIT_TOL
        )
        matched += ok
        if not ok:
            misses.append(
                {
                    "entry": d,
                    "replay": r["status"] if r["status"] != "entered" else "different",
                    "reason": r.get("reason"),
                }
            )
    share = matched / len(overlap) if overlap else 0.0
    return {
        "paper_entries": len(by_day),
        "overlapping": len(overlap),
        "matched": matched,
        "share": round(share, 4),
        "passed": bool(overlap) and share >= ENTRY_MATCH_MIN,
        "misses": misses,
    }


def fire_agreement(records: dict[str, dict], paper: list[dict]) -> dict:
    """Reported, not gated: for each add-on arm, whether paper and the replay agree that its add-on
    fired, and on which day."""
    out: dict[str, dict] = {}
    for p in paper:
        arm, r = p["arm"], records.get(p["entry_session"])
        if (
            arm not in ("bounce", "flip", "delta")
            or not r
            or r["status"] != "entered"
            or p.get("addon_missed_at")
        ):
            continue
        fire = r["arms"][arm][0]
        a = out.setdefault(arm, {"n": 0, "agree": 0, "both": 0, "same_day": 0})
        paper_day = (p["addon_fired_at"] or "")[:10]
        a["n"] += 1
        a["agree"] += bool(paper_day) == bool(fire)
        if paper_day and fire:
            a["both"] += 1
            a["same_day"] += paper_day == fire["day"]
    return out


def validate_flip(pairs: list[tuple[float, float | None]]) -> dict:
    """(recorded, recomputed) flips for each measured paper tick the store covers: within five index
    points on at least 80% of them, over at least `FLIP_MIN_TICKS`, or the flip arms are no verdict."""
    n = len(pairs)
    close = sum(1 for rec, new in pairs if new is not None and abs(rec - new) <= FLIP_TOL)
    share = close / n if n else 0.0
    return {
        "ticks": n,
        "within_tolerance": close,
        "share": round(share, 4),
        "tolerance": FLIP_TOL,
        "passed": n >= FLIP_MIN_TICKS and share >= FLIP_MATCH_MIN,
    }


def gate_agreement(pairs: list[tuple[float | None, float | None, float | None, float | None]]) -> dict:
    """(open VIX9D, open VIX, tick VIX9D, tick VIX): how often the open-based gate agrees with the one
    the entry tick saw."""
    n = agree = 0
    for o9, o, t9, t in pairs:
        if not (t9 and t):
            continue
        n += 1
        agree += gate_state(o9, o)[0] == gate_state(t9, t)[0]
    return {"n": n, "agree": agree, "share": round(agree / n, 4) if n else None}


# ------------------------------------------------------------------------------------------------
# The stores and files.


def _paths():
    from cherrypick.backtest import paths

    return paths


def out_dir() -> Path:
    d = _paths().results_dir() / "research" / "item35"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def parse_cboe(text: str, column: str) -> dict[str, float]:
    """{iso date: value} from a Cboe daily file's `column` (OPEN or CLOSE), else its one value column."""
    import csv
    import io

    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or rows[0][0].strip().upper() != "DATE":
        return {}
    header = [h.strip().upper() for h in rows[0]]
    col = header.index(column) if column in header else 1
    out = {}
    for row in rows[1:]:
        try:
            out[datetime.strptime(row[0].strip(), "%m/%d/%Y").date().isoformat()] = float(row[col])
        except (ValueError, IndexError):
            continue
    return {d: v for d, v in out.items() if v > 0}


def cboe_file(name: str, column: str) -> dict[str, float]:
    path = core_home.data_dir() / "market-files" / "cboe" / f"{name}.csv"
    return parse_cboe(path.read_text(encoding="utf-8"), column) if path.exists() else {}


class OIStore:
    """The morning's SPXW open interest per weekly expiry, from `open_interest.db` (`fetch-oi`)."""

    def __init__(self, conn: sqlite3.Connection | None):
        self.conn, self.cache = conn, {}

    def oi_of(self, expiration: str):
        def read(day: str) -> dict:
            key = (expiration, day)
            if key not in self.cache:
                self.cache[key] = (
                    {}
                    if self.conn is None
                    else {
                        (r, k): n
                        for r, k, n in self.conn.execute(
                            "SELECT right, strike, open_interest FROM oi_contracts "
                            "WHERE symbol = ? AND session = ? AND expiration = ?",
                            (ROOT, day, expiration),
                        )
                    }
                )
            return self.cache[key]

        return read


@dataclass
class Inputs:
    conn: sqlite3.Connection
    oi: OIStore
    opens: dict  # {"VIX9D": {...}, "VIX": {...}}
    closes: dict  # {"VIX9D": ..., "VIX": ..., "SPX": ...}
    paper_db: Path | None

    def gate(self, day: str, prior: bool = False) -> tuple[bool, str | None]:
        if prior:
            return gate_state(self.prior_close("VIX9D", day), self.prior_close("VIX", day))
        return gate_state(self.opens["VIX9D"].get(day), self.opens["VIX"].get(day))

    def prior_close(self, name: str, day: str) -> float | None:
        d = cal.previous_trading_day(date.fromisoformat(day))
        return self.closes[name].get(d.isoformat())


def open_inputs() -> Inputs:
    from cherrypick.backtest import addonfetch, oifetch, settings
    from cherrypick.bwb import db as bwb_db

    s = settings.load(None)
    oi_path = oifetch.db_path(s)
    return Inputs(
        _ro(addonfetch.db_path(s)),
        OIStore(_ro(oi_path) if oi_path.exists() else None),
        {n: cboe_file(n, "OPEN") for n in ("VIX9D", "VIX")},
        {n: cboe_file(n, "CLOSE") for n in ("VIX9D", "VIX", "SPX")},
        Path(bwb_db.default_db_path()),
    )


def entry_days() -> list[tuple[str, str, int]]:
    """(entry day, expiry, dte) for every entry the store can hold: the judged windows and the paper's."""
    out = []
    for d in sessions(STORE_FIRST_ENTRY, STORE_LAST_EXPIRY):
        plan = clock.target_expiration(d)
        if plan and date.fromisoformat(plan["expiration"]) <= STORE_LAST_EXPIRY:
            out.append((d.isoformat(), plan["expiration"], plan["dte"]))
    return out


def simulate_all(inp: Inputs, days: list[tuple[str, str, int]], log=print) -> dict[str, dict]:
    """simulate_entry for each day, expiry by expiry so each is read once."""
    by_exp: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for d, e, dte in days:
        by_exp[e].append((d, dte))
    out = {}
    for n, (exp, items) in enumerate(sorted(by_exp.items())):
        data = load_expiry(inp.conn, exp, inp.oi.oi_of(exp))
        for d, dte in items:
            out[d] = simulate_entry(data, d, exp, dte)
        if n % 25 == 0:
            log(f"simulated {n}/{len(by_exp)} expiries")
    return out


def rows_from_records(inp: Inputs, records: dict[str, dict]) -> tuple[dict[str, list[dict]], dict]:
    """One row per entered position and arm, and the drops and refusals by gate state, arm, reason and
    year. A position with no settlement print is dropped (`no_settlement`). A gated arm never attempts
    an entry on a refused day, so that day counts there only as the gate's refusal."""
    rows: dict[str, list[dict]] = {a: [] for a in ARMS}
    counts: dict = {g: {a: defaultdict(lambda: defaultdict(int)) for a in ARMS} for g in GATES}
    for d, rec in sorted(records.items()):
        if not any(in_window(d, w) for w in ("design", "holdout")):
            continue
        year = d[:4]
        status, reason = rec["status"], rec.get("reason")
        settle = inp.closes["SPX"].get(rec["exp"])
        if status == "entered" and settle is None:
            status, reason = "dropped", "no_settlement"
        gate_ok, gate_reason = inp.gate(d)
        for arm in ARMS:
            outcome = "entered" if status == "entered" else reason
            counts["ungated"][arm][outcome][year] += 1
            counts["gated"][arm][outcome if gate_ok else gate_reason][year] += 1
            if status != "entered":
                continue
            row = {
                "entry": d,
                "exp": rec["exp"],
                "gate": gate_ok,
                "gate_prior": inp.gate(d, prior=True)[0],
                "tue": date.fromisoformat(d).weekday() == 1,
                "quarter_end": cal.is_quarterly_expiry(date.fromisoformat(d)),
                "fired": rec["arms"][arm][0] is not None,
                "fired_lag1": rec["arms"][arm][1] is not None,
                "net": trade_nets(rec, arm, 0, settle),
                "net_lag1": trade_nets(rec, arm, 1, settle)["verdict"] if arm != "control" else None,
            }
            rows[arm].append(row)
    return rows, {
        g: {a: {r: dict(y) for r, y in v.items()} for a, v in arms.items()} for g, arms in counts.items()
    }


def settlement_check(inp: Inputs, expiries: list[str]) -> dict:
    """The exchange's close against the store's last index price at or before 16:00 on the expiry day:
    differences reported, nothing repaired."""
    diffs = []
    for e in expiries:
        close = inp.closes["SPX"].get(e)
        row = inp.conn.execute(
            "SELECT spot FROM q WHERE expiration = ? AND day = ? AND ts <= ? AND spot IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1",
            (e, e, minute_ts(e, 16, 0)),
        ).fetchone()
        if close and row:
            diffs.append(abs(row[0] - close))
    if not diffs:
        return {"n": 0}
    return {
        "n": len(diffs),
        "mean_abs": round(mean(diffs), 2),
        "max_abs": round(max(diffs), 2),
        "over_1_point": sum(1 for x in diffs if x > 1),
        "over_5_points": sum(1 for x in diffs if x > 5),
    }


# ------------------------------------------------------------------------------------------------
# Paper.

PAPER_COLS = (
    "entry_session",
    "arm",
    "expiration",
    "body_strike",
    "near_strike",
    "far_strike",
    "entry_credit",
    "addon_fired_at",
    "entry_vix9d",
    "entry_vix",
    "structure_signature",
)


def load_paper(path: Path, start: str) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    conn = _ro(path)
    conn.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
        missed = "addon_missed_at" if "addon_missed_at" in have else "NULL AS addon_missed_at"
        cols = ", ".join(c if c in have else f"NULL AS {c}" for c in PAPER_COLS)
        q = (
            f"SELECT {cols}, {missed} FROM bwb_positions "
            "WHERE status != 'cancelled' AND entry_session >= ? ORDER BY entry_session"
        )
        return [dict(r) for r in conn.execute(q, (start,))]
    finally:
        conn.close()


def load_paper_flips(path: Path, start: str) -> list[tuple[str, int, float, str]]:
    """(expiry, tick epoch, recorded flip, session) for each measured paper trigger tick."""
    if not path or not Path(path).exists():
        return []
    conn = _ro(path)
    try:
        q = (
            "SELECT p.expiration, t.ticked_at, t.gamma_flip, t.session_date FROM bwb_trigger_ticks t "
            "JOIN (SELECT DISTINCT entry_session, structure_signature, expiration FROM bwb_positions) p "
            "ON p.entry_session = t.entry_session AND p.structure_signature = t.structure_signature "
            "WHERE t.flip_measured = 1 AND t.gamma_flip IS NOT NULL AND t.session_date >= ?"
        )
        return list(conn.execute(q, (start,)))
    finally:
        conn.close()


def flip_pairs(inp: Inputs, ticks: list[tuple[str, int, float, str]]) -> list[tuple[float, float | None]]:
    """(recorded, recomputed) for the paper ticks whose minute the store covers."""
    out, loaded = [], {}
    for exp, at, recorded, _session in ticks:
        if exp not in loaded:
            loaded[exp] = load_expiry(inp.conn, exp, inp.oi.oi_of(exp))
        data = loaded[exp]
        minute = data.by_ts.get(int(at // 60 * 60)) if data else None
        if minute is None:
            continue
        out.append((recorded, data.flip_at(minute)))
    return out


def run_validation(inp: Inputs, log=print) -> dict:
    """The paper checks, before any outcome is read: entries (gated), fires (reported), the recomputed
    flip (gates the flip arms), the gate's open-based reading against the tick's (reported)."""
    start = WINDOWS["paper"][0].isoformat()
    paper = load_paper(inp.paper_db, start)
    days = [x for x in entry_days() if x[0] >= start]
    records = simulate_all(inp, days, log)
    val = {"entries": validate_entries(records, paper), "fires": fire_agreement(records, paper)}
    ticks = load_paper_flips(inp.paper_db, start)
    val["flip"] = validate_flip(flip_pairs(inp, ticks))
    val["gate"] = gate_agreement(
        [
            (
                inp.opens["VIX9D"].get(p["entry_session"]),
                inp.opens["VIX"].get(p["entry_session"]),
                p["entry_vix9d"],
                p["entry_vix"],
            )
            for p in paper
            if p["arm"] == "control"
        ]
    )
    return val


# ------------------------------------------------------------------------------------------------
# Commands.


def _load(path: str | None) -> Path:
    return Path(path) if path else out_dir() / "results.json"


def cmd_plan(args) -> int:
    spec = importlib.util.spec_from_file_location(
        "fetch_addon_weeklies", Path(__file__).with_name("fetch_addon_weeklies.py")
    )
    fetch = importlib.util.module_from_spec(spec)
    sys.modules["fetch_addon_weeklies"] = fetch
    spec.loader.exec_module(fetch)
    from cherrypick.backtest import addonfetch, settings

    s = settings.load(None)
    held, refused = fetch.store_state(addonfetch.db_path(s))
    body = fetch.build_requests(date.fromisoformat(fetch.START), date.fromisoformat(fetch.END), held, refused)
    out = Path(args.out) if args.out else out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "addon_requests.json").write_text(
        json.dumps({"requests": body["requests"]}, indent=1), encoding="utf-8"
    )
    days = [d.isoformat() for d in sessions(STORE_FIRST_ENTRY, STORE_LAST_EXPIRY)]
    (out / "oi_plan.json").write_text(json.dumps({"sessions": {d: [ROOT] for d in days}}), encoding="utf-8")
    entries = entry_days()
    window = {w: sum(1 for d, _, _ in entries if in_window(d, w)) for w in ("design", "holdout", "paper")}
    print(
        json.dumps(
            {
                "addon_requests": body["counts"],
                "oi_sessions": len(days),
                "entries_by_window": window,
                "dir": str(out),
            }
        )
    )
    return 0


def cmd_validate(args) -> int:
    inp = open_inputs()
    val = run_validation(inp)
    path = out_dir() / "validation.json"
    path.write_text(json.dumps(val, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "entries_passed": val["entries"]["passed"],
                "flip_passed": val["flip"]["passed"],
                "file": str(path),
            }
        )
    )
    return 0 if val["entries"]["passed"] else 2


def cmd_study(args) -> int:
    inp = open_inputs()
    val = run_validation(inp)
    (out_dir() / "validation.json").write_text(json.dumps(val, indent=1, default=str), encoding="utf-8")
    if not val["entries"]["passed"]:
        print(
            "the replay's entries do not match paper's: the study stops and the replay is fixed first",
            file=sys.stderr,
        )
        return 2
    records = simulate_all(
        inp, [x for x in entry_days() if any(in_window(x[0], w) for w in ("design", "holdout"))]
    )
    rows, counts = rows_from_records(inp, records)
    result = build_result(rows, flip_valid=val["flip"]["passed"])
    result["validation"] = val
    result["counts"] = counts
    result["settlement_check"] = settlement_check(inp, sorted({r["exp"] for rs in rows.values() for r in rs}))
    (d0, d1), (h0, h1) = WINDOWS["design"], WINDOWS["holdout"]
    result["window"] = f"design {d0}..{d1}; holdout {h0}..{h1}"
    result["run_id"] = f"item35-{date.today().isoformat()}"
    path = _load(args.json)
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "file": str(path)}))
    return 0


def cmd_report(args) -> int:
    src = _load(args.json)
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.


def _d(x: float | None) -> str:
    return "" if x is None else f"{'−' if x < 0 else ''}${abs(x):,.0f}"


CHECKS = (
    ("mean_above_zero_holm", "Mean above zero, Holm of 12"),
    ("each_half_positive", "Positive in both halves"),
    ("positive_halfway_fills", "Positive at halfway fills"),
    ("positive_1_5x_fees", "Positive at 1.5 times fees"),
    ("positive_worst_weeks_removed", "Positive without the 5 worst weeks"),
    ("positive_exit_a_minute_later", "Exit a minute later (shortstop)"),
    ("above_control", "Above control, same entries"),
    ("holdout_mean_above_zero", "Holdout mean above zero"),
    ("holdout_above_control", "Holdout above control"),
)


def findings(result: dict) -> list[str]:
    out = []
    v = result["verdict"]
    passed = [k for k, w in v.items() if w == "PASS"]
    out.append(
        f"**Twelve tests, {len(passed)} pass.** "
        + (f"Passing: {', '.join(passed)}." if passed else "None passes both windows.")
    )
    out += [f"**Partial reading:** {x}." for x in result.get("reading", [])]
    val = result.get("validation") or {}
    if val:
        e, f = val["entries"], val["flip"]
        out.append(
            f"**Entries against paper:** {e['matched']} of {e['overlapping']} paper entries on covered"
            f" dates match the replay (needed {ENTRY_MATCH_MIN:.0%})."
        )
        out.append(
            f"**Flip against paper:** {f['within_tolerance']} of {f['ticks']} ticks within"
            f" {f['tolerance']:g} points ({f['share']:.0%}, needed {FLIP_MATCH_MIN:.0%}); the flip arms are"
            + (" judged." if result.get("flip_valid") else " no verdict (input).")
        )
    return out


def report(result: dict) -> str:
    lines = [
        "# Item 35: the bwb module's whole rule at real quotes",
        "",
        "Does the put broken-wing butterfly, alone or with an add-on put spread, earn after costs; and"
        " does it do so on days when VIX9D is below VIX? Six arms, each ungated and gated: twelve tests,"
        " Holm across all twelve. Net dollars per trade, one lot, the paper ledger's fills.",
        "",
        "## The twelve verdicts",
        "",
        "| arm | ungated | gated |",
        "|---|---|---|",
    ]
    for arm in ARMS:
        u, g = (result["verdict"][cell_key(arm, gate)].lower() for gate in GATES)
        lines.append(f"| {arm} | {u} | {g} |")
    lines += [
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## The design window, per test",
        "",
    ]
    lines += [
        "| test | n | fire weeks | mean/trade | Holm p | halves | halfway | 1.5x fees | no worst 5"
        " | vs control | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, cell in result["cells"].items():
        d = cell["design"]
        vc = d.get("vs_control")
        gap = f"{_d(vc['mean'])} (p {vc['p']:.3f})" if vc else ""
        halves = f"{_d(d['halves'][0])} / {_d(d['halves'][1])}"
        stress = f"{_d(d['stress']['halfway'])} | {_d(d['stress']['fees15'])}"
        head = f"| {key} | {d['n']} | {d['fire_weeks']} | {_d(d['boot']['mean'])}"
        lines.append(
            f"{head} | {cell['holm_adjusted_p']:.3f} | {halves} | {stress} | {_d(d['without_worst'])}"
            f" | {gap} | {cell['verdict'].lower()} |"
        )
    lines += [
        "",
        "## Checks behind each verdict",
        "",
        "| test | " + " | ".join(label for _, label in CHECKS) + " |",
        "|---|" + "---|" * len(CHECKS),
    ]
    for key, cell in result["cells"].items():
        marks = [
            ("" if k not in cell["conditions"] else "pass" if cell["conditions"][k] else "fail")
            for k, _ in CHECKS
        ]
        lines.append(f"| {key} | " + " | ".join(marks) + " |")
    lines += [
        "",
        "## The holdout, reported",
        "",
        "| test | n | mean/trade | control's | gap |",
        "|---|---|---|---|---|",
    ]
    for key, cell in result["cells"].items():
        h = cell["holdout"]
        gap, ctrl = _d(h.get("vs_control")), _d(h.get("control_mean"))
        lines.append(f"| {key} | {h['n']} | {_d(h['mean'])} | {ctrl} | {gap} |")
    lines += ["", "## Reported beside the tests", ""]
    for key, cell in result["reported"]["cells"].items():
        d = cell["design"]
        b = d.get("by_basis", {})
        lines.append(
            f"- **{key}** design: win rate {d['win_rate']}, worst trade {_d(d['worst_trade'])},"
            f" natural side {_d(b.get('natural'))}, live basis {_d(b.get('live'))},"
            f" trigger a minute late {_d(d['lag1_mean'])}, fires {d['fires']} in {d['fire_weeks']} weeks."
        )
    for key, v in result["reported"]["versus_delta"].items():
        lines.append(
            f"- {key} against `delta`: {_d(v['mean'])} per trade over {v['n']} entries"
            f" (p {v['p']:.3f}); reported, not judged."
        )
    lines += [
        "",
        "## Limits",
        "",
        "- Minute quotes, not the module's cache ticks; the vendor's delta feed runs about a minute behind"
        " the cache's, so the trigger is also read a minute late (above). A minute stamp has no quote"
        " age: kinder than a cache up to 300 seconds old.",
        "- `wall` (a call structure) cannot be replayed from this store and is not judged.",
        "- The gate reads the day's opening prints, not the entry tick's.",
        "- The `flip` is recomputed from the weekly chain and the morning's open interest; strikes beyond"
        " the stored band carry no gamma.",
        "- One lot of a $5/$10 structure at index levels of 3,700 to 7,000: the same rule is a different"
        " trade in 2022 and 2026.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="the fetch request files and what they cover")
    pl.add_argument("--out")
    pl.set_defaults(fn=cmd_plan)
    va = sub.add_parser("validate", help="the paper checks")
    va.set_defaults(fn=cmd_validate)
    st = sub.add_parser("study", help="validate, then every arm, then the verdicts")
    st.add_argument("--json")
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--json")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
