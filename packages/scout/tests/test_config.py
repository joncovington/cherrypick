import json

from cherrypick.scout import config as _config


def test_defaults_when_no_config_present():
    cfg = _config.load()
    assert cfg["screener"]["short_delta"] == 0.30
    # No `serve` block: this package stopped serving when the console became the one read surface.
    assert "serve" not in cfg


def test_home_config_overrides_defaults_and_merges_nested(monkeypatch, tmp_path):
    home_cfg_dir = tmp_path / "cherrypick-home" / "config"
    home_cfg_dir.mkdir(parents=True)
    (home_cfg_dir / "scout.json").write_text(
        json.dumps({"refresh": {"quotes_seconds": 9}, "screener": {"short_delta": 0.25}}),
        encoding="utf-8",
    )
    cfg = _config.load()
    assert cfg["refresh"]["quotes_seconds"] == 9
    assert cfg["refresh"]["metrics_ttl_seconds"] == 900  # untouched sibling key survives the merge
    assert cfg["screener"]["short_delta"] == 0.25
    assert cfg["screener"]["min_iv_rank"] == 25  # untouched sibling key survives the merge


def test_data_and_logs_dirs_follow_the_shared_home(monkeypatch, tmp_path):
    assert _config.data_dir() == tmp_path / "cherrypick-home" / "data" / "scout"
    assert _config.logs_dir() == tmp_path / "cherrypick-home" / "logs" / "scout"
    assert _config.cache_db_path() == _config.data_dir() / "cache.db"
    assert _config.watchlist_path() == _config.data_dir() / "watchlist.json"
