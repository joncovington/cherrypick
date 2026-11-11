"""`connect --login NAME` and `account --login NAME`: an extra broker login, never a designation.

- The name lands in `broker.logins` by a byte splice: every other byte of the config survives.
- The secrets go through core's hidden-input child (`cherrypick.core.auth setup --login`).
- `account --login` lists; setting or clearing a designation there is refused.
"""

import json
import types

import pytest

from cherrypick.orchestrator import accounts, connect

pytestmark = pytest.mark.unit

_CFG_TEXT = '{\n  "_header": "keep me",\n  "modules": {}\n}\n'


def test_a_login_is_appended_with_every_other_byte_kept():
    out = connect.add_login_to_config_text(_CFG_TEXT, "margin", "Reg T", True)
    assert out.startswith('{\n  "_header": "keep me",\n  "modules": {},\n  "broker": ')
    assert json.loads(out)["broker"] == {"logins": [{"name": "margin", "label": "Reg T", "manage": True}]}


def test_an_existing_login_is_replaced_by_name_and_others_kept():
    text = '{"broker": {"note": "x", "logins": [{"name": "margin"}, {"name": "ira2"}]}}'
    out = json.loads(connect.add_login_to_config_text(text, "margin", None, False))
    assert out["broker"]["note"] == "x"
    assert out["broker"]["logins"] == [{"name": "ira2"}, {"name": "margin", "label": None, "manage": False}]


def test_a_broker_block_without_logins_gains_the_list():
    out = json.loads(connect.add_login_to_config_text('{"broker": {"note": "x"}}', "margin", None, True))
    assert out["broker"] == {"note": "x", "logins": [{"name": "margin", "label": None, "manage": True}]}


def test_run_login_refuses_main_and_bad_names():
    assert connect.run_login({}, "main")["ok"] is False
    assert "not a login name" in connect.run_login({}, "Bad")["error"]


def test_run_login_delegates_secrets_and_writes_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(_CFG_TEXT, encoding="utf-8")
    spawned = []
    monkeypatch.setattr(
        connect.subprocess,
        "run",
        lambda argv, **kw: spawned.append(argv) or types.SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(accounts, "_first_broker_module", lambda cfg: (None, None, None, None))
    from cherrypick.orchestrator import configedit

    monkeypatch.setattr(configedit, "_target_path", lambda cfg, target: cfg_path)
    monkeypatch.setattr(configedit, "backup_dir", lambda: tmp_path / "backups")
    answers = iter(["Reg T", "y"])
    out = connect.run_login({}, "margin", prompt_fn=lambda prompt: next(answers))
    assert out["ok"] is True and out["manage"] is True
    assert spawned and spawned[0][1:] == ["-m", "cherrypick.core.auth", "setup", "--login", "margin"]
    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert written["_header"] == "keep me"
    assert written["broker"]["logins"][0]["name"] == "margin"


def test_list_login_needs_the_login_in_config():
    out = accounts.list_login({"modules": {}}, "margin")
    assert out["ok"] is False and "broker.logins" in out["error"]


def test_list_login_lists_masked_and_designates_nothing(monkeypatch, tmp_path):
    cfg = {"modules": {}, "broker": {"logins": [{"name": "margin"}]}}
    monkeypatch.setattr(accounts, "_first_broker_module", lambda cfg: ("meic", {}, tmp_path, ["-m", "x"]))
    seen = {}

    def fake_tt(root, *argv, tool=None, login=None):
        seen["login"] = login
        return {"ok": True, "accounts": [{"account_number": "9ZZ000001234", "account_type": "Individual"}]}

    monkeypatch.setattr(accounts, "_tt", fake_tt)
    out = accounts.list_login(cfg, "margin")
    assert seen["login"] == "margin"
    assert out["accounts"] == [
        {"account": "****1234", "nickname": None, "type": "Individual", "designated": False}
    ]
    assert "000001234" not in json.dumps(out)


def test_logins_status_reports_presence_only(mem_keyring_orch):
    from cherrypick.core.auth import CLIENT_SECRET, REFRESH_TOKEN, logins

    store = logins.login_store("margin")
    store.set_secret(CLIENT_SECRET, "s")
    store.set_secret(REFRESH_TOKEN, "t")
    cfg = {"broker": {"logins": [{"name": "margin", "label": "Reg T"}, {"name": "ira2"}]}}
    out = accounts.logins_status(cfg)
    assert out == [
        {"login": "margin", "label": "Reg T", "manage": False, "credentials": "present"},
        {"login": "ira2", "label": None, "manage": False, "credentials": "missing"},
    ]
    assert '"s"' not in json.dumps(out)


@pytest.fixture
def mem_keyring_orch(monkeypatch):
    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, e: store.get((s, e)))
    monkeypatch.setattr(keyring, "set_password", lambda s, e, v: store.__setitem__((s, e), v))
    return store
