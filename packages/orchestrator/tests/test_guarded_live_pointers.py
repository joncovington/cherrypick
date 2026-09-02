"""Every module with a live gate must have that gate in `configedit.GUARDED`.

This was prose in four CLAUDE.md files, each stating some of the list. Prose cannot fail: a new
module could ship a `live.enabled` and the settings surface would happily write it, while every
instruction file still claimed the surface "can never arm live trading". The invariant is the same
one the docs assert -- it is just enforced here instead of promised there.

Deliberately driven off each module's own config EXAMPLE rather than a hand-kept list, so a module
that adds a live gate is covered the moment it declares one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrypick.orchestrator.configedit import GUARDED

REPO = Path(__file__).resolve().parents[3]

# desk is deliberately absent: it is the discretionary live path, authorized entirely on its own
# (own config, own PIN kept as a salted verifier, per-order ticket) and never through this surface.
# It has no `enable_live_trading` to guard -- borrowing credentials is not borrowing permissions.
MODULE_CONFIGS = {
    "meic": "packages/meic/config.example.json",
    "earnings": "packages/earnings/config/config.example.json",
    "flies": "packages/flies/config.example.json",
    "calendars": "packages/calendars/config.example.json",
    "pmcc": "packages/pmcc/config.example.json",
}


def _live_pointers(doc: dict) -> set[str]:
    """The JSON pointers in `doc` that arm or de-risk live trading."""
    found: set[str] = set()
    if "enable_live_trading" in doc:
        found.add("/enable_live_trading")
    live = doc.get("live")
    if isinstance(live, dict) and "enabled" in live:
        found.add("/live/enabled")
    return found


@pytest.mark.parametrize("module, rel", sorted(MODULE_CONFIGS.items()))
def test_every_declared_live_gate_is_guarded(module: str, rel: str):
    path = REPO / rel
    assert path.exists(), f"{module}: {rel} is missing — update MODULE_CONFIGS"
    doc = json.loads(path.read_text(encoding="utf-8"))

    declared = _live_pointers(doc)
    assert declared, f"{module} declares no live gate; drop it from MODULE_CONFIGS if that is intended"

    guarded = set(GUARDED.get(module, {}))
    missing = declared - guarded
    assert not missing, (
        f"{module} declares {sorted(missing)} but configedit.GUARDED does not refuse it — "
        "the settings surface could arm or de-risk live trading for this module"
    )


def test_guarded_names_only_modules_that_exist():
    """A stale entry is harmless but misleading: it reads as a protection that guards nothing."""
    unknown = set(GUARDED) - set(MODULE_CONFIGS)
    assert not unknown, f"GUARDED names modules with no config here: {sorted(unknown)}"


def test_flies_keeps_its_extra_live_pointers_guarded():
    """flies carries more than the on/off switch, and all of it must stay unreachable.

    gate0_confirmed is an attestation that a quantitative gate was passed; the loss and deploy
    limits bound what a live pilot can lose. Guarding `enabled` alone would leave a surface that
    cannot arm the loop but can widen it once armed.
    """
    assert {
        "/live/enabled",
        "/live/gate0_confirmed",
        "/live/daily_loss_halt_dollars",
        "/live/account_deploy_limit_pct",
    } <= set(GUARDED["flies"])


def test_meic_deploy_limit_is_guarded_with_its_switch():
    assert {"/enable_live_trading", "/account_deploy_limit_pct"} <= set(GUARDED["meic"])
