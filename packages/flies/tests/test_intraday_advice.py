"""The intraday agent's deterministic half (docs/intraday-agent-plan.md): when a tick is worth a call,
what a reply may say, the decision file a loop reads and its expiry, and the record of every pack.
Every tick here runs a fake model -- the package never calls one."""

import json

import pytest
from test_intraday_pack import SESSION, T, _pack, stores  # noqa: F401 -- the shared stores fixture

from cherrypick.flies import intraday_advice as ia

CFG = {"intraday_agent": {"enabled": True}}


def _fake(reply, cost=0.07):
    calls = []

    def ask(prompt, pack_json):
        calls.append(json.loads(pack_json))
        return {"reply": reply, "model": "fake-model", "cost_usd": cost}

    ask.calls = calls
    return ask


def test_a_valid_reply_maps_labels_back_to_real_ids(stores):  # noqa: F811
    raw = _pack(*stores)
    v = ia.validate(
        '```json\n{"trend_gate": "on", "close_stranded": ["p2"], "confidence": 0.8, "reason": "call wall stepped up"}\n```',
        raw,
    )
    assert v["ok"] and v["decision"]["close_stranded"] == ["X-20260921113000000002"]
    assert v["decision"]["close_labels"] == ["p2"]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ('{"trend_gate": "maybe", "close_stranded": [], "confidence": 0.5, "reason": "x"}', "trend_gate"),
        (
            '{"trend_gate": "on", "close_stranded": ["p1"], "confidence": 0.5, "reason": "x"}',
            "not open verticals",
        ),
        (
            '{"trend_gate": "on", "close_stranded": ["p9"], "confidence": 0.5, "reason": "x"}',
            "not open verticals",
        ),
        ('{"trend_gate": "on", "close_stranded": [], "confidence": 1.5, "reason": "x"}', "confidence"),
        (
            '{"trend_gate": "on", "close_stranded": [], "confidence": 0.5, "reason": "' + "w " * 41 + '"}',
            "40 words",
        ),
        ("the trend looks strong", "JSON"),
    ],
)
def test_an_inadmissible_reply_is_refused_with_its_reason(stores, reply, error):  # noqa: F811
    v = ia.validate(reply, _pack(*stores))
    assert not v["ok"] and error in v["error"]


def test_a_tick_is_worth_a_call_only_on_an_open_vertical_or_near_the_band():
    acfg = ia.agent_config(CFG)
    assert ia.trigger({"flies": {"open_verticals": 1}, "spx": {"vs_open_points": 2}}, acfg) == "open_vertical"
    assert (
        ia.trigger({"flies": {"open_verticals": 0}, "spx": {"vs_open_points": 27}}, acfg) == "near_trend_band"
    )
    assert ia.trigger({"flies": {"open_verticals": 0}, "spx": {"vs_open_points": 3}}, acfg) is None
    assert ia.trigger({"flies": {"open_verticals": 0}, "spx": {"vs_open_points": -45}}, acfg) is None


def test_the_config_is_off_by_default_and_an_unknown_live_mode_never_widens():
    assert ia.agent_config({})["enabled"] is False
    assert ia.agent_config({"intraday_agent": {"live_mode_max": "everything"}})["live_mode_max"] == "off"


def test_a_tick_records_its_pack_writes_a_decision_and_the_loop_reads_it_until_it_expires(stores):  # noqa: F811
    gex, ledger = (__import__("sqlite3").connect(p) for p in stores)
    ask = _fake(
        '{"trend_gate": "on", "close_stranded": ["p2"], "confidence": 0.7, "reason": "higher highs, wall up"}'
    )
    out = ia.run_tick(
        cfg=CFG,
        target="paper",
        session=SESSION,
        as_of=T,
        arm="control",
        gex_conn=gex,
        ledger_conn=ledger,
        ask=ask,
        prompt="p",
    )
    assert out["called"] and out["ok"] and out["trigger"] == "open_vertical"
    assert "_position_ids" not in json.dumps(ask.calls[0]) and "2026" not in json.dumps(ask.calls[0])
    (rec,) = ia.records(SESSION)
    assert (
        rec["pack"]["flies"]["_position_ids"]["p2"] == "X-20260921113000000002"
    )  # kept locally, for the fit
    assert rec["cost_usd"] == 0.07 and rec["decision"]["trend_gate"] == "on"
    assert ia.read_decision("paper", session=SESSION, now=T + 60)["close_stranded"] == [
        "X-20260921113000000002"
    ]
    assert ia.read_decision("paper", session=SESSION, now=T + 11 * 60) is None  # expired: the rule again
    assert ia.read_decision("paper", session="2026-09-22", now=T + 60) is None
    assert ia.read_decision("live", session=SESSION, now=T + 60) is None  # another target's file


