"""Every data source the suite holds, declared: what it holds, at what cadence, who writes it, and whether
it is licensed. One list, so a backtest, the builder and an automation can each ask "does data cover
this field, for this symbol, over these dates?" and get a yes, or the gap and the source that would
fill it.

**Declared here, measured there.** A `Source` says what a store can supply (`fields`, in the words
`chainmeasures.NEEDS` already uses, plus a few of its own) and how to read its span (`Probe`). Its
dates are never declared: `inventory.run` reads each source's first and last date and its session count
per symbol, read-only, into a cache (`inventory.path()`), and `coverage` answers from that cache, so
asking never scans a store.

**Licensed.** A vendor source is `licensed`; a result is licensed when any of its inputs is. Coverage
leaves a licensed source out unless asked for it, since new work may use one only while the licence
is active (the orchestrator's `features.backtest`).

**What is not a source.** A module's own ledger, a live cache, a study's private store and data that
is not market data are left out on purpose, each with its reason (`IGNORED`), so `inventory.undeclared`
can name a new store nobody declared.

Pure: no I/O here. `inventory` reads the stores; `__main__` is the command line.
"""

from __future__ import annotations

import bisect
import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from cherrypick.core import calendar as cal

#: What a source can supply. `closes`, `chain`, `iv_history` and `legs` are `chainmeasures.NEEDS`' words
#: (`legs` is read from a chain); the rest are this registry's.
FIELDS = frozenset(
    {
        "closes",  # daily closes
        "ohlc",  # open, high, low and close bars
        "volume",
        "spot",  # an underlying's price through the session
        "chain",  # option quotes for an expiry at an instant
        "greeks",  # per-contract delta (and more) beside the quotes
        "iv",  # an implied-volatility reading per day
        "iv_history",  # a series of the same IV reading over a lookback (IV rank, IV percentile)
        "oi",  # open interest per contract
        "profile",  # gamma, open interest and volume by strike
        "rates",  # a risk-free rate series
    }
)

KINDS = ("bars", "chain", "quotes", "iv", "profile", "index", "rates")

#: Cadences finest first, in seconds; a source at a finer cadence serves a coarser request.
CADENCE_SECONDS = {"~10s": 10, "~15s": 15, "1m": 60, "5m": 300, "1h": 3600, "1d": 86400}

#: Which expiries a chain source holds. `monthly` alone means no weeklies (Dolt's chains).
EXPIRIES = ("0dte", "1dte", "weekly", "monthly", "all")


@dataclass(frozen=True)
class Probe:
    """How `inventory` reads a source's span, read-only.

    `backend`:
    - `sqlite`: `where` is a file under the data root; `table`, `date_col`, `symbol_col`, an optional
      literal `filter`.
    - `dolt`: `where` is `database.table` on the local Dolt server; read one date at a time, since its
      key leads with the date.
    - `csv`: `where` is a directory under the data root; one file per symbol, named by it, dates in
      the first column (`date_format` `iso` or `mdy`).
    - `store_cache`: `where` is a vendor store's file name, its span read from the backtest's own
      inventory cache (`table` names the table whose span is the store's sessions).
    """

    backend: str
    where: str
    table: str = ""
    date_col: str = ""
    symbol_col: str = ""
    filter: str = ""
    date_format: str = "iso"
    #: csv only: every file in the folder is one series under this name (one file per year).
    series: str = ""


@dataclass(frozen=True)
class Source:
    name: str
    holds: str
    kind: str
    fields: tuple[str, ...]
    cadence: str
    probe: Probe
    writer: str
    licensed: bool = False
    expiries: tuple[str, ...] = ()
    #: For a store whose symbols the probe cannot list (one store per symbol, or a cache with no
    #: per-symbol spans): the symbols it holds. Empty: whatever the probe finds.
    symbols: tuple[str, ...] = ()
    #: Kept up to date by its writer. False: a fixed history that grows no more.
    ongoing: bool = True
    #: Keeps a moving window only, so its first date moves forward.
    rolling: bool = False
    note: str = ""


