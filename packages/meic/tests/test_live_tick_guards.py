"""The live entry guards read where the live path reads them, not only as functions.

`entry_blockers`, `daily_loss_tripped` and `tt._live_trading_enabled` each have tests of their own;
these pin that the tick and `tt`'s order doors still consult them. A tick with the halt flag present,
or the day's loss past the breaker, opens no IC; `execute_trade`, `adjust_order` and `close_position`
with `enable_live_trading` false in the config reach no account. A tick with neither runs alongside,
so a blocked tick is blocked by its guard and not by the setup. Offline; the broker is a fake.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import DAY, FakeBroker, _config, _entry_snapshot, _unrecorded_close  # noqa: E402

from cherrypick.meic import live_loop, tt  # noqa: E402

NOW = datetime.fromisoformat(f"{DAY}T12:30:00").replace(tzinfo=ZoneInfo("America/New_York"))


def _ledger(tmp_path) -> str:
    """An empty live ledger, made in-process the way the tick makes its own."""
    db_path = str(tmp_path / "live.db")
    live_loop._ensure_live_schema(db_path)
    return db_path


def _tick(monkeypatch, tmp_path, db_path, cfg=None) -> tuple[dict, FakeBroker]:
    """One live `_tick` on a full entry snapshot, with everything it reaches outside the ledger
    pointed at the test. Returns the tick's entry decision and the broker."""
    broker, logged = FakeBroker(), []
    monkeypatch.setattr(live_loop, "_build_snapshot", lambda cfg, symbol: (_entry_snapshot(), None))
    monkeypatch.setattr(live_loop, "make_broker", lambda cfg, designated: broker)
    monkeypatch.setattr(live_loop._paths, "live_db_path", lambda: Path(db_path))
    monkeypatch.setattr(live_loop._stream_request, "register_live", lambda cfg: None)
    monkeypatch.setattr(live_loop, "halt_flag_path", lambda: str(tmp_path / "HALT"))
    monkeypatch.setattr(live_loop._pl, "_now_et", lambda: NOW)
    monkeypatch.setattr(live_loop, "_append_log", logged.append)
    monkeypatch.setattr(live_loop, "_record_attempt", lambda *a, **k: None)
    assert live_loop._tick(cfg or _config(), "****1234", [], True) == 0
    return json.loads(logged[-1])["entry"], broker


def _entered(broker) -> bool:
    return any(p["live"] is True for p in broker.placed)


def test_an_unblocked_tick_enters(tmp_path, monkeypatch):
    """The control: this setup opens an IC when nothing blocks it."""
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path))
    assert _entered(broker) and entry["entry"] == "placed"


def test_a_tick_with_the_halt_flag_present_opens_nothing(tmp_path, monkeypatch):
    (tmp_path / "HALT").write_text("halted by a test", encoding="utf-8")
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path))
    assert not _entered(broker)
    assert "halt_flag_present" in entry["blocked"]


def test_a_tick_past_the_daily_loss_breaker_opens_nothing(tmp_path, monkeypatch):
    """Today's book already lost $250 against a $200 breaker. The cap is lifted, so the breaker
    is the only thing between this tick and a second IC."""
    db_path = _ledger(tmp_path)
    _unrecorded_close(db_path, DAY, "LOST1", pnl=-250.0, fees=2.0)
    entry, broker = _tick(monkeypatch, tmp_path, db_path, _config(max_structures_per_day=None))
    assert not _entered(broker)
    assert "daily_loss_breaker" in entry["blocked"]


def test_the_master_switch_off_in_config_keeps_every_order_door_shut(monkeypatch):
    """Only the config is set: `_live_trading_enabled` itself is read, never stubbed."""
    monkeypatch.setattr(tt, "_load_config", lambda: {"enable_live_trading": False, "live": {}})
    reached = []

    async def _account(account_number=None):
        reached.append(True)
        raise RuntimeError("stop at the account: nothing is placed in a test")

    monkeypatch.setattr(tt, "_get_account", _account)
    leg = {"instrument_type": "Equity Option", "symbol": "XSP   260708P00583000", "action": "Sell to Open"}
    order = json.dumps({"price": 1.0, "price_effect": "credit", "legs": [{**leg, "quantity": 1}]})
    args = type("Args", (), {"dry_run": False, "order": order, "account_number": None, "order_id": "1"})()
    for command in (tt.cmd_execute_trade, tt.cmd_adjust_order, tt.cmd_close_position):
        out = asyncio.run(command(args))
        assert reached == [], command.__name__
        assert out["ok"] is False and "disabled" in out["error"], command.__name__
