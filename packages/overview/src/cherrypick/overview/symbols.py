"""The overview's declared market-breadth symbol set, and its stream request registration.

The morning fact pack is a pure stream-cache consumer, so the breadth it reads has to be streamed
by the suite's single producer. This module declares that need through the same
``state/stream_requests/`` contract every module uses: quote-only symbols (no chains, no
expirations, no window hints), unioned by the streamer with everyone else's requests.

Two deliberate substitutions keep the package credential-free and inside what the streamer can do:

- **No futures.** The streamer's chain path is equity/index only, and the overview needs quotes,
  not chains -- so WTI and gold ride on their ETF proxies (USO, GLD), labeled as proxies in every
  reading that uses them. The report never claims a futures price it did not observe.
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

# Commodity proxies -- ETFs standing in for futures the streamer cannot subscribe. Every reading
# built from these carries the proxy label; the render never prints them as WTI/gold spot.
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
QUOTE_ONLY_SYMBOLS = tuple(sorted({*SECTOR_ETFS, *COMMODITY_PROXIES, *CREDIT_PROXIES,
                                   "VIX", "VIX3M", "VVIX"}))
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
HISTORY_DAYS = {
    symbol: HISTORY_LOOKBACK
    for symbol in ("VIX", "VIX3M", "HYG", "TLT", "SPX", *SECTOR_ETFS)
}


def register() -> str | None:
    """Write ``state/stream_requests/overview.json``. Returns the path written, or None.

    Best-effort by design: the fact pack degrades to unmeasured readings when a symbol is not in
    the cache, so a failed registration costs data, never a run. Note the streamer's restart
    staleness check tracks the ``symbols`` union -- the FIRST registration (or any change to this
    set) is a reason for the producer to recycle, which is expected and safe outside market hours.
    """
    try:
        return str(_requests.write_request(
            MODULE, UNDERLYING_SYMBOLS, legs=QUOTE_ONLY_SYMBOLS, history_days=HISTORY_DAYS,
        ))
    except OSError:
        return None