_VENDOR = "the data vendor, licensed: kept until the licence's purge date (backtest `purge --derived`)"
#: The full set's underlyings, spelt as the backtest's `fullset.SYMBOLS` (core never imports it; a
#: backtest test pins the two equal).
FULL_SET = ("SPX", "QQQ", "XSP", "SPY", "IWM", "GLD", "TLT")

SOURCES: tuple[Source, ...] = (
    Source(
        name="stock-history",
        holds="US stocks and ETFs, daily bars, raw (adjusted on read), from the Dolt stocks dataset",
        kind="bars",
        fields=("closes", "ohlc", "volume"),
        cadence="1d",
        probe=Probe("sqlite", "technicals/history.db", "bars", "date", "symbol"),
        writer="technicals history landing (Dolt stocks)",
    ),
    Source(
        name="technicals-eod",
        holds="the rotation set, benchmarks and liquid names, daily bars, raw",
        kind="bars",
        fields=("closes", "ohlc", "volume"),
        cadence="1d",
        probe=Probe("sqlite", "technicals/eod.db", "bars", "date", "symbol"),
        writer="technicals-land, 06:15 ET",
    ),
    Source(
        name="technicals-iv",
        holds="daily IV with its year high and low, and HV, for the names technicals lands",
        kind="iv",
        fields=("iv", "iv_history"),
        cadence="1d",
        probe=Probe("sqlite", "technicals/eod.db", "iv", "date", "symbol"),
        writer="technicals-land, 06:15 ET",
    ),
    Source(
        name="dolt-chains",
        holds="end-of-day option chains with IV and greeks for liquid names; monthlies only, no weeklies",
        kind="chain",
        fields=("chain", "greeks", "iv"),
        cadence="1d",
        expiries=("monthly",),
        probe=Probe("dolt", "options.option_chain", date_col="date", symbol_col="act_symbol"),
        writer="the Dolt options dataset, pulled 05:30 ET",
        note="early years are sparse: read sessions against the trading days in the span",
    ),
    Source(
        name="dolt-iv",
        holds="daily IV and HV with their week, month and year readings, per symbol",
        kind="iv",
        fields=("iv", "iv_history"),
        cadence="1d",
        probe=Probe("dolt", "options.volatility_history", date_col="date", symbol_col="act_symbol"),
        writer="the Dolt options dataset, pulled 05:30 ET",
    ),
    Source(
        name="index-sessions",
        holds="daily open, high, low and close for the indexes and ETFs the gex recorder follows",
        kind="index",
        fields=("closes", "ohlc"),
        cadence="1d",
        probe=Probe("sqlite", "gex/gex_history.db", "daily_sessions", "trade_date", "symbol"),
        writer="gex recorder",
    ),
    Source(
        name="cboe-vol",
        holds="Cboe's daily index closes: the VIX family, VVIX, SKEW, SPX and core.volindex's vol indexes",
        kind="index",
        fields=("closes",),
        cadence="1d",
        probe=Probe("csv", "market-files/cboe", date_format="mdy"),
        writer="scripts that fetch Cboe's public files",
    ),
    Source(
        name="spot-trail",
        holds="an underlying's spot at every gex recorder tick, regular hours",
        kind="quotes",
        fields=("spot",),
        cadence="~15s",
        probe=Probe("sqlite", "gex/gex_history.db", "gex_spot_history", "trade_date", "symbol"),
        writer="gex recorder",
    ),
    Source(
        name="recorded-0dte",
        holds="same-day option quotes and delta, the strikes nearest spot on each side, every recorder tick",
        kind="chain",
        fields=("chain", "greeks"),
        cadence="~10s",
        expiries=("0dte",),
        probe=Probe("sqlite", "gex/gex_history.db", "gex_quote_samples", "trade_date", "symbol"),
        writer="gex recorder (`quote_history`)",
    ),
    Source(
        name="gex-profile",
        holds="open interest, volume and gamma by strike through the session",
        kind="profile",
        fields=("profile", "oi"),
        cadence="5m",
        probe=Probe("sqlite", "gex/gex_history.db", "gex_profile_history", "trade_date", "symbol"),
        writer="gex recorder",
    ),
    Source(
        name="vol-curve-5m",
        holds="the VIX term structure and SPX, 5-minute bars, regular hours",
        kind="bars",
        fields=("ohlc", "closes"),
        cadence="5m",
        probe=Probe(
            "sqlite",
            "market-files/vol_curve_bars.db",
            "vol_curve_bars",
            "et_day",
            "symbol",
            "interval = '5m'",
        ),
        writer="vol-curve-bars, 16:30 ET (broker candles)",
    ),
    Source(
        name="vol-curve-1h",
        holds="the VIX term structure and SPX, 1-hour bars, regular hours",
        kind="bars",
        fields=("ohlc", "closes"),
        cadence="1h",
        probe=Probe(
            "sqlite",
            "market-files/vol_curve_bars.db",
            "vol_curve_bars",
            "et_day",
            "symbol",
            "interval = '1h'",
        ),
        writer="vol-curve-bars, 16:30 ET (broker candles)",
    ),
    Source(
        name="gold-1h",
        holds="gold futures (each dated contract and the continuous front) and GLD, 1-hour bars",
        kind="bars",
        fields=("ohlc", "closes", "volume"),
        cadence="1h",
        probe=Probe(
            "sqlite", "market-files/gold_bars.db", "gold_bars", "et_day", "symbol", "interval = '1h'"
        ),
        writer="scripts/fetch_gold_bars.py (broker candles)",
    ),
    Source(
        name="gold-1d",
        holds="gold futures (each dated contract and the continuous front) and GLD, daily bars",
        kind="bars",
        fields=("ohlc", "closes", "volume"),
        cadence="1d",
        probe=Probe(
            "sqlite", "market-files/gold_bars.db", "gold_bars", "et_day", "symbol", "interval = '1d'"
        ),
        writer="scripts/fetch_gold_bars.py (broker candles)",
    ),
    Source(
        name="profile-bars",
        holds="one-minute bars with the overnight, for the Market Profile set (/ES, /NQ, SPX, NDX, SPY, QQQ)",
        kind="bars",
        fields=("ohlc", "closes", "volume"),
        cadence="1m",
        probe=Probe("sqlite", "market-files/profile_bars.db", "profile_bars", "et_day", "symbol"),
        writer="profile-bars job",
        rolling=True,
    ),
    Source(
        name="yahoo-adjusted",
        holds="Yahoo's adjusted and raw daily closes, the check on our adjustment",
        kind="bars",
        fields=("closes",),
        cadence="1d",
        probe=Probe("sqlite", "market-report/adjusted/yahoo.db", "closes", "date", "symbol"),
        writer="scripts/fetch_yahoo_adjusted.py, each evening",
    ),
    Source(
        name="spx-index-bars",
        holds="SPX's daily bars from the broker's candles",
        kind="index",
        fields=("closes", "ohlc"),
        cadence="1d",
        probe=Probe("sqlite", "market-report/index/bars.db", "index_bars", "date", "symbol"),
        writer="technicals-land, 06:15 ET",
    ),
    Source(
        name="treasury-curve",
        holds="the Treasury's daily par yield curve, 1 month to 30 years (core.treasury reads it)",
        kind="rates",
        fields=("rates",),
        cadence="1d",
        probe=Probe("csv", "market-files/treasury", date_format="mdy", series="UST"),
        writer="scripts/fetch_market_files.py",
    ),
    Source(
        name="fred-rates",
        holds="the 3-month Treasury bill rate (FRED DGS3MO), daily",
        kind="rates",
        fields=("rates",),
        cadence="1d",
        probe=Probe("csv", "public/fred"),
        writer="fetched by script into ~/.cherrypick (public data)",
    ),
    # The vendor's stores: declared in `backtest.stores` (core never imports it); their spans come from
    # the backtest's own inventory cache. One store per symbol for the 0DTE ones.
    *(
        Source(
            name=f"vendor-0dte-{store.removesuffix('.db').lower()}",
            holds=f"0DTE quotes, spot, GEX profiles and closes ({store})",
            kind="chain",
            fields=("chain", "greeks", "spot", "profile", "closes"),
            cadence="1m" if "-60s" in store else "~15s",
            expiries=("0dte",),
            probe=Probe("store_cache", store, "vendor_sessions"),
            writer=_VENDOR,
            licensed=True,
            symbols=(store.split("-", 1)[0],),
            ongoing=False,
        )
        for store in (
            "SPX-60s.db",
            "SPX-60s-wide.db",
            "QQQ-60s-wide.db",
            "XSP-60s-wide.db",
            "SPX-15s.db",
            *(f"{sym}-60s-full.db" for sym in FULL_SET),
        )  # fmt: skip
    ),
    # The full set's intraday stores (backtest `fullset`), one pair per underlying.
    *(
        Source(
            name=f"vendor-{part}-{sym.lower()}",
            holds=f"{sym} {what} ({sym}-{part}-{cadence}.db)",
            kind="chain",
            fields=("chain", "greeks"),
            cadence=cadence,
            expiries=expiries,
            probe=Probe("store_cache", f"{sym}-{part}-{cadence}.db"),
            writer=_VENDOR,
            licensed=True,
            symbols=(sym,),
            ongoing=False,
        )
        for sym in FULL_SET
        for part, cadence, expiries, what in (
            ("near", "1m", ("1dte", "weekly"), "expiries 1-7 days out, each session at one minute"),
            (
                "mid",
                "5m",
                ("weekly", "monthly"),
                "monthlies 8-90 days out and weeklies to 45, at five minutes",
            ),
        )
    ),
    Source(
        name="vendor-eod-chains",
        holds="end-of-day option chains with weeklies, for the names studies requested",
        kind="chain",
        fields=("chain", "greeks", "iv"),
        cadence="1d",
        expiries=("all",),
        probe=Probe("store_cache", "eod_chains.db", "eod_chain_quotes"),
        writer=_VENDOR,
        licensed=True,
        ongoing=False,
    ),
    Source(
        name="vendor-intraday-chains",
        holds="intraday option chains for the names and instants studies requested",
        kind="chain",
        fields=("chain", "greeks"),
        cadence="1m",
        expiries=("all",),
        probe=Probe("store_cache", "intraday_chains.db"),
        writer=_VENDOR,
        licensed=True,
        ongoing=False,
    ),
    Source(
        name="vendor-oi",
        holds="open interest per contract per name and session",
        kind="chain",
        fields=("oi",),
        cadence="1d",
        expiries=("all",),
        probe=Probe("store_cache", "open_interest.db"),
        writer=_VENDOR,
        licensed=True,
        ongoing=False,
    ),
)

