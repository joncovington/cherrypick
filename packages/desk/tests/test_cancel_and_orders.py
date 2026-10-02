"""`cherrypick-desk cancel` and `orders`.

Broker and session are stubbed, so these exercise the gates, the terminal-only PIN, and
`policy.evaluate_management` without a real connection.
"""

import json

import pytest

from cherrypick.desk import cli, journal
from cherrypick.desk import config as cfgmod

pytestmark = pytest.mark.unit

ACCOUNT = "5WT01234"


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _last_entry():
    return json.loads(cfgmod.journal_path().read_text().strip().splitlines()[-1])


@pytest.fixture
def broker_ok(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(cli, "_cancel", lambda cfg, order_id, account: {"ok": True, "order_id": order_id})


# --------------------------------------------------------------------------- cancel: the PIN gate
def test_cancel_without_a_configured_pin_is_refused(desk_on, broker_ok):
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None))
    assert out["ok"] is False and "PIN" in out["error"]


def test_cancel_without_a_tty_is_refused(desk_on, pin_set, broker_ok):
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None))
    assert out["ok"] is False and "interactive terminal" in out["error"]


def test_cancel_has_no_pin_flag():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["cancel", "--order-id", "1", "--pin", "x"])


def test_cancel_with_the_wrong_pin_is_refused_and_journaled(desk_on, pin_set, broker_ok, tty):
    tty.answers = ["wrong-pin-value"]
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None))
    assert out["ok"] is False and "PIN rejected" in out["error"]
    entry = _last_entry()
    assert entry["event"] == "refused" and entry["phase"] == "cancel"


def test_the_fifth_bad_pin_journals_a_lockout(desk_on, pin_set, broker_ok, tty):
    for _ in range(5):
        tty.answers = ["wrong-pin-value"]
        cli.cmd_cancel(_Args(order_id=1, account_number=None))
    events = [e["event"] for e in journal.read_all()]
    assert "pin_lockout" in events
    tty.answers = [pin_set]
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None))
    assert out["ok"] is False and "locked out" in out["error"]


# --------------------------------------------------------------------------- cancel: policy gate
def test_cancel_is_refused_when_the_account_is_not_allowlisted(desk_on, pin_set, monkeypatch, tty):
    tty.answers = [pin_set]
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: "9WI69999")
    out = cli.cmd_cancel(_Args(order_id=1, account_number=None))
    assert out["ok"] is False
    assert any("allowed_accounts" in r for r in out["refusals"])


def test_cancel_is_allowed_under_the_suite_halt_flag(desk_on, pin_set, broker_ok, tty):
    from cherrypick.core import home

    home.halt_flag_path().parent.mkdir(parents=True, exist_ok=True)
    home.halt_flag_path().touch()
    tty.answers = [pin_set]
    out = cli.cmd_cancel(_Args(order_id=42, account_number=None))
    assert out["ok"] is True and out["order_id"] == 42


def test_a_successful_cancel_is_journaled_with_the_masked_account(desk_on, pin_set, broker_ok, tty):
    tty.answers = [pin_set]
    cli.cmd_cancel(_Args(order_id=7, account_number=None))
    entry = _last_entry()
    assert entry["event"] == "cancelled"
    assert entry["account"] == "****1234"
    assert ACCOUNT not in cfgmod.journal_path().read_text()


def test_a_failed_broker_cancel_is_journaled_as_cancel_failed(desk_on, pin_set, monkeypatch, tty):
    tty.answers = [pin_set]
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: ACCOUNT)
    monkeypatch.setattr(
        cli, "_cancel", lambda cfg, order_id, account: {"ok": False, "error": "already filled"}
    )
    out = cli.cmd_cancel(_Args(order_id=7, account_number=None))
    assert out["ok"] is False
    assert _last_entry()["event"] == "cancel_failed"


# --------------------------------------------------------------------------- orders
class _FakeAcct:
    def __init__(self, number):
        self.account_number = number


@pytest.fixture
def fake_broker(monkeypatch):
    import cherrypick.core.broker as _broker

    import cherrypick.desk.session as _session

    monkeypatch.setattr(_session, "reset", lambda: None)
    monkeypatch.setattr(_session, "get_session", lambda cfg: "fake-session")

    async def fake_resolve_account(session, number=None, **kw):
        return _FakeAcct(number or ACCOUNT)

    async def fake_working_orders(account, session):
        return [{"order_id": 1, "status": "Live", "underlying_symbol": "APO", "account_number": ACCOUNT}]

    monkeypatch.setattr(_broker, "resolve_account", fake_resolve_account)
    monkeypatch.setattr(_broker, "working_orders", fake_working_orders)


def test_orders_needs_no_pin_once_the_gates_are_open(desk_on, fake_broker):
    out = cli.cmd_orders(_Args(account_number=None))
    assert out["ok"] is True
    assert out["account"] == "****1234"
    assert out["orders"][0]["underlying_symbol"] == "APO"


def test_orders_output_never_prints_a_full_account_number(desk_on, fake_broker, capsys):
    assert cli.main(["orders"]) == 0
    printed = capsys.readouterr().out
    assert ACCOUNT not in printed
    assert "****1234" in printed


def test_orders_is_gated(fake_broker):
    with pytest.raises(cli.DeskDisabled):
        cli.cmd_orders(_Args(account_number=None))


def test_orders_reports_an_unresolved_account(desk_on, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: None)
    out = cli.cmd_orders(_Args(account_number=None))
    assert out["ok"] is False and "could not resolve" in out["error"]
