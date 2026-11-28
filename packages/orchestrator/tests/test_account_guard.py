"""`account --set` and `--clear` refuse while the account's live loop is armed today or its live book
holds a position or a working order (#1513). No override: the refusal lives in `accounts.py`, so the
console's onboarding and `connect` get it too.

The module's `live_loop --status` is stubbed (`accounts.module_status`); the arm record is a real file
under a temp home. Nothing here reads the real home, a keyring or a broker.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cherrypick.core import live as _live

from cherrypick.orchestrator import accounts, timeutil
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

_ACCTS = [
    {"account_number": "5WU111117777", "nickname": "Main", "account_type": "Individual"},
    {"account_number": "5WU222226666", "nickname": None, "account_type": "Individual"},
]
FULL = "5WU111117777"


class _Store:
    """In-memory CredentialStore keyed by (service, prefix)."""

    mem: dict = {}

    def __init__(self, service, legacy_service_names=(), prefix=None):
        self.key = (service, prefix)
        _Store.mem.setdefault(self.key, {})

    def get_secret(self, key):
        return _Store.mem[self.key].get(key)

    def set_secret(self, key, value):
        _Store.mem[self.key][key] = value

    def delete_secret(self, key):
        _Store.mem[self.key].pop(key, None)


def _today() -> str:
    return timeutil.now_et().date().isoformat()


def flat(**extra) -> dict:
    """A meic-shaped status: not armed, no position, no working order."""
    return {
        "ok": True,
        "date": _today(),
        "armed_for": None,
        "open_positions": 0,
        "pending_orders": [],
        **extra,
    }


def ladder_flat(**extra) -> dict:
    return {"ok": True, "armed_today": False, "armed_for": None, "open_lots": 0, "working_orders": 0, **extra}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """meic and ladder checked out, each with a designated account; `statuses` maps (module, login) to
    the status the stub answers (None = cannot be read). Returns (cfg, statuses)."""
    _Store.mem = {}
    for name in ("meic", "ladder"):
        (tmp_path / name).mkdir()
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "meic.json").write_text(
        json.dumps({"live_accounts": {"margin": {"enabled": True}}}), encoding="utf-8"
    )
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    monkeypatch.setattr(accounts, "CredentialStore", _Store)
    monkeypatch.setattr(
        accounts, "_tt", lambda root, *argv, tool=None, **kw: {"ok": True, "accounts": _ACCTS}
    )
    cfg = {
        "broker": {"logins": [{"name": "margin"}]},
        "modules": {
            "meic": {"enabled": True, "path": str(tmp_path / "meic"), "keyring_service": "meic-test"},
            "ladder": {"enabled": True, "path": str(tmp_path / "ladder"), "keyring_service": "ladder-test"},
        },
    }
    statuses: dict = {("meic", None): flat(), ("ladder", None): ladder_flat(), ("meic", "margin"): flat()}
    asked: list = []

    def status(_cfg, module, login=None):
        asked.append((module, login))
        return statuses[(module, login)]

    monkeypatch.setattr(accounts, "module_status", status)
    _Store.mem[("meic-test", None)] = {"account_number": FULL}
    _Store.mem[("ladder-test", None)] = {"account_number": FULL}
    statuses["asked"] = asked
    return cfg, statuses


def _designated(service="meic-test", prefix=None):
    return _Store.mem.get((service, prefix), {}).get("account_number")


def _refused(out: dict, *fragments: str) -> None:
    assert out["ok"] is False
    for fragment in fragments:
        assert fragment in out["error"], out["error"]
    assert "7777" in out["error"] and FULL not in out["error"]  # the account is masked


# --------------------------------------------------------------------------- one module's own account
def test_a_flat_unarmed_module_may_change_and_clear_its_account(world):
    cfg, _ = world
    assert accounts.set_account(cfg, "meic", "6666")["ok"] is True
    assert _designated() == "5WU222226666"
    assert accounts.clear_account(cfg, "meic")["ok"] is True
    assert _designated() is None


def test_an_armed_module_refuses_set_and_clear_and_writes_nothing(world):
    cfg, statuses = world
    statuses[("meic", None)] = flat(armed_for=_today())
    out = accounts.set_account(cfg, "meic", "6666")
    _refused(out, "meic", "armed for today")
    _refused(accounts.clear_account(cfg, "meic"), "meic", "armed for today")
    assert _designated() == FULL


def test_the_arm_record_alone_counts_as_armed(world):
    cfg, _ = world
    _live.write_arm_record("meic", date=_today(), armed_by="test", at="2026-11-28T09:00:00-05:00")
    _refused(accounts.set_account(cfg, "meic", "6666"), "meic", "armed for today")
    assert _designated() == FULL


def test_an_unarmed_module_that_holds_a_position_refuses(world):
    """MEIC's main account manages its book unarmed, so armed alone is not enough."""
    cfg, statuses = world
    statuses[("meic", None)] = flat(open_positions=2)
    _refused(accounts.set_account(cfg, "meic", "6666"), "meic", "2 open position(s)")
    _refused(accounts.clear_account(cfg, "meic"), "meic", "2 open position(s)")
    assert _designated() == FULL


