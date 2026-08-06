import json

from cherrypick.scout.services import watchlist as _watchlist


def test_clean_symbols_dedupes_uppercases_and_drops_junk():
    assert _watchlist.clean_symbols([" aapl ", "MSFT", "", None, "aapl", 42]) == ["AAPL", "MSFT"]


def test_load_on_missing_file_is_empty(tmp_path):
    assert _watchlist.load(tmp_path / "watchlist.json") == []


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "watchlist.json"
    _watchlist.save(path, ["nvda", "amd"])
    assert _watchlist.load(path) == ["AMD", "NVDA"]


def test_save_write_is_atomic_tmp_then_replace(tmp_path):
    path = tmp_path / "watchlist.json"
    _watchlist.save(path, ["aapl"])
    assert not path.with_name(path.name + ".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"symbols": ["AAPL"]}


def test_add_merges_with_existing(tmp_path):
    path = tmp_path / "watchlist.json"
    _watchlist.save(path, ["aapl"])
    result = _watchlist.add(path, ["msft"])
    assert result == ["AAPL", "MSFT"]


def test_remove_drops_only_named_symbols(tmp_path):
    path = tmp_path / "watchlist.json"
    _watchlist.save(path, ["aapl", "msft", "nvda"])
    result = _watchlist.remove(path, ["msft"])
    assert result == ["AAPL", "NVDA"]


def test_a_bad_stream_request_write_does_not_break_save(tmp_path, monkeypatch):
    """A failed streamrequests write is best-effort -- it must never break a watchlist edit."""
    import cherrypick.core.streamrequests as _streamrequests

    def _boom_write(*a, **kw):
        raise RuntimeError("no state dir")

    monkeypatch.setattr(_streamrequests, "write_request", _boom_write)
    path = tmp_path / "watchlist.json"
    result = _watchlist.save(path, ["aapl"])
    assert result == ["AAPL"]
    assert _watchlist.load(path) == ["AAPL"]
