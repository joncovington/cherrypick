"""Every parameter the advisor is allowed to move must be one the paper engine actually reads.

A bound over a parameter no code consumes is not harmless. It validates, it is admitted, and the
loop then produces an `advised:<base>` book byte-identical to its control — a spent experiment slot
that could not have measured anything either way. This module has already retired an arm for exactly
that (`sign`: 3,036 blocked attempts, zero fills, 100% decision-agreement with control, "the arm
produced no measurement and could not"), and reaching the same state through a config bound is
cheaper and quieter.

Found on this lint's first run: `entry_price_strategy` shipped in `config.example.json`'s advice
bounds and appears nowhere in the package's source. It is consumed only by the agent-driven live
path (`.claude/commands/execute-entry.md`), and the advisor only ever influences paper.

Driven off the config the repo SHIPS rather than a hand-kept list, so a bound added later is covered
the moment it is declared.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG / "src"))

SRC = PKG / "src" / "cherrypick" / "meic"


def _example_bounds() -> dict:
    config = json.loads((PKG / "config.example.json").read_text(encoding="utf-8"))
    bounds = (config.get("advice") or {}).get("bounds") or {}
    return {k: v for k, v in bounds.items() if not k.startswith("_")}


def _source() -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(SRC.glob("*.py")))


def test_the_example_config_declares_some_bounds():
    """Guards the lint itself: an empty bounds block would make every assertion below vacuous."""
    assert _example_bounds(), "no advice bounds declared — the checks below would pass over nothing"


@pytest.mark.parametrize("param", sorted(_example_bounds()))
def test_each_bounded_param_is_read_by_the_engine(param):
    """Matched as a `params[...]`/`params.get(...)` lookup, not as a bare substring.

    The distinction matters: `entry_price_strategy` appears in this package's CLAUDE.md and in the
    config file itself, so a substring search over the repo would have called it used. What makes a
    bound live is that the ENGINE reads it out of the merged params the advised overlay produces.
    """
    lookup = re.compile(r"params(?:\.get\(|\[)\s*[\"']" + re.escape(param) + r"[\"']")
    assert lookup.search(_source()), (
        f"advice.bounds declares {param!r}, but no paper-engine params lookup reads it — "
        "a proposal on it would produce an advised book identical to its control"
    )


def test_every_bound_is_a_closed_range_or_an_enumeration():
    """`cherrypick.core.advice._check_proposal` admits a numeric only against BOTH min and max. A
    half-open bound rejects every numeric proposal with 'declares no closed range', which reads as
    the advisor behaving badly rather than as a config error."""
    for param, rule in _example_bounds().items():
        if "choices" in rule:
            assert rule["choices"], f"{param} declares an empty choice list"
        else:
            assert rule.get("min") is not None and rule.get("max") is not None, (
                f"{param} declares no closed range"
            )
            assert rule["min"] <= rule["max"], f"{param} has min above max"


def test_the_call_otm_floor_can_only_tighten_the_advised_book():
    """The property that made this bound safe to grant, pinned so a later widening is deliberate.

    `control` — the base the advised twin shadows — runs min_call_otm_pct 0.0001, and a HIGHER floor
    pushes the short call further out of the money. So every admissible value refuses at least as
    much as the control does. Lowering this floor below the base would let the advisor take trades
    its own control would not, which is a different kind of change and should not arrive silently.
    """
    from cherrypick.meic import paper

    rule = _example_bounds().get("min_call_otm_pct")
    if rule is None:
        pytest.skip("min_call_otm_pct is not bounded in the shipped example")

    base = paper.load_profiles()["control"]["min_call_otm_pct"]
    assert rule["min"] >= base, (
        f"the bound's floor ({rule['min']}) is below control's own ({base}) — this would permit "
        "the advised book to enter closer to the money than its control"
    )
