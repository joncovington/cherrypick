"""Data-home paths for cherrypick-fundamentals, through :mod:`cherrypick.core.home`.

The package writes only its own store (``~/.cherrypick/data/fundamentals`` or ``FUNDAMENTALS_DATA_DIR``):
`fundamentals.db` (SEC facts, Dolt's annual statements, the revenue consensus) and the fair-value
files. The SEC and Damodaran files under it are written by scripts/fetch_sec_facts.py and
scripts/fetch_damodaran.py. It reads, read-only, the technicals store's bars (through the technicals
package) and the local Dolt server.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "fundamentals"


def data_dir() -> Path:
    return _home.data_dir(PACKAGE, env="FUNDAMENTALS_DATA_DIR")


def db() -> Path:
    """The package's store: `sec_facts`, `sec_filers`, `statements_annual`, `sales_consensus`."""
    return data_dir() / "fundamentals.db"


def valuation_dir() -> Path:
    """One fair-value file a session, `<session>.json` (valuation.py). The console and the technicals
    income lists read it by path."""
    return data_dir() / "valuation"


def study_dir() -> Path:
    return data_dir() / "study"


def sec_dir() -> Path:
    """SEC EDGAR's public files, one company at a time: `tickers.json` (ticker to CIK),
    `submissions/<CIK>.json` (the filing index) and `facts/<CIK>.json.gz` (every XBRL fact the company
    has filed), with `fetched.json`. Written by scripts/fetch_sec_facts.py; `secfacts.py` lands them."""
    return data_dir() / "sec"


def damodaran_dir() -> Path:
    """Aswath Damodaran's public valuation inputs: `erp.json` (the monthly implied equity risk premium)
    and `ratings.json` (interest coverage to synthetic rating and default spread). Written by
    scripts/fetch_damodaran.py; `damodaran.py` reads them."""
    return data_dir() / "damodaran"
