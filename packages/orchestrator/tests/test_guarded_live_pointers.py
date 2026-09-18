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
import re
from pathlib import Path

import pytest

from cherrypick.orchestrator.configedit import GUARDED

REPO = Path(__file__).resolve().parents[3]

# Every package's config example, found rather than listed (2026-09-18): the hand-kept dict this
# replaced named five modules while bwb and curve had each declared a `live.enabled` placeholder
# that nothing guarded -- the exact gap a guard driven off what the system declares cannot have.
# desk is deliberately excluded: it is the discretionary live path, authorized entirely on its own
# (own config, own PIN kept as a salted verifier, per-order ticket) and never through this surface.
# It has no `enable_live_trading` to guard -- borrowing credentials is not borrowing permissions.
_EXCLUDED = {"desk", "orchestrator"}


def _discover_module_configs() -> dict[str, str]:
    found: dict[str, str] = {}
    for rel in ("config.example.json", "config/config.example.json"):
        for path in sorted((REPO / "packages").glob(f"*/{rel}")):
            module = path.relative_to(REPO / "packages").parts[0]
            if module in _EXCLUDED:
                continue
            found.setdefault(module, str(path.relative_to(REPO)).replace("\\", "/"))
    return found


MODULE_CONFIGS = _discover_module_configs()


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
    if not declared:
        pytest.skip(f"{module} declares no live gate")

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


def test_bwb_keeps_every_live_sizing_and_admission_rule_guarded():
    """bwb's live block carries the arm, the floor, both caps and both breakers beside the switch;
    a surface that cannot arm the loop but can widen it once armed is the flies lesson again."""
    assert {
        "/live/enabled",
        "/live/gate0_confirmed",
        "/live/arm",
        "/live/min_net_credit_dollars",
        "/live/max_open_margin_dollars",
        "/live/max_open_margin_per_expiration_dollars",
        "/live/mark_drawdown_halt_dollars",
        "/live/daily_loss_halt_dollars",
        "/live/account_deploy_limit_pct",
    } <= set(GUARDED["bwb"])


def test_discovery_finds_every_module_with_a_live_gate():
    """The discovery itself, pinned: the five the hand-kept list named plus the two it missed."""
    assert {"meic", "earnings", "flies", "calendars", "pmcc", "bwb", "curve"} <= set(MODULE_CONFIGS)
    assert "desk" not in MODULE_CONFIGS and "orchestrator" not in MODULE_CONFIGS


_ENV_ARMING = re.compile(r"environ\b[^\n]*LIVE", re.IGNORECASE)


def test_no_module_arms_live_from_the_environment():
    """The guarded table only means something if the config file is the ONLY arming surface.
    Until 2026-09-17 meic and earnings both fell back to an ENABLE_LIVE_TRADING environment
    variable when the config key was absent -- armable from a shell with no attestation and no
    guard. Every package's source is scanned for an environment read whose key names LIVE;
    verified to fail by putting the old fallback back."""
    offenders = []
    for src in sorted((REPO / "packages").glob("*/src/**/*.py")):
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
            if _ENV_ARMING.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{src.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "live trading armed from the environment:\n" + "\n".join(offenders)