_OWN = "a module's own ledgers, caches and records"

#: Files under the data root that are deliberately not sources, by path prefix, with the reason.
IGNORED: dict[str, str] = {
    "backtest/": "the vendor's stores and every result: declared in backtest.stores",
    "marketdata/": "the stream cache: live quotes, kept only while fresh",
    "fundamentals/": "company financials, not market data",
    "market-report/analysts/": "analyst revision events, not market data",
    "market-report/splits/": "corporate actions for the adjustment, not prices",
    "market-report/browser-profile/": "a browser profile",
    "quikoptions/browser-profile/": "a browser profile",
    "technicals/board_history.db": "technicals' own readings, derived",
    "technicals/skate/": "technicals' own forward record",
    "research/": "a study's private store",
    "public/item": "a study's private store",
    "earnings/": "the earnings module's ledgers, and the Dolt server's own files",
    "scout.retired": "a retired module's cache",
    **{
        f"{m}/": _OWN
        for m in (
            "advisor",
            "bwb",
            "calendars",
            "console",
            "contango",
            "curve",
            "automations",
            "ensemble",
            "external",
            "flies",
            "ladder",
            "live",
            "manage",
            "meic",
            "outbound",
            "overview",
            "pmcc",
            "quikoptions",
            "review",
            "time-spreads",
            "weeklyput",
        )
    },  # fmt: skip
}


