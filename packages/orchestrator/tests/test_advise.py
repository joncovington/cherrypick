"""The advise pipeline (orchestrator.advise): fenced generation, deterministic validation,
orchestrator-written artifacts. Claude is never invoked — the _run_claude seam is stubbed.
"""

import json

import pytest
from cherrypick.core import advice as core_advice

from cherrypick.orchestrator import advise
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

DAY = "2026-07-28"  # a Tuesday's predecessor: next trading day is 2026-07-29


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(cfgmod, "STATE_DIR", state)
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(advise, "_claude_available", lambda: "claude")
    monkeypatch.setattr(advise, "_context_blocks", lambda cfg, day: "===== reports =====\nnet 0")
    cfg = {
        "advise": {
            "enabled": True,
            "timeout_seconds": 5,
            "modules": {
                "meic": {
                    "enabled": True,
                    "advice_bounds": {"stop_trigger_ratio": {"min": 0.85, "max": 0.95}},
                },
            },
        },
        "modules": {"meic": {"enabled": True}},
    }
    return state, cfg


def test_disabled_is_skipped(env, monkeypatch):
    _, cfg = env
    cfg["advise"]["enabled"] = False
    assert advise.run(cfg, day=DAY)["skipped"] == "disabled"


def test_missing_claude_is_skipped(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(advise, "_claude_available", lambda: None)
    assert advise.run(cfg, day=DAY)["skipped"] == "claude_not_found"


def test_in_bounds_proposals_are_written_and_loadable(env, monkeypatch):
    state, cfg = env
    monkeypatch.setattr(advise, "_run_claude", lambda *a, **k: {
        "ok": True,
        "text": json.dumps({"proposals": [
            {"param": "stop_trigger_ratio", "value": 0.9, "rationale": "elevated VIX1D"}]}),
    })
    out = advise.run(cfg, day=DAY)
    assert out["ok"] is True and out["session"] == "2026-07-29"
    m = out["modules"]["meic"]
    assert m["ok"] is True and m["proposals"] == 1 and m["rejected"] == 0

    # The loop-side read (same core code) admits it for that session…
    loaded = core_advice.load(state, "meic", "2026-07-29",
                              cfg["advise"]["modules"]["meic"]["advice_bounds"])
    assert loaded["ok"] is True and loaded["proposals"][0]["value"] == 0.9
    # …and the artifact records provenance.
    art = json.loads(core_advice.advice_path(state, "meic", "2026-07-29").read_text())
    assert art["advisor"] == advise.ADVISOR and art["session"] == "2026-07-29"


def test_out_of_bounds_rejects_all_but_still_audits(env, monkeypatch):
    state, cfg = env
    monkeypatch.setattr(advise, "_run_claude", lambda *a, **k: {
        "ok": True,
        "text": json.dumps({"proposals": [
            {"param": "stop_trigger_ratio", "value": 0.9, "rationale": "fine"},
            {"param": "stop_trigger_ratio_x", "value": 1.0, "rationale": "nope"}]}),
    })
    out = advise.run(cfg, day=DAY)
    m = out["modules"]["meic"]
    assert m["ok"] is False and m["proposals"] == 0 and m["rejected"] == 1
    # The artifact exists for audit, and the loop-side read yields baseline (no proposals).
    loaded = core_advice.load(state, "meic", "2026-07-29",
                              cfg["advise"]["modules"]["meic"]["advice_bounds"])
    assert loaded["proposals"] == []
    art = json.loads(core_advice.advice_path(state, "meic", "2026-07-29").read_text())
    assert art["rejected"][0]["param"] == "stop_trigger_ratio_x"


def test_fenced_json_is_tolerated_garbage_is_not(env, monkeypatch):
    state, cfg = env
    monkeypatch.setattr(advise, "_run_claude", lambda *a, **k: {
        "ok": True, "text": '```json\n{"proposals": []}\n```'})
    out = advise.run(cfg, day=DAY)
    assert out["modules"]["meic"]["ok"] is True and out["modules"]["meic"]["proposals"] == 0

    monkeypatch.setattr(advise, "_run_claude", lambda *a, **k: {"ok": True, "text": "no json here"})
    out = advise.run(cfg, day=DAY)
    assert out["modules"]["meic"]["ok"] is False
    assert "not the requested JSON" in out["modules"]["meic"]["error"]


def test_module_without_bounds_is_skipped(env, monkeypatch):
    _, cfg = env
    cfg["advise"]["modules"]["meic"]["advice_bounds"] = {}
    monkeypatch.setattr(advise, "_run_claude", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("claude must not be called without bounds")))
    out = advise.run(cfg, day=DAY)
    assert out["modules"]["meic"]["skipped"] == "no_advice_bounds"


def test_next_session_skips_weekend():
    # Friday 2026-07-31 -> Monday 2026-08-03.
    assert advise._next_session({}, "2026-07-31") == "2026-08-03"


def test_expires_at_is_end_of_session_et():
    exp = advise._expires_at("2026-07-29")
    assert exp.startswith("2026-07-29T23:59:59")
