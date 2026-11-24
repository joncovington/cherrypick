"""`live.dry_run`: new live entries are preflighted and not sent, and a held book is still managed. Offline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import FakeBroker, _config, _entry_snapshot, _init_db, _open_trades  # noqa: E402

from cherrypick.meic import live_account, live_loop  # noqa: E402


def _quiet(*_):
    return None


def _tick(db_path, config, broker):
    return live_loop.run_once(config, _entry_snapshot(), db_path, broker, live=True, log=_quiet)


def _without_key(tmp_path):
    cfg = _config()
    del cfg["live"]["dry_run"]
    return cfg


@pytest.mark.parametrize("value", [True, None, "false", 0, "no"])
def test_anything_but_a_literal_false_is_a_dry_run(tmp_path, value):
    """Fails on a loop that reads the key as truthy-means-dry (0, None and "false" would then place)."""
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    out = _tick(db_path, _config(dry_run=value), broker)
    assert out["entry"]["entry"] == "dry_run"
    assert [p["live"] for p in broker.placed] == [False]
    assert _open_trades(db_path, "XSP") == []


def test_a_config_without_the_key_is_a_dry_run(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    out = _tick(db_path, _without_key(tmp_path), broker)
    assert out["entry"]["entry"] == "dry_run"
    assert [p["live"] for p in broker.placed] == [False]
    assert _open_trades(db_path, "XSP") == []


def test_a_literal_false_sends_the_entry(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    out = _tick(db_path, _config(dry_run=False), broker)
    assert out["entry"]["entry"] == "placed"
    assert [p["live"] for p in broker.placed] == [True]
    assert len(_open_trades(db_path, "XSP")) == 1


def test_a_held_book_is_still_confirmed_while_dry_run_is_true(tmp_path):
    """A close must always get through: the flag is read for the entry alone."""
    db_path = _init_db(tmp_path)
    broker = FakeBroker(statuses={"ORD1": {"status": "Filled", "price": "-1.37", "filled": True}})
    _tick(db_path, _config(dry_run=False), broker)
    summary = _tick(db_path, _config(dry_run=True), broker)
    assert summary["entries_confirmed"] == 1
    row = [t for t in _open_trades(db_path, "XSP") if t["ic_order_id"] == "LIVE-XSP-ORD1"][0]
    assert row["status"] == "open"
    # and the tick that confirmed it sent no second entry
    assert len([p for p in broker.placed if p["live"]]) == 1


def test_status_says_so_and_the_arming_readout_warns(tmp_path):
    cfg = _config(dry_run=True)
    status = live_loop.run_status(cfg, designated="ACC")
    assert status["dry_run"] is True
    assert any("live.dry_run is true" in w for w in status["arm_ready"]["warnings"])
    off = live_loop.run_status(_config(dry_run=False), designated="ACC")
    assert off["dry_run"] is False
    assert not any("live.dry_run" in w for w in off["arm_ready"]["warnings"])


def test_a_further_login_reads_its_own_key(tmp_path):
    cfg = _config(dry_run=False)
    cfg["live_accounts"] = {
        "margin": {"enabled": True, "symbol": "XSP"},
        "other": {"enabled": True, "symbol": "XSP", "dry_run": False},
    }
    assert live_loop.live_dry_run(live_account.config_view(cfg, "margin")) is True
    assert live_loop.live_dry_run(live_account.config_view(cfg, "other")) is False
    assert live_loop.live_dry_run(cfg) is False


def test_the_shipped_example_is_a_dry_run():
    example = json.loads((Path(__file__).parent.parent / "config.example.json").read_text(encoding="utf-8"))
    assert example["live"]["dry_run"] is True
    assert live_loop.live_dry_run(example) is True
