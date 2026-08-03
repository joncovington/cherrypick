"""Enforces the package's core safety invariant: this module can validate orders but never submit
one. See `services/staging.py`'s module docstring -- `live=False` must be a hardcoded literal at the
single `cherrypick.core.broker.place_order` call site, and nowhere else in the package may reach the
SDK's own order-placement call directly. This is a source scan (like orchestrator's
`test_headless.py`), not prose, plus a behavioral check with a faked broker account confirming the
SDK's own `dry_run` kwarg is always `True`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cherrypick.scout.services import staging as _staging
from cherrypick.scout.services.session import BrokerSession

SRC = Path(__file__).resolve().parent.parent / "src" / "cherrypick" / "scout"


def _source_files():
    return sorted(SRC.rglob("*.py"))


def test_place_order_is_called_exactly_once_with_live_hardcoded_false():
    sites = []
    for py in _source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "place_order"
            ):
                sites.append((py, node))

    assert len(sites) == 1, f"expected exactly one place_order call site, found: {[str(p) for p, _ in sites]}"
    py, call = sites[0]
    assert py.name == "staging.py", f"place_order called outside staging.py: {py}"

    live_kw = next((kw for kw in call.keywords if kw.arg == "live"), None)
    assert live_kw is not None, "the place_order call must pass live= explicitly"
    assert isinstance(live_kw.value, ast.Constant) and live_kw.value.value is False, (
        "live must be the literal False -- never a variable, config value, or True"
    )


def test_no_live_true_or_direct_dry_run_false_anywhere_in_the_package():
    offenders = [str(py) for py in _source_files() if "live=True" in py.read_text(encoding="utf-8")]
    assert not offenders, f"found live=True in: {offenders}"

    offenders = [str(py) for py in _source_files() if "dry_run=False" in py.read_text(encoding="utf-8")]
    assert not offenders, f"found dry_run=False in: {offenders}"


class _FakeAccount:
    account_number = "5WT99998888"

    def __init__(self):
        self.calls = []

    async def place_order(self, session, order, dry_run):
        self.calls.append(dry_run)
        return _FakePreflight()


class _FakePreflight:
    errors = []
    warnings = []
    buying_power_effect = None


class _FakeManager:
    def get_session(self):
        return "session"

    def reset_session(self):
        pass


@pytest.mark.asyncio
async def test_the_sdks_own_dry_run_kwarg_is_always_true(monkeypatch):
    """Even with a faked broker willing to accept a live submission, `dry_run_order` never asks for
    one -- the SDK sees `dry_run=True` and nothing else, exactly once."""
    account = _FakeAccount()

    async def fake_resolve_account(session, *a, **kw):
        return account

    monkeypatch.setattr(_staging._broker, "resolve_account", fake_resolve_account)

    legs = [
        {"symbol": "AAPL  260116P00150000", "quantity": -1, "price": 2.00},
        {"symbol": "AAPL  260116P00145000", "quantity": 1, "price": 1.00},
    ]
    result = await _staging.dry_run_order(
        BrokerSession(manager=_FakeManager(), politeness_seconds=0), legs
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert account.calls == [True]
