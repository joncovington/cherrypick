"""The subscription registry: per-module request files, the union the streamer reads (symbols + legs,
where legs may be pulled from a module's own trades DB via a leg_sources query), and how the daemon wires
that union into the engine. No broker required."""

import json
import sqlite3
from pathlib import Path

import pytest

from cherrypick.streamer import daemon as _daemon
from cherrypick.streamer import registry as _registry


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


def test_union_window_hints_takes_the_max_per_symbol(home):
    _registry.write_request("flies", ["XSP"], window_hints={"XSP": 40})
    _registry.write_request("gex", ["XSP", "QQQ"], window_hints={"XSP": 90, "QQQ": 30})
    assert _registry.union_window_hints() == {"XSP": (90, 90), "QQQ": (30, 30)}


def test_union_window_hints_one_modules_silence_never_narrows_another(home):
    _registry.write_request("flies", ["XSP"], window_hints={"XSP": 90})
    _registry.write_request("gex", ["XSP"])  # no hint at all for XSP
    assert _registry.union_window_hints() == {"XSP": (90, 90)}


def test_union_window_hints_empty_when_none_set(home):
    _registry.write_request("flies", ["XSP"])
    assert _registry.union_window_hints() == {}


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


_IC_QUERY = (
    "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol FROM ic_trades "
    "WHERE status IN ('pending','open','partial','partial_entry')"
)


