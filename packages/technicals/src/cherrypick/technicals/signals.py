"""The vendor's six scan rules, as declared rules over our trend scores, CCI and RSI.

The vendor's scan list (`trade-ideas.json`, saved with every chart capture) flags ~250 names a day,
each with its rule. Refitted 2026-10-14 and landed 2026-10-27, scored on all 17 saved lists
(sessions 2026-09-25 to 10-23), the five through 2026-10-02 fitting and the twelve after unseen:

| Rule                    | Ours                                                                       |
|-------------------------|----------------------------------------------------------------------------|
| BullishTrendFollowing   | CCI(50) > 0, close below the 26 EMA, RSI(14) <= 50.5 (a pullback)            |
| BearishTrendFollowing   | CCI(50) < 0, close above the 26 EMA, RSI(14) >= 50.5                        |
| BullishCounterTrend     | the mean of RSI(10) and RSI(40) <= 35, options-liquid names only            |
| BearishCounterTrend     | the mean of RSI(10) and RSI(40) >= 70, options-liquid names only            |
| CciRallyInBearishTrend  | short trend <= -2, sentiment Bearish, CCI(5) crosses down through +100      |
| CciDipInBullishTrend    | short trend >= 3, CCI(5) < -100 yesterday and back above today (unchanged)  |

| Rules                       | Fit recall     | Fit precision | Later recall     | Later precision |
|-----------------------------|----------------|---------------|------------------|-----------------|
| these (`CURRENT`)           | 95.0% (880/926) | 79.4%        | 92.5% (2415/2611) | 81.3%          |
| before (`FIT_2026_09_29`)   | 87.5% (810/926) | 65.3%        | 86.7% (2263/2611) | 64.9%          |

Recall is the vendor's flagged names we flag with the same rule, among the names the store holds;
precision is how many of our flags the vendor made. Of the readings the 2026-10-14 notes left open,
the fitting lists chose: "back across the 26 EMA" as the close on the far side of it (a close that
crossed it that day caught 78 of 212 bullish flags, against 200), and the rally's bearish sentiment
together with the short trend (sentiment alone, the notes' choice made after seeing the later
lists, made more false rallies than the rule before it on both halves; the pair made 6 on the
fitting lists where the old rule made 26).

**Options-liquid** stands in for the vendor's own liquidity rank, which the counter-trend lists
almost never go below (its rank 3, "not liquid"): the vendor's rank is gone with the
subscription, so the proxy is ours -- a median of at least `LIQUID_CONTRACTS` option contracts a
day on the public security list (`securities.py`). A name the list does not rate is treated as
liquid. On the fitting lists 200 balanced recall against precision best of 100/200/300/500 and
our rank 1-2 (which also needs $100M of stock a day and cut recall hardest); applied to the
trend-following rules too, it cost more recall than it bought, so it is not.

**The rules before** (`FIT_2026_09_29`, refitted 2026-09-29 on two lists) read the trend scores for
every rule: counter-trend was both trends strong with RSI(14) stretched, trend following a short
trend in a band with RSI(14) 40..50 (bullish) or CCI(14) > 50 (bearish). Kept so they can be
re-scored (`score-signals --rules fit-2026-09-29`).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators, trend

RULES = (
    "BearishCounterTrend",
    "CciRallyInBearishTrend",
    "CciDipInBullishTrend",
    "BullishCounterTrend",
    "BullishTrendFollowing",
    "BearishTrendFollowing",
)
COUNTER_TREND = ("BullishCounterTrend", "BearishCounterTrend")

CURRENT = "fit-2026-10-27"
FIT_2026_09_29 = "fit-2026-09-29"
RULESETS = (CURRENT, FIT_2026_09_29)

# The counter-trend rules' liquidity bar: median option contracts a day on the security list.
LIQUID_CONTRACTS = 200


@dataclass(frozen=True)
class Readings:
    short: int | None
    long: int | None
    cci5: float | None
    cci5_prev: float | None
    cci14: float | None
    rsi14: float | None
    cci50: float | None
    rsi10: float | None
    rsi40: float | None
    close: float | None
    ema26: float | None
    sentiment: str | None


def _sentiments(closes: list[float]) -> list[str | None]:
    s, w = indicators.sma(closes, trend.SENTIMENT_SMA), indicators.wma(closes, trend.SENTIMENT_WMA)
    return [
        None if a is None or b is None else ("Bearish", "Neutral", "Bullish")[(c > a) + (c > b)]
        for c, a, b in zip(closes, s, w, strict=True)
    ]


def readings_series(highs: list[float], lows: list[float], closes: list[float]) -> list[Readings | None]:
    """Every session's inputs from whole-history series computed once; None where any is missing."""
    short = trend.scores(closes, trend.SHORT_TERM)
    long_ = trend.scores(closes, trend.LONG_TERM)
    cci5 = indicators.cci(highs, lows, closes, 5)
    cci14 = indicators.cci(highs, lows, closes, 14)
    cci50 = indicators.cci(highs, lows, closes, 50)
    rsi14, rsi10, rsi40 = (indicators.rsi(closes, n) for n in (14, 10, 40))
    ema26 = indicators.ema(closes, 26)
    sent = _sentiments(closes)
    out: list[Readings | None] = [None] * min(1, len(closes))
    for i in range(1, len(closes)):
        r = Readings(
            short[i], long_[i], cci5[i], cci5[i - 1], cci14[i], rsi14[i],
            cci50[i], rsi10[i], rsi40[i], closes[i], ema26[i], sent[i],
        )  # fmt: skip
        out.append(None if any(v is None for v in vars(r).values()) else r)
    return out


