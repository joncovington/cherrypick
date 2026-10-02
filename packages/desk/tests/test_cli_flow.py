"""propose -> confirm through the CLI, with the broker stubbed at the desk's own seams.

What is pinned: the ticket is spent by a bad PIN or code, the buying-power cap and the daily caps
bind at confirm, a `submitting` line must land before anything is sent, every submission carries
`desk-<ticket>` and a submit that raises is resolved by read-back rather than a retry, one submit
runs at a time, and nothing printed carries a full account number.
"""

import json
import os

import pytest
from conftest import write_config

from cherrypick.desk import cli, journal, pin
from cherrypick.desk import config as cfgmod

pytestmark = pytest.mark.unit

ACCOUNT = "5WT01234"
FLY = {
    "price": 0.90,
    "price_effect": "debit",
    "legs": [
        {
            "instrument_type": "Equity Option",
            "symbol": "XYZ   260807C00085000",
            "action": "buy to open",
            "quantity": 1,
        },
        {
            "instrument_type": "Equity Option",
            "symbol": "XYZ   260807C00091000",
            "action": "sell to open",
            "quantity": 2,
        },
        {
            "instrument_type": "Equity Option",
            "symbol": "XYZ   260807C00097000",
            "action": "buy to open",
            "quantity": 1,
        },
    ],
}


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _preflight_ok(change="-90.00"):
    return {
        "ok": True,
        "dry_run": True,
        "account_number": ACCOUNT,
        "buying_power": {"change_in_buying_power": change},
        "response": {"order": {"account-number": ACCOUNT}},
    }


@pytest.fixture
def broker(monkeypatch):
    """Stub every broker-touching helper. `broker.submits` records what reached `_submit`."""

    class _B:
        submits: list = []
        preflight = _preflight_ok()
        submit_result = {
            "ok": True,
            "dry_run": False,
            "account_number": ACCOUNT,
            "response": {"order": {"id": 777}},
        }

    b = _B()
    b.submits = []
    monkeypatch.setattr(cli, "_resolve_account", lambda cfg, req: cli._note_account(ACCOUNT))
    monkeypatch.setattr(cli, "_resolve_masked_account", lambda cfg, masked: ACCOUNT)
    monkeypatch.setattr(cli, "_preflight", lambda cfg, spec, account: b.preflight)

    def _submit(cfg, spec, account):
        b.submits.append(spec)
        return b.submit_result

    monkeypatch.setattr(cli, "_submit", _submit)
    return b


def _propose(order=FLY):
    return cli.cmd_propose(_Args(order=json.dumps(order), account_number=None))


def _confirm(rec, code=None):
    return cli.cmd_confirm(_Args(ticket=rec["ticket_id"], code=code or rec["confirmation_code"]))


def _events():
    return [e["event"] for e in journal.read_all()]


# --------------------------------------------------------------------------- propose
def test_propose_mints_a_ticket_and_carries_the_disclaimer(desk_on, pin_set, broker):
    out = _propose()
    assert out["ok"] is True, out
    assert out["disclaimer"].startswith("EXPERIMENTAL PROTOTYPE - educational use only")
    assert out["account"] == "****1234"
    path = cfgmod.desk_dir() / f"pending-{out['ticket_id']}.json"
    assert ACCOUNT not in path.read_text()


def test_propose_output_is_redacted(desk_on, pin_set, broker, capsys):
    assert cli.main(["propose", "--order", json.dumps(FLY)]) == 0
    printed = capsys.readouterr().out
    assert ACCOUNT not in printed
    assert "EXPERIMENTAL PROTOTYPE - educational use only" in printed


def test_propose_refuses_without_a_buying_power_change(desk_on, pin_set, broker):
    broker.preflight = {"ok": True, "buying_power": {"warnings": []}}
    out = _propose()
    assert out["ok"] is False
    assert any("no buying-power change" in r for r in out["refusals"])


def test_propose_refuses_buying_power_over_the_cap(desk_on, pin_set, broker):
    broker.preflight = _preflight_ok(change="-450.00")
    out = _propose()
    assert out["ok"] is False
    assert any("max_order_buying_power_dollars" in r for r in out["refusals"])


