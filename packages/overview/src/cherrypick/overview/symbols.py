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

ALL_SYMBOLS = tuple(sorted({*INDEX_SYMBOLS, *SECTOR_ETFS, *COMMODITY_PROXIES}))


def register() -> str | None:
    """Write ``state/stream_requests/overview.json``. Returns the path written, or None.

    Best-effort by design: the fact pack degrades to unmeasured readings when a symbol is not in
    the cache, so a failed registration costs data, never a run. Note the streamer's restart
    staleness check tracks the ``symbols`` union -- the FIRST registration (or any change to this
    set) is a reason for the producer to recycle, which is expected and safe outside market hours.
    """
    try:
        return str(_requests.write_request(MODULE, ALL_SYMBOLS))
    except OSError:
        return None
