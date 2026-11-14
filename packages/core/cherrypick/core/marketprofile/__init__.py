"""cherrypick.core.marketprofile — one session's Market Profile (TPO), derived and never recorded.

A profile organises a session's prices by time. The regular session is cut into 30-minute periods
lettered A, B, C ... (09:30-16:00 ET is A to M); prices are cut into rows of a fixed size; each
period prints its letter in every row its bars traded through. One letter in one row is a TPO. The
row with the most letters is the point of control (POC); the rows around it that hold 70% of the
letters are the value area (VAL..VAH); the range of periods A and B is the initial balance (IB).

**Derived on read, stored nowhere** (`core.regime`'s rule): the bars are the record, so a definition
changed here re-cuts every session at once. Pure: bars in, a `Profile` out, no clock, no I/O.

**Any symbol, one session clock.** SPX, NDX, SPY, QQQ, /ES and /NQ are all profiled over the same
09:30-16:00 ET regular session; for a future, bars outside it (the overnight) are ignored, so its
profile is the day session's. An index carries no volume (SPX's candles say 0): its profile is
TPO-only and `volume_poc` is None, never a zero-volume row.

The rules, fixed once and stated so they cannot drift:

* **Bars** are `{t, o, h, l, c, v}` with `t` the bar's START in epoch seconds -- the console's
  `CandleBar`, so one fixture serves both implementations. `bar_seconds` must divide the period and
  every bar must start on a multiple of it from the open, so no bar straddles two periods; a bar that
  does not is a `ValueError`, never silently assigned.
* **Rows** are `floor(price / row_size)` (with a 1e-9 nudge so a price exactly on an edge is not
  lost to float error), and a row's price is its LOWER edge. A bar covers every row from its low's to
  its high's. A period's letters are the union of its bars' rows, so a gap inside a period leaves
  its rows empty.
* **Volume** is spread evenly across the rows of each bar: the approximation every profile built from
  candles makes. Exact volume at price needs every print.
* **POC**: the most TPOs; a tie goes to the row nearest the middle of the range, and an exact tie
  there to the lower row.
* **Value area**: the CBOT expansion. From the POC, compare the next two rows above with the next two
  below and take the pair with more TPOs (above on a tie), until the area holds `value_area_share`
  of all TPOs. Near an edge the pair is whatever rows remain.
* **IB** is the high and low of periods A and B's bars (prices, not rows). None until B has traded.
* **Tails** are runs of single-TPO rows at an extreme; `tail_high`/`tail_low` count the rows (two or
  more is a tail by the usual reading, one is not). **Poor** extreme: two or more TPOs share the
  extreme row. **Single prints** are the single-TPO rows that are not part of a tail.

No day-type label: which shapes count as "trend" or "neutral" is a definition a study declares, not
something this module decides. `default_row_size` is a convenience for display; a study fixes its row
size in its declaration.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

SESSION_OPEN_MIN = 9 * 60 + 30
SESSION_CLOSE_MIN = 16 * 60
PERIOD_MINUTES = 30
VALUE_AREA_SHARE = 0.70
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Rows about 0.03% of price apart: ~2 points on SPX, ~10 on NDX, 0.20 on SPY or QQQ, so a 1% day
# is about 30 rows on any of them.
ROW_FRACTION = 0.0003
_NICE = (1.0, 2.0, 2.5, 5.0)
_EDGE = 1e-9


@dataclass(frozen=True)
class Row:
    price: float  # the row's lower edge
    letters: str  # the periods that traded here, in order
    volume: float

    @property
    def tpo(self) -> int:
        return len(self.letters)


@dataclass(frozen=True)
class Profile:
    session: _dt.date
    row_size: float
    rows: tuple[Row, ...]  # low to high, every row in the range (an empty row has no letters)
    periods: str  # the letters of the periods that have bars
    open: float
    close: float
    high: float
    low: float
    poc: float
    val: float
    vah: float
    tpo_total: int
    ib_low: float | None
    ib_high: float | None
    extension_up: float | None
    extension_down: float | None
    tail_high: int
    tail_low: int
    poor_high: bool
    poor_low: bool
    single_prints: tuple[float, ...]
    volume_total: float
    volume_poc: float | None

    def as_dict(self) -> dict:
        return {
            "session": self.session.isoformat(),
            "row_size": self.row_size,
            "rows": [{"price": r.price, "letters": r.letters, "volume": r.volume} for r in self.rows],
            "periods": self.periods,
            "open": self.open,
            "close": self.close,
            "high": self.high,
            "low": self.low,
            "poc": self.poc,
            "val": self.val,
            "vah": self.vah,
            "tpo_total": self.tpo_total,
            "ib_low": self.ib_low,
            "ib_high": self.ib_high,
            "extension_up": self.extension_up,
            "extension_down": self.extension_down,
            "tail_high": self.tail_high,
            "tail_low": self.tail_low,
            "poor_high": self.poor_high,
            "poor_low": self.poor_low,
            "single_prints": list(self.single_prints),
            "volume_total": self.volume_total,
            "volume_poc": self.volume_poc,
        }


def default_row_size(price: float) -> float:
    """A display row size for a price: `ROW_FRACTION` of it, rounded to the nearest 1/2/2.5/5 step
    (nearest on a log scale)."""
    if not (price > 0 and math.isfinite(price)):
        raise ValueError(f"price must be positive, got {price!r}")
    target = price * ROW_FRACTION
    exp = math.floor(math.log10(target))
    best, best_dist = 1.0, math.inf
    for e in (exp - 1, exp, exp + 1):
        for m in _NICE:
            cand = m * 10.0**e
            dist = abs(math.log(cand / target))
            if dist < best_dist - 1e-12:
                best, best_dist = cand, dist
    return float(f"{best:.10g}")


def _et(t: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(float(t), ET)


def _field(bar, key: str) -> float:
    return float(bar[key]) if isinstance(bar, Mapping) else float(getattr(bar, key))


def session_dates(
    bars: Iterable, *, open_min: int = SESSION_OPEN_MIN, close_min: int = SESSION_CLOSE_MIN
) -> list[_dt.date]:
    """The ET dates that have at least one bar starting inside the regular session, oldest first."""
    out = set()
    for bar in bars:
        moment = _et(_field(bar, "t"))
        if open_min <= moment.hour * 60 + moment.minute < close_min:
            out.add(moment.date())
    return sorted(out)


def _row(price: float, row_size: float) -> int:
    return math.floor(price / row_size + _EDGE)


def _price(index: int, row_size: float) -> float:
    return float(f"{index * row_size:.10g}")


def _point_of_control(counts: Sequence[float]) -> int:
    mid = (len(counts) - 1) / 2
    best = 0
    for i, n in enumerate(counts):
        b = counts[best]
        if n > b or (n == b and abs(i - mid) < abs(best - mid)):
            best = i
    return best


def value_area(counts: Sequence[float], poc: int, share: float = VALUE_AREA_SHARE) -> tuple[int, int]:
    """The value area's (low, high) row indices: the CBOT expansion from `poc`, two rows at a time
    toward the heavier side (above on a tie), until it holds `share` of the total."""
    total = sum(counts)
    lo, hi = poc, poc
    acc = counts[poc]
    while acc < share * total:
        up_rows = list(range(hi + 1, min(hi + 3, len(counts))))
        dn_rows = list(range(lo - 1, max(lo - 3, -1), -1))
        if not up_rows and not dn_rows:
            break
        up = sum(counts[i] for i in up_rows)
        dn = sum(counts[i] for i in dn_rows)
        if up_rows and (up >= dn or not dn_rows):
            hi = up_rows[-1]
            acc += up
        else:
            lo = dn_rows[-1]
            acc += dn
    return lo, hi


def build_profile(
    bars: Iterable,
    *,
    row_size: float,
    bar_seconds: int,
    session: _dt.date | None = None,
    open_min: int = SESSION_OPEN_MIN,
    close_min: int = SESSION_CLOSE_MIN,
    period_minutes: int = PERIOD_MINUTES,
    value_area_share: float = VALUE_AREA_SHARE,
) -> Profile | None:
    """One session's profile from its bars, or None when the session has no bars.

    `session` defaults to the newest ET date with a regular-session bar. Bars on other dates, and
    bars outside `[open_min, close_min)`, are ignored, so a multi-day series (the console's 15-minute
    candles) can be passed whole. A bar with a missing or non-positive price is skipped.
    """
    if not (row_size > 0 and math.isfinite(row_size)):
        raise ValueError(f"row_size must be positive, got {row_size!r}")
    period_s = period_minutes * 60
    if bar_seconds <= 0 or period_s % bar_seconds != 0:
        raise ValueError(f"bar_seconds {bar_seconds} must divide the {period_minutes}-minute period")
    if not 0 < value_area_share <= 1:
        raise ValueError(f"value_area_share must be in (0, 1], got {value_area_share!r}")

    bars = list(bars)
    if session is None:
        dates = session_dates(bars, open_min=open_min, close_min=close_min)
        if not dates:
            return None
        session = dates[-1]

    # period index -> bars, in time order
    by_period: dict[int, list[tuple[float, float, float, float, float, float]]] = {}
    for bar in bars:
        t = _field(bar, "t")
        moment = _et(t)
        if moment.date() != session:
            continue
        offset_s = (moment.hour * 60 + moment.minute - open_min) * 60 + moment.second
        if not 0 <= offset_s < (close_min - open_min) * 60:
            continue
        if offset_s % bar_seconds != 0:
            raise ValueError(
                f"bar at {moment.isoformat()} does not start on a {bar_seconds}s boundary from the open"
            )
        o, h, low, c = (_field(bar, k) for k in ("o", "h", "l", "c"))
        if min(o, h, low, c) <= 0 or not all(math.isfinite(x) for x in (o, h, low, c)):
            continue
        v = _field(bar, "v") if (isinstance(bar, Mapping) and "v" in bar) or hasattr(bar, "v") else 0.0
        v = v if math.isfinite(v) and v > 0 else 0.0
        by_period.setdefault(offset_s // period_s, []).append((t, o, h, low, c, v))
    if not by_period:
        return None

    lo_idx = min(_row(b[3], row_size) for p in by_period.values() for b in p)
    hi_idx = max(_row(b[2], row_size) for p in by_period.values() for b in p)
    n_rows = hi_idx - lo_idx + 1
    letters: list[list[str]] = [[] for _ in range(n_rows)]
    volume = [0.0] * n_rows
    for period in sorted(by_period):
        letter = LETTERS[period]
        touched: set[int] = set()
        for _t, _o, h, low, _c, v in by_period[period]:
            a, b = _row(low, row_size) - lo_idx, _row(h, row_size) - lo_idx
            touched.update(range(a, b + 1))
            share = v / (b - a + 1)
            for i in range(a, b + 1):
                volume[i] += share
        for i in touched:
            letters[i].append(letter)

    counts = [len(x) for x in letters]
    total = sum(counts)
    poc = _point_of_control(counts)
    lo_va, hi_va = value_area(counts, poc, value_area_share)

    def run_of_singles(indices) -> int:
        k = 0
        for i in indices:
            if counts[i] != 1:
                break
            k += 1
        return k

    tail_high = run_of_singles(range(n_rows - 1, -1, -1))
    tail_low = run_of_singles(range(n_rows))
    if tail_high == n_rows:  # a one-period session: every row single; not a tail at both ends
        tail_low = 0
    singles = tuple(
        _price(lo_idx + i, row_size) for i in range(tail_low, n_rows - tail_high) if counts[i] == 1
    )

    ordered = [b for p in sorted(by_period) for b in sorted(by_period[p])]
    high = max(b[2] for b in ordered)
    low = min(b[3] for b in ordered)
    ib_bars = by_period.get(0, []) + by_period.get(1, [])
    has_ib = 0 in by_period and 1 in by_period
    ib_high = max(b[2] for b in ib_bars) if has_ib else None
    ib_low = min(b[3] for b in ib_bars) if has_ib else None
    volume_total = sum(volume)

    return Profile(
        session=session,
        row_size=row_size,
        rows=tuple(
            Row(price=_price(lo_idx + i, row_size), letters="".join(letters[i]), volume=volume[i])
            for i in range(n_rows)
        ),
        periods="".join(LETTERS[p] for p in sorted(by_period)),
        open=ordered[0][1],
        close=ordered[-1][4],
        high=high,
        low=low,
        poc=_price(lo_idx + poc, row_size),
        val=_price(lo_idx + lo_va, row_size),
        vah=_price(lo_idx + hi_va, row_size),
        tpo_total=total,
        ib_low=ib_low,
        ib_high=ib_high,
        extension_up=max(0.0, high - ib_high) if ib_high is not None else None,
        extension_down=max(0.0, ib_low - low) if ib_low is not None else None,
        tail_high=tail_high,
        tail_low=tail_low,
        poor_high=counts[-1] >= 2,
        poor_low=counts[0] >= 2,
        single_prints=singles,
        volume_total=volume_total,
        volume_poc=_price(lo_idx + _point_of_control(volume), row_size) if volume_total > 0 else None,
    )
