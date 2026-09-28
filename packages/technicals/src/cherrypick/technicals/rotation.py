"""Sector rotation: each fund's state from a slow and a fast relative-strength trend.

The report's four states map onto the relative-rotation quadrants: *leading* (the vendor's
"Confirmed Leadership") and *lagging* ("Confirmed Weakness") when the slow and fast trends agree,
*improving* ("Early Rotation candidates") when the slow trend is down and the fast one has turned up,
*weakening* ("Maturing Leadership Rolling Over") when the slow trend is up and the fast one has
turned down. A fund whose trends sit inside the neutral bands is in no state -- XLK, XLF, XLV and
XLC were in none on Sept 25, and an engine must say so rather than force one.

Each trend is the fund's return minus its benchmark's over the window: SPY for the sector and
industry funds, AOR for the funds the vendor types "Asset" (`symbols.ASSET_ETFS`).

**The numbers are parameters, fitted on five editions (2026-09-27).** A 10-session fast trend
against a 63-session slow one, with neutral bands of 1% and 3%, gives the vendor's exact state for
91 of its 119 placements (76%) and puts 26 funds in a state the vendor left empty. Dropping the fast
band lifts agreement to 82% at 35 extras. Like the stage rule, it is declared and re-scored as
editions accumulate, not tuned further on five days.
"""

from __future__ import annotations

from dataclasses import dataclass

from .stage import excess

STATES = ("leading", "improving", "weakening", "lagging")


@dataclass(frozen=True)
class RotationRule:
    fast: int = 10
    slow: int = 63
    fast_margin: float = 0.01
    slow_margin: float = 0.03
    benchmark: str = "SPY"
    asset_benchmark: str = "AOR"
    name: str = "fit-2026-09-27"


DEFAULT_RULE = RotationRule()


def classify(slow: float | None, fast: float | None, rule: RotationRule = DEFAULT_RULE) -> str | None:
    if slow is None or fast is None:
        return None
    up_slow, down_slow = slow > rule.slow_margin, slow < -rule.slow_margin
    up_fast, down_fast = fast > rule.fast_margin, fast < -rule.fast_margin
    if up_slow and up_fast:
        return "leading"
    if down_slow and down_fast:
        return "lagging"
    if down_slow and up_fast:
        return "improving"
    if up_slow and down_fast:
        return "weakening"
    return None


def states_on(
    day: str,
    closes: dict[str, dict[str, float]],
    asset_funds: tuple[str, ...],
    rule: RotationRule = DEFAULT_RULE,
) -> dict[str, str | None]:
    """Every fund's state on `day`. `closes` must hold both benchmarks as well as the funds. SPY is
    an Asset fund, so it is measured against AOR; the benchmarks are not otherwise states."""
    out = {}
    for fund, series in closes.items():
        if fund in (rule.benchmark, rule.asset_benchmark) and fund not in asset_funds:
            continue
        bench_name = rule.asset_benchmark if fund in asset_funds else rule.benchmark
        bench = closes.get(bench_name) or {}
        sessions = sorted(bench)
        out[fund] = classify(
            excess(series, bench, sessions, day, rule.slow),
            excess(series, bench, sessions, day, rule.fast),
            rule,
        )
    return out
