"""cherrypick.core.advice — the deterministic admission gate for AI parameter advice.

The posture under test: absent/stale/expired/invalid ⇒ baseline (empty proposals), one
violation rejects the whole artifact, advice is single-session and never sticky.
"""

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
