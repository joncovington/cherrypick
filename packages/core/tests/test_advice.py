"""cherrypick.core.advice — the deterministic admission gate for AI parameter advice.

The posture under test: absent/stale/expired/invalid ⇒ baseline (empty proposals), one
violation rejects the whole artifact, advice is single-session and never sticky.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cherrypick.core import advice


def _raise_oserror(*_args, **_kwargs):
    raise OSError("disk full")


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
    assert v == {"ok": False, "reason": "absent", "proposals": [], "rejected": [], "experiments": []}


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

    first = advice.session_decision(
        state, "flies", "2026-08-20", _cfg(enabled=True, bounds=bounds), path, base_key="base_arm"
    )
    assert first["params"] == {"stop": 1.0}
    assert first["base_arm"] == "control"

    # The artifact is replaced mid-session; the decision must not move.
    _publish(state, "flies", 1.4)
    again = advice.session_decision(
        state, "flies", "2026-08-20", _cfg(enabled=True, bounds=bounds), path, base_key="base_arm"
    )
    assert again["params"] == {"stop": 1.0}, "advice changed mid-session"


def test_session_decision_does_not_replay_yesterday(tmp_path):
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    path.write_text(json.dumps({"day": "2026-08-19", "base_book": "control", "params": {"x": 1}}))

    today = advice.session_decision(state, "pmcc", "2026-08-20", _cfg(), path)

    assert today["day"] == "2026-08-20"
    assert today["params"] is None
    assert today["reason"] == "advice_disabled: no advice block in config"


def test_session_decision_is_baseline_when_disabled_or_unbounded(tmp_path):
    """Each cause gets its own words. A bare "advice_disabled" against a live artifact is what the
    advisor could not diagnose on 2026-08-25, and the three causes need different fixes."""
    state = tmp_path / "state"
    cases = (
        ("off", _cfg(enabled=False, bounds={"a": {}}), "advice_disabled: advice.enabled is false"),
        ("nobounds", _cfg(enabled=True), "advice_disabled: advice.bounds is empty"),
        ("noblock", {}, "advice_disabled: no advice block in config"),
    )
    for name, cfg, reason in cases:
        d = advice.session_decision(state, "calendars", "2026-08-20", cfg, tmp_path / f"{name}.json")
        assert d["params"] is None
        assert d["reason"] == reason
        assert d["base_book"] == "control"


def test_a_baseline_decision_is_never_made_sticky(tmp_path):
    """The 2026-08-25 loss, as a test. A process that reaches session_decision with an advice-less
    config must not be able to fix the day's decision for the loop that comes after it with a good
    one — meic and earnings each lost their most informative session to exactly this."""
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    bounds = {"stop": {"min": 0.5, "max": 1.5}}
    _publish(state, "flies", 1.0)

    early = advice.session_decision(state, "flies", "2026-08-20", {}, path, base_key="base_arm")
    assert early["params"] is None
    assert not path.exists(), "a baseline decision must not be recorded"

    later = advice.session_decision(
        state, "flies", "2026-08-20", _cfg(enabled=True, bounds=bounds), path, base_key="base_arm"
    )
    assert later["params"] == {"stop": 1.0}, "the baseline decision blocked a valid artifact"


def test_persist_false_derives_without_fixing_the_day(tmp_path):
    """A replay of a past date, or an iteration forced outside the window, still needs a decision to
    run under — it just must not be the one the session is recorded as having made."""
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    bounds = {"stop": {"min": 0.5, "max": 1.5}}
    _publish(state, "flies", 1.0)

    d = advice.session_decision(
        state,
        "flies",
        "2026-08-20",
        _cfg(enabled=True, bounds=bounds),
        path,
        base_key="base_arm",
        persist=False,
    )
    assert d["params"] == {"stop": 1.0}
    assert not path.exists()


def test_a_recorded_decision_carries_when_it_was_derived(tmp_path):
    """`derived_at` is what diagnosed the 08-25 loss, read off file mtimes. It belongs in the data:
    the advisor reads this file and cannot stat it."""
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    _publish(state, "flies", 1.0)
    d = advice.session_decision(
        state,
        "flies",
        "2026-08-20",
        _cfg(enabled=True, bounds={"stop": {"min": 0.5, "max": 1.5}}),
        path,
        base_key="base_arm",
    )
    assert d["derived_at"].startswith("20")


def test_session_decision_carries_the_modules_own_base_key(tmp_path):
    """flies calls its books arms; calendars and pmcc call them books. The key is persisted and read
    by each module's loop, so it stays theirs rather than being normalised here."""
    state = tmp_path / "state"
    fl = advice.session_decision(
        state, "flies", "2026-08-20", _cfg(base_arm="width-5"), tmp_path / "f.json", base_key="base_arm"
    )
    cal = advice.session_decision(
        state, "calendars", "2026-08-20", _cfg(base_book="path"), tmp_path / "c.json"
    )
    assert fl["base_arm"] == "width-5" and "base_book" not in fl
    assert cal["base_book"] == "path" and "base_arm" not in cal


