"""The broker snapshot the daily position evaluation is judged on (`run.py manage`).

packages/manage is credential-free and network-free: it evaluates a snapshot and nothing else. This is
where the snapshot comes from -- the one broker-touching step, and every call in it is a READ: the
`positions` verb's priced positions and balances, the broker's market metrics (last price, the next
earnings date, the ex-dividend date) for every underlying held and for SPY and VIX, each underlying's
trade history, the XSP option chain the hedge is sized from, and the chain of any expiry holding a
short strangle. Nothing here
places, cancels or modifies an order. Account numbers arrive masked from `positions`, and the history
rows carry none.

The history is what lets manage see an ORDER CHAIN -- the opening order and every roll since -- rather
than the one average open price per leg the positions endpoint keeps. Without it a rolled trade's
basis is wrong (GLD on 2026-10-13: the open legs said $1,104 in, the chain $193, because two rolls had
taken in $640).

Metrics and history come from the first enabled module whose broker tool answers (meic's does). A
failure to get either is recorded in the snapshot, never fatal: the evaluation still runs every rule
that needs only prices, and says what was unavailable.

Version 3 (2026-10-18) adds what a portfolio reading needs (docs/portfolio-guidance-plan.md): each
underlying's beta and three-month correlation to SPY as the broker states them -- the beta the broker's
own beta-weighted delta uses, so the two can agree -- and the benchmark's metrics whether or not it is
held, since a beta-weighted delta is priced in its units. The account's kind (margin or cash, IRA or
not) rides on `positions`, from the broker's account record.

Version 4 adds each underlying's implied volatility index and 30-day historical volatility, kept in
the broker's own units and named for them (`iv_index` is a decimal, 0.196; `hv_30_day_pct` a
percentage, 10.45). SPY's are what a day's expected move is read against.

Version 5 adds the hedge chain: one expiry of XSP, quoted, for manage to size a beta-weighted-delta
hedge from. XSP because it is European and cash-settled (no early assignment, no shares delivered,
allowed in an IRA as a defined-risk spread) and a tenth of SPX, so a small account can size it. The
expiry is the standard monthly 30-60 days out, preferring 45-60 -- the suite's premium-selling cycle --
and the window is HEDGE_STRIKES strikes either side of spot, wide enough to reach a 15-delta strike.
Its metrics are quoted every day with the benchmark's.

Version 6 quotes VIX every day too: the buying-power band rises with it.

Version 7 carries the income reading: the newest `screener-greeks/income/<date>.json` (written by
`fetch_screener_greeks.py income` the previous afternoon; never a `.trial` file), at most
INCOME_MAX_AGE_DAYS old, which manage sizes candidate trades from. A file read, not a broker call, so
the snapshot stays the record of everything an evaluation was judged on.

Version 8 fills an earnings report's time of day where the broker leaves it blank, from Nasdaq's
public calendar as `scripts/fetch_earnings_times.py` records it (`market-report/earnings/times/`),
for the same report date only and in the broker's own spelling (`BTO` / `AMC`), marked
`earnings_time_source: "nasdaq"`. An earnings trade closes on its report's day when that is before
the open and the day after otherwise, and on 2026-10-18 the broker timed none of RTX, NFLX and CLF:
CLF and RTX report before the open, so without this both would have been held a day past the call.

Version 9 carries the broker's own greeks for every option leg held (`broker_greeks`, keyed by the
leg's symbol, per share as the feed states them): the stream cache's where it holds them fresh, the
feed's for the rest through the broker tool's read-only `get_greeks`. manage prices every leg itself,
so these are not inputs to any verdict; they let each day's reading set its beta-weighted delta and
theta beside the same sums on the broker's model, which is the check the portfolio plan asks for
before the bands are trusted.

Version 10 carries the account's orders as the broker lists them for the day (`working_orders`,
through the broker tool's read-only `get_working_orders`): working ones and the day's filled or
cancelled ones alike, each slimmed to its id, status, underlying, type, price and legs, with every
account number left out. manage counts a call still being sold against shares as already written
(docs/skate-plan.md step 5 reads it for the wheel view's resting buy-to-close).

Version 11 carries `roll_chains`: for every expiry in which one underlying is short both a put and a
call (a short strangle or straddle, or anything holding one), that expiry's chain, quoted, ROLL_STRIKES
strikes either side of spot. manage rolls a tested strangle's untested short toward the money (the
tastylive strategy guide's defence), and needs a strike that is listed and a price that was quoted for
it, which the trade's own legs cannot supply. One read per such expiry; none when the account holds
none.

Version 12 carries `skate_bands`, the skate screen's range bands (docs/skate-plan.md step 1) for the
names the account holds, so the wheel view can judge each short strike against today's band and
`reevaluate` still gets the same answer twice. A file read, not a broker call: the newest
`technicals/skate/<day>.json` on or before the session and at most SKATE_MAX_SESSIONS trading sessions
behind it, cut to the held names and to what manage reads (the band day's close; each row's side,
expiry and bands with their skate curves; the file's `offsets` and the settings that say what the
rates mean), since the whole file is ~2 MB. The band is copied as the file states it, at the band
day's close; manage moves it to today's price (`wheel._band`, band + today's spot - that close, as
`technicals.skate.shift_for` does), so a re-run with a different spot stays consistent. A missing,
stale or unreadable file is `skate_bands: null` with `skate_bands_error` saying why, never fatal.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import date, timedelta
from typing import Any

from cherrypick.core import clock, streamcache
from cherrypick.core.db import connect_ro

from . import config as cfgmod
from . import doctor
from . import positions as positions_mod
from .util import first_json

SNAPSHOT_VERSION = 12
# Greeks move far more slowly than a bid; the cache's are used up to this old, the feed's otherwise.
GREEKS_MAX_AGE_SECONDS = 900.0
# Monday reads Friday's afternoon reading; anything older is a stale book of quotes.
INCOME_MAX_AGE_DAYS = 4
# The timings file is fetched nightly a month ahead; a week-old one still knows this week's reports.
EARNINGS_TIMES_MAX_AGE_DAYS = 7
# The timings file's spelling to the broker's, which manage reads.
EARNINGS_TIME_SPELLING = {"bmo": "BTO", "amc": "AMC"}
# What a beta-weighted delta is stated in. The broker's betas are against SPY, so this is not a free
# choice: a different benchmark would need betas the broker does not supply.
BENCHMARK = "SPY"
# What the buying-power band is scaled by.
VOLATILITY_INDEX = "VIX"
# The instrument a delta hedge is sized in, and the slice of its chain the snapshot carries.
HEDGE_ROOT = "XSP"
HEDGE_DTE = (30, 60)
HEDGE_PREFER_MIN_DTE = 45
# XSP lists 1-point strikes near the money; 60 a side reaches past the 15-delta strike at 45 days.
HEDGE_STRIKES = 60
# Strikes either side of spot a short strangle's expiry is quoted at: an untested short rolls toward
# the money, so the window is centred there, and 40 a side reaches a 30-delta strike on a $1 ladder.
ROLL_STRIKES = 40
# How far back a chain is rebuilt. A LEAPS-backed position can roll for a year; anything older than
# this is reported as an incomplete chain rather than guessed.
HISTORY_DAYS = 400
# The band file is built at 06:45 ET from the previous session's bars, so it is normally one session
# behind; two is one missed build, three two. Past that the range under the band is a week old and its
# weekly expiries have begun to roll off, so the wheel check says it has no band rather than use it.
SKATE_MAX_SESSIONS = 3
# What the snapshot copies of a band file: per row, and per band within a row (the skate curves are
# what the rate at a strike is read from, on the file's `offsets`), and the settings that say what
# the rates mean (`recent` is the last `recent_years` from `history_from`; `soft` the screen's marks).
SKATE_ROW_KEEP = ("side", "expiry", "days", "sessions")
SKATE_BAND_KEEP = ("haircut", "band", "n", "n_recent", "skate", "skate_recent")
SKATE_CONFIG_KEEP = ("history_from", "recent_years", "soft")


def _metric(entry: dict[str, Any]) -> dict[str, Any]:
    earnings = entry.get("earnings") if isinstance(entry.get("earnings"), dict) else {}
    return {
        "last": entry.get("last"),
        "earnings_date": earnings.get("expected_report_date"),
        "earnings_time": earnings.get("time_of_day"),
        "ex_dividend_date": entry.get("dividend_ex_date"),
        "dividend_rate_per_share": entry.get("dividend_rate_per_share"),
        "iv_rank": entry.get("implied_volatility_index_rank"),
        "beta": entry.get("beta"),
        "beta_updated_at": entry.get("beta_updated_at"),
        "corr_spy_3month": entry.get("corr_spy_3month"),
        "iv_index": entry.get("implied_volatility_index"),
        "hv_30_day_pct": entry.get("historical_volatility_30_day"),
    }


def _first_answer(
    cfg: dict[str, Any], prefer: str | None, argv: list[str], timeout: int = 35
) -> tuple[dict, str | None]:
    """The first enabled module whose broker tool answers `argv` ok, or the last error."""
    modules = cfgmod.enabled_modules(cfg)
    order = [m for m in ([prefer] if prefer else []) + ["meic", *modules] if m in modules]
    last_error = "no enabled module with a broker tool"
    for name in dict.fromkeys(order):
        mcfg = modules[name]
        try:
            run = doctor._run(
                cfgmod.module_root(mcfg, name), [*cfgmod.broker_tool(mcfg, name), *argv], timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # A slow broker read is a missing piece of the snapshot, never the end of it: until
            # 2026-10-18 one timed-out metrics call took the whole evaluation down with it.
            last_error = f"{name}: {argv[0]} timed out after {timeout}s"
            continue
        payload = first_json(run.stdout)
        if payload.get("ok"):
            return payload, None
        last_error = f"{name}: {payload.get('error') or f'no answer to {argv[0]}'}"
    return {}, last_error


def market_metrics(
    cfg: dict[str, Any], symbols: list[str], prefer: str | None = None
) -> tuple[dict, str | None]:
    """{symbol: metrics} for `symbols`, and an error string when no broker tool could supply them."""
    if not symbols:
        return {}, None
    payload, error = _first_answer(cfg, prefer, ["get_market_overview", "--symbols", *symbols])
    out = {}
    for entry in payload.get("metrics") or []:
        if isinstance(entry, dict) and entry.get("symbol"):
            out[str(entry["symbol"]).upper()] = _metric(entry)
    return out, error


def transactions(
    cfg: dict[str, Any], symbols: list[str], prefer: str | None = None
) -> tuple[dict, str | None]:
    """{underlying: [trade history rows]} reaching back HISTORY_DAYS, so manage can rebuild each
    position's order chain (its opening order and every roll); an error string when unavailable."""
    if not symbols:
        return {}, None
    since = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    payload, error = _first_answer(
        cfg, prefer, ["get_transactions", "--underlyings", *symbols, "--since", since], timeout=180
    )
    return payload.get("transactions") or {}, error


