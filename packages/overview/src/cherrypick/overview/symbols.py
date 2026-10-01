"""The overview's declared market-breadth symbol set, and its stream request registration.

The morning fact pack is a pure stream-cache consumer, so the breadth it reads has to be streamed
by the suite's single producer. This module declares that need through the same
``state/stream_requests/`` contract every module uses: quote-only symbols (no chains, no
expirations, no window hints), unioned by the streamer with everyone else's requests.

Two deliberate limits keep the package credential-free and inside what the streamer can do:

- **Futures only through the contract map.** A future is quoted as a leg, and its contract comes
  from `state/futures_contracts.json` (written outside the package, by a script holding the
  credential). WTI and gold are read both ways: as /CL and /GC on the pre-market tape, and as their
  ETF proxies (USO, GLD), labeled as proxies, which still carry a reading when the map is stale.
  The report never claims a futures price it did not observe.
- **No IV rank.** tastytrade market metrics need a credential; this package has none. The reading
  is simply absent rather than sourced through a side door.
"""

from __future__ import annotations

from cherrypick.core import streamrequests as _requests

MODULE = "overview"

# The index complex. SPX is already streamed by half the suite; the vol symbols are this module's
# own need. VIX3M pairs with VIX for the contango gate; VVIX is the vol-of-vol stress line.
INDEX_SYMBOLS = ("SPX", "VIX", "VIX3M", "VVIX")

# One ETF per GICS sector -- prior-session strongest/weakest come from these.
SECTOR_ETFS = {
    "XLB": "Materials",
    "XLC": "Communication",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLK": "Technology",
    "XLP": "Staples",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLV": "Health Care",
    "XLY": "Discretionary",
}

# Commodity proxies -- ETFs standing in for crude and gold. The futures themselves (/CL, /GC) are
# on the pre-market tape below; the proxies stay because they need no contract map, and because the
# narrative and older packs read them. Every reading built from these carries the proxy label; the
# render never prints them as WTI/gold spot.
COMMODITY_PROXIES = {
    "USO": "WTI crude (ETF proxy)",
    "GLD": "Gold (ETF proxy)",
}

# Credit proxies for the deployment score's credit signal -- ETFs standing in for the cash
# high-yield and long-Treasury markets, same posture as the commodity proxies: the score reads a
# HYG/TLT ratio z-score and labels it a proxy, never an OAS the suite did not observe.
CREDIT_PROXIES = {
    "HYG": "High-yield credit (ETF proxy)",
    "TLT": "20+yr Treasuries (ETF proxy)",
}

ALL_SYMBOLS = tuple(sorted({*INDEX_SYMBOLS, *SECTOR_ETFS, *COMMODITY_PROXIES, *CREDIT_PROXIES}))

# --------------------------------------------------------------------------- what we ask the producer for
#
# **This package needs quotes, and `symbols` does not mean quotes.** In the streamer's contract a
# `symbols` entry is an UNDERLYING: it brings a spot subscription, an ATM window, GEX and an option
# chain fetch that repeats every subscription poll. Declaring the breadth set there had the producer
# maintaining 0DTE chains for eleven sector ETFs, VIX, GLD, USO, HYG and TLT -- roughly 1,700 option
# symbols nothing in this suite reads -- which starved the modules that trade. `legs` is the
# quote-only field (a static list of streamer symbols, subscribed as-is, no chain machinery), so the
# breadth rides there.
#
# SPX stays an underlying because it genuinely is one for half the suite; the union means this
# package's entry costs nothing extra.
# The other cash indexes beside SPX (added 2026-09-27): quote-only legs, each a few subscriptions.
# RUT is not here on purpose: the stream delivers nothing for it (no trade, no quote, and "no
# candles" on backfill, 2026-09-27) although tastytrade's REST quotes it, so the Russell 2000's cash
# read rides on IWM, labelled a proxy exactly as USO and GLD are. /RTY carries the pre-market.
INDEX_LEGS = {
    "ndx": ("NDX", "Nasdaq-100 (NDX)"),
    "djx": ("DJX", "Dow Jones / 100 (DJX)"),
    "iwm": ("IWM", "Russell 2000 proxy (IWM ETF, not the RUT index)"),
}

QUOTE_ONLY_SYMBOLS = tuple(
    sorted(
        {
            *SECTOR_ETFS,
            *COMMODITY_PROXIES,
            *CREDIT_PROXIES,
            "VIX",
            "VIX1D",
            "VIX3M",
            "VVIX",
            *(symbol for symbol, _ in INDEX_LEGS.values()),
        }
    )
)