def by_name(name: str) -> Source:
    for s in SOURCES:
        if s.name == name:
            return s
    raise KeyError(f"no source {name!r}")


def providing(
    field: str, *, cadence: str | None = None, expiry: str | None = None, licensed: bool = False
) -> list[Source]:
    """The sources that can supply `field`: at `cadence` or finer, holding `expiry` (a source holding
    `all` holds every expiry; `weekly` is held by `all`, never by `monthly`), and licensed ones only
    when `licensed`."""
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}; one of {sorted(FIELDS)}")
    if cadence is not None and cadence not in CADENCE_SECONDS:
        raise ValueError(f"unknown cadence {cadence!r}; one of {list(CADENCE_SECONDS)}")
    if expiry is not None and expiry not in EXPIRIES:
        raise ValueError(f"unknown expiry {expiry!r}; one of {list(EXPIRIES)}")
    out = []
    for s in SOURCES:
        if field not in s.fields or (s.licensed and not licensed):
            continue
        if cadence is not None and CADENCE_SECONDS[s.cadence] > CADENCE_SECONDS[cadence]:
            continue
        if expiry is not None and expiry not in s.expiries and "all" not in s.expiries:
            continue
        out.append(s)
    return out


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


@functools.lru_cache(maxsize=1)
def _trading_list() -> tuple[str, ...]:
    """Every NYSE trading day from 1970 through 2040, ISO strings, in order: built once, then bisected,
    since an inventory counts the days of tens of thousands of spans."""
    d, end, out = date(1970, 1, 1), date(2040, 12, 31), []
    while d <= end:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return tuple(out)