def _option_symbols(positions: dict[str, Any]) -> dict[str, str]:
    """{leg symbol: streamer symbol} for every option leg held, in every account."""
    out: dict[str, str] = {}
    for account in positions.get("accounts") or []:
        for u in account.get("underlyings") or []:
            for leg in u.get("legs") or []:
                occ = str(leg.get("symbol") or "")
                if leg.get("right") in ("C", "P") and occ:
                    out[occ] = streamcache.occ_to_streamer_symbol(occ) or occ.strip()
    return out


def _cached_greeks(streamer_syms: list[str]) -> dict[str, dict]:
    path = streamcache.cache_path()
    if not streamer_syms or not path.exists():
        return {}
    try:
        conn = connect_ro(path)
    except Exception:  # noqa: BLE001 -- an unreadable cache means ask the feed instead
        return {}
    try:
        return streamcache.greeks_for(
            conn, streamer_syms, now_ts=time.time(), max_age_seconds=GREEKS_MAX_AGE_SECONDS
        )
    finally:
        conn.close()


def broker_greeks(
    cfg: dict[str, Any], positions: dict[str, Any], prefer: str | None = None
) -> tuple[dict[str, dict], str | None]:
    """{leg symbol: the broker's per-share greeks} for every option leg held, cache first, feed second;
    and an error string when the feed was needed and could not answer. A leg neither has is absent."""
    wanted = _option_symbols(positions)
    unique = sorted(set(wanted.values()))
    found = {sym: dict(g, source="cache") for sym, g in _cached_greeks(unique).items()}
    missing = [s for s in unique if s not in found]
    error = None
    if missing:
        payload, error = _first_answer(cfg, prefer, ["get_greeks", "--symbols", *missing], timeout=60)
        for sym, g in (payload.get("greeks") or {}).items():
            found[sym] = dict(g, source="feed")
    keep = ("delta", "gamma", "theta", "vega", "iv", "source")
    return (
        {occ: {k: found[s].get(k) for k in keep} for occ, s in wanted.items() if s in found},
        error,
    )


