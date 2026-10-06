"""The intraday agent on live (docs/intraday-agent-plan.md, step 4): the day's selection on the arm
record, what each mode lets a decision do, the stamp the shadow is scored on, and the scoring."""

import json

import pytest

from cherrypick.flies import intraday_advice, intraday_eval, live_loop

DAY = "2026-10-07"
ON = {"enabled": True}


def _acfg(**kw):
    return intraday_advice.agent_config({"intraday_agent": {**ON, **kw}})


def _record(mode, day=DAY):
    return {"date": day, "intraday_agent": {"mode": mode}}


def _decide(monkeypatch, decision):
    monkeypatch.setattr(intraday_advice, "read_decision", lambda target, session, now: decision)


GATE_OFF = {"trend_gate": "off", "close_stranded": []}
GATE_ON = {"trend_gate": "on", "close_stranded": []}


def test_shadow_reads_the_decision_and_never_changes_a_param(monkeypatch):
    _decide(monkeypatch, GATE_OFF)
    params = {"refuse_completion_against_trend": True}
    out, ctx = intraday_advice.live_tick(params, _acfg(live_mode_max="gates"), _record("shadow"), DAY, 0.0)
    assert out is params and ctx == {"mode": "shadow", "decision": GATE_OFF}


def test_gates_follow_a_fresh_decision_and_keep_the_arms_own_gate_without_one(monkeypatch):
    params = {"refuse_completion_against_trend": False}
    _decide(monkeypatch, GATE_ON)
    on, _ = intraday_advice.live_tick(params, _acfg(live_mode_max="gates"), _record("gates"), DAY, 0.0)
    assert (
        on["refuse_completion_against_trend"] is True and params["refuse_completion_against_trend"] is False
    )
    _decide(monkeypatch, None)
    same, ctx = intraday_advice.live_tick(params, _acfg(live_mode_max="gates"), _record("gates"), DAY, 0.0)
    assert same is params and ctx["decision"] is None


def test_config_caps_the_days_choice_and_an_unbuilt_mode_runs_as_gates(monkeypatch):
    _decide(monkeypatch, GATE_ON)
    capped, ctx = intraday_advice.live_tick({}, _acfg(live_mode_max="shadow"), _record("gates"), DAY, 0.0)
    assert ctx["mode"] == "shadow" and capped == {}
    assert intraday_advice.live_mode_today(
        _acfg(live_mode_max="gates_and_closures"), _record("gates_and_closures"), DAY
    ) == ("gates_and_closures" if intraday_advice.LIVE_CLOSES_BUILT else "gates")
    assert (
        intraday_advice.live_mode_today(_acfg(live_mode_max="gates"), _record("gates", day="2026-10-06"), DAY)
        == "off"
    )


@pytest.mark.parametrize(
    ("decision", "side", "spot", "expected"),
    [
        (GATE_ON, "call", 7730.0, 1),  # +30 committed up: a call vertical needs it to turn down
        (GATE_ON, "put", 7730.0, 0),  # a put vertical completes on the up move
        (GATE_OFF, "call", 7730.0, 0),  # the agent had the gate off
        (None, "call", 7730.0, None),  # no fresh decision: nothing to score
    ],
)
def test_the_entry_stamp_says_whether_the_agents_gate_would_have_refused(decision, side, spot, expected):
    snapshot = {"underlying_price": spot, "session": {"day_open": 7700.0}}
    stamp = intraday_advice.entry_stamp({"mode": "shadow", "decision": decision}, snapshot, {}, side)
    assert stamp["agent_mode"] == "shadow" and stamp["agent_would_refuse"] == expected
    assert intraday_advice.entry_stamp({"mode": "off", "decision": GATE_ON}, snapshot, {}, side) == {}


def test_offered_modes_are_the_evidence_narrowed_by_config_and_by_what_live_can_do():
    cfg = {"intraday_agent": {**ON, "live_mode_max": "gates_and_closures"}}
    assert intraday_eval.offered_live_modes({"intraday_agent": {"enabled": False}}, None) == ["off"]
    assert intraday_eval.offered_live_modes(cfg, None) == ["off", "shadow"]  # no evidence yet
    full = {"offered_modes": ["off", "shadow", "gates", "gates_and_closures"]}
    assert intraday_eval.offered_live_modes(cfg, full) == ["off", "shadow", "gates"]  # no live close path
    narrowed = {"intraday_agent": {**ON, "live_mode_max": "shadow"}}
    assert intraday_eval.offered_live_modes(narrowed, full) == ["off", "shadow"]


@pytest.fixture
def arm_file(tmp_path, monkeypatch):
    path = tmp_path / "flies-live-arm.json"
    monkeypatch.setattr(live_loop, "arm_stamp_path", lambda: str(path))
    monkeypatch.setattr(intraday_eval, "read_qualification", lambda: None)
    return path


