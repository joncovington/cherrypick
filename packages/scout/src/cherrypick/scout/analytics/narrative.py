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


# --------------------------------------------------------------------- secondary detectors
def cci(bars: list[dict], period: int = 20) -> float | None:
    """Commodity Channel Index -- the indicator behind the reference platform's "CCI Trend" scan
    chip. Standard formulation: (typical price - its SMA) / (0.015 * mean absolute deviation)."""
    if len(bars) < period:
        return None
    typical = [(b["h"] + b["l"] + b["c"]) / 3 for b in bars[-period:]]
    mean = sum(typical) / period
    deviation = sum(abs(t - mean) for t in typical) / period
    if deviation == 0:
        return 0.0
    return (typical[-1] - mean) / (0.015 * deviation)


def _golden_death_cross_today(closes: list[float]) -> str | None:
    """The 50-day SMA crossing the 200-day SMA between yesterday and today."""
    if len(closes) < 201:
        return None

    def _sma(series, period):
        return sum(series[-period:]) / period

    f_now, s_now = _sma(closes, 50), _sma(closes, 200)
    f_prev, s_prev = _sma(closes[:-1], 50), _sma(closes[:-1], 200)
    if f_prev <= s_prev and f_now > s_now:
        return "golden"
    if f_prev >= s_prev and f_now < s_now:
        return "death"
    return None


def _week52(closes: list[float]) -> str | None:
    if len(closes) < 60:
        return None
    high, low, last = max(closes), min(closes), closes[-1]
    if last >= high:
        return "made a new 52-week closing high today"
    if last <= low:
        return "made a new 52-week closing low today"
    if last >= 0.98 * high:
        return f"is within {((high - last) / high) * 100:.1f}% of its 52-week high"
    if last <= 1.02 * low:
        return f"is within {((last - low) / low) * 100:.1f}% of its 52-week low"
    return None


def _streak(closes: list[float]) -> str | None:
    if len(closes) < 7:
        return None
    up = down = 0
    for prev, cur in zip(reversed(closes[:-1]), reversed(closes), strict=False):
        if cur > prev and down == 0:
            up += 1
        elif cur < prev and up == 0:
            down += 1
        else:
            break
    if up >= 5:
        return f"has closed higher {up} sessions in a row"
    if down >= 5:
        return f"has closed lower {down} sessions in a row"
    return None


def _squeeze(bars: list[dict], window: int = 20, lookback: int = 126) -> bool:
    """True when the current `window`-day high-low range is the narrowest such range in `lookback`
    bars -- a coiling/compression read without needing full Bollinger/Keltner machinery."""
    if len(bars) < lookback:
        return False
    ranges = []
    for end in range(window, len(bars) + 1):
        chunk = bars[end - window : end]
        ranges.append(max(b["h"] for b in chunk) - min(b["l"] for b in chunk))
    return ranges[-1] <= min(ranges[-(lookback - window + 1) :])


def _extension(closes: list[float]) -> str | None:
    if len(closes) < 50:
        return None
    sma50 = sum(closes[-50:]) / 50
    pct = (closes[-1] - sma50) / sma50
    if pct >= 0.12:
        return f"is stretched {pct * 100:.0f}% above its 50-day moving average"
    if pct <= -0.12:
        return f"is stretched {abs(pct) * 100:.0f}% below its 50-day moving average"
    return None


def technical_bullet(name: str, bars: list[dict]) -> str | None:
    """The strongest secondary technical observation, or None. Priority mirrors specificity: a
    50/200 cross is rarer and stronger than a 52-week note, which beats a streak, etc."""
    closes = [b["c"] for b in bars]
    cross = _golden_death_cross_today(closes)
    if cross == "golden":
        return f"{name}'s 50-day moving average crossed above its 200-day today (a golden cross)."
    if cross == "death":
        return f"{name}'s 50-day moving average crossed below its 200-day today (a death cross)."
    week52 = _week52(closes)
    if week52:
        return f"{name} {week52}."
    streak = _streak(closes)
    if streak:
        return f"{name} {streak}."
    if _squeeze(bars):
        return f"{name} is coiling in its tightest 20-day range of the past six months."
    ext = _extension(closes)
    if ext:
        return f"{name} {ext}."
    return None


