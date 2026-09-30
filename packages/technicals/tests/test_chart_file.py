"""The chart file: our series as the engines compute them, and the vendor's levels beside them."""

from __future__ import annotations

import json
import math

from cherrypick.technicals import chart, indicators, paths, signals, store


def _land(conn, symbol, n=320):
    days = [f"{2025 + i // 336}-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)]
    rows = []
    for i, d in enumerate(days):
        c = 100 + 10 * math.sin(i / 9) + i * 0.05
        rows.append((symbol, d, c - 0.5, c + 1.0, c - 1.0, c, 1000))
    store.upsert_bars(conn, rows)
    conn.commit()
    return days


def test_the_chart_draws_the_grid_window_and_the_engines_own_readings():
    conn = store.connect()
    days = _land(conn, "ABC")
    doc = chart.build(conn, "ABC")
    assert doc["session"] == days[-1] and len(doc["bars"]["date"]) == chart.DISPLAY
    assert doc["bars"]["date"][0] == days[-chart.DISPLAY]
    bars = store.adjusted_bars(conn, "ABC")
    highs, lows, closes = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    assert doc["cci14"][-1] == round(indicators.cci(highs, lows, closes, 14)[-1], 2)
    window = bars[-250:]
    assert doc["grid"]["low"] == min(b.low for b in window) and doc["grid"]["high"] == max(
        b.high for b in window
    )
    assert doc["vendor"] is None


def test_signal_days_agree_with_the_scan_engine_on_every_day():
    """The O(n) series pass must give exactly what `signals.readings` gives on each prefix."""
    conn = store.connect()
    _land(conn, "ABC")
    bars = store.adjusted_bars(conn, "ABC")
    highs, lows, closes = [b.high for b in bars], [b.low for b in bars], [b.close for b in bars]
    fast = {s["index"]: s["rules"] for s in chart.signal_days(highs, lows, closes, 200)}
    for i in range(200, len(bars)):
        r = signals.readings(highs[: i + 1], lows[: i + 1], closes[: i + 1])
        assert fast.get(i, []) == (signals.matches(r) if r else []), i
    assert fast, "the fixture should match some rule, or this test proves nothing"


def test_a_vendor_capture_carries_its_levels_marked_against_our_grid_and_gaps():
    conn = store.connect()
    days = _land(conn, "ABC")
    # A gap up into the second-to-last bar: its low (top edge, gap support) sits above the prior high.
    top = round(store.adjusted_bars(conn, "ABC")[-3].high + 4.0, 2)
    store.upsert_bars(conn, [("ABC", days[-2], top + 1, top + 2, top, top + 1, 1000)])
    conn.commit()
    bars = store.adjusted_bars(conn, "ABC")
    lo = min(b.low for b in bars[-250:])
    root = paths.market_report_dir() / "vendor-charts" / days[-1]
    root.mkdir(parents=True, exist_ok=True)
    quotes = [{"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in bars]
    (root / "ABC.json").write_text(
        json.dumps(
            {
                "ticker": "ABC",
                "why": {
                    "historicalQuotes": quotes,
                    "supportAndResistance": {
                        "support": [{"value": round(lo, 4), "date": days[-5]}],
                        "resistance": [{"value": round(lo + 0.123, 4), "date": days[-3]}],
                        "gapSupport": [
                            {"value": top, "date": days[-2]},  # our gap's top edge, on its bar
                            {"value": top, "date": days[-3]},  # the same price on the wrong bar
                        ],
                    },
                    "technicalRank": 7,
                    "syrahSentimentShortTerm": [{"date": days[-1], "value": 2.0}],
                },
            }
        ),
        encoding="utf-8",
    )
    v = chart.build(conn, "ABC")["vendor"]
    assert v["capture"] == days[-1] and v["rank"] == 7 and v["bars_agree"] == v["bars_compared"] > 0
    assert [(x["kind"], x["on_our_grid"]) for x in v["levels"]] == [
        ("support", True),
        ("resistance", False),
        ("gapSupport", True),
        ("gapSupport", False),
    ]


def test_write_all_writes_a_file_per_name_and_an_index():
    conn = store.connect()
    _land(conn, "ABC")
    cands = paths.universe_candidates()
    cands.parent.mkdir(parents=True, exist_ok=True)
    cands.write_text(json.dumps({"names": {"ABC": {}}}), encoding="utf-8")
    out = chart.write_all(conn=conn)
    assert out["charts"] >= 1
    index = json.loads((chart.charts_dir() / "index.json").read_text(encoding="utf-8"))
    assert "ABC" in [s["symbol"] for s in index["symbols"]]
    assert json.loads((chart.charts_dir() / "ABC.json").read_text(encoding="utf-8"))["symbol"] == "ABC"


def test_the_index_funds_are_charted_although_breadth_leaves_funds_out():
    conn = store.connect()
    _land(conn, "SPY")
    _land(conn, "ABC")
    store.upsert_listings(conn, [("SPY", 1), ("ABC", 0)])
    conn.commit()
    cands = paths.universe_candidates()
    cands.parent.mkdir(parents=True, exist_ok=True)
    cands.write_text(json.dumps({"names": {"ABC": {}}}), encoding="utf-8")
    assert store.stocks(conn, ["SPY", "ABC"]) == ["ABC"]  # still out of breadth
    chart.write_all(conn=conn)
    index = json.loads((chart.charts_dir() / "index.json").read_text(encoding="utf-8"))
    assert [s["symbol"] for s in index["symbols"]] == ["SPY", "ABC"]  # QQQ and IWM hold no bars here
