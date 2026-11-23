"""The overview's text writers: the rendered morning page and the market-files fetcher's `_write`.

Both used to write-then-rename by hand and now go through `core.jsonio.write_text_atomic`. The text
lands in UTF-8 text mode, so a newline is the platform's line ending, exactly as the old
`Path.write_text` wrote it; nothing is left beside the file, and a rewrite replaces the old page.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from cherrypick.overview import paths, render

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_market_files.py"
TEXT = "# Morning\n\nline two é ü\n\nlast"


def _expected(text: str) -> bytes:
    return text.encode("utf-8").replace(b"\n", os.linesep.encode())


def _no_tmp(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_the_morning_page_is_written_as_text_and_replaced_on_a_rewrite(monkeypatch):
    monkeypatch.setattr(render, "render", lambda _session: TEXT)
    path = render.write("2026-11-20")
    assert path == str(paths.render_path("2026-11-20"))
    assert Path(path).read_bytes() == _expected(TEXT)
    monkeypatch.setattr(render, "render", lambda _session: "second")
    render.write("2026-11-20")
    assert Path(path).read_bytes() == b"second"
    assert _no_tmp(Path(path).parent) == []


def test_no_page_is_written_when_there_is_nothing_to_render(monkeypatch):
    monkeypatch.setattr(render, "render", lambda _session: None)
    assert render.write("2026-11-20") is None
    assert not paths.render_path("2026-11-20").exists()


def test_the_market_files_fetcher_writes_text_through_the_shared_writer(tmp_path):
    spec = importlib.util.spec_from_file_location("fetch_market_files", SCRIPT)
    fmf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fmf)
    target = tmp_path / "files" / "calendar.csv"
    fmf._write(target, TEXT)
    assert target.read_bytes() == _expected(TEXT)
    assert _no_tmp(target.parent) == []
