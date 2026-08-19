"""Tests for `cherrypick-desk cancel` and `orders` — the order-management commands added alongside
`propose`/`confirm`.

Broker and session are stubbed (matching the desk's own pattern: nothing here should need a real
tastytrade connection), so these exercise the PIN gate, `policy.evaluate_management`, and the journal
exactly as `test_pin_and_journal.py` and `test_policy.py` do for the order path.
"""

import json

import pytest

from cherrypick.desk import cli, pin
from cherrypick.desk import config as cfgmod

pytestmark = pytest.mark.unit

ACCOUNT = "5WI62375"


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, key, value):
        self.store[(service, key)] = value

    def get_password(self, service, key):
        return self.store.get((service, key))

    def delete_password(self, service, key):
        self.store.pop((service, key), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(pin, "_keyring", lambda: kr)
    return kr


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setattr(cfgmod, "journal_path", lambda: path)
    return path


@pytest.fixture
def enabled_cfg(monkeypatch, tmp_path):
    cfg_path = tmp_path / "desk.json"
    cfg_path.write_text(json.dumps({"enabled": True, "allowed_accounts": ["2375"]}))
    monkeypatch.setattr(cfgmod, "config_path", lambda: cfg_path)
    return cfg_path


def _pin_set(fake_keyring, value="hunter2-desk"):
    pin.set_pin(value)
    return value


# --------------------------------------------------------------------------- cancel: the PIN gate
def test_cancel_without_a_pin_is_refused(enabled_cfg, tmp_journal, fake_keyring, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None, pin=None))
    assert out["ok"] is False
    assert "PIN" in out["error"]


def test_cancel_with_the_wrong_pin_is_refused_and_journaled(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    _pin_set(fake_keyring)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None, pin="wrong-pin-value"))
    assert out["ok"] is False
    assert "PIN rejected" in out["error"]
    entry = json.loads(tmp_journal.read_text().strip().splitlines()[-1])
    assert entry["event"] == "refused" and entry["phase"] == "cancel"


# --------------------------------------------------------------------------- cancel: policy gate
def test_cancel_is_refused_when_the_account_is_not_allowlisted(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    value = _pin_set(fake_keyring)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: "9WI69999")  # not in allowlist
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None, pin=value))
    assert out["ok"] is False
    assert any("allowed_accounts" in r for r in out["refusals"])


def test_cancel_is_allowed_under_the_suite_halt_flag(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    """The load-bearing assertion: `evaluate_management` never receives a halt-flag argument at all
    (it has no `halt_present` parameter, unlike `evaluate`), so a cancel proceeds regardless of the
    suite halt flag's presence — pulling a resting order only reduces exposure. This test never
    touches the halt-flag file precisely because cancel does not check it; if a future edit adds
    that check, this test's mock of `_cancel` still succeeding proves nothing changed on that axis,
    but `test_management_is_exempt_from_the_halt_flag` in test_policy.py is the one that would catch
    a regression at the source."""
    value = _pin_set(fake_keyring)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(cli, "_cancel", lambda cfg, order_id, account: {"ok": True, "order_id": order_id})
    out = cli.cmd_cancel(_Args(order_id=42, account_number=None, pin=value))
    assert out["ok"] is True
    assert out["order_id"] == 42


# --------------------------------------------------------------------------- cancel: journaling
def test_a_successful_cancel_is_journaled_with_the_masked_account(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    value = _pin_set(fake_keyring)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(cli, "_cancel", lambda cfg, order_id, account: {"ok": True, "order_id": order_id})
    cli.cmd_cancel(_Args(order_id=7, account_number=None, pin=value))
    entry = json.loads(tmp_journal.read_text().strip().splitlines()[-1])
    assert entry["event"] == "cancelled"
    assert entry["account"] == "****2375"
    assert "5WI62375" not in tmp_journal.read_text()


def test_a_failed_broker_cancel_is_journaled_as_cancel_failed(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    value = _pin_set(fake_keyring)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(
        cli, "_cancel", lambda cfg, order_id, account: {"ok": False, "error": "already filled"}
    )
    out = cli.cmd_cancel(_Args(order_id=7, account_number=None, pin=value))
    assert out["ok"] is False
    entry = json.loads(tmp_journal.read_text().strip().splitlines()[-1])
    assert entry["event"] == "cancel_failed"


def test_env_pin_is_honored_same_as_confirm(
    enabled_cfg, tmp_journal, fake_keyring, monkeypatch
):
    """`cancel` must read `CHERRYPICK_DESK_PIN` the same way `confirm` does — a remote session that
    exported it for confirm should not need a second mechanism for cancel."""
    value = _pin_set(fake_keyring)
    monkeypatch.setattr(pin, "env_pin", lambda: value)
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(cli, "_cancel", lambda cfg, order_id, account: {"ok": True, "order_id": order_id})
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None, pin=None))
    assert out["ok"] is True


# --------------------------------------------------------------------------- orders: read-only
class _FakeAcct:
    def __init__(self, number):
        self.account_number = number


def test_orders_needs_no_pin(enabled_cfg, monkeypatch):
    """A status read, not an action — same posture as `status` itself. No PIN is set up anywhere in
    this test, and it still succeeds, which is the point being pinned."""
    import cherrypick.core.broker as _broker

    import cherrypick.desk.session as _session

    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(_session, "reset", lambda: None)
    monkeypatch.setattr(_session, "get_session", lambda cfg: "fake-session")

    async def fake_resolve_account(session, number=None, **kw):
        return _FakeAcct(number)

    async def fake_working_orders(account, session):
        return [{"order_id": 1, "status": "Live", "underlying_symbol": "APO"}]

    monkeypatch.setattr(_broker, "resolve_account", fake_resolve_account)
    monkeypatch.setattr(_broker, "working_orders", fake_working_orders)

    out = cli.cmd_orders(_Args(account_number=None))
    assert out["ok"] is True
    assert out["account"] == "****2375"
    assert out["orders"][0]["underlying_symbol"] == "APO"


def test_orders_masks_the_account_and_never_needs_a_broker_when_unresolved(enabled_cfg, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: None)
    out = cli.cmd_orders(_Args(account_number=None))
    assert out["ok"] is False
    assert "could not resolve" in out["error"]