def test_a_working_order_refuses(world):
    cfg, statuses = world
    statuses[("meic", None)] = flat(pending_orders=["ic-1"])
    _refused(accounts.set_account(cfg, "meic", "6666"), "meic", "1 working order(s)")
    _refused(accounts.clear_account(cfg, "meic"), "meic", "1 working order(s)")


def test_an_unresolved_broker_submission_or_orphan_refuses(world):
    cfg, statuses = world
    statuses[("meic", None)] = flat(broker_held={"unresolved": ["x"], "unrecorded": {}})
    _refused(accounts.set_account(cfg, "meic", "6666"), "unresolved")
    statuses[("meic", None)] = flat(orphaned_orders=1)
    _refused(accounts.set_account(cfg, "meic", "6666"), "not in its ledger")


def test_a_past_session_still_unsettled_refuses(world):
    """`open_positions` counts today's book only; a condor left from yesterday shows as overdue."""
    cfg, statuses = world
    statuses[("meic", None)] = flat(overdue_settlement=[{"trade_date": "2026-11-26"}])
    _refused(accounts.set_account(cfg, "meic", "6666"), "meic", "still unsettled")
    _refused(accounts.clear_account(cfg, "meic"), "meic", "still unsettled")


def test_a_status_that_cannot_be_read_refuses(world):
    cfg, statuses = world
    statuses[("meic", None)] = None
    _refused(accounts.set_account(cfg, "meic", "6666"), "meic", "cannot be read")
    _refused(accounts.clear_account(cfg, "meic"), "meic", "cannot be read")
    assert _designated() == FULL


def test_a_status_that_does_not_say_what_it_holds_refuses(world):
    cfg, statuses = world
    statuses[("meic", None)] = {"ok": True, "armed_for": None}
    out = accounts.set_account(cfg, "meic", "6666")
    _refused(out, "position is not known", "working order is not known")


def test_the_ladder_refuses_on_a_held_lot_or_a_working_order(world):
    cfg, statuses = world
    statuses[("ladder", None)] = ladder_flat(open_lots=3)
    _refused(accounts.set_account(cfg, "ladder", "6666"), "ladder", "3 open position(s)")
    statuses[("ladder", None)] = ladder_flat(working_orders=1)
    _refused(accounts.clear_account(cfg, "ladder"), "ladder", "1 working order(s)")
    statuses[("ladder", None)] = ladder_flat(armed_today=True, armed_for=_today())
    _refused(accounts.set_account(cfg, "ladder", "6666"), "ladder", "armed for today")
    statuses[("ladder", None)] = ladder_flat(open_lots=None, working_orders=None)  # an unreadable ledger
    _refused(accounts.set_account(cfg, "ladder", "6666"), "not known")


def test_another_modules_book_does_not_block_this_one(world):
    cfg, statuses = world
    statuses[("ladder", None)] = ladder_flat(open_lots=3)
    assert accounts.set_account(cfg, "meic", "6666")["ok"] is True
    assert ("ladder", None) not in statuses["asked"]


# --------------------------------------------------------------------------- the suite-wide account
def test_the_shared_account_refuses_while_a_module_that_inherits_it_is_armed_or_holds(world):
    cfg, statuses = world
    _Store.mem[("meic-test", None)] = {}  # meic has no designation of its own: it inherits
    _Store.mem[(accounts.SHARED_SERVICE, None)] = {"account_number": FULL}
    statuses[("meic", None)] = flat(armed_for=_today())
    _refused(accounts.set_shared_account(cfg, "6666"), "meic", "armed for today")
    _refused(accounts.clear_shared_account(cfg), "meic", "armed for today")
    statuses[("meic", None)] = flat(open_positions=1)
    _refused(accounts.set_shared_account(cfg, "6666"), "meic", "1 open position(s)")
    assert _Store.mem[(accounts.SHARED_SERVICE, None)]["account_number"] == FULL


