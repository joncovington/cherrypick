"""The security list (securities.py): the public rule, and which list a reader gets.

The labels and ranks are the ones the 2026-10-16 comparison with the vendor's list settled on.
"""

from __future__ import annotations

import json

from cherrypick.core import home

from cherrypick.technicals import paths, securities

VENDOR_ON = {"market_report": {"collector": True, "vendor_dashboard_url": "x", "vendor_edition_title": "y"}}


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_securitys_type_comes_from_its_directory_name_and_etf_flag():
    k = securities.kind
    assert k("Apple Inc. - Common Stock", False) == "Stock"
    assert k("Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares", False) == (
        "Depository Receipt"
    )
    assert k("PayPay Corporation - American Depository Shares", False) == "Depository Receipt"
    assert k("SPDR S&P 500 ETF Trust", True) == "Exchange Traded Fund"
    assert k("iPath Series B Carbon Exchange-Traded Notes", True) == "Structured Product"
    # A preferred depositary share is preferred, not a receipt.
    assert k("Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock", False) == (
        "Preferred Shares"
    )
    assert k("Acme Acquisition Corp - Units", False) == "Units"


def test_the_rank_needs_both_option_volume_and_stock_dollar_volume():
    r = securities.rank
    assert r(2_000, 300e6) == 1
    assert r(1_999, 900e6) == 2  # deep stock, too few contracts for 1
    assert r(50_000, 299e6) == 2
    assert r(200, 100e6) == 2
    assert r(199, 900e6) == 3
    assert r(50_000, 99e6) == 3
    assert r(None, 500e6) == 3  # no option volume is none
    assert r(50_000, None) is None  # no stock volume: unknown, never guessed


def test_the_directory_spelling_becomes_the_brokers_and_occs():
    assert securities.vendor_spelling("BRK.B") == "BRK/B"
    assert securities.vendor_spelling("ABR$D") == "ABR/PR/D"
    assert securities.occ_spelling("BRK.B") == "BRKB"


def test_a_session_an_underlying_is_absent_from_counts_as_no_volume():
    """OCC lists every underlying that traded; one missing from a session traded none of it."""
    directory = [{"symbol": "AAA", "name": "Aaa Inc. Common Stock", "etf": False}]
    contracts = {"AAA": [5000], "ZZZ": [10, 10, 10]}  # AAA traded in one of three sessions
    (row,) = securities.public_rows(directory, contracts, {"AAA": 500e6})
    assert row["option_contracts"] == 0 and row["hasOptions"] is True and row["liquidityRank"] == 3


def test_the_vendors_list_is_read_while_it_is_on_and_current():
    pick = securities.choose("2026-10-19", True, ["2026-10-16"], ["2026-10-19"])
    assert (pick["source"], pick["day"]) == ("vendor", "2026-10-16")  # Friday's list on Monday


def test_a_stale_vendor_list_falls_back_and_says_why():
    """A failed sign-in leaves the last list behind; every reader used to keep reading it."""
    pick = securities.choose("2026-10-26", True, ["2026-10-21"], ["2026-10-26"])
    assert (pick["source"], pick["day"]) == ("public", "2026-10-26")
    assert "5 days old" in pick["reason"]


def test_off_means_public_even_with_a_fresh_vendor_list_on_disk():
    pick = securities.choose("2026-10-16", False, ["2026-10-16"], ["2026-10-16"])
    assert pick["source"] == "public" and "off" in pick["reason"]


def test_no_list_at_all_is_said_not_guessed():
    assert securities.choose("2026-10-16", False, [], [])["source"] is None


def test_lists_dated_after_the_day_are_never_read():
    pick = securities.choose("2026-10-16", True, ["2026-10-17"], ["2026-10-15"])
    assert (pick["source"], pick["day"]) == ("public", "2026-10-15")


def test_latest_follows_the_suite_configs_switch():
    _write(paths.vendor_securities_dir() / "2026-10-16.json", {"rows": [{"symbol": "V", "type": "Stock"}]})
    _write(paths.securities_dir() / "2026-10-16.json", {"rows": [{"symbol": "P", "type": "Stock"}]})
    rows, pick = securities.latest("2026-10-16")
    assert [r["symbol"] for r in rows] == ["P"] and pick["source"] == "public"  # no suite config: off
    _write(home.config_path(), VENDOR_ON)
    rows, pick = securities.latest("2026-10-16")
    assert [r["symbol"] for r in rows] == ["V"] and pick["source"] == "vendor"


def test_the_comparison_scores_the_top_two_set():
    public = [
        {"symbol": "A", "type": "Stock", "hasOptions": True, "liquidityRank": 1},
        {"symbol": "B", "type": "Stock", "hasOptions": True, "liquidityRank": 2},
        {"symbol": "C", "type": "Exchange Traded Fund", "hasOptions": False, "liquidityRank": 3},
    ]
    vendor = [
        {"symbol": "A", "type": "Stock", "hasOptions": True, "liquidityRank": 2},
        {"symbol": "B", "type": "Depository Receipt", "hasOptions": True, "liquidityRank": 3},
        {"symbol": "C", "type": "Exchange Traded Fund", "hasOptions": False, "liquidityRank": 1},
    ]
    out = securities.compare(public, vendor)
    assert out["type_agreement"] == round(2 / 3, 4) and out["options_agreement"] == 1.0
    assert out["top_two"] == {"ours": 2, "vendor": 2, "both": 1, "precision": 0.5, "recall": 0.5}


def test_the_build_writes_the_list_and_the_pointer(monkeypatch):
    occ = paths.market_files_dir() / "occ"
    header = (
        "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue"
    )
    lines = [header] + [
        f"Y|S{i}|Stock {i} Common Stock|N| |N|100|N" for i in range(securities.MIN_DIRECTORY_ROWS)
    ]
    lines += ["Y|AAPL|Apple Inc. - Common Stock|Q|Q|N|100|N", "Y|TEST|Test Issue|Q|Q|N|100|Y"]
    lines += ["File Creation Time: 1016202618:00|||||||"]
    occ.mkdir(parents=True, exist_ok=True)
    (occ / "nasdaqtraded.txt").write_text("\n".join(lines), encoding="utf-8")
    for s, q in (("2026-10-15", 6000), ("2026-10-16", 4000)):
        _write(occ / f"{s}.json", {"session": s, "underlyings": {"AAPL": [q, 0, 0, 0, q, 0, 0, 0]}})
    monkeypatch.setattr(securities, "_dolt_dollars", lambda cfg, day: ({"AAPL": 9e9}, "2026-10-16"))
    out = securities.build(day="2026-10-16")
    assert out["ok"] and out["current"]["source"] == "public"
    doc = json.loads((paths.securities_dir() / "2026-10-16.json").read_text(encoding="utf-8"))
    rows = {r["symbol"]: r for r in doc["rows"]}
    assert "TEST" not in rows
    # 6,000 and 4,000 sides a session each way: 6,000 and 4,000 contracts, median 5,000.
    assert (rows["AAPL"]["option_contracts"], rows["AAPL"]["liquidityRank"]) == (5000, 1)
    assert doc["inputs"]["directory_as_of"] == "2026-10-16"
    ptr = json.loads((paths.securities_dir() / "current.json").read_text(encoding="utf-8"))
    assert (ptr["source"], ptr["day"], ptr["vendor_on"]) == ("public", "2026-10-16", False)
