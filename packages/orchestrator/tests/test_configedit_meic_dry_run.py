"""The Config page cannot flip MEIC's `live.dry_run`: it is the switch from preflighting entries to sending them."""

from __future__ import annotations

from cherrypick.orchestrator import configedit


def test_meic_live_dry_run_is_guarded():
    assert "/live/dry_run" in configedit.GUARDED["meic"]
