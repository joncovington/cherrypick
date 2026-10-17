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
    assert board.vendor_methods(tags) == {"stage", "rotation", "scan", "revision", "fundamentals"}
    # Fundamentals-vs-price is its own method, not counted; flow is paid data.
    assert (
        board.vendor_methods(("Fundamentals ahead of the price action", "Institutional Call Buying")) == set()
    )


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


def test_the_scan_counts_trend_following_on_the_session_or_the_one_before(monkeypatch):
    from cherrypick.technicals import signals

    monkeypatch.setattr(stage, "stages_on", lambda day, closes, bench: {})
    monkeypatch.setattr(rotation, "states_on", lambda day, closes, funds: {})
    # The last close of the bars read stands in for the session's readings; each fires one rule.
    fired = {
        "PREV": {1.0: "BullishTrendFollowing"},  # the session before: counts
        "OLD": {0.5: "BearishTrendFollowing"},  # two sessions back: outside the window
        "CCI": {2.0: "CciDipInBullishTrend"},  # a CCI dip on the day: not a trend-following rule
        "BOTH": {1.0: "BullishTrendFollowing", 2.0: "BearishTrendFollowing"},  # sides disagree: none
    }
    monkeypatch.setattr(signals, "readings", lambda highs, lows, closes: closes[-1])
    current = {}
    monkeypatch.setattr(signals, "matches", lambda r: [current["rules"][r]] if r in current["rules"] else [])
    days = ["2026-10-14", "2026-10-15", "2026-10-16"]
    out = {}
    for sym, rules in fired.items():
        current["rules"] = rules
        bars = {
            sym: [
                SimpleNamespace(date=d, high=1.0, low=1.0, close=c)
                for d, c in zip(days, (0.5, 1.0, 2.0), strict=True)
            ]
        }
        out.update(board.tags_on("2026-10-16", bars, {}, {}, {}, {}))
    assert [(t.side, t.detail) for t in out["PREV"]] == [(1, "BullishTrendFollowing 2026-10-15")]
    assert "OLD" not in out and "CCI" not in out and "BOTH" not in out


def test_fundamentals_tags_the_market_tails_only(monkeypatch):
    monkeypatch.setattr(stage, "stages_on", lambda day, closes, bench: {})
    monkeypatch.setattr(rotation, "states_on", lambda day, closes, funds: {})
    bar = SimpleNamespace(date="2026-10-15", high=1.0, low=1.0, close=1.0)
    bars = {s: [bar] for s in ("TOP", "EDGE", "MID", "LOW", "NONE")}
    pct = {"TOP": 0.93, "EDGE": board.WEAK_AT, "MID": 0.5, "LOW": 0.05}
    out = board.tags_on("2026-10-16", bars, {}, {}, {}, {}, pct)
    assert [(t.label, t.detail) for t in out["TOP"]] == [
        ("Compelling fundamentals", "score at the 93rd percentile")
    ]
    assert [t.side for t in out["EDGE"]] == [-1], "the cut-off itself is in the tail"
    assert [t.label for t in out["LOW"]] == ["Weak fundamentals"]
    assert "MID" not in out and "NONE" not in out


def test_market_percentiles_rank_every_scored_name_across_the_market(tmp_path):
    from cherrypick.technicals import fundamentals

    conn = fundamentals.connect(tmp_path / "history.db")
    snap = "2026-10-11"
    # Six names in one sector, each better than the last on every measure: cheaper, faster growing,
    # higher margin. A seventh has no price and is not scored.
    for i, sym in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
        conn.execute("INSERT INTO estimates VALUES (?, ?, 'eps', NULL, ?, ?)", (sym, snap, 2.0 + i, 1.0))
        conn.execute(
            "INSERT INTO estimates VALUES (?, ?, 'sales', NULL, ?, ?)", (sym, snap, 100.0 + 10 * i, 90.0)
        )
        for q in ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"):
            conn.execute("INSERT INTO quarters VALUES (?, ?, 25.0, ?, 10.0)", (sym, q, 1.0 + i))
        if sym != "G":
            conn.execute("INSERT INTO bars VALUES (?, '2026-10-09', 50, 50, 50, 50, 1)", (sym,))
    conn.commit()
    pct = fundamentals.market_percentiles(conn, snap, {s: "Technology" for s in "ABCDEFG"})
    assert sorted(pct, key=pct.get) == ["A", "B", "C", "D", "E", "F"]
    assert (pct["A"], pct["F"]) == (0.0, 1.0)
    assert fundamentals.snapshot_on(conn, "2026-10-16") == snap
    assert fundamentals.snapshot_on(conn, "2026-10-10") is None
