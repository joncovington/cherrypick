"""The mechanical half of the hybrid framework: five gates, one phase, no editorial input.

Each gate is a declared comparison over readings the suite itself produced, so the phase banner is
auditable: every Met/Not-met prints the measured value beside the threshold it was held to, and a
value nobody measured is UNKNOWN -- never silently assumed either way.

The phase rules, stated once here and enforced nowhere else:

- **RED** requires a measured stress failure: the vol curve inverted, or vol-of-vol at/over its
  stress line. Missing data can never produce RED -- an unmeasured market is an unknown market,
  not a crisis.
- **GREEN** requires everything measured to be met, at least MIN_MEASURED_FOR_GREEN of the five
  measured at all, and the three load-bearing gates (contango, vol-of-vol, spot-vs-flip)
  measured explicitly. Missing data blocks GREEN.
- Everything else is **YELLOW**, with the blocking gate named.

The thresholds are constants, versioned in git, on purpose: a threshold that lives in config is a
knob someone turns mid-experiment; one that lives here has a commit explaining it. VVIX 120 keeps
the line the reference reports used, so our phase history stays comparable with the collected
examples; 1.5% is roughly one calm-regime daily sigma for SPX -- past it, the prior session was a
move, not drift.
"""

from __future__ import annotations

from typing import Any

VVIX_STRESS = 120.0
CALM_TAPE_MAX_ABS_PCT = 1.5
MIN_MEASURED_FOR_GREEN = 4

MET = "met"
NOT_MET = "not_met"
UNKNOWN = "unknown"

# Gates whose measured failure is a stress signal (RED), and whose absence blocks GREEN.
STRESS_GATES = ("vol_curve", "vol_of_vol")
GREEN_REQUIRED_MEASURED = ("vol_curve", "vol_of_vol", "spot_vs_flip")


def _gate(gate_id: str, label: str, status: str, detail: str, value=None, threshold=None) -> dict:
    return {
        "id": gate_id,
        "label": label,
        "status": status,
        "value": value,
        "threshold": threshold,
        "detail": detail,
    }


def _num(readings: dict[str, Any], name: str) -> float | None:
    reading = readings.get(name) or {}
    value = reading.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def evaluate(readings: dict[str, Any], levels: dict[str, Any]) -> list[dict]:
    """The five gates, in display order. `readings` is facts.build's readings map; `levels` its
    GEX levels block (reference price + flip/walls)."""
    gates: list[dict] = []

    vix = _num(readings, "vix")
    vix3m = _num(readings, "vix3m")
    if vix is None or vix3m is None:
        gates.append(_gate("vol_curve", "Vol curve contango", UNKNOWN, "VIX or VIX3M not measured"))
    elif vix < vix3m:
        gates.append(_gate("vol_curve", "Vol curve contango", MET,
                           f"VIX {vix:.2f} < VIX3M {vix3m:.2f}", value=vix, threshold=vix3m))
    else:
        gates.append(_gate("vol_curve", "Vol curve contango", NOT_MET,
                           f"inverted: VIX {vix:.2f} >= VIX3M {vix3m:.2f}", value=vix, threshold=vix3m))

    vvix = _num(readings, "vvix")
    if vvix is None:
        gates.append(_gate("vol_of_vol", f"Vol-of-vol below {VVIX_STRESS:.0f}", UNKNOWN,
                           "VVIX not measured", threshold=VVIX_STRESS))
    else:
        ok = vvix < VVIX_STRESS
        gates.append(_gate("vol_of_vol", f"Vol-of-vol below {VVIX_STRESS:.0f}",
                           MET if ok else NOT_MET,
                           f"VVIX {vvix:.2f}", value=vvix, threshold=VVIX_STRESS))

    ref = levels.get("reference_price")
    flip = levels.get("zero_gamma")
    if not isinstance(ref, (int, float)) or not isinstance(flip, (int, float)):
        gates.append(_gate("spot_vs_flip", "Spot above gamma flip", UNKNOWN,
                           "reference price or gamma flip not measured"))
    else:
        ok = ref > flip
        gates.append(_gate("spot_vs_flip", "Spot above gamma flip", MET if ok else NOT_MET,
                           f"ref {ref:.2f} vs flip {flip:.2f}", value=ref, threshold=flip))

    put_wall = levels.get("put_wall")
    call_wall = levels.get("call_wall")
    if (
        not isinstance(ref, (int, float))
        or not isinstance(put_wall, (int, float))
        or not isinstance(call_wall, (int, float))
    ):
        gates.append(_gate("inside_walls", "Inside the wall band", UNKNOWN,
                           "reference price or walls not measured"))
    else:
        ok = put_wall <= ref <= call_wall
        gates.append(_gate("inside_walls", "Inside the wall band", MET if ok else NOT_MET,
                           f"ref {ref:.2f} in [{put_wall:.0f}, {call_wall:.0f}]", value=ref))

    change = _num(readings, "spx_prior_change_pct")
    if change is None:
        gates.append(_gate("calm_tape", f"Prior session within {CALM_TAPE_MAX_ABS_PCT}%", UNKNOWN,
                           "prior-session SPX change not measured", threshold=CALM_TAPE_MAX_ABS_PCT))
    else:
        ok = abs(change) < CALM_TAPE_MAX_ABS_PCT
        gates.append(_gate("calm_tape", f"Prior session within {CALM_TAPE_MAX_ABS_PCT}%",
                           MET if ok else NOT_MET,
                           f"prior SPX move {change:+.2f}%", value=change,
                           threshold=CALM_TAPE_MAX_ABS_PCT))

    return gates


def phase(gates: list[dict]) -> dict:
    """GREEN / YELLOW / RED from the gate list alone. Returns the phase with its reason and the
    measured/met counts so the render and the console never re-derive them."""
    by_id = {g["id"]: g for g in gates}
    measured = [g for g in gates if g["status"] != UNKNOWN]
    met = [g for g in gates if g["status"] == MET]
    counts = {
        "gates_total": len(gates),
        "gates_measured": len(measured),
        "gates_met": len(met),
    }

    for gate_id in STRESS_GATES:
        gate = by_id.get(gate_id)
        if gate and gate["status"] == NOT_MET:
            return {"phase": "red", "reason": f"stress gate failed: {gate['label']} ({gate['detail']})",
                    **counts}

    failed = [g for g in measured if g["status"] == NOT_MET]
    unmeasured_required = [
        by_id[g].get("label", g) for g in GREEN_REQUIRED_MEASURED
        if by_id.get(g, {}).get("status", UNKNOWN) == UNKNOWN
    ]
    if not failed and not unmeasured_required and len(measured) >= MIN_MEASURED_FOR_GREEN:
        return {"phase": "green", "reason": "every measured gate met", **counts}

    if failed:
        reason = "blocked by: " + "; ".join(f"{g['label']} ({g['detail']})" for g in failed)
    elif unmeasured_required:
        reason = "unmeasured: " + ", ".join(unmeasured_required)
    else:
        reason = f"only {len(measured)} of {len(gates)} gates measured"
    return {"phase": "yellow", "reason": reason, **counts}
