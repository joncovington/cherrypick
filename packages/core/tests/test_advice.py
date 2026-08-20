"""cherrypick.core.advice — the deterministic admission gate for AI parameter advice.

The posture under test: absent/stale/expired/invalid ⇒ baseline (empty proposals), one
violation rejects the whole artifact, advice is single-session and never sticky.
"""

import json
from datetime import datetime, timedelta, timezone

from cherrypick.core import advice

BOUNDS = {
    "stop_trigger_ratio": {"min": 0.85, "max": 0.95},
    "daily_ic_trade_target": {"min": 0, "max": 3},
    "entry_price_strategy": {"choices": ["mid", "auto"]},
}
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
SESSION = "2026-07-29"


def _artifact(proposals, session=SESSION, expires=None):
    return {
        "module": "meic",
        "session": session,
        "expires_at": (expires or (NOW + timedelta(hours=8))).isoformat(),
        "proposals": proposals,
    }


def test_admits_in_bounds_proposals():
    art = _artifact(
        [
            {"param": "stop_trigger_ratio", "value": 0.90, "rationale": "regime"},
            {"param": "entry_price_strategy", "value": "mid", "rationale": "tight spreads"},
        ]
    )
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert v["ok"] is True
    assert [p["param"] for p in v["proposals"]] == ["stop_trigger_ratio", "entry_price_strategy"]


def test_one_violation_rejects_all():
    art = _artifact(
        [
            {"param": "stop_trigger_ratio", "value": 0.90, "rationale": "fine"},
            {"param": "stop_trigger_ratio2", "value": 0.90, "rationale": "unknown param"},
        ]
    )
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert v["ok"] is False and v["proposals"] == []
    assert "reject-all" in v["reason"]
    assert v["rejected"][0]["param"] == "stop_trigger_ratio2"


def test_closed_range_is_closed_and_out_of_bounds_rejects():
    ok = advice.validate(
        _artifact([{"param": "stop_trigger_ratio", "value": 0.85}]), BOUNDS, SESSION, now=NOW
    )
    assert ok["ok"] is True  # boundary value admitted (closed range)
    bad = advice.validate(
        _artifact([{"param": "stop_trigger_ratio", "value": 0.96}]), BOUNDS, SESSION, now=NOW
    )
    assert bad["ok"] is False and "outside" in bad["rejected"][0]["reason"]


def test_choices_membership_and_non_numeric_rejection():
    bad_choice = advice.validate(
        _artifact([{"param": "entry_price_strategy", "value": "yolo"}]), BOUNDS, SESSION, now=NOW
    )
    assert bad_choice["ok"] is False
    # A bool is not the number 1 — it must not slip through an int range.
    bad_bool = advice.validate(
        _artifact([{"param": "daily_ic_trade_target", "value": True}]), BOUNDS, SESSION, now=NOW
    )
    assert bad_bool["ok"] is False and "not numeric" in bad_bool["rejected"][0]["reason"]


def test_wrong_session_never_sticky():
    art = _artifact([{"param": "stop_trigger_ratio", "value": 0.9}], session="2026-07-28")
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert v["ok"] is False and "never sticky" in v["reason"]


def test_expired_advice_is_baseline():
    art = _artifact([{"param": "stop_trigger_ratio", "value": 0.9}], expires=NOW - timedelta(minutes=1))
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert v["ok"] is False and v["reason"] == "advice expired"


def test_duplicate_params_reject():
    art = _artifact(
        [
            {"param": "stop_trigger_ratio", "value": 0.9},
            {"param": "stop_trigger_ratio", "value": 0.85},
        ]
    )
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert v["ok"] is False and "duplicate" in v["rejected"][0]["reason"]


def test_load_absent_is_baseline(tmp_path):
    v = advice.load(tmp_path, "meic", SESSION, BOUNDS, now=NOW)
    assert v == {"ok": False, "reason": "absent", "proposals": [], "rejected": []}


