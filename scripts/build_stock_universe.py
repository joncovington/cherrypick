"""Build the market report's stock universe: candidates from the vendor's editions and the tastylive
follow feed, kept only when tastytrade's own quotes show them to be very liquid.

**Why this exists.** `docs/market-report-plan.md` runs its breadth and stage engines over a stock
universe, and the vendor's is neither the S&P 500 nor an option-volume screen (the plan's gap 3).
The two places names come from are the ones a person actually watches: every ticker the vendor's
editions link, and every underlying the tastylive traders put on. A name earns its place only by
trading tightly, measured, not by being mentioned.

A script rather than package code, for the same reason as the vendor collector: it reaches the
network. It writes only its own folder, `~/.cherrypick/data/market-report/universe/`, and is
read-only against the broker apart from the `watchlist` step: it asks for instruments, metrics,
chains and quotes, and places nothing. It uses REST snapshots, never the streamer: several hundred
names would blow the stream budget (the plan's "The streamer cannot carry a stock universe"), and
the stream cache has one writer.

Three steps, each its own subcommand and its own schedule:

- **harvest** (after the close): every symbol linked in the saved vendor editions, plus the follow
  feed. The feed returns only its 50 newest orders, about a day's worth, so it is asked once per
  trader (about a week each) and the orders are kept, merged by id, so the history grows. Futures
  are dropped here; indexes are dropped by tastytrade, which does not list them as equities.
  It also lands OCC's daily option volume by underlying for each recent session not yet stored,
  through the morning pack's fetcher (`scripts/fetch_market_files.py`, the one place OCC is
  fetched) into `market-files/occ/`, and reads contracts per underlying from there
  (`cherrypick.overview.occ.contracts_by_session`). Until 2026-10-01 it kept its own copy,
  totals only, in `universe/occ-volume/`; the two agreed on every underlying of all 13 sessions
  both held.
- **measure** (twice a session, inside regular hours only): for each candidate, tastytrade's
  liquidity rating (recorded as a guide), the stock's bid/ask, and the bid/ask of its at-the-money
  call and put on the standard monthly expiry nearest 30 days out (`monthly_expiry`). Never a
  weekly: weeklies quote wider than the monthly, so their spread is not the name's (the user's
  direction, 2026-10-04). Until then it took whichever expiry was nearest 30 days, which was usually a
  weekly (30 October for 188 of 190 names on 2026-10-02). Every listed candidate's options are
  quoted, including names whose option volume already rules them out of the universe, because the
  same spread is to decide the setups watchlist's options-tradable label, which admits names under
  this volume bar (LOW and ABT, at about 7,600 contracts a day). A quote not stamped inside that
  day's regular session is discarded rather than measured — a weekend snapshot of NMR read
  9.55/10.98, the overnight book, not the market.
- **build**: a pure function over every saved measurement and OCC session. A name is **in** only
  when its stock spread, option spread and option volume all hold on medians over at least
  `MIN_SESSIONS` sessions; fewer sessions is **pending**, never a pass. Every name gets its
  reasons, in or out, with tastytrade's rating beside them as a guide.
  It also rebuilds `sectors.json`, the vendor's own sectors read from every edition's
  leaders/laggards table under the Sept 25 labels, with `sectors.manual.json` for names no edition
  has listed yet, and warns when the vendor renames a sector or moves a name.
- **watchlist**: mirrors the members to a private tastytrade watchlist, `cherrypick universe`. It
  is the one step that writes to the account — a watchlist, never an order — so it shows its plan
  and writes nothing without `--apply`, has its own schedule switch, and only ever replaces the
  list it created (marked by its group). SPX, NDX, SPY, QQQ and IWM are pinned: on the list from
  the first sync and never removed. It refuses to strip the list to those or to cut more than half
  of the universe's names in one sync.

Pacing: the follow feed gets one request per trader and OCC one per missing session, 5-10
seconds apart (OCC's pacing is the shared fetcher's); tastytrade gets batched calls and one chain
request per name a day (cached), a second apart. A throttling response ends the step with what it
has.

    python scripts/build_stock_universe.py harvest [--no-follow]
    python scripts/build_stock_universe.py measure [--force] [--limit N]
    python scripts/build_stock_universe.py build
    python scripts/build_stock_universe.py daily        # harvest, then build
    python scripts/build_stock_universe.py watchlist [--apply] [--allow-shrink]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# The rule. "Very liquid, tight bid/ask" (the strict bar, chosen 2026-09-27), decided by what is
# measured: spreads and option volume. tastytrade's rating is a guide only (see `judge`). A spread
# passes on EITHER its percentage of mid OR its absolute width, because a one-cent-wide $10 stock is
# 0.1% wide and cannot get tighter: the absolute leg stops a price-level artefact failing a liquid
# name.
RULE = {
    "guide_liquidity_rating": 4,  # below this is noted beside the verdict, never decides it
    "min_option_volume": 10_000,  # contracts a day, median of the recent OCC sessions
    "stock_max_spread_pct": 0.0005,  # 0.05% of mid
    "stock_max_spread_abs": 0.01,  # or one tick
    "option_max_spread_pct": 0.03,  # 3% of mid, worse of the ATM call and put
    "option_max_spread_abs": 0.05,  # or five cents
    "option_expiry": "standard monthly",  # third Friday; never a weekly (see `monthly_expiry`)
    "option_target_dte": 30,
    "option_dte_range": (14, 60),  # 46 days always holds a monthly: they are at most 35 apart
    "min_sessions": 3,
    "lookback_sessions": 10,
}
MIN_SESSIONS = RULE["min_sessions"]

# Measuring window, ET: from 10:00 to half an hour before the close (15:30 on a regular day).
# Spreads around the open and the closing auction are not the market's resting width.
WINDOW_START = "10:00"
SESSION_OPEN = "09:30"

# Pacing.
FOLLOW_PAUSE_RANGE_S = (5.0, 10.0)
TT_PAUSE_S = 1.0
TT_BATCH = 50  # instruments and metrics per call
TT_QUOTE_BATCH = 100  # the market-data endpoint's own limit

FOLLOW_BASE = "https://follow.tastylive.com"
FOLLOW_UA = "cherrypick-universe/1.0"
_TICKER_RE = re.compile(r"[?&]symbol=([A-Z][A-Z.]{0,6})\b")


# ------------------------------------------------------------------------------------------------
# Pure functions: candidates, readings, the rule. No network, no clock, no store.


def edition_candidates(editions: dict[str, str]) -> dict[str, dict]:
    """{symbol: {first, last, editions}} over every ticker the editions link, the breadth table
    included — that table is the vendor's universe itself. `editions` maps ISO date -> HTML."""
    out: dict[str, dict] = {}
    for day in sorted(editions):
        for sym in set(_TICKER_RE.findall(editions[day])):
            row = out.setdefault(sym, {"first": day, "last": day, "editions": 0})
            row["last"] = day
            row["editions"] += 1
    return out


