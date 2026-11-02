"""Item 38, the calendars module's own rule at real quotes (docs/edge-hypotheses-plan.md): does the
weekly SPY double calendar earn after costs, entered Monday or the Friday before?

    python scripts/calendars_item38.py exdates                 # SPY ex-dividend dates from the broker's file
    python scripts/calendars_item38.py listing [--out-dir DIR]   # the listing probes, then the start S
    python scripts/calendars_item38.py pilot   [--out-dir DIR]   # the 40-week pilot, then the basis
    python scripts/calendars_item38.py requests [--out-dir DIR]  # the full request file and the run card
    python scripts/calendars_item38.py validate [--out-dir DIR]  # the replay's entries against paper's
    python scripts/calendars_item38.py study   [--json PATH]     # the verdict per arm
    python scripts/calendars_item38.py report  [--json PATH]     # results.md from an existing results.json

Queued as research-queue rows `item38/exdates`, `item38/listing-fetch`, `item38/listing`,
`item38/pilot-fetch`, `item38/pilot`, `item38/fetch`, `item38/validate`, `item38/study`; the fetches
are `fetch-intraday-chains` rows (packages/backtest), everything else a `script` row.

**The listing step is a request file, not an expirations call.** The vendor's expirations call is a
network call a `script` row may not make (the research queue keeps the one ThetaData session for the
fetch kinds). `listing` therefore writes `listing-requests.json`: for every Monday from 2016-01-04 to
2022-12-26, a one-bar probe (09:45, two strikes either side of the money) of the week's Friday front
expiry and of its following-Monday back expiry on the entry session. The store records each as
`complete` (the vendor lists the expiry and holds a quote) or `empty` (it does not), and `listing`
reads those records: S is the first Monday where both are complete. Counts only; no price is read.
About 730 tiny requests rather than a dozen calls, and the cheapest way the queue allows.

**The rest, in order.** `pilot` draws 40 weeks by a seeded hash of the date (never by outcome),
spread across the years from S, writes their eleven requests each, and, once the store holds the
answers, the coverage and the basis decision: the study starts at the first year from which every
later year has at least 90% of the pilot's requests complete. `requests` writes the full request file
and the run card (start, windows, ex-dividend dates, fee basis) before any quote is fetched.
`validate` compares the replay's entries with the paper ledger's, and the study refuses to run unless
that passed. `study` replays the module's own rule at the stored bars and writes `results.json`
(a top-level `verdict` per arm and `window`, as the register's board reads them) and `results.md`.

**What is reused, not restated.** The week arithmetic (`clock`), the strike selection (`engine.plan_entry`,
`engine.price_at_strikes`), the cost stack (`engine.entry_cost`, `close_cost`, `assignment_from`,
`core.fees.assignment_fee`), the exit gate (`core.spreadbook.exit_spread_blocks`), the usable-quote
test (`core.streamcache.usable_quote`) and the exit-policy derivation (`exit_policies.derive` for
`control`, `time-fri-noon` and `expiry-longs-mon`), fed week records built from the stored bars in the
shape `week_data` returns. A second derivation of the same rule would drift where nothing could see it.

Everything written goes under the backtest data dir (results/research/item38/), never into the repo:
the inputs and everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.calendars import clock, engine, exit_policies
from cherrypick.core import calendar as cal
from cherrypick.core import fees as core_fees
from cherrypick.core import settlement as core_settlement
from cherrypick.core import stats as core_stats
from cherrypick.core.spreadbook import exit_spread_blocks
from cherrypick.core.streamcache import usable_quote

SYMBOL = "SPY"
SEED = 38
STRIKE_RANGE = 50  # strikes either side of the money, every chain request
PROBE_RANGE = 2  # the listing probes only need to find a quote
LISTING_FROM, LISTING_TO = date(2016, 1, 4), date(2022, 12, 26)
PROBE_BAR = "09:45"
LAST_WEEK = date(2026, 10, 19)  # its Monday disposition falls on 2026-10-26
PAPER_FROM = date(2026, 8, 24)
HOLDOUT_END = date(2026, 8, 21)
WARMUP_WEEKS = 52

PILOT_WEEKS = 40
THRESHOLD = 0.90
LATE_START = date(2019, 12, 31)  # after this the split moves, before any quote is read
WINDOWS_EARLY = {"design_end": date(2023, 12, 29), "holdout_start": date(2024, 1, 2)}
WINDOWS_LATE = {"design_end": date(2024, 12, 31), "holdout_start": date(2025, 1, 2)}

ORDINARY_MONDAY, ORDINARY_FRIDAY = "dc_4_7", "dc_7_10"
ARMS = ("control", "friday:control", "low-ivr", "noon-friday")
RECORDING = ("path", "friday:path")
MIN_DESIGN = {"control": 150, "friday:control": 150, "noon-friday": 150, "low-ivr": 50}
MIN_HOLDOUT = {"control": 60, "friday:control": 60, "noon-friday": 60, "low-ivr": 20}
RESAMPLES = 2000
ALPHA = 0.05
FIVE = 5
IVR_ENTER = 1 / 3
IVR_WINDOW = 52
IVR_MIN_READINGS = 45  # of the 52 weekly readings, one reading declared here
IV_SANE = (0.02, 3.0)  # a decimal implied volatility; a percent-unit misread drops out as missing

MON_BARS = ("10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45", "12:00", "12:15")
MON_REQUIRED = ("10:00", "10:30", "11:30", "12:00")
FRI_BARS = ("15:50", "15:55", "16:00")
NOON_BARS = ("12:00", "12:05", "12:10")
CLOSE_BARS = ("15:45", "15:50", "15:55")
DISPO_BARS = ("09:45", "09:50", "09:55")
SENS_ENTRY_MON = ("10:30", "11:30", "12:00")
SENS_ENTRY_FRI = ("15:50", "15:55")
# The validation's two gates (2026-11-01, the user's decision, before any arm was read): the rule, on
# paper's own spot and expected move, and the feed, the store's spot against paper's at paper's bar.
# The first run's three misses were a strike flipped by a spot 20-34 cents off and a paper debit no
# replay bar came near; the bound was set after reading those gaps.
VALIDATE_SHARE = 0.75
FEED_SPOT_TOL = 1.00  # dollars of SPY, about one listed strike

#: The calendars config the declaration read (config.example.json `defaults`); the study refuses a
#: config that differs, since the verdict would then be about another rule.
DECLARED = {
    "em_factor": 0.85,
    "exit_window_start": "15:45",
    "exit_window_end": "15:55",
    "noon_exit_start": "12:00",
    "mon_disposition_time": "09:45",
    "max_leg_spread_pct": 0.25,
    "max_leg_spread_abs": 0.05,
    "strike_window_pct": 0.04,
}
COSTS = {
    "base": {},
    "mid": {"slippage_frac_of_spread": 0.0},
    "half": {"slippage_frac_of_spread": 0.25, "slippage_cap_frac_of_mid": None},
    "natural": {"slippage_frac_of_spread": 0.5, "slippage_cap_frac_of_mid": None},
}
#: A refusal that says nothing about the vendor's coverage: the request was not answered, the decision
#: waits for a re-run (as item 31's pilot reads them).
NOT_AN_ANSWER = (
    "unauthenticated",
    "session_in_use",
    "library_missing",
    "no_key",
    "transport_not_built",
    "unknown_vendor",
)


def config_for(variant: str = "base") -> dict:
    return {"defaults": dict(DECLARED), "tastytrade_costs": dict(COSTS[variant])}


# ------------------------------------------------------------------------------------------------
# Pure: the weeks.


def mondays(first: date, last: date) -> list[date]:
    d = first + timedelta(days=(0 - first.weekday()) % 7)
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def week_of(monday: date) -> dict | None:
    """One week's dates by the module's own functions. `tag` is the Monday regime's structure,
    `friday_tag` the Friday regime's (`clock.friday_entry_plan`'s entry is the session before)."""
    entry = clock.entry_session(monday)
    if entry is None:
        return None
    front, back = clock.front_expiration(entry), clock.back_expiration(entry)
    friday = clock.previous_trading_day(entry)
    if front is None or back is None or friday is None:
        return None
    return {
        "week_of": monday,
        "entry": entry,
        "front": front,
        "back": back,
        "friday": friday,
        "tag": clock.structure_tag(entry, front, back),
        "friday_tag": clock.structure_tag(friday, front, back),
    }


def weeks_between(first: date, last: date) -> list[dict]:
    return [w for m in mondays(first, last) if (w := week_of(m)) is not None]


def div_config(ex_dates: list[str], through: str) -> dict:
    return {"dividends": {SYMBOL: {"declared_through": through, "ex_dates": sorted(ex_dates)}}}


def week_status(w: dict, divs: dict) -> dict:
    """Which regime can trade the week, and why not: `{"monday": None | reason, "friday": ...}`.
    A holiday-shifted tag is `calendar_shift`; a declared ex-date in [entry, back] (Friday regime: from
    the entry Friday) is `ex_dividend`, by the module's own `engine.ex_date_in_span`. Neither is fetched.
    A week the declared calendar does not reach is refused, never read as dividend-free."""
    if not engine.dividend_coverage_ok(divs, SYMBOL, w["back"].isoformat()):
        raise ValueError(f"the ex-dividend calendar does not cover {w['back']}")
    out = {}
    for regime, start, tag, ordinary in (
        ("monday", w["entry"], w["tag"], ORDINARY_MONDAY),
        ("friday", w["friday"], w["friday_tag"], ORDINARY_FRIDAY),
    ):
        if tag != ordinary:
            out[regime] = "calendar_shift"
        elif engine.ex_date_in_span(divs, SYMBOL, start.isoformat(), w["back"].isoformat()):
            out[regime] = "ex_dividend"
        else:
            out[regime] = None
    return out


def in_play(status: dict) -> bool:
    return status["monday"] is None or status["friday"] is None


def window_for(entry: date, first: date, windows: dict | None = None) -> str:
    """design, holdout, paper (validation only) or outside, by the Monday entry session."""
    cut = windows or (WINDOWS_LATE if first > LATE_START else WINDOWS_EARLY)
    if entry < first:
        return "outside"
    if entry <= cut["design_end"]:
        return "design"
    if cut["holdout_start"] <= entry <= HOLDOUT_END:
        return "holdout"
    if entry >= PAPER_FROM:
        return "paper"
    return "outside"


# ------------------------------------------------------------------------------------------------
# Pure: the requests.


def _req(
    session: date,
    expiration: date,
    start: str,
    end: str,
    interval: str,
    kind: str,
    week: date,
    required: tuple,
    width: int = STRIKE_RANGE,
) -> dict:
    return {
        "symbol": SYMBOL,
        "session": session.isoformat(),
        "expiration": expiration.isoformat(),
        "start": start,
        "end": end,
        "interval": interval,
        "strike_range": width,
        "kind": kind,
        "week": week.isoformat(),
        "year": week.year,
        "required": list(required),
    }


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, the session before when that is a holiday."""
    d = third_friday(year, month)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def bracketing_monthlies(day: date) -> tuple[date, date] | None:
    """The two standard monthlies around 30 calendar days from `day`: the latest one at 1 to 30 days
    out and the earliest one at 30 or more. None when no monthly sits 1 to 30 days out (the monthly
    expired today and the next is further than 30 days): that week has no reading."""
    months = [(day.year + (day.month - 1 + k) // 12, (day.month - 1 + k) % 12 + 1) for k in range(0, 4)]
    exp = sorted(monthly_expiry(y, m) for y, m in months)
    lo = [e for e in exp if 1 <= (e - day).days <= 30]
    hi = [e for e in exp if (e - day).days >= 30]
    if not lo or not hi:
        return None
    return max(lo), min(hi)


def week_requests(w: dict, status: dict) -> list[dict]:
    """The week's requests: the 30-day reading's two monthlies always (the rank is a weekly series
    over every week), the other nine only when a regime can trade the week."""
    m, f = w["week_of"], w["friday"]
    out = []
    pair = bracketing_monthlies(f)
    if pair:
        for e in dict.fromkeys(pair):
            out.append(_req(f, e, "15:55", "15:55", "5m", "ivr", m, ("15:55",)))
    if not in_play(status):
        return out
    if status["monday"] is None:
        for e, k in ((w["front"], "mon_front"), (w["back"], "mon_back")):
            out.append(_req(w["entry"], e, "10:00", "12:15", "15m", k, m, MON_REQUIRED))
        for e, k in ((w["front"], "noon_front"), (w["back"], "noon_back")):
            out.append(_req(w["front"], e, "12:00", "12:10", "5m", k, m, ("12:00",)))
    for e, k in ((w["front"], "close_front"), (w["back"], "close_back")):
        out.append(_req(w["front"], e, "15:45", "15:55", "5m", k, m, ("15:45", "15:55")))
    out.append(_req(w["back"], w["back"], "09:45", "09:55", "5m", "dispo", m, ("09:45",)))
    if status["friday"] is None:
        for e, k in ((w["front"], "fri_front"), (w["back"], "fri_back")):
            out.append(_req(f, e, "15:50", "16:00", "5m", k, m, ("15:50", "15:55")))
    return out


def dedupe(reqs: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for r in reqs:
        seen.setdefault((r["session"], r["expiration"], r["start"], r["end"], r["interval"]), r)
    return list(seen.values())


def full_requests(first: date, divs: dict, last: date = LAST_WEEK) -> tuple[list[dict], dict]:
    """The study's whole request list from the start `first`, with the 52 weeks of warm-up readings
    before it, and counts by kind and by reason for the weeks left out."""
    warm = first - timedelta(weeks=WARMUP_WEEKS)
    reqs: list[dict] = []
    skipped: Counter = Counter()
    for w in weeks_between(warm, last):
        st = week_status(w, divs)
        if w["entry"] >= first:
            for regime, why in st.items():
                if why:
                    skipped[f"{regime}:{why}"] += 1
            reqs += week_requests(w, st)
        else:
            reqs += [r for r in week_requests(w, st) if r["kind"] == "ivr"]
    reqs = dedupe(reqs)
    return reqs, {"by_kind": dict(Counter(r["kind"] for r in reqs)), "weeks_skipped": dict(skipped)}


# --------------------------------------------------------------------------------------------
# Pure: the listing step and the pilot.


def probe_requests() -> list[dict]:
    """One-bar probes of the week's front and back expiries, for every Monday of the search range."""
    out = []
    for w in weeks_between(LISTING_FROM, LISTING_TO):
        for e, k in ((w["front"], "front"), (w["back"], "back")):
            r = _req(w["entry"], e, PROBE_BAR, PROBE_BAR, "5m", f"probe_{k}", w["week_of"], (), PROBE_RANGE)
            out.append(r)
    return out


def first_listed(states: dict[date, dict[str, str]]) -> dict:
    """S, the first Monday whose front and back probes are both `complete`, from each Monday's states
    (`complete`, `empty`, `refused`, `unanswered`, `missing`). Undecided while any probe before it is
    unanswered: an unasked or refused week could be the first."""
    weeks = sorted(states)
    for m in weeks:
        s = states[m]
        if s.get("front") == "complete" and s.get("back") == "complete":
            return {"decided": True, "start": m, "weeks_before": weeks.index(m)}
        if any(s.get(k) in ("missing", "unanswered", "refused") for k in ("front", "back")):
            return {"decided": False, "start": None, "stuck_at": m}
    return {"decided": True, "start": None, "weeks_before": len(weeks)}


def draw_key(week: date) -> str:
    """The rank of a week in the draw: a hash of the seed and the date, nothing else."""
    return hashlib.sha256(f"{SEED}|{week.isoformat()}".encode()).hexdigest()


def pilot_draw(candidates: list[date], total: int = PILOT_WEEKS) -> list[date]:
    """`total` weeks spread across the candidate years: an equal share each, the remainder to the
    years with the lowest hash of the year, within a year the lowest hashes of the date."""
    by_year: dict[int, list[date]] = defaultdict(list)
    for d in candidates:
        by_year[d.year].append(d)
    years = sorted(by_year)
    if not years:
        return []
    base, extra = divmod(total, len(years))
    bonus = set(sorted(years, key=lambda y: hashlib.sha256(f"{SEED}|y{y}".encode()).hexdigest())[:extra])
    out = []
    for y in years:
        k = base + (1 if y in bonus else 0)
        out += sorted(sorted(by_year[y], key=draw_key)[:k])
    return out


def basis_year(per_year: dict[int, dict], threshold: float = THRESHOLD) -> int | None:
    """The first year from which every later year has at least `threshold` of its pilot requests
    complete; None when the last year does not. `per_year[y] = {"requests": n, "complete": k}`."""
    start = None
    for y in sorted(per_year, reverse=True):
        c = per_year[y]
        if c["requests"] == 0 or c["complete"] / c["requests"] < threshold:
            break
        start = y
    return start


def moved_start(listed: date, year: int | None) -> date | None:
    """The study's first Monday: S itself, or the year's first Monday when the basis year is later."""
    if year is None:
        return None
    return max(listed, mondays(date(year, 1, 1), date(year, 1, 14))[0])


# ------------------------------------------------------------------------------------------------
# The store: the stored bars, read as the module would have been shown them.

_SQL_ROWS = (
    "SELECT strike, right, bid, ask, delta, iv, underlying FROM intraday_chain_quotes "
    "WHERE symbol = ? AND session = ? AND expiration = ? AND bar = ?"
)


class Chains:
    """Read access to `intraday_chains.db` for SPY. One chain at one bar is a dict keyed
    (strike, 'CALL'|'PUT') to its row; an absent bar is an empty dict."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cache: dict[tuple, dict] = {}

    def rows(self, session: date | str, expiration: date | str, bar: str) -> dict:
        key = (str(session), str(expiration), bar)
        if key not in self._cache:
            got = self.conn.execute(_SQL_ROWS, (SYMBOL, *key)).fetchall()
            self._cache[key] = {
                (r[0], r[1]): dict(
                    zip(("strike", "right", "bid", "ask", "delta", "iv", "underlying"), r, strict=True)
                )
                for r in got
            }
        return self._cache[key]

    def bars(self, session, expiration, start: str, end: str) -> list[str]:
        got = self.conn.execute(
            "SELECT DISTINCT bar FROM intraday_chain_quotes WHERE symbol = ? AND session = ? "
            "AND expiration = ? AND bar >= ? AND bar <= ? ORDER BY bar",
            (SYMBOL, str(session), str(expiration), start, end),
        ).fetchall()
        return [b[0] for b in got]

    def spot(self, session, expiration, bar: str) -> float | None:
        vals = [r["underlying"] for r in self.rows(session, expiration, bar).values() if r["underlying"]]
        return statistics.median(vals) if vals else None


def _usable(row: dict | None) -> dict | None:
    """A stored quote the module would price: `core.streamcache.usable_quote`, age zero."""
    if row is None:
        return None
    return usable_quote({"bid": row["bid"], "ask": row["ask"], "mid": None, "updated_at": 0.0}, 0.0, 1.0)


def _sym(exp, strike: float, right: str) -> str:
    return f"{SYMBOL}|{exp}|{right[0].upper()}|{strike}"


def snapshot(chains: Chains, session, front, back, bar: str, *, window_pct: float) -> dict | None:
    """The entry snapshot `engine.plan_entry` takes, from the stored chains at one bar: strikes within
    `window_pct` of spot, a quote only where the module would price one. None without both chains."""
    frows, brows = chains.rows(session, front, bar), chains.rows(session, back, bar)
    spot = chains.spot(session, front, bar)
    if not frows or not brows or not spot:
        return None
    entries = {"front": [], "back": []}
    quotes = {}
    for side, exp, rows in (("front", front, frows), ("back", back, brows)):
        for (strike, right), row in rows.items():
            if abs(strike - spot) > window_pct * spot:
                continue
            sym = _sym(exp, strike, right)
            entries[side].append(
                {
                    "strike_price": strike,
                    "option_type": right.lower(),
                    "streamer_symbol": sym,
                    "occ_symbol": sym,
                }
            )
            q = _usable(row)
            if q is not None:
                quotes[sym] = q
    return {
        "ok": True,
        "symbol": SYMBOL,
        "spot": spot,
        "front_expiration": str(front),
        "back_expiration": str(back),
        "front": entries["front"],
        "back": entries["back"],
        "quotes": quotes,
        "greeks": {},
    }


def attempt_entry(
    chains: Chains, session, front, back, bars, *, after: str | None = None, only: str | None = None
) -> dict:
    """The first bar at which the module's entry would go: `plan_entry` accepts the snapshot. `after`
    (Friday regime) admits only bars strictly later than the Monday regime's exit; `only` forces one
    bar (a sensitivity). A refusal carries the last attempted bar's reason."""
    last = {"reason": "no_entry_bars"}
    for bar in bars:
        if only and bar != only:
            continue
        if after and bar <= after:
            last = {"reason": "awaiting_session_exits"}
            continue
        snap = snapshot(chains, session, front, back, bar, window_pct=DECLARED["strike_window_pct"])
        if snap is None:
            continue
        got = engine.plan_entry(snap, {"em_factor": DECLARED["em_factor"]})
        if got["ok"]:
            return {"ok": True, "bar": bar, "plan": got["plan"], "snapshot": snap}
        last = {"reason": got["reason"]}
    return {"ok": False, **last}


def _stamp(day, bar: str) -> float:
    hh, mm = bar.split(":")
    d = date.fromisoformat(str(day))
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=clock.ET).timestamp()


def _tick(chains: Chains, legs: dict, day: date, bar: str, roles: tuple, params: dict) -> dict:
    """One bar as the exit logic sees it: every role priceable and no leg too wide, else nothing usable
    (the module closes a structure whole, and `exit_spread_blocks` is its gate)."""
    marks, spreads = {}, []
    ok = True
    for role in roles:
        leg = legs[role]
        row = chains.rows(day, leg["expiration"], bar).get((leg["strike"], leg["option_type"].upper()))
        q = _usable(row)
        if q is None:
            ok = False
            continue
        marks[role] = {"bid": q["bid"], "ask": q["ask"], "mid": q["mid"]}
        if q["mid"] > 0:
            spreads.append({"pct": (q["ask"] - q["bid"]) / q["mid"], "abs": q["ask"] - q["bid"]})
    if ok and exit_spread_blocks({"leg_spreads": spreads}, params):
        ok = False
    spot = None
    for role in roles:
        spot = chains.spot(day, legs[role]["expiration"], bar)
        if spot:
            break
    return {
        "ts": _stamp(day, bar),
        "session_date": str(day),
        "bar": bar,
        "spot": spot,
        "legs": marks if ok else {},
    }


FRONT_ROLES = ("front_put", "front_call")
LONG_ROLES = ("back_put", "back_call")
ALL_ROLES = ("front_put", "back_put", "front_call", "back_call")


def tick_groups(chains: Chains, legs: dict, front_day: date, back_day: date) -> dict:
    params = DECLARED
    return {
        "noon": [_tick(chains, legs, front_day, b, ALL_ROLES, params) for b in NOON_BARS],
        "close": [_tick(chains, legs, front_day, b, ALL_ROLES, params) for b in CLOSE_BARS],
        "dispo": [_tick(chains, legs, back_day, b, LONG_ROLES, params) for b in DISPO_BARS],
    }


def first_bar(ticks: list[dict], roles: tuple = ALL_ROLES) -> str | None:
    """The bar `exit_policies._first_tick` takes: the first with every role priceable."""
    for t in ticks:
        if all(r in t["legs"] for r in roles):
            return t["bar"]
    return None


# ------------------------------------------------------------------------------------------------
# The week record: built in the shape `exit_policies.week_data` returns, then handed to `derive`.


def entry_week(entry: dict, w: dict, regime: str) -> dict:
    """What an accepted entry fixes: the plan's two sides (strikes, mids, quotes) and the dates. The
    costs are a function of the cost basis, so they are added by `week_dict`."""
    arm = "friday:control" if regime == "friday" else "control"
    day = w["friday"] if regime == "friday" else w["entry"]
    tag = w["friday_tag"] if regime == "friday" else w["tag"]
    sides = {}
    for side, s in entry["plan"]["sides"].items():
        front, back = s["legs"]
        sides[side] = {
            "strike": s["strike"],
            "debit": s["debit"],
            "legs": {front["leg_role"]: front, back["leg_role"]: back},
        }
    return {
        "w": w,
        "regime": regime,
        "arm": arm,
        "entry_day": day,
        "tag": tag,
        "bar": entry["bar"],
        "sides": sides,
        "spot": entry["plan"]["spot"],
    }


def week_dict(rec: dict, groups: dict, cfg: dict) -> dict:
    """The `week_data` shape for one entry under one cost basis, with no ticks yet."""
    w = rec["w"]
    positions, legs = {}, {}
    for side, s in rec["sides"].items():
        fl, bl = (s["legs"][f"front_{side}"], s["legs"][f"back_{side}"])
        cost = engine.entry_cost(
            SYMBOL, [{"bid": fl["bid"], "ask": fl["ask"]}, {"bid": bl["bid"], "ask": bl["ask"]}], 1, cfg
        )
        positions[side] = {
            "position_id": f"{w['week_of']}:{rec['arm']}:{side}",
            "arm": rec["arm"],
            "side": side,
            "symbol": SYMBOL,
            "quantity": 1,
            "structure": rec["tag"],
            "strike": s["strike"],
            "front_expiration": w["front"].isoformat(),
            "back_expiration": w["back"].isoformat(),
            "entry_debit": s["debit"],
            "entry_cost": cost["fee"],
            "entry_slippage": cost["slippage"],
            "settlement_spot": None,
            "exit_cost": None,
            "exit_slippage": None,
            "settlement_fees": None,
        }
        for role, leg in s["legs"].items():
            legs[role] = {
                "leg_role": role,
                "strike": leg["strike"],
                "option_type": leg["option_type"],
                "expiration": leg["expiration"],
                "action": leg["action"],
                "entry_mid": leg["mid"],
                "close_value": None,
                "close_kind": None,
            }
    return {
        "week_of": w["week_of"].isoformat(),
        "positions": positions,
        "legs": legs,
        "assignments": {},
        "ticks": [],
    }


def settle(wk: dict, spot: float, dispose_spot: float | None) -> None:
    """Shorts settle at the official close; an in-the-money one delivers shares (booked at the
    settlement spot, `engine.assignment_from`), disposed at the Monday bar's underlying with the
    module's costs (`core.fees.assignment_fee`). Without a disposal price the assignment stays open and
    the derivation refuses the week."""
    for p in wk["positions"].values():
        p["settlement_spot"] = spot
    for role in FRONT_ROLES:
        leg = wk["legs"].get(role)
        if leg is None:
            continue
        a = engine.assignment_from(leg, spot, 1)
        if a is None:
            continue
        if dispose_spot is None:
            wk["assignments"][role] = {"status": "open", "share_pnl": None, "fees": None}
            continue
        wk["assignments"][role] = {
            "status": "disposed",
            "share_pnl": core_settlement.share_pnl(a["direction"], a["shares"], a["basis"], dispose_spot),
            "fees": core_fees.assignment_fee(a, dispose_spot),
            "direction": a["direction"],
        }


def run_policy(rec: dict, groups: dict, label: str, cfg: dict, close_of) -> dict:
    """One week under one policy label (`control`, `noon`, `path`) and one cost basis, through the
    module's `derive`. `control` falls through to the settle-and-Monday route when no close-window bar
    passes, `noon` first to the control window, as the declaration says; the fall-throughs are named."""
    fell = []
    plan = {"control": ("control", "close"), "noon": ("time-fri-noon", "noon"), "path": (None, None)}[label]
    policy, ticks = plan
    res = None
    if policy:
        wk = week_dict(rec, groups, cfg)
        wk["ticks"] = list(groups[ticks])
        res = exit_policies.derive(wk, policy, cfg)
        if not res["derivable"] and label == "noon":
            fell.append("noon_to_control")
            wk = week_dict(rec, groups, cfg)
            wk["ticks"] = list(groups["close"])
            res = exit_policies.derive(wk, "control", cfg)
        if not res["derivable"]:
            fell.append("close_to_monday")
            res = None
    if res is None:
        wk = week_dict(rec, groups, cfg)
        front_day = rec["w"]["front"]
        spot = close_of(front_day)
        if spot is None:
            return {"derivable": False, "reason": "no_settlement_spot", "fell": fell}
        dispo = next((t for t in groups["dispo"] if all(r in t["legs"] for r in LONG_ROLES)), None)
        settle(wk, spot, dispo["spot"] if dispo else None)
        wk["ticks"] = list(groups["dispo"])
        res = exit_policies.derive(wk, "expiry-longs-mon", cfg)
        if not res["derivable"]:
            return {"derivable": False, "reason": res["reason"], "fell": fell}
    res = dict(res, fell=fell, week=wk)
    res["settlement"] = _settlement_cost(res, wk)
    return res


def _settlement_cost(res: dict, wk: dict) -> float:
    cost = 0.0
    for role, x in res["exits"].items():
        if x["kind"] == "assigned":
            cost += wk["assignments"][role]["fees"] or 0.0
        elif x["kind"] == "cash_settled" and x["value"] > 0 and role in FRONT_ROLES:
            cost += engine.settlement_fee(1)
    return round(cost, 2)


def _debit_dollars(rec: dict) -> float:
    return round(100 * sum(s["debit"] for s in rec["sides"].values()), 2)


def entry_flags(rec: dict) -> dict:
    """Entry legs wider than the exit gate's bar (counted, not refused: the module has no entry gate),
    legs priced from a zero bid, and the back legs' mean spread as a share of mid."""
    wide = zero = 0
    back = []
    for s in rec["sides"].values():
        for role, leg in s["legs"].items():
            width = leg["ask"] - leg["bid"]
            if (
                leg["mid"] > 0
                and width / leg["mid"] > DECLARED["max_leg_spread_pct"]
                and width > DECLARED["max_leg_spread_abs"]
            ):
                wide += 1
            if leg["bid"] <= 0:
                zero += 1
            if role.startswith("back") and leg["mid"] > 0:
                back.append(width / leg["mid"])
    return {
        "wide_entry_legs": wide,
        "zero_bid_legs": zero,
        "back_spread": round(statistics.fmean(back), 4) if back else None,
    }


def _exit_bar(groups: dict, label: str, fell: list) -> str | None:
    """The bar a traded close took (None when the legs settled instead): the first bar of its window
    with every leg priceable, the window `derive` was given."""
    if label == "path" or "close_to_monday" in fell:
        return None
    window = "noon" if label == "noon" and "noon_to_control" not in fell else "close"
    return first_bar(groups[window])


def trade(rec: dict, groups: dict, label: str, close_of, *, bar_ticks: list | None = None) -> dict:
    """One trade's columns, adding up: gross at the mid marks, entry and exit fees, entry and exit
    slippage, settlement and share costs, net. Slippage is the difference to a run with none; the
    exit fees are what remains, so the row sums by construction. `bar_ticks` restricts the close
    window to given ticks (a sensitivity)."""
    g = groups if bar_ticks is None else {**groups, "close": bar_ticks}
    base = run_policy(rec, g, label, config_for("base"), close_of)
    if not base["derivable"]:
        return {"unresolved": base["reason"], "fell": base.get("fell", [])}
    free = run_policy(rec, g, label, config_for("mid"), close_of)
    wk = base["week"]
    entry_fee = round(sum(p["entry_cost"] for p in wk["positions"].values()), 2)
    entry_slip = round(sum(p["entry_slippage"] for p in wk["positions"].values()), 2)
    slip_total = round(base["fees"] - free["fees"], 2)
    exit_slip = round(slip_total - entry_slip, 2)
    settle_cost = base["settlement"]
    exit_fee = round(base["fees"] - entry_fee - entry_slip - exit_slip - settle_cost, 2)
    shares = round(sum((a["share_pnl"] or 0.0) for a in wk["assignments"].values()), 2)
    out = {
        "gross": base["gross_pnl"],
        "entry_fees": entry_fee,
        "entry_slippage": entry_slip,
        "exit_fees": exit_fee,
        "exit_slippage": exit_slip,
        "settlement": settle_cost,
        "costs": base["fees"],
        "net": base["net_pnl"],
        "share_pnl": shares,
        "net_stress15": round(base["gross_pnl"] - 1.5 * base["fees"], 2),
        "net_mid": free["net_pnl"],
        "debit": _debit_dollars(rec),
        "tag": rec["tag"],
        "entry_bar": rec["bar"],
        "fell": base["fell"],
        "trigger": base["trigger"]["reason"],
        "strikes": {s: v["strike"] for s, v in rec["sides"].items()},
        "exit_bar": _exit_bar(g, label, base["fell"]),
        **entry_flags(rec),
    }
    for name in ("half", "natural"):
        v = run_policy(rec, g, label, config_for(name), close_of)
        out[f"net_{name}"] = v["net_pnl"] if v["derivable"] else None
    return out


# ------------------------------------------------------------------------------------------------
# The 30-day reading and its rank.


def iv_at_money(rows: dict) -> float | None:
    """The mean of the call's and the put's implied volatility at the strike nearest the underlying."""
    spots = [r["underlying"] for r in rows.values() if r["underlying"]]
    if not spots:
        return None
    spot = statistics.median(spots)
    strikes = sorted({k[0] for k in rows}, key=lambda k: abs(k - spot))
    for k in strikes[:1]:
        c, p = rows.get((k, "CALL")), rows.get((k, "PUT"))
        vals = [x["iv"] for x in (c, p) if x and x["iv"] is not None]
        if len(vals) == 2 and all(IV_SANE[0] <= v <= IV_SANE[1] for v in vals):
            return sum(vals) / 2
    return None


def interpolate_30(day: date, lo: date, hi: date, iv_lo: float, iv_hi: float) -> float:
    """Implied volatility at exactly 30 calendar days, linear in total variance between the two."""
    d_lo, d_hi = (lo - day).days, (hi - day).days
    if d_lo == 30 or d_hi == d_lo:
        return iv_lo
    w_lo, w_hi = iv_lo**2 * d_lo / 365, iv_hi**2 * d_hi / 365
    w = w_lo + (w_hi - w_lo) * (30 - d_lo) / (d_hi - d_lo)
    return math.sqrt(w * 365 / 30)


def reading_at(chains: Chains, friday: date) -> float | None:
    pair = bracketing_monthlies(friday)
    if pair is None:
        return None
    ivs = [iv_at_money(chains.rows(friday, e, "15:55")) for e in pair]
    if None in ivs:
        return None
    return interpolate_30(friday, pair[0], pair[1], ivs[0], ivs[1])


def trailing_rank(
    series: list[float | None], i: int, window: int = IVR_WINDOW, minimum: int = IVR_MIN_READINGS
) -> float | None:
    """(reading less the trailing minimum) over (maximum less minimum) across the last `window`
    weekly readings including this one. None when this reading is missing, fewer than `minimum` of the
    window are present, or the range is flat."""
    if series[i] is None:
        return None
    seen = [x for x in series[max(0, i - window + 1) : i + 1] if x is not None]
    if len(seen) < minimum or max(seen) == min(seen):
        return None
    return (series[i] - min(seen)) / (max(seen) - min(seen))


def enters_low_ivr(rank: float | None) -> bool:
    return rank is not None and rank <= IVR_ENTER


# ------------------------------------------------------------------------------------------------
# The statistics.


def bootstrap(values: list[float], *, seed: int = SEED, resamples: int = RESAMPLES) -> dict | None:
    """The mean of weekly values, its 95% percentile interval and the one-sided p of a mean at or
    below zero, resampling weeks (one trade per arm per week). Seeded."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    return {
        "mean": sum(values) / n,
        "lo": means[int(0.025 * resamples)],
        "hi": means[math.ceil(0.975 * resamples) - 1],
        "p": sum(m <= 0 for m in means) / resamples,
        "n": n,
    }


def bootstrap_two(
    a: list[float], b: list[float], *, seed: int = SEED, resamples: int = RESAMPLES
) -> dict | None:
    """Mean of `a` less mean of `b`, resampling each group alone; one-sided p of a difference at or
    below zero."""
    if len(a) < 2 or len(b) < 2:
        return None
    rng = random.Random(seed)
    diffs = sorted(
        sum(rng.choices(a, k=len(a))) / len(a) - sum(rng.choices(b, k=len(b))) / len(b)
        for _ in range(resamples)
    )
    return {
        "diff": sum(a) / len(a) - sum(b) / len(b),
        "p": sum(d <= 0 for d in diffs) / resamples,
        "na": len(a),
        "nb": len(b),
    }


def without_worst(values: list[float], k: int = FIVE) -> list[float]:
    """The values less the `k` lowest weeks: the declaration's words, "the five worst weeks removed"."""
    return sorted(values)[k:]


def without_best(values: list[float], k: int = FIVE) -> list[float]:
    """The values less the `k` highest weeks. A mean cannot fall by losing its worst weeks, so the
    literal check never fires on a positive mean; the check that can show a mean living in a few big
    weeks is this one. Both are judged (see `judge_arm`)."""
    return sorted(values)[:-k] if k else list(values)


def mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def mde(sd: float | None, n: int, family: int = 4) -> dict | None:
    """The smallest mean detectable at the Holm bar (alpha / family, one-sided), and at 80% power."""
    if not sd or n < 2:
        return None
    nd = statistics.NormalDist()
    z_bar = nd.inv_cdf(1 - ALPHA / family)
    se = sd / math.sqrt(n)
    return {"at_bar": round(z_bar * se, 2), "at_80_power": round((z_bar + nd.inv_cdf(0.8)) * se, 2)}


# ------------------------------------------------------------------------------------------------
# The replay: every week, every arm.


def legs_of(rec: dict) -> dict:
    """The four legs' strikes, sides and expiries: all the tick builder needs of an entry."""
    return {
        role: {k: leg[k] for k in ("strike", "option_type", "expiration")}
        for s in rec["sides"].values()
        for role, leg in s["legs"].items()
    }


def groups_for(chains: Chains, rec: dict) -> dict:
    return tick_groups(chains, legs_of(rec), rec["w"]["front"], rec["w"]["back"])


def replay_week(chains: Chains, w: dict, status: dict, ctx: dict) -> dict:
    """One week under every arm. `ctx` carries `close_of`, `rank_of(friday)` (None where unmeasured),
    `public_rank_of`, and `prior_control` (front expiry -> that week's control trade). The Monday
    regime's exit marks are fixed before the Friday regime's entry bar is read: the Friday entry may
    only use bars after the prior week's control exit, and none at all if that exit fell through."""
    close_of = ctx["close_of"]
    rec: dict = {
        "week_of": w["week_of"].isoformat(),
        "entry": w["entry"].isoformat(),
        "year": w["entry"].year,
        "status": status,
        "arms": {},
        "sens": {},
    }
    arms = rec["arms"]
    mon = None
    if status["monday"] is None:
        got = attempt_entry(chains, w["entry"], w["front"], w["back"], MON_BARS)
        if got["ok"]:
            mon = (got, entry_week(got, w, "monday"))
    if mon:
        got, er = mon
        groups = groups_for(chains, er)
        arms["control"] = trade(er, groups, "control", close_of)
        arms["noon-friday"] = trade(er, groups, "noon", close_of)
        arms["path"] = trade(er, groups, "path", close_of)
        rank = ctx["rank_of"](w["friday"])
        rec["ivr"] = {"rank": rank, "public": ctx["public_rank_of"](w["friday"])}
        arms["low-ivr"] = (
            arms["control"]
            if enters_low_ivr(rank)
            else {"not_entered": "ivr_missing" if rank is None else "ivr_above"}
        )
        for bar in SENS_ENTRY_MON:
            alt = attempt_entry(chains, w["entry"], w["front"], w["back"], MON_BARS, only=bar)
            if alt["ok"]:
                er2 = entry_week(alt, w, "monday")
                rec["sens"][f"entry_{bar}"] = trade(er2, groups_for(chains, er2), "control", close_of).get(
                    "net"
                )
        for bar, tk in zip(CLOSE_BARS, groups["close"], strict=True):
            t = trade(er, groups, "control", close_of, bar_ticks=[tk])
            rec["sens"][f"exit_{bar}"] = None if t.get("fell") else t.get("net")
    else:
        why = status["monday"]
        arms["control"] = {"skipped": why} if why else {"refused": got["reason"]}
        arms["noon-friday"] = arms["low-ivr"] = arms["control"]
    if status["friday"] is None:
        prior = ctx["prior_control"].get(w["friday"].isoformat())
        after = None
        if prior is not None and "net" in prior:
            after = prior["exit_bar"] or "99:99"  # no exit bar: the exits are pending all session
        got = attempt_entry(chains, w["friday"], w["front"], w["back"], FRI_BARS, after=after)
        if got["ok"]:
            fri = entry_week(got, w, "friday")
            g2 = groups_for(chains, fri)
            arms["friday:control"] = trade(fri, g2, "control", close_of)
            arms["friday:path"] = trade(fri, g2, "path", close_of)
            for bar in SENS_ENTRY_FRI:
                alt = attempt_entry(
                    chains, w["friday"], w["front"], w["back"], FRI_BARS, after=after, only=bar
                )
                if alt["ok"]:
                    er2 = entry_week(alt, w, "friday")
                    rec["sens"][f"fentry_{bar}"] = trade(
                        er2, groups_for(chains, er2), "control", close_of
                    ).get("net")
            if mon:  # the paired-debit read: the Friday strikes priced at the Monday moment
                priced = engine.price_at_strikes(
                    mon[0]["snapshot"], {s: v["strike"] for s, v in fri["sides"].items()}
                )
                if priced["ok"]:
                    rec["paired_debit"] = {
                        "friday": _debit_dollars(fri),
                        "monday_moment": round(100 * priced["debit"], 2),
                    }
        else:
            arms["friday:control"] = {"refused": got["reason"]}
    else:
        arms["friday:control"] = {"skipped": status["friday"]}
    if "net" in arms.get("control", {}):
        ctx["prior_control"][w["front"].isoformat()] = arms["control"]
    return rec


# ------------------------------------------------------------------------------------------------
# The verdict.


def arm_series(records: list[dict], arm: str, window: str) -> dict[str, dict]:
    return {
        r["entry"]: r["arms"][arm]
        for r in records
        if r["window"] == window and "net" in r["arms"].get(arm, {})
    }


def median_week(records: list[dict]) -> str | None:
    """The median judged week of `control` in the design window: the date the halves split at."""
    days = sorted(arm_series(records, "control", "design"))
    return days[len(days) // 2] if days else None


def judge_arm(arm: str, records: list[dict], mid: str | None) -> dict:
    """All the design checks and the holdout reading for one arm."""
    out: dict = {"arm": arm}
    for win in ("design", "holdout"):
        s = arm_series(records, arm, win)
        nets = [t["net"] for t in s.values()]
        out[win] = {"n": len(nets), "mean": mean(nets), "boot": bootstrap(nets)}
    d = arm_series(records, arm, "design")
    nets = [t["net"] for t in d.values()]
    checks: dict[str, bool | None] = {}
    checks["enough_weeks"] = len(nets) >= MIN_DESIGN[arm]
    b = out["design"]["boot"]
    checks["lower_bound_above_zero"] = bool(b and b["lo"] > 0)
    halves = (
        [[t["net"] for day, t in d.items() if (day < mid) == first] for first in (True, False)] if mid else []
    )
    checks["positive_each_half"] = bool(halves) and all(h and mean(h) > 0 for h in halves)
    checks["positive_at_1_5x_costs"] = bool(d) and mean([t["net_stress15"] for t in d.values()]) > 0
    checks["positive_without_five_worst"] = len(nets) > FIVE and mean(without_worst(nets)) > 0
    checks["positive_without_five_best"] = len(nets) > FIVE and mean(without_best(nets)) > 0
    out["comparator"] = _comparator(arm, records, "design")
    checks["above_comparator"] = True if out["comparator"] is None else out["comparator"]["ok"]
    out["hold_comparator"] = _comparator(arm, records, "holdout")
    out["checks"] = checks
    out["sd"] = statistics.stdev(nets) if len(nets) > 1 else None
    out["mde"] = mde(out["sd"], len(nets))
    return out


def _comparator(arm: str, records: list[dict], win: str) -> dict | None:
    """noon-friday: paired against control on the same weeks. low-ivr: against control on the weeks
    it did not enter (two-sample). The others need none."""
    ctrl = arm_series(records, "control", win)
    if arm == "noon-friday":
        mine = arm_series(records, arm, win)
        days = sorted(set(mine) & set(ctrl))
        diffs = [mine[d]["net"] - ctrl[d]["net"] for d in days]
        bs = bootstrap(diffs)
        return {
            "kind": "paired",
            "weeks": len(days),
            "diff": mean(diffs),
            "p": bs["p"] if bs else None,
            "ok": bool(bs and bs["mean"] > 0 and bs["p"] < ALPHA),
        }
    if arm == "low-ivr":
        mine = arm_series(records, arm, win)
        rest = [t["net"] for d, t in ctrl.items() if d not in mine]
        two = bootstrap_two([t["net"] for t in mine.values()], rest)
        return {
            "kind": "two_sample",
            "entered": len(mine),
            "not_entered": len(rest),
            "diff": two["diff"] if two else None,
            "p": two["p"] if two else None,
            "ok": bool(two and two["diff"] > 0 and two["p"] < ALPHA),
        }
    return None


def verdict_for(arm: str, j: dict, holm_ok: bool) -> tuple[str, list[str]]:
    c = j["checks"]
    c["holm_significant"] = holm_ok
    if not c["enough_weeks"]:
        return "no verdict", [f"fewer than {MIN_DESIGN[arm]} design weeks"]
    failed = [k for k, v in c.items() if not v]
    if failed:
        return "fail", failed
    h = j["holdout"]
    if h["n"] < MIN_HOLDOUT[arm]:
        return "no verdict", [f"design passed; fewer than {MIN_HOLDOUT[arm]} holdout weeks"]
    hc = j["hold_comparator"]
    if not (h["mean"] > 0 and (hc is None or (hc.get("diff") or 0) > 0)):
        return "fail", ["holdout"]
    return "pass", []


def partial_reading(verdict: dict) -> list[str]:
    """Said out loud, as the declaration asks: an arm passing while control fails is that arm only."""
    out = []
    if verdict.get("control") != "pass":
        passing = [a for a in ARMS if a != "control" and verdict.get(a) == "pass"]
        if passing == ["friday:control"]:
            out.append("the Friday entrance only (control fails)")
        elif passing:
            out.append("recorded for " + ", ".join(passing) + " only; control itself does not pass")
    if verdict.get("noon-friday") == "pass" and verdict.get("control") != "pass":
        out.append("noon-friday is the exit only")
    return out


def judge_all(records: list[dict], first: date) -> dict:
    mid = median_week(records)
    judged = {arm: judge_arm(arm, records, mid) for arm in ARMS}
    p = {
        arm: (j["design"]["boot"]["p"] if j["design"]["boot"] and j["checks"]["enough_weeks"] else 1.0)
        for arm, j in judged.items()
    }
    holm = core_stats.holm(p, ALPHA)
    verdict, why = {}, {}
    for arm, j in judged.items():
        verdict[arm], why[arm] = verdict_for(arm, j, holm[arm])
    return {
        "verdict": verdict,
        "why": why,
        "judged": judged,
        "halves_at": mid,
        "reading": partial_reading(verdict),
    }


# ------------------------------------------------------------------------------------------------
# Reports on the records.


def by_key(records: list[dict], arm: str, key) -> dict:
    out: dict = defaultdict(list)
    for r in records:
        t = r["arms"].get(arm, {})
        if "net" in t and r["window"] in ("design", "holdout"):
            out[key(r)].append(t["net"])
    return {
        str(k): {"n": len(v), "mean": round(mean(v), 2), "total": round(sum(v), 2)}
        for k, v in sorted(out.items())
    }


def quarter(r: dict) -> str:
    d = date.fromisoformat(r["entry"])
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def tail_stats(records: list[dict], arm: str) -> dict:
    nets = sorted(
        t["net"]
        for r in records
        if r["window"] in ("design", "holdout")
        for t in [r["arms"].get(arm, {})]
        if "net" in t
    )
    if not nets:
        return {}
    k = max(1, math.ceil(0.05 * len(nets)))
    total = sum(nets)
    return {
        "n": len(nets),
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 4),
        "worst": nets[:FIVE],
        "share_lost_in_worst_5pct": round(sum(nets[:k]) / sum(x for x in nets if x < 0), 4)
        if any(x < 0 for x in nets)
        else None,
        "total": round(total, 2),
    }


def leave_one_year_out(records: list[dict], arm: str) -> list[dict]:
    years = sorted({r["year"] for r in records if "net" in r["arms"].get(arm, {})})
    out = []
    for y in years:
        vals = [
            r["arms"][arm]["net"]
            for r in records
            if "net" in r["arms"].get(arm, {}) and r["window"] == "design" and r["year"] != y
        ]
        out.append({"removed": y, "mean": round(mean(vals), 2) if vals else None})
    return sorted(out, key=lambda x: (x["mean"] is None, x["mean"]))[:2]


def refusal_counts(records: list[dict]) -> dict:
    out: dict = defaultdict(Counter)
    for r in records:
        for arm, t in r["arms"].items():
            for key in ("refused", "skipped", "not_entered", "unresolved"):
                if key in t:
                    out[arm][f"{key}:{t[key]}:{r['year']}"] += 1
            for f in t.get("fell", []):
                out[arm][f"fell:{f}:{r['year']}"] += 1
    return {a: dict(c) for a, c in out.items()}


def sensitivity_means(records: list[dict]) -> dict:
    out: dict = defaultdict(list)
    for r in records:
        if r["window"] not in ("design", "holdout"):
            continue
        for k, v in r["sens"].items():
            if v is not None:
                out[k].append(v)
    return {k: {"n": len(v), "mean": round(mean(v), 2)} for k, v in sorted(out.items())}


def cost_bases(records: list[dict], arm: str) -> dict:
    ts = [
        r["arms"][arm]
        for r in records
        if r["window"] in ("design", "holdout") and "net" in r["arms"].get(arm, {})
    ]
    if not ts:
        return {}
    keys = ("net", "net_stress15", "net_half", "net_natural", "net_mid")
    out = {}
    for k in keys:
        vals = [t[k] for t in ts if t.get(k) is not None]
        out[k] = round(mean(vals), 2) if vals else None
    return out


def columns(records: list[dict], arm: str) -> dict:
    ts = [
        r["arms"][arm]
        for r in records
        if r["window"] in ("design", "holdout") and "net" in r["arms"].get(arm, {})
    ]
    if not ts:
        return {}
    keys = (
        "gross",
        "entry_fees",
        "entry_slippage",
        "exit_fees",
        "exit_slippage",
        "settlement",
        "net",
        "debit",
    )
    out = {k: round(mean(t[k] for t in ts), 2) for k in keys}
    out["net_share_of_debit"] = round(sum(t["net"] for t in ts) / sum(t["debit"] for t in ts), 4)
    return out


def friday_vs_control(records: list[dict]) -> dict:
    diffs = [
        r["arms"]["friday:control"]["net"] - r["arms"]["control"]["net"]
        for r in records
        if r["window"] in ("design", "holdout")
        and "net" in r["arms"].get("friday:control", {})
        and "net" in r["arms"].get("control", {})
    ]
    paired = [
        r["paired_debit"] for r in records if r.get("paired_debit") and r["window"] in ("design", "holdout")
    ]
    return {
        "common_weeks": len(diffs),
        "mean_diff": round(mean(diffs), 2) if diffs else None,
        "paired_debit_weeks": len(paired),
        "mean_friday_less_monday_moment": round(mean(p["friday"] - p["monday_moment"] for p in paired), 2)
        if paired
        else None,
    }


def ivr_agreement(records: list[dict]) -> dict:
    both = [
        (enters_low_ivr(r["ivr"]["rank"]), enters_low_ivr(r["ivr"]["public"]))
        for r in records
        if r.get("ivr") and r["ivr"]["rank"] is not None and r["ivr"]["public"] is not None
    ]
    return {
        "weeks": len(both),
        "agree": sum(a == b for a, b in both),
        "vendor_enters": sum(a for a, _ in both),
        "public_enters": sum(b for _, b in both),
    }


def low_rank_by_year(records: list[dict]) -> dict:
    return dict(
        sorted(
            Counter(r["year"] for r in records if enters_low_ivr((r.get("ivr") or {}).get("rank"))).items()
        )
    )


def back_spread_by_year(records: list[dict]) -> dict:
    out: dict = defaultdict(list)
    for r in records:
        t = r["arms"].get("control", {})
        if t.get("back_spread") is not None:
            out[r["year"]].append(t["back_spread"])
    return {y: round(mean(v), 4) for y, v in sorted(out.items())}


def summarize(records: list[dict]) -> dict:
    return {
        "by_year": {a: by_key(records, a, lambda r: r["year"]) for a in (*ARMS, *RECORDING)},
        "by_quarter": {a: by_key(records, a, quarter) for a in ("control", "friday:control")},
        "tails": {a: tail_stats(records, a) for a in (*ARMS, *RECORDING)},
        "leave_one_year_out": {a: leave_one_year_out(records, a) for a in ("control", "friday:control")},
        "costs": {a: cost_bases(records, a) for a in (*ARMS, *RECORDING)},
        "columns": {a: columns(records, a) for a in (*ARMS, *RECORDING)},
        "refusals": refusal_counts(records),
        "sensitivities": sensitivity_means(records),
        "friday_vs_control": friday_vs_control(records),
        "ivr_agreement": ivr_agreement(records),
        "low_rank_weeks_by_year": low_rank_by_year(records),
        "back_leg_spread_by_year": back_spread_by_year(records),
    }


# ------------------------------------------------------------------------------------------------
# The ex-dividend dates, the closes, the files.


def out_dir(override: str | None = None) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / "item38"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")


def card_update(d: Path, **kw) -> dict:
    """The run card: every declared choice, written before it is used."""
    path = d / "runcard.json"
    card = read_json(path) if path.exists() else {}
    card.update(kw)
    write_json(path, card)
    return card


def ex_dates_from_broker(doc: dict) -> list[str]:
    """SPY's ex-dividend dates from the broker's dividend history file (scripts/fetch_dividends.py):
    each row's `occurred_date`. Read, never computed from a calendar rule."""
    return sorted(
        {str(row[0])[:10] for row in (doc.get("symbols", {}).get(SYMBOL) or {}).get("dividends", [])}
    )


def exdate_problems(ex_dates: list[str], first: date, last: date) -> list[str]:
    """A year without a quarterly pattern is a hole in the file, not a year without dividends: SPY
    pays four times a year. Declared in the study as a refusal, never an assumption."""
    out = []
    per = Counter(d[:4] for d in ex_dates)
    for y in range(first.year, last.year + 1):
        if per.get(str(y), 0) < 3 and not (y == last.year):
            out.append(f"{y}: {per.get(str(y), 0)} ex-dates")
    if not ex_dates or max(ex_dates) < (last - timedelta(days=90)).isoformat():
        out.append(f"the file ends {max(ex_dates) if ex_dates else 'empty'}; the study runs to {last}")
    return out


def cmd_exdates(args) -> int:
    from cherrypick.technicals import paths as tpaths

    path = tpaths.tastytrade_dividends()
    if not path.exists():
        raise SystemExit(f"no dividend file at {path}: run scripts/fetch_dividends.py first")
    dates = ex_dates_from_broker(read_json(path))
    d = out_dir(args.out_dir)
    problems = exdate_problems(dates, LISTING_FROM, LAST_WEEK)
    declared = []
    try:
        from cherrypick.calendars import cli as ccli

        declared = ((ccli.load_config().get("dividends") or {}).get(SYMBOL) or {}).get("ex_dates") or []
    except Exception:  # noqa: BLE001 -- the cross-check is a courtesy
        pass
    doc = {
        "source": "tastytrade dividend history (scripts/fetch_dividends.py)",
        "ex_dates": dates,
        "per_year": dict(sorted(Counter(x[:4] for x in dates).items())),
        "problems": problems,
        "calendars_declared_missing_from_broker": sorted(set(declared) - set(dates)),
    }
    write_json(d / "exdates.json", doc)
    card_update(d, exdates=doc["source"], exdate_count=len(dates))
    print(json.dumps({k: doc[k] for k in ("per_year", "problems", "calendars_declared_missing_from_broker")}))
    return 0 if dates else 1


def load_divs(d: Path, first: date) -> dict:
    """The declared ex-dividend calendar the study skips weeks by: the broker's dates, refused when a
    year from the study's start has no quarterly pattern or the file stops short of the last week.
    `declared_through` is the last week's back expiry, so a week past it is refused, not assumed
    dividend-free (`week_status`)."""
    dates = read_json(d / "exdates.json")["ex_dates"]
    problems = exdate_problems(dates, first, LAST_WEEK)
    if problems:
        raise SystemExit(f"ex-dividend dates have problems: {problems}")
    return div_config(dates, (LAST_WEEK + timedelta(days=7)).isoformat())


def _ladder_closes() -> dict[str, float]:
    from cherrypick.core.home import data_dir

    path = data_dir("research") / "item26" / "bars.db"
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return dict(
            conn.execute(
                "SELECT start, close FROM bars WHERE symbol = ? AND source = 'broker' AND interval = '1d' "
                "AND close IS NOT NULL",
                (SYMBOL,),
            )
        )
    finally:
        conn.close()


def load_closes() -> tuple[dict[str, float], dict]:
    """SPY's official close by session: the broker's daily candles first (scripts/fetch_ladder_bars.py
    --symbols SPY --source broker), the Dolt history for the sessions the broker's feed does not reach,
    and the two compared where both hold one."""
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    broker = _ladder_closes()
    conn = store.connect(tpaths.history_db())
    try:
        dolt = {b.date: b.close for b in store.raw_bars(conn, SYMBOL) if b.close}
    finally:
        conn.close()
    both = sorted(set(broker) & set(dolt))
    diffs = [abs(broker[d] - dolt[d]) for d in both]
    merged = {**dolt, **broker}
    return merged, {
        "broker_sessions": len(broker),
        "dolt_sessions": len(dolt),
        "compared": len(both),
        "differ_over_1c": sum(x > 0.01 for x in diffs),
        "max_diff": round(max(diffs), 4) if diffs else None,
    }


def vix_series() -> dict[str, float]:
    from cherrypick.overview import files

    path = files.cboe_path("VIX")
    if not path.exists():
        return {}
    return {d.isoformat(): v for d, v in files.parse_cboe(path.read_text(encoding="utf-8"))}


# ------------------------------------------------------------------------------------------------
# The coverage judgement (counts and presence only) and the commands.


def _item31():
    spec = importlib.util.spec_from_file_location(
        "earnings_item31", Path(__file__).with_name("earnings_item31.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("earnings_item31", module)
    spec.loader.exec_module(module)
    return module


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def request_state(conn: sqlite3.Connection, req: dict) -> str:
    """`complete`, `empty`, `refused`, `unanswered` (a refusal that is no answer) or `missing`."""
    row = conn.execute(
        "SELECT status, detail FROM intraday_chain_requests WHERE symbol = ? AND session = ? AND expiration = ? "  # noqa: E501
        'AND start = ? AND "end" = ? AND interval = ?',
        (SYMBOL, req["session"], req["expiration"], req["start"], req["end"], req["interval"]),
    ).fetchone()
    if row is None:
        return "missing"
    if row[0] == "refused":
        return "unanswered" if (row[1] or "").split(":")[0] in NOT_AN_ANSWER else "refused"
    return row[0]


def probe_states(conn: sqlite3.Connection, probes: list[dict]) -> dict[date, dict[str, str]]:
    out: dict[date, dict[str, str]] = defaultdict(dict)
    for r in probes:
        out[date.fromisoformat(r["week"])][r["kind"].removeprefix("probe_")] = request_state(conn, r)
    return dict(out)


def pilot_coverage(conn: sqlite3.Connection, reqs: list[dict]) -> dict:
    """Pilot coverage by year and kind: a request is complete when the vendor answered with rows and
    every required bar has a contract with a bid, an ask, a delta, a positive implied volatility and
    the underlying's price (item 31's own test). Also, as counts, the bars the declaration asks to test:
    whether the 15-minute grid came back and whether a 16:00 bar exists. Never a price."""
    judge = _item31().judge
    years: dict[int, dict] = {}
    kinds: dict[str, Counter] = defaultdict(Counter)
    missing_bars: Counter = Counter()
    reasons: Counter = Counter()
    bar_1600 = Counter()
    grid_15 = Counter()
    for r in reqs:
        j = judge(conn, r)
        y = years.setdefault(r["year"], {"requests": 0, "complete": 0, "unanswered": 0})
        y["requests"] += 1
        y["complete"] += j["state"] == "complete"
        y["unanswered"] += j["state"] == "unanswered"
        kinds[r["kind"]][j["state"]] += 1
        for b in j.get("missing", []):
            missing_bars[f"{r['kind']} {b}"] += 1
        if j.get("reason"):
            reasons[j["reason"]] += 1
        if r["kind"].startswith("fri_"):
            bar_1600["asked"] += 1
            bar_1600["present"] += bool(
                conn.execute(
                    "SELECT 1 FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ? "
                    "AND bar = '16:00' LIMIT 1",
                    (SYMBOL, r["session"], r["expiration"]),
                ).fetchone()
            )
        if r["kind"].startswith("mon_"):
            grid_15["asked"] += 1
            grid_15["bars_present"] += len(
                conn.execute(
                    "SELECT DISTINCT bar FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND "
                    "expiration = ? AND bar >= '10:00' AND bar <= '12:15'",
                    (SYMBOL, r["session"], r["expiration"]),
                ).fetchall()
            )
    unanswered = sum(y["unanswered"] for y in years.values())
    out = {
        "requests": sum(y["requests"] for y in years.values()),
        "unanswered": unanswered,
        "years": {
            str(k): dict(v, share=round(v["complete"] / v["requests"], 4)) for k, v in sorted(years.items())
        },
        "by_kind": {k: dict(v) for k, v in kinds.items()},
        "missing_required_bars": dict(missing_bars),
        "refusal_reasons": dict(reasons),
        "bar_16_00": dict(bar_1600),
        "monday_grid_15m": dict(grid_15),
    }
    if unanswered:
        out.update(decided=False, note=f"{unanswered} requests unanswered: fetch them, then run pilot again")
    else:
        out.update(decided=True, basis_year=basis_year({y: v for y, v in years.items()}))
    return out


def store_path() -> Path:
    from cherrypick.backtest import intradayfetch, settings

    return intradayfetch.db_path(settings.load(None))


def cmd_listing(args) -> int:
    d = out_dir(args.out_dir)
    probes = probe_requests()
    write_json(d / "listing-requests.json", {"requests": probes})
    print(f"{len(probes)} listing probes in {d / 'listing-requests.json'}")
    store = store_path()
    if not store.exists():
        print("no intraday store yet: queue the fetch, then run listing again")
        return 0
    conn = _ro(store)
    try:
        states = probe_states(conn, probes)
    finally:
        conn.close()
    got = first_listed(states)
    counts = Counter(s for v in states.values() for s in v.values())
    report = {
        "decided": got["decided"],
        "start": got.get("start"),
        "probe_states": dict(counts),
        "complete_weeks_from_start": sum(
            1
            for m, v in states.items()
            if got.get("start")
            and m >= got["start"]
            and v.get("front") == "complete"
            and v.get("back") == "complete"
        ),
        "weeks_without_both_from_start": sum(
            1
            for m, v in states.items()
            if got.get("start")
            and m >= got["start"]
            and not (v.get("front") == "complete" and v.get("back") == "complete")
        ),
        "stuck_at": got.get("stuck_at"),
    }
    write_json(d / "listing.json", report)
    if got["decided"] and got["start"]:
        card_update(d, listed_start=got["start"].isoformat())
    print(json.dumps(report, default=str))
    return 0


def candidate_weeks(first: date, divs: dict) -> list[dict]:
    return [w for w in weeks_between(first, LAST_WEEK) if in_play(week_status(w, divs))]


def cmd_pilot(args) -> int:
    d = out_dir(args.out_dir)
    listed = date.fromisoformat(read_json(d / "listing.json")["start"])
    divs = load_divs(d, listed)
    cands = candidate_weeks(listed, divs)
    drawn = pilot_draw([w["week_of"] for w in cands])
    chosen = {w["week_of"]: w for w in cands}
    reqs = dedupe([r for m in drawn for r in week_requests(chosen[m], week_status(chosen[m], divs))])
    write_json(
        d / "pilot-requests.json", {"seed": SEED, "weeks": [m.isoformat() for m in drawn], "requests": reqs}
    )
    print(f"{len(reqs)} pilot requests over {len(drawn)} weeks in {d / 'pilot-requests.json'}")
    store = store_path()
    if not store.exists():
        print("no intraday store yet: queue the fetch, then run pilot again")
        return 0
    conn = _ro(store)
    try:
        report = pilot_coverage(conn, reqs)
    finally:
        conn.close()
    report.update(
        rule=f"the first year from which every later year has at least {THRESHOLD:.0%} complete",
        threshold=THRESHOLD,
        listed_start=listed.isoformat(),
    )
    if report["decided"]:
        start = moved_start(listed, report["basis_year"])
        report["start"] = start.isoformat() if start else None
        if start:
            card_update(d, start=start.isoformat(), pilot_basis_year=report["basis_year"])
    write_json(d / "pilot.json", report)
    print(
        json.dumps({k: report.get(k) for k in ("requests", "unanswered", "decided", "basis_year", "start")})
    )
    return 0


def cmd_requests(args) -> int:
    d = out_dir(args.out_dir)
    pilot = read_json(d / "pilot.json")
    if not pilot.get("decided") or not pilot.get("start"):
        raise SystemExit("the pilot has not decided a start: fetch it and run pilot first")
    first = date.fromisoformat(pilot["start"])
    divs = load_divs(d, first)
    reqs, counts = full_requests(first, divs)
    windows = WINDOWS_LATE if first > LATE_START else WINDOWS_EARLY
    write_json(d / "requests.json", {"requests": reqs})
    card = card_update(
        d,
        start=first.isoformat(),
        last_week=LAST_WEEK.isoformat(),
        paper_from=PAPER_FROM.isoformat(),
        design_end=windows["design_end"].isoformat(),
        holdout_start=windows["holdout_start"].isoformat(),
        holdout_end=HOLDOUT_END.isoformat(),
        split="late" if first > LATE_START else "early",
        fee_basis="paper: 12.5% of spread capped at 15% of mid; stress 1.5x all costs",
        declared_defaults=DECLARED,
        strike_range=STRIKE_RANGE,
        ivr_threshold=IVR_ENTER,
        ivr_window=IVR_WINDOW,
        ivr_min_readings=IVR_MIN_READINGS,
        request_counts=counts["by_kind"],
        weeks_skipped=counts["weeks_skipped"],
        requests=len(reqs),
    )
    print(json.dumps({"requests": len(reqs), **counts, "start": card["start"]}))
    return 0


def declared_config_problems(config: dict) -> list[str]:
    """Where the loaded calendars config differs from the one the declaration read."""
    have = config.get("defaults") or {}
    out = [f"{k}: config {have.get(k)!r}, declared {v!r}" for k, v in DECLARED.items() if have.get(k, v) != v]
    if config.get("tastytrade_costs"):
        out.append(f"tastytrade_costs overridden: {config['tastytrade_costs']}")
    return out


def load_week_inputs(d: Path):
    card = read_json(d / "runcard.json")
    return card, date.fromisoformat(card["start"])


def replay_all(
    chains: Chains, first: date, divs: dict, close_of, vix: dict[str, float] | None = None
) -> list[dict]:
    """Every week from `first` to the last: the records the verdict reads."""
    warm = first - timedelta(weeks=WARMUP_WEEKS)
    weeks = weeks_between(warm, LAST_WEEK)
    fridays = [w["friday"] for w in weeks]
    readings = [reading_at(chains, f) for f in fridays]
    vix = vix or {}
    public = [vix.get(f.isoformat()) for f in fridays]
    index = {f: i for i, f in enumerate(fridays)}
    ctx = {
        "close_of": close_of,
        "rank_of": lambda f: trailing_rank(readings, index[f]),
        "public_rank_of": lambda f: trailing_rank(public, index[f]),
        "prior_control": {},
    }
    records = []
    for w in weeks:
        if w["entry"] < first:
            continue
        st = week_status(w, divs)
        rec = (
            replay_week(chains, w, st, ctx)
            if in_play(st)
            else {
                "week_of": w["week_of"].isoformat(),
                "entry": w["entry"].isoformat(),
                "year": w["entry"].year,
                "status": st,
                "arms": {"control": {"skipped": st["monday"]}, "friday:control": {"skipped": st["friday"]}},
                "sens": {},
            }
        )
        rec["window"] = window_for(w["entry"], first)
        records.append(rec)
    return records


def exit_ctx(closes: dict) -> dict:
    """A replay context with no ranks and no prior weeks: one Monday week's control exit."""
    return {
        "close_of": lambda d: closes.get(d.isoformat() if hasattr(d, "isoformat") else d),
        "rank_of": lambda f: None,
        "public_rank_of": lambda f: None,
        "prior_control": {},
    }


def rule_strikes(snap: dict, spot: float, em: float) -> dict[str, float] | None:
    """The module's strike choice (`engine.plan_entry`'s: the strike quoted in both expiries nearest
    spot less and plus the expected move) on a given spot and move, over the snapshot's listing."""
    out = {}
    for side, target in (("put", spot - em), ("call", spot + em)):
        shared = set(engine._quoted_strikes(snap["front"], snap["quotes"], side)) & set(
            engine._quoted_strikes(snap["back"], snap["quotes"], side)
        )
        if not shared:
            return None
        out[side] = min(shared, key=lambda s: abs(s - target))
    return out


def rule_check(snap: dict | None, sides: dict[str, dict]) -> bool:
    """Paper's own entry spot and expected move, through the module's choice on the store's listing,
    give paper's strikes on both sides."""
    first = next(iter(sides.values()))
    if snap is None or first.get("entry_spot") is None or first.get("entry_em") is None:
        return False
    got = rule_strikes(snap, float(first["entry_spot"]), float(first["entry_em"]))
    return got is not None and all(got[s] == sides[s]["strike"] for s in sides)


def feed_gap(snap: dict | None, sides: dict[str, dict]) -> float | None:
    """|store spot - paper's entry spot| at paper's bar, or None when either is missing."""
    spot = next(iter(sides.values())).get("entry_spot")
    if snap is None or spot is None:
        return None
    return round(abs(snap["spot"] - float(spot)), 4)


def cmd_validate(args) -> int:
    """The replay's entries against the paper ledger's, on the overlapping weeks, Monday and Friday
    separately, each on at least 75% of weeks, else the study stops:
    - the rule: paper's own entry spot and expected move, through the module's strike choice on the
      store's listing at paper's bar, give paper's strikes;
    - the feed: the store's spot at paper's bar is within FEED_SPOT_TOL of paper's.
    The full agreement (the replay's own strikes and each debit within $0.10), exits and settlement
    are compared and reported, not gated."""
    from cherrypick.calendars import db as cdb

    d = out_dir(args.out_dir)
    card, first = load_week_inputs(d)
    divs = load_divs(d, first)
    conn = _ro(store_path())
    chains = Chains(conn)
    paper = cdb.connect_ro(args.paper_db)
    rows = [
        dict(r)
        for r in paper.execute(
            "SELECT * FROM dc_positions WHERE arm IN ('control', 'friday:control') AND week_of >= ? ORDER BY week_of",  # noqa: E501
            (PAPER_FROM.isoformat(),),
        )
    ]
    result = {r: {"weeks": 0, "agree": 0, "rule": 0, "feed": 0} for r in ("monday", "friday")}
    result["detail"] = []
    closes = load_closes()[0]
    for w in weeks_between(PAPER_FROM, LAST_WEEK):
        st = week_status(w, divs)
        for regime, arm, day in (
            ("monday", "control", w["entry"]),
            ("friday", "friday:control", w["friday"]),
        ):
            sides = {
                r["side"]: r for r in rows if r["arm"] == arm and r["week_of"] == w["week_of"].isoformat()
            }
            if st[regime] is not None or len(sides) != 2:
                continue
            bars = MON_BARS if regime == "monday" else FRI_BARS
            got = attempt_entry(chains, day, w["front"], w["back"], bars)
            near = snap = None
            et = (next(iter(sides.values())).get("entry_time") or "")[11:16]
            if et:
                bar = min(bars, key=lambda b: abs(_min(b) - _min(et)))
                near = attempt_entry(chains, day, w["front"], w["back"], bars, only=bar)
                snap = snapshot(
                    chains, day, w["front"], w["back"], bar, window_pct=DECLARED["strike_window_pct"]
                )
            result[regime]["weeks"] += 1
            ok = bool(got["ok"]) and all(
                got["plan"]["sides"][s]["strike"] == sides[s]["strike"]
                and abs(got["plan"]["sides"][s]["debit"] - sides[s]["entry_debit"]) <= 0.10
                for s in sides
            )
            rule = rule_check(snap, sides)
            gap = feed_gap(snap, sides)
            feed = gap is not None and gap <= FEED_SPOT_TOL
            result[regime]["agree"] += ok
            result[regime]["rule"] += rule
            result[regime]["feed"] += feed
            row = {
                "week": w["week_of"].isoformat(),
                "regime": regime,
                "ok": ok,
                "rule": rule,
                "feed_gap": gap,
                "nearest_bar_ok": bool(
                    near
                    and near["ok"]
                    and all(near["plan"]["sides"][s]["strike"] == sides[s]["strike"] for s in sides)
                ),
            }
            if regime == "monday" and ok:
                # Reported, not gated: the replay's control net beside the paper's, week by week. Nothing
                # in the replay reads it.
                replayed = replay_week(chains, w, st, exit_ctx(closes)).get("arms", {}).get("control", {})
                done = all(r.get("status") == "closed" for r in sides.values())
                row["exit"] = {
                    "replay_net": replayed.get("net"),
                    "paper_net": round(
                        sum((r["gross_pnl"] or 0) - (r["fees"] or 0) for r in sides.values()), 2
                    )
                    if done
                    else None,
                }
            result["detail"].append(row)
    for regime in ("monday", "friday"):
        r, n = result[regime], result[regime]["weeks"]
        r["share"] = round(r["agree"] / n, 4) if n else None
        r["passed"] = n > 0 and r["rule"] / n >= VALIDATE_SHARE and r["feed"] / n >= VALIDATE_SHARE
    result["passed"] = result["monday"]["passed"] and result["friday"]["passed"]
    result["rule"] = (
        f"on at least {VALIDATE_SHARE:.0%} of overlapping weeks, Monday and Friday separately: "
        "paper's spot and expected move give paper's strikes, and the store's spot is within "
        f"${FEED_SPOT_TOL:.2f} of paper's; the replay's own strikes and debits are reported"
    )
    write_json(d / "validation.json", result)
    print(json.dumps({k: result[k] for k in ("monday", "friday", "passed")}))
    return 0 if result["passed"] else 1


def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def study_result(chains: Chains, first: date, divs: dict, close_of, vix: dict[str, float]) -> dict:
    records = replay_all(chains, first, divs, close_of, vix)
    judged = judge_all(records, first)
    windows = WINDOWS_LATE if first > LATE_START else WINDOWS_EARLY
    design = f"{first}..{windows['design_end']}"
    return {
        "verdict": judged["verdict"],
        "window": f"{design}; holdout {windows['holdout_start']}..{HOLDOUT_END}",
        "run_id": f"item38-{date.today().isoformat()}",
        "halves_at": judged["halves_at"],
        "why": judged["why"],
        "reading": judged["reading"],
        "arms": {a: {k: v for k, v in j.items() if k != "arm"} for a, j in judged["judged"].items()},
        "summary": summarize(records),
        "weeks": records,
    }


def cmd_study(args) -> int:
    d = out_dir(args.out_dir)
    val = d / "validation.json"
    if not val.exists() or not read_json(val).get("passed"):
        raise SystemExit("the replay's entries have not been validated against paper: run validate first")
    from cherrypick.calendars import cli as ccli

    problems = declared_config_problems(ccli.load_config())
    if problems:
        raise SystemExit("the calendars config is not the one declared: " + "; ".join(problems))
    card, first = load_week_inputs(d)
    closes, cross = load_closes()
    conn = _ro(store_path())
    close_of = exit_ctx(closes)["close_of"]  # the replay asks by date; the files are keyed by ISO text
    result = study_result(Chains(conn), first, load_divs(d, first), close_of, vix_series())
    result["settlement_close_check"] = cross
    path = Path(args.json) if args.json else d / "results.json"
    write_json(path, result)
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "file": str(path)}))
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.

CHECK_LABELS = {
    "enough_weeks": "Enough design weeks",
    "holm_significant": "Mean above zero (Holm across four)",
    "lower_bound_above_zero": "95% lower bound above zero",
    "positive_each_half": "Positive in each half",
    "positive_at_1_5x_costs": "Positive at 1.5 times the costs",
    "positive_without_five_worst": "Positive without the five worst weeks",
    "positive_without_five_best": "Positive without the five best weeks",
    "above_comparator": "Above its comparator",
}


def _d(x) -> str:
    return "" if x is None else f"{x:,.2f}"


def report(result: dict) -> str:
    word = {a: v for a, v in result["verdict"].items()}
    lines = [
        "# Item 38: the calendars module's own rule at real quotes",
        "",
        "**Verdicts:** " + "; ".join(f"{a} {v}" for a, v in word.items()) + ". "
        "The weekly SPY double calendar at about 0.85 expected moves, shorts on the week's last trading day "
        "and longs on the following Monday, replayed at stored quotes through the module's own entry and "
        "exit code with the paper ledger's costs. Dollars are net per trade (one week, both sides, one lot "
        "each). Fills are mid plus 12.5% of the quoted spread (capped at 15% of mid), as in paper.",
        "",
        "## The judged checks (design window)",
        "",
        "| arm | " + " | ".join(CHECK_LABELS.values()) + " | verdict |",
        "|---|" + "---|" * (len(CHECK_LABELS) + 1),
    ]
    for arm, j in result["arms"].items():
        row = [("pass" if j["checks"].get(k) else "fail") if k in j["checks"] else "" for k in CHECK_LABELS]
        lines.append(f"| {arm} | " + " | ".join(row) + f" | {result['verdict'][arm]} |")
    lines += ["", "## What it says", ""]
    for arm, j in result["arms"].items():
        d, h = j["design"], j["holdout"]
        lines.append(
            f"- **{arm}:** design {d['n']} weeks, mean {_d(d['mean'])} a trade"
            + (f" (95% interval {_d(d['boot']['lo'])} to {_d(d['boot']['hi'])})" if d["boot"] else "")
            + f"; holdout {h['n']} weeks, mean {_d(h['mean'])}. "
            + (
                f"Fails on: {', '.join(result['why'][arm])}."
                if result["why"][arm] and result["verdict"][arm] != "pass"
                else ""
            )
            + (
                f" Smallest mean the design could see: {j['mde']['at_bar']} at the Holm bar."
                if j.get("mde")
                else ""
            )
        )
    for line in result["reading"]:
        lines.append(f"- **Partial reading:** {line}.")
    s = result["summary"]
    lines += [
        "",
        "## Columns that add up (mean per trade, dollars)",
        "",
        "| arm | gross | entry fees | entry slippage | exit fees | exit slippage | settlement | net | debit | net / debit |",  # noqa: E501
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, c in s["columns"].items():
        if c:
            lines.append(
                f"| {arm} | "
                + " | ".join(
                    _d(c[k])
                    for k in (
                        "gross",
                        "entry_fees",
                        "entry_slippage",
                        "exit_fees",
                        "exit_slippage",
                        "settlement",
                        "net",
                        "debit",
                    )
                )
                + f" | {c['net_share_of_debit']:.4f} |"
            )
    lines += [
        "",
        "## Net on other fill bases (mean per trade)",
        "",
        "| arm | paper basis | 1.5x costs | halfway to natural | natural side | mid, no slippage |",
        "|---|---|---|---|---|---|",
    ]
    for arm, c in s["costs"].items():
        if c:
            lines.append(
                f"| {arm} | "
                + " | ".join(
                    _d(c.get(k)) for k in ("net", "net_stress15", "net_half", "net_natural", "net_mid")
                )
                + " |"
            )
    lines += ["", "## Net by year (mean, weeks)", ""]
    years = sorted({y for a in s["by_year"].values() for y in a})
    lines += ["| arm | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    for arm, by in s["by_year"].items():
        if by:
            lines.append(
                f"| {arm} | "
                + " | ".join(f"{_d(by[y]['mean'])} ({by[y]['n']})" if y in by else "" for y in years)
                + " |"
            )
    lines += ["", "## Reported, not judged", ""]
    fc = s["friday_vs_control"]
    lines.append(
        f"- **Friday against control** on the {fc['common_weeks']} weeks both traded: mean difference "
        f"{_d(fc['mean_diff'])}. Paired-debit read ({fc['paired_debit_weeks']} weeks): the Friday entrance "
        f"paid {_d(fc['mean_friday_less_monday_moment'])} more than the same strikes cost at the Monday bar."
    )
    lines.append(
        "- **Entry and exit bars** (mean net of the same weeks): "
        + (", ".join(f"{k} {_d(v['mean'])} ({v['n']})" for k, v in s["sensitivities"].items()) or "none")
        + "."
    )
    ia = s["ivr_agreement"]
    lines.append(
        f"- **The public index's rank beside the vendor's:** {ia['agree']} of {ia['weeks']} weeks agree on entering; "  # noqa: E501
        f"vendor enters {ia['vendor_enters']}, index {ia['public_enters']}. Low-rank weeks by year: {s['low_rank_weeks_by_year']}."  # noqa: E501
    )
    lines.append(f"- **Back-leg spread by year** (share of mid): {s['back_leg_spread_by_year']}.")
    lines.append(
        "- **Leave one year out** (the worst two): "
        + "; ".join(f"{a} {x}" for a, x in s["leave_one_year_out"].items())
        + "."
    )
    lines.append(
        "- **Tails:** "
        + "; ".join(
            f"{a} win rate {t['win_rate']}, worst five {t['worst']}" for a, t in s["tails"].items() if t
        )
        + "."
    )
    lines += ["", "## Refusals, fall-throughs and set-aside weeks (by arm, reason and year)", ""]
    for arm, c in s["refusals"].items():
        lines.append(f"- **{arm}:** " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    lines += [
        "",
        f"The halves split at {result['halves_at']}. Settlement closes were checked against a second source: "
        f"{result.get('settlement_close_check')}.",
        "",
    ]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(read_json(src)), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
        ("exdates", cmd_exdates, "SPY ex-dividend dates from the broker's dividend file"),
        ("listing", cmd_listing, "the listing probes, then S from the store"),
        ("pilot", cmd_pilot, "the 40-week pilot's requests, then its coverage and basis"),
        ("requests", cmd_requests, "the full request file and the run card"),
        ("validate", cmd_validate, "the replay's entries against the paper ledger's"),
        ("study", cmd_study, "the verdict per arm"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--out-dir")
        if name == "validate":
            sp.add_argument("--paper-db")
        if name == "study":
            sp.add_argument("--json")
        sp.set_defaults(fn=fn)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--json")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