def options_bullet(name: str, info: dict | None, skew_edge: float | None = None) -> str | None:
    """The strongest options/market-context observation from the metrics fields -- the layer a
    price-only narrative lacks, and the one an options tool should lead with."""
    info = info or {}
    iv, hv = info.get("iv_30d"), info.get("hv_30d")
    if iv and hv:
        ratio = iv / hv
        if ratio >= 1.25:
            return (
                f"{name}'s options trade at {ratio:.1f}x realized volatility "
                f"(IV {iv * 100:.0f}% vs realized {hv * 100:.0f}%) -- premium is rich."
            )
        if ratio <= 0.8:
            return (
                f"{name}'s options trade at {ratio:.1f}x realized volatility "
                f"(IV {iv * 100:.0f}% vs realized {hv * 100:.0f}%) -- premium is cheap."
            )
    iv_rank = info.get("iv_rank")
    try:
        iv_rank = float(iv_rank) if iv_rank is not None else None
    except (TypeError, ValueError):
        iv_rank = None
    if iv_rank is not None:
        if iv_rank >= 0.70:
            return f"{name}'s IV rank is {iv_rank * 100:.0f}/100 -- near its richest of the year."
        if iv_rank <= 0.20:
            return f"{name}'s IV rank is {iv_rank * 100:.0f}/100 -- options are cheap by its own history."
    if skew_edge is not None and abs(skew_edge) > 0:
        lean = "calls pricing richer than puts" if skew_edge > 0 else "puts pricing richer than calls"
        return f"{name}'s option chain shows {lean} at matched distances from spot."
    return None


def relative_strength_bullet(
    name: str, closes: list[float], benchmark_closes: list[float] | None
) -> str | None:
    """True relative performance vs a benchmark (SPX) over ~3 months -- unlike the reference
    platform's "Relative Strength" score, which its own docs describe as a trend composite."""
    if not benchmark_closes or len(closes) < 64 or len(benchmark_closes) < 64:
        return None
    sym_ret = closes[-1] / closes[-64] - 1
    bench_ret = benchmark_closes[-1] / benchmark_closes[-64] - 1
    diff = sym_ret - bench_ret
    if abs(diff) < 0.08:
        return None
    word = "outperformed" if diff > 0 else "underperformed"
    return f"{name} has {word} the S&P 500 by {abs(diff) * 100:.0f}% over the past three months."


def event_warnings(
    expiration: date, earnings: dict | None, info: dict | None, *, today: date | None = None
) -> list[str]:
    """Builder-facing warnings: events landing inside a chosen expiration that change a ticket's
    risk character. Returns [] when nothing applies -- absence of a warning is a real claim, so
    unparseable dates contribute nothing rather than a guessed warning."""
    today = today or datetime.now(tz=UTC).date()
    warnings: list[str] = []
    raw = (earnings or {}).get("expected_report_date")
    if raw:
        try:
            report = date.fromisoformat(str(raw))
            if today <= report <= expiration:
                warnings.append(
                    f"An earnings report ({report.isoformat()}) lands inside this expiration -- "
                    "gap and IV-crush risk apply."
                )
        except ValueError:
            pass
    for field in ("dividend_ex_date", "dividend_next_date"):
        raw = (info or {}).get(field)
        if not raw:
            continue
        try:
            ex_date = date.fromisoformat(str(raw))
        except ValueError:
            continue
        if today <= ex_date <= expiration:
            rate = (info or {}).get("dividend_rate_per_share")
            amount = f" (${float(rate):.2f}/share)" if rate else ""
            warnings.append(
                f"Goes ex-dividend {ex_date.isoformat()}{amount} before this expiration -- "
                "short in-the-money calls carry early-assignment risk."
            )
            break
    return warnings


def scan_headline(
    name: str, trend_1m: str | None, trend_6m: str | None, bars: list[dict] | None = None
) -> dict | None:
    """The scan classification: a CCI dip/rally within an established trend (the more specific
    setup, checked first) or a longer-term trend with a short-term counter-move (trend following).
    Counter-trend reversal scans remain a follow-up -- absent a match this returns None and the UI
    just omits the headline."""
    side_6m = _SIDE.get(trend_6m) if trend_6m else None
    side_1m = _SIDE.get(trend_1m) if trend_1m else None
    cci_now = cci(bars) if bars else None
    if cci_now is not None and side_6m == "bullish" and cci_now <= -100:
        return {
            "scan": "CCI Dip in Bullish Trend",
            "text": (
                f"{name} is in a bullish trend and recently experienced a short-term pullback "
                f"(CCI {cci_now:.0f}), which may provide a buying opportunity."
            ),
        }
    if cci_now is not None and side_6m == "bearish" and cci_now >= 100:
        return {
            "scan": "CCI Rally in Bearish Trend",
            "text": (
                f"{name} is in a bearish trend and recently experienced a short-term rally "
                f"(CCI {cci_now:.0f}), which may provide a selling opportunity."
            ),
        }
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