def test_session_decision_survives_an_unwritable_path(tmp_path, monkeypatch):
    """A write failure must not cost the tick — the decision still governs this process."""
    state = tmp_path / "state"
    _publish(state, "pmcc", 1.0)
    monkeypatch.setattr(Path, "write_text", _raise_oserror)
    d = advice.session_decision(
        state,
        "pmcc",
        "2026-08-20",
        _cfg(enabled=True, bounds={"stop": {"min": 0.5, "max": 1.5}}),
        tmp_path / "d.json",
    )
    assert d["params"] == {"stop": 1.0}


def test_session_decision_ignores_an_unreadable_record(tmp_path):
    path = tmp_path / "advice_active.json"
    path.write_text("{ not json")
    d = advice.session_decision(tmp_path / "state", "pmcc", "2026-08-20", _cfg(), path)
    assert d["day"] == "2026-08-20"


# --------------------------------------------------------------------------- 2026-09-12 review fixes


def test_a_malformed_bounds_rule_rejects_instead_of_raising():
    """A rule that is a bare number is a config mistake; it must read as a rejection with a reason,
    not raise out of the validator (2026-09-12: on the producer side that raise aborted every other
    module's nightly issuance)."""
    from datetime import datetime, timedelta, timezone

    from cherrypick.core import advice as _advice

    artifact = {
        "module": "meic",
        "session": "2026-09-14",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "proposals": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "r"}],
    }
    out = _advice.validate(artifact, {"stop_trigger_ratio": 0.9}, "2026-09-14")
    assert out["ok"] is False and out["proposals"] == []
    assert "malformed" in out["rejected"][0]["reason"]


# --------------------------------------------------------------------------- experiment identity


def test_the_artifact_carries_its_experiment_and_load_passes_it_through(tmp_path):
    state = tmp_path / "state"
    advice.write(
        advice.advice_path(state, "meic", "2026-08-20"),
        "meic",
        "2026-08-20",
        [{"param": "stop", "value": 1.0}],
        advisor="cherrypick.advisor/enact-v1 (exp-2026-08-19-meic-1)",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        experiment_id="exp-2026-08-19-meic-1",
    )
    out = advice.load(state, "meic", "2026-08-20", {"stop": {"min": 0.5, "max": 1.5}})
    assert out["ok"] is True and out["experiment_id"] == "exp-2026-08-19-meic-1"
    # A rejected artifact still names whose session it cost.
    out = advice.load(state, "meic", "2026-08-20", {"stop": {"min": 2.0, "max": 3.0}})
    assert out["ok"] is False and out["experiment_id"] == "exp-2026-08-19-meic-1"


def test_an_artifact_written_before_the_field_attributes_from_its_stamp():
    """Every artifact the advisor ever wrote ends `advisor` with the experiment in parentheses."""
    stamped = {"advisor": "cherrypick.advisor/enact-v1 (exp-2026-09-09-meic-1)"}
    assert advice.artifact_experiment_id(stamped) == "exp-2026-09-09-meic-1"
    assert advice.artifact_experiment_id({"advisor": "claude -p / eod-advise-v1"}) is None
    assert advice.artifact_experiment_id({"advisor": "x (exp-a)", "experiment_id": "exp-b"}) == "exp-b"
    assert advice.artifact_experiment_id("not a dict") is None


def test_the_session_decision_records_the_experiment(tmp_path):
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    advice.write(
        advice.advice_path(state, "flies", "2026-08-20"),
        "flies",
        "2026-08-20",
        [{"param": "stop", "value": 1.0}],
        advisor="cherrypick.advisor/enact-v1 (exp-2026-08-19-flies-1)",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
    )
    cfg = _cfg(enabled=True, bounds={"stop": {"min": 0.5, "max": 1.5}})
    d = advice.session_decision(state, "flies", "2026-08-20", cfg, path, base_key="base_arm")
    assert d["experiment_id"] == "exp-2026-08-19-flies-1"
    assert json.loads(path.read_text())["experiment_id"] == "exp-2026-08-19-flies-1"
    off = advice.session_decision(
        state, "flies", "2026-08-20", _cfg(enabled=False), tmp_path / "off.json", base_key="base_arm"
    )
    assert off["experiment_id"] is None


def test_only_an_advised_book_carries_the_experiment_stamp():
    assert advice.stamp_for("advised:control", "exp-1") == "exp-1"
    assert advice.stamp_for("advised:strat_test:iron_fly", "exp-1") == "exp-1"
    assert advice.stamp_for("control", "exp-1") is None
    assert advice.stamp_for("advised:control", None) is None
    assert advice.stamp_for(None, "exp-1") is None


# --------------------------------------------------------------------------- many experiments, many books
def _entries():
    return [
        {
            "experiment_id": "exp-2026-09-14-flies-1",
            "name": "forecast-range-gate-floor-probe",
            "tag": "advised:forecast-range-gate-floor-probe",
            "base": "control",
            "proposals": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "a"}],
        },
        {
            "experiment_id": "exp-2026-09-17-flies-1",
            "name": "trend-gate",
            "tag": "advised:trend-gate",
            "base": "control",
            "proposals": [{"param": "entry_price_strategy", "value": "mid", "rationale": "b"}],
        },
    ]


