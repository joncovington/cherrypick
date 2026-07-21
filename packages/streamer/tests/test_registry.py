"""The subscription registry: per-module request files, the union the streamer reads (symbols + legs,
where legs may be pulled from a module's own trades DB via a leg_sources query), and how the daemon wires
that union into the engine. No broker required."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import daemon as _daemon  # noqa: E402
import registry as _registry  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def test_write_then_read_round_trip(home):
    _registry.write_request("flies", ["spx", " xsp "])
    assert _registry.union_symbols() == ["SPX", "XSP"]
    assert _registry.request_path("flies").exists()


def test_union_symbols_across_modules_plus_seed(home):
    _registry.write_request("meic", ["SPX", "XSP"])
    _registry.write_request("flies", ["SPX"])
    _registry.write_request("gex", ["SPX", "QQQ"])
    assert _registry.union_symbols(seed_symbols=["ndx"]) == ["NDX", "QQQ", "SPX", "XSP"]


def test_explicit_legs_list(home):
    _registry.write_request("meic", ["SPX"], legs=[".SPX250620C6900"])
    assert _registry.union_legs() == [".SPX250620C6900"]


def test_corrupt_file_is_skipped_not_fatal(home):
    _registry.write_request("flies", ["SPX"])
    (_registry.requests_dir() / "broken.json").write_text("{not valid json", encoding="utf-8")
    assert _registry.union_symbols() == ["SPX"]
    assert _registry.union_legs() == []


# --------------------------------------------------------------------------- leg_sources (DB pull)
def _make_ic_trades_db(path: Path) -> None:
    """A minimal ic_trades-shaped DB, mirroring what MEIC's streamer used to read directly."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE ic_trades (status TEXT, put_symbol TEXT, call_symbol TEXT, "
        "long_put_symbol TEXT, long_call_symbol TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades VALUES (?, ?, ?, ?, ?)",
        [
            ("open", ".SPX250620P6800", ".SPX250620C6900", ".SPX250620P6790", ".SPX250620C6910"),
            ("closed", ".SPX250620P6700", ".SPX250620C7000", None, None),  # excluded by the WHERE
            ("pending", ".XSP250620P680", ".XSP250620C690", None, None),
        ],
    )
    conn.commit()
    conn.close()


_IC_QUERY = ("SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol FROM ic_trades "
             "WHERE status IN ('pending','open','partial','partial_entry')")


def test_leg_sources_pulls_open_legs_from_db(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX", "XSP"],
                            leg_sources=[{"db": str(db), "query": _IC_QUERY}])
    legs = _registry.union_legs()
    # Every non-null cell of the open/pending rows; the 'closed' row is filtered out by the query.
    assert legs == sorted([
        ".SPX250620P6800", ".SPX250620C6900", ".SPX250620P6790", ".SPX250620C6910",
        ".XSP250620P680", ".XSP250620C690",
    ])


def test_leg_sources_union_with_explicit_legs(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX"], legs=[".SPX250620C9999"],
                            leg_sources=[{"db": str(db), "query": _IC_QUERY}])
    legs = _registry.union_legs()
    assert ".SPX250620C9999" in legs and ".SPX250620C6900" in legs


def test_leg_sources_missing_db_is_not_fatal(home, tmp_path):
    _registry.write_request("meic", ["SPX"],
                            leg_sources=[{"db": str(tmp_path / "nope.db"), "query": _IC_QUERY}])
    assert _registry.union_legs() == []  # missing DB contributes nothing, no crash


def test_leg_sources_rejects_non_select(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX"],
                            leg_sources=[{"db": str(db), "query": "DELETE FROM ic_trades"}])
    assert _registry.union_legs() == []  # non-SELECT ignored (belt-and-suspenders; DB is opened ?mode=ro)


def test_leg_sources_opens_readonly(home, tmp_path):
    """The declared DB is opened read-only — a would-be write in the query can never mutate it."""
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX"],
                            leg_sources=[{"db": str(db), "query": "SELECT put_symbol FROM ic_trades"}])
    _registry.union_legs()
    # DB still intact and unchanged.
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM ic_trades").fetchone()[0] == 3
    conn.close()


# --------------------------------------------------------------------------- daemon wiring
def test_build_streamer_uses_registry_union(home, tmp_path):
    # make_session_factory is lazy (no keyring hit at construction), so build_streamer is broker-free.
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX", "XSP"],
                            leg_sources=[{"db": str(db), "query": _IC_QUERY}])
    _registry.write_request("flies", ["SPX"])
    streamer = _daemon.build_streamer({"symbols": ["QQQ"]})  # config seed adds QQQ
    assert streamer.symbols == ["QQQ", "SPX", "XSP"]

    subs = streamer._extra_subscriptions(streamer.symbols)
    assert ".SPX250620C6900" in subs["Quote"] and ".XSP250620C690" in subs["Greeks"]
    assert subs["Trade"] == ["QQQ", "SPX", "XSP"]
    assert ".SPX250620P6800" in streamer._protected_symbols()


def test_build_streamer_no_legs_matches_engine_default(home):
    _registry.write_request("flies", ["SPX"])
    streamer = _daemon.build_streamer({})
    subs = streamer._extra_subscriptions(["SPX"])
    assert subs == {"Trade": ["SPX"], "Quote": [], "Greeks": [], "Summary": ["SPX"]}
    assert streamer._protected_symbols() == set()


def test_write_request_schema_is_flat_json(home):
    path = _registry.write_request("gex", ["SPX"])
    assert not path.with_name(path.name + ".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "symbols": ["SPX"], "legs": [], "leg_sources": [],
    }