def test_the_shared_account_ignores_a_module_with_its_own_designation(world):
    cfg, statuses = world
    _Store.mem[("meic-test", None)] = {}  # meic inherits and is flat
    statuses[("ladder", None)] = ladder_flat(open_lots=5)  # the ladder has its own account: not affected
    out = accounts.set_shared_account(cfg, "6666")
    assert out["ok"] is True
    assert _Store.mem[(accounts.SHARED_SERVICE, None)]["account_number"] == "5WU222226666"
    assert ("ladder", None) not in statuses["asked"]
    assert accounts.clear_shared_account(cfg)["ok"] is True


# --------------------------------------------------------------------------- one login's account
def test_a_logins_account_is_guarded_by_that_logins_book_alone(world):
    cfg, statuses = world
    statuses[("meic", None)] = flat(open_positions=4)  # the main account's book is not this login's
    assert accounts.set_login_account(cfg, "meic", "margin", "7777")["ok"] is True
    statuses[("meic", "margin")] = flat(open_positions=1)
    _refused(accounts.set_login_account(cfg, "meic", "margin", "6666"), "meic on login margin", "1 open")
    _refused(accounts.clear_login_account(cfg, "meic", "margin"), "meic on login margin", "1 open")
    assert _designated("cherrypick-broker.margin", "meic") == FULL
    statuses[("meic", "margin")] = flat(armed_for=_today())
    _refused(accounts.clear_login_account(cfg, "meic", "margin"), "armed for today")
    statuses[("meic", "margin")] = None
    _refused(accounts.clear_login_account(cfg, "meic", "margin"), "cannot be read")