def test_write_then_load_round_trip(tmp_path):
    path = advice.advice_path(tmp_path, "meic", SESSION)
    advice.write(
        path,
        "meic",
        SESSION,
        [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}],
        advisor="test",
        expires_at=(NOW + timedelta(hours=8)).isoformat(),
    )
    assert not path.with_suffix(".tmp").exists()  # atomic: no half-written leftover
    v = advice.load(tmp_path, "meic", SESSION, BOUNDS, now=NOW)
    assert v["ok"] is True and v["proposals"][0]["value"] == 0.9
    # The same artifact the next day is stale by construction.
    v2 = advice.load(tmp_path, "meic", "2026-07-30", BOUNDS, now=NOW + timedelta(days=1))
    assert v2["reason"] == "absent"  # next session looks for its own file


def test_load_corrupt_file_is_baseline(tmp_path):
    path = advice.advice_path(tmp_path, "meic", SESSION)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    v = advice.load(tmp_path, "meic", SESSION, BOUNDS, now=NOW)
    assert v["ok"] is False and v["reason"].startswith("unreadable") and v["proposals"] == []


# --------------------------------------------------------------------------- session_decision

def _publish(state_dir, module, value, session="2026-08-20"):
    """An artifact on disk for `module`, the way the advisor writes one."""
    advice.write(
        advice.advice_path(state_dir, module, session),
        module,
        session,
        [{"param": "stop", "value": value}],
        advisor="test",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
    )


def _cfg(**advice):
    return {"advice": advice}


def test_session_decision_is_derived_once_and_replayed(tmp_path):
    """The read-once rule. A later tick must replay what the first one recorded, even after the
    artifact on disk changes — otherwise an artifact landing mid-session would change the rules an
    already-open book is being managed under."""
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    bounds = {"stop": {"min": 0.5, "max": 1.5}}
    _publish(state, "flies", 1.0)

    first = advice.session_decision(state, "flies", "2026-08-20", _cfg(enabled=True, bounds=bounds),
                                    path, base_key="base_arm")
    assert first["params"] == {"stop": 1.0}
    assert first["base_arm"] == "control"

    # The artifact is replaced mid-session; the decision must not move.
    _publish(state, "flies", 1.4)
    again = advice.session_decision(state, "flies", "2026-08-20", _cfg(enabled=True, bounds=bounds),
                                    path, base_key="base_arm")
    assert again["params"] == {"stop": 1.0}, "advice changed mid-session"


def test_session_decision_does_not_replay_yesterday(tmp_path):
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    path.write_text(json.dumps({"day": "2026-08-19", "base_book": "control", "params": {"x": 1}}))

    today = advice.session_decision(state, "pmcc", "2026-08-20", _cfg(), path)

    assert today["day"] == "2026-08-20"
    assert today["params"] is None and today["reason"] == "advice_disabled"


def test_session_decision_is_baseline_when_disabled_or_unbounded(tmp_path):
    state = tmp_path / "state"
    for name, cfg in (("off", _cfg(enabled=False, bounds={"a": {}})), ("nobounds", _cfg(enabled=True))):
        d = advice.session_decision(state, "calendars", "2026-08-20", cfg, tmp_path / f"{name}.json")
        assert d["params"] is None
        assert d["reason"] == "advice_disabled"
        assert d["base_book"] == "control"


def test_session_decision_carries_the_modules_own_base_key(tmp_path):
    """flies calls its books arms; calendars and pmcc call them books. The key is persisted and read
    by each module's loop, so it stays theirs rather than being normalised here."""
    state = tmp_path / "state"
    fl = advice.session_decision(state, "flies", "2026-08-20", _cfg(base_arm="width-5"),
                                 tmp_path / "f.json", base_key="base_arm")
    cal = advice.session_decision(state, "calendars", "2026-08-20", _cfg(base_book="path"),
                                  tmp_path / "c.json")
    assert fl["base_arm"] == "width-5" and "base_book" not in fl
    assert cal["base_book"] == "path" and "base_arm" not in cal


def test_session_decision_survives_an_unwritable_path(tmp_path):
    """A write failure must not cost the tick — the decision still governs this process."""
    d = advice.session_decision(tmp_path / "state", "pmcc", "2026-08-20", _cfg(),
                                tmp_path / "nope" / "deep" / "d.json")
    assert d["reason"] == "advice_disabled"


def test_session_decision_ignores_an_unreadable_record(tmp_path):
    path = tmp_path / "advice_active.json"
    path.write_text("{ not json")
    d = advice.session_decision(tmp_path / "state", "pmcc", "2026-08-20", _cfg(), path)
    assert d["day"] == "2026-08-20"
