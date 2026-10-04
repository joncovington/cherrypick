"""The chart's entry and exit setups: four textbook pairings, long and short, each walked as one position.

The chart page used to mark the vendor's scan-rule matches with arrows. Those are patterns, not
trades: nothing says where one ends. These four say both, so the arrows come in pairs -- an entry,
then the exit its own rule gives -- and a position that has not exited is reported as open.

Each setup is evaluated on the close of a daily bar, and an arrow sits on the bar whose close fired
it; no fill is assumed. A setup holds at most one position: an entry signal while one is open is not
a second position, and a bar that exits does not also enter. The walk runs over the whole history the
store holds, so a position opened before the chart's window still exits where its rule says.

These are the textbook definitions, chosen with the user on 2026-10-03, not fitted to anything and
not scored against outcomes. Each long setup has a short mirror (2026-10-04): every comparison
reversed, highs for lows, the upper band for the lower, an overbought RSI for an oversold one. A long
and a short of the same family are separate positions and never net against each other. Nothing
here reads vendor data; the vendor only checks the trend scores and rank the watchlist shows.
The parameters are the constants below; a change to one is a change to what every arrow means, so
say so where it lands.

The breakout setup needs volume. A cash index has none -- nothing trades as SPX, it is computed from
its members -- so the chart builder passes SPY's volume for SPX and the chart says it did.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from . import indicators

ADX_MIN = 20.0
PULLBACK_RSI = (40.0, 50.0)
PULLBACK_RSI_SHORT = (50.0, 60.0)  # the mirror: a rally that RSI(14) peaked between 50 and 60
# The pullback's RSI is read as the dip's low over the last few sessions, not on the entry bar: on
# bars where the EMAs are stacked and the 21 holds, RSI(14) was never under 50.5 (553 bars, eleven
# names, three years -- 2026-10-03), so the rule as first written could not fire. docs/setups.md.
# The short mirror is the same: on bars stacked down where the 21 rejected the close, RSI never
# reached 50 (232 bars, peak 49.8).
PULLBACK_RSI_WINDOW = 5
OVERSOLD_RSI = 30.0
OVERBOUGHT_RSI = 70.0
TARGET_LOOKBACK = 20  # the pullback's target: the highest high of the sessions before entry
CHANDELIER_N, CHANDELIER_MULT = 22, 3.0
REVERSION_STOP_ATR = 2.0  # x ATR(14) at entry, under the entry close
SQUEEZE_LOOKBACK, SQUEEZE_RECENT = 120, 5
VOLUME_AVG, VOLUME_MULT = 50, 1.5
SUPERTREND_N, SUPERTREND_MULT = 10, 3.0


@dataclass(frozen=True)
class Setup:
    id: str
    name: str
    rule: str
    lines: tuple[str, ...]  # the `lines` series the rule reads, for the chart to draw beside it
    family: str = ""  # the setup a short mirrors; a long is its own family
    side: str = "long"

    def __post_init__(self):
        if not self.family:
            object.__setattr__(self, "family", self.id)


SETUPS = (
    Setup(
        "trend",
        "Trend following",
        "Enter when the 9 EMA crosses above the 21 EMA, with the close above the 50 EMA and ADX(14) "
        "above 20. Exit on the first close under the 21 EMA.",
        ("ema9", "ema21", "ema50"),
    ),
    Setup(
        "pullback",
        "Pullback",
        "Enter when the EMAs are stacked 9 > 21 > 50 and the bar dips to the 21 EMA but closes above "
        "it, after RSI(14) dipped to between 40 and 50 in the last 5 sessions. Exit when the high "
        "reaches the highest high of the 20 sessions before entry (target), or on a close under the "
        "highest high since entry less 3 × ATR(22) (stop); a bar that does both counts as a stop.",
        ("ema9", "ema21", "ema50"),
    ),
    Setup(
        "reversion",
        "Mean reversion",
        "Enter when the low touches the lower Bollinger band (20, 2) with RSI(14) under 30. Exit on "
        "a close at or above the middle band (target), or a close more than 2 × ATR(14) under the "
        "entry close (stop).",
        ("bb_upper", "bb_mid", "bb_lower"),
    ),
    Setup(
        "breakout",
        "Breakout",
        "Enter on a close above the upper Bollinger band (20, 2) within 5 sessions of a squeeze — "
        "the band's width at its narrowest of 120 sessions — on volume above 1.5 × its average of "
        "the 50 sessions before, with Supertrend(10, 3) already up. Exit on the first close with "
        "Supertrend down.",
        ("bb_upper", "bb_mid", "bb_lower", "supertrend"),
    ),
    Setup(
        "trend-short",
        "Trend following (short)",
        "Short when the 9 EMA crosses below the 21 EMA, with the close below the 50 EMA and ADX(14) "
        "above 20. Cover on the first close over the 21 EMA.",
        ("ema9", "ema21", "ema50"),
        "trend",
        "short",
    ),
    Setup(
        "pullback-short",
        "Pullback (short)",
        "Short when the EMAs are stacked 9 < 21 < 50 and the bar rallies to the 21 EMA but closes under "
        "it, after RSI(14) rallied to between 50 and 60 in the last 5 sessions. Cover when the low "
        "reaches the lowest low of the 20 sessions before entry (target), or on a close over the "
        "lowest low since entry plus 3 × ATR(22) (stop); a bar that does both counts as a stop.",
        ("ema9", "ema21", "ema50"),
        "pullback",
        "short",
    ),
    Setup(
        "reversion-short",
        "Mean reversion (short)",
        "Short when the high touches the upper Bollinger band (20, 2) with RSI(14) over 70. Cover on a "
        "close at or under the middle band (target), or a close more than 2 × ATR(14) over the entry "
        "close (stop).",
        ("bb_upper", "bb_mid", "bb_lower"),
        "reversion",
        "short",
    ),
    Setup(
        "breakout-short",
        "Breakdown (short)",
        "Short on a close under the lower Bollinger band (20, 2) within 5 sessions of a squeeze — the "
        "band's width at its narrowest of 120 sessions — on volume above 1.5 × its average of the 50 "
        "sessions before, with Supertrend(10, 3) already down. Cover on the first close with Supertrend "
        "up.",
        ("bb_upper", "bb_mid", "bb_lower", "supertrend"),
        "breakout",
        "short",
    ),
)


@dataclass(frozen=True)
class Trade:
    entry: int
    exit: int | None = None
    reason: str | None = None
    target: float | None = None


@dataclass(frozen=True)
class Readings:
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float | None]
    ema9: list[float | None]
    ema21: list[float | None]
    ema50: list[float | None]
    adx14: list[float | None]
    rsi14: list[float | None]
    atr14: list[float | None]
    atr22: list[float | None]
    bb_upper: list[float | None]
    bb_mid: list[float | None]
    bb_lower: list[float | None]
    squeeze: list[bool]
    supertrend: list[float | None]
    supertrend_up: list[bool | None]


def _squeezes(upper, mid, lower) -> list[bool]:
    """True where the band's width, (upper - lower) / middle, is the narrowest of the last
    `SQUEEZE_LOOKBACK` sessions (all of them defined)."""
    width = [
        None if m is None or m == 0 else (u - lo) / m for u, m, lo in zip(upper, mid, lower, strict=True)
    ]
    out = [False] * len(width)
    for i in range(SQUEEZE_LOOKBACK - 1, len(width)):
        window = width[i - SQUEEZE_LOOKBACK + 1 : i + 1]
        if None not in window and width[i] <= min(window):
            out[i] = True
    return out


def readings(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float | None]
) -> Readings:
    upper, mid, lower = indicators.bollinger(closes)
    line, up = indicators.supertrend(highs, lows, closes, SUPERTREND_N, SUPERTREND_MULT)
    return Readings(
        highs,
        lows,
        closes,
        volumes,
        indicators.ema(closes, 9),
        indicators.ema(closes, 21),
        indicators.ema(closes, 50),
        indicators.adx(highs, lows, closes, 14),
        indicators.rsi(closes, 14),
        indicators.atr(highs, lows, closes, 14),
        indicators.atr(highs, lows, closes, CHANDELIER_N),
        upper,
        mid,
        lower,
        _squeezes(upper, mid, lower),
        line,
        up,
    )


def _defined(*vs) -> bool:
    return all(v is not None for v in vs)


def _walk(
    size: int, enter: Callable[[int], Trade | None], leave: Callable[[int, Trade], str | None]
) -> list[Trade]:
    trades: list[Trade] = []
    held: Trade | None = None
    for i in range(size):
        if held is not None:
            reason = leave(i, held)
            if reason is not None:
                trades.append(replace(held, exit=i, reason=reason))
                held = None
            continue
        held = enter(i)
    if held is not None:
        trades.append(held)
    return trades


def trend(r: Readings) -> list[Trade]:
    def enter(i):
        if i < 1 or not _defined(
            r.ema9[i - 1], r.ema21[i - 1], r.ema9[i], r.ema21[i], r.ema50[i], r.adx14[i]
        ):
            return None
        crossed = r.ema9[i - 1] <= r.ema21[i - 1] and r.ema9[i] > r.ema21[i]
        return Trade(i) if crossed and r.closes[i] > r.ema50[i] and r.adx14[i] > ADX_MIN else None

    def leave(i, t):
        return "21 EMA" if r.closes[i] < r.ema21[i] else None

    return _walk(len(r.closes), enter, leave)


def pullback(r: Readings) -> list[Trade]:
    lo, hi = PULLBACK_RSI

    def enter(i):
        recent = r.rsi14[max(0, i - PULLBACK_RSI_WINDOW + 1) : i + 1]
        if i < TARGET_LOOKBACK or not _defined(r.ema9[i], r.ema21[i], r.ema50[i], *recent):
            return None
        stacked = r.ema9[i] > r.ema21[i] > r.ema50[i]
        held = r.lows[i] <= r.ema21[i] < r.closes[i]
        if not (stacked and held and lo <= min(recent) <= hi):
            return None
        target = max(r.highs[i - TARGET_LOOKBACK : i])
        return Trade(i, target=target if target > r.closes[i] else None)

    def leave(i, t):
        a = r.atr22[i]
        if a is not None and r.closes[i] < max(r.highs[t.entry : i + 1]) - CHANDELIER_MULT * a:
            return "stop"
        if t.target is not None and r.highs[i] >= t.target:
            return "target"
        return None

    return _walk(len(r.closes), enter, leave)


def reversion(r: Readings) -> list[Trade]:
    def enter(i):
        if not _defined(r.bb_lower[i], r.rsi14[i], r.atr14[i]):
            return None
        return Trade(i) if r.lows[i] <= r.bb_lower[i] and r.rsi14[i] < OVERSOLD_RSI else None

    def leave(i, t):
        if r.closes[i] < r.closes[t.entry] - REVERSION_STOP_ATR * r.atr14[t.entry]:
            return "stop"
        if r.closes[i] >= r.bb_mid[i]:
            return "target"
        return None

    return _walk(len(r.closes), enter, leave)


def volume_confirms(volumes: list[float | None], i: int) -> bool:
    """Today's volume above `VOLUME_MULT` x the mean of the `VOLUME_AVG` sessions before it; False
    when any of them is missing -- an unmeasured volume never confirms."""
    if i < VOLUME_AVG:
        return False
    window = volumes[i - VOLUME_AVG : i]
    if volumes[i] is None or None in window:
        return False
    return volumes[i] > VOLUME_MULT * sum(window) / VOLUME_AVG


def breakout(r: Readings) -> list[Trade]:
    def enter(i):
        if not _defined(r.bb_upper[i]) or not any(r.squeeze[max(0, i - SQUEEZE_RECENT + 1) : i + 1]):
            return None
        # Supertrend must already be up: entered against it, the exit fired the next close on 33 of
        # 45 such breakouts (2026-10-04) -- a one-day round trip, not a breakout failing.
        on_side = r.supertrend_up[i] is True
        return Trade(i) if on_side and r.closes[i] > r.bb_upper[i] and volume_confirms(r.volumes, i) else None

    def leave(i, t):
        return "supertrend" if r.supertrend_up[i] is False else None

    return _walk(len(r.closes), enter, leave)


def trend_short(r: Readings) -> list[Trade]:
    def enter(i):
        if i < 1 or not _defined(
            r.ema9[i - 1], r.ema21[i - 1], r.ema9[i], r.ema21[i], r.ema50[i], r.adx14[i]
        ):
            return None
        crossed = r.ema9[i - 1] >= r.ema21[i - 1] and r.ema9[i] < r.ema21[i]
        return Trade(i) if crossed and r.closes[i] < r.ema50[i] and r.adx14[i] > ADX_MIN else None

    def leave(i, t):
        return "21 EMA" if r.closes[i] > r.ema21[i] else None

    return _walk(len(r.closes), enter, leave)


def pullback_short(r: Readings) -> list[Trade]:
    lo, hi = PULLBACK_RSI_SHORT

    def enter(i):
        recent = r.rsi14[max(0, i - PULLBACK_RSI_WINDOW + 1) : i + 1]
        if i < TARGET_LOOKBACK or not _defined(r.ema9[i], r.ema21[i], r.ema50[i], *recent):
            return None
        stacked = r.ema9[i] < r.ema21[i] < r.ema50[i]
        rejected = r.highs[i] >= r.ema21[i] > r.closes[i]
        if not (stacked and rejected and lo <= max(recent) <= hi):
            return None
        target = min(r.lows[i - TARGET_LOOKBACK : i])
        return Trade(i, target=target if target < r.closes[i] else None)

    def leave(i, t):
        a = r.atr22[i]
        if a is not None and r.closes[i] > min(r.lows[t.entry : i + 1]) + CHANDELIER_MULT * a:
            return "stop"
        if t.target is not None and r.lows[i] <= t.target:
            return "target"
        return None

    return _walk(len(r.closes), enter, leave)


def reversion_short(r: Readings) -> list[Trade]:
    def enter(i):
        if not _defined(r.bb_upper[i], r.rsi14[i], r.atr14[i]):
            return None
        return Trade(i) if r.highs[i] >= r.bb_upper[i] and r.rsi14[i] > OVERBOUGHT_RSI else None

    def leave(i, t):
        if r.closes[i] > r.closes[t.entry] + REVERSION_STOP_ATR * r.atr14[t.entry]:
            return "stop"
        if r.closes[i] <= r.bb_mid[i]:
            return "target"
        return None

    return _walk(len(r.closes), enter, leave)


def breakout_short(r: Readings) -> list[Trade]:
    def enter(i):
        if not _defined(r.bb_lower[i]) or not any(r.squeeze[max(0, i - SQUEEZE_RECENT + 1) : i + 1]):
            return None
        on_side = r.supertrend_up[i] is False  # the mirror: 47 of 57 entered against it covered next close
        return Trade(i) if on_side and r.closes[i] < r.bb_lower[i] and volume_confirms(r.volumes, i) else None

    def leave(i, t):
        return "supertrend" if r.supertrend_up[i] is True else None

    return _walk(len(r.closes), enter, leave)


RUN = {
    "trend": trend,
    "pullback": pullback,
    "reversion": reversion,
    "breakout": breakout,
    "trend-short": trend_short,
    "pullback-short": pullback_short,
    "reversion-short": reversion_short,
    "breakout-short": breakout_short,
}


def lines(r: Readings) -> dict[str, list[float | None]]:
    """Every series a setup's rule reads, by the names `Setup.lines` uses."""
    return {
        "ema9": r.ema9,
        "ema21": r.ema21,
        "ema50": r.ema50,
        "bb_upper": r.bb_upper,
        "bb_mid": r.bb_mid,
        "bb_lower": r.bb_lower,
        "supertrend": r.supertrend,
    }
