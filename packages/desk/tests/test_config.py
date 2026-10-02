"""The two gates and the fail-closed config.

The desk is off unless desk.json says `"enabled": true` (literally) AND the environment carries
CHERRYPICK_DESK_EXPERIMENTAL=1. Every order command must refuse before it touches the network or the
keyring. And a config with any cap that is not a finite non-negative number is refused whole.
"""

import json
import math

import pytest
from conftest import write_config

from cherrypick.desk import cli, keystore
from cherrypick.desk import config as cfgmod
from cherrypick.desk import session as desk_session

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- defaults
def test_defaults_are_the_small_safe_ones():
    cfg = cfgmod.resolve({})
    assert cfg["enabled"] is False
    assert cfg["allowed_accounts"] == [] and cfg["allowed_underlyings"] == []
    assert cfg["require_defined_risk"] is True
    assert cfg["max_order_risk_dollars"] == 100.0
    assert cfg["max_orders_per_day"] == 2
    assert cfg["max_daily_risk_dollars"] == 200.0
    assert cfg["max_spreads_per_order"] == 1
    assert cfg["max_order_buying_power_dollars"] == 100.0
    assert cfg["ticket_ttl_seconds"] == 120
    assert cfg["config_errors"] == []


def test_the_example_config_carries_the_same_defaults():
    """The example is what people copy; it must not be looser than the code."""
    from pathlib import Path

    example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text("utf-8"))
    resolved = cfgmod.resolve(example)
    assert resolved["config_errors"] == []
    defaults = cfgmod.resolve({})
    for key in (*cfgmod.CAPS, "enabled", "require_defined_risk", "allowed_accounts", "allowed_underlyings"):
        assert resolved[key] == defaults[key], key


def test_the_example_is_never_loaded_as_a_fallback():
    assert not cfgmod.config_path().exists()
    assert cfgmod.load() == {}


# --------------------------------------------------------------------------- strict booleans
@pytest.mark.parametrize("value", ["true", "yes", 1, "1", [True], {"on": True}])
def test_enabled_is_only_a_literal_true(value):
    assert cfgmod.resolve({"enabled": value})["enabled"] is False


def test_enabled_true_is_on():
    assert cfgmod.resolve({"enabled": True})["enabled"] is True


@pytest.mark.parametrize("value", ["false", 0, None, "no", []])
def test_require_defined_risk_is_only_turned_off_by_a_literal_false(value):
    assert cfgmod.resolve({"require_defined_risk": value})["require_defined_risk"] is True


def test_require_defined_risk_false_is_off():
    assert cfgmod.resolve({"require_defined_risk": False})["require_defined_risk"] is False


# --------------------------------------------------------------------------- caps fail closed
BAD_VALUES = [None, math.nan, math.inf, -math.inf, -1, "500", "", True, False, [100], {"v": 1}]


@pytest.mark.parametrize("key", list(cfgmod.CAPS))
@pytest.mark.parametrize("value", BAD_VALUES)
def test_a_bad_cap_refuses_the_whole_config(key, value):
    cfg = cfgmod.resolve({"enabled": True, "allowed_accounts": ["1234"], key: value})
    assert cfg["config_errors"], f"{key}={value!r} was accepted"
    assert cfg["enabled"] is False  # the whole file is refused, not just the one field
    assert cfg["allowed_accounts"] == []
    assert cfg[key] == cfgmod.resolve({})[key]


@pytest.mark.parametrize("key", ["max_orders_per_day", "max_spreads_per_order"])
def test_count_caps_must_be_whole_numbers(key):
    assert cfgmod.resolve({key: 1.5})["config_errors"]
    assert cfgmod.resolve({key: 3})[key] == 3


def test_zero_is_a_valid_cap():
    assert cfgmod.resolve({"max_order_risk_dollars": 0})["config_errors"] == []


@pytest.mark.parametrize(("given", "expected"), [(1, 30), (30, 30), (200, 200), (300, 300), (9999, 300)])
def test_ticket_ttl_is_clamped(given, expected):
    assert cfgmod.resolve({"ticket_ttl_seconds": given})["ticket_ttl_seconds"] == expected


