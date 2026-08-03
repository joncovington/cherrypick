"""Plain-language per-symbol analysis, generated from data scout already computes -- the
two-paragraph shape a commercial research platform shows under each chart (a scan-classification
headline plus a "Price Action" observation), emulated from our own candles/levels/trend/metrics
rather than scraped.

Same posture as the rest of ``analytics/``: pure functions, stdlib only, no I/O. Every sentence is
generated from a *detected condition* with the numbers inline, so a claim is checkable against the
chart it sits under; nothing here free-writes text. The trend wording rides scout's own provisional
``price_ma_count`` classifier (see ``trend.py`` -- fitted on 25 labeled rows, pending re-validation),
which is honest as "our trend read", not as a reproduction of anyone else's.

Price Action picks ONE observation by priority (the reference platform does the same -- one concrete
recent event beats a laundry list): 200-day MA cross today > 50-day MA cross today > gap on high
volume > level break > large 3-session move > bounce off a nearby level > trend + S/R fallback.
An earnings-timing suffix is appended when metrics says the report is today/tomorrow.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from . import trend as _trend

_BIG_MOVE_PCT = 0.05
_GAP_VOLUME_RATIO = 1.5
_BOUNCE_PROXIMITY = 0.01

_SIDE = {
    _trend.BULLISH: "bullish",
    _trend.MILDLY_BULLISH: "bullish",
    _trend.NEUTRAL: "neutral",
    _trend.MILDLY_BEARISH: "bearish",
    _trend.BEARISH: "bearish",
}


def _sma_cross_today(closes: list[float], period: int) -> tuple[str, float] | None:
    """("above"|"below", sma_value) when yesterday->today moved price across the SMA, else None."""
    if len(closes) < period + 1:
        return None
    today_sma = sum(closes[-period:]) / period
    prev_sma = sum(closes[-period - 1 : -1]) / period
    prev_diff = closes[-2] - prev_sma
    today_diff = closes[-1] - today_sma
    if prev_diff <= 0 < today_diff:
        return ("above", today_sma)
    if prev_diff >= 0 > today_diff:
        return ("below", today_sma)
    return None


def _gap_on_volume(bars: list[dict]) -> str | None:
    if len(bars) < 31:
        return None
    today, prev = bars[-1], bars[-2]
    volumes = [b["v"] for b in bars[-31:-1] if b.get("v")]
    if not volumes or not today.get("v"):
        return None
    high_volume = today["v"] >= _GAP_VOLUME_RATIO * (sum(volumes) / len(volumes))
    if not high_volume:
        return None
    if today["l"] > prev["h"]:
        return "up"
    if today["h"] < prev["l"]:
        return "down"
    return None


def _level_break(bars: list[dict], levels: list[dict]) -> tuple[str, float] | None:
    """A close crossing a clustered S/R level between yesterday and today."""
    if len(bars) < 2:
        return None
    prev_close, close = bars[-2]["c"], bars[-1]["c"]
    for level in levels:
        price = level["price"]
        if prev_close < price <= close:
            return ("above", price)
        if prev_close > price >= close:
            return ("below", price)
    return None


def _big_move(closes: list[float]) -> float | None:
    if len(closes) < 4:
        return None
    pct = (closes[-1] - closes[-4]) / closes[-4]
    return pct if abs(pct) >= _BIG_MOVE_PCT else None


def _bounce(bars: list[dict], levels: list[dict]) -> dict | None:
    if not bars:
        return None
    close = bars[-1]["c"]
    near = [lv for lv in levels if abs(lv["price"] - close) / close <= _BOUNCE_PROXIMITY]
    return near[0] if near else None


def _earnings_suffix(earnings: dict | None, today: date) -> str:
    if not earnings:
        return ""
    raw = earnings.get("expected_report_date")
    if not raw:
        return ""
    try:
        report = date.fromisoformat(str(raw))
    except ValueError:
        return ""
    timing = str(earnings.get("time_of_day") or "")
    when = {"BTO": " before the open", "AMC": " after the close"}.get(timing, "")
    days = (report - today).days
    if days == 0:
        return f" and reports earnings today{when}"
    if days == 1:
        return f" and reports earnings tomorrow{when}"
    return ""


def price_action(
    name: str,
    bars: list[dict],
    levels: list[dict],
    trend_6m: str | None,
    earnings: dict | None = None,
    *,
    today: date | None = None,
) -> str:
    """One concrete, checkable observation about recent price behavior, priority-ordered."""
    today = today or datetime.now(tz=UTC).date()
    closes = [b["c"] for b in bars]
    suffix = _earnings_suffix(earnings, today)
    last = closes[-1] if closes else None
    supports = [lv for lv in levels if lv["kind"] == "support" and lv["price"] < last] if closes else []
    resistances = (
        [lv for lv in levels if lv["kind"] == "resistance" and lv["price"] > last] if closes else []
    )

    for period in (200, 50):
        cross = _sma_cross_today(closes, period)
        if cross:
            direction, value = cross
            return f"{name} crossed {direction} its {period}-day moving average at {value:.2f} today{suffix}."

    gap = _gap_on_volume(bars)
    if gap:
        day = datetime.fromtimestamp(bars[-1]["t"], tz=UTC).date().isoformat()
        return f"{name} gapped {gap} on high volume on {day}{suffix}."

    brk = _level_break(bars, levels)
    if brk:
        direction, price = brk
        if direction == "above":
            role = "resistance, which now becomes support"
        else:
            role = "support, which now becomes resistance"
        return f"{name} broke {direction} its {price:.2f} {role}{suffix}."

    move = _big_move(closes)
    if move is not None:
        word = "higher" if move > 0 else "lower"
        return f"{name} moved {abs(move) * 100:.2f}% {word} over the last 3 sessions{suffix}."

    bounced = _bounce(bars, levels)
    if bounced:
        return f"{name} is trading at its {bounced['price']:.2f} {bounced['kind']} level{suffix}."

    side = _SIDE.get(trend_6m, "neutral") if trend_6m else "neutral"
    parts = [f"{name} is in a {side} trend"]
    if supports:
        parts.append(f"with support at {max(s['price'] for s in supports):.2f}")
    if resistances:
        joiner = "and resistance" if supports else "with resistance"
        parts.append(f"{joiner} at {min(r['price'] for r in resistances):.2f}")
    return " ".join(parts) + suffix + "."


def scan_headline(name: str, trend_1m: str | None, trend_6m: str | None) -> dict | None:
    """The trend-following scan classification, v1: a longer-term trend with a short-term
    counter-move (the setup both trend-following scan variants describe). CCI and counter-trend
    scan types are follow-ups -- absent, this returns None and the UI just omits the headline."""
    side_6m = _SIDE.get(trend_6m) if trend_6m else None
    side_1m = _SIDE.get(trend_1m) if trend_1m else None
    if side_6m == "bullish" and side_1m in ("bearish", "neutral"):
        return {
            "scan": "Bullish Trend Following",
            "text": (
                f"{name} has recently pulled back within a longer-term bullish trend, "
                "which may offer a favorable risk/reward for a bullish trade."
            ),
        }
    if side_6m == "bearish" and side_1m in ("bullish", "neutral"):
        return {
            "scan": "Bearish Trend Following",
            "text": (
                f"{name} has recently rallied within a longer-term bearish trend, "
                "which may offer a favorable risk/reward for a bearish trade."
            ),
        }
    return None
