"""A grid study's block (cherrypick.backtest.grid): settings lined up from a result document, the live
setting and every passing one labelled plateau or lone peak. Invented documents only."""

from __future__ import annotations

import json

import pytest

from cherrypick.backtest import grid


def doc(means: dict[str, float | None], verdicts: dict[str, str] | None = None, reference=("d16",)):
    """A result document in the shape items 23, 24 and 43 write: tested arms under `arms`, the base
    under `reference` as its own figures."""
    verdicts = verdicts or {}
    arms = {
        a: {"own": {"mean_net": m, "n": 120}, "verdict": verdicts.get(a, "fail")}
        for a, m in means.items()
        if a not in reference
    }
    ref = {a: {"mean_net": means[a], "n": 120} for a in reference if a in means}
    return {"arms": arms, "reference": ref}


SETTINGS = {"d12": 0.12, "d16": 0.16, "d20": 0.20, "d25": 0.25}


def test_rows_are_in_setting_order_and_read_the_base_from_reference():
    b = grid.block(doc({"d25": 1.0, "d12": 4.0, "d16": 5.0, "d20": 3.0}), "short delta", SETTINGS, "d16")
    assert [r["arm"] for r in b["rows"]] == ["d12", "d16", "d20", "d25"]
    assert [r["mean"] for r in b["rows"]] == [4.0, 5.0, 3.0, 1.0]
    assert [r["declared"] for r in b["rows"]] == [False, True, False, False]
    assert b["rows"][1]["verdict"] is None  # the base is a reference, never judged
    assert b["measure"] == grid.MEASURE


def test_the_live_setting_reads_plateau_when_its_neighbours_hold_and_lone_peak_when_one_falls():
    held = grid.block(doc({"d12": 4.0, "d16": 5.0, "d20": 3.0, "d25": 1.0}), "short delta", SETTINGS, "d16")
    assert held["neighbourhood"] == {"d16": "plateau"}
    fell = grid.block(doc({"d12": 1.0, "d16": 5.0, "d20": 3.0, "d25": 1.0}), "short delta", SETTINGS, "d16")
    assert fell["neighbourhood"] == {"d16": "lone_peak"}


def test_a_losing_live_setting_reads_not_positive_even_where_its_neighbours_lose_less():
    b = grid.block(doc({"d12": -1.0, "d16": -10.0, "d20": -2.0, "d25": 1.0}), "short delta", SETTINGS, "d16")
    assert b["neighbourhood"] == {"d16": grid.NOT_POSITIVE}


def test_a_passing_arm_gets_its_own_label_and_a_failing_one_does_not():
    b = grid.block(
        doc({"d12": 1.0, "d16": 2.0, "d20": 9.0, "d25": 1.0}, {"d20": "pass"}), "short delta", SETTINGS, "d16"
    )
    assert b["neighbourhood"] == {"d16": grid.NEIGHBOUR_BETTER, "d20": "lone_peak"}


def test_a_setting_on_a_slope_reads_neighbour_better_not_lone_peak():
    # d12 keeps under half of d16's mean, which alone the core calls a lone peak; d20 is higher.
    b = grid.block(doc({"d12": 1.0, "d16": 5.0, "d20": 8.0, "d25": 9.0}), "short delta", SETTINGS, "d16")
    assert b["neighbourhood"] == {"d16": grid.NEIGHBOUR_BETTER}


def test_an_arm_the_document_lacks_reads_unmeasured_not_zero():
    b = grid.block(
        doc({"d12": 4.0, "d20": 3.0}), "short delta", {"d12": 0.12, "d16": 0.16, "d20": 0.2}, "d16"
    )
    assert b["rows"][1]["mean"] is None
    assert b["neighbourhood"] == {"d16": "unmeasured"}


def test_a_reference_entry_may_carry_its_figures_under_own():
    d = {
        "arms": {"s1200": {"own": {"mean_net": 2.0, "n": 5}}},
        "reference": {"s1000": {"own": {"mean_net": 1.0}}},
    }
    b = grid.block(
        d, "entry slot", {"s1000": 600, "s1200": 720}, "s1200", labels={"s1000": "10:00", "s1200": "12:00"}
    )
    assert [(r["label"], r["mean"]) for r in b["rows"]] == [("10:00", 1.0), ("12:00", 2.0)]


def test_annotate_adds_the_block_and_keeps_every_other_key(tmp_path):
    p = tmp_path / "study.json"
    d = {**doc({"d12": 4.0, "d16": 5.0}), "verdict": "fail", "run_id": "r1"}
    p.write_text(json.dumps(d), encoding="utf-8")
    grid.annotate([p], lambda x: grid.block(x, "short delta", {"d12": 0.12, "d16": 0.16}, "d16"))
    back = json.loads(p.read_text(encoding="utf-8"))
    assert {k: back[k] for k in d} == d
    assert back["grid"]["neighbourhood"] == {"d16": "plateau"}


def test_a_declared_arm_outside_the_grid_is_refused():
    with pytest.raises(ValueError, match="not in the grid"):
        grid.block(doc({"d12": 1.0}), "short delta", {"d12": 0.12}, "d16")