# The broker's reply also carries the account number, the user's name and id: none of them is kept.
ORDER_KEEP = ("id", "status", "underlying_symbol", "order_type", "time_in_force", "price", "size")
LEG_KEEP = ("symbol", "instrument_type", "action", "quantity", "remaining_quantity")


def _slim_order(order: dict[str, Any]) -> dict[str, Any]:
    """An order without its account number or fills: what manage reads, nothing else."""
    out = {k: order.get(k) for k in ORDER_KEEP}
    out["legs"] = [
        {k: leg.get(k) for k in LEG_KEEP} for leg in order.get("legs") or [] if isinstance(leg, dict)
    ]
    return out


def working_orders(cfg: dict[str, Any], prefer: str | None = None) -> tuple[list[dict], str | None]:
    """The day's orders on the account, slimmed (`_slim_order`), and an error string when unavailable."""
    payload, error = _first_answer(cfg, prefer, ["get_working_orders"])
    return [_slim_order(o) for o in payload.get("orders") or [] if isinstance(o, dict)], error


def pick_hedge_expiry(listing: dict[str, list[dict]], session: date) -> str | None:
    """The standard monthly 30-60 days out, preferring 45-60 and then the nearest to 45; a weekly
    only when no monthly falls in the window."""
    lo, hi = HEDGE_DTE
    found = []
    for exp, rows in listing.items():
        try:
            dte = (date.fromisoformat(exp) - session).days
        except ValueError:
            continue
        if lo <= dte <= hi:
            monthly = any(
                str(r.get("expiration_type")) in ("Regular", "ExpirationType.REGULAR") for r in rows
            )
            found.append((not monthly, dte < HEDGE_PREFER_MIN_DTE, abs(dte - HEDGE_PREFER_MIN_DTE), exp))
    return min(found)[-1] if found else None


