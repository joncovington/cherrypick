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


def market_report_dir() -> Path:
    """Where scripts/build_stock_universe.py and the vendor collector write. Read-only here."""
    return _home.data_dir("market-report")


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


def universe_candidates() -> Path:
    return market_report_dir() / "universe" / "candidates.json"
