"""Lifecycle tests that need no broker: symbol/path resolution and the --status / --stop contract.

They exercise the daemon's status shape (a single merged object with the keys the orchestrator watchdog
reads) and the not-running paths, against a temp $CHERRYPICK_HOME so nothing touches a real cache.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import config as _config  # noqa: E402
import daemon as _daemon  # noqa: E402


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
    proves status() reads the cache without a live daemon and the merge keeps a single flat object."""
    from cherrypick.core import streamcache

    cache = _config.cache_path({})
    cache.parent.mkdir(parents=True, exist_ok=True)
    conn = streamcache.connect(cache)  # creates the DDL
    conn.close()

    st = _daemon.status({})
    assert st["running"] is False
    assert st["oldest_event_age_s"] is None
    # A single flat dict (no nested status object) is what util.first_json needs.
    assert all(not isinstance(v, dict) for v in st.values())


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
    conn.execute("INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
                 ("SPX", 7517.0, 0.0, 0.0, now - 3600))
    conn.execute("INSERT INTO stream_quotes(symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
                 "VALUES (?,?,?,?,?,?,?)", (".SPXW260722C7500", 1.0, 1.2, 1.1, 1, 1, now - 5))
    conn.commit()
    conn.close()

    st = _daemon.status({})
    assert st["oldest_event_age_s"] < 60, "global age is kept fresh by the option quote"
    assert st["underlyings_stale_age_s"] >= 3500, "underlying spot age reflects the frozen SPX feed"


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
    monkeypatch.setattr(keyring, "set_password",
                        lambda service, key, value: store.__setitem__((service, key), value))
    return store


def test_secrets_status_and_set(fake_keyring):
    import credentials as _credentials

    assert _credentials.status() == {"client_secret": False, "refresh_token": False}
    written = _credentials.set_secrets(prompt_fn=lambda p: "value-for-" + p)
    assert written == ["client_secret", "refresh_token"]
    assert _credentials.status() == {"client_secret": True, "refresh_token": True}
    # Stored under the shared service + production: prefix, so MEIC/earnings/gex read the same entry.
    assert fake_keyring[("meicagent", "production:client_secret")] == "value-for-client_secret: "


def test_secrets_set_empty_input_skips(fake_keyring):
    import credentials as _credentials

    assert _credentials.set_secrets(prompt_fn=lambda p: "") == []
    assert _credentials.status() == {"client_secret": False, "refresh_token": False}
