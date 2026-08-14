"""Lifecycle tests that need no broker: symbol/path resolution and the --status / --stop contract.

They exercise the daemon's status shape (a single merged object with the keys the orchestrator watchdog
reads) and the not-running paths, against a temp $CHERRYPICK_HOME so nothing touches a real cache.
"""

import json

import pytest

from cherrypick.streamer import config as _config
from cherrypick.streamer import daemon as _daemon


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point the whole cherrypick tree at a tmp dir so cache/log/pid paths resolve under it."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def test_symbols_precedence():
    assert _config.symbols({}) == ["SPX"]
    assert _config.symbols({"symbols": ["spx", " xsp "]}) == ["SPX", "XSP"]
    assert _config.symbols({"symbols": ["SPX"]}, cli_override=["qqq"]) == ["QQQ"]


def test_cache_path_default_and_override(home):
    default = _config.cache_path({})
    assert default == home / "data" / "marketdata" / "stream_cache.db"
    override = _config.cache_path({"source": {"stream_cache_db": str(home / "elsewhere.db")}})
    assert override == home / "elsewhere.db"


def test_pid_colocated_with_cache(home):
    assert _config.pid_path({}) == _config.cache_path({}).parent / "streamer.pid"


def test_status_no_cache_reports_not_running(home):
    st = _daemon.status({})
    assert st["running"] is False
    assert st["pid"] is None
    assert st["oldest_event_age_s"] is None
    assert st["stale_age_s"] is None
    # Not running -> not flagged stale (a stopped daemon isn't a silent stall).
    assert st["stale_warning"] is False


def test_status_empty_cache_reports_not_running(home):
    """An initialized-but-empty cache (schema present, no events) still reports not-running/no-age —
    proves status() reads the cache without a live daemon and the merge keeps a single JSON object
    (util.first_json needs exactly one top-level object on stdout — a plain json.loads handles
    nested dict VALUES like symbol_health/chain_fetch_errors fine; the old, stricter "every value is
    flat" assertion here was incidental, not load-bearing)."""
    from cherrypick.core import streamcache

    cache = _config.cache_path({})
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = streamcache.connect(cache)  # creates the DDL
    conn.close()

    st = _daemon.status({})
    assert st["running"] is False
    assert st["oldest_event_age_s"] is None
    assert st["symbol_health"] == {}
    assert st["chain_fetch_errors"] == {}
    assert json.loads(json.dumps(st)) == st  # still exactly one JSON-serializable object


def test_status_tracks_underlying_spot_freshness_separately(home):
    """The 2026-07-22 stall: underlying spot froze while option quotes streamed on, so the global
    'freshest anything' age stayed fresh and masked the dead spot feed. status() must report the
    subscribed underlyings' spot age separately so the watchdog can catch it."""
    import time as _time

    from cherrypick.core import streamcache

    cache = _config.cache_path({})
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = streamcache.connect(cache)
    now = _time.time()
    # SPX (a default-seeded underlying) frozen an hour ago; an option quote 5s ago keeps global fresh.
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("SPX", 7517.0, 0.0, 0.0, now - 3600),
    )
    conn.execute(
        "INSERT INTO stream_quotes(symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (".SPXW260722C7500", 1.0, 1.2, 1.1, 1, 1, now - 5),
    )
    conn.commit()
    conn.close()

    st = _daemon.status({})
    assert st["oldest_event_age_s"] < 60, "global age is kept fresh by the option quote"
    assert st["underlyings_stale_age_s"] >= 3500, "underlying spot age reflects the frozen SPX feed"


def test_status_surfaces_chain_fetch_errors(home):
    """A symbol whose chain fetch is currently failing (stream_symbol_health.chain_fetch_error set)
    must show up in chain_fetch_errors even while every other symbol's aggregate ages look healthy
    — this is the per-symbol signal the 2026-07-31 XSP incident needed and didn't have."""
    from cherrypick.core import streamcache

    cache = _config.cache_path({})
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = streamcache.connect(cache)
    streamcache.upsert_symbol_health(conn, "XSP", chain_fetch_error="Couldn't parse response: <html>")
    streamcache.upsert_symbol_health(
        conn, "QQQ", chain_loaded_at="2026-07-31T12:00:00+00:00", chain_fetch_error=None
    )
    conn.close()

    st = _daemon.status({})
    assert st["chain_fetch_errors"] == {"XSP": "Couldn't parse response: <html>"}
    assert st["symbol_health"]["QQQ"]["chain_fetch_error"] is None
    assert st["symbol_health"]["XSP"]["chain_fetch_error"] == "Couldn't parse response: <html>"


def test_status_echoes_extra_expirations_and_their_health_rows(home):
    """The registry's extra-expiration requests surface in --status two ways: the union itself
    (what is currently being asked for) and the per-date `SYMBOL@date` symbol_health rows (how the
    serving is going), so a calendars-shaped module's missing chain is diagnosable from one JSON."""
    from cherrypick.core import streamcache

    from cherrypick.streamer import registry as _registry

    _registry.write_request("calendars", ["SPX"], expirations={"SPX": ["2099-01-15"]})
    cache = _config.cache_path({})
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = streamcache.connect(cache)
    streamcache.upsert_symbol_health(conn, "SPX@2099-01-15", chain_fetch_error="expiration not listed")
    conn.close()

    st = _daemon.status({})
    assert st["extra_expirations"] == {"SPX": ["2099-01-15"]}
    assert st["chain_fetch_errors"]["SPX@2099-01-15"] == "expiration not listed"
    assert json.loads(json.dumps(st)) == st  # still exactly one JSON-serializable object


def test_stop_when_not_running(home):
    result = _daemon.stop({})
    assert result == {"ok": False, "error": "Streamer not running"}


@pytest.fixture()
def fake_keyring(monkeypatch):
    """Back cherrypick.core.auth's keyring with an in-memory dict so credential tests never touch the
    real OS keyring (the dev box has real suite creds — reading them would make assertions flaky)."""
    import keyring

    store: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, key: store.get((service, key)))
    monkeypatch.setattr(
        keyring, "set_password", lambda service, key, value: store.__setitem__((service, key), value)
    )
    return store


def test_secrets_status_and_set(fake_keyring):
    from cherrypick.streamer import credentials as _credentials

    assert _credentials.status() == {"client_secret": False, "refresh_token": False}
    written = _credentials.set_secrets(prompt_fn=lambda p: "value-for-" + p)
    assert written == ["client_secret", "refresh_token"]
    assert _credentials.status() == {"client_secret": True, "refresh_token": True}
    # Stored under the shared service + production: prefix, so MEIC/earnings/gex read the same entry.
    assert fake_keyring[("meicagent", "production:client_secret")] == "value-for-client_secret: "


def test_secrets_set_empty_input_skips(fake_keyring):
    from cherrypick.streamer import credentials as _credentials

    assert _credentials.set_secrets(prompt_fn=lambda p: "") == []
    assert _credentials.status() == {"client_secret": False, "refresh_token": False}