def test_propose_refuses_an_underlying_outside_the_allowlist(monkeypatch, pin_set, broker):
    write_config(allowed_underlyings=["SPY"])
    monkeypatch.setenv(cfgmod.EXPERIMENTAL_ENV, "1")
    out = _propose()
    assert out["ok"] is False and any("allowed_underlyings" in r for r in out["refusals"])


# --------------------------------------------------------------------------- confirm: happy path
def test_confirm_submits_with_the_desk_identifier_and_journals_first(desk_on, pin_set, broker, tty):
    rec = _propose()
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is True, out
    assert out["order_id"] == "777"
    assert broker.submits[0]["external_identifier"] == f"desk-{rec['ticket_id']}"
    events = _events()
    assert events.index("submitting") < events.index("submitted")
    submitted = journal.read_all()[-1]
    assert submitted["order_id"] == "777" and submitted["account"] == "****1234"
    assert not list(cfgmod.desk_dir().glob("pending-*"))


def test_confirm_output_is_redacted(desk_on, pin_set, broker, tty, capsys):
    rec = _propose()
    tty.answers = [pin_set]
    assert cli.main(["confirm", "--ticket", rec["ticket_id"], "--code", rec["confirmation_code"]]) == 0
    assert ACCOUNT not in capsys.readouterr().out


def test_confirm_has_no_pin_flag():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["confirm", "--ticket", "deadbeef", "--code", "X", "--pin", "y"])


# --------------------------------------------------------------------------- confirm: the ticket is spent
def test_a_bad_pin_spends_the_ticket(desk_on, pin_set, broker, tty):
    rec = _propose()
    tty.answers = ["wrong-pin-value"]
    assert _confirm(rec)["ok"] is False
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and "no pending ticket" in out["error"]
    assert broker.submits == []


def test_no_tty_spends_the_ticket(desk_on, pin_set, broker):
    rec = _propose()
    out = _confirm(rec)
    assert out["ok"] is False and "interactive terminal" in out["error"]
    assert not list(cfgmod.desk_dir().glob("pending-*"))


def test_a_wrong_code_spends_the_ticket(desk_on, pin_set, broker, tty):
    rec = _propose()
    tty.answers = [pin_set]
    assert _confirm(rec, code="ZZZZZZ")["ok"] is False
    tty.answers = [pin_set]
    assert "no pending ticket" in _confirm(rec)["error"]
    assert broker.submits == []


# --------------------------------------------------------------------------- confirm: gates at submit time
def test_the_halt_flag_at_confirm_time_refuses(desk_on, pin_set, broker, tty):
    from cherrypick.core import home

    rec = _propose()
    home.halt_flag_path().touch()
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and any("halt flag" in r for r in out["refusals"])
    assert broker.submits == []


def test_buying_power_is_rechecked_at_confirm(desk_on, pin_set, broker, tty):
    rec = _propose()
    broker.preflight = {"ok": True}
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and any("no buying-power change" in r for r in out["refusals"])
    assert broker.submits == []


def test_failed_attempts_count_toward_the_daily_cap(desk_on, pin_set, broker, tty):
    broker.submit_result = {"ok": False, "error": "rejected"}
    for _ in range(2):
        rec = _propose()
        tty.answers = [pin_set]
        assert _confirm(rec)["ok"] is False
    out = _propose()
    assert out["ok"] is False and any("daily order cap" in r for r in out["refusals"])
    assert len(broker.submits) == 2


def test_a_journal_that_cannot_record_submitting_blocks_the_submit(
    desk_on, pin_set, broker, tty, monkeypatch
):
    rec = _propose()

    def fail(*a, **k):
        raise journal.JournalError("disk full")

    monkeypatch.setattr(journal, "record_strict", fail)
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and "journal could not record" in out["error"]
    assert broker.submits == []


def test_one_submit_at_a_time(desk_on, pin_set, broker, tty):
    rec = _propose()
    lock = cfgmod.desk_dir() / "submit.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))  # held by a live process: this one
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and "another desk submission" in out["error"]
    assert broker.submits == []


def test_an_uncertain_submit_is_journaled_and_counted(desk_on, pin_set, broker, tty):
    broker.submit_result = {"ok": False, "uncertain": True, "error": "timeout; read-back failed"}
    rec = _propose()
    tty.answers = [pin_set]
    out = _confirm(rec)
    assert out["ok"] is False and "UNKNOWN" in out["error"]
    assert "uncertain" in _events()
    assert journal.today_totals(journal.read_all()[-1]["session_date"])[0] == 1


