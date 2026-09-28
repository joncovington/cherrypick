"""The narrative's inputs beside the pack: which technicals report and which headlines it is handed.

The model call itself is not tested here (it is outside every package, by design); what is tested is
the deterministic half -- that a morning is paired with the close it actually saw, and that a
missing input reaches the prompt as null rather than stopping the note.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "morning_narrative.py"


def _module():
    spec = importlib.util.spec_from_file_location("morning_narrative", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mn = _module()


def _report(dir_: Path, session: str, **extra) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    doc = {
        "ok": True,
        "session": session,
        "movers": {"gainers": [{"symbol": "UP", "change_pct": 5.0}], "losers": []},
        "stages": [
            {"sector": "Energy", "net": -2, "leaders": [], "laggards": [{"symbol": "X"}, {"symbol": "Y"}]}
        ],
        "signals": {"BullishTrendFollowing": ["A", "B"]},
        **extra,
    }
    (dir_ / f"report-{session}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_a_morning_gets_the_last_report_before_it_never_its_own_days(tmp_path, monkeypatch):
    monkeypatch.setattr(mn, "TECHNICALS", tmp_path)
    _report(tmp_path, "2026-09-24")
    _report(tmp_path, "2026-09-25")
    _report(tmp_path, "2026-09-28")
    t = mn._technicals("2026-09-28")
    assert t["session"] == "2026-09-25"
    assert t["stages"] == [{"sector": "Energy", "net": -2, "leaders": 0, "laggards": 2}]
    assert t["signal_counts"] == {"BullishTrendFollowing": 2}


def test_no_report_or_a_failed_one_is_null(tmp_path, monkeypatch):
    monkeypatch.setattr(mn, "TECHNICALS", tmp_path)
    assert mn._technicals("2026-09-28") is None
    _report(tmp_path, "2026-09-25", ok=False)
    assert mn._technicals("2026-09-28") is None


def test_headlines_fall_back_one_day_and_carry_no_links(tmp_path, monkeypatch):
    monkeypatch.setattr(mn, "HEADLINES", tmp_path)
    assert mn._headlines("2026-09-28") is None
    item = {"title": "T", "link": "https://x", "source": "S", "published": "2026-09-27T20:00:00+00:00"}
    (tmp_path / "2026-09-27.json").write_text(
        json.dumps({"generated_at": "g", "items": [item]}), encoding="utf-8"
    )
    assert mn._headlines("2026-09-28") == {
        "generated_at": "g",
        "items": [{"title": "T", "source": "S", "published": "2026-09-27T20:00:00+00:00"}],
    }
    assert mn._headlines("2026-09-30") is None, "two days stale is not this morning's news"
