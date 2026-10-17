"""The rotation rule, the edition decoder for it, and the benchmark each fund is measured against."""

from __future__ import annotations

from cherrypick.technicals import editions, rotation
from cherrypick.technicals.rotation import RotationRule, classify, quadrant, ratio_readings

# The banded rule (2026-09-27), kept for re-scoring.
RULE = RotationRule(fast=1, slow=2, fast_margin=0.01, slow_margin=0.03, method="excess")


def test_the_default_is_the_daily_quadrant_with_no_band():
    assert rotation.DEFAULT_RULE.method == "ratio"
    assert (rotation.DEFAULT_RULE.slow, rotation.DEFAULT_RULE.fast) == (63, 10)


def test_every_reading_is_placed_in_a_quadrant():
    assert quadrant(1.02, 1.01) == "leading"
    assert quadrant(1.02, 0.99) == "weakening"
    assert quadrant(0.98, 0.99) == "lagging"
    assert quadrant(0.98, 1.01) == "improving"
    # On the line counts as the upper side; only a missing reading has no state.
    assert quadrant(1.0, 1.0) == "leading"
    assert quadrant(None, 1.01) is None


def test_the_level_is_against_its_own_average_and_the_momentum_against_itself():
    # Flat, then a rise over the last three sessions: above its 4-session average, and rising.
    rs = [1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.2, 1.3]
    level, momentum = ratio_readings(rs, slow=4, fast=2)
    assert abs(level - 1.3 / ((1.0 + 1.1 + 1.2 + 1.3) / 4)) < 1e-12
    assert abs(momentum - level / (1.1 / ((1.0 + 1.0 + 1.0 + 1.1) / 4))) < 1e-12
    assert ratio_readings(rs[:5], slow=4, fast=2) is None  # too short for both readings


def test_the_daily_quadrant_reads_a_fund_against_its_benchmark():
    days = [f"d{i:02d}" for i in range(12)]
    spy = {d: 100.0 for d in days}
    # A fund that ran ahead of SPY and has since started to fade: still above its average, falling.
    path = [100, 100, 100, 100, 104, 108, 112, 116, 120, 119, 118, 117]
    closes = {"SPY": spy, "AOR": spy, "XLK": dict(zip(days, map(float, path), strict=True))}
    rule = RotationRule(fast=2, slow=6)
    assert rotation.states_on("d11", closes, (), rule)["XLK"] == "weakening"
    # No bar on the day is no state, never the last one carried forward.
    del closes["XLK"]["d11"]
    assert rotation.states_on("d11", closes, (), rule)["XLK"] is None


def test_the_four_quadrants():
    assert classify(0.05, 0.02, RULE) == "leading"
    assert classify(-0.05, -0.02, RULE) == "lagging"
    assert classify(-0.05, 0.02, RULE) == "improving"
    assert classify(0.05, -0.02, RULE) == "weakening"


def test_inside_either_band_is_no_state_never_a_forced_one():
    """XLK, XLF, XLV and XLC sat in no state on Sept 25."""
    assert classify(0.02, 0.05, RULE) is None
    assert classify(0.05, 0.005, RULE) is None
    assert classify(None, 0.05, RULE) is None


def test_asset_funds_are_measured_against_aor_and_the_rest_against_spy():
    days = {"d0": 0, "d1": 1, "d2": 2}
    closes = {
        "SPY": {d: 100.0 * 1.10**i for d, i in days.items()},  # rises 10% a day
        "AOR": {d: 100.0 for d in days},  # flat
        "XLK": {d: 100.0 * 1.10**i for d, i in days.items()},  # moves with SPY
    }
    states = rotation.states_on("d2", closes, ("SPY",), RULE)
    assert states["SPY"] == "leading", "SPY against AOR, not against itself"
    assert states["XLK"] is None, "a sector fund moving with SPY has no relative trend"
    assert "AOR" not in states


def _rotation_html(prose, sections):
    link = '<a style="x" href="https://x/?symbol={s}" target="_blank">{s}</a>, {k})'
    body = "".join(
        f"<div>{heading}</div>" + ", ".join(f"Name ({link.format(s=s, k=k)}" for s, k in funds)
        for heading, funds in sections
    )
    return f"<h2>Sector Rotation</h2><p>{prose}</p>{body}<h2>Relative Strength Leadership</h2>"


def test_the_decoder_finds_headings_by_markup_not_by_the_same_words_in_prose():
    html = _rotation_html(
        "Industrials climbed out of Confirmed Weakness into Early Rotation; Energy is Maturing Leadership.",
        [
            ("Confirmed Leadership", [("HACK", "Industry"), ("SPY", "Asset")]),
            ("Early Rotation candidates", [("XLI", "Sector")]),
            ("Maturing Leadership Rolling Over", [("XLE", "Sector")]),
            ("Confirmed Weakness", [("TLT", "Asset")]),
        ],
    )
    assert editions.decode_rotation(html) == {
        "HACK": "leading",
        "SPY": "leading",
        "XLI": "improving",
        "XLE": "weakening",
        "TLT": "lagging",
    }