# The pre-market tape (added 2026-09-27): reading -> (product code, label). The CONTRACT is never
# assembled here -- it comes from `state/futures_contracts.json`, written by
# scripts/refresh_futures_contracts.py from the broker's instruments endpoint, exactly as the gex
# recorder reads it. Crude (CL), Brent (BZ) and gold (GC, added 2026-10-01) are real futures, so
# unlike USO and GLD they are not proxies.
PREMARKET_FUTURES = {
    "es": ("ES", "S&P 500 e-mini (/ES)"),
    "nq": ("NQ", "Nasdaq-100 e-mini (/NQ)"),
    "ym": ("YM", "Dow e-mini (/YM)"),
    "rty": ("RTY", "Russell 2000 e-mini (/RTY)"),
    "cl": ("CL", "WTI crude (/CL)"),
    "bz": ("BZ", "Brent crude (/BZ)"),
    "gc": ("GC", "Gold (/GC)"),
}

# A map older than this names contracts that may have rolled; the same bound the gex recorder uses.
# Stale or missing is simply no futures legs and no futures readings -- a legible gap, never a
# rolled-off contract read as the market.
FUTURES_MAP_MAX_AGE_DAYS = 5


def futures_legs(now=None) -> dict[str, str]:
    """`{reading: streamer_symbol}` for the pre-market futures, or `{}` when the map is missing,
    unreadable or stale."""
    import json
    from datetime import datetime

    from cherrypick.core import home as _home
    from cherrypick.core.clock import ET

    try:
        raw = json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))
        refreshed = datetime.fromisoformat(str(raw["refreshed_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if ((now or datetime.now(ET)) - refreshed).days > FUTURES_MAP_MAX_AGE_DAYS:
        return {}
    out = {}
    for reading, (product, _label) in PREMARKET_FUTURES.items():
        rows = (raw.get("contracts") or {}).get(product) or []
        if rows and rows[0].get("streamer_symbol"):
            out[reading] = str(rows[0]["streamer_symbol"])
    return out


UNDERLYING_SYMBOLS = ("SPX",)

# Completed daily rows the deployment score needs stream_summary to hold. 270 covers a trailing
# 252-session year for the VIX percentile / HYG-TLT z-score with slack, and the sector breadth's
# 200-day SMA inside the same number.
#
# **Deliberately not the ~4 years the zone backtest would prefer.** A backfill is a burst of writes
# into the cache every live consumer is reading, and 16 symbols x 1000 days did not merely cost more
# -- it never finished. Each reconnect restarted it from the top, so the producer spent its life
# re-fetching four years of candles and crash-looping on a locked database, and every module's
# quotes went stale behind it. The backtest reports a short history honestly; a starved producer is
# not a trade-off worth making for a record-only score. Raise this only deliberately, off-hours,
# and watch the producer while it lands.
HISTORY_LOOKBACK = 270

# Only the symbols whose series the score actually reads. VIX3M is here for the backtest alone --
# the live score takes it from a current quote, but a historical day needs its close.
HISTORY_DAYS = {symbol: HISTORY_LOOKBACK for symbol in ("VIX", "VIX3M", "HYG", "TLT", "SPX", *SECTOR_ETFS)}

# The pre-market legs need only their LAST settle, but the producer writes live Summary rows for
# underlyings alone: a leg's daily rows come solely from the connect-time candle backfill, and only
# for a leg that declares `history_days`. Found on the first live run (2026-09-27): all nine new legs
# printed within seconds and none ever got a settle row. Thirty sessions keeps the last settle on
# file across a long weekend or a missed connect, at ~270 candles once -- small beside the 270-day
# requests above.
PREMARKET_HISTORY_DAYS = 30


def _history_days(now=None) -> dict[str, int]:
    premarket = [*futures_legs(now).values(), *(symbol for symbol, _ in INDEX_LEGS.values())]
    return {**{symbol: PREMARKET_HISTORY_DAYS for symbol in premarket}, **HISTORY_DAYS}


def register() -> str | None:
    """Write ``state/stream_requests/overview.json``. Returns the path written, or None.

    Best-effort by design: the fact pack degrades to unmeasured readings when a symbol is not in
    the cache, so a failed registration costs data, never a run. Note the streamer's restart
    staleness check tracks the ``symbols`` union -- the FIRST registration (or any change to this
    set) is a reason for the producer to recycle, which is expected and safe outside market hours.
    """
    try:
        return str(
            _requests.write_request(
                MODULE,
                UNDERLYING_SYMBOLS,
                legs=tuple(sorted({*QUOTE_ONLY_SYMBOLS, *futures_legs().values()})),
                history_days=_history_days(),
            )
        )
    except OSError:
        return None
