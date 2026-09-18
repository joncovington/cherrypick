"""American physical settlement: the arithmetic two modules must not disagree about.

Deliberately small. `calendars` and `pmcc` both model physical delivery — pmcc's CLAUDE.md says it
uses "the calendars decomposition" — so the *money* has to be computed identically or the two
modules' paper results stop being comparable. That is the bar for putting something here.

**The disposal LOOPS are deliberately not here.** Both modules have a `_dispose_shares`/
`_dispose_longs` pass, and folding those in was considered and rejected: they differ in two real
ways (calendars filters on `back_expiration`; pmcc finalizes the position afterward), and what is
left once those are parameterized is I/O plumbing — the ledger query, the writer, the logger — that
would need roughly as many injection hooks as the twenty lines of logic it wrapped. An adapter
bigger than the duplication it removes is not a dedup. The spot reads inside those loops already
share one implementation via `cherrypick.core.streamcache`, and the fee stack already shares one via
`cherrypick.core.fees`, so what remained genuinely common is the function below.
"""

from __future__ import annotations

import asyncio

__all__ = ["OFFICIAL_SOURCES", "official_index_close", "share_pnl"]

# Which `official_index_close` sources count as a POSTED close a cash-settled ledger may settle on.
# Anything else -- an intraday last/mark tick tagged provisional, or nothing at all -- means the
# caller keeps retrying. "official" is the tag a hand-supplied `--price` carries.
OFFICIAL_SOURCES = frozenset({"official", "tastytrade_close", "yahoo", "barchart"})


def is_official_source(source: str | None) -> bool:
    return source in OFFICIAL_SOURCES


def share_pnl(direction: str, shares: int, basis: float, price: float) -> float:
    """Dollar P&L of a delivered share position disposed at `price`. Long earns the rise.

    Rounded to the cent because it is booked, not intermediate: the calendars derivation validates
    itself against the real books to the cent, so an unrounded value here would show up there as a
    validation failure rather than as the rounding difference it actually is.
    """
    move = price - basis if direction == "long" else basis - price
    return round(move * shares, 2)


_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cherrypick settlement fetch)"}
_HTTP_TIMEOUT_SECONDS = 8


async def _tastytrade_index_price(session, symbol: str) -> tuple[float | None, str | None]:
    """The index's own close (once posted), falling back to last/mark if `close` hasn't populated
    yet — tastytrade's `indices=[...]` market-data call, same REST endpoint flies' `fresh_option_quotes`
    uses for options. Never raises: any SDK/network error is treated as "this source came up
    empty," letting the caller move to the next one.

    Returns `(price, field)` — WHICH field answered, not just the number. Only `close` is a posted
    settlement value; `last`/`mark` are intraday prints that merely happen to be the most recent
    one. Callers must not label those "official" (2026-07-31: `close` was still None ~50 minutes
    after the bell, so an unconditional "official" label was asserting more than was established).
    """
    from tastytrade.market_data import get_market_data_by_type

    try:
        rows = await get_market_data_by_type(session, indices=[symbol])
    except Exception:  # noqa: BLE001 — one source of several; caller falls through on failure
        return None, None
    for row in rows:
        for field in ("close", "last", "mark"):
            candidate = getattr(row, field, None)
            if candidate is not None and float(candidate) > 0:
                return float(candidate), field
    return None, None


def _yahoo_index_price(symbol: str) -> float | None:
    """Yahoo Finance's public chart JSON endpoint (not HTML scraping — a plain structured API
    response), keyed by the `^`-prefixed index ticker. Blocking; called via `asyncio.to_thread`."""
    import json
    import urllib.request

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5E{symbol}?interval=1d&range=1d"
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            data = json.load(resp)
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        return float(price) if price else None
    except Exception:  # noqa: BLE001 — one source of several; caller falls through on failure
        return None


def _barchart_index_price(symbol: str) -> float | None:
    """Barchart's quote page embeds its data as inline JSON (`"lastPrice":"..."`) rather than
    requiring JS rendering — a plain regex pull, not a browser-driven scrape. Blocking; called via
    `asyncio.to_thread`. Last resort: no structured API, most exposed to the page changing shape."""
    import re
    import urllib.request

    url = f"https://www.barchart.com/stocks/quotes/${symbol}/performance"
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r'"lastPrice":"([0-9.]+)"', html)
        return float(match.group(1)) if match else None
    except Exception:  # noqa: BLE001 — last source; caller reports "no_source_available"
        return None


async def official_index_close(session, symbol: str) -> tuple[float | None, str]:
    """Best-effort fetch of the day's settlement/closing index level for `symbol` (XSP/SPX).
    Returns `(price, source)`, where `source` names not just WHICH provider answered but whether
    the value is authoritative:

        tastytrade_close / yahoo / barchart   -> a posted CLOSING value; safe to call official
        tastytrade_<field>_provisional        -> an intraday tick; caller MUST keep retrying
        no_source_available                   -> nothing answered; caller must not guess

    That distinction is the point (added 2026-07-31): this previously returned a bare "tastytrade"
    for any of close/last/mark, and the caller stamped every one of them `settlement_source =
    'official'` -- which also stopped the retry loop. On 2026-07-31 tastytrade's `close` was still
    None ~50 minutes after the bell, so the session was marked official off an intraday `last`.
    The caller must never guess a settlement price; that stays a hard refusal, same as a missing
    fresh option quote.

    Deliberately not gated behind a fixed post-close delay: the real settlement print isn't
    guaranteed to exist the instant the market closes, so this is designed to be called on every
    live tick until one succeeds (see flies' `live_loop.run_settle_live`), not just once."""
    # tastytrade's own posted CLOSE is the only genuinely-official reading available here, so it
    # goes first. If it hasn't populated, prefer Yahoo/Barchart -- their post-close quote IS the
    # closing print -- over tastytrade's intraday `last`/`mark`, which is merely the most recent
    # tick and can sit well away from the settlement value (2026-07-31, a month-end Friday: the
    # last streamed tick was 750.46 against a 748.97 close, a 15-SPX-point closing-auction move).
    price, field = await _tastytrade_index_price(session, symbol)
    if price is not None and field == "close":
        return price, "tastytrade_close"

    fallback = await asyncio.to_thread(_yahoo_index_price, symbol)
    if fallback is not None:
        return fallback, "yahoo"
    fallback = await asyncio.to_thread(_barchart_index_price, symbol)
    if fallback is not None:
        return fallback, "barchart"

    # Nothing authoritative answered. tastytrade's last/mark is better than no price at all, but
    # the caller MUST treat it as provisional and keep retrying -- see the caller's settle loop.
    if price is not None:
        return price, f"tastytrade_{field}_provisional"
    return None, "no_source_available"