def test_leg_sources_pulls_open_legs_from_db(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX", "XSP"], leg_sources=[{"db": str(db), "query": _IC_QUERY}])
    legs = _registry.union_legs()
    # Every non-null cell of the open/pending rows; the 'closed' row is filtered out by the query.
    assert legs == sorted(
        [
            ".SPX250620P6800",
            ".SPX250620C6900",
            ".SPX250620P6790",
            ".SPX250620C6910",
            ".XSP250620P680",
            ".XSP250620C690",
        ]
    )


def test_leg_sources_union_with_explicit_legs(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request(
        "meic", ["SPX"], legs=[".SPX250620C9999"], leg_sources=[{"db": str(db), "query": _IC_QUERY}]
    )
    legs = _registry.union_legs()
    assert ".SPX250620C9999" in legs and ".SPX250620C6900" in legs


def test_leg_sources_missing_db_is_not_fatal(home, tmp_path):
    _registry.write_request(
        "meic", ["SPX"], leg_sources=[{"db": str(tmp_path / "nope.db"), "query": _IC_QUERY}]
    )
    assert _registry.union_legs() == []  # missing DB contributes nothing, no crash


def test_leg_sources_rejects_non_select(home, tmp_path):
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request("meic", ["SPX"], leg_sources=[{"db": str(db), "query": "DELETE FROM ic_trades"}])
    assert _registry.union_legs() == []  # non-SELECT ignored (belt-and-suspenders; DB is opened ?mode=ro)


def test_leg_sources_opens_readonly(home, tmp_path):
    """The declared DB is opened read-only — a would-be write in the query can never mutate it."""
    db = tmp_path / "meic_trades.db"
    _make_ic_trades_db(db)
    _registry.write_request(
        "meic", ["SPX"], leg_sources=[{"db": str(db), "query": "SELECT put_symbol FROM ic_trades"}]
    )
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
    _registry.write_request("meic", ["SPX", "XSP"], leg_sources=[{"db": str(db), "query": _IC_QUERY}])
    _registry.write_request("flies", ["SPX"])
    streamer = _daemon.build_streamer({"symbols": ["QQQ"]})  # config seed adds QQQ
    assert streamer.symbols == ["QQQ", "SPX", "XSP"]

    subs = streamer._extra_subscriptions(streamer.symbols)
    assert ".SPX250620C6900" in subs["Quote"] and ".XSP250620C690" in subs["Greeks"]
    assert subs["Trade"] == ["QQQ", "SPX", "XSP"]
    assert ".SPX250620P6800" in streamer._protected_symbols()


def test_build_streamer_trade_subscribes_cash_legs_but_not_option_legs(home):
    """Overview's breadth legs (VIX/VIX3M/VVIX are INDICES: Trade events only, never quotes) must
    ride the Trade subscription — Quote-only left them with no price at all, and overview reads
    every breadth spot from stream_trades. Option legs stay off Trade deliberately: sparse prints
    would read as perpetually stale to the engine's stale-trade self-heal."""
    _registry.write_request("overview", ["SPX"], legs=["VIX", "GLD", ".SPXW260821C7700"])
    streamer = _daemon.build_streamer({})
    subs = streamer._extra_subscriptions(streamer.symbols)
    assert "VIX" in subs["Trade"] and "GLD" in subs["Trade"]
    assert ".SPXW260821C7700" not in subs["Trade"]
    assert ".SPXW260821C7700" in subs["Quote"]  # options keep their marks
    # An index has no order book, so its Quote subscription could never deliver an event; an ETF
    # leg has a real one and modules price off it. Nothing cash-settled has greeks at all.
    assert "VIX" not in subs["Quote"]
    assert "GLD" in subs["Quote"]
    assert subs["Greeks"] == [".SPXW260821C7700"]


def test_build_streamer_no_legs_matches_engine_default(home):
    _registry.write_request("flies", ["SPX"])
    streamer = _daemon.build_streamer({})
    subs = streamer._extra_subscriptions(["SPX"])
    assert subs == {"Trade": ["SPX"], "Quote": [], "Greeks": [], "Summary": ["SPX"]}
    assert streamer._protected_symbols() == set()


def test_build_streamer_window_strike_count_for_widens_on_a_hint(home):
    _registry.write_request("flies", ["XSP"], window_hints={"XSP": 90})
    streamer = _daemon.build_streamer({"symbols": ["XSP"], "streamer": {"window_strike_count": 60}})
    assert streamer.window_strike_count == 60
    # (below, above): a hint wins over the configured default, and the default is the floor.
    assert streamer._window_strike_count_for("XSP") == (90, 90)
    assert streamer._window_strike_count_for("QQQ") == (60, 60)  # no hint -> the default


def test_build_streamer_serves_a_directional_hint_without_widening_the_other_side(home):
    """pmcc's deep-ITM long sits below spot; the strikes an equal distance above it are the largest
    block of subscriptions in the suite that no module can read (2026-08-24)."""
    _registry.write_request("pmcc", ["TQQQ"], window_hints={"TQQQ": {"down": 163, "up": 5}})
    streamer = _daemon.build_streamer({"symbols": ["TQQQ"], "streamer": {"window_strike_count": 30}})
    # The shallow side falls back to the configured default rather than to the hint's own 5: a
    # directional hint asks for MORE on one side, it never narrows the base on the other.
    assert streamer._window_strike_count_for("TQQQ") == (163, 30)


def test_write_request_schema_is_flat_json(home):
    path = _registry.write_request("gex", ["SPX"])
    assert not path.with_name(path.name + ".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "symbols": ["SPX"],
        "legs": [],
        "leg_sources": [],
        "window_hints": {},
        "expirations": {},
        "history_days": {},
    }


def test_write_request_carries_history_days_and_union_takes_max(home):
    path = _registry.write_request("pmcc", ["TNA"], history_days={"tna": 42})
    assert json.loads(path.read_text(encoding="utf-8"))["history_days"] == {"TNA": 42}
    _registry.write_request("other", ["TNA"], history_days={"TNA": 60})
    assert _registry.union_history_days() == {"TNA": 60}


def test_build_streamer_history_days_for_reads_the_union(home):
    _registry.write_request("pmcc", ["TNA"], history_days={"TNA": 42})
    streamer = _daemon.build_streamer({"symbols": ["TNA"]})
    assert streamer._history_days_for("TNA") == 42
    assert streamer._history_days_for("QQQ") == 0  # no request -> no backfill


def test_write_request_carries_expirations(home):
    path = _registry.write_request("calendars", ["SPX"], expirations={"spx": ["2099-01-15"]})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["expirations"] == {"SPX": ["2099-01-15"]}


def test_union_expirations_reads_across_module_files(home):
    _registry.write_request("calendars", ["SPX"], expirations={"SPX": ["2099-01-15", "2099-01-18"]})
    _registry.write_request("other", ["SPX"], expirations={"SPX": ["2099-01-18", "2099-01-22"]})
    assert _registry.union_expirations() == {"SPX": ["2099-01-15", "2099-01-18", "2099-01-22"]}


def test_build_streamer_summary_subscribes_cash_legs_too(home):
    """`day_close` arrives ONLY on Summary, and it is the input to every percentile and seasonal
    reading in the suite.

    Same bug as the Trade case above, one event type later and eleven days quieter. Summary was
    underlyings-only, so the entire vol complex and the commodity proxies -- all declared as legs --
    stopped accumulating daily closes on 2026-08-14 and nothing noticed, because SPY kept its own
    only by virtue of another module declaring it an underlying.

    Option legs stay off Summary as they stay off Trade: a per-contract daily bar is not a series
    anything reads, and it would be thousands of subscriptions for nothing.
    """
    _registry.write_request("gex", ["SPX"], legs=["VIX", "VIX3M", "GLD", ".SPXW260821C7700"])
    streamer = _daemon.build_streamer({})

    subs = streamer._extra_subscriptions(streamer.symbols)

    for sym in ("VIX", "VIX3M", "GLD"):
        assert sym in subs["Summary"], f"{sym} needs Summary or it accumulates no daily closes"
    assert "SPX" in subs["Summary"], "underlyings keep theirs"
    assert ".SPXW260821C7700" not in subs["Summary"], "a per-contract daily bar is nobody's series"


def test_history_backfill_covers_every_symbol_we_maintain_summary_for(home):
    """`history_days` declared for a LEG was silently ignored: the backfill iterated the underlyings
    alone, so the 270 days the whole vol complex asked for delivered not one row.

    The deficit scan is keyed off the Summary subscription because those are exactly the symbols
    whose rows stay current -- backfilling anything else writes history that is stale from day one.
    """
    _registry.write_request(
        "gex", ["SPX"], legs=["VIX", "VVIX"], history_days={"VIX": 270, "VVIX": 270, "SPX": 30}
    )
    streamer = _daemon.build_streamer({})

    maintained = set(streamer._extra_subscriptions(streamer.symbols)["Summary"])

    assert {"VIX", "VVIX"} <= maintained, "declared history for a leg the backfill would never visit"
    assert streamer._history_days_for("VVIX") == 270