# --------------------------------------------------------------------------- _submit's read-back
class _Acct:
    account_number = ACCOUNT


@pytest.fixture
def sdk(monkeypatch):
    import cherrypick.core.broker as _broker

    import cherrypick.desk.session as _session

    class _S:
        place_raises = True
        today: list | Exception = []
        placed = 0

    s = _S()
    monkeypatch.setattr(_session, "reset", lambda: None)
    monkeypatch.setattr(_session, "get_session", lambda cfg: "session")

    async def resolve_account(session, number=None, **kw):
        return _Acct()

    async def place_order(account, session, order, *, live, serialize=None):
        s.placed += 1
        if s.place_raises:
            raise TimeoutError("read timed out")
        return {"ok": True, "response": {"order": {"id": 5}}}

    async def orders_today(account, session):
        if isinstance(s.today, Exception):
            raise s.today
        return s.today

    monkeypatch.setattr(_broker, "resolve_account", resolve_account)
    monkeypatch.setattr(_broker, "build_order", lambda spec: spec)
    monkeypatch.setattr(_broker, "place_order", place_order)
    monkeypatch.setattr(_broker, "orders_today", orders_today)
    return s


SPEC = {**FLY, "external_identifier": "desk-abcd1234"}


def test_a_raised_submit_found_by_its_identifier_is_ok(sdk):
    sdk.today = [{"order_id": 9, "external_identifier": "desk-abcd1234"}]
    out = cli._submit({}, SPEC, ACCOUNT)
    assert out["ok"] is True and out["recovered"] is True and out["order_id"] == "9"
    assert sdk.placed == 1  # never resubmitted


def test_a_raised_submit_absent_at_the_broker_is_a_failure(sdk):
    sdk.today = [{"order_id": 9, "external_identifier": "someone-else"}]
    out = cli._submit({}, SPEC, ACCOUNT)
    assert out["ok"] is False and not out.get("uncertain")
    assert sdk.placed == 1


def test_a_raised_submit_with_no_read_back_is_uncertain(sdk):
    sdk.today = ConnectionError("down")
    out = cli._submit({}, SPEC, ACCOUNT)
    assert out["ok"] is False and out["uncertain"] is True
    assert sdk.placed == 1


def test_a_clean_submit_passes_through(sdk):
    sdk.place_raises = False
    assert cli._submit({}, SPEC, ACCOUNT)["ok"] is True


# --------------------------------------------------------------------------- pin-set / pin-clear
def test_pin_set_first_time_and_then_needs_the_current_pin(tty):
    tty.answers = ["first-desk-pin", "first-desk-pin"]
    assert cli.cmd_pin_set(_Args())["ok"] is True
    assert "pin_set" in _events()

    tty.answers = ["not-the-current-pin", "second-desk-pin", "second-desk-pin"]
    out = cli.cmd_pin_set(_Args())
    assert out["ok"] is False
    assert pin.check("first-desk-pin") == pin.OK

    tty.answers = ["first-desk-pin", "second-desk-pin", "second-desk-pin"]
    assert cli.cmd_pin_set(_Args())["ok"] is True
    assert pin.check("second-desk-pin") == pin.OK


def test_pin_set_refuses_a_mismatched_repeat(tty):
    tty.answers = ["first-desk-pin", "first-desk-pix"]
    assert cli.cmd_pin_set(_Args())["ok"] is False
    assert not pin.is_set()


def test_pin_set_refuses_without_a_tty():
    assert cli.cmd_pin_set(_Args())["ok"] is False
    assert not pin.is_set()


def test_pin_set_has_no_pin_flag():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["pin-set", "--pin", "x"])


def test_pin_clear_needs_the_current_pin(pin_set, tty):
    tty.answers = ["not-the-current-pin"]
    assert cli.cmd_pin_clear(_Args())["ok"] is False
    assert pin.is_set()
    tty.answers = [pin_set]
    assert cli.cmd_pin_clear(_Args())["ok"] is True
    assert not pin.is_set()
    assert "pin_cleared" in _events()


def test_a_path_shaped_ticket_id_is_refused(desk_on, pin_set, broker):
    out = cli.cmd_confirm(_Args(ticket="../../x", code="ABCDEF"))
    assert out["ok"] is False and "not a ticket id" in out["error"]
