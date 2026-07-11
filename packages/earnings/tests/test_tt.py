import asyncio
import json
from datetime import date

import tt

# --- pure helpers ------------------------------------------------------------

def test_num_converts_valid_values():
    assert tt._num("1.5") == 1.5
    assert tt._num(3) == 3.0


def test_num_none_and_invalid_return_none():
    assert tt._num(None) is None
    assert tt._num("not-a-number") is None


def test_serialize_primitives_passthrough():
    assert tt._serialize(None) is None
    assert tt._serialize("x") == "x"
    assert tt._serialize(5) == 5
    assert tt._serialize(True) is True


def test_serialize_list_and_dict_recurse():
    assert tt._serialize([1, "a", None]) == [1, "a", None]
    assert tt._serialize({"k": [1, 2]}) == {"k": [1, 2]}


def test_serialize_model_dump_object():
    class _Model:
        def model_dump(self, mode="json"):
            return {"a": 1}
    assert tt._serialize(_Model()) == {"a": 1}


def test_serialize_falls_back_to_str():
    class _Plain:
        def __str__(self):
            return "plain-repr"
    assert tt._serialize(_Plain()) == "plain-repr"


def test_error_flags_retryable_for_5xx():
    exc = RuntimeError("upstream returned 503 ")
    result = tt._error(exc)
    assert result["ok"] is False
    assert result.get("retryable") is True


def test_error_not_retryable_for_generic_exception():
    result = tt._error(ValueError("bad input"))
    assert result["ok"] is False
    assert "retryable" not in result


def test_strike_converts_and_handles_bad_values():
    class _Opt:
        strike_price = "150.5"
    assert tt._strike(_Opt()) == 150.5

    class _BadOpt:
        strike_price = None
    assert tt._strike(_BadOpt()) is None


def test_atm_window_centers_on_around_price():
    class _Opt:
        def __init__(self, strike):
            self.strike_price = strike
    options = [_Opt(s) for s in (90, 95, 100, 105, 110)]
    result = tt._atm_window(options, strike_count=1, around_price=100)
    strikes = sorted(o.strike_price for o in result)
    assert strikes == [95, 100, 105]


def test_atm_window_empty_options_returns_input():
    assert tt._atm_window([], strike_count=5, around_price=100) == []


def test_nearest_expiration_picks_closest_to_target_days():
    today = date.today()
    from datetime import timedelta
    expirations = [today + timedelta(days=d) for d in (1, 5, 30)]
    result = tt._nearest_expiration(expirations, target_days=0)
    assert result == expirations[0]


def test_live_trading_enabled_reads_config(monkeypatch):
    monkeypatch.setattr(tt, "_load_config", lambda: {"enable_live_trading": True})
    assert tt._live_trading_enabled() is True
    monkeypatch.setattr(tt, "_load_config", lambda: {"enable_live_trading": False})
    assert tt._live_trading_enabled() is False


def test_live_trading_enabled_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(tt, "_load_config", lambda: {})
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    assert tt._live_trading_enabled() is True
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    assert tt._live_trading_enabled() is False


# --- _build_order ------------------------------------------------------------

def test_build_order_credit_price_is_negative():
    spec = {
        "order_type": "Limit", "time_in_force": "Day", "price": 2.5, "price_effect": "Credit",
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1}],
    }
    order = tt._build_order(spec)
    assert order.price < 0
    assert len(order.legs) == 1


def test_build_order_debit_price_is_positive():
    spec = {
        "order_type": "Limit", "time_in_force": "Day", "price": 2.5, "price_effect": "Debit",
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1}],
    }
    order = tt._build_order(spec)
    assert order.price > 0


def test_build_order_maps_all_actions():
    for action in ("buy to open", "sell to open", "buy to close", "sell to close"):
        spec = {
            "legs": [{"symbol": "X", "instrument_type": "Equity Option", "action": action, "quantity": 1}],
        }
        order = tt._build_order(spec)
        assert len(order.legs) == 1


# --- cmd_execute_trade (mocked account/session) -------------------------------

