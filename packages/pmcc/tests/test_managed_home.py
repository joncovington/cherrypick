"""The test home redirect cannot be undone from inside a test.

`monkeypatch.undo()` reverts every patch made through the test's `monkeypatch`; when the autouse
redirect shared it, one such call sent the rest of the test to the real home (2026-10-04).
"""

from cherrypick.core import home as _home

from cherrypick.pmcc import paper_loop


def test_the_redirect_survives_a_tests_own_undo(managed_home, monkeypatch):
    monkeypatch.setattr(paper_loop, "RTH_OPEN_MIN", paper_loop.RTH_OPEN_MIN)
    monkeypatch.undo()
    assert _home.home() == managed_home
    assert paper_loop.log_file().is_relative_to(managed_home)
