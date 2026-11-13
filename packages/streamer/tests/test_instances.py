"""`--instance NAME`: a producer for another broker login, everything of its own.

The guard that matters: two producers never share a cache, pid or stop file -- one writer per file is
the stream cache's invariant -- and an instance never authenticates with main's login.
"""

from __future__ import annotations

import json

import keyring
import pytest
from cherrypick.core import streamrequests as _sr
from cherrypick.core.auth import CLIENT_SECRET, REFRESH_TOKEN, SHARED_SERVICE, CredentialStore
from cherrypick.core.auth import logins as _logins

from cherrypick.streamer import cli as _cli
from cherrypick.streamer import config as _config
from cherrypick.streamer import daemon as _daemon


@pytest.fixture(autouse=True)
def _home(tmp_home):
    yield tmp_home
    _sr.set_instance(None)


@pytest.fixture
def mem_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, e: store.get((s, e)))
    monkeypatch.setattr(keyring, "set_password", lambda s, e, v: store.__setitem__((s, e), v))
    return store


def test_an_instance_never_shares_a_file_with_main():
    main, margin = _config.load(), _config.load("margin")
    for fn in (_config.cache_path, _config.pid_path, _config.stop_path, _config.log_path):
        assert fn(main) != fn(margin), fn.__name__
    assert _config.cache_path(margin).parent.name == "margin"
    assert _config.is_main(main) and _config.instance_of(margin) == "margin"


def test_main_reads_exactly_as_before():
    main = _config.load()
    assert "_instance" not in main
    assert _config.cache_path(main).name == "stream_cache.db"
    assert _config.cache_path(main).parent.name == "marketdata"
    assert _config.log_path(main).name == "streamer.log"


def test_an_instance_takes_main_s_tuning_but_never_its_symbols_or_paths(tmp_home):
    main_cfg = tmp_home / "config" / "streamer.json"
    main_cfg.parent.mkdir(parents=True, exist_ok=True)
    main_cfg.write_text(
        json.dumps(
            {"symbols": ["SPX"], "streamer": {"window_strike_count": 40}, "source": {"stream_cache_db": "x"}}
        ),
        encoding="utf-8",
    )
    margin = _config.load("margin")
    assert margin["streamer"] == {"window_strike_count": 40}
    assert "source" not in margin and _config.symbols(margin) == []
    assert _config.symbols(_config.load()) == ["SPX"]


def test_an_instance_with_its_own_file_reads_it(tmp_home):
    own = tmp_home / "config" / "streamer.margin.json"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({"symbols": ["IWM"]}), encoding="utf-8")
    assert _config.symbols(_config.load("margin")) == ["IWM"]


def test_an_instance_authenticates_with_its_own_login_only(mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "main")
    store = _daemon.credential_store("margin")
    assert store.service_name == _logins.login_service("margin")
    assert store.get_secret(CLIENT_SECRET) is None  # never main's
    assert "cherrypick-broker" in _daemon.credential_store(None).legacy_service_names


def test_an_instance_with_no_secrets_refuses_to_start(mem_keyring, capsys):
    assert _cli.main(["--instance", "margin"]) == 1
    assert "connect --login margin" in capsys.readouterr().out


def test_an_instance_status_names_it_and_reads_its_own_cache(mem_keyring, capsys):
    assert _cli.main(["--instance", "margin", "--status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["instance"] == "margin" and out["running"] is False


def test_an_instance_s_secrets_are_its_login_s(mem_keyring, capsys):
    store = _logins.login_store("margin")
    store.set_secret(CLIENT_SECRET, "a")
    store.set_secret(REFRESH_TOKEN, "b")
    assert _cli.main(["--instance", "margin", "--secrets-status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"client_secret": True, "refresh_token": True}
    assert _cli.main(["--instance", "margin", "--secrets-set"]) == 1


def test_an_instance_reads_its_own_requests():
    _sr.write_request("manage", ["SPY"])
    _sr.write_request("manage", [], legs=[".XYZ261120P50"], instance_name="margin")
    _sr.set_instance("margin")
    from cherrypick.streamer import registry as _registry

    assert _registry.union_symbols(seed_symbols=[]) == []
    assert _registry.union_legs() == [".XYZ261120P50"]


def test_a_bad_instance_name_is_refused(capsys):
    assert _cli.main(["--instance", "../x", "--status"]) == 1
