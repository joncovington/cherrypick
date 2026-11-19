"""The generic shape: each leg placed by its rule alone, spot 0, no session and no price."""

from __future__ import annotations

from cherrypick.automations import arms, sketch


def _labels(s):
    return {g["name"]: g["label"] for g in s["legs"]}


def test_legs_placed_in_points_sit_at_their_distance_from_the_spot():
    s = sketch.sketch(arms.ARMS["iron-condor-1345"])
    assert _labels(s) == {"short_put": "−5", "long_put": "−10", "short_call": "+5", "long_call": "+10"}
    assert s["exact"] is True
    assert [t["label"] for t in s["ticks"]] == ["−10", "−5", "0 · spot", "+5", "+10"]
    assert (s["top"], s["bottom"]) == ("max profit", "max loss")
    ys = [y for _, y in s["curve"]]
    flat = [y for x, y in s["curve"] if -5 <= x <= 5]
    assert max(ys) == flat[0] == flat[-1] > 0 > min(ys)


def test_a_leg_placed_by_delta_reads_by_its_delta_and_keeps_its_order():
    s = sketch.sketch(arms.ARMS["gld-call-spread-20d"])
    assert _labels(s) == {"long_call": "Δ0.30", "short_call": "Δ0.30 +15"}
    assert s["exact"] is False
    at = {g["name"]: g["at"] for g in s["legs"]}
    assert 0 < at["long_call"] < at["short_call"]
    assert (s["top"], s["bottom"]) == ("max profit", "max loss")
    assert s["curve"][0][1] < 0 < s["curve"][-1][1]  # a debit spread: loses below, gains above


def test_a_leg_of_two_contracts_turns_the_line_twice_as_steeply():
    fly = {**arms.ARMS["iron-fly-1000"], "legs": [
        {"name": "L1", "right": "P", "side": 1, "strike": {"rule": "spot", "points": 0}},
        {"name": "L2", "right": "P", "side": -2, "strike": {"rule": "from_leg", "leg": "L1", "points": -5}},
        {"name": "L3", "right": "P", "side": 1, "strike": {"rule": "from_leg", "leg": "L2", "points": -10}},
    ]}  # fmt: skip
    s = sketch.sketch(fly)
    assert _labels(s) == {"L1": "0", "L2": "−5", "L3": "−15"}
    pts = dict((x, y) for x, y in s["curve"])
    assert pts[-5.0] == max(pts.values())  # the body is the peak
    assert pts[0.0] - pts[-5.0] == -5  # one contract up to the spot: 5 points over 5
    assert pts[-5.0] - pts[-15.0] == 10  # two below the body: 10 over 10 minus the wing's 0


def test_an_unhedged_sold_call_says_its_loss_is_unlimited():
    naked = {**arms.ARMS["iron-fly-1000"], "legs": [
        {"name": "L1", "right": "C", "side": -1, "strike": {"rule": "spot", "points": 10}},
    ]}  # fmt: skip
    s = sketch.sketch(naked)
    assert s["bottom"] == "loss, unlimited" and s["top"] == "max profit"


def test_every_shipped_automation_has_a_shape_with_each_leg_placed():
    for name, arm in arms.ARMS.items():
        s = sketch.sketch(arm)
        assert len(s["legs"]) == len(arm["legs"]) and all(g["label"] != "?" for g in s["legs"]), name
        assert s["curve"] and s["ticks"][0]["at"] <= 0 <= s["ticks"][-1]["at"], name
