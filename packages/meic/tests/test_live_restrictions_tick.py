"""An account's trading restrictions and the pattern-day-trader rule stop a NEW IC at the tick.

`restriction_blockers` has tests of its own (test_live_account.py); these pin that the tick reads it.
The status is read only for an account on another login (`_live_account`) or one that asks
(`live.restriction_check`): a closing-only, frozen or day-trade-starved account opens nothing, a
status that cannot be read blocks as well, and a clean one enters. Offline; the broker is a fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import FakeBroker, _config  # noqa: E402
from test_live_tick_guards import _entered, _ledger, _tick  # noqa: E402

CLEAN = {"margin_or_cash": "Margin", "net_liquidating_value": 40000.0, "day_trade_count": 0}
CLOSING_ONLY = {"margin_or_cash": "Cash", "is_closing_only": True}
# A margin account under $25k net liq with two day trades gone: one left, and a stopped IC may need two.
PDT_STARVED = {"margin_or_cash": "Margin", "net_liquidating_value": 812.0, "day_trade_count": 2}


class StatusBroker(FakeBroker):
    """A FakeBroker that reports a trading status, or fails to."""

    def __init__(self, status=None, error=None, **kw):
        super().__init__(**kw)
        self._trading_status, self._error, self.status_reads = status, error, 0

    def trading_status(self):
        self.status_reads += 1
        if self._error:
            raise self._error
        return self._trading_status


def _asks(**over):
    return _config(restriction_check=True, **over)


def test_a_clean_status_enters_and_was_read(tmp_path, monkeypatch):
    """The control: the status is read for an account that asks, and a clean one opens an IC."""
    broker = StatusBroker(CLEAN)
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path), _asks(), broker)
    assert broker.status_reads == 1
    assert _entered(broker) and entry["entry"] == "placed"


@pytest.mark.parametrize("status", [CLOSING_ONLY, PDT_STARVED], ids=["closing_only", "pdt_starved"])
def test_a_restricted_account_opens_nothing(tmp_path, monkeypatch, status):
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path), _asks(), StatusBroker(status))
    assert not broker.placed
    assert entry["blocked"] and entry["blocked"][0].startswith(("restricted:", "pattern_day_trader:"))


def test_an_account_on_another_login_is_read_without_asking(tmp_path, monkeypatch):
    """`_live_account` is what a second login's view carries; its status is read whatever
    `live.restriction_check` says."""
    cfg = {**_config(), "_live_account": {"login": "margin", "enabled": True}}
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path), cfg, StatusBroker(CLOSING_ONLY))
    assert not broker.placed and broker.status_reads == 1
    assert "restricted: account is closing-only" in entry["blocked"]


def test_a_status_that_cannot_be_read_blocks_entries(tmp_path, monkeypatch):
    """Unreadable is not unrestricted."""
    broker = StatusBroker(error=RuntimeError("status endpoint down"))
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path), _asks(), broker)
    assert not broker.placed
    assert any(b.startswith("trading_status_unreadable") for b in entry["blocked"])


def test_the_main_account_is_not_read_unless_it_asks(tmp_path, monkeypatch):
    """Main's tick makes no extra broker call: an account that never asked is not read, restricted or not."""
    broker = StatusBroker(CLOSING_ONLY)
    entry, broker = _tick(monkeypatch, tmp_path, _ledger(tmp_path), _config(), broker)
    assert broker.status_reads == 0
    assert _entered(broker) and entry["entry"] == "placed"
