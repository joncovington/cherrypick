"""Chart-cache behavior for the earnings dashboard (strategy_dashboard._cached_chart).

Each dashboard build is a cold subprocess, so charts are cached to disk keyed by their input data —
an unchanged dataset skips matplotlib rendering. These tests exercise the cache seam directly (no
matplotlib render needed).
"""
import strategy_dashboard as sd


def test_cached_chart_memoizes_by_key_data(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_CACHE_DISABLED", False)
    monkeypatch.setattr(sd._paths, "reports_dir", lambda: tmp_path)
    calls = []

    def render():
        calls.append(1)
        return "B64"

    # First call renders and writes the cache; second (same key) is a hit — render not called again.
    assert sd._cached_chart("chartX", {"a": 1}, render) == "B64"
    assert sd._cached_chart("chartX", {"a": 1}, render) == "B64"
    assert len(calls) == 1
    assert list(tmp_path.glob(".chart_cache/*.b64"))  # persisted to disk

    # Different input data -> different key -> re-render.
    sd._cached_chart("chartX", {"a": 2}, render)
    assert len(calls) == 2
    # Different chart id, same data -> also a distinct entry.
    sd._cached_chart("chartY", {"a": 1}, render)
    assert len(calls) == 3


def test_cached_chart_falls_back_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_CACHE_DISABLED", True)
    monkeypatch.setattr(sd._paths, "reports_dir", lambda: tmp_path)
    calls = []

    def render():
        calls.append(1)
        return "B64"

    sd._cached_chart("c", {"a": 1}, render)
    sd._cached_chart("c", {"a": 1}, render)
    assert len(calls) == 2  # disabled -> always renders, no cache dir touched
    assert not list(tmp_path.glob(".chart_cache/*.b64"))


def test_cached_chart_renders_on_unserializable_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "_CACHE_DISABLED", False)
    monkeypatch.setattr(sd._paths, "reports_dir", lambda: tmp_path)
    calls = []

    def render():
        calls.append(1)
        return "B64"

    # A key that json can't serialize (and default=str still stringifies, so this stays cacheable) —
    # the point is it never raises; a genuinely unserializable object just renders directly.
    assert sd._cached_chart("c", {1, 2, 3}, render) == "B64"  # set -> default=str, no crash
    assert len(calls) == 1