def readings(highs: list[float], lows: list[float], closes: list[float]) -> Readings | None:
    """The last session's inputs; None when there is not enough history for every one of them."""
    if len(closes) < 2:
        return None
    cci5 = indicators.cci(highs, lows, closes, 5)
    r = Readings(
        trend.last_score(closes, trend.SHORT_TERM),
        trend.last_score(closes, trend.LONG_TERM),
        cci5[-1],
        cci5[-2],
        indicators.cci(highs, lows, closes, 14)[-1],
        indicators.rsi(closes, 14)[-1],
        indicators.cci(highs, lows, closes, 50)[-1],
        indicators.rsi(closes, 10)[-1],
        indicators.rsi(closes, 40)[-1],
        closes[-1],
        indicators.ema(closes, 26)[-1],
        trend.sentiment(closes),
    )
    return None if any(v is None for v in vars(r).values()) else r


def _matches_2026_09_29(r: Readings) -> list[str]:
    out = []
    if r.short >= 3 and r.long >= 3 and r.rsi14 >= 72:
        out.append("BearishCounterTrend")
    if r.short <= -2 and r.cci5_prev > 100 and r.cci5 < r.cci5_prev:
        out.append("CciRallyInBearishTrend")
    if r.short >= 3 and r.cci5_prev < -100 and r.cci5 > -100:
        out.append("CciDipInBullishTrend")
    if r.short <= -3 and r.long <= 0 and r.rsi14 <= 32:
        out.append("BullishCounterTrend")
    if r.long >= -1 and 0 <= r.short <= 2 and 40 <= r.rsi14 <= 50:
        out.append("BullishTrendFollowing")
    if -1 <= r.short <= 1 and r.cci14 > 50:
        out.append("BearishTrendFollowing")
    return out


def matches(r: Readings, liquid: bool = True, rules: str = CURRENT) -> list[str]:
    """Every rule the readings satisfy, in `RULES` order (a name can match more than one). `liquid`
    False (the name's options trade under `LIQUID_CONTRACTS` a day) drops the counter-trend rules;
    the rules of 2026-09-29 ignore it."""
    if rules == FIT_2026_09_29:
        return _matches_2026_09_29(r)
    if rules != CURRENT:
        raise ValueError(f"unknown scan rules {rules!r}")
    mean_rsi = (r.rsi10 + r.rsi40) / 2.0
    out = []
    if liquid and mean_rsi >= 70:
        out.append("BearishCounterTrend")
    if r.short <= -2 and r.sentiment == "Bearish" and r.cci5_prev > 100 and r.cci5 <= 100:
        out.append("CciRallyInBearishTrend")
    if r.short >= 3 and r.cci5_prev < -100 and r.cci5 > -100:
        out.append("CciDipInBullishTrend")
    if liquid and mean_rsi <= 35:
        out.append("BullishCounterTrend")
    if r.cci50 > 0 and r.close < r.ema26 and r.rsi14 <= 50.5:
        out.append("BullishTrendFollowing")
    if r.cci50 < 0 and r.close > r.ema26 and r.rsi14 >= 50.5:
        out.append("BearishTrendFollowing")
    return out


def option_contracts(rows: list[dict]) -> dict[str, float]:
    """{symbol in the store's spelling: median option contracts a day} from a security list. The
    vendor's list carries no contract count, so it gives an empty map (every name liquid)."""
    return {
        str(r["symbol"]).replace("/PR/", "$").replace("/", "."): float(r["option_contracts"] or 0.0)
        for r in rows
        if r.get("symbol") and "option_contracts" in r
    }


def is_liquid(symbol: str, contracts: dict[str, float] | None) -> bool:
    """Whether the counter-trend rules may list `symbol`: true when no list is given or the list does
    not rate the name, never a guess that hides it."""
    if not contracts or symbol not in contracts:
        return True
    return contracts[symbol] >= LIQUID_CONTRACTS


def liquid_contracts(day: str | None = None) -> dict[str, float]:
    """The current security list's option contracts (`securities.latest`); empty when none is built."""
    from . import securities

    rows, _pick = securities.latest(day)
    return option_contracts(rows)