def test_the_selection_needs_todays_arm_record(arm_file):
    cfg = {"intraday_agent": ON}
    assert live_loop.set_agent_mode(cfg, "shadow", today=DAY)["ok"] is False
    arm_file.write_text(json.dumps({"date": "2026-10-06"}), encoding="utf-8")
    assert live_loop.set_agent_mode(cfg, "shadow", today=DAY)["ok"] is False


def test_a_mode_the_evidence_does_not_offer_is_refused_and_nothing_is_written(arm_file):
    arm_file.write_text(json.dumps({"date": DAY, "armed_by": "live-flies-start"}), encoding="utf-8")
    out = live_loop.set_agent_mode({"intraday_agent": {**ON, "live_mode_max": "gates"}}, "gates", today=DAY)
    assert out["ok"] is False and out["offered"] == ["off", "shadow"]
    assert "intraday_agent" not in json.loads(arm_file.read_text(encoding="utf-8"))


def test_a_chosen_mode_lands_on_the_record_beside_what_was_there(arm_file):
    arm_file.write_text(json.dumps({"date": DAY, "armed_by": "live-flies-start"}), encoding="utf-8")
    out = live_loop.set_agent_mode({"intraday_agent": ON}, "shadow", today=DAY)
    rec = json.loads(arm_file.read_text(encoding="utf-8"))
    assert out["ok"] and rec["armed_by"] == "live-flies-start"
    assert rec["intraday_agent"]["mode"] == "shadow" and rec["intraday_agent"]["model"] == "opus"
    assert intraday_advice.live_mode_today(_acfg(), rec, DAY) == "shadow"


# --------------------------------------------------------------------------- the shadow, scored
def _live(day, would, pnl, mode="shadow"):
    return {"trade_date": day, "agent_mode": mode, "agent_would_refuse": would, "pnl": pnl}


def _arm(nets):
    return {d: {"settled_net": n} for d, n in nets.items()}


def test_shadow_scoring_counts_only_sessions_where_the_gate_moved():
    rows = [
        _live("a", 1, -250.0),  # live: refusing it saves 250; paper agent beat control
        _live("a", 0, 120.0),
        _live("b", 1, 90.0),  # live: refusing a winner costs 90; paper agent beat control -> disagree
        _live("c", 0, 50.0),  # nothing refused, paper flat: no evidence
        _live("d", 1, -80.0, mode="gates"),  # not a shadow session
    ]
    agent = _arm({"a": 300.0, "b": 50.0, "c": 10.0, "d": 0.0})
    control = _arm({"a": 100.0, "b": 0.0, "c": 10.0, "d": 0.0})
    out = intraday_eval.shadow_scoring(rows, agent, control, {})
    by = {s["session"]: s for s in out["sessions"]}
    assert set(by) == {"a", "b", "c"}
    assert (by["a"]["live_effect"], by["a"]["paper_effect"], by["a"]["agree"]) == (250.0, 200.0, True)
    assert (by["b"]["live_effect"], by["b"]["agree"]) == (-90.0, False)
    assert by["c"]["counted"] is False and by["c"]["agree"] is None
    assert (out["counted"], out["agree"], out["agreement"]) == (2, 1, 0.5)


def _evaluate(shadow, live_mode_max="gates_and_closures"):
    days = [f"2026-11-{d:02d}" for d in range(1, 26)]
    rule = {d: {"entries": 4, "stranded": 2, "settled_net": 0.0, "net_closes_2x": 0.0} for d in days}
    agent = {
        d: {"entries": 4, "stranded": 1, "settled_net": 150.0 + i % 3, "net_closes_2x": 160.0 + i % 3}
        for i, d in enumerate(days)
    }
    summary = {"paper_sessions": days, "shadow_sessions": days[:6], "episodes": 80}
    return intraday_eval.evaluate(
        rule, agent, summary, live_mode_max=live_mode_max, generated_at="t", arms={}, shadow=shadow
    )


def _shadow(counted, agree, tagged=0, saved=None):
    return {
        "sessions": [],
        "counted": counted,
        "agree": agree,
        "agreement": agree / counted if counted else None,
        "closes": {"tagged": tagged, "saved": saved},
    }


def test_gates_unlock_on_five_agreeing_shadow_sessions_and_not_on_four():
    assert _evaluate(_shadow(4, 4))["unlocked"]["gates"] is False
    assert _evaluate(_shadow(5, 3))["unlocked"]["gates"] is False  # 60% agreement
    out = _evaluate(_shadow(5, 4))
    assert out["unlocked"]["gates"] is True and out["offered_modes"] == ["off", "shadow", "gates"]


def test_closures_never_offered_while_live_cannot_close_even_when_the_evidence_unlocks_them():
    out = _evaluate(_shadow(6, 6, tagged=12, saved=400.0))
    assert out["unlocked"]["gates_and_closures"] is True
    assert "gates_and_closures" not in out["offered_modes"] or intraday_advice.LIVE_CLOSES_BUILT
    assert _evaluate(_shadow(6, 6, tagged=9, saved=400.0))["unlocked"]["gates_and_closures"] is False
