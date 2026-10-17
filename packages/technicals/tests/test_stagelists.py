"""Our stage lists (stagelists.py): which list a name is on, its order, the tradable filter, and the
score against the vendor's watchlists."""

from __future__ import annotations

import json

from cherrypick.technicals import paths, stagelists
from cherrypick.technicals.stage import Stage

L, G = "leader", "laggard"


def test_a_name_is_on_the_list_of_its_side_and_stage_and_only_if_tradable():
    stages = {
        "TSM": Stage(G, "early"),
        "PLTR": Stage(G, "early"),
        "THIN": Stage(G, "early"),  # not tradable: off every list
        "ECL": Stage(L, "early"),
        "KO": Stage(L, "confirmed"),
    }
    one_month = {"TSM": -0.03, "PLTR": -0.08, "THIN": -0.20, "ECL": 0.05, "KO": 0.02}
    lists = stagelists.lists_from(stages, one_month, {"TSM", "PLTR", "ECL", "KO"})
    assert lists["Early Breakdown"] == ["PLTR", "TSM"]  # weakest first
    assert lists["Early Breakout"] == ["ECL"]
    assert lists["Confirmed Outperformers"] == ["KO"]
    assert lists["Building Underperformance"] == []
    assert list(lists) == [n for n, _, _ in stagelists.LISTS]


def test_leaders_run_strongest_first():
    stages = {"A": Stage(L, "early"), "B": Stage(L, "early")}
    lists = stagelists.lists_from(stages, {"A": 0.02, "B": 0.09}, {"A", "B"})
    assert lists["Early Breakout"] == ["B", "A"]


def test_the_score_counts_only_names_we_hold_bars_for():
    stages = {"TSM": Stage(G, "early"), "PANW": Stage(G, "building"), "KO": Stage(L, "confirmed")}
    vendor = {"Early Breakdown": ["TSM", "PANW", "NOBARS"], "Confirmed Outperformers": ["KO"]}
    out = stagelists.score(stages, {"TSM", "PANW", "KO"}, vendor)
    assert out["lists"]["Early Breakdown"] == {"vendor": 3, "held": 2, "same_side": 2, "same_stage": 1}
    assert out["total"] == {"held": 3, "same_side": 3, "same_stage": 2, "side_rate": 1.0, "stage_rate": 0.667}


def test_the_vendors_saved_lists_are_read_by_session():
    path = paths.market_report_dir() / "vendor-watchlists" / "2026-10-16.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"session": "2026-10-16", "lists": [{"name": "Early Breakdown", "symbols": [{"symbol": "TSM"}]}]}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert stagelists.vendor_lists("2026-10-16") == {"Early Breakdown": ["TSM"]}
    assert stagelists.vendor_lists("2026-10-15") is None
