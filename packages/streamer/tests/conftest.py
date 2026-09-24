"""Every streamer test runs against a throwaway cherrypick home.

Autouse, and not optional. This package manages the suite's ONE market-data producer, and its
paths default into the real home: the cache, the PID file and the stop-request file all resolve
through `core.home` unless a config overrides them. On 2026-09-23 a test passed its cache path under
a key `cache_path` does not read (`streamer.cache_db`, where the real key is
`source.stream_cache_db`). The override was silently ignored, `stop_path` fell through to
`~/.cherrypick/data/marketdata/streamer.stop`, and the production streamer read the test's stop
request and shut down -- after the close, as it happened, but a run of this suite during a session
would have cut every module's quotes.

A fixture that relocates the home cannot be defeated by a mistyped key, which is the point: the
per-test config is the second line, this is the first. `CHERRYPICK_HOME` is the master override;
the narrow per-scope ones are cleared because a leaked one wins over it.
"""

from __future__ import annotations

# ---- before ANY package import: the home is a throwaway, for the whole session -------------------
# Several modules resolve paths ONCE, at import (`db_paper.DB_PATH`, `db.DB_PATH` -- the LIVE ledger --,
# the paper loop's lock and pid files). A function-scoped fixture runs too late to move those: they
# already point at the real home. Measured 2026-09-23: with a per-test fixture in place, a plain run
# still inserted 5 frozen-clock `loop_iterations` rows into the REAL earnings ledger every time. So the
# master override is set here, when pytest imports this conftest and before it imports a single
# test module -- which catches every import-time path, including ones nobody has listed.
import os as _os
import tempfile as _tempfile

import pytest

_os.environ["CHERRYPICK_HOME"] = _tempfile.mkdtemp(prefix="cherrypick-test-home-")
for _leaked in (
    "STREAMER_DATA_DIR",
    "STREAMER_LOGS_DIR",
    "STREAMER_CONFIG",
    "MARKETDATA_DATA_DIR",
    "CHERRYPICK_MODULES_HOME",
):
    _os.environ.pop(_leaked, None)


@pytest.fixture(autouse=True)
def tmp_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("cherrypick-home")
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    for leaked in (
        "MARKETDATA_DATA_DIR",
        "MARKETDATA_LOGS_DIR",
        "STREAMER_DATA_DIR",
        "STREAMER_LOGS_DIR",
        "STREAMER_CONFIG",
    ):
        monkeypatch.delenv(leaked, raising=False)
    return home
