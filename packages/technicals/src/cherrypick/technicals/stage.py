"""The relative-strength stage: leader or laggard, and how long it has been one.

The vendor's footnote: *early* shows on the one-month screen only, *building* means one and two
months agree, *confirmed* means one, two and three months all agree, each measured against the
S&P 500. Fitting that against the five saved editions (2026-09-27) added two things the footnote
does not say:

- **A name is listed only on a day its own one-day move agrees with its side.** BKNG trailed the
  S&P by 16-26% on every window and was a confirmed laggard on four straight editions; the one day
  it beat the index by 1.0% it was absent. CRWD, NVO and FFIV behave the same way. Adding that
  condition cut the names wrongly listed from 859 to ~295 across the five days.
- **The windows and margins are parameters, not settled facts.** The best fit on five days is
  10/30/63 sessions with margins of 1%, 2% and 2% against dividend-adjusted SPY: 955 of the
  vendor's 980 listings on the right side, the exact stage on 786. The ~295 names it lists that
  the vendor does not are not removed by any margin, which says the vendor's universe itself varies
  by day -- so counts are compared as rates, and the rule is re-scored as editions accumulate
  rather than tuned further on five days.

Pure: excess returns in, a verdict out.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageRule:
    windows: tuple[int, int, int] = (10, 30, 63)  # sessions: the "one-, two- and three-month" screens
    margins: tuple[float, float, float] = (0.01, 0.02, 0.02)  # excess return each window must clear
    day_margin: float = 0.0  # the one-day excess must clear this, on the same side
    benchmark: str = "SPY"  # dividend-adjusted; Dolt carries no SPX
    name: str = field(default="fit-2026-09-27")


DEFAULT_RULE = StageRule()


@dataclass(frozen=True)
class Stage:
    side: str  # "leader" | "laggard"
    stage: str  # "confirmed" | "building" | "early"


def excess(
    closes: dict[str, float], bench: dict[str, float], sessions: list[str], day: str, n: int
) -> float | None:
    """The name's return over the `n` sessions ending `day` minus the benchmark's, or None when
    either lacks a close at either end."""
    try:
        i = sessions.index(day)
    except ValueError:
        return None
    if i - n < 0:
        return None
    start = sessions[i - n]
    a, b, ba, bb = closes.get(start), closes.get(day), bench.get(start), bench.get(day)
    if not (a and b and ba and bb):
        return None
    return (b / a - 1.0) - (bb / ba - 1.0)


def classify(
    window_excess: list[float | None], day_excess: float | None, rule: StageRule = DEFAULT_RULE
) -> Stage | None:
    """A verdict from the three windows' excess returns and the one-day excess. None when the name
    is neither, or when any reading is missing -- an unmeasured name is never a leader."""
    if day_excess is None or any(x is None for x in window_excess):
        return None
    for side, sign in (("leader", 1.0), ("laggard", -1.0)):
        if sign * window_excess[0] > rule.margins[0] and sign * day_excess > rule.day_margin:
            two = sign * window_excess[1] > rule.margins[1]
            three = sign * window_excess[2] > rule.margins[2]
            return Stage(side, "confirmed" if two and three else "building" if two else "early")
    return None


def stages_on(
    day: str,
    closes_by_symbol: dict[str, dict[str, float]],
    bench: dict[str, float],
    rule: StageRule = DEFAULT_RULE,
) -> dict[str, Stage]:
    """Every symbol's stage on `day`, over the benchmark's own session calendar."""
    sessions = sorted(bench)
    out = {}
    for sym, closes in closes_by_symbol.items():
        windows = [excess(closes, bench, sessions, day, n) for n in rule.windows]
        verdict = classify(windows, excess(closes, bench, sessions, day, 1), rule)
        if verdict:
            out[sym] = verdict
    return out
