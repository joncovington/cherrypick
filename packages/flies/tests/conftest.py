"""Session-wide test setup: the core submodule on sys.path, and a managed home that is never real.

The module's own files bootstrap `src/_core` when imported (the suite-wide pattern — see MEIC's
paper.py), but that makes a test's `cherrypick.core.*` import depend on which of our modules happened
to be imported first. Import sorters reorder those freely, so doing it here once removes an ordering
trap that would otherwise surface as a collection error after an unrelated lint fix.
"""

import os
import sys

import pytest

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Point `CHERRYPICK_HOME` at a temporary directory for every test in the suite.

    Autouse, and deliberately not something a test opts into. This was a per-test `home` fixture, and
    on 2026-07-20 three settlement tests that did not request it ran while the live loop was in
    session: they wrote a real `paper-eod-2026-07-20.md` into `~/.cherrypick/logs/flies`, the loop
    read the day as already finished, and the session never settled. Eleven open positions sat under
    a report describing a fixture at spot 5998.

    The lesson is not "remember the fixture" — it is that opting in to isolation puts the real
    trading home one forgotten argument away. `FLIES_DB_PATH` and `FLIES_CONFIG` are cleared for the
    same reason: an operator's shell may carry them, and they resolve to real files.
    """
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("FLIES_DB_PATH", raising=False)
    monkeypatch.delenv("FLIES_CONFIG", raising=False)
    return home
