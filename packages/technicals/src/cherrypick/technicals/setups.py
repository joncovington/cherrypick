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
# Round 3 of the historical study (docs/signal-log-plan.md): the Vortex in its authors' period, and
# the RSI divergence in TradingView's Divergence Indicator defaults (pivots 5 bars either side, 5 to
# 60 bars apart). Study-only: the chart does not draw these setups.
VORTEX_N = 14
DIVERGENCE_PIVOT = 5
DIVERGENCE_SPAN = (5, 60)


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

# Setups the historical study scores and the chart does not draw: round 3's two setups from published
# infographics, declared before they were run (docs/signal-log-plan.md, "Round 3"), and each with one
# of its two conditions removed, so the study can say which half does the work. A long and a short of
# one family are separate positions, as on the chart.
STUDIED = (
    Setup(
        "st-vortex",
        "Supertrend + Vortex",
        "Enter on the first close on which Supertrend(10, 3) is up and VI+(14) is above VI-, after a "
        "close on which they were not both so. Exit on the first close with Supertrend down or VI+ "
        "under VI-.",
        ("supertrend",),
    ),
    Setup(
        "st-vortex-short",
        "Supertrend + Vortex (short)",
        "Short on the first close on which Supertrend(10, 3) is down and VI+(14) is under VI-, after a "
        "close on which they were not both so. Cover on the first close with Supertrend up or VI+ over "
        "VI-.",
        ("supertrend",),
        "st-vortex",
        "short",
    ),
    Setup(
        "squeeze-div",
        "Squeeze + RSI divergence",
        "Enter on a close above the upper Bollinger band (20, 2) within 5 sessions of a squeeze, while "
        "the latest two RSI(14) pivot lows confirmed by then are a bullish divergence. Exit on the "
        "first close under the middle band.",
        ("bb_upper", "bb_mid", "bb_lower"),
    ),
    Setup(
        "squeeze-div-short",
        "Squeeze + RSI divergence (short)",
        "Short on a close under the lower Bollinger band (20, 2) within 5 sessions of a squeeze, while "
        "the latest two RSI(14) pivot highs confirmed by then are a bearish divergence. Cover on the "
        "first close over the middle band.",
        ("bb_upper", "bb_mid", "bb_lower"),
        "squeeze-div",
        "short",
    ),
    Setup("st-only", "Supertrend alone", "Enter when Supertrend turns up; exit when it turns down.", ()),
    Setup(
        "st-only-short",
        "Supertrend alone (short)",
        "Short when Supertrend turns down; cover when up.",
        (),
        "st-only",
        "short",
    ),
    Setup(
        "vortex-only", "Vortex alone", "Enter when VI+ crosses above VI-; exit on a close with VI+ under.", ()
    ),
    Setup(
        "vortex-only-short",
        "Vortex alone (short)",
        "Short when VI+ crosses under VI-; cover on VI+ over.",
        (),
        "vortex-only",
        "short",
    ),
    Setup("squeeze-band", "Squeeze, no divergence", "squeeze-div without the divergence.", ()),
    Setup(
        "squeeze-band-short",
        "Squeeze, no divergence (short)",
        "squeeze-div-short without the divergence.",
        (),
        "squeeze-band",
        "short",
    ),
    Setup("div-band", "Divergence, no squeeze", "squeeze-div without the squeeze.", ()),
    Setup(
        "div-band-short",
        "Divergence, no squeeze (short)",
        "squeeze-div-short without the squeeze.",
        (),
        "div-band",
        "short",
    ),
    # round 4: the rules read SPY as well as the name, so they live in round4.py, not in RULES
    Setup(
        "rs-break",
        "Relative-strength breakout",
        "Enter on a close above the highest close of the prior 21 sessions on which close / SPY's close "
        "is also above its highest of the prior 21, with no such close in the prior 10 sessions. Exit "
        "on the close 21 sessions after entry.",
        (),
    ),
    Setup(
        "rs-break-vol",
        "Relative-strength breakout on volume",
        "rs-break, on a session whose volume is at least 1.5x the mean of the prior 30 sessions'.",
        (),
    ),
    # round 5: the 1-10 score inside the fundamentals label; rules in round5.py
    Setup(
        "rs-fund",
        "Low score, compelling fundamentals",
        "Long on an early-leader or Bullish-trend trigger while the fundamentals are compelling and "
        "the 1-10 score is 1-3; exit on the close 21 sessions after entry.",
        (),
    ),
    Setup(
        "rs-fund-short",
        "High score, weak fundamentals (short)",
        "Short on an early-laggard or Bearish-trend trigger while the fundamentals are weak and the "
        "1-10 score is 8-10; cover on the close 21 sessions after entry.",
        (),
        "rs-fund",
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
    vi_plus: list[float | None]
    vi_minus: list[float | None]
    bear_div: list[bool]  # the latest RSI(14) pivot highs confirmed by this bar diverge from price
    bull_div: list[bool]


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
    rsi14 = indicators.rsi(closes, 14)
    vi_plus, vi_minus = indicators.vortex(highs, lows, closes, VORTEX_N)
    bear, bull = indicators.divergence(highs, lows, rsi14, DIVERGENCE_PIVOT, DIVERGENCE_SPAN)
    return Readings(
        highs,
        lows,
        closes,
        volumes,
        indicators.ema(closes, 9),
        indicators.ema(closes, 21),
        indicators.ema(closes, 50),
        indicators.adx(highs, lows, closes, 14),
        rsi14,
        indicators.atr(highs, lows, closes, 14),
        indicators.atr(highs, lows, closes, CHANDELIER_N),
        upper,
        mid,
        lower,
        _squeezes(upper, mid, lower),
        line,
        up,
        vi_plus,
        vi_minus,
        bear,
        bull,
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


# Each setup is an entry rule, an exit rule and, for the pullbacks, a target set at entry -- module
# functions over the readings, so the walk below and `exit_from` (which applies a setup's exit to an
# entry the setup did not make, for the historical study's random baseline) run the same code.


def _trend_enter(r: Readings, i: int) -> Trade | None:
    if i < 1 or not _defined(r.ema9[i - 1], r.ema21[i - 1], r.ema9[i], r.ema21[i], r.ema50[i], r.adx14[i]):
        return None
    crossed = r.ema9[i - 1] <= r.ema21[i - 1] and r.ema9[i] > r.ema21[i]
    return Trade(i) if crossed and r.closes[i] > r.ema50[i] and r.adx14[i] > ADX_MIN else None


def _trend_leave(r: Readings, i: int, t: Trade) -> str | None:
    return "21 EMA" if r.closes[i] < r.ema21[i] else None


def _pullback_target(r: Readings, i: int) -> float | None:
    target = max(r.highs[i - TARGET_LOOKBACK : i]) if i >= TARGET_LOOKBACK else None
    return target if target is not None and target > r.closes[i] else None


def _pullback_enter(r: Readings, i: int) -> Trade | None:
    lo, hi = PULLBACK_RSI
    recent = r.rsi14[max(0, i - PULLBACK_RSI_WINDOW + 1) : i + 1]
    if i < TARGET_LOOKBACK or not _defined(r.ema9[i], r.ema21[i], r.ema50[i], *recent):
        return None
    stacked = r.ema9[i] > r.ema21[i] > r.ema50[i]
    held = r.lows[i] <= r.ema21[i] < r.closes[i]
    if not (stacked and held and lo <= min(recent) <= hi):
        return None
    return Trade(i, target=_pullback_target(r, i))


def _pullback_leave(r: Readings, i: int, t: Trade) -> str | None:
    a = r.atr22[i]
    if a is not None and r.closes[i] < max(r.highs[t.entry : i + 1]) - CHANDELIER_MULT * a:
        return "stop"
    if t.target is not None and r.highs[i] >= t.target:
        return "target"
    return None


def _reversion_enter(r: Readings, i: int) -> Trade | None:
    if not _defined(r.bb_lower[i], r.rsi14[i], r.atr14[i]):
        return None
    return Trade(i) if r.lows[i] <= r.bb_lower[i] and r.rsi14[i] < OVERSOLD_RSI else None


def _reversion_leave(r: Readings, i: int, t: Trade) -> str | None:
    if r.closes[i] < r.closes[t.entry] - REVERSION_STOP_ATR * r.atr14[t.entry]:
        return "stop"
    if r.closes[i] >= r.bb_mid[i]:
        return "target"
    return None


def volume_confirms(volumes: list[float | None], i: int) -> bool:
    """Today's volume above `VOLUME_MULT` x the mean of the `VOLUME_AVG` sessions before it; False
    when any of them is missing -- an unmeasured volume never confirms."""
    if i < VOLUME_AVG:
        return False
    window = volumes[i - VOLUME_AVG : i]
    if volumes[i] is None or None in window:
        return False
    return volumes[i] > VOLUME_MULT * sum(window) / VOLUME_AVG


def _breakout_enter(r: Readings, i: int) -> Trade | None:
    if not _defined(r.bb_upper[i]) or not any(r.squeeze[max(0, i - SQUEEZE_RECENT + 1) : i + 1]):
        return None
    # Supertrend must already be up: entered against it, the exit fired the next close on 33 of
    # 45 such breakouts (2026-10-04) -- a one-day round trip, not a breakout failing.
    on_side = r.supertrend_up[i] is True
    return Trade(i) if on_side and r.closes[i] > r.bb_upper[i] and volume_confirms(r.volumes, i) else None


def _breakout_leave(r: Readings, i: int, t: Trade) -> str | None:
    return "supertrend" if r.supertrend_up[i] is False else None


def _trend_short_enter(r: Readings, i: int) -> Trade | None:
    if i < 1 or not _defined(r.ema9[i - 1], r.ema21[i - 1], r.ema9[i], r.ema21[i], r.ema50[i], r.adx14[i]):
        return None
    crossed = r.ema9[i - 1] >= r.ema21[i - 1] and r.ema9[i] < r.ema21[i]
    return Trade(i) if crossed and r.closes[i] < r.ema50[i] and r.adx14[i] > ADX_MIN else None


def _trend_short_leave(r: Readings, i: int, t: Trade) -> str | None:
    return "21 EMA" if r.closes[i] > r.ema21[i] else None


def _pullback_short_target(r: Readings, i: int) -> float | None:
    target = min(r.lows[i - TARGET_LOOKBACK : i]) if i >= TARGET_LOOKBACK else None
    return target if target is not None and target < r.closes[i] else None


def _pullback_short_enter(r: Readings, i: int) -> Trade | None:
    lo, hi = PULLBACK_RSI_SHORT
    recent = r.rsi14[max(0, i - PULLBACK_RSI_WINDOW + 1) : i + 1]
    if i < TARGET_LOOKBACK or not _defined(r.ema9[i], r.ema21[i], r.ema50[i], *recent):
        return None
    stacked = r.ema9[i] < r.ema21[i] < r.ema50[i]
    rejected = r.highs[i] >= r.ema21[i] > r.closes[i]
    if not (stacked and rejected and lo <= max(recent) <= hi):
        return None
    return Trade(i, target=_pullback_short_target(r, i))


def _pullback_short_leave(r: Readings, i: int, t: Trade) -> str | None:
    a = r.atr22[i]
    if a is not None and r.closes[i] > min(r.lows[t.entry : i + 1]) + CHANDELIER_MULT * a:
        return "stop"
    if t.target is not None and r.lows[i] <= t.target:
        return "target"
    return None


def _reversion_short_enter(r: Readings, i: int) -> Trade | None:
    if not _defined(r.bb_upper[i], r.rsi14[i], r.atr14[i]):
        return None
    return Trade(i) if r.highs[i] >= r.bb_upper[i] and r.rsi14[i] > OVERBOUGHT_RSI else None


def _reversion_short_leave(r: Readings, i: int, t: Trade) -> str | None:
    if r.closes[i] > r.closes[t.entry] + REVERSION_STOP_ATR * r.atr14[t.entry]:
        return "stop"
    if r.closes[i] <= r.bb_mid[i]:
        return "target"
    return None


def _breakout_short_enter(r: Readings, i: int) -> Trade | None:
    if not _defined(r.bb_lower[i]) or not any(r.squeeze[max(0, i - SQUEEZE_RECENT + 1) : i + 1]):
        return None
    on_side = r.supertrend_up[i] is False  # the mirror: 47 of 57 entered against it covered next close
    return Trade(i) if on_side and r.closes[i] < r.bb_lower[i] and volume_confirms(r.volumes, i) else None


def _breakout_short_leave(r: Readings, i: int, t: Trade) -> str | None:
    return "supertrend" if r.supertrend_up[i] is True else None


# Round 3 (study-only). Supertrend + Vortex holds while the two agree: it enters on the first close
# they agree after one they did not -- whichever of the two turned second is the trigger, the usual
# reading of "Supertrend turns green and +VI crosses above -VI" -- and leaves on the first close
# either disagrees. The squeeze + divergence setup leaves at the middle band, which for a position
# entered outside the band is a stop that trails it.


def agree(r: Readings, i: int, up: bool) -> bool:
    """Supertrend and the Vortex both on the `up` side at bar `i` (False while either is undefined)."""
    s, p, m = r.supertrend_up[i], r.vi_plus[i], r.vi_minus[i]
    if s is None or p is None or m is None:
        return False
    return (s is True and p > m) if up else (s is False and p < m)


def _observed(r: Readings, i: int) -> bool:
    return r.supertrend_up[i] is not None and r.vi_plus[i] is not None and r.vi_minus[i] is not None


def _st_vortex_enter(r: Readings, i: int, up: bool) -> Trade | None:
    # The bar before must be readable: a first defined reading is not a turn.
    if i < 1 or not _observed(r, i - 1):
        return None
    return Trade(i) if agree(r, i, up) and not agree(r, i - 1, up) else None


def _st_vortex_leave(r: Readings, i: int, t: Trade, up: bool) -> str | None:
    if r.supertrend_up[i] is (not up):
        return "supertrend"
    p, m = r.vi_plus[i], r.vi_minus[i]
    if p is not None and m is not None and (p < m if up else p > m):
        return "vortex"
    return None


def _st_only_enter(r: Readings, i: int, up: bool) -> Trade | None:
    if i < 1:
        return None
    return Trade(i) if r.supertrend_up[i] is up and r.supertrend_up[i - 1] is (not up) else None


def _st_only_leave(r: Readings, i: int, t: Trade, up: bool) -> str | None:
    return "supertrend" if r.supertrend_up[i] is (not up) else None


def _vortex_only_enter(r: Readings, i: int, up: bool) -> Trade | None:
    if i < 1 or not _defined(r.vi_plus[i - 1], r.vi_minus[i - 1], r.vi_plus[i], r.vi_minus[i]):
        return None
    p0, m0, p, m = r.vi_plus[i - 1], r.vi_minus[i - 1], r.vi_plus[i], r.vi_minus[i]
    crossed = (p0 <= m0 and p > m) if up else (p0 >= m0 and p < m)
    return Trade(i) if crossed else None


def _vortex_only_leave(r: Readings, i: int, t: Trade, up: bool) -> str | None:
    p, m = r.vi_plus[i], r.vi_minus[i]
    return "vortex" if p is not None and m is not None and (p < m if up else p > m) else None


def _band_enter(r: Readings, i: int, up: bool, squeeze: bool = True, divergence: bool = True) -> Trade | None:
    band = r.bb_upper[i] if up else r.bb_lower[i]
    if band is None or not (r.closes[i] > band if up else r.closes[i] < band):
        return None
    if squeeze and not any(r.squeeze[max(0, i - SQUEEZE_RECENT + 1) : i + 1]):
        return None
    if divergence and not (r.bull_div[i] if up else r.bear_div[i]):
        return None
    return Trade(i)


def _band_leave(r: Readings, i: int, t: Trade, up: bool) -> str | None:
    return "middle band" if (r.closes[i] < r.bb_mid[i] if up else r.closes[i] > r.bb_mid[i]) else None


def _sided(enter, leave, up: bool, **kw) -> tuple[Callable, Callable, None]:
    return (
        lambda r, i: enter(r, i, up, **kw),
        lambda r, i, t: leave(r, i, t, up),
        None,
    )


# setup id -> (entry rule, exit rule, the target an entry sets, if any)
RULES: dict[str, tuple[Callable, Callable, Callable | None]] = {
    "trend": (_trend_enter, _trend_leave, None),
    "pullback": (_pullback_enter, _pullback_leave, _pullback_target),
    "reversion": (_reversion_enter, _reversion_leave, None),
    "breakout": (_breakout_enter, _breakout_leave, None),
    "trend-short": (_trend_short_enter, _trend_short_leave, None),
    "pullback-short": (_pullback_short_enter, _pullback_short_leave, _pullback_short_target),
    "reversion-short": (_reversion_short_enter, _reversion_short_leave, None),
    "breakout-short": (_breakout_short_enter, _breakout_short_leave, None),
    # round 3, study-only (`STUDIED`)
    "st-vortex": _sided(_st_vortex_enter, _st_vortex_leave, True),
    "st-vortex-short": _sided(_st_vortex_enter, _st_vortex_leave, False),
    "squeeze-div": _sided(_band_enter, _band_leave, True),
    "squeeze-div-short": _sided(_band_enter, _band_leave, False),
    "st-only": _sided(_st_only_enter, _st_only_leave, True),
    "st-only-short": _sided(_st_only_enter, _st_only_leave, False),
    "vortex-only": _sided(_vortex_only_enter, _vortex_only_leave, True),
    "vortex-only-short": _sided(_vortex_only_enter, _vortex_only_leave, False),
    "squeeze-band": _sided(_band_enter, _band_leave, True, divergence=False),
    "squeeze-band-short": _sided(_band_enter, _band_leave, False, divergence=False),
    "div-band": _sided(_band_enter, _band_leave, True, squeeze=False),
    "div-band-short": _sided(_band_enter, _band_leave, False, squeeze=False),
}


def run(
    setup_id: str,
    r: Readings,
    allow: Callable[[int], bool] | None = None,
    leave: Callable[[Readings, int, Trade], str | None] | None = None,
) -> list[Trade]:
    """The setup's positions. `allow`, if given, must also hold for an entry (a filter applied at
    entry, so a filtered-out signal never blocks a later one); `leave`, if given, replaces the
    setup's own exit rule. With neither, exactly the setup as declared."""
    enter, own_leave, _ = RULES[setup_id]
    out = leave or own_leave
    return _walk(
        len(r.closes),
        (lambda i: enter(r, i) if allow(i) else None) if allow else (lambda i: enter(r, i)),
        lambda i, t: out(r, i, t),
    )


def exit_from(
    setup_id: str, r: Readings, i: int, leave: Callable[[Readings, int, Trade], str | None] | None = None
) -> Trade:
    """A position opened at bar `i` and held to the setup's own exit rule (or `leave`, replacing it),
    whether or not the setup would have entered there -- the random baseline's trade. The entry
    carries the target the setup would set (the pullbacks'), and the exit is checked from the next
    bar on, as the walk does."""
    _, own_leave, target = RULES[setup_id]
    leave = leave or own_leave
    t = Trade(i, target=target(r, i) if target else None)
    for j in range(i + 1, len(r.closes)):
        reason = leave(r, j, t)
        if reason is not None:
            return replace(t, exit=j, reason=reason)
    return t


def trend(r: Readings) -> list[Trade]:
    return run("trend", r)


def pullback(r: Readings) -> list[Trade]:
    return run("pullback", r)


def reversion(r: Readings) -> list[Trade]:
    return run("reversion", r)


def breakout(r: Readings) -> list[Trade]:
    return run("breakout", r)


def trend_short(r: Readings) -> list[Trade]:
    return run("trend-short", r)


def pullback_short(r: Readings) -> list[Trade]:
    return run("pullback-short", r)


def reversion_short(r: Readings) -> list[Trade]:
    return run("reversion-short", r)


def breakout_short(r: Readings) -> list[Trade]:
    return run("breakout-short", r)


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
