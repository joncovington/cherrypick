"""The technicals and fetch-script writers that keep a compact one-line JSON form.

Each used to carry its own write-then-rename. They now go through `core.jsonio`, and their bytes on
disk must not move: no indent, `,` and `:` with no spaces, no trailing newline, and a value that is
not JSON raising rather than being written as its `str()`. A reader of these files (the console,
the next fetch) parses them either way, but a changed byte shows as a changed file in every diff
and backup, so the form is pinned here.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cherrypick.technicals import chart, watchlist

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"

DOC = {"b": [1, 2.5, None, True], "a": {"nested": "é ü", "empty": {}, "list": []}, "z": "x"}
COMPACT = json.dumps(DOC, separators=(",", ":")).encode("utf-8")


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_compact(path: Path) -> bytes:
    """The file's bytes, after checking they are the compact form of their own content and that no
    tmp file is left beside it."""
    raw = path.read_bytes()
    assert raw == json.dumps(json.loads(raw), separators=(",", ":")).encode("utf-8")
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []
    return raw


def test_a_chart_is_written_compact_and_a_non_json_value_is_refused(tmp_path):
    path = tmp_path / "charts" / "SPY.json"
    chart._write(path, DOC)
    assert path.read_bytes() == COMPACT
    with pytest.raises(TypeError):
        chart._write(tmp_path / "charts" / "bad.json", {"x": object()})
    assert not (tmp_path / "charts" / "bad.json").exists()


def test_the_watchlist_is_written_compact(tmp_path):
    watchlist.write(tmp_path / "wl", [{"symbol": "SPY", "score": 1.5}], "2026-11-20", "2026-11-19")
    raw = _is_compact(tmp_path / "wl" / watchlist.FILE)
    assert json.loads(raw)["rows"] == [{"symbol": "SPY", "score": 1.5}]


def test_the_iv_rank_files_are_written_compact():
    fir = _script("fetch_iv_rank")
    fir.save({"days": {"2026-11-20": {"SPY": 0.5}}})
    from cherrypick.technicals import paths

    assert _is_compact(paths.tastytrade_iv_rank()) == b'{"days":{"2026-11-20":{"SPY":0.5}}}'
    fir.save_expiry_ivs("2026-11-20", {"SPY": {"iv": 0.2}})
    fir.save_expiry_ivs("2026-11-20", {"QQQ": {"iv": 0.3}})  # a rerun merges names into the file
    raw = _is_compact(paths.expiry_iv_dir() / "2026-11-20.json")
    assert set(json.loads(raw)["names"]) == {"SPY", "QQQ"}


def test_the_earnings_times_file_is_written_compact(monkeypatch):
    fet = _script("fetch_earnings_times")
    monkeypatch.setattr(fet.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fet, "fetch_day", lambda _d: {})
    monkeypatch.setattr(fet, "parse", lambda _body: {"AAA": "bmo"})
    assert fet.main(["--days", "1"]) == 0
    from cherrypick.technicals import paths

    files = list(paths.earnings_times_dir().glob("*.json"))
    assert len(files) == 1
    assert json.loads(_is_compact(files[0]))["dates"]