# --------------------------------------------------------------------------- every caller gets it
def test_the_account_command_exits_non_zero_on_the_refusal(world, capsys):
    from cherrypick import cli

    cfg, statuses = world
    statuses[("meic", None)] = flat(open_positions=1)
    args = SimpleNamespace(module="meic", set="6666", clear=False, yes=True, login=None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_account(cfg, args)
    assert exc.value.code == 1
    assert "1 open position(s)" in capsys.readouterr().out
    args = SimpleNamespace(module="meic", set=None, clear=True, yes=True, login=None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_account(cfg, args)
    assert exc.value.code == 1
    assert _designated() == FULL


def test_the_account_command_exits_non_zero_on_the_shared_and_login_refusals(world, capsys):
    from cherrypick import cli

    cfg, statuses = world
    _Store.mem[("meic-test", None)] = {}  # meic inherits the shared account
    statuses[("meic", None)] = flat(open_positions=1)
    statuses[("meic", "margin")] = flat(open_positions=2)
    cases = [
        SimpleNamespace(module=None, set="6666", clear=False, yes=True, login=None),
        SimpleNamespace(module=None, set=None, clear=True, yes=True, login=None),
        SimpleNamespace(module="meic", set="7777", clear=False, yes=True, login="margin"),
        SimpleNamespace(module="meic", set=None, clear=True, yes=True, login="margin"),
    ]
    for args in cases:
        with pytest.raises(SystemExit) as exc:
            cli.cmd_account(cfg, args)
        assert exc.value.code == 1
        assert "open position(s)" in capsys.readouterr().out


# --------------------------------------------------------------------------- odd statuses
@pytest.mark.parametrize("value", ["many", True, object(), float("nan"), float("inf"), {"a": 1}])
def test_a_count_that_is_not_a_number_is_unknown_not_an_error(value):
    got = accounts._count({"open_positions": value}, "open_positions")
    assert got == (1 if isinstance(value, dict) else None)


def test_a_string_count_that_parses_is_a_count():
    assert accounts._count({"open_lots": "3"}, "open_lots") == 3


def test_a_status_with_a_junk_count_refuses_rather_than_raises(world):
    cfg, statuses = world
    statuses[("meic", None)] = flat(open_positions="many")
    _refused(accounts.set_account(cfg, "meic", "6666"), "position is not known")


def test_a_module_with_no_keyring_service_still_inherits_the_shared_account(world):
    cfg, statuses = world
    cfg["modules"]["ladder"]["keyring_service"] = None  # no service of its own: it reads the shared one
    _Store.mem[("meic-test", None)] = {"account_number": FULL}  # meic has its own, so it is not affected
    _Store.mem[(accounts.SHARED_SERVICE, None)] = {"account_number": FULL}
    statuses[("ladder", None)] = ladder_flat(open_lots=2)
    _refused(accounts.set_shared_account(cfg, "6666"), "ladder", "2 open position(s)")
    _refused(accounts.clear_shared_account(cfg), "ladder", "2 open position(s)")


# --------------------------------------------------------------------------- a retired module
@pytest.fixture
def retired(world, tmp_path):
    """The world plus flies: switched off in the suite config and its own live switch off, inheriting
    the shared account."""
    cfg, statuses = world
    (tmp_path / "flies").mkdir()
    (tmp_path / "home" / "config" / "flies.json").write_text(
        json.dumps({"live": {"enabled": False}}), encoding="utf-8"
    )
    cfg["modules"]["flies"] = {
        "enabled": False,
        "path": str(tmp_path / "flies"),
        "keyring_service": "flies-test",
    }
    _Store.mem[("meic-test", None)] = {"account_number": FULL}  # only flies inherits
    _Store.mem[(accounts.SHARED_SERVICE, None)] = {"account_number": FULL}
    statuses[("flies", None)] = {"ok": True, "armed_for": None, "open_positions": 0, "pending_orders": 0}
    return cfg, statuses


def test_a_retired_module_whose_status_cannot_be_read_is_a_warning_not_a_refusal(retired):
    cfg, statuses = retired
    statuses[("flies", None)] = None
    out = accounts.set_shared_account(cfg, "6666")
    assert out["ok"] is True and "flies" in out["warnings"][0] and "switched off" in out["warnings"][0]
    out = accounts.clear_shared_account(cfg)
    assert out["ok"] is True and out["warnings"]


def test_a_retired_module_that_does_not_say_what_it_holds_is_a_warning(retired):
    cfg, statuses = retired
    statuses[("flies", None)] = {"ok": True, "armed_for": None}
    out = accounts.set_shared_account(cfg, "6666")
    assert out["ok"] is True and len(out["warnings"]) == 2


def test_a_retired_module_that_holds_or_is_armed_still_refuses(retired):
    cfg, statuses = retired
    statuses[("flies", None)] = {"ok": True, "armed_for": None, "open_positions": 1, "pending_orders": 0}
    _refused(accounts.set_shared_account(cfg, "6666"), "flies", "1 open position(s)")
    statuses[("flies", None)] = {"ok": True, "armed_for": _today(), "open_positions": 0, "pending_orders": 0}
    _refused(accounts.clear_shared_account(cfg), "flies", "armed for today")


def test_a_module_that_is_enabled_or_live_switched_on_stays_fail_closed(retired, tmp_path):
    cfg, statuses = retired
    statuses[("flies", None)] = None
    cfg["modules"]["flies"]["enabled"] = True
    _refused(accounts.set_shared_account(cfg, "6666"), "flies", "cannot be read")
    cfg["modules"]["flies"]["enabled"] = False
    (tmp_path / "home" / "config" / "flies.json").write_text(
        json.dumps({"live": {"enabled": True}}), encoding="utf-8"
    )
    _refused(accounts.set_shared_account(cfg, "6666"), "flies", "cannot be read")
    (tmp_path / "home" / "config" / "flies.json").unlink()  # its switch is unknown: not retired
    _refused(accounts.set_shared_account(cfg, "6666"), "flies", "cannot be read")


def test_the_arm_record_decides_for_a_retired_module_too(retired):
    cfg, _ = retired
    _live.write_arm_record("flies", date=_today(), armed_by="test", at="2026-11-28T09:00:00-05:00")
    _refused(accounts.set_shared_account(cfg, "6666"), "flies", "armed for today")


def test_the_onboarding_step_reports_the_refusal(world):
    """`connect` calls `accounts.set_account` and shows what it answers; the guard needs no second copy."""
    from cherrypick.orchestrator import connect

    cfg, statuses = world
    statuses[("meic", None)] = flat(armed_for=_today())
    out = connect._select_account(cfg, "meic", prompt_fn=lambda _: "2")
    assert out["ok"] is False and out["changed"] is False
    assert "armed for today" in out["error"]
    assert _designated() == FULL
