"""The Config page cannot flip MEIC's `live.dry_run`: it decides whether entries are sent."""

from __future__ import annotations

from cherrypick.orchestrator import configedit


def test_meic_live_dry_run_is_guarded():
    assert "/live/dry_run" in configedit.GUARDED["meic"]