def test_cmd_execute_trade_blocks_live_when_disabled(monkeypatch):
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: False)
    args = type("Args", (), {"live": True, "order": "{}", "account_number": None})()
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result == {"ok": False, "error": "Live trading is disabled. Set enable_live_trading=true in config.json."}


def test_cmd_execute_trade_dry_run_returns_without_submitting(monkeypatch):
    order_spec = {
        "order_type": "Limit", "time_in_force": "Day", "price": 1.0, "price_effect": "Credit",
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1}],
    }

    class _Preflight:
        errors = []
        warnings = []
        buying_power_effect = None

    class _FakeAccount:
        account_number = "ACC1"
        async def place_order(self, session_obj, order, dry_run):
            assert dry_run is True
            return _Preflight()

    async def _fake_get_account(account_number=None):
        return _FakeAccount()

    monkeypatch.setattr(tt, "_get_account", _fake_get_account)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    args = type("Args", (), {"live": False, "order": json.dumps(order_spec), "account_number": None})()
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["account_number"] == "ACC1"


def test_cmd_execute_trade_preflight_errors_block_submission(monkeypatch):
    order_spec = {
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1}],
    }

    class _Preflight:
        errors = ["insufficient buying power"]
        warnings = []
        buying_power_effect = None

    class _FakeAccount:
        account_number = "ACC1"
        async def place_order(self, session_obj, order, dry_run):
            return _Preflight()

    async def _fake_get_account(account_number=None):
        return _FakeAccount()

    monkeypatch.setattr(tt, "_get_account", _fake_get_account)
    monkeypatch.setattr(tt, "get_session", lambda: object())

    args = type("Args", (), {"live": False, "order": json.dumps(order_spec), "account_number": None})()
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is False
    assert result["problems"] == ["insufficient buying power"]


def test_cmd_execute_trade_live_submits_order(monkeypatch):
    order_spec = {
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1}],
    }

    class _Preflight:
        errors = []
        warnings = []
        buying_power_effect = None

    class _Response:
        pass

    calls = []

    class _FakeAccount:
        account_number = "ACC1"
        async def place_order(self, session_obj, order, dry_run):
            calls.append(dry_run)
            return _Preflight() if dry_run else _Response()

    async def _fake_get_account(account_number=None):
        return _FakeAccount()

    monkeypatch.setattr(tt, "_get_account", _fake_get_account)
    monkeypatch.setattr(tt, "get_session", lambda: object())
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)

    args = type("Args", (), {"live": True, "order": json.dumps(order_spec), "account_number": None})()
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert calls == [True, False]


def test_cmd_execute_trade_deploy_governor_blocks_live_over_cap(monkeypatch):
    # account_deploy_limit_pct wired from config -> cherrypit.broker deploy governor blocks a live
    # order that would push deployed BP over the cap, before any live submit.
    order_spec = {
        "legs": [{"symbol": "AAPL_C", "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1}],
    }

    class _BPE:
        change_in_buying_power = "-5300"  # consumes 5300 BP

    class _Preflight:
        errors = []
        warnings = []
        buying_power_effect = _BPE()

    class _Balances:
        used_derivative_buying_power = "0"
        derivative_buying_power = "10000"  # capacity 10000, 50% cap = 5000 < 5300

    calls = []

    class _FakeAccount:
        account_number = "ACC1"
        async def place_order(self, session_obj, order, dry_run):
            calls.append(dry_run)
            return _Preflight()
        async def get_balances(self, session_obj):
            return _Balances()

    async def _fake_get_account(account_number=None):
        return _FakeAccount()

    monkeypatch.setattr(tt, "_get_account", _fake_get_account)
    monkeypatch.setattr(tt, "get_session", lambda: object())
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_load_config", lambda: {"account_deploy_limit_pct": 50})

    args = type("Args", (), {"live": True, "order": json.dumps(order_spec), "account_number": None})()
    result = asyncio.run(tt.cmd_execute_trade(args))
    assert result["ok"] is False
    assert result["error"] == "account deploy limit exceeded"
    assert result["governor"]["deploy_governor"] == "enforced"
    assert calls == [True]  # blocked before the live submit
