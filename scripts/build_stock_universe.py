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
  It also lands OCC's daily option volume by underlying for each recent session it lacks (one
  CSV a session covers every name, ~4,400 underlyings), kept as `{underlying: contracts}`.
- **measure** (twice a session, inside regular hours only): for each candidate, tastytrade's
  liquidity rating (recorded as a guide), the stock's bid/ask, and the bid/ask of its at-the-money
  call and put about 30 days out. A quote not stamped inside that day's regular session is
  discarded rather than measured — a weekend snapshot of NMR read 9.55/10.98, the overnight book,
  not the market.
- **build**: a pure function over every saved measurement and OCC session. A name is **in** only
  when its stock spread, option spread and option volume all hold on medians over at least
  `MIN_SESSIONS` sessions; fewer sessions is **pending**, never a pass. Every name gets its
  reasons, in or out, with tastytrade's rating beside them as a guide.
- **watchlist**: mirrors the members to a private tastytrade watchlist, `cherrypick universe`. It
  is the one step that writes to the account — a watchlist, never an order — so it shows its plan
  and writes nothing without `--apply`, has its own schedule switch, and only ever replaces the
  list it created (marked by its group). SPX, NDX, SPY, QQQ and IWM are pinned: on the list from
  the first sync and never removed. It refuses to strip the list to those or to cut more than half
  of the universe's names in one sync.

Pacing: the follow feed gets one request per trader and OCC one per missing session, 5-10
seconds apart; tastytrade gets batched calls and one chain request per name a day (cached), a
second apart. A throttling response ends
the step with what it has.

    python scripts/build_stock_universe.py harvest [--no-follow]
    python scripts/build_stock_universe.py measure [--force] [--limit N]
    python scripts/build_stock_universe.py build
    python scripts/build_stock_universe.py daily        # harvest, then build
    python scripts/build_stock_universe.py watchlist [--apply] [--allow-shrink]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
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
    "option_target_dte": 30,
    "option_dte_range": (14, 60),
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

OCC_PAUSE_RANGE_S = (5.0, 10.0)
OCC_URL = (
    "https://marketdata.theocc.com/volume-query?reportDate={yyyymmdd}&format=csv&volumeQueryType=O"
    "&symbolType=ALL&symbol=&reportType=D&accountType=ALL&productKind=ALL&porc=BOTH"
)
# An OCC reply this small is a "no data" page, not a session: a real day runs to ~4 MB.
OCC_MIN_BYTES = 100_000

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


def choose_options(expirations: list[dict], spot: float, rule: dict = RULE) -> dict | None:
    """The at-the-money call and put of the expiration nearest the target DTE, inside the range.
    `expirations` is the cached chain: [{expiration, dte, strikes: [[strike, call, put], ...]}]."""
    lo, hi = rule["option_dte_range"]
    usable = [e for e in expirations if lo <= e["dte"] <= hi and e["strikes"]]
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
        out["option"] = max(legs, key=lambda x: x["score"])  # the worse leg decides
    elif row.get("options"):
        out["notes"].append("option quote stale or one-sided")
    return out


