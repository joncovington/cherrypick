"""The signal board: a name's count of agreeing methods, one side listed, scored on the saved boards."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from cherrypick.technicals import board, revisions, rotation, stage
from cherrypick.technicals.board import Tag
from cherrypick.technicals.editions import BoardRow
from cherrypick.technicals.revisions import Event


def ev(action="down", firm="Barclays", prior=100.0, current=100.0, at="2026-10-16T13:00:00+00:00"):
    return Event("X", datetime.fromisoformat(at), firm, "Buy", "Hold", action, prior, current)


def test_a_rows_side_is_the_side_with_more_tags_and_only_its_tags_are_listed():
    tags = [
        Tag("stage", 1, "Early breakout"),
        Tag("rotation", 1, "Money rotating into the sector", "XLK leading"),
        Tag("revision", -1, "Analyst bearish revision", "Barclays downgrade"),
    ]
    r = board.row("AAA", tags, "Technology")
    assert (r["side"], r["count"], r["bullish"], r["bearish"]) == ("bullish", 2, 2, 1)
    assert [t["method"] for t in r["tags"]] == ["stage", "rotation"]
    # A tie has no side and lists nothing, though both counts are kept.
    tie = board.row("TIE", tags[1:], None)
    assert (tie["side"], tie["count"], tie["tags"]) == (None, 1, [])


def test_the_revision_detail_names_the_deciding_event():
    rating = revisions.Tag(-1, (ev("main", "Citigroup", 100, 120), ev("down", "Barclays")), "rating")
    assert board.revision_detail(rating) == "Barclays downgrade"
    target = revisions.Tag(1, (ev("main", "Citigroup", 100, 113),), "target")
    assert board.revision_detail(target) == "Citigroup target +13%"


def test_the_vendors_tag_words_map_to_our_four_methods_and_nothing_else():
    tags = (
        "Early Breakdown", "Building Outperformance", "Confirmed Underperformers",
        "Money rotating out of the sector", "Bearish Trade Scan", "Analyst Bearish Revision",
        "Weak fundamentals", "Price action ahead of the fundamentals", "Institutional Put Buying",
    )  # fmt: skip
    assert board.vendor_methods(tags) == {"stage", "rotation", "scan", "revision"}
    assert board.vendor_methods(("Compelling fundamentals", "Institutional Call Buying")) == set()


def test_scoring_counts_only_our_tags_on_the_rows_side():
    boards = {
        "2026-10-14": {
            "BULL": BoardRow(
                "bullish",
                ("Early Breakout", "Money rotating into the sector", "Compelling fundamentals"),
                0,
                "",
            ),
            "BEAR": BoardRow(
                "bearish", ("Analyst Bearish Revision", "Weak fundamentals", "Early Breakdown"), -1, "x"
            ),
        }
    }
    ours = {
        "2026-10-14": {
            # Stage found; rotation is on the wrong side, so neither found nor extra; a scan extra.
            "BULL": [Tag("stage", 1, ""), Tag("rotation", -1, ""), Tag("scan", 1, "")],
            # Revision found; stage missed; an opposite-side scan is not an extra.
            "BEAR": [Tag("revision", -1, ""), Tag("scan", 1, "")],
        }
    }
    s = board.score_boards(ours, boards)
    m = s["methods"]
    assert m["stage"] == {"found": 1, "tagged": 2, "extra": 0}
    assert m["rotation"] == {"found": 0, "tagged": 1, "extra": 0}
    assert m["scan"] == {"found": 0, "tagged": 0, "extra": 1}
    assert m["revision"] == {"found": 1, "tagged": 1, "extra": 0}
    # BULL's net is bullish (2 vs 1), BEAR's is a tie (1 vs 1): one row on the board's side.
    assert (s["rows"], s["same_side"]) == (2, 1)


def test_tags_on_reads_each_engine_on_the_day(monkeypatch):
    monkeypatch.setattr(
        stage, "stages_on", lambda day, closes, bench: {"AAA": stage.Stage("leader", "building")}
    )
    monkeypatch.setattr(
        rotation,
        "states_on",
        lambda day, closes, funds: {"XLK": "leading", "XLE": "weakening", "XLV": "improving"},
    )
    bar = SimpleNamespace(date="2026-10-15", high=1.0, low=1.0, close=1.0)
    bars = {"AAA": [bar], "BBB": [bar], "CCC": [bar]}
    sectors = {"AAA": "Technology", "BBB": "Energy", "CCC": "Healthcare"}
    rev = {"BBB": revisions.Tag(-1, (ev(),), "rating")}
    out = board.tags_on("2026-10-16", bars, {}, {}, sectors, rev)
    assert [(t.method, t.side, t.label) for t in out["AAA"]] == [
        ("stage", 1, "Building outperformance"),
        ("rotation", 1, "Money rotating into the sector"),
    ]
    assert [(t.method, t.side) for t in out["BBB"]] == [("rotation", -1), ("revision", -1)]
    # Improving is not the vendor's "into"; no other method fired, so no row at all.
    assert "CCC" not in out