def trading_days(first: str, last: str) -> int:
    """Trading days from `first` through `last`, both counted when they are trading days."""
    lo, hi = first[:10], last[:10]
    if hi < lo:
        return 0
    days = _trading_list()
    return bisect.bisect_right(days, hi) - bisect.bisect_left(days, lo)


#: A source covers a window only when it holds at least this share of the window's trading days.
DENSE = 0.95

#: A source ending at most this many trading days before the window's end still covers it, through its
#: last session: a store written the next morning (Dolt's, the vendor's end of day) is always a session
#: or two behind the last finished one.
LAG = 5


def sparse_years(span: Mapping, years: Mapping[str, int], days=trading_days) -> dict[str, int]:
    """The per-year session counts worth keeping for a span: all of them when any year inside the span
    holds fewer than `DENSE` of its trading days, else none (a dense span needs only its ends)."""
    for y, n in years.items():
        held = days(max(span["first"], f"{y}-01-01"), min(span["last"], f"{y}-12-31"))
        if held and n < DENSE * held:
            return dict(sorted(years.items()))
    return {}


def window_sessions(span: Mapping, lo: str, hi: str, days=trading_days) -> float:
    """Sessions a span holds between `lo` and `hi`. With no `years` kept (the inventory drops them for a
    dense span), its sessions are spread evenly over its span; a sparse one is read year by year, each
    year's count prorated over its share of that year's trading days inside the window (an estimate
    within a year, exact for whole years)."""
    years = span.get("years") or {}
    if not years:
        whole = days(span["first"], span["last"])
        return float(span.get("sessions") or 0) * days(lo, hi) / whole if whole else 0.0
    total = 0.0
    for y, n in years.items():
        y_lo, y_hi = max(span["first"], f"{y}-01-01"), min(span["last"], f"{y}-12-31")
        held = days(y_lo, y_hi)
        inside = days(max(lo, y_lo), min(hi, y_hi))
        if held and inside:
            total += n * inside / held
    return total


def summary(inventory: Mapping) -> dict:
    """One row per declared source, as the console lists them: the declaration, and from the inventory
    its state (`read`, `absent`, `skipped`, `error` or `not read`), why when it is not read, how many
    symbols it holds and the earliest and latest date across them. Pure over the inventory doc."""
    rows = []
    for s in SOURCES:
        e = (inventory.get("sources") or {}).get(s.name) or {}
        spans = list((e.get("symbols") or {}).values()) or ([e["store"]] if e.get("store") else [])
        if e.get("error"):
            state, reason = "error", e["error"]
        elif e.get("absent"):
            state, reason = "absent", e["absent"]
        elif e.get("skipped") and not spans:
            state, reason = "skipped", e["skipped"]
        elif e:
            state, reason = "read", None
        else:
            state, reason = "not read", "not inventoried yet"
        rows.append(
            {
                "name": s.name,
                "holds": s.holds,
                "kind": s.kind,
                "fields": list(s.fields),
                "cadence": s.cadence,
                "expiries": list(s.expiries),
                "licensed": s.licensed,
                "writer": s.writer,
                "ongoing": s.ongoing,
                "rolling": s.rolling,
                "note": s.note or None,
                "state": state,
                "reason": reason,
                "symbols": len(e.get("symbols") or {}) if e.get("symbols") else (len(s.symbols) or None),
                "first": min((x["first"] for x in spans if x.get("first")), default=None),
                "last": max((x["last"] for x in spans if x.get("last")), default=None),
                "read_at": e.get("read_at"),
            }
        )
    return {
        "generated_at": inventory.get("generated_at"),
        "sources": rows,
        "undeclared": list(inventory.get("undeclared") or []),
    }