def occ_volume(csv_text: str) -> dict[str, int]:
    """{underlying: contracts traded} from one OCC daily volume-query CSV. OCC counts each side of a
    trade (customer + firm on one side roughly equals market maker on the other: AAPL 1.53M C +
    0.05M F against 1.56M M on 2026-09-25), so contracts traded is half the file's total. Rows for
    every option root of an underlying (`AAPL`, `2AAPL` after an adjustment) add to it."""
    totals: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        try:
            q = int(row["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        und = (row.get("underlying") or "").strip()
        if und:
            totals[und] = totals.get(und, 0) + q
    return {und: q // 2 for und, q in totals.items()}


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
    spread, and option volume, each on a median over at least `min_sessions` sessions, and until
    each has that many the name is `pending`."""
    if not name.get("listed"):
        return "out", ["not a listed equity on tastytrade (an index, a future, or a retired symbol)"], {}

    recent = sorted(sessions)[-rule["lookback_sessions"] :]
    medians: dict = {}
    for kind in ("stock", "option"):
        per_session = []
        for day in recent:
            vals = [r[kind] for r in sessions[day] if r.get(kind)]
            if vals:
                per_session.append(
                    {
                        "score": statistics.median(v["score"] for v in vals),
                        "pct": statistics.median(v["pct"] for v in vals),
                        "width": statistics.median(v["width"] for v in vals),
                    }
                )
        medians[kind] = {
            "sessions": len(per_session),
            **(
                {k: statistics.median(p[k] for p in per_session) for k in ("score", "pct", "width")}
                if per_session
                else {}
            ),
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
        if m["sessions"] < rule["min_sessions"]:
            pending.append(f"{kind} spread measured in {m['sessions']} of {rule['min_sessions']} sessions")
        elif m["score"] > 1.0:
            reasons.append(
                f"{kind} spread {m['pct']:.2%} / ${m['width']:.2f} wider than "
                f"{rule[pct_key]:.2%} or ${rule[abs_key]:.2f}"
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
) -> dict:
    """The universe document from the candidate list, every saved measurement, and the OCC volume
    by session (`{ISO date: {OCC underlying: contracts}}`). Pure: the same files give the same
    universe, so it can be rebuilt after any rule change."""
    volumes = volumes or {}
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


def _occ_dir() -> Path:
    return store_dir() / "occ-volume"


def load_volumes() -> dict[str, dict[str, int]]:
    return {p.stem: v for p in sorted(_occ_dir().glob("????-??-??.json")) if (v := _read_json(p, None))}


def harvest_occ(sessions: int) -> tuple[int, list[str]]:
    """Land OCC's daily volume for each of the last `sessions` trading sessions not yet stored, one
    request each, paced. A session OCC has not published yet (a tiny "no data" reply) is left for
    the next run rather than stored as a day of zeros."""
    from cherrypick.core import calendar as cal

    today = datetime.now(ET).date()
    day = today if cal.is_trading_day(today) else cal.previous_trading_day(today)
    wanted = []
    while len(wanted) < sessions:
        wanted.append(day)
        day = cal.previous_trading_day(day)
    landed, problems = 0, []
    first = True
    for d in sorted(wanted):
        path = _occ_dir() / f"{d.isoformat()}.json"
        if path.exists():
            continue
        if not first:
            time.sleep(random.uniform(*OCC_PAUSE_RANGE_S))
        first = False
        url = OCC_URL.format(yyyymmdd=d.strftime("%Y%m%d"))
        req = urllib.request.Request(url, headers={"User-Agent": FOLLOW_UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            problems.append(f"OCC {d}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            problems.append(f"OCC {d}: {exc}")
            continue
        if len(body) < OCC_MIN_BYTES:
            continue  # not published yet
        volumes = occ_volume(body.decode("utf-8", errors="replace"))
        if volumes:
            _write_json(path, volumes)
            landed += 1
    return landed, problems


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
    occ_landed, occ_problems = harvest_occ(RULE["lookback_sessions"])
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


async def _measure(
    session, symbols: list[str], chain_cache: dict, limit_chains: int | None, thin: set[str]
) -> dict:
    """Instruments, metrics and stock quotes for every candidate; chains and option quotes only for
    names whose option volume has not already ruled them out (`thin`), so the per-name chain calls
    are spent where a spread can still decide."""
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

    for sym in listed:
        if sym in thin:
            names[sym]["options_skipped"] = "option volume already below the bar"
    optionable = [s for s in listed if s not in thin]

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
        volumes = load_volumes()
        thin = {s for s in cands if volume_too_thin(volume_series(s, volumes))}
        names = asyncio.run(_measure(session, sorted(cands), chain_cache, args.limit, thin))
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


def cmd_build(_args) -> int:
    cands = _read_json(store_dir() / "candidates.json", {}).get("names") or {}
    measurements = [
        m for p in sorted((store_dir() / "measurements").glob("*/*.json")) if (m := _read_json(p, None))
    ]
    universe = build_universe(cands, measurements, load_volumes())
    universe["built_at"] = datetime.now(UTC).isoformat()
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
