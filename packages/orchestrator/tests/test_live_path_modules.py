"""`config.LIVE_PATH_MODULES` names the modules with a live loop, and must follow the code.

The set lets a surface say "no live path" for a module that is paper by construction. If it
fell behind (a package gains a `live_loop.py` and nobody adds it), a module that can trade live
would be shown as paper only, so the set is checked against the packages on disk rather than kept
by hand alone.
"""

from pathlib import Path

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import liveops

pytestmark = pytest.mark.unit

PACKAGES = Path(__file__).resolve().parents[2]


def _packages_with_a_live_loop() -> set[str]:
    return {p.parent.name for p in PACKAGES.glob("*/src/cherrypick/*/live_loop.py")}


def test_the_set_is_every_package_with_a_live_loop():
    found = _packages_with_a_live_loop()
    assert found, f"no live_loop.py found under {PACKAGES}; the discovery itself is broken"
    assert set(cfgmod.LIVE_PATH_MODULES) == found


def test_earnings_is_not_a_live_path():
    assert "earnings" in cfgmod.PAPER_ONLY_MODULES
    assert not cfgmod.has_live_path("earnings")


def test_the_live_ops_views_carry_it(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    for name in ("meic", "contango"):
        (tmp_path / name).mkdir()
    cfg = {"modules": {n: {"enabled": True, "path": str(tmp_path / n)} for n in ("meic", "contango")}}
    rows = {m["module"]: m["live_path"] for m in liveops.halt_status(cfg)["modules"]}
    assert rows == {"meic": True, "contango": False}
