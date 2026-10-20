"""gex's standalone streamer may not write the shared stream cache, which only packages/streamer writes.

Its config defaults `stream_cache_db` to the shared cache, which is right for READING (provider.py),
and until 2026-10-19 `run.py stream` wrote there too with no guard. The engine now refuses
(`cherrypick.core.streamer.ChainStreamer`, `producer=`); this holds gex's own path to it.
"""

from __future__ import annotations

import pytest

from cherrypick.gex import config as gex_config
from cherrypick.gex import streamer as gex_streamer


def test_the_standalone_streamer_cannot_write_the_shared_cache_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    monkeypatch.setattr(gex_streamer, "make_session_factory", lambda: lambda: None)
    cfg = gex_config.load()
    with pytest.raises(ValueError, match="shared stream cache"):
        gex_streamer.build_streamer(cfg)
    own = {**cfg, "stream_cache_db": tmp_path / "gex-own.db"}
    assert gex_streamer.build_streamer(own).db_path == tmp_path / "gex-own.db"
