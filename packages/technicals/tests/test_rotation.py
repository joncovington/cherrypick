"""The rotation rule, the edition decoder for it, and the benchmark each fund is measured against."""

from __future__ import annotations

from cherrypick.technicals import editions, rotation
from cherrypick.technicals.rotation import RotationRule, classify

RULE = RotationRule(fast=1, slow=2, fast_margin=0.01, slow_margin=0.03)


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
