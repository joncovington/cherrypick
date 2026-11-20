"""The live gates are pure: every one refuses on its own, and the example ships off."""

import copy
import json
import pathlib

import pytest

from cherrypick.ladder import live_gates

EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "config.example.json"
ACCOUNT = "12345670000"
ARMS = ["session", "chain"]
FUNDS = ["TQQQ", "SOXL"]


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def ready_config() -> dict:
    cfg = example()
    cfg["live"]["enabled"] = True
    cfg["live"]["gate0_confirmed"] = "owner 2026-11-20"
    return cfg


def gates(cfg=None, **over):
    kw = {
        "module_enabled": True,
        "armed_for": "2026-11-20",
        "today": "2026-11-20",
        "now_min": 10 * 60,
        "designated": ACCOUNT,
        "halted": False,
        "arm_names": ARMS,
        "fund_names": FUNDS,
    }
    kw.update(over)
    return live_gates.evaluate(cfg if cfg is not None else ready_config(), **kw)


def by_name(result):
    return {g.name: g for g in result}


def test_example_ships_off_with_the_issue_defaults():
    live = example()["live"]
    assert live["enabled"] is False
    assert not live_gates.attested(live["gate0_confirmed"])
    assert (live["arm"], live["symbols"], live["lot_dollars"]) == ("session", ["TQQQ"], 1000)
    assert (live["max_open_lots"], live["max_open_lots_total"], live["max_open_dollars"]) == (1, 2, 2500)
    assert live["correlated_groups"] == {"nasdaq-semis": ["TQQQ", "SOXL", "TECL", "QLD", "USD"]}
    assert live["max_open_lots_per_group"] == 1
    assert (live["drawdown_breaker_frac"], live["session_start"], live["disarm_at"]) == (
        0.5,
        "08:15",
        "20:00",
    )
    assert live["hospital_alert_days"] == 18
    assert "task_name" not in live


def test_every_live_key_has_a_note():
    live = example()["live"]
    for key in live:
        if not key.startswith("_"):
            assert f"_{key}_note" in live, key


def test_a_ready_config_passes_every_gate():
    result = gates()
    assert [g.name for g in result if not g.ok] == []
    assert live_gates.ready(result) and live_gates.new_buys_allowed(result)


def test_the_example_as_shipped_fails_the_config_gates():
    result = gates(example(), armed_for=None)
    failing = {g.name for g in result if not g.ok}
    assert {"live.enabled", "live.gate0_confirmed", "arm.record"} <= failing
    assert not live_gates.ready(result)


def test_live_enabled_refuses():
    for value in (False, None, "true", 1):
        cfg = ready_config()
        cfg["live"]["enabled"] = value
        assert not by_name(gates(cfg))["live.enabled"].ok


@pytest.mark.parametrize("value", ["", "   ", False, None, 0])
def test_gate0_refuses_without_an_attestation(value):
    cfg = ready_config()
    cfg["live"]["gate0_confirmed"] = value
    assert not by_name(gates(cfg))["live.gate0_confirmed"].ok


def test_gate0_accepts_a_string_or_true():
    for value in ("owner 2026-11-20", True):
        cfg = ready_config()
        cfg["live"]["gate0_confirmed"] = value
        assert by_name(gates(cfg))["live.gate0_confirmed"].ok


def test_module_off_refuses():
    assert not by_name(gates(module_enabled=False))["modules.ladder.enabled"].ok


@pytest.mark.parametrize(
    "armed_for, now_min",
    [(None, 600), ("2026-11-19", 600), ("2026-11-20", 20 * 60), ("2026-11-20", 20 * 60 + 5)],
)
def test_arm_record_refuses_when_missing_stale_or_past_disarm(armed_for, now_min):
    g = by_name(gates(armed_for=armed_for, now_min=now_min))["arm.record"]
    assert not g.ok and g.reason


def test_arm_record_follows_the_configured_disarm_time():
    cfg = ready_config()
    cfg["live"]["disarm_at"] = "12:00"
    assert by_name(gates(cfg, now_min=11 * 60 + 59))["arm.record"].ok
    assert not by_name(gates(cfg, now_min=12 * 60))["arm.record"].ok


@pytest.mark.parametrize("designated", [None, "", "  "])
def test_no_designated_account_refuses(designated):
    assert not by_name(gates(designated=designated))["account.designated"].ok


def test_the_account_is_shown_masked_never_whole():
    g = by_name(gates())["account.designated"]
    assert "****0000" in g.reason and "1234567" not in g.reason


def test_the_halt_flag_blocks_new_buys_only():
    result = gates(halted=True)
    g = by_name(result)["halt.flag"]
    assert not g.ok and g.blocks == "new_buys" and "new buy" in g.reason
    assert live_gates.ready(result)  # held lots are still managed
    assert not live_gates.new_buys_allowed(result)


def test_live_arm_must_be_an_enabled_paper_arm():
    cfg = ready_config()
    for value in ("nope", "", None):
        cfg["live"]["arm"] = value
        assert not by_name(gates(cfg))["live.arm"].ok
    cfg["live"]["arm"] = "session"
    assert not by_name(gates(cfg, arm_names=["chain"]))["live.arm"].ok


@pytest.mark.parametrize("symbols", [[], None, "TQQQ", ["SPXU"], ["TQQQ", "SPXU"]])
def test_live_symbols_must_be_non_empty_and_in_the_fund_list(symbols):
    cfg = ready_config()
    cfg["live"]["symbols"] = symbols
    assert not by_name(gates(cfg))["live.symbols"].ok


def test_live_symbols_accept_another_listed_fund_case_insensitively():
    cfg = ready_config()
    cfg["live"]["symbols"] = ["tqqq", "SOXL"]
    assert by_name(gates(cfg))["live.symbols"].ok


BAD_LIMITS = [
    ("lot_dollars", 0),
    ("lot_dollars", -5),
    ("lot_dollars", "1000"),
    ("lot_dollars", True),
    ("max_open_lots", 0),
    ("max_open_lots", 1.5),
    ("max_open_lots_total", 0),
    ("max_open_lots_total", None),
    ("max_open_dollars", 0),
    ("max_open_dollars", 500),  # under one lot
    ("max_open_lots_per_group", 0),
    ("drawdown_breaker_frac", 0),
    ("drawdown_breaker_frac", 1.5),
    ("hospital_alert_days", 0),
    ("session_start", "8:15"),
    ("disarm_at", "25:00"),
    ("disarm_at", "08:00"),  # not after session_start
    ("correlated_groups", []),
    ("correlated_groups", {"g": []}),
    ("correlated_groups", {"g": [1]}),
]


@pytest.mark.parametrize("key, value", BAD_LIMITS)
def test_invalid_limits_refuse(key, value):
    cfg = ready_config()
    cfg["live"][key] = value
    g = by_name(gates(cfg))["live.limits"]
    assert not g.ok and g.reason


def test_total_below_per_fund_refuses():
    cfg = ready_config()
    cfg["live"]["max_open_lots"] = 3
    cfg["live"]["max_open_lots_total"] = 2
    g = by_name(gates(cfg))["live.limits"]
    assert not g.ok and "at least max_open_lots" in g.reason


def test_a_missing_live_block_fails_without_raising():
    cfg = copy.deepcopy(ready_config())
    del cfg["live"]
    result = gates(cfg)
    assert not live_gates.ready(result)
    assert not by_name(result)["live.limits"].ok
