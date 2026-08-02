"""MEIC declares its symbols AND its open paper legs to the streamer via a stream-request file.

Regression for the 2026-07-29 defect: the request file was hand-written at the 2026-07-21 cutover
(seven retired symbols, leg query against the LIVE ledger — whose open-trades query returns nothing),
so open paper positions' legs were never explicitly subscribed. The writer regenerates it every tick.
"""

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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
