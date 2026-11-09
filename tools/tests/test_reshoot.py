"""Tests for scripts/reshoot.py -- the release-time retake of the console screenshots.

The pieces that decide what is photographed and what is overwritten are pure and tested here: which
ui-check arguments an entry makes, which folders may be emptied, which ports may be served, how two images
are told apart, and that a shot taken from the wrong console is refused. The server, the browser and the
home build are not run; they are what the release step exercises.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import reshoot as rs  # noqa: E402


def shot(**kw) -> dict:
    base = {
        "path": "docs/images/console/x.png",
        "route": "live/wheel",
        "card": None,
        "scale": 1,
        "full_page": False,
    }
    return {**base, **kw, "seed": kw.get("seed", 7), "day": kw.get("day", "2026-11-06")}


def test_a_card_entry_crops_to_the_card():
    args = rs.ui_check_args(shot(card="payoff at expiry"), Path("o.png"))
    assert args[:4] == ["--route", "live/wheel", "--shot", "o.png"]
    assert args[4:] == ["--card", "payoff at expiry"]


def test_a_full_page_entry_takes_the_tab_in_a_tall_viewport():
    args = rs.ui_check_args(shot(full_page=True), Path("o.png"))
    assert args[args.index("--selector") + 1] == ".mf-slide"
    assert args[args.index("--viewport") + 1] == rs.TALL_VIEWPORT
    assert "--scale" not in args


def test_an_entry_can_name_its_own_selector_viewport_and_scale():
    args = rs.ui_check_args(
        shot(full_page=True, selector="main", viewport="1200x3000", scale=2), Path("o.png")
    )
    assert args[args.index("--selector") + 1] == "main"
    assert args[args.index("--viewport") + 1] == "1200x3000"
    assert args[args.index("--scale") + 1] == "2"


def test_a_plain_entry_is_the_viewport_as_it_is():
    args = rs.ui_check_args(shot(), Path("o.png"))
    assert "--selector" not in args and args[args.index("--viewport") + 1] == rs.DEFAULT_VIEWPORT


def test_shots_are_grouped_by_the_demo_they_come_from():
    got = rs.groups([shot(), shot(), shot(seed=8), shot(day="2026-11-05")])
    assert sorted(got) == [(7, "2026-11-05"), (7, "2026-11-06"), (8, "2026-11-06")]
    assert len(got[(7, "2026-11-06")]) == 2


# ---------------------------------------------------------------------------- what may be emptied and served
def test_a_folder_that_is_not_a_demo_home_is_never_emptied(tmp_path):
    real = tmp_path / "real"
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "notes.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit, match="would be emptied"):
        rs.check_home(tmp_path / "mine", real=real)


def test_an_empty_folder_a_missing_one_and_a_demo_home_are_allowed(tmp_path):
    real = tmp_path / "real"
    (tmp_path / "empty").mkdir()
    rs.check_home(tmp_path / "empty", real=real)
    rs.check_home(tmp_path / "new", real=real)
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / rs.MARKER).write_text("demo\n", encoding="utf-8")
    (tmp_path / "demo" / "config.json").write_text("{}", encoding="utf-8")
    rs.check_home(tmp_path / "demo", real=real)


def test_the_real_home_and_its_parent_are_refused_even_with_a_marker(tmp_path):
    real = tmp_path / "home" / ".cherrypick"
    real.mkdir(parents=True)
    (real / rs.MARKER).write_text("demo\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="real home"):
        rs.check_home(real, real=real)
    with pytest.raises(SystemExit, match="real home"):
        rs.check_home(tmp_path / "home", real=real)


def test_the_port_is_never_5070_or_the_real_consoles(monkeypatch):
    offered = iter([5070, 5123, 6001])

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            self.port = next(offered)

        def getsockname(self):
            return ("127.0.0.1", self.port)

    monkeypatch.setattr(rs.socket, "socket", lambda *a, **k: FakeSocket())
    assert rs.free_port({5070, 5123}) == 6001


# ---------------------------------------------------------------------------- the shot is of the demo
def test_a_shot_taken_from_another_console_is_refused(tmp_path, monkeypatch):
    """ui-check falls back to the real console when CHERRYPICK_HOME is wrong; that must stop the run."""

    def wrong_console(*a, **k):
        return subprocess.CompletedProcess(
            a, 0, stdout="ui-check: http://127.0.0.1:5070/live/wheel OK\n", stderr=""
        )

    monkeypatch.setattr(rs.subprocess, "run", wrong_console)
    with pytest.raises(SystemExit, match="did not use the demo port 51000"):
        rs.take(shot(), tmp_path / "home", 51000, tmp_path / "out" / "x.png")


def test_a_shot_that_wrote_no_file_is_a_failure(tmp_path, monkeypatch):
    def skipped(*a, **k):
        return subprocess.CompletedProcess(
            a, 0, stdout="ui-check: http://127.0.0.1:51000/live/wheel OK\n", stderr=""
        )

    monkeypatch.setattr(rs.subprocess, "run", skipped)  # a skipped ui-check exits 0 and writes nothing
    with pytest.raises(SystemExit, match="ui-check failed"):
        rs.take(shot(), tmp_path / "home", 51000, tmp_path / "out" / "x.png")


# ---------------------------------------------------------------------------- telling two images apart
@pytest.fixture
def image_pair(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    old, new = tmp_path / "old.png", tmp_path / "new.png"
    pil.new("RGB", (100, 100), (20, 22, 30)).save(old)
    return pil, old, new


def test_an_identical_image_is_unchanged(image_pair):
    pil, old, new = image_pair
    pil.new("RGB", (100, 100), (20, 22, 30)).save(new)
    assert rs.verdict(old, new, rs.DEFAULT_THRESHOLD) == ("unchanged", 0.0)


def test_palette_noise_below_the_tolerance_is_not_a_change(image_pair):
    pil, old, new = image_pair
    pil.new("RGB", (100, 100), (30, 22, 30)).save(new)  # ten off in one channel
    assert rs.verdict(old, new, rs.DEFAULT_THRESHOLD)[0] == "unchanged"


def test_a_visible_difference_over_the_threshold_is_changed_and_the_threshold_is_configurable(image_pair):
    pil, old, new = image_pair
    im = pil.new("RGB", (100, 100), (20, 22, 30))
    im.paste((250, 250, 250), (0, 0, 10, 100))  # a tenth of the pixels
    im.save(new)
    state, frac = rs.verdict(old, new, 0.5)
    assert state == "changed" and frac == pytest.approx(0.10)
    assert rs.verdict(old, new, 15.0)[0] == "unchanged"  # the same difference under a looser threshold


def test_another_size_is_always_changed_and_a_missing_image_is_new(image_pair):
    pil, old, new = image_pair
    pil.new("RGB", (100, 120), (20, 22, 30)).save(new)
    assert rs.verdict(old, new, 99.0) == ("changed", 1.0)
    assert rs.verdict(old.with_name("none.png"), new, 0.5) == ("new", 1.0)


def test_crop_and_compress_make_the_entry_image(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    path = tmp_path / "x.png"
    pil.new("RGB", (400, 300), (20, 22, 30)).save(path)
    rs.finish(path, shot(crop=[0, 10, 200, 110]))
    assert pil.open(path).size == (200, 100)