def test_bad_allowlists_refuse_the_config():
    assert cfgmod.resolve({"allowed_accounts": "1234"})["config_errors"]
    assert cfgmod.resolve({"allowed_underlyings": [True]})["config_errors"]


def test_underlyings_are_upper_cased():
    assert cfgmod.resolve({"allowed_underlyings": [" spy "]})["allowed_underlyings"] == ["SPY"]


def test_a_corrupt_file_is_refused_not_silently_emptied():
    path = cfgmod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    cfg = cfgmod.resolve(cfgmod.load())
    assert cfg["config_errors"] and cfg["enabled"] is False


# --------------------------------------------------------------------------- the second gate
def test_the_experimental_env_must_be_exactly_one():
    assert cfgmod.experimental_ack({"CHERRYPICK_DESK_EXPERIMENTAL": "1"}) is True
    for value in ("", "0", "true", "yes", " 1"):
        assert cfgmod.experimental_ack({"CHERRYPICK_DESK_EXPERIMENTAL": value}) is False
    assert cfgmod.experimental_ack({}) is False


def test_both_gates_open_means_no_gate_refusals():
    cfg = cfgmod.resolve({"enabled": True})
    assert cfgmod.gate_refusals(cfg, {"CHERRYPICK_DESK_EXPERIMENTAL": "1"}) == []
    assert cfgmod.gate_refusals(cfg, {}) != []
    assert cfgmod.gate_refusals(cfgmod.resolve({}), {"CHERRYPICK_DESK_EXPERIMENTAL": "1"}) != []


# --------------------------------------------------------------------------- the CLI refuses early
ORDER = json.dumps(
    {
        "price": 1.0,
        "price_effect": "debit",
        "legs": [
            {
                "instrument_type": "Equity Option",
                "symbol": "XYZ   260807C00085000",
                "action": "buy to open",
                "quantity": 1,
            }
        ],
    }
)
GATED = [
    ["propose", "--order", ORDER],
    ["confirm", "--ticket", "deadbeef", "--code", "ABCDEF"],
    ["cancel", "--order-id", "1"],
    ["orders"],
]


@pytest.fixture
def no_network_no_keyring(monkeypatch, fake_keyring):
    """Any network or keyring touch fails the test outright."""

    def boom(*a, **k):
        raise AssertionError("touched the network or keyring while the desk is disabled")

    monkeypatch.setattr(desk_session, "get_session", boom)
    monkeypatch.setattr(keystore, "_keyring", boom)
    monkeypatch.setattr(cli, "_resolve_account", boom)
    monkeypatch.setattr(cli, "_resolve_masked_account", boom)


@pytest.mark.parametrize("argv", GATED, ids=lambda a: a[0])
@pytest.mark.parametrize(
    "setup",
    ["no_config", "enabled_without_env", "env_without_enabled", "enabled_string", "bad_cap"],
)
def test_order_commands_refuse_before_any_network_or_keyring(
    argv, setup, monkeypatch, capsys, no_network_no_keyring
):
    if setup == "enabled_without_env":
        write_config()
    elif setup == "env_without_enabled":
        write_config(enabled=False)
        monkeypatch.setenv(cfgmod.EXPERIMENTAL_ENV, "1")
    elif setup == "enabled_string":
        write_config(enabled="true")
        monkeypatch.setenv(cfgmod.EXPERIMENTAL_ENV, "1")
    elif setup == "bad_cap":
        write_config(max_order_risk_dollars=None)
        monkeypatch.setenv(cfgmod.EXPERIMENTAL_ENV, "1")

    code = cli.main(argv)
    err = capsys.readouterr().err
    assert code != 0
    assert "cherrypick-desk is EXPERIMENTAL and disabled" in err
    assert "CHERRYPICK_DESK_EXPERIMENTAL=1" in err
    assert "EXPERIMENTAL PROTOTYPE - educational use only" in err


def test_offline_commands_still_work_while_disabled(capsys, no_network_no_keyring):
    assert cli.main(["analyze", "--order", ORDER]) == 0
    assert '"max_loss": 100.0' in capsys.readouterr().out
