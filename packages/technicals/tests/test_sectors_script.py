"""The Yahoo sector fetch (scripts/fetch_sectors.py) and how the earnings calendar uses it.

Each search response is the shape the endpoint sent on 2026-10-17.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from cherrypick.technicals import earnings_calendar as ec
from cherrypick.technicals import paths

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_sectors.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_sectors", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fs = _module()


def _quote(symbol, sector, industry="x", kind="EQUITY"):
    return {"symbol": symbol, "quoteType": kind, "sector": sector, "industry": industry}


def test_only_the_quote_that_is_the_name_counts_and_yahoos_labels_become_the_vendors():
    body = {
        "quotes": [
            _quote("GME", "Consumer Cyclical"),
            _quote("GM", "Consumer Cyclical", "Auto Manufacturers"),
        ]
    }
    assert fs.parse(body, "GM") == {"sector": "Consumer Discretionary", "industry": "Auto Manufacturers"}
    assert fs.parse({"quotes": [_quote("BRK-B", "Financial Services")]}, "BRK.B")["sector"] == "Financials"
    assert fs.parse({"quotes": [_quote("KO", "Consumer Defensive")]}, "KO")["sector"] == "Consumer Staples"
    assert fs.parse({"quotes": [_quote("CLF", "Basic Materials")]}, "CLF")["sector"] == "Basic Materials"
    # A search that never names the symbol itself is no sector, never a neighbour's.
    assert fs.parse({"quotes": [_quote("AGNCN", "Real Estate")]}, "AGNC") == {
        "sector": None,
        "industry": None,
    }
    assert fs.parse({}, "AGNC") == {"sector": None, "industry": None}


def test_a_name_is_asked_for_only_when_the_vendor_lacks_it_and_its_reading_is_old():
    today = date(2026, 10, 17)
    have = {
        "FRESH": {"sector": "Energy", "fetched_on": "2026-09-01"},
        "STALE": {"sector": "Energy", "fetched_on": "2026-03-01"},
        "NONE_NEW": {"sector": None, "fetched_on": "2026-10-10"},
        "NONE_OLD": {"sector": None, "fetched_on": "2026-09-01"},
    }
    names = ["VENDOR", "FRESH", "STALE", "NONE_NEW", "NONE_OLD", "NEW"]
    assert fs.due(names, have, {"VENDOR"}, today) == ["STALE", "NONE_OLD", "NEW"]


def test_the_calendar_takes_the_vendors_sector_first_and_yahoos_otherwise():
    universe = paths.market_report_dir() / "universe"
    universe.mkdir(parents=True, exist_ok=True)
    (universe / "sectors.json").write_text(
        json.dumps({"sectors": {"CB": {"sector": "Financials"}}}), encoding="utf-8"
    )
    yahoo = {
        "names": {
            "CB": {"sector": "Industrials", "fetched_on": "2026-10-17"},
            "GM": {"sector": "Consumer Discretionary", "fetched_on": "2026-10-17"},
            "AGNC": {"sector": None, "fetched_on": "2026-10-17"},
        }
    }
    paths.yahoo_sectors().write_text(json.dumps(yahoo), encoding="utf-8")
    assert ec._sector_map() == {"CB": ("Financials", "vendor"), "GM": ("Consumer Discretionary", "yahoo")}
