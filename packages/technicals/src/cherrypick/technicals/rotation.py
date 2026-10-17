"""Sector rotation: each fund's quadrant from its relative strength's level and momentum.

The report's four states are the relative-rotation quadrants: *leading* (the vendor's "Confirmed
Leadership"), *improving* ("Early Rotation candidates"), *weakening* ("Maturing Leadership Rolling
Over") and *lagging* ("Confirmed Weakness"). Relative strength is the fund's close over its
benchmark's: SPY for the sector and industry funds, AOR for the funds the vendor types "Asset"
(`symbols.ASSET_ETFS`).

**The daily quadrant (fit 2026-10-17, `method="ratio"`, the default).** The vendor's daily view
places every fund it shows in a quadrant -- there is no neutral band. The level is relative strength
over its own `slow`-session average, the momentum is that level against itself `fast` sessions
earlier, and each is read against 1. With 63 and 10 sessions it gives the vendor's quadrant for 84%
of 333 placements on the first 13 saved editions and 80% of 163 on the five after (93% of the 11
sector funds there), against 55% for the banded rule below on all 18. Windows from 63 to 126 and a
10-session momentum all sit on one plateau; 63 is the middle of it and the old slow window.

**The banded rule (fit 2026-09-27, `method="excess"`).** Each trend is the fund's return minus its
benchmark's over the window, and a fund inside either neutral band (1% fast, 3% slow) is in no
state. Fitted on five editions, it ran weeks behind the daily view (XLK leading from Sept 4 to 30
by the vendor, from Sept 30 by it). Kept for re-scoring, not used.
"""

from __future__ import annotations

from dataclasses import dataclass

from .stage import excess

STATES = ("leading", "improving", "weakening", "lagging")


@dataclass(frozen=True)
class RotationRule:
    fast: int = 10
    slow: int = 63
    fast_margin: float = 0.0
    slow_margin: float = 0.0
    benchmark: str = "SPY"
    asset_benchmark: str = "AOR"
    method: str = "ratio"  # "ratio": the daily quadrant; "excess": the banded trend rule
    name: str = "fit-2026-10-17"


DEFAULT_RULE = RotationRule()
BANDED_RULE = RotationRule(fast_margin=0.01, slow_margin=0.03, method="excess", name="fit-2026-09-27")


def classify(slow: float | None, fast: float | None, rule: RotationRule = BANDED_RULE) -> str | None:
    """The banded rule's state from the slow and fast excess returns; None inside either band."""
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


def quadrant(level: float | None, momentum: float | None) -> str | None:
    """The daily quadrant from the level and momentum, each read against 1. Every reading is placed."""
    if level is None or momentum is None:
        return None
    if level >= 1.0:
        return "leading" if momentum >= 1.0 else "weakening"
    return "improving" if momentum >= 1.0 else "lagging"


def ratio_readings(rs: list[float], slow: int, fast: int) -> tuple[float, float] | None:
    """(level, momentum) at the end of a relative-strength series: the last value over its `slow`
    average, and that level over the same level `fast` sessions earlier. None when too short."""
    if len(rs) < slow + fast:
        return None

    def level(i: int) -> float:
        window = rs[i - slow + 1 : i + 1]
        return rs[i] / (sum(window) / slow)

    last = len(rs) - 1
    now, then = level(last), level(last - fast)
    return now, now / then


def _rs_upto(series: dict[str, float], bench: dict[str, float], day: str) -> list[float]:
    """The fund over its benchmark on every benchmark session to `day` where both closed."""
    return [series[d] / bench[d] for d in sorted(bench) if d <= day and d in series and bench[d] > 0]


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
        if rule.method == "ratio":
            if day not in series or day not in bench:
                out[fund] = None
                continue
            r = ratio_readings(_rs_upto(series, bench, day), rule.slow, rule.fast)
            out[fund] = quadrant(*r) if r else None
            continue
        sessions = sorted(bench)
        out[fund] = classify(
            excess(series, bench, sessions, day, rule.slow),
            excess(series, bench, sessions, day, rule.fast),
            rule,
        )
    return out