def merge_orders(kept: dict[str, dict], fetched: list[dict]) -> int:
    """Add fetched follow-feed orders to `kept` (keyed by order id); returns how many were new."""
    new = 0
    for order in fetched:
        oid = order.get("id") if isinstance(order, dict) else None
        if oid is None:
            continue
        if str(oid) not in kept:
            new += 1
        kept[str(oid)] = order
    return new


def follow_candidates(orders: dict[str, dict], trader_names: dict[str, str]) -> dict[str, dict]:
    """{symbol: {first, last, orders, traders}} over the orders' underlyings. Futures (a slash
    prefix) are not stocks and never enter."""
    out: dict[str, dict] = {}
    for order in orders.values():
        when = str(order.get("filled_at") or order.get("placed_at") or "")[:10]
        trader = trader_names.get(str(order.get("trader_id")), str(order.get("trader_id")))
        syms = {str(leg.get("underlying_symbol") or "") for leg in order.get("order_legs") or []}
        for sym in sorted(s for s in syms if s and not s.startswith("/")):
            row = out.setdefault(sym, {"first": when, "last": when, "orders": 0, "traders": []})
            row["first"] = min(row["first"], when) if when else row["first"]
            row["last"] = max(row["last"], when)
            row["orders"] += 1
            if trader not in row["traders"]:
                row["traders"].append(trader)
    return out


def to_tastytrade(symbol: str) -> str:
    """The vendor writes share classes with a dot (BRK.B); tastytrade with a slash (BRK/B)."""
    return symbol.replace(".", "/")


def in_window(now_et: datetime, trading_day: bool, close_hhmm: str = "16:00") -> bool:
    """Inside the measuring window of a trading session. On an early close the window ends half an
    hour before that close, as it does before the regular one."""
    if not trading_day:
        return False
    sh, sm = (int(x) for x in WINDOW_START.split(":"))
    start = now_et.replace(hour=sh, minute=sm, second=0, microsecond=0)
    hh, mm = (int(x) for x in close_hhmm.split(":"))
    end = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(minutes=30)
    return start <= now_et <= end


def spread(bid, ask) -> tuple[float, float] | None:
    """(absolute width, width as a fraction of mid), or None for a quote that isn't two-sided."""
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a < b:
        return None
    return a - b, (a - b) / ((a + b) / 2)


