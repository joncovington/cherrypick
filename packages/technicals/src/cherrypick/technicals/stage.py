"""The relative-strength stage: leader or laggard, and how long it has been one.

The vendor's footnote: *early* shows on the one-month screen only, *building* means one and two
months agree, *confirmed* means one, two and three months all agree, each measured against the
S&P 500.

**The rule is a channel breakout on the price ratio** (refit 2026-10-14, landed 2026-10-27). The
ratio is the name's adjusted close over dividend-adjusted SPY's. A name is a leader when today's
ratio is above **every** ratio of the prior 20 sessions (early), of the prior 20 and 41 (building),
of the prior 20, 41 and 62 (confirmed); a laggard when it is below every one of the prior 14, 30
and 62. No margins and no one-day condition: a new channel high is itself a day on the right side.
Scored against all 24 saved editions (sessions 2026-09-18 to 10-22), the first ten fitting and
the fourteen after them unseen:

| Rule                     | Half            | Same side | Same stage | Extra listings |
|--------------------------|-----------------|-----------|------------|----------------|
| channel (this)           | fit, 1,795      | 97.3%     | 97.5%      | 5.9%           |
| channel (this)           | later, 2,137    | 99.4%     | 97.3%      | 3.9%           |
| excess (`EXCESS_RULE`)   | fit, 1,795      | 97.6%     | 82.4%      | 73%            |
| excess (`EXCESS_RULE`)   | later, 2,137    | 98.3%     | 78.1%      | 88%            |

"Each of the prior N" read as the single ratio N sessions back instead (a point comparison, the
excess rule's shape without margins) loses: 94% side and 77% stage on the later sessions, extras
twice the vendor's listings.

**The rule before it** (`EXCESS_RULE`, fitted 2026-09-27 on five editions) compared excess returns
over 10/30/63 sessions with margins of 1/2/2% and listed a name only on a day its one-day excess
agreed with its side. It placed the vendor's names on the right side but listed most of the
universe besides, which was read then as the vendor's universe varying by day; the channel rule's
4-6% extras say it was the rule. Kept so it can be re-scored (`score-stages --rule excess`).

Pure: closes in, a verdict out.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageRule:
    # Sessions: the "one-, two- and three-month" screens. For "channel", the leader's channels.
    windows: tuple[int, int, int] = (20, 41, 62)
    # The laggard's channels ("channel" only); None reads `windows` for both sides.
    laggard_windows: tuple[int, int, int] | None = (14, 30, 62)
    method: str = "channel"  # "channel" (ratio beyond every prior one) | "excess" (returns over margins)
    margins: tuple[float, float, float] = (0.0, 0.0, 0.0)  # "excess" only: each window must clear it
    day_margin: float = 0.0  # "excess" only: the one-day excess must clear this, on the same side
    benchmark: str = "SPY"  # dividend-adjusted; Dolt carries no SPX
    name: str = field(default="channel-2026-10-27")

    def side_windows(self, side: str) -> tuple[int, int, int]:
        return self.laggard_windows if side == "laggard" and self.laggard_windows else self.windows


CHANNEL_RULE = StageRule()
EXCESS_RULE = StageRule(
    windows=(10, 30, 63),
    laggard_windows=None,
    method="excess",
    margins=(0.01, 0.02, 0.02),
    name="fit-2026-09-27",
)
DEFAULT_RULE = CHANNEL_RULE
RULES = {"channel": CHANNEL_RULE, "excess": EXCESS_RULE}


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


def _verdict(side: str, one: bool, two: bool, three: bool) -> Stage | None:
    if not one:
        return None
    return Stage(side, "confirmed" if two and three else "building" if two else "early")


def classify(
    window_excess: list[float | None], day_excess: float | None, rule: StageRule = EXCESS_RULE
) -> Stage | None:
    """The excess rule's verdict from the three windows' excess returns and the one-day excess.
    None when the name is neither, or when any reading is missing -- an unmeasured name is never a
    leader."""
    if day_excess is None or any(x is None for x in window_excess):
        return None
    for side, sign in (("leader", 1.0), ("laggard", -1.0)):
        if sign * window_excess[0] > rule.margins[0] and sign * day_excess > rule.day_margin:
            two = sign * window_excess[1] > rule.margins[1]
            three = sign * window_excess[2] > rule.margins[2]
            return _verdict(side, True, two, three)
    return None


def classify_channel(ratios: list[float | None], rule: StageRule = CHANNEL_RULE) -> Stage | None:
    """The channel rule's verdict for the last ratio in `ratios` (the name's close over the
    benchmark's, oldest first). None when the name is neither, or when a ratio the verdict reads is
    missing -- an unmeasured name is never a leader."""
    reach = max(max(rule.windows), max(rule.side_windows("laggard")))
    if len(ratios) < reach + 1:
        return None
    window = ratios[-(reach + 1) :]
    if any(r is None for r in window):
        return None
    today, prior = window[-1], window[:-1]
    for side in ("leader", "laggard"):
        beyond = [
            today > max(prior[-n:]) if side == "leader" else today < min(prior[-n:])
            for n in rule.side_windows(side)
        ]
        verdict = _verdict(side, *beyond)
        if verdict:
            return verdict
    return None


def stage_at(closes: list[float | None], bench: list[float | None], i: int, rule: StageRule = DEFAULT_RULE):
    """The verdict on index `i` of two series on one calendar (the benchmark's), either rule."""
    if rule.method == "channel":
        reach = max(max(rule.windows), max(rule.side_windows("laggard")))
        lo = i - reach
        if lo < 0:
            return None
        pairs = zip(closes[lo : i + 1], bench[lo : i + 1], strict=True)
        return classify_channel([c / b if c and b else None for c, b in pairs], rule)

    def ex(n: int) -> float | None:
        if i - n < 0:
            return None
        a, b, ba, bb = closes[i - n], closes[i], bench[i - n], bench[i]
        if not (a and b and ba and bb):
            return None
        return (b / a - 1.0) - (bb / ba - 1.0)

    return classify([ex(n) for n in rule.windows], ex(1), rule)


def series(closes: list[float | None], bench: list[float | None], rule: StageRule = DEFAULT_RULE) -> list:
    """`stage_at` on every index: the stage history of one name on the benchmark's calendar."""
    return [stage_at(closes, bench, i, rule) for i in range(len(bench))]


def stages_on(
    day: str,
    closes_by_symbol: dict[str, dict[str, float]],
    bench: dict[str, float],
    rule: StageRule = DEFAULT_RULE,
) -> dict[str, Stage]:
    """Every symbol's stage on `day`, over the benchmark's own session calendar."""
    sessions = sorted(bench)
    try:
        i = sessions.index(day)
    except ValueError:
        return {}
    reach = max(max(rule.windows), max(rule.side_windows("laggard")))
    span = sessions[max(0, i - reach) : i + 1]
    b = [bench.get(d) for d in span]
    out = {}
    for sym, closes in closes_by_symbol.items():
        verdict = stage_at([closes.get(d) for d in span], b, len(span) - 1, rule)
        if verdict:
            out[sym] = verdict
    return out
