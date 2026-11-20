"""`read portfolio-sources`: the backtest results Compare's portfolio tab may hold beside paper arms.

Guards, each broken on purpose while this was written: only results whose trades are engine trade
records are listed (a study written in another shape is left out, not read as zeros); a file under
`results/` is licensed whatever it says; reproduction copies are not listed; an unreadable file is
named.
"""

from __future__ import annotations

import json

from cherrypick.backtest import paths, readout

TRADE = {
    "arm": "a",
    "session": "2026-09-14",
    "exit_session": "2026-09-14",
    "net": {"verdict": 5.0, "mid": 6.0},
}


def _put(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")


def test_builder_runs_and_engine_shaped_studies_are_listed(tmp_path, monkeypatch):
    root = tmp_path / "bt"
    monkeypatch.setattr(paths, "data_dir", lambda: root)
    builder = {"name": "builder-mine-2611161730", "arms": {"a": {}}, "sessions": ["2026-09-14", "2026-09-25"],
               "licensed": False, "trades": [TRADE], "generated_at": "2026-11-16T17:30:00-05:00"}  # fmt: skip
    _put(root / "automations" / "builder-mine-2611161730.json", builder)
    study = {
        "item": 75,
        "window": ["2022-05-16", "2026-11-13"],
        "arms": {"x": {"trades": [dict(TRADE, arm="x")]}},
    }
    _put(root / "results" / "research" / "item75" / "study-all.json", study)
    _put(root / "results" / "research" / "item9" / "study-all.json", {"arms": {"y": {"sessions": [1, 2]}}})
    _put(root / "results" / "research" / "reproduce" / "r1" / "item75" / "study-all.json", study)
    _put(root / "results" / "research" / "item8" / "study-bad.json", "{")
    out = readout.portfolio_sources()
    assert out["ok"] is True
    by = {s["id"]: s for s in out["sources"]}
    assert set(by) == {"automations/builder-mine-2611161730", "results/research/item75/study-all"}
    b, s = by["automations/builder-mine-2611161730"], by["results/research/item75/study-all"]
    assert b["kind"] == "builder" and b["arms"] == ["a"] and b["licensed"] is False
    assert b["window"] == ["2026-09-14", "2026-09-25"]
    assert s["kind"] == "study" and s["arms"] == ["x"] and s["licensed"] is True  # under results/
    assert s["label"] == "item 75 study-all"
    assert out["unreadable"] == ["study-bad.json"]