def test_too_soon_and_the_call_cap_spend_nothing(stores):  # noqa: F811
    gex, ledger = (__import__("sqlite3").connect(p) for p in stores)
    ask = _fake('{"trend_gate": "on", "close_stranded": [], "confidence": 0.6, "reason": "trend intact"}')
    kw = dict(
        cfg=CFG,
        target="paper",
        session=SESSION,
        arm="control",
        gex_conn=gex,
        ledger_conn=ledger,
        ask=ask,
        prompt="p",
    )
    assert ia.run_tick(as_of=T, **kw)["called"]
    assert ia.run_tick(as_of=T + 60, **kw)["skipped"] == "too_soon"
    capped = {"intraday_agent": {"enabled": True, "max_calls_per_session": 1}}
    assert ia.run_tick(**{**kw, "cfg": capped}, as_of=T + 600)["skipped"] == "call_cap"
    assert len(ask.calls) == 1


def test_a_failed_call_is_recorded_and_leaves_the_loop_on_its_rule(stores):  # noqa: F811
    gex, ledger = (__import__("sqlite3").connect(p) for p in stores)

    def broken(prompt, pack_json):
        return {"error": "claude timed out after 180s"}

    out = ia.run_tick(
        cfg=CFG,
        target="live",
        session=SESSION,
        as_of=T,
        arm="control",
        gex_conn=gex,
        ledger_conn=ledger,
        ask=broken,
        prompt="p",
    )
    assert out["called"] and not out["ok"] and "timed out" in out["error"]
    assert ia.read_decision("live", session=SESSION, now=T + 60) is None


def test_the_replay_records_but_never_writes_the_file_a_loop_reads(stores):  # noqa: F811
    gex, ledger = (__import__("sqlite3").connect(p) for p in stores)
    ask = _fake('{"trend_gate": "off", "close_stranded": [], "confidence": 0.5, "reason": "lower lows"}')
    ia.run_tick(
        cfg=CFG,
        target="paper",
        session=SESSION,
        as_of=T,
        arm="control",
        gex_conn=gex,
        ledger_conn=ledger,
        ask=ask,
        prompt="p",
        write_file=False,
    )
    assert len(ia.records(SESSION)) == 1 and not ia.decision_path("paper").exists()


@pytest.mark.parametrize(
    ("chosen", "ceiling", "expected"),
    [
        ("gates_and_closures", "shadow", "shadow"),  # config only narrows the day's choice
        ("gates", "gates_and_closures", "gates"),
        ("shadow", "gates", "shadow"),
        ("gates", "off", "off"),
    ],
)
def test_the_live_mode_is_the_days_choice_capped_by_config(chosen, ceiling, expected):
    acfg = ia.agent_config({"intraday_agent": {"enabled": True, "live_mode_max": ceiling}})
    record = {"date": SESSION, "intraday_agent": {"mode": chosen}}
    assert ia.live_mode_today(acfg, record, SESSION) == expected


def test_no_record_another_day_or_disabled_is_off():
    acfg = ia.agent_config({"intraday_agent": {"enabled": True, "live_mode_max": "gates"}})
    assert ia.live_mode_today(acfg, None, SESSION) == "off"
    assert (
        ia.live_mode_today(acfg, {"date": "2026-09-20", "intraday_agent": {"mode": "gates"}}, SESSION)
        == "off"
    )
    assert ia.live_mode_today(acfg, {"date": SESSION}, SESSION) == "off"  # armed, but no agent chosen
    off = ia.agent_config({"intraday_agent": {"enabled": False, "live_mode_max": "gates"}})
    assert ia.live_mode_today(off, {"date": SESSION, "intraday_agent": {"mode": "gates"}}, SESSION) == "off"


def test_a_close_past_the_wing_width_is_dropped_and_the_rest_of_the_decision_stands():
    pack = {
        "flies": {
            "positions": [
                {"id": "p1", "state": "open_vertical", "wing": 5, "spot_past_short_points": 12.0},
                {"id": "p2", "state": "open_vertical", "wing": 5, "spot_past_short_points": 2.5},
            ],
            "_position_ids": {"p1": "deep", "p2": "near"},
        }
    }
    v = ia.validate(
        '{"trend_gate": "on", "close_stranded": ["p1", "p2"], "confidence": 0.7, "reason": "x"}', pack
    )
    assert v["ok"] and v["decision"]["close_stranded"] == ["near"]
    assert v["decision"]["dropped_closes"] == [{"label": "p1", "why": "past_wing_width"}]
