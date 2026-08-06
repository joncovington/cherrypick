"""The dashboard HTML must never be observable half-written.

The orchestrator embeds this file as a `kind: static` iframe and deliberately serves the copy already
on disk *while* regenerating it in the background (`embeds.build_static`), so a reader and the writer
overlap by design every `refresh_seconds`. `Path.write_text` truncates before it writes, so a reader
could catch a 0-byte file -- which rendered as a blank white Earnings card on the suite dashboard and
appeared to "fix itself" on reload, because the reload landed after the write finished.

These tests pin the two properties that fix depends on: the destination is never truncated, and the
Windows-specific PermissionError retry actually retries instead of failing the build.
"""

from __future__ import annotations

import os

import pytest

from cherrypick.earnings.strategy_dashboard import _write_atomic

OLD = "<html>PREVIOUS COMPLETE DASHBOARD</html>"
NEW = "<html>" + ("x" * 50_000) + "</html>"


def test_writes_the_content_and_leaves_no_tmp_behind(tmp_path):
    out = tmp_path / "strategy_dashboard.html"
    _write_atomic(out, NEW)
    assert out.read_text(encoding="utf-8") == NEW
    assert list(tmp_path.glob("*.tmp")) == []


def test_destination_is_never_truncated_mid_write(tmp_path, monkeypatch):
    """The actual regression. At the instant of the swap the destination must still hold the whole
    previous document -- never a zero-length or partial one, which is what `write_text` produced."""
    out = tmp_path / "strategy_dashboard.html"
    out.write_text(OLD, encoding="utf-8")

    observed: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        # Whatever a reader would see immediately before the atomic swap.
        observed.append(os.path.exists(dst) and open(dst, encoding="utf-8").read())
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _write_atomic(out, NEW)

    assert observed == [OLD], "the old file must stay complete right up to the swap"
    assert out.read_text(encoding="utf-8") == NEW


def test_retries_a_locked_destination_rather_than_failing_the_build(tmp_path, monkeypatch):
    """On Windows os.replace raises PermissionError while another handle holds the destination open.
    A bare swap would trade a blank card for a failed build, so it retries."""
    out = tmp_path / "strategy_dashboard.html"
    out.write_text(OLD, encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    _write_atomic(out, NEW, attempts=5, delay=0)

    assert calls["n"] == 3
    assert out.read_text(encoding="utf-8") == NEW


def test_a_permanently_locked_destination_keeps_the_previous_file_whole(tmp_path, monkeypatch):
    """If the swap can never happen, a stale-but-whole dashboard beats a truncated one -- and the
    failure is raised rather than silently reported as a successful write."""
    out = tmp_path / "strategy_dashboard.html"
    out.write_text(OLD, encoding="utf-8")

    def always_locked(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", always_locked)
    with pytest.raises(OSError):
        _write_atomic(out, NEW, attempts=3, delay=0)

    assert out.read_text(encoding="utf-8") == OLD, "the previous dashboard must survive intact"
    assert list(tmp_path.glob("*.tmp")) == [], "the temp file must be cleaned up"