def _option_row(row: dict[str, Any]) -> dict[str, Any] | None:
    right = str(row.get("option_type") or "")
    right = (
        "C" if right.endswith(("C", "CALL", "Call")) else "P" if right.endswith(("P", "PUT", "Put")) else None
    )
    if right is None or row.get("strike_price") is None:
        return None
    return {
        "symbol": row.get("symbol"),
        "strike": float(row["strike_price"]),
        "right": right,
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "mid": row.get("mid"),
        # The broker's own greeks, kept for comparison; manage prices from the mid.
        "broker_delta": row.get("delta"),
        "broker_iv": row.get("iv"),
    }


def hedge_chain(cfg: dict[str, Any], spot: float | None, session: date, prefer: str | None) -> dict[str, Any]:
    """One quoted expiry of HEDGE_ROOT around `spot`, or a record of why there is none. Two reads:
    the expiry listing, then the slice with quotes and greeks."""
    out: dict[str, Any] = {"root": HEDGE_ROOT, "expiration": None, "options": [], "error": None}
    if not spot:
        out["error"] = f"no {HEDGE_ROOT} price to centre the chain on"
        return out
    listing, error = _first_answer(
        cfg, prefer, ["get_option_chain", "--symbol", HEDGE_ROOT, "--strike_count", "0"]
    )
    expiry = pick_hedge_expiry(listing.get("chain") or {}, session)
    if expiry is None:
        out["error"] = error or f"no {HEDGE_ROOT} expiry {HEDGE_DTE[0]}-{HEDGE_DTE[1]} days out"
        return out
    argv = [
        "get_option_chain",
        "--symbol",
        HEDGE_ROOT,
        "--expiration",
        expiry,
        "--include_quotes",
        "--include_greeks",
        "--strike_count",
        str(HEDGE_STRIKES),
        "--around_price",
        str(spot),
    ]
    payload, error = _first_answer(cfg, prefer, argv, timeout=60)
    rows = (payload.get("chain") or {}).get(expiry) or []
    first = rows[0] if rows else {}
    out.update(
        {
            "expiration": expiry,
            "dte": (date.fromisoformat(expiry) - session).days,
            "spot": spot,
            "settlement_type": first.get("settlement_type"),
            "exercise_style": first.get("exercise_style"),
            "quotes_complete": payload.get("quotes_complete"),
            "options": [o for o in (_option_row(r) for r in rows) if o is not None],
            "error": error,
        }
    )
    return out


