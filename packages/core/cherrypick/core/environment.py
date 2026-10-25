"""cherrypick.core.environment — the suite's one market-environment label (B8).

`docs/trading-book-review-plan.md` B8. Every study and report says "uptrend", "sideways" or
"downtrend" by this function, so "it fails in downtrends" means the same thing everywhere. Before it,
each study defined its own regime (SPY against its 200-day in one, VIX/VIX3M in curve and contango).

**The rules, declared once (the book's three definitions, made exclusive by an order):**

1. `downtrend`: the close is at least `DOWN_FROM_PEAK` (20%) below its highest close of the last
   `PEAK_SESSIONS` (252).
2. `uptrend`: the close is above its `SMA` (20)-session average, and that average is rising (above
   its value `SLOPE_SESSIONS` (5) sessions earlier).
3. `sideways`: the close is within `SIDEWAYS_BAND` (10%) of its close `SIDEWAYS_SESSIONS` (126,
   about six months) earlier.
4. `mixed`: none of the three. The book's definitions do not cover every session, and forcing the
   rest into one of them would blur all three.

A session is labelled from the closes BEFORE it, so a trade entered that morning carries a label it
could have known. Too little history is `None`, never a guess. The parameters are fixed: a filter on
the label is a new hypothesis, tested under the usual rules, not a tuning of these numbers.

Pure functions over (date, close) pairs; nothing here reads a store.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

DOWN_FROM_PEAK = 0.20
PEAK_SESSIONS = 252
SMA = 20
SLOPE_SESSIONS = 5
SIDEWAYS_BAND = 0.10
SIDEWAYS_SESSIONS = 126
LABELS = ("uptrend", "sideways", "downtrend", "mixed")


def classify(closes: Sequence[float]) -> str | None:
    """The label for the session AFTER the last of `closes` (oldest first)."""
    n = len(closes)
    if n < max(SMA + SLOPE_SESSIONS, SIDEWAYS_SESSIONS + 1):
        return None
    last = closes[-1]
    peak = max(closes[-PEAK_SESSIONS:])
    if peak > 0 and last <= peak * (1 - DOWN_FROM_PEAK):
        return "downtrend"
    sma_now = sum(closes[-SMA:]) / SMA
    sma_then = sum(closes[-SMA - SLOPE_SESSIONS : -SLOPE_SESSIONS]) / SMA
    if last > sma_now and sma_now > sma_then:
        return "uptrend"
    base = closes[-1 - SIDEWAYS_SESSIONS]
    if base > 0 and abs(last / base - 1) < SIDEWAYS_BAND:
        return "sideways"
    return "mixed"


def labels(bars: Iterable[tuple[str, float]]) -> dict[str, str | None]:
    """{session: label} for every session in `bars` ((date, close), oldest first), each from the
    closes before it."""
    dates, closes = [], []
    for d, c in bars:
        dates.append(d)
        closes.append(float(c))
    return {d: classify(closes[:i]) for i, d in enumerate(dates)}


def label_on(bars: Sequence[tuple[str, float]], session: str) -> str | None:
    """The label for `session` from the closes strictly before it (the session need not be in `bars`)."""
    return classify([float(c) for d, c in bars if d < session])


def counts(session_labels: dict[str, str | None], trade_sessions: Iterable[str] = ()) -> dict:
    """How many sessions and trades each environment holds, so a thin cell reads as thin."""
    sessions = Counter(v for v in session_labels.values() if v)
    trades = Counter(session_labels.get(s) for s in trade_sessions)
    return {lab: {"sessions": sessions.get(lab, 0), "trades": trades.get(lab, 0)} for lab in LABELS} | {
        "unlabelled": {
            "sessions": sum(1 for v in session_labels.values() if not v),
            "trades": trades.get(None, 0),
        }
    }