def quote_fresh(quote: dict, measured_at: datetime) -> bool:
    """A quote belongs to the session it was fetched in: stamped at or after that day's open and
    not after the fetch. An overnight or weekend book fails; a resting quote that simply hasn't
    changed since mid-morning does not, because it is still the book. Judged against the quote's
    own `fetched_at` where recorded, since option quotes are fetched minutes after the run starts."""
    try:
        stamp = datetime.fromisoformat(quote["updated_at"])
        fetched = datetime.fromisoformat(quote["fetched_at"]) if quote.get("fetched_at") else measured_at
    except (KeyError, TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    fetched_et = fetched.astimezone(ET)
    oh, om = (int(x) for x in SESSION_OPEN.split(":"))
    opened = fetched_et.replace(hour=oh, minute=om, second=0, microsecond=0)
    return opened <= stamp <= fetched + timedelta(minutes=1)


def spread_score(width: float, pct: float, max_pct: float, max_abs: float) -> float:
    """How far a spread is from the bar, on either leg of it: 1.0 is exactly at the bar, below 1
    passes. Taking the smaller ratio is what "percentage OR absolute" means."""
    return min(pct / max_pct, width / max_abs)


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly options expiry: the third Friday, or the trading day before it when that
    Friday is a market holiday (Good Friday on 2025-04-18, Juneteenth observed on 2027-06-18).
    Weeklies, end-of-month and quarterly expiries are not it."""
    from cherrypick.core import calendar as cal

    third = cal.nth_weekday(year, month, cal.FRI, 3)
    return third if cal.is_trading_day(third) else cal.previous_trading_day(third)


def is_monthly(expiration: str | None) -> bool:
    """Whether an ISO expiration date is its month's standard monthly expiry."""
    try:
        d = date.fromisoformat(str(expiration))
    except ValueError:
        return False
    return d == monthly_expiry(d.year, d.month)


def choose_options(expirations: list[dict], spot: float, rule: dict = RULE) -> dict | None:
    """The at-the-money call and put of the standard monthly expiry nearest the target DTE, inside
    the range; None when the range holds no monthly. `expirations` is the cached chain:
    [{expiration, dte, strikes: [[strike, call, put], ...]}]."""
    lo, hi = rule["option_dte_range"]
    usable = [e for e in expirations if lo <= e["dte"] <= hi and e["strikes"] and is_monthly(e["expiration"])]
    if not usable or not spot:
        return None
    exp = min(usable, key=lambda e: (abs(e["dte"] - rule["option_target_dte"]), e["dte"]))
    strike, call, put = min(exp["strikes"], key=lambda s: abs(float(s[0]) - spot))
    return {
        "expiration": exp["expiration"],
        "dte": exp["dte"],
        "strike": float(strike),
        "call": call,
        "put": put,
    }


def reading(row: dict, measured_at: datetime, rule: dict = RULE) -> dict:
    """One name's reading from one measurement: the stock and option spread scores, or why it has
    none. Stale or one-sided quotes give no score, never a guessed one."""
    out: dict = {"rating": row.get("liquidity_rating"), "stock": None, "option": None, "notes": []}
    sq = row.get("quote") or {}
    s = spread(sq.get("bid"), sq.get("ask")) if quote_fresh(sq, measured_at) else None
    if s:
        out["stock"] = {
            "width": s[0],
            "pct": s[1],
            "score": spread_score(s[0], s[1], rule["stock_max_spread_pct"], rule["stock_max_spread_abs"]),
        }
    else:
        out["notes"].append("stock quote stale or one-sided")
    legs = []
    for leg in ("call", "put"):
        q = (row.get("options") or {}).get(leg) or {}
        o = spread(q.get("bid"), q.get("ask")) if quote_fresh(q, measured_at) else None
        if o is None:
            legs = None
            break
        legs.append(
            {
                "width": o[0],
                "pct": o[1],
                "score": spread_score(
                    o[0], o[1], rule["option_max_spread_pct"], rule["option_max_spread_abs"]
                ),
            }
        )
    if legs:
        out["option"] = {
            **max(legs, key=lambda x: x["score"]),  # the worse leg decides
            "monthly": is_monthly((row.get("options") or {}).get("expiration")),
        }
    elif row.get("options"):
        out["notes"].append("option quote stale or one-sided")
    return out


def to_occ(symbol: str) -> str:
    """OCC writes a share class with no separator: BRK.B is `BRKB`."""
    return symbol.replace(".", "").replace("/", "")


def judge(
    name: dict, sessions: dict[str, list[dict]], volumes: list[int], rule: dict = RULE
) -> tuple[str, list[str], dict]:
    """(status, reasons, medians) for one name. `name` is its instrument facts ({listed, is_etf,
    is_illiquid, rating}); `sessions` maps ISO session date -> that session's readings; `volumes`
    is its daily option contract volume over the recent OCC sessions, oldest first.

    Status is `in`, `out` or `pending`. Only a name tastytrade does not list is `out` at once.
    tastytrade's liquidity rating and illiquid flag are **guides, never gates** (decided
    2026-09-27): on 2026-09-25 DELL (266k contracts), COST (121k) and ARM (113k) rated 2 while LYG
    rated 4 on 22 contracts. What decides is measured: the stock spread, the at-the-money option
    spread on the standard monthly expiry, and option volume, each on a median over at least
    `min_sessions` sessions, and until each has that many the name is `pending`."""
    if not name.get("listed"):
        return "out", ["not a listed equity on tastytrade (an index, a future, or a retired symbol)"], {}

    recent = sorted(sessions)[-rule["lookback_sessions"] :]

    def per_session(kind: str, keep=lambda v: True) -> list[dict]:
        out = []
        for day in recent:
            vals = [r[kind] for r in sessions[day] if r.get(kind) and keep(r[kind])]
            if vals:
                out.append({k: statistics.median(v[k] for v in vals) for k in ("score", "pct", "width")})
        return out

    def summary(rows: list[dict]) -> dict:
        return {
            "sessions": len(rows),
            **({k: statistics.median(p[k] for p in rows) for k in ("score", "pct", "width")} if rows else {}),
        }

    # The option spread is the monthly's. The changeover (2026-10-05) is per name: a name with fewer
    # than `min_sessions` sessions on the monthly is judged as before, on the readings taken nearest
    # 30 days out, while it has enough of them; the two are never pooled. Once the older readings
    # leave the lookback, every name is on the monthly and the second branch can no longer be taken.
    monthly = per_session("option", lambda v: v.get("monthly"))
    earlier = per_session("option", lambda v: not v.get("monthly"))
    on_monthly = len(monthly) >= rule["min_sessions"] or len(earlier) < rule["min_sessions"]
    medians: dict = {
        "stock": summary(per_session("stock")),
        "option": {
            **summary(monthly if on_monthly else earlier),
            "expiry": "monthly" if on_monthly else "nearest 30 days",
            "monthly_sessions": len(monthly),
        },
    }
    vols = volumes[-rule["lookback_sessions"] :]
    medians["option_volume"] = {
        "sessions": len(vols),
        **({"contracts": statistics.median(vols)} if vols else {}),
    }

    reasons: list[str] = []
    pending: list[str] = []
    for kind, pct_key, abs_key in (
        ("stock", "stock_max_spread_pct", "stock_max_spread_abs"),
        ("option", "option_max_spread_pct", "option_max_spread_abs"),
    ):
        m = medians[kind]
        # Until a name is on the monthly, its option reasons say which expiry they read.
        note = (
            f" (nearest 30 days; the monthly has {m['monthly_sessions']} of {rule['min_sessions']} sessions)"
            if m.get("expiry") == "nearest 30 days"
            else ""
        )
        if m["sessions"] < rule["min_sessions"]:
            where = " on the monthly" if m.get("expiry") == "monthly" else ""
            pending.append(
                f"{kind} spread measured in {m['sessions']} of {rule['min_sessions']} sessions{where}"
            )
        elif m["score"] > 1.0:
            reasons.append(
                f"{kind} spread {m['pct']:.2%} / ${m['width']:.2f} wider than "
                f"{rule[pct_key]:.2%} or ${rule[abs_key]:.2f}{note}"
            )
    v = medians["option_volume"]
    minimum = rule["min_option_volume"]
    if v["sessions"] < rule["min_sessions"]:
        pending.append(f"option volume known for {v['sessions']} of {rule['min_sessions']} sessions")
    elif v["contracts"] < minimum:
        reasons.append(f"option volume {v['contracts']:,.0f} contracts/day < {minimum:,}")
    if reasons:
        return "out", reasons + pending, medians
    if pending:
        return "pending", pending, medians
    return "in", ["stock spread, option spread and option volume all within the rule"], medians


def volume_series(sym: str, volumes: dict[str, dict[str, int]]) -> list[int]:
    """A name's daily option contracts over the stored OCC sessions, oldest first. A session's file
    that lacks the name means no options traded in it, which is a zero, not a gap."""
    return [volumes[d].get(to_occ(sym), 0) for d in sorted(volumes)]


def volume_too_thin(vols: list[int], rule: dict = RULE) -> bool:
    """True only when enough sessions are known AND their median is under the bar — the one fact
    that settles a name before any quote is taken."""
    recent = vols[-rule["lookback_sessions"] :]
    return len(recent) >= rule["min_sessions"] and statistics.median(recent) < rule["min_option_volume"]


def guides(name: dict, rule: dict = RULE) -> list[str]:
    """tastytrade's own view, recorded beside the verdict and never deciding it."""
    out = []
    if name.get("rating") is not None and name["rating"] < rule["guide_liquidity_rating"]:
        out.append(f"tastytrade liquidity rating {name['rating']} of 4")
    if name.get("is_illiquid"):
        out.append("tastytrade flags it illiquid")
    return out


def build_universe(
    candidates: dict[str, dict],
    measurements: list[dict],
    volumes: dict[str, dict[str, int]] | None = None,
    rule: dict = RULE,
    illiquid: dict[str, dict] | None = None,
) -> dict:
    """The universe document from the candidate list, every saved measurement, and the OCC volume
    by session (`{ISO date: {OCC underlying: contracts}}`). Pure: the same files give the same
    universe, so it can be rebuilt after any rule change.

    `illiquid` is the liquidity verdict's illiquid names with their verdicts (packages/technicals,
    `liquidity.py`): they are `out` with its reasons, whatever their readings say. They are no longer
    measured between sweeps (decided 2026-10-07), so left to the readings they would drift to
    `pending` as their last readings age out of the lookback."""
    volumes = volumes or {}
    illiquid = illiquid or {}
    facts: dict[str, dict] = {}
    sessions: dict[str, dict[str, list[dict]]] = {}
    for m in sorted(measurements, key=lambda m: m["measured_at"]):
        at = datetime.fromisoformat(m["measured_at"])
        day = at.astimezone(ET).date().isoformat()
        for sym, row in (m.get("names") or {}).items():
            facts[sym] = {
                "listed": row.get("listed", False),
                "is_etf": row.get("is_etf"),
                "is_illiquid": row.get("is_illiquid"),
                "rating": row.get("liquidity_rating"),
            }
            if row.get("listed"):
                sessions.setdefault(sym, {}).setdefault(day, []).append(reading(row, at, rule))
    days = sorted(volumes)
    names: dict[str, dict] = {}
    for sym in sorted(candidates):
        vols = volume_series(sym, volumes)
        if sym in illiquid:
            v = illiquid[sym]
            names[sym] = {
                "status": "out",
                "reasons": [f"judged illiquid on {v.get('judged_on')}: " + "; ".join(v.get("reasons") or [])],
                "sources": candidates[sym],
            }
            continue
        if sym not in facts:
            if volume_too_thin(vols, rule):
                median = statistics.median(vols[-rule["lookback_sessions"] :])
                reasons = [f"option volume {median:,.0f} contracts/day < {rule['min_option_volume']:,}"]
                names[sym] = {"status": "out", "reasons": reasons, "sources": candidates[sym]}
            else:
                names[sym] = {
                    "status": "pending",
                    "reasons": ["not measured yet"],
                    "sources": candidates[sym],
                }
            continue
        status, reasons, medians = judge(facts[sym], sessions.get(sym, {}), vols, rule)
        names[sym] = {
            "status": status,
            "kind": "etf" if facts[sym].get("is_etf") else "stock",
            "rating": facts[sym].get("rating"),
            "reasons": reasons,
            "guides": guides(facts[sym], rule),
            "medians": medians,
            "sources": candidates[sym],
        }
    members = [s for s, n in names.items() if n["status"] == "in"]
    return {
        "rule": {k: list(v) if isinstance(v, tuple) else v for k, v in rule.items()},
        "volume_sessions": days[-rule["lookback_sessions"] :],
        "counts": {
            st: sum(1 for n in names.values() if n["status"] == st) for st in ("in", "out", "pending")
        },
        "members": members,
        "stocks": [s for s in members if names[s]["kind"] == "stock"],
        "etfs": [s for s in members if names[s]["kind"] == "etf"],
        "names": names,
    }


# ------------------------------------------------------------------------------------------------
# The tastytrade watchlist: the universe mirrored where a person trades from. Pure planning here;
# the write is `cmd_watchlist`.

WATCHLIST_NAME = "cherrypick universe"
# The mark that says this script made the list. A replace sends the whole entry list and drops
# everything left out, so a same-named list a person made by hand would be wiped; without this
# group the script refuses to touch it.
WATCHLIST_GROUP = "cherrypick"
# A sync that would remove more than this share of the list in one go is refused unless told
# otherwise: a universe that collapses overnight is far likelier a broken build (a missing OCC file,
# a failed measurement) than a market in which half the names stopped trading.
MAX_SHRINK = 0.5


# The major index symbols, pinned to the list for good (decided 2026-09-27): they are put on first,
# kept whether or not they pass the universe's rule, and never removed by a sync. Symbol ->
# tastytrade instrument type; SPX and NDX are cash indexes, not equities.
PINNED = {"SPX": "Index", "NDX": "Index", "SPY": "Equity", "QQQ": "Equity", "IWM": "Equity"}


def watchlist_plan(members: list[str], existing: dict | None, *, allow_shrink: bool = False) -> dict:
    """What a sync would do: `create`, `replace`, `none` or `refuse`, with the symbols it adds and
    removes. `existing` is the account's list of that name ({group_name, symbols}) or None.

    The list is the pinned symbols first, then the universe's members. The pinned ones are in every
    list this sends, so no sync can remove them; the empty and shrink guards count only the
    universe's part, since the pinned part never moves."""
    universe = sorted({to_tastytrade(s) for s in members} - set(PINNED))
    want = [*PINNED, *universe]
    if existing is None:
        return {"action": "create", "add": want, "remove": [], "entries": want}
    if existing.get("group_name") != WATCHLIST_GROUP:
        return {
            "action": "refuse",
            "reason": (
                f"a watchlist named {WATCHLIST_NAME!r} exists without the {WATCHLIST_GROUP!r} group, "
                "so this script did not make it and will not replace it"
            ),
            "add": [],
            "remove": [],
        }
    have = set(existing.get("symbols") or [])
    add, remove = sorted(set(want) - have), sorted(have - set(want))
    assert not set(remove) & set(PINNED), "a pinned symbol must never be removed"
    if not add and not remove:
        return {"action": "none", "add": [], "remove": [], "entries": want}
    have_universe = have - set(PINNED)
    if not universe and have_universe:
        return {
            "action": "refuse",
            "reason": "the universe is empty; not stripping the list to its pinned symbols",
            "add": [],
            "remove": remove,
        }
    if have_universe and not allow_shrink and len(remove) > MAX_SHRINK * len(have_universe):
        return {
            "action": "refuse",
            "reason": (
                f"would remove {len(remove)} of {len(have_universe)} universe names at once "
                f"(more than {MAX_SHRINK:.0%})"
            ),
            "add": add,
            "remove": remove,
        }
    return {"action": "replace", "add": add, "remove": remove, "entries": want}


def watchlist_body(entries: list[str]) -> dict:
    return {
        "name": WATCHLIST_NAME,
        "group-name": WATCHLIST_GROUP,
        "watchlist-entries": [{"symbol": s, "instrument-type": PINNED.get(s, "Equity")} for s in entries],
    }


# ------------------------------------------------------------------------------------------------
# Sectors: the vendor's own, read from the editions (decided 2026-09-27: match the Sept 25 edition,
# and re-evaluate if the vendor changes). Each edition's leaders/laggards table is grouped by
# sector, so every name it lists carries the vendor's sector for that day.

# The Sept 25 labels, the canonical set. That edition is a hybrid: three sectors took GICS names
# while Technology, Healthcare and Basic Materials kept Yahoo/Morningstar's.
SECTORS = (
    "Technology",
    "Healthcare",
    "Communication Services",
    "Consumer Staples",
    "Industrials",
    "Real Estate",
    "Energy",
    "Basic Materials",
    "Consumer Discretionary",
    "Utilities",
    "Financials",
)
# Earlier labels for the same sectors. Across Sept 21-25, 48 names changed label this way and none
# changed sector, so these are renames, not reclassifications.
RELABEL = {
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial Services": "Financials",
}
_SECTOR_CELL_RE = re.compile(r"<td[^>]*>\s*(?:<[^>]+>\s*)*([A-Z][A-Za-z &]+?)\s*<")


def edition_sectors(page_html: str) -> dict[str, str]:
    """{symbol: sector label as printed} from one edition's leaders/laggards table."""
    i = page_html.find(">Sector</th>")
    j = page_html.find("The three shades", i)
    if i < 0 or j < 0:
        return {}
    out: dict[str, str] = {}
    for row in re.findall(r"<tr.*?</tr>", page_html[i:j], re.S):
        m = _SECTOR_CELL_RE.search(row)
        if not m:
            continue
        for sym in _TICKER_RE.findall(row):
            out[sym] = m.group(1)
    return out


def sector_map(editions: dict[str, str], manual: dict[str, str] | None = None) -> dict:
    """The sector file from every edition (ISO date -> HTML), plus hand-set sectors for names no
    edition has listed yet. The latest edition's sector wins; labels are mapped onto the Sept 25 set.

    `changes` is what calls for re-evaluating the decision: a label that is neither canonical nor a
    known rename (the vendor renamed again), or a name whose sector differs between editions after
    mapping (the vendor reclassified). A hand-set sector an edition later contradicts is one too."""
    manual = manual or {}
    by_name: dict[str, dict[str, str]] = {}
    unknown: dict[str, list[str]] = {}
    for day in sorted(editions):
        for sym, label in edition_sectors(editions[day]).items():
            canon = RELABEL.get(label, label)
            if canon not in SECTORS:
                unknown.setdefault(label, []).append(day)
            by_name.setdefault(sym, {})[day] = canon
    sectors: dict[str, dict] = {}
    changes: list[str] = [
        f"new sector label {label!r} (first seen {days[0]}): not in the Sept 25 set or a known rename"
        for label, days in sorted(unknown.items())
    ]
    for sym, days in sorted(by_name.items()):
        latest = days[max(days)]
        sectors[sym] = {"sector": latest, "source": "edition", "last_seen": max(days), "editions": len(days)}
        if len(set(days.values())) > 1:
            history = ", ".join(f"{d} {v}" for d, v in sorted(days.items()))
            changes.append(f"{sym} changed sector between editions: {history}")
        if sym in manual and manual[sym] != latest:
            changes.append(f"{sym} is hand-set to {manual[sym]} but the {max(days)} edition says {latest}")
    for sym, sector in sorted(manual.items()):
        if sym not in sectors:
            sectors[sym] = {"sector": sector, "source": "manual"}
            if sector not in SECTORS:
                changes.append(f"{sym} is hand-set to {sector!r}, not a Sept 25 sector")
    return {"canonical": list(SECTORS), "relabel": RELABEL, "sectors": sectors, "changes": changes}


def merge_candidates(vendor: dict[str, dict], follow: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym, row in vendor.items():
        out.setdefault(sym, {})["vendor"] = row
    for sym, row in follow.items():
        out.setdefault(sym, {})["follow"] = row
    return dict(sorted(out.items()))


# ------------------------------------------------------------------------------------------------
# Store.


def store_dir() -> Path:
    from cherrypick.core import home

    return home.data_dir("market-report") / "universe"


def _write_json(path: Path, body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a file


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _warn(title: str, message: str) -> None:
    print(f"WARNING: {title}\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify(
            "WARNING", "market_report.universe", title, message
        )
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------
# harvest


def _follow_get(path: str, params: list[tuple[str, str]] | None = None):
    url = FOLLOW_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": FOLLOW_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def harvest_follow(kept: dict[str, dict]) -> tuple[dict[str, str], int, list[str]]:
    """Ask the feed once per trader; returns (trader names, new orders, problems). One pass, paced;
    a refusal or error ends the pass with what was gathered."""
    problems: list[str] = []
    try:
        roster = _follow_get("/api/traders").get("traders") or []
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {}, 0, [f"follow roster: {exc}"]
    names = {str(t["id"]): str(t.get("name") or t["id"]) for t in roster if isinstance(t, dict) and "id" in t}
    new = 0
    for name in names.values():
        time.sleep(random.uniform(*FOLLOW_PAUSE_RANGE_S))
        try:
            body = _follow_get("/api/public_orders", [("traders[]", name)])
        except urllib.error.HTTPError as exc:
            problems.append(f"follow orders for {name}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            problems.append(f"follow orders for {name}: {exc}")
            continue
        new += merge_orders(kept, body.get("public_orders") or [])
    return names, new, problems


def load_volumes() -> dict[str, dict[str, int]]:
    """{session: {underlying: contracts}} from the shared OCC store. Imported here, not at the top:
    this script's tests run where only the orchestrator is installed."""
    from cherrypick.overview import occ

    return occ.contracts_by_session()


def harvest_occ() -> tuple[int, list[str]]:
    """Land any recent OCC session the shared store lacks, by the morning pack's own fetcher, so
    the screen never waits on the next market-files run for a session OCC has already published.
    A session OCC has not published yet is left for the next run."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fetch_market_files", Path(__file__).with_name("fetch_market_files.py")
    )
    fmf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fmf)
    report: dict = {"problems": []}
    fmf.fetch_occ(report, datetime.now(ET).date())
    return len(report["occ"]["landed"]), report["problems"]


def cmd_harvest(args) -> int:
    from cherrypick.core import home

    editions_dir = home.data_dir("market-report") / "vendor-editions"
    editions = {p.stem: p.read_text(encoding="utf-8") for p in sorted(editions_dir.glob("????-??-??.html"))}
    vendor = edition_candidates(editions)

    feed_path = store_dir() / "follow-feed.json"
    feed = _read_json(feed_path, {"traders": {}, "orders": {}})
    problems: list[str] = []
    new = 0
    if not args.no_follow:
        names, new, problems = harvest_follow(feed["orders"])
        feed["traders"].update(names)
        feed["harvested_at"] = datetime.now(UTC).isoformat()
        _write_json(feed_path, feed)
    follow = follow_candidates(feed["orders"], feed["traders"])
    occ_landed, occ_problems = harvest_occ()
    problems += occ_problems

    candidates = merge_candidates(vendor, follow)
    _write_json(
        store_dir() / "candidates.json",
        {"harvested_at": datetime.now(UTC).isoformat(), "editions": sorted(editions), "names": candidates},
    )
    print(
        json.dumps(
            {
                "ok": not problems,
                "editions": len(editions),
                "vendor_names": len(vendor),
                "follow_orders": len(feed["orders"]),
                "follow_new": new,
                "follow_names": len(follow),
                "candidates": len(candidates),
                "occ_sessions_landed": occ_landed,
                "occ_sessions_stored": len(load_volumes()),
                "problems": problems,
            },
            indent=1,
        )
    )
    if problems:
        _warn("Universe harvest incomplete", "\n".join(problems))
    return 0


# ------------------------------------------------------------------------------------------------
# measure


def _batches(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


async def _measure(session, symbols: list[str], chain_cache: dict, limit_chains: int | None) -> dict:
    """Instruments, metrics, stock quotes, chains and option quotes for every listed candidate. The
    chains are one call a name a day, a second apart (about ten minutes for the first measurement of
    a day, cached for the second). They are spent on names the universe's volume bar already rules
    out as well, because their monthly spread is still wanted (see the module note)."""
    from tastytrade.instruments import Equity, NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type
    from tastytrade.metrics import get_market_metrics

    tt = {to_tastytrade(s): s for s in symbols}
    names: dict[str, dict] = {s: {"listed": False} for s in symbols}

    for batch in _batches(list(tt), TT_BATCH):
        for e in await Equity.get(session, batch):
            if e.symbol in tt and getattr(e, "active", True):
                names[tt[e.symbol]].update(
                    listed=True, is_etf=bool(e.is_etf), is_illiquid=bool(e.is_illiquid)
                )
        await asyncio.sleep(TT_PAUSE_S)
    listed = [s for s in symbols if names[s]["listed"]]

    for batch in _batches([to_tastytrade(s) for s in listed], TT_BATCH):
        for m in await get_market_metrics(session, batch):
            if m.symbol in tt:
                names[tt[m.symbol]].update(
                    liquidity_rating=m.liquidity_rating,
                    liquidity_rank=float(m.liquidity_rank) if m.liquidity_rank is not None else None,
                )
        await asyncio.sleep(TT_PAUSE_S)

    for batch in _batches([to_tastytrade(s) for s in listed], TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, equities=batch):
            if q.symbol in tt:
                names[tt[q.symbol]]["quote"] = {
                    "bid": float(q.bid) if q.bid is not None else None,
                    "ask": float(q.ask) if q.ask is not None else None,
                    "updated_at": _iso(q.updated_at),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
        await asyncio.sleep(TT_PAUSE_S)

    optionable = listed

    fetched = 0
    lo, hi = RULE["option_dte_range"]
    for sym in optionable:
        if sym in chain_cache:
            continue
        if limit_chains is not None and fetched >= limit_chains:
            break
        try:
            chains = await NestedOptionChain.get(session, to_tastytrade(sym))
        except Exception as exc:  # noqa: BLE001 — a name without a chain is recorded as such
            if "429" in str(exc):
                raise
            chain_cache[sym] = []
            continue
        fetched += 1
        exps = []
        for chain in chains if isinstance(chains, list) else [chains]:
            if chain.root_symbol != to_tastytrade(sym):
                continue  # a non-standard root (an adjusted deliverable) is not the name's chain
            for e in chain.expirations:
                if lo <= e.days_to_expiration <= hi:
                    exps.append(
                        {
                            "expiration": e.expiration_date.isoformat(),
                            "dte": e.days_to_expiration,
                            "strikes": [[float(s.strike_price), s.call, s.put] for s in e.strikes],
                        }
                    )
        chain_cache[sym] = exps
        await asyncio.sleep(TT_PAUSE_S)

    wanted: dict[str, tuple[str, str]] = {}
    for sym in optionable:
        q = names[sym].get("quote") or {}
        s = spread(q.get("bid"), q.get("ask"))
        spot = (q["bid"] + q["ask"]) / 2 if s else None
        pick = choose_options(chain_cache.get(sym) or [], spot) if spot else None
        if pick:
            names[sym]["options"] = {
                "expiration": pick["expiration"],
                "dte": pick["dte"],
                "strike": pick["strike"],
            }
            wanted[pick["call"]] = (sym, "call")
            wanted[pick["put"]] = (sym, "put")
    for batch in _batches(list(wanted), TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, options=batch):
            if q.symbol in wanted:
                sym, leg = wanted[q.symbol]
                names[sym]["options"][leg] = {
                    "symbol": q.symbol,
                    "bid": float(q.bid) if q.bid is not None else None,
                    "ask": float(q.ask) if q.ask is not None else None,
                    "updated_at": _iso(q.updated_at),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
        await asyncio.sleep(TT_PAUSE_S)
    return names


def _illiquid_verdicts() -> dict[str, dict]:
    """The names the liquidity verdict holds illiquid, with their verdicts."""
    from cherrypick.technicals import liquidity

    names = liquidity.load()
    return {s: names[s] for s in liquidity.illiquid(names)}


def cmd_measure(args) -> int:
    from cherrypick.core import calendar as cal
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    now = datetime.now(ET)
    today = now.date()
    if not args.force and not in_window(now, cal.is_trading_day(today), cal.session_close_hhmm(today)):
        print(
            json.dumps(
                {
                    "ok": True,
                    "skipped": f"outside the measuring window ({WINDOW_START} ET to 30 min before the close)",
                }
            )
        )
        return 0
    cands = _read_json(store_dir() / "candidates.json", {}).get("names") or {}
    if not cands:
        print(json.dumps({"ok": False, "reason": "no candidates; run harvest first"}))
        return 1
    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1

    chain_path = store_dir() / "chains" / f"{today.isoformat()}.json"
    chain_cache = _read_json(chain_path, {})
    started = datetime.now(UTC)
    problem = None
    try:
        session = SessionManager(store).get_session()
        # Names held illiquid are not measured between sweeps (technicals `liquidity.skip`).
        from cherrypick.technicals import liquidity

        todo = sorted(set(cands) - liquidity.skip(today))
        names = asyncio.run(_measure(session, todo, chain_cache, args.limit))
    except Exception as exc:  # noqa: BLE001 — keep the chains gathered so far; warn and stop
        names, problem = None, f"{type(exc).__name__}: {exc}"
    _write_json(chain_path, chain_cache)
    if names is None:
        _warn("Universe measurement failed", problem or "")
        print(json.dumps({"ok": False, "reason": problem}))
        return 1

    body = {"measured_at": started.isoformat(), "names": names}
    out = store_dir() / "measurements" / today.isoformat() / f"{started.astimezone(ET):%H%M}.json"
    _write_json(out, body)
    usable = sum(1 for row in names.values() if reading(row, started)["option"]) if names else 0
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(out),
                "names": len(names),
                "listed": sum(1 for r in names.values() if r.get("listed")),
                "with_option_reading": usable,
                "chains_cached": len(chain_cache),
            },
            indent=1,
        )
    )
    return 0


# ------------------------------------------------------------------------------------------------
# build


def build_sectors() -> dict:
    """Rebuild `sectors.json` from the editions and the hand-kept `sectors.manual.json`, and warn
    when the changes list grows — the signal to re-evaluate matching the vendor's sectors."""
    from cherrypick.core import home

    editions_dir = home.data_dir("market-report") / "vendor-editions"
    editions = {p.stem: p.read_text(encoding="utf-8") for p in sorted(editions_dir.glob("????-??-??.html"))}
    manual = _read_json(store_dir() / "sectors.manual.json", {})
    path = store_dir() / "sectors.json"
    before = set(_read_json(path, {}).get("changes") or [])
    doc = sector_map(editions, {k: v for k, v in manual.items() if not k.startswith("_")})
    doc["built_at"] = datetime.now(UTC).isoformat()
    _write_json(path, doc)
    new = [c for c in doc["changes"] if c not in before]
    if new:
        _warn("Vendor sectors changed: re-evaluate the sector decision", "\n".join(new))
    return doc


def cmd_build(_args) -> int:
    cands = _read_json(store_dir() / "candidates.json", {}).get("names") or {}
    measurements = [
        m for p in sorted((store_dir() / "measurements").glob("*/*.json")) if (m := _read_json(p, None))
    ]
    universe = build_universe(cands, measurements, load_volumes(), illiquid=_illiquid_verdicts())
    universe["built_at"] = datetime.now(UTC).isoformat()
    sectors = build_sectors()
    for sym, row in universe["names"].items():
        row["sector"] = (sectors["sectors"].get(sym) or {}).get("sector")
    # ETFs and indexes have no sector: the breadth table the sectors serve holds stocks only.
    universe["members_without_sector"] = [s for s in universe["stocks"] if not universe["names"][s]["sector"]]
    universe["measurements"] = len(measurements)
    day = datetime.now(ET).date().isoformat()
    _write_json(store_dir() / f"universe-{day}.json", universe)
    _write_json(store_dir() / "universe.json", universe)
    print(
        json.dumps(
            {
                "ok": True,
                "counts": universe["counts"],
                "stocks": len(universe["stocks"]),
                "etfs": len(universe["etfs"]),
                "measurements": len(measurements),
                "members_without_sector": len(universe["members_without_sector"]),
            },
            indent=1,
        )
    )
    return 0


def cmd_watchlist(args) -> int:
    """Mirror the universe's members to the account's tastytrade watchlist. Shows the plan and writes
    nothing unless `--apply`. Only ever touches the one list it made (see `WATCHLIST_GROUP`)."""
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
    from tastytrade.watchlists import PrivateWatchlist

    universe = _read_json(store_dir() / "universe.json", None)
    if not universe:
        print(json.dumps({"ok": False, "reason": "no universe.json; run build first"}))
        return 1
    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1

    async def run() -> dict:
        session = SessionManager(store).get_session()
        found = next((w for w in await PrivateWatchlist.get(session) if w.name == WATCHLIST_NAME), None)
        existing = None
        if found is not None:
            existing = {
                "group_name": found.group_name,
                "symbols": [e.get("symbol") for e in found.watchlist_entries or [] if e.get("symbol")],
            }
        plan = watchlist_plan(universe.get("members") or [], existing, allow_shrink=args.allow_shrink)
        plan["applied"] = False
        if args.apply and plan["action"] in ("create", "replace"):
            body = watchlist_body(plan["entries"])
            if plan["action"] == "create":
                await session._post("/watchlists", json=body)
            else:
                await session._put(f"/watchlists/{urllib.parse.quote(WATCHLIST_NAME)}", json=body)
            plan["applied"] = True
        return plan

    try:
        plan = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — a refused or failed write changes nothing; say so
        _warn("Universe watchlist sync failed", f"{type(exc).__name__}: {exc}")
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    plan.pop("entries", None)
    if plan["action"] == "refuse":
        _warn("Universe watchlist not synced", plan["reason"])
    print(json.dumps({"ok": plan["action"] != "refuse", "watchlist": WATCHLIST_NAME, **plan}, indent=1))
    return 0 if plan["action"] != "refuse" else 1


def cmd_daily(args) -> int:
    rc = cmd_harvest(args)
    return rc or cmd_build(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("harvest", cmd_harvest), ("daily", cmd_daily)):
        p = sub.add_parser(name)
        p.add_argument("--no-follow", action="store_true", help="skip the follow feed; editions only")
        p.set_defaults(fn=fn)
    me = sub.add_parser("measure")
    me.add_argument("--force", action="store_true", help="measure outside the window (for testing only)")
    me.add_argument("--limit", type=int, help="fetch at most this many new chains")
    me.set_defaults(fn=cmd_measure)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    wl = sub.add_parser("watchlist")
    wl.add_argument("--apply", action="store_true", help="write to tastytrade (default: show the plan)")
    wl.add_argument(
        "--allow-shrink", action="store_true", help=f"allow removing over {MAX_SHRINK:.0%} at once"
    )
    wl.set_defaults(fn=cmd_watchlist)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