def short_pairs(positions: dict[str, Any]) -> list[tuple[str, str]]:
    """(underlying, expiry) for every expiry in which one underlying is short both a put and a call."""
    out: set[tuple[str, str]] = set()
    for account in positions.get("accounts") or []:
        for u in account.get("underlyings") or []:
            short_rights: dict[str, set[str]] = {}
            for leg in u.get("legs") or []:
                try:
                    qty = float(leg.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
                if leg.get("right") in ("C", "P") and qty < 0 and leg.get("expiration"):
                    short_rights.setdefault(str(leg["expiration"])[:10], set()).add(leg["right"])
            for exp, rights in short_rights.items():
                if rights == {"C", "P"} and u.get("underlying"):
                    out.add((str(u["underlying"]).upper(), exp))
    return sorted(out)


def roll_chains(
    cfg: dict[str, Any], positions: dict[str, Any], metrics: dict[str, dict], prefer: str | None
) -> dict[str, dict[str, Any]]:
    """{"<UNDERLYING>|<expiry>": that expiry's quoted chain around spot} for every `short_pairs` entry,
    each with `error` saying why it is empty when it is."""
    out: dict[str, dict[str, Any]] = {}
    for underlying, expiry in short_pairs(positions):
        spot = (metrics.get(underlying) or {}).get("last")
        entry: dict[str, Any] = {"underlying": underlying, "expiration": expiry, "spot": spot, "options": []}
        if not spot:
            out[f"{underlying}|{expiry}"] = {
                **entry,
                "error": f"no {underlying} price to centre the chain on",
            }
            continue
        argv = [
            "get_option_chain",
            "--symbol",
            underlying,
            "--expiration",
            expiry,
            "--include_quotes",
            "--include_greeks",
            "--strike_count",
            str(ROLL_STRIKES),
            "--around_price",
            str(spot),
        ]
        payload, error = _first_answer(cfg, prefer, argv, timeout=60)
        rows = (payload.get("chain") or {}).get(expiry) or []
        options = [o for o in (_option_row(r) for r in rows) if o is not None]
        out[f"{underlying}|{expiry}"] = {
            **entry,
            "quotes_complete": payload.get("quotes_complete"),
            "options": options,
            "error": error or (None if options else f"no quoted {underlying} strikes for {expiry}"),
        }
    return out


def income_dir():
    from cherrypick.core import home

    return home.data_dir("market-report") / "screener-greeks" / "income"


def income_reading(session: date, folder=None) -> dict[str, Any]:
    """The newest income reading on or before `session` and at most INCOME_MAX_AGE_DAYS old, with
    `error` saying why there is none. A `.trial` file is a test run and is never read."""
    folder = income_dir() if folder is None else folder
    days = []
    for f in folder.glob("????-??-??.json") if folder.is_dir() else []:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d <= session:
            days.append((d, f))
    if not days:
        return {"error": "no income reading yet (fetch_screener_greeks.py income, 14:45 ET)"}
    day, path = max(days)
    if (session - day).days > INCOME_MAX_AGE_DAYS:
        return {"error": f"the newest income reading is from {day.isoformat()}, too old to size from"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"{path.name} is unreadable: {exc}"[:200]}
    return {**doc, "error": None}


def skate_dir():
    from cherrypick.core import home

    # technicals' own home for the band files (`technicals.paths.data_dir`), override included.
    return home.data_dir("technicals", env="TECHNICALS_DATA_DIR") / "skate"


def _slim_skate_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: row.get(k) for k in SKATE_ROW_KEEP},
        "bands": {
            which: {k: (b or {}).get(k) for k in SKATE_BAND_KEEP}
            for which, b in (row.get("bands") or {}).items()
        },
    }


def skate_bands(session: date, symbols: list[str], folder=None) -> tuple[dict[str, Any] | None, str | None]:
    """The held names' entries from the newest skate band file on or before `session`, at most
    SKATE_MAX_SESSIONS trading sessions behind it, and an error string when there is none.

    Only `symbols` are copied, and of each only what manage reads: the band day's close, and each row's
    side, expiry and bands with their skate curves. With the file's `offsets` (the grid the curves are
    recorded on) that is enough to read the skate rate at any strike. A band file keys BRK.B where the
    broker says BRK/B; the copy is keyed by the broker's spelling, which is what the positions use."""
    from cherrypick.core import calendar as cal

    folder = skate_dir() if folder is None else folder
    days = []
    for f in folder.glob("????-??-??.json") if folder.is_dir() else []:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d <= session:
            days.append((d, f))
    if not days:
        return None, "no skate band file yet (python -m cherrypick.technicals skate, 06:45 ET)"
    day, path = max(days)
    behind = cal.trading_days_between(day, session)
    if behind > SKATE_MAX_SESSIONS:
        return None, (
            f"the newest skate band file is from {day.isoformat()}, {behind} sessions old; "
            f"bands older than {SKATE_MAX_SESSIONS} are not read"
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path.name} is unreadable: {exc}"[:200]
    names = doc.get("names") if isinstance(doc.get("names"), dict) else {}
    out: dict[str, dict] = {}
    for sym in symbols:
        entry = next(
            (names[s] for s in (sym, sym.replace("/", "."), sym.replace(".", "/")) if s in names), None
        )
        if isinstance(entry, dict):
            out[sym] = {
                "spot": entry.get("spot"),
                "rows": [_slim_skate_row(r) for r in entry.get("rows") or [] if isinstance(r, dict)],
            }
    cfg = doc.get("config") if isinstance(doc.get("config"), dict) else {}
    return {
        "day": doc.get("day") or day.isoformat(),
        "sessions_behind": behind,
        "offsets": doc.get("offsets"),
        "config": {k: cfg.get(k) for k in SKATE_CONFIG_KEEP},
        "names": out,
        "not_banded": [s for s in symbols if s not in out],
    }, None


