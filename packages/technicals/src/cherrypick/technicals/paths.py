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


def universe_candidates() -> Path:
    return market_report_dir() / "universe" / "candidates.json"
