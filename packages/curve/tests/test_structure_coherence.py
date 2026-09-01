"""Can the shipped structure ever clear its own credit floor?

The failure this exists to catch, measured 2026-08-27: `spread_width` was 5.0 and
`min_credit_pct_of_width` was 0.15, and the module had refused entry on EVERY attempt since it
began evaluating — no position ever opened.

The two numbers were arithmetically incompatible. A call credit spread's credit can never exceed
the short's own premium, so `credit / width <= short_mid / width` is a hard ceiling that no wing
price can lift. At a ~30-delta short on VXX near $18 the short is worth about $0.88, so a 5-wide
spread ceilings at 17.6% — leaving under $0.13 for a wing that actually cost $0.435, and landing at
8.9% against a 15% floor. Sweeping every strike, 5-wide only cleared 15% at delta 0.46+, an
essentially at-the-money short: a different strategy from the ~30-delta one this module documents.

$5 on an $18 underlying is 27.6% of spot. MEIC's 5-point SPX wings are 0.06% of spot, and the 5.0
read as an index-scale number that was never rescaled to VXX.

This guard is deliberately about the SHIPPED numbers rather than a live chain: it needs no market,
and it fails at declaration time rather than after a month of silent refusals.
"""

import json
import pathlib

# The short's premium as a fraction of SPOT, at the ~30-delta the module targets. Measured on the
# 2026-10-16 VXX chain (spot 18.105, K=21 delta 0.32 mid 0.88 -> 4.9%); 4.0% is the conservative
# end of what a 50-DTE 30-delta VXX call is worth, and the guard is a floor test, not a forecast.
SHORT_PREMIUM_PCT_OF_SPOT = 0.040
# VXX has spent 2026 in the high teens; the guard wants the level this structure is sized against,
# not a live quote.
REFERENCE_SPOT = 18.0


def _defaults() -> dict:
    path = pathlib.Path(__file__).resolve().parents[1] / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))["defaults"]


def _ceiling(defaults: dict) -> float:
    """The best `credit / width` this structure could reach with a FREE wing."""
    short_premium = REFERENCE_SPOT * SHORT_PREMIUM_PCT_OF_SPOT
    return short_premium / defaults["spread_width"]


def test_the_shipped_width_can_actually_clear_the_shipped_credit_floor():
    d = _defaults()
    ceiling, floor = _ceiling(d), d["min_credit_pct_of_width"]
    assert ceiling > floor, (
        f"spread_width={d['spread_width']} ceilings at {ceiling:.1%} of width with a FREE wing, "
        f"under the {floor:.0%} floor — the structure can never fill, whatever the market does"
    )


def test_the_ceiling_clears_the_floor_with_room_for_a_real_wing():
    """Clearing the floor only with a free wing is not clearing it. The wing costs real money —
    at 5-wide the ceiling was 17.6% against a 15% floor and the structure STILL never filled,
    because the wing needed to come in under $0.13 and cost $0.435."""
    d = _defaults()
    ceiling, floor = _ceiling(d), d["min_credit_pct_of_width"]
    assert ceiling >= floor * 1.5, (
        f"ceiling {ceiling:.1%} leaves only {ceiling - floor:.1%} of width for the wing above the "
        f"{floor:.0%} floor — too little for a real wing to fit inside"
    )


def test_the_guard_catches_the_structure_that_actually_shipped():
    """It has to be able to fail, so prove it on the 5-wide that ran for days without trading."""
    broken = {**_defaults(), "spread_width": 5.0, "min_credit_pct_of_width": 0.15}
    ceiling = _ceiling(broken)
    assert ceiling < broken["min_credit_pct_of_width"] * 1.5, (
        "the 5-wide/15% pairing must trip this guard; it refused every entry for days"
    )