def earnings_times_dir():
    from cherrypick.core import home

    return home.data_dir("market-report") / "earnings" / "times"


def earnings_times(session: date, folder=None) -> dict[str, dict[str, str]]:
    """{report date: {symbol: "BTO" | "AMC"}} from the newest timings file on or before `session` and
    at most EARNINGS_TIMES_MAX_AGE_DAYS old; empty when there is none or it cannot be read."""
    folder = earnings_times_dir() if folder is None else folder
    days = []
    for f in folder.glob("????-??-??.json") if folder.is_dir() else []:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d <= session and (session - d).days <= EARNINGS_TIMES_MAX_AGE_DAYS:
            days.append((d, f))
    if not days:
        return {}
    try:
        doc = json.loads(max(days)[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for day, symbols in (doc.get("dates") or {}).items():
        for symbol, when in (symbols or {}).items():
            spelled = EARNINGS_TIME_SPELLING.get(str(when or "").lower())
            if spelled:
                out.setdefault(day, {})[str(symbol).upper()] = spelled
    return out


def fill_earnings_times(metrics: dict[str, dict], times: dict[str, dict[str, str]]) -> None:
    """Give each metric the broker left untimed the timings file's time for the SAME report date."""
    for symbol, m in metrics.items():
        if m.get("earnings_time") or not m.get("earnings_date"):
            continue
        when = (times.get(str(m["earnings_date"])[:10]) or {}).get(symbol)
        if when:
            m["earnings_time"] = when
            m["earnings_time_source"] = "nasdaq"


def build(cfg: dict[str, Any]) -> dict[str, Any]:
    positions = positions_mod.run(cfg)
    symbols = sorted(
        {
            str(u.get("underlying")).upper()
            for account in positions.get("accounts") or []
            for u in account.get("underlyings") or []
            if u.get("underlying")
        }
    )
    ok, prefer = positions.get("ok"), positions.get("broker_module")
    # The benchmark, the hedge root and VIX are quoted whether or not they are held; their history is not
    # wanted unless they are.
    quoted = sorted({*symbols, BENCHMARK, HEDGE_ROOT, VOLATILITY_INDEX})
    metrics, error = market_metrics(cfg, quoted, prefer) if ok else ({}, None)
    history, history_error = transactions(cfg, symbols, prefer) if ok else ({}, None)
    session = clock.today_iso()
    fill_earnings_times(metrics, earnings_times(date.fromisoformat(session)))
    hedge_spot = (metrics.get(HEDGE_ROOT) or {}).get("last")
    hedge = hedge_chain(cfg, hedge_spot, date.fromisoformat(session), prefer) if ok else None
    greeks, greeks_error = broker_greeks(cfg, positions, prefer) if ok else ({}, None)
    orders, orders_error = working_orders(cfg, prefer) if ok else ([], None)
    chains = roll_chains(cfg, positions, metrics, prefer) if ok else {}
    bands, bands_error = skate_bands(date.fromisoformat(session), symbols)
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "session": session,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark": BENCHMARK,
        "positions": positions,
        "metrics": metrics,
        "metrics_error": error,
        "transactions": history,
        "transactions_error": history_error,
        "hedge_chain": hedge,
        "broker_greeks": greeks,
        "broker_greeks_error": greeks_error,
        "working_orders": orders,
        "working_orders_error": orders_error,
        "roll_chains": chains,
        "income": income_reading(date.fromisoformat(session)),
        "skate_bands": bands,
        "skate_bands_error": bands_error,
    }
