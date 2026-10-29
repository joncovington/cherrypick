"""Data-home paths for cherrypick-technicals, through :mod:`cherrypick.core.home`.

The package writes only its own store (``~/.cherrypick/data/technicals`` or ``TECHNICALS_DATA_DIR``).
It reads, read-only, the market-report store the scripts fill (the universe candidates) and the
local Dolt server.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "technicals"


def data_dir() -> Path:
    return _home.data_dir(PACKAGE, env="TECHNICALS_DATA_DIR")


def eod_db() -> Path:
    """The end-of-day store: raw bars, splits, dividends and IV, as Dolt last stated them."""
    return data_dir() / "eod.db"


def history_db() -> Path:
    """The historical study's research store (docs/signal-log-plan.md, Phase 1): Dolt's whole daily
    history in the same schema as `eod.db`, kept apart so the nightly store neither slows nor grows.
    Rebuildable from Dolt at any time."""
    return data_dir() / "history.db"


def study_dir() -> Path:
    """The historical study's results, one versioned file per run."""
    return data_dir() / "study"


def market_report_dir() -> Path:
    """Where scripts/build_stock_universe.py and the vendor collector write. Read-only here."""
    return _home.data_dir("market-report")


def market_files_dir() -> Path:
    """The morning pack's daily files (scripts/fetch_market_files.py): OCC's option volume and
    Nasdaq Trader's symbol directory among them. Read-only here."""
    return _home.data_dir("market-files")


def securities_dir() -> Path:
    """The public security list this package builds (`securities.py`), and the pointer naming which
    list -- public or the vendor's -- is current."""
    return market_report_dir() / "securities"


def vendor_securities_dir() -> Path:
    """The vendor's nightly security list, saved by the collector. Read-only here."""
    return market_report_dir() / "vendor-securities"


def tastytrade_dividends() -> Path:
    """Tastytrade's dividend history, written by scripts/fetch_dividends.py. Read-only here."""
    return market_report_dir() / "dividends" / "tastytrade.json"


def split_history() -> Path:
    """Splits fetched from a public source for symbols Dolt's split table misses, written by
    scripts/fetch_split_history.py. Read-only here."""
    return market_report_dir() / "splits" / "split_history.db"


def tastytrade_iv_rank() -> Path:
    """Tastytrade's IV rank by session, written by scripts/fetch_iv_rank.py. Read-only here."""
    return market_report_dir() / "iv" / "tastytrade.json"


def dollar_volume_dir() -> Path:
    """Each name's 50-session median dollar volume from the broker's daily candles, one file a
    session (`<day>.json`), written by scripts/fetch_dollar_volume.py. Read-only here."""
    return market_report_dir() / "dollar-volume"


def earnings_times_dir() -> Path:
    """Nasdaq's public earnings calendar's time of day per report date, one file a session
    (`<day>.json`), written by scripts/fetch_earnings_times.py. The earnings calendar reads it.
    Read-only here."""
    return market_report_dir() / "earnings" / "times"


def yahoo_sectors() -> Path:
    """Yahoo's sector per name, relabelled to the vendor's sector names, for the earnings calendar
    names the vendor's sector map lacks. Written by scripts/fetch_sectors.py. Read-only here."""
    return market_report_dir() / "universe" / "sectors.yahoo.json"


def expiry_iv_dir() -> Path:
    """Tastytrade's implied vol per listed expiration, one file a session (`<day>.json`), written
    by scripts/fetch_iv_rank.py alongside the rank. The earnings calendar's expected move reads it.
    Read-only here."""
    return market_report_dir() / "iv" / "expiry-iv"


def index_bars() -> Path:
    """Daily bars for the cash indexes Dolt does not carry (SPX), from the broker's daily candles,
    written by scripts/fetch_index_bars.py. Read-only here."""
    return market_report_dir() / "index" / "bars.db"


def analyst_revisions() -> Path:
    """Per-firm rating and price-target events, written by scripts/fetch_analyst_revisions.py.
    Read-only here."""
    return market_report_dir() / "analysts" / "revisions.db"


def yahoo_adjusted() -> Path:
    """Yahoo's daily closes and adjusted closes for every stored name, about a year each, replaced
    whole per name on every fetch (an adjusted close is restated at each later dividend). Written
    by scripts/fetch_yahoo_adjusted.py; `adjust_check` reads it. Read-only here."""
    return market_report_dir() / "adjusted" / "yahoo.db"


def universe_candidates() -> Path:
    return market_report_dir() / "universe" / "candidates.json"


def fair_value_dir() -> Path:
    """packages/fundamentals' fair-value files, one a session. Read here by path, never by import:
    fundamentals reads this package for prices, so an import back would be a cycle. Read-only here."""
    return _home.data_dir("fundamentals", env="FUNDAMENTALS_DATA_DIR") / "valuation"