def test_each_experiment_is_validated_on_its_own():
    """One experiment's out-of-bounds overlay is THAT experiment's baseline, never its neighbour's."""
    art = _artifact([])
    art["experiments"] = _entries()
    art["experiments"][1]["proposals"] = [{"param": "stop_trigger_ratio", "value": 5.0}]
    v = advice.validate(art, BOUNDS, SESSION, now=NOW)
    assert [e["ok"] for e in v["experiments"]] == [True, False]
    assert v["experiments"][1]["proposals"] == [] and v["experiments"][1]["rejected"]
    # the top level mirrors the FIRST entry
    assert v["ok"] is True and v["proposals"] == v["experiments"][0]["proposals"]
    # artifact-level failures still reject everything
    v = advice.validate(art, BOUNDS, "2026-01-01", now=NOW)
    assert v["ok"] is False and v["experiments"] == []


def test_a_legacy_artifact_reads_as_one_unnamed_entry():
    v = advice.validate(_artifact([{"param": "stop_trigger_ratio", "value": 0.9}]), BOUNDS, SESSION, now=NOW)
    assert len(v["experiments"]) == 1
    e = v["experiments"][0]
    assert e["name"] is None and e["tag"] is None and e["ok"] is True


def test_write_carries_every_experiment_and_mirrors_the_first(tmp_path):
    path = advice.advice_path(tmp_path, "flies", SESSION)
    advice.write(
        path,
        "flies",
        SESSION,
        [],
        advisor="x",
        expires_at=(NOW + timedelta(hours=8)).isoformat(),
        experiments=_entries(),
    )
    raw = json.loads(path.read_text())
    assert [e["tag"] for e in raw["experiments"]] == [
        "advised:forecast-range-gate-floor-probe",
        "advised:trend-gate",
    ]
    assert raw["experiment_id"] == "exp-2026-09-14-flies-1"
    assert raw["proposals"] == _entries()[0]["proposals"]
    out = advice.load(tmp_path, "flies", SESSION, BOUNDS, now=NOW)
    assert [e["ok"] for e in out["experiments"]] == [True, True]


def test_session_decision_opens_one_book_per_experiment(tmp_path):
    state, path = tmp_path / "state", tmp_path / "advice_active.json"
    advice.write(
        advice.advice_path(state, "flies", SESSION),
        "flies",
        SESSION,
        [],
        advisor="x",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        experiments=_entries(),
    )
    cfg = _cfg(enabled=True, bounds=BOUNDS, base_arm="control")
    d = advice.session_decision(state, "flies", SESSION, cfg, path, base_key="base_arm")
    books = advice.advised_books(d)
    assert [b["tag"] for b in books] == ["advised:forecast-range-gate-floor-probe", "advised:trend-gate"]
    assert books[0]["params"] == {"stop_trigger_ratio": 0.9} and books[1]["params"] == {
        "entry_price_strategy": "mid"
    }
    assert all(b["base"] == "control" for b in books)
    # the stamp resolves per book from the decision itself
    assert advice.stamp_for("advised:trend-gate", d) == "exp-2026-09-17-flies-1"
    assert advice.stamp_for("advised:trend-gate:iron_fly", d) == "exp-2026-09-17-flies-1"
    assert advice.stamp_for("control", d) is None
    assert advice.stamp_for("advised:unknown", d) is None
    # the legacy mirror still names the first experiment
    assert d["experiment_id"] == "exp-2026-09-14-flies-1" and d["params"] == {"stop_trigger_ratio": 0.9}
    # replayed from disk, the list survives
    again = advice.session_decision(state, "flies", SESSION, cfg, path, base_key="base_arm")
    assert [b["tag"] for b in advice.advised_books(again)] == [b["tag"] for b in books]


def test_a_legacy_decision_record_is_one_book_on_the_base():
    """A decision file written before `experiments` existed still opens its `advised:<base>` book."""
    legacy = {"day": SESSION, "base_arm": "control", "params": {"x": 1}, "experiment_id": "exp-old"}
    books = advice.advised_books(legacy)
    assert books == [
        {
            "experiment_id": "exp-old",
            "name": None,
            "tag": "advised:control",
            "base": "control",
            "params": {"x": 1},
        }
    ]
    assert advice.stamp_for("advised:control", legacy) == "exp-old"
    assert advice.advised_books({"day": SESSION, "params": None}) == []
    assert advice.advised_books(None) == []


def test_advised_tag_is_one_rule_and_slug_safe():
    assert advice.advised_tag("Forecast Range (floor probe)") == "advised:forecast-range-floor-probe"
    assert (
        advice.advised_tag("condor-take-earlier", "iron_condor") == "advised:condor-take-earlier:iron_condor"
    )
    assert advice.slug("  a:b  ") == "a-b"
    assert advice.is_advised("advised:x") and not advice.is_advised("control") and not advice.is_advised(None)
