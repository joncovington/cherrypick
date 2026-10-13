"""Each advisable param's `arms` list must be the arms the module's own rules let it act on.

`advice.bounds.<param>.arms` is how this module tells `cherrypick.core.advice` that a param cannot
move an advised twin of some base (2026-10-12: `delta-trigger-early` put `delta_trigger` on
`control`, which never arms, and its eleven rows matched control's). The list is only worth having
if it is right, so it is derived here from `management.evaluate` -- the one function that decides
whether an open position arms and fires -- rather than kept by hand: a param acts on a base when
varying it inside its bounds changes some decision that base can actually reach.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from cherrypick.bwb import engine, management

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"

DELTAS = [None, 0.05, 0.2, 0.3, 0.4, 0.45, 0.5, 0.6, 0.8, 0.95]
SPOT_FLIP = [(None, None), (100.0, 101.0), (100.0, 100.0), (100.05, 100.0), (101.0, 100.0), (106.0, 100.0)]
CREDITS = [None, 0.0, 0.5, 2.0, 6.0]


def _decisions(base: str, params: dict) -> list[tuple]:
    """Every decision `base` makes over a grid of latch states, ticks and add-on credits -- armed
    only when the base's own trigger can arm it on that grid."""
    out = []
    p = {**management.PARAM_DEFAULTS, **params, "base_book": base, "arm": base}
    can_arm = False
    for peak, below, delta, (spot, flip) in itertools.product(DELTAS, (False, True), DELTAS, SPOT_FLIP):
        state = {"peak_abs_delta": peak, "below_flip_seen": below}
        tick = {"abs_delta": delta, "spot": spot, "gamma_flip": flip}
        decision, _ = management.evaluate({}, p, trigger_state=state, tick=tick, addon_credit=None)
        can_arm = can_arm or decision.action == "arm"
        out.append(decision.action)
    if can_arm:
        for credit in CREDITS:
            decision, _ = management.evaluate(
                {"armed_at": "x"}, p, trigger_state={}, tick={}, addon_credit=credit
            )
            out.append((credit, decision.action))
    return out


def _acts_on(param: str, rule: dict, bases: list[str]) -> set[str]:
    values = [rule["min"], rule["max"], management.PARAM_DEFAULTS[param]]
    return {
        base for base in bases if len({tuple(map(str, _decisions(base, {param: v}))) for v in values}) > 1
    }


def test_every_decision_param_declares_exactly_the_arms_it_acts_on():
    bounds = json.loads(EXAMPLE.read_text(encoding="utf-8"))["advice"]["bounds"]
    bases = list(engine.ARMS) + ["wall"]
    checked = 0
    for param, rule in bounds.items():
        if param not in management.PARAM_DEFAULTS or not isinstance(rule.get("min"), (int, float)):
            continue  # entry-side params act on every base through the shared plan
        if param in ("credit_floor", "max_quote_age_seconds", "max_leg_spread_pct"):
            continue  # entry and mark gates: every base enters and marks the same structure
        acts = _acts_on(param, rule, bases)
        declared = set(rule.get("arms") or bases)
        assert declared == acts, f"{param}: declares {sorted(declared)}, acts on {sorted(acts)}"
        checked += 1
    assert checked == 4, "the four add-on params are all checked"


def test_control_twin_of_a_trigger_param_is_refused_by_the_shared_validator():
    from cherrypick.core import advice

    bounds = json.loads(EXAMPLE.read_text(encoding="utf-8"))["advice"]["bounds"]
    proposals = [{"param": "delta_trigger", "value": 0.3}]
    refused = advice.validate_proposals(proposals, bounds, "control")
    assert refused["ok"] is False
    assert "cannot act on base arm 'control'" in refused["rejected"][0]["reason"]
    assert advice.validate_proposals(proposals, bounds, "delta")["ok"] is True
