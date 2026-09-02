"""The gate/phase rules, exercised as pure functions.

The rules under test are the package's whole opinion: missing data can never produce RED, missing
data always blocks GREEN, and every verdict names its evidence.
"""

from cherrypick.overview import gates


def _reading(value):
    return {
        "value": value,
        "basis": "prior",
        "session": "2026-08-14",
        "as_of": None,
        "source": "test",
        "label": "test",
    }


def _readings(vix=14.6, vix3m=18.9, vvix=90.9, change=0.7):
    return {
        "vix": _reading(vix),
        "vix3m": _reading(vix3m),
        "vvix": _reading(vvix),
        "spx_prior_change_pct": _reading(change),
    }


def _levels(ref=7798.99, flip=7560.58, put_wall=7500.0, call_wall=7800.0):
    return {"reference_price": ref, "zero_gamma": flip, "put_wall": put_wall, "call_wall": call_wall}


def _by_id(gate_list):
    return {g["id"]: g for g in gate_list}


def test_all_measured_and_met_is_green():
    gate_list = gates.evaluate(_readings(), _levels())
    assert all(g["status"] == gates.MET for g in gate_list)
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "green"
    assert verdict["gates_measured"] == 5
    assert verdict["gates_met"] == 5


def test_inverted_vol_curve_is_red():
    gate_list = gates.evaluate(_readings(vix=25.0, vix3m=22.0), _levels())
    assert _by_id(gate_list)["vol_curve"]["status"] == gates.NOT_MET
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "red"
    assert "Vol curve" in verdict["reason"]


def test_vvix_at_stress_line_is_red():
    gate_list = gates.evaluate(_readings(vvix=gates.VVIX_STRESS), _levels())
    assert gates.phase(gate_list)["phase"] == "red"


def test_missing_stress_reading_blocks_green_but_never_red():
    readings = _readings()
    readings["vvix"] = _reading(None)
    gate_list = gates.evaluate(readings, _levels())
    assert _by_id(gate_list)["vol_of_vol"]["status"] == gates.UNKNOWN
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "yellow"
    assert "unmeasured" in verdict["reason"]


def test_non_stress_failure_is_yellow_with_named_blocker():
    gate_list = gates.evaluate(_readings(change=2.4), _levels())
    assert _by_id(gate_list)["calm_tape"]["status"] == gates.NOT_MET
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "yellow"
    assert "Prior session" in verdict["reason"]


def test_spot_below_flip_is_yellow_not_red():
    gate_list = gates.evaluate(_readings(), _levels(ref=7400.0))
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "yellow"
    # 7400 is also below the 7500 put wall, so both positioning gates fail together.
    assert _by_id(gate_list)["inside_walls"]["status"] == gates.NOT_MET


def test_nothing_measured_is_yellow():
    empty = {k: _reading(None) for k in ("vix", "vix3m", "vvix", "spx_prior_change_pct")}
    gate_list = gates.evaluate(empty, {})
    assert all(g["status"] == gates.UNKNOWN for g in gate_list)
    verdict = gates.phase(gate_list)
    assert verdict["phase"] == "yellow"
    assert verdict["gates_measured"] == 0