def coverage(
    inventory: Mapping,
    field: str,
    symbol: str,
    start: str,
    end: str,
    *,
    cadence: str | None = None,
    expiry: str | None = None,
    licensed: bool = False,
    days: Callable[[str, str], int] = trading_days,
    min_density: float = DENSE,
    lag: int = LAG,
) -> dict:
    """Which sources hold `field` for `symbol` from `start` through `end`, read from an inventory doc
    (`inventory.run`'s). Each candidate comes back with its span and density (its sessions over the
    trading days in the part of the window it reaches); `covers` is a span reaching both ends with
    density at least `min_density` (`DENSE` unless a caller accepts a thinner source, as a weekly
    entry may). A
    span ending at most `lag` trading days before `end` still reaches it, and `through` is where it
    stops: a run over it ends there. A source the inventory has not read, or that holds nothing for
    the symbol, is listed with why.
    `covered` is whether any candidate covers; `best` is the first that does, the finest cadence first.
    Pure over its arguments."""
    symbol = symbol.upper()
    found = []
    for s in providing(field, cadence=cadence, expiry=expiry, licensed=licensed):
        entry = (inventory.get("sources") or {}).get(s.name) or {}
        base = {"source": s.name, "cadence": s.cadence, "licensed": s.licensed, "rolling": s.rolling}
        if not entry:
            found.append({**base, "covers": False, "why": "not inventoried yet"})
            continue
        if entry.get("error"):
            found.append({**base, "covers": False, "why": f"inventory failed: {entry['error']}"})
            continue
        if entry.get("absent") or entry.get("skipped"):
            found.append({**base, "covers": False, "why": entry.get("absent") or entry["skipped"]})
            continue
        spans = entry.get("symbols") or {}
        span = spans.get(symbol)
        unconfirmed = False
        if span is None and s.symbols and symbol in s.symbols:
            span = entry.get("store")
        elif span is None and not spans and not s.symbols and entry.get("store"):
            # A store whose cache keeps no per-symbol spans: its span, the symbol not confirmed.
            span, unconfirmed = entry["store"], True
        if not span or not span.get("first"):
            found.append({**base, "covers": False, "why": f"holds nothing for {symbol}"})
            continue
        first, last, sessions = span["first"], span["last"], span.get("sessions")
        # Density inside the window the request reaches, not over the whole span: a source sparse in
        # its early years can still cover a recent window.
        lo, hi = max(start, first), min(end, last)
        wanted = days(lo, hi)
        density = None
        if sessions is not None and wanted > 0:
            density = round(min(1.0, window_sessions(span, lo, hi, days) / wanted), 3)
        gaps = []
        if first > start:
            gaps.append(f"starts {first}, after {start}")
        behind = days(last, end) - 1 if last < end else 0  # sessions after its last, through `end`
        if behind > lag:
            gaps.append(f"ends {last}, before {end}")
        if density is not None and density < min_density:
            gaps.append(f"holds about {density:.0%} of the trading days from {lo} to {hi}")
        if unconfirmed:
            gaps.append(f"its spans are not kept per symbol, so {symbol} is not confirmed")
        found.append(
            {
                **base,
                "first": first,
                "last": last,
                "through": min(end, last),
                "sessions": sessions,
                "density": density,
                "covers": not gaps,
                "why": "; ".join(gaps) or None,
            }
        )
    found.sort(key=lambda x: (not x["covers"], CADENCE_SECONDS[x["cadence"]]))
    best = next((x["source"] for x in found if x["covers"]), None)
    return {
        "field": field,
        "symbol": symbol,
        "start": start,
        "end": end,
        "cadence": cadence,
        "expiry": expiry,
        "covered": best is not None,
        "best": best,
        "sources": found,
    }
