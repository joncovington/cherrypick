"""MEIC declares its symbols AND its open paper legs to the streamer via a stream-request file.

Regression for the 2026-07-29 defect: the request file was hand-written at the 2026-07-21 cutover
(seven retired symbols, leg query against the LIVE ledger — whose open-trades query returns nothing),
so open paper positions' legs were never explicitly subscribed. The writer regenerates it every tick.

And for the 2026-09-17 gap on the other ledger: the live loop existed, held real ICs, and declared
nothing -- its legs survived only while they sat inside the ATM window. The live loop now writes its
own ``meic-live.json`` from its preamble, pointed at the LIVE ledger. The leg-query assertions run
the query through the PRODUCER's own reader (``cherrypick.streamer.registry``), so a change to what
it accepts fails here rather than in production; that package is not a dependency of this one, so
those tests skip on a standalone install.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cherrypick.meic import paths as _paths  # noqa: E402
from cherrypick.meic import stream_request  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point the whole cherrypick tree (state dir AND the meic data home) at a tmp dir."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    monkeypatch.delenv("MEIC_DATA_DIR", raising=False)
    return tmp_path


def _payload(home_dir):
    return json.loads((home_dir / "state" / "stream_requests" / "meic.json").read_text())


def test_register_writes_deduped_upper_symbols_and_paper_leg_source(home):
    stream_request.register({"symbols": ["xsp", " qqq ", "xsp"]})
    payload = _payload(home)
    assert payload["symbols"] == ["QQQ", "XSP"]
    assert payload["legs"] == []
    (source,) = payload["leg_sources"]
    # The PAPER ledger — the hand-written file pointed at the live one, whose open-trades query
    # returns nothing, which is exactly the defect this writer retires.
    assert source["db"].endswith("paper_trades.db")
    assert "meic_trades.db" not in source["db"]
    # The canonical open-trades status set over the DDL's four leg columns, verbatim.
    assert "put_symbol, call_symbol, long_put_symbol, long_call_symbol" in source["query"]
    assert "('pending','open','partial','partial_entry')" in source["query"]


def test_register_accepts_the_deprecated_single_symbol_alias(home):
    stream_request.register({"symbol": "xsp"})
    assert _payload(home)["symbols"] == ["XSP"]


def test_write_is_atomic_no_tmp_residue(home):
    stream_request.write(["XSP"])
    directory = home / "state" / "stream_requests"
    assert [p.name for p in directory.iterdir()] == ["meic.json"]


def test_register_is_best_effort_never_raises(home, monkeypatch):
    def _boom(_symbols):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["XSP"]})  # must not propagate — the loop keeps running


# --------------------------------------------------------------------------- the live ledger


def _payload_live(home_dir):
    return json.loads((home_dir / "state" / "stream_requests" / "meic-live.json").read_text())


def test_register_live_writes_a_separate_file_with_the_live_leg_source(home):
    stream_request.register({"symbols": ["XSP", "QQQ"]})
    stream_request.register_live({"live": {"symbol": " xsp "}})

    paper = _payload(home)
    live = _payload_live(home)
    # Two files, two ledgers: the paper file is untouched by the live write and vice versa.
    assert paper["symbols"] == ["QQQ", "XSP"]
    assert live["symbols"] == ["XSP"]
    (source,) = live["leg_sources"]
    assert source["db"] == str(_paths.live_db_path())
    assert source["db"].endswith("meic_trades.db")
    assert source["query"] == stream_request._LEG_QUERY
    assert paper["leg_sources"][0]["db"] == str(_paths.paper_db_path())


def test_register_live_with_no_pinned_symbol_still_declares_the_ledger(home):
    """An unset live.symbol is a readiness failure the loop reports on its own; the request file
    must still carry the leg source, because an IC opened yesterday is still open today."""
    stream_request.register_live({"live": {}})
    live = _payload_live(home)
    assert live["symbols"] == []
    assert live["leg_sources"][0]["db"].endswith("meic_trades.db")


def test_register_live_is_best_effort_never_raises(home, monkeypatch):
    def _boom(_symbols, live=False):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register_live({"live": {"symbol": "XSP"}})  # must not propagate


def test_live_loop_preamble_registers_the_live_request(home, monkeypatch):
    """The live loop's own entry point is the writer -- not a test helper calling the adapter."""
    from cherrypick.meic import live_loop

    calls = []
    monkeypatch.setattr(live_loop._stream_request, "register_live", lambda cfg: calls.append(cfg))
    monkeypatch.setattr(live_loop.paper, "load_base_config", lambda: {"live": {"symbol": "XSP"}})
    monkeypatch.setattr(live_loop, "_designated_account", lambda: None)
    monkeypatch.setattr(live_loop, "_build_snapshot", lambda cfg, symbol: (None, "stubbed"))
    monkeypatch.setattr(sys, "argv", ["live_loop", "--once"])
    assert live_loop.main() == 1  # stops at the stubbed snapshot -- after registering
    assert calls == [{"live": {"symbol": "XSP"}}]


registry = pytest.importorskip(
    "cherrypick.streamer.registry",
    reason="the producer package is not installed; the leg-source contract cannot be checked against it",
)


def _init_ledger(path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.db", "--db", str(path), "init_db"],
        check=True,
        capture_output=True,
    )


def _insert_trade(path: Path, ic_order_id: str, status: str, legs: tuple[str, str, str, str]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO ic_trades (ic_order_id, trade_date, symbol, status, put_symbol, call_symbol, "
        "long_put_symbol, long_call_symbol, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ic_order_id, "2026-09-17", "XSP", status, *legs, "2026-09-17T10:00:00", "2026-09-17T10:00:00"),
    )
    con.commit()
    con.close()


def test_the_producer_reads_only_open_live_legs_from_the_written_file(home):
    """Run the written file's leg source exactly the way the producer does: read-only, one SELECT.
    Open (and half-open) ICs contribute their four legs; closed and settled ones contribute nothing,
    so a morning of stops does not pin dead subscriptions."""
    _init_ledger(_paths.live_db_path())
    _insert_trade(
        _paths.live_db_path(),
        "LIVE-1",
        "open",
        (".XSP260917P650", ".XSP260917C660", ".XSP260917P645", ".XSP260917C665"),
    )
    _insert_trade(
        _paths.live_db_path(),
        "LIVE-2",
        "partial",
        (".XSP260917P640", ".XSP260917C670", ".XSP260917P635", ".XSP260917C675"),
    )
    _insert_trade(
        _paths.live_db_path(),
        "LIVE-3",
        "closed",
        (".XSP260917P600", ".XSP260917C700", ".XSP260917P595", ".XSP260917C705"),
    )
    stream_request.register_live({"live": {"symbol": "XSP"}})

    (source,) = _payload_live(home)["leg_sources"]
    assert registry._is_single_select(source["query"])
    legs = sorted(registry._legs_from_source(source))
    assert legs == sorted(
        [
            ".XSP260917P650",
            ".XSP260917C660",
            ".XSP260917P645",
            ".XSP260917C665",
            ".XSP260917P640",
            ".XSP260917C670",
            ".XSP260917P635",
            ".XSP260917C675",
        ]
    )


def test_a_live_ledger_that_does_not_exist_yet_contributes_nothing(home):
    """The producer may read meic-live.json before the live loop has ever opened its ledger."""
    stream_request.register_live({"live": {"symbol": "XSP"}})
    (source,) = _payload_live(home)["leg_sources"]
    assert not Path(source["db"]).exists()
    assert registry._legs_from_source(source) == []
