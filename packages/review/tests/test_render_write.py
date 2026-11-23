"""The review's rendered page is written through `core.jsonio.write_text_atomic`.

UTF-8 text mode, so a newline is the platform's line ending as it was under `Path.write_text`; the
directory is created, a rewrite replaces the page, and nothing is left beside it.
"""

from __future__ import annotations

import os

from cherrypick.review import paths, render


def test_the_review_page_is_written_as_text_and_replaced_on_a_rewrite(tmp_path, monkeypatch):
    target = tmp_path / "out" / "eod-2026-11-20.md"
    monkeypatch.setattr(paths, "render_path", lambda _session: target)
    monkeypatch.setattr(render, "render", lambda _session, _window: "# Review\n\nline é\n\nlast")
    assert render.write("2026-11-20") == target
    assert target.read_bytes() == "# Review\n\nline é\n\nlast".encode().replace(b"\n", os.linesep.encode())
    monkeypatch.setattr(render, "render", lambda _session, _window: "second")
    render.write("2026-11-20")
    assert target.read_bytes() == b"second"
    assert [p.name for p in target.parent.iterdir()] == ["eod-2026-11-20.md"]
