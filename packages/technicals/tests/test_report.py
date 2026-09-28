"""The report artifact: gathered from the engines, never recomputed downstream."""

from __future__ import annotations

import json

from cherrypick.technicals import paths, report, store


def test_the_report_gathers_stages_by_sector_breadth_rotation_and_leaders():
    conn = store.connect()
    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)][:200]
    rows = []
    for i, d in enumerate(days):
        rows += [
            ("SPY", d, 1, 1, 1, 100.0, 1),
            ("UP", d, 1, 1, 1, 100.0 * 1.01**i, 1),
            ("DOWN", d, 1, 1, 1, 100.0 * 0.99**i, 1),
        ]
    store.upsert_bars(conn, rows)
    conn.commit()
    cands = paths.universe_candidates()
    cands.parent.mkdir(parents=True, exist_ok=True)
    cands.write_text(json.dumps({"names": {"UP": {}, "DOWN": {}}}), encoding="utf-8")
    (cands.parent / "sectors.json").write_text(
        json.dumps({"sectors": {"UP": {"sector": "Technology"}}}), encoding="utf-8"
    )
    doc = report.build(conn=conn)
    assert doc["ok"] and doc["session"] == days[-1] and doc["record_only"] is True
    by_sector = {s["sector"]: s for s in doc["stages"]}
    assert by_sector["Technology"]["leaders"] == [{"symbol": "UP", "stage": "confirmed"}]
    assert by_sector["Unassigned"]["laggards"] == [{"symbol": "DOWN", "stage": "confirmed"}]
    assert doc["breadth"][-1] == {
        "session": days[-1],
        "leaders": 1,
        "laggards": 1,
        "net": 0,
        "bullish_share": 0.5,
    }
    assert doc["leaders"][0]["symbol"] == "UP"
    # The session's movers: gainers up only, losers down only, volume against the name's own average.
    assert [m["symbol"] for m in doc["movers"]["gainers"]] == ["UP"]
    assert [m["symbol"] for m in doc["movers"]["losers"]] == ["DOWN"]
    assert (
        doc["movers"]["gainers"][0]["change_pct"] == 1.0
        and doc["movers"]["gainers"][0]["volume_ratio"] == 1.0
    )
    path = report.write(doc)
    assert json.loads(open(path, encoding="utf-8").read())["session"] == days[-1]
