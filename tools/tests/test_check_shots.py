"""Tests for tools/check_shots.py -- the check that keeps the console screenshots and their manifest honest.

Each case builds a small repository (nav files, a manifest, images, source files) in a temp directory, so
a rule is shown firing on exactly the fault it names and staying quiet on a clean tree. The shipped
manifest is checked too, so a screenshot added without an entry fails here before it fails in CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_shots as cs  # noqa: E402

NAV_GROUPS = """
export const LIVE_SLIDES = [
  { id: "today", label: "today" },
  { id: "wheel", label: "wheel" },
] as const satisfies readonly NavSlide[];
export const REPORTS_SLIDES = [
  { id: "morning", label: "morning" },
  { id: "external", label: "external" },
] as const satisfies readonly NavSlide[];
export const NAV_DECL: Record<ModuleId, ModuleNavDecl> = {
  live: { slides: LIVE_SLIDES, legacy: {} },
  reports: { slides: REPORTS_SLIDES },
  config: { slides: CONFIG_SLIDES, legacy: {} },
  ghost: { slides: GHOST_SLIDES },
};
"""
MODULE_ORDER = 'export const SUITE_ORDER = ["live", "reports", "config"] as const;\n'
CONFIG_META = 'export const SECTIONS: SectionMeta[] = [\n  {\n    id: "arms",\n    title: "Arms",\n  },\n  { id: "timing", title: "t" },\n];\n'  # noqa: E501


def entry(path: str = "docs/images/console/live-wheel.png", route: str = "live/wheel", **kw) -> dict:
    base = {
        "path": path,
        "route": route,
        "card": None,
        "scale": 1,
        "full_page": True,
        "seed": 7,
        "day": "2026-11-06",
        "sources": ["packages/console/web/src/pages/Live/ManagePage.tsx"],
    }
    return {**base, **kw}


def build(root: Path, shots: list[dict], images: list[str] | None = None) -> None:
    for rel, text in (
        (cs.NAV_GROUPS, NAV_GROUPS),
        (cs.MODULE_ORDER, MODULE_ORDER),
        (cs.CONFIG_META, CONFIG_META),
        ("packages/console/web/src/pages/Live/ManagePage.tsx", "export {};\n"),
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / cs.MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (root / cs.MANIFEST).write_text(json.dumps({"version": 1, "shots": shots}), encoding="utf-8")
    for rel in images if images is not None else [s["path"] for s in shots]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG")


def test_a_clean_tree_has_no_findings(tmp_path):
    build(tmp_path, [entry(), entry("docs/images/console/live-today.png", "live/today")])
    assert cs.failures(tmp_path) == []


def test_an_image_with_no_manifest_entry_fails(tmp_path):
    build(tmp_path, [entry()], images=["docs/images/console/live-wheel.png", "docs/images/console/stray.png"])
    found = cs.failures(tmp_path)
    assert len(found) == 1 and "stray.png" in found[0] and "no manifest entry" in found[0]


def test_a_manifest_entry_whose_image_is_missing_fails(tmp_path):
    build(tmp_path, [entry()], images=[])
    found = cs.failures(tmp_path)
    assert len(found) == 1 and "the image is missing" in found[0]


@pytest.mark.parametrize(
    "route,why",
    [
        ("nowhere/today", "unknown page"),
        ("live/renamed-tab", "no tab"),
        ("live/today/extra", "no tab"),
        ("ghost/x", "unknown page"),  # in navGroups, but the shell does not list the module
    ],
)
def test_an_unknown_route_fails(tmp_path, route, why):
    build(tmp_path, [entry(route=route)])
    found = cs.failures(tmp_path)
    assert len(found) == 1 and why in found[0]


@pytest.mark.parametrize(
    "route",
    [
        "live",
        "live/today",
        "live/wheel?symbol=x",
        "reports/external/some-report",
        "config/timing",
        "config/prefs",
    ],
)
def test_known_routes_pass(tmp_path, route):
    build(tmp_path, [entry(route=route)])
    assert cs.failures(tmp_path) == []


def test_a_missing_field_or_source_fails(tmp_path):
    no_sources = {k: v for k, v in entry().items() if k != "sources"}
    build(tmp_path, [no_sources])
    assert "missing field(s) sources" in cs.failures(tmp_path)[0]
    build(tmp_path, [entry(sources=["packages/console/web/src/gone.tsx"])])
    assert "does not exist" in cs.failures(tmp_path)[0]


def test_a_changed_source_without_its_image_is_stale():
    shots = [entry()]
    src = shots[0]["sources"][0]
    assert cs.stale(shots, {src}) == [("docs/images/console/live-wheel.png", [src])]


def test_a_changed_source_with_its_image_is_not_stale():
    shots = [entry()]
    assert cs.stale(shots, {shots[0]["sources"][0], shots[0]["path"]}) == []


def test_an_unrelated_change_is_not_stale():
    assert cs.stale([entry()], {"README.md"}) == []


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A repository whose `base` branch holds a clean tree and whose HEAD changes one source file."""
    build(tmp_path, [entry()])
    git(tmp_path, "init", "-q", "-b", "base")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "work")
    (tmp_path / "packages/console/web/src/pages/Live/ManagePage.tsx").write_text(
        "export const x = 1;\n", encoding="utf-8"
    )
    git(tmp_path, "commit", "-qam", "change the page")
    return tmp_path


def test_a_changed_source_warns_and_exits_zero(repo, capsys, monkeypatch):
    monkeypatch.delenv("PR_LABELS", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cs.main(["base"], root=repo) == 0
    out = capsys.readouterr().out
    assert "warning: docs/images/console/live-wheel.png" in out
    assert "::warning file=docs/images/console/live-wheel.png::" in out  # the GitHub annotation


def test_the_label_silences_the_warning(repo, capsys, monkeypatch):
    monkeypatch.setenv("PR_LABELS", "bug, screenshots-ok")
    assert cs.main(["base"], root=repo) == 0
    assert "warning: docs/images" not in capsys.readouterr().out


def test_changing_the_image_too_is_silent(repo, capsys, monkeypatch):
    monkeypatch.delenv("PR_LABELS", raising=False)
    (repo / "docs/images/console/live-wheel.png").write_bytes(b"\x89PNG-new")
    git(repo, "commit", "-qam", "reshoot")
    assert cs.main(["base"], root=repo) == 0
    assert "warning: docs/images" not in capsys.readouterr().out


def test_no_base_to_diff_against_skips_the_warning_but_not_the_failures(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PR_LABELS", raising=False)
    build(tmp_path, [entry()])
    assert cs.main(["no-such-ref"], root=tmp_path) == 0
    assert "skipped" in capsys.readouterr().out
    build(tmp_path, [entry(route="nowhere")])
    assert cs.main(["no-such-ref"], root=tmp_path) == 1


def test_the_shipped_manifest_is_consistent():
    """Every shipped image has an entry, every entry its image, a known route and real sources."""
    assert cs.failures() == []
