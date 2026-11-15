"""Entry signals: everything that turns bars into "it fired at 10:35", as pure functions.

A backtest, a paper or live loop, a console alert and a screener all ask this module whether a signal
fired, so the same bars always give the same firings. Choosing an ENTRY from the firings (a module's
entry window, its cadence) is not here: that belongs to the entry trigger (#1178).

**A signal is a dict** (`spec`) with a `kind` and that kind's parameters, plus the firing rules every
kind shares. `validate` refuses an unknown key, a missing parameter or a bad value, naming the path:
a misspelt key that defaulted silently would change what a study measured without a word.

**Two forms from one definition.** `state(spec, bars)` is true or false at each bar ("RSI(14) is above
70"); `firings(spec, bars)` is the bars where it fired ("RSI(14) crosses above 70"). An event is a
bar where the state turns true while the signal is armed, so the two forms cannot disagree about
what 70 means. `None` in a state is "not yet defined" (the indicator's window is not full, the
opening range is not done): it never fires and never arms.

**Bars** are `Bar`s, oldest first, any one width: `from_trail` cuts a session's spot trail into
N-minute bars (a bar is returned only once it has completed), `daily` takes one row per session. A
signal is evaluated at each bar's close, and a firing's `minute` is the bar's `end`.

**Firing rules** (every kind):

- `confirm` (default 1): the state must hold for this many bars in a row; the signal fires on the
  last of them. A run broken before then fires nothing.
- `reset` (default 0): after firing, the signal re-arms only once its value has gone back past the
  threshold by at least this much, in the kind's own units (RSI points, percent, index points). A
  kind with no value takes no reset.
- `first_of_day` (default false): at most one firing per session.
- `latest` (default none): no firing on a bar that ends after this time ("HH:MM", Eastern).
- `only_when` (default none): other signals whose STATE must be true on the firing bar. A firing
  they stop is not a firing: it does not count toward `first_of_day`, but the signal is spent as if
  it had fired, since the cross happened.

**Arming.** A *session* kind (gap, change, opening range, prior session, a session extreme or
distance, a market event) starts every session armed: a gap is news at the open. A *continuous* kind
(crosses, ranges, consecutive closes, an N-bar extreme or distance) is armed only after it has been
seen false, so a series that is already above 70 when its window fills has not crossed anything.
With `seed: false` its indicator is computed within each session and arming starts over each
session; with `seed: true` earlier sessions' bars warm the indicator and arming carries across the
close. An N-bar window spans the close by definition, so its arming always carries. A *candle*
pattern is a fact about one bar (and the bar before it), so it is armed on every bar: two inside
bars in a row are two firings. A candle takes no `confirm` and no `reset`.

**Market events** come from the caller: `firings(spec, bars, events={"fomc": [...ISO dates]})`. A
label the spec names and the caller does not supply is refused, never read as "no events". The
event kind fires once on each target session, on the first bar that ends at or after its `at`
time; its target is the event date itself, or `days` sessions (trading days, `core.calendar`) or
calendar days before or after it. A calendar-day target that is not a trading day never fires.

**Candlestick patterns**, on one bar's body (|close - open|), range (high - low) and shadows:

- `doji`: a body no more than `body_pct` percent of a non-zero range.
- `hammer`: a body no more than `body_pct` percent of the range, a lower shadow at least
  `shadow_ratio` times the body, and an upper shadow no longer than the body.
  `shooting_star` is its mirror (the long shadow above).
- `bullish_engulfing`: the bar before closed down, this one closes up, and this body covers that
  one (opens at or under its close, closes at or over its open) and is larger. `bearish_engulfing`
  is its mirror.
- `inside_bar`: a high under the bar before's high and a low over its low. `outside_bar`: a high over
  it and a low under it.

A pattern says nothing about the trend it appears in; that is what `only_when` is for. The bar
before is the previous bar in the series, across the close for intraday bars.

**Swing breaks** read swing points: a swing high is a bar whose high is above each of the `k` bars
before it and not passed by any of the `k` after (`core.indicators.swing_points`, the rule the
divergence already uses; lows mirrored). A swing point is known only `k` bars after it, so at each bar
only the swing points confirmed by then are read.

- `level_break`: the close beyond the latest confirmed swing high (`side: resistance`, above it) or
  swing low (`side: support`, below it).
- `trendline_break`: the close beyond the straight line through the latest two confirmed swing highs
  (resistance) or lows (support), extended to this bar. Distance along the line is counted in bars,
  so an overnight gap is one step; the line's slope is not restricted.
- `consolidation_break`: the `n` bars before this one span no more than `max_pct` percent of their
  low, and this bar closes above their high (`side: above`) or below their low (`side: below`).

All three are continuous: armed on a false state, carried across the close.

**Volume and VWAP sources** (an `a` or `b` of a cross or range):

- `{"name": "vwap", "anchor": A, "price": P, "weight": W}`: the running weighted mean of each bar's
  price `P` (`close`, `hlc3` or `ohlc4`) from the anchor. `A` is `"session"` (each session's first
  bar), a time `"HH:MM"` (each session's first bar starting at or after it; undefined before), or
  `{"event": label}` (the first bar of each session the label falls on, carried across sessions
  until the next; undefined before the first). `W` is `volume` (a true VWAP) or `equal` (every bar
  weighs the same: the declared stand-in for an index with no volume of its own).
- `{"name": "vwap_band", ...the vwap keys..., "k": x, "side": "upper" | "lower"}`: the VWAP plus or
  minus x weighted standard deviations of the price about it, over the same bars.
- `{"name": "relative_volume", "n": N}`: the bar's volume over the mean volume of the bar starting
  at the same minute in each of the N sessions before (undefined until there are N).
- `{"name": "volume_ratio", "n": N}`: the bar's volume over the mean of the N bars before it, across
  the close (a volume surge is a cross of it above a number).

These span sessions by their own definition (an anchor, the sessions before), so a signal's `seed`
does not split them. A source that reads volume (`needs_volume`) refuses bars without it
(`MissingVolume`): a missing volume is never a zero. `from_trail` takes `(minute, price, volume)`
ticks (a bar's volume is the sum of its ticks', missing if any tick's is) and `daily` a `volume`
column.

The defaults above are the identity of each rule (confirm once, no reset, no limit), not choices a
study inherits: a kind's own parameters have no defaults at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from cherrypick.core import calendar as cal
from cherrypick.core import indicators as ind

OPEN_MINUTE = 570  # 09:30 ET
CLOSE_MINUTE = 960  # 16:00 ET


@dataclass(frozen=True)
class Bar:
    """One bar: its session (ISO date), its start and end in minutes since midnight Eastern, and its
    prices. A daily bar runs 09:30 to 16:00."""

    session: str
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class MissingVolume(ValueError):
    """A volume or volume-weighted source was given bars without volume."""


@dataclass(frozen=True)
class Firing:
    """A bar where a signal fired: its index in the bars given, its session, the minute it closed,
    and the kind's value there (None for a kind without one)."""

    index: int
    session: str
    minute: int
    value: float | None


# --- bars -------------------------------------------------------------------------------------------


def from_trail(
    session: str, trail: Iterable[tuple[int, float]], size: int, *, until: int | None = None
) -> list[Bar]:
    """A session's spot trail, `(minute, price)` or `(minute, price, volume)` ticks, as `size`-minute
    bars from 09:30, oldest first. Ticks before the open or at or after the close are ignored, and a
    bucket with no tick is absent, never filled in. A bar's volume is the sum of its ticks' volumes,
    None if any tick has none. With `until`, only bars that have completed by then are returned (a bar
    from `start` completes at `start + size`), so a live reading never uses a half-formed bar."""
    if size <= 0:
        raise ValueError("size must be a positive number of minutes")
    buckets: dict[int, list[float]] = {}
    volumes: dict[int, float | None] = {}
    for tick in sorted(trail, key=lambda p: p[0]):
        minute, price = tick[0], tick[1]
        if not OPEN_MINUTE <= minute < CLOSE_MINUTE:
            continue
        start = OPEN_MINUTE + ((minute - OPEN_MINUTE) // size) * size
        buckets.setdefault(start, []).append(float(price))
        v = tick[2] if len(tick) > 2 else None
        if start not in volumes:
            volumes[start] = None if v is None else float(v)
        elif volumes[start] is not None:
            volumes[start] = None if v is None else volumes[start] + float(v)
    out = []
    for start in sorted(buckets):
        end = min(start + size, CLOSE_MINUTE)
        if until is not None and end > until:
            continue
        p = buckets[start]
        out.append(Bar(session, start, end, p[0], max(p), min(p), p[-1], volumes[start]))
    return out


def daily(rows: Iterable[Mapping]) -> list[Bar]:
    """One bar per session from rows with `session`, `open`, `high`, `low`, `close` (and `volume`,
    when the rows carry it), oldest first."""
    out = [
        Bar(
            str(r["session"]),
            OPEN_MINUTE,
            CLOSE_MINUTE,
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            None if r.get("volume") is None else float(r["volume"]),
        )
        for r in rows
    ]
    return sorted(out, key=lambda b: b.session)


def _sessions(bars: Sequence[Bar]) -> list[tuple[int, int]]:
    """(first, last + 1) index of each session's run of bars."""
    out, start = [], 0
    for i in range(1, len(bars) + 1):
        if i == len(bars) or bars[i].session != bars[start].session:
            out.append((start, i))
            start = i
    return out


# --- indicators as series over bars -------------------------------------------------------------------

_SOURCES: dict[str, tuple[str, ...]] = {
    "close": (),
    "sma": ("n",),
    "ema": ("n",),
    "rsi": ("n",),
    "cci": ("n",),
    "adx": ("n",),
    "atr": ("n",),
    "stochastic": ("k_period", "k_smooth", "d_smooth", "output"),
    "macd": ("fast", "slow", "signal", "output"),
    "vwap": ("anchor", "price", "weight"),
    "vwap_band": ("anchor", "price", "weight", "k", "side"),
    "relative_volume": ("n",),
    "volume_ratio": ("n",),
}
_OUTPUTS = {"stochastic": ("k", "d"), "macd": ("line", "signal", "histogram")}
#: Sources computed over all the bars whatever the signal's `seed`: they span sessions by definition.
_SPANNING = frozenset({"vwap", "vwap_band", "relative_volume", "volume_ratio"})
_PRICES = ("close", "hlc3", "ohlc4")


def _reads_volume(src: Mapping) -> bool:
    return src["name"] in ("relative_volume", "volume_ratio") or (
        src["name"] in ("vwap", "vwap_band") and src["weight"] == "volume"
    )


def needs_volume(spec: Mapping) -> bool:
    """Whether a (valid) signal reads bar volume: one of its sources, or an `only_when` signal's."""
    srcs = [spec.get(k) for k in ("a", "b")]
    if any(isinstance(s, Mapping) and _reads_volume(s) for s in srcs):
        return True
    return any(needs_volume(o) for o in spec.get("only_when", ()))


def _volumes(bars: Sequence[Bar], name: str) -> list[float]:
    vols = [b.volume for b in bars]
    if any(v is None for v in vols):
        raise MissingVolume(f"{name} reads bar volume, and these bars carry none")
    return [float(v) for v in vols]  # type: ignore[arg-type]


def _price(b: Bar, how: str) -> float:
    if how == "close":
        return b.close
    if how == "hlc3":
        return (b.high + b.low + b.close) / 3.0
    return (b.open + b.high + b.low + b.close) / 4.0


def _anchor_starts(anchor: object, bars: Sequence[Bar], events: Events) -> list[bool | None]:
    """Per bar: True where a new anchor begins, False where the running sum carries on, None where
    no anchor has begun yet (the value is undefined)."""
    out: list[bool | None] = [None] * len(bars)
    if anchor == "session":
        for a, b in _sessions(bars):
            out[a] = True
            for i in range(a + 1, b):
                out[i] = False
        return out
    if isinstance(anchor, str):
        at = _minute(anchor)
        for a, b in _sessions(bars):
            began = False
            for i in range(a, b):
                if bars[i].start >= at:
                    out[i] = not began
                    began = True
        return out
    label = anchor["event"]  # type: ignore[index]
    if events is None or label not in events:
        raise ValueError(f"vwap anchor: no dates supplied for event {label!r}")
    days = {str(d) for d in events[label]}
    began = False
    for a, b in _sessions(bars):
        if bars[a].session in days:
            out[a], began = True, True
        elif began:
            out[a] = False
        for i in range(a + 1, b):
            out[i] = out[a] if out[a] is None else False
    return out


def _vwap(src: Mapping, bars: Sequence[Bar], events: Events) -> list[float | None]:
    weights = _volumes(bars, src["name"]) if src["weight"] == "volume" else [1.0] * len(bars)
    starts = _anchor_starts(src["anchor"], bars, events)
    out: list[float | None] = [None] * len(bars)
    w = wp = wpp = 0.0
    for i, b in enumerate(bars):
        if starts[i] is None:
            continue
        if starts[i]:
            w = wp = wpp = 0.0
        p = _price(b, src["price"])
        w += weights[i]
        wp += weights[i] * p
        wpp += weights[i] * p * p
        if w <= 0:
            continue
        mean = wp / w
        if src["name"] == "vwap":
            out[i] = mean
        else:
            sd = max(wpp / w - mean * mean, 0.0) ** 0.5
            out[i] = mean + (1.0 if src["side"] == "upper" else -1.0) * float(src["k"]) * sd
    return out


def _relative_volume(src: Mapping, bars: Sequence[Bar]) -> list[float | None]:
    vols = _volumes(bars, src["name"])
    n = src["n"]
    out: list[float | None] = [None] * len(bars)
    seen: dict[int, list[float]] = {}  # bar start -> volumes of the sessions before, oldest first
    for a, b in _sessions(bars):
        today = {}
        for i in range(a, b):
            before = seen.get(bars[i].start, [])
            if len(before) >= n:
                mean = sum(before[-n:]) / n
                out[i] = None if mean <= 0 else vols[i] / mean
            today[bars[i].start] = vols[i]
        for start, v in today.items():
            seen.setdefault(start, []).append(v)
    return out


def _volume_ratio(src: Mapping, bars: Sequence[Bar]) -> list[float | None]:
    vols = _volumes(bars, src["name"])
    n = src["n"]
    out: list[float | None] = [None] * len(bars)
    for i in range(n, len(bars)):
        mean = sum(vols[i - n : i]) / n
        out[i] = None if mean <= 0 else vols[i] / mean
    return out


def _source_series(src: Mapping, bars: Sequence[Bar]) -> list[float | None]:
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    name = src["name"]
    if name == "close":
        return list(closes)
    if name == "sma":
        return ind.sma(closes, src["n"])
    if name == "ema":
        return ind.ema(closes, src["n"])
    if name == "rsi":
        return ind.rsi(closes, src["n"])
    if name == "cci":
        return ind.cci(highs, lows, closes, src["n"])
    if name == "adx":
        return ind.adx(highs, lows, closes, src["n"])
    if name == "atr":
        return ind.atr(highs, lows, closes, src["n"])
    if name == "stochastic":
        k, d = ind.stochastic(highs, lows, closes, src["k_period"], src["k_smooth"], src["d_smooth"])
        return k if src["output"] == "k" else d
    line, sig, hist = ind.macd(closes, src["fast"], src["slow"], src["signal"])
    return {"line": line, "signal": sig, "histogram": hist}[src["output"]]


def source(src: Mapping, bars: Sequence[Bar], *, seed: bool, events: Events = None) -> list[float | None]:
    """An indicator over the bars. With `seed`, computed over all of them, so earlier sessions warm it;
    without, computed within each session, so every session starts cold. A VWAP or volume source spans
    sessions by its own definition and ignores `seed`; an event anchor reads `events`."""
    name = src["name"]
    if name in ("vwap", "vwap_band"):
        return _vwap(src, bars, events)
    if name == "relative_volume":
        return _relative_volume(src, bars)
    if name == "volume_ratio":
        return _volume_ratio(src, bars)
    if seed:
        return _source_series(src, bars)
    out: list[float | None] = []
    for a, b in _sessions(bars):
        out.extend(_source_series(src, bars[a:b]))
    return out


# --- kinds ------------------------------------------------------------------------------------------
#
# Each kind returns (state, value) per bar. `value` is the signed distance INTO the state in the kind's
# own units, so the state is true where the value clears zero, and `reset` reads it. A kind with no
# meaningful distance returns value None.

Series = tuple[list[bool | None], list[float | None] | None]


def _sign(direction: str) -> float:
    return 1.0 if direction in ("above", "up", "high", "enters") else -1.0


def _cross(spec: Mapping, bars: Sequence[Bar], events: Events = None) -> Series:
    a = source(spec["a"], bars, seed=spec["seed"], events=events)
    b_spec = spec["b"]
    b = (
        [float(b_spec)] * len(bars)
        if isinstance(b_spec, (int, float))
        else source(b_spec, bars, seed=spec["seed"], events=events)
    )
    sense = _sign(spec["direction"])
    value = [None if x is None or y is None else sense * (x - y) for x, y in zip(a, b, strict=True)]
    return [None if v is None else v > 0 for v in value], value


def _range(spec: Mapping, bars: Sequence[Bar], events: Events = None) -> Series:
    lo, hi = float(spec["low"]), float(spec["high"])
    a = source(spec["a"], bars, seed=spec["seed"], events=events)
    sense = _sign(spec["direction"])
    value = [None if x is None else sense * min(x - lo, hi - x) for x in a]
    if spec["direction"] == "enters":
        state = [None if x is None else lo <= x <= hi for x in a]
    else:
        state = [None if x is None else not lo <= x <= hi for x in a]
    return state, value


def _prior_close(bars: Sequence[Bar], first: int) -> float | None:
    return bars[first - 1].close if first > 0 else None


def _gap(spec: Mapping, bars: Sequence[Bar]) -> Series:
    sense, pct = _sign(spec["direction"]), float(spec["pct"])
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for a, b in _sessions(bars):
        prior = _prior_close(bars, a)
        if prior is None:
            continue
        v = sense * (bars[a].open / prior - 1.0) * 100.0 - pct
        for i in range(a, b):
            state[i], value[i] = v >= 0, v
    return state, value


def _gap_fill(spec: Mapping, bars: Sequence[Bar]) -> Series:
    pct = float(spec["min_pct"])
    state: list[bool | None] = [None] * len(bars)
    for a, b in _sessions(bars):
        prior = _prior_close(bars, a)
        if prior is None:
            continue
        gap = (bars[a].open / prior - 1.0) * 100.0
        filled = False
        for i in range(a, b):
            if abs(gap) >= pct and not filled:
                filled = bars[i].low <= prior if gap > 0 else bars[i].high >= prior
            state[i] = filled
    return state, None


def _change(spec: Mapping, bars: Sequence[Bar]) -> Series:
    sense, pct = _sign(spec["direction"]), float(spec["pct"])
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for a, b in _sessions(bars):
        ref = bars[a].open if spec["from"] == "open" else _prior_close(bars, a)
        if ref is None:
            continue
        for i in range(a, b):
            v = sense * (bars[i].close / ref - 1.0) * 100.0 - pct
            state[i], value[i] = v >= 0, v
    return state, value


def _opening_range(spec: Mapping, bars: Sequence[Bar]) -> Series:
    done = OPEN_MINUTE + int(spec["minutes"])
    sense = _sign(spec["side"])
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for a, b in _sessions(bars):
        inside = [bars[i] for i in range(a, b) if bars[i].end <= done]
        covered = (
            inside
            and inside[0].start == OPEN_MINUTE
            and inside[-1].end == done
            and all(x.end == y.start for x, y in zip(inside, inside[1:], strict=False))
        )
        if not covered:
            continue
        hi, lo = max(x.high for x in inside), min(x.low for x in inside)
        for i in range(a, b):
            if bars[i].start < done:
                continue
            v = bars[i].close - hi if sense > 0 else lo - bars[i].close
            state[i], value[i] = v > 0, v
    return state, value


def _prior_session(spec: Mapping, bars: Sequence[Bar]) -> Series:
    sense = _sign(spec["side"])
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    runs = _sessions(bars)
    for (pa, pb), (a, b) in zip(runs, runs[1:], strict=False):
        hi = max(bars[i].high for i in range(pa, pb))
        lo = min(bars[i].low for i in range(pa, pb))
        for i in range(a, b):
            v = bars[i].close - hi if sense > 0 else lo - bars[i].close
            state[i], value[i] = v > 0, v
    return state, value


def _new_extreme(spec: Mapping, bars: Sequence[Bar]) -> Series:
    sense = _sign(spec["side"])
    look = spec["lookback"]
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    windows: list[tuple[int, int]] = []
    if look == "session":
        for a, b in _sessions(bars):
            windows.extend((a, i) for i in range(a, b))
    else:
        n = int(look)
        windows = [(i - n, i) for i in range(len(bars))]
    for i, (a, _) in enumerate(windows):
        if a < 0 or a >= i:
            continue
        if sense > 0:
            v = bars[i].high - max(bars[j].high for j in range(a, i))
        else:
            v = min(bars[j].low for j in range(a, i)) - bars[i].low
        state[i], value[i] = v > 0, v
    return state, value


def _consecutive(spec: Mapping, bars: Sequence[Bar]) -> Series:
    n, sense = int(spec["n"]), _sign(spec["direction"])
    state: list[bool | None] = [None] * len(bars)
    runs = [(0, len(bars))] if spec["seed"] else _sessions(bars)
    for a, b in runs:
        for i in range(a + n, b):
            state[i] = all(sense * (bars[j].close - bars[j - 1].close) > 0 for j in range(i - n + 1, i + 1))
    return state, None


def _distance(spec: Mapping, bars: Sequence[Bar]) -> Series:
    """How far the close sits from the window's high (or low), in percent of it. The window includes
    the bar itself: the session so far, or the last N bars."""
    side, look, pct = spec["side"], spec["lookback"], float(spec["pct"])
    within = spec["direction"] == "within"
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    first = [a for a, b in _sessions(bars) for _ in range(a, b)]
    for i in range(len(bars)):
        a = first[i] if look == "session" else i - int(look) + 1
        if a < 0:
            continue
        if side == "high":
            ext = max(bars[j].high for j in range(a, i + 1))
            d = (ext - bars[i].close) / ext * 100.0
        else:
            ext = min(bars[j].low for j in range(a, i + 1))
            d = (bars[i].close - ext) / ext * 100.0
        v = pct - d if within else d - pct
        state[i], value[i] = v >= 0, v
    return state, value


def _event_targets(spec: Mapping, dates: Iterable[str]) -> set[str]:
    n, timing, unit = int(spec["days"]), spec["timing"], spec["unit"]
    out = set()
    for text in dates:
        d = date.fromisoformat(str(text))
        if timing == "on":
            t = d
        elif unit == "calendar":
            t = d + timedelta(days=n if timing == "after" else -n)
        elif timing == "after":
            t = cal.nth_trading_day(d, n)
        else:
            t = d
            for _ in range(n):
                t = cal.previous_trading_day(t)
        out.add(t.isoformat())
    return out


def _event(spec: Mapping, bars: Sequence[Bar], events: Mapping[str, Iterable[str]] | None) -> Series:
    label = spec["event"]
    if events is None or label not in events:
        raise ValueError(f"signal: no dates given for event {label!r}")
    targets = _event_targets(spec, events[label])
    at = _minute(spec["at"])
    return [b.session in targets and b.end >= at for b in bars], None


def _candle_parts(b: Bar) -> tuple[float, float, float, float]:
    """(body, range, upper shadow, lower shadow)."""
    return abs(b.close - b.open), b.high - b.low, b.high - max(b.open, b.close), min(b.open, b.close) - b.low


def _is_pattern(spec: Mapping, prev: Bar | None, b: Bar) -> bool | None:
    p = spec["pattern"]
    body, rng, upper, lower = _candle_parts(b)
    if p == "doji":
        return rng > 0 and body <= spec["body_pct"] / 100.0 * rng
    if p in ("hammer", "shooting_star"):
        long, short = (lower, upper) if p == "hammer" else (upper, lower)
        return (
            rng > 0
            and body <= spec["body_pct"] / 100.0 * rng
            and long >= spec["shadow_ratio"] * body
            and short <= body
        )
    if prev is None:
        return None
    if p == "inside_bar":
        return b.high < prev.high and b.low > prev.low
    if p == "outside_bar":
        return b.high > prev.high and b.low < prev.low
    prev_body = abs(prev.close - prev.open)
    if p == "bullish_engulfing":
        return (
            prev.close < prev.open
            and b.close > b.open
            and b.open <= prev.close
            and b.close >= prev.open
            and body > prev_body
        )
    return (
        prev.close > prev.open
        and b.close < b.open
        and b.open >= prev.close
        and b.close <= prev.open
        and body > prev_body
    )


def _candle(spec: Mapping, bars: Sequence[Bar]) -> Series:
    return [_is_pattern(spec, bars[i - 1] if i else None, bars[i]) for i in range(len(bars))], None


def _confirmed_swings(bars: Sequence[Bar], k: int, high: bool) -> list[list[int]]:
    """At each bar, the swing points (indices, oldest first) confirmed by then: point p is known at
    bar p + k."""
    points = ind.swing_points([b.high if high else b.low for b in bars], k, high)
    out, j = [], 0
    for i in range(len(bars)):
        while j < len(points) and points[j] + k <= i:
            j += 1
        out.append(points[:j])
    return out


def _level_break(spec: Mapping, bars: Sequence[Bar]) -> Series:
    high = spec["side"] == "resistance"
    swings = _confirmed_swings(bars, int(spec["k"]), high)
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for i, pts in enumerate(swings):
        if not pts:
            continue
        p = pts[-1]
        v = bars[i].close - bars[p].high if high else bars[p].low - bars[i].close
        state[i], value[i] = v > 0, v
    return state, value


def _trendline_break(spec: Mapping, bars: Sequence[Bar]) -> Series:
    high = spec["side"] == "resistance"
    swings = _confirmed_swings(bars, int(spec["k"]), high)
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for i, pts in enumerate(swings):
        if len(pts) < 2:
            continue
        p1, p2 = pts[-2], pts[-1]
        y1, y2 = (bars[p1].high, bars[p2].high) if high else (bars[p1].low, bars[p2].low)
        line = y2 + (y2 - y1) / (p2 - p1) * (i - p2)
        v = bars[i].close - line if high else line - bars[i].close
        state[i], value[i] = v > 0, v
    return state, value


def _consolidation_break(spec: Mapping, bars: Sequence[Bar]) -> Series:
    n, width = int(spec["n"]), float(spec["max_pct"])
    above = spec["side"] == "above"
    state: list[bool | None] = [None] * len(bars)
    value: list[float | None] = [None] * len(bars)
    for i in range(n, len(bars)):
        window = bars[i - n : i]
        hi, lo = max(b.high for b in window), min(b.low for b in window)
        if (hi - lo) / lo * 100.0 > width:
            state[i] = False
            continue
        v = bars[i].close - hi if above else lo - bars[i].close
        state[i], value[i] = v > 0, v
    return state, value


#: Each candle pattern's own parameters, beyond `pattern`.
CANDLES: dict[str, tuple[str, ...]] = {
    "doji": ("body_pct",),
    "hammer": ("body_pct", "shadow_ratio"),
    "shooting_star": ("body_pct", "shadow_ratio"),
    "bullish_engulfing": (),
    "bearish_engulfing": (),
    "inside_bar": (),
    "outside_bar": (),
}


@dataclass(frozen=True)
class _Kind:
    fn: Callable[..., Series]
    params: dict[str, Callable[[object], bool]]
    arm: str  # "session": armed at every open; "continuous": armed on a false state; "each": every bar


def _is_dir(*allowed: str) -> Callable[[object], bool]:
    return lambda v: v in allowed


def _num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pos_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _flag(v: object) -> bool:
    return isinstance(v, bool)


def _src_or_num(v: object) -> bool:
    return _num(v) or isinstance(v, Mapping)


def _lookback(v: object) -> bool:
    return v == "session" or _pos_int(v)


KINDS: dict[str, _Kind] = {
    "cross": _Kind(
        _cross,
        {
            "a": lambda v: isinstance(v, Mapping),
            "b": _src_or_num,
            "direction": _is_dir("above", "below"),
            "seed": _flag,
        },
        "continuous",
    ),
    "range": _Kind(
        _range,
        {
            "a": lambda v: isinstance(v, Mapping),
            "low": _num,
            "high": _num,
            "direction": _is_dir("enters", "exits"),
            "seed": _flag,
        },
        "continuous",
    ),
    "gap": _Kind(_gap, {"direction": _is_dir("up", "down"), "pct": _num}, "session"),
    "gap_fill": _Kind(_gap_fill, {"min_pct": _num}, "session"),
    "change": _Kind(
        _change,
        {"from": _is_dir("open", "prior_close"), "direction": _is_dir("up", "down"), "pct": _num},
        "session",
    ),
    "opening_range": _Kind(
        _opening_range, {"minutes": _pos_int, "side": _is_dir("above", "below")}, "session"
    ),
    "prior_session": _Kind(_prior_session, {"side": _is_dir("high", "low")}, "session"),
    "new_extreme": _Kind(
        _new_extreme,
        {"side": _is_dir("high", "low"), "lookback": _lookback},
        "session",  # a session lookback; an N-bar lookback is continuous (see _arm_mode)
    ),
    "distance": _Kind(
        _distance,
        {
            "side": _is_dir("high", "low"),
            "lookback": _lookback,
            "direction": _is_dir("within", "beyond"),
            "pct": lambda v: _num(v) and v >= 0,
        },
        "session",  # as new_extreme
    ),
    "consecutive": _Kind(
        _consecutive, {"n": _pos_int, "direction": _is_dir("up", "down"), "seed": _flag}, "continuous"
    ),
    "event": _Kind(
        _event,
        {
            "event": lambda v: isinstance(v, str) and bool(v),
            "timing": _is_dir("before", "on", "after"),
            "days": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
            "unit": _is_dir("sessions", "calendar"),
            "at": lambda v: isinstance(v, str) and _hhmm_ok(v),
        },
        "session",
    ),
    "candle": _Kind(_candle, {"pattern": lambda v: v in CANDLES}, "each"),
    "level_break": _Kind(
        _level_break, {"side": _is_dir("resistance", "support"), "k": _pos_int}, "continuous"
    ),
    "trendline_break": _Kind(
        _trendline_break, {"side": _is_dir("resistance", "support"), "k": _pos_int}, "continuous"
    ),
    "consolidation_break": _Kind(
        _consolidation_break,
        {"n": _pos_int, "max_pct": lambda v: _num(v) and v >= 0, "side": _is_dir("above", "below")},
        "continuous",
    ),
}

#: Kinds whose state has no distance to measure, so `reset` means nothing for them.
_NO_VALUE = frozenset({"gap_fill", "consecutive", "event", "candle"})

_RULES: dict[str, Callable[[object], bool]] = {
    "confirm": _pos_int,
    "reset": lambda v: _num(v) and v >= 0,
    "first_of_day": _flag,
    "latest": lambda v: isinstance(v, str) and _hhmm_ok(v),
    "only_when": lambda v: isinstance(v, (list, tuple)),
}


def _hhmm_ok(text: str) -> bool:
    parts = text.split(":")
    return len(parts) == 2 and all(p.isdigit() for p in parts) and int(parts[0]) < 24 and int(parts[1]) < 60


def _minute(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _validate_source(src: object, path: str) -> None:
    if not isinstance(src, Mapping):
        raise ValueError(f"{path}: an indicator is a mapping with a name")
    name = src.get("name")
    if name not in _SOURCES:
        raise ValueError(f"{path}.name: unknown indicator {name!r}")
    need = set(_SOURCES[name])
    have = set(src) - {"name"}
    if have - need:
        raise ValueError(f"{path}: unknown key {sorted(have - need)[0]!r} for {name}")
    if need - have:
        raise ValueError(f"{path}: {name} needs {sorted(need - have)[0]!r}")
    if name in ("vwap", "vwap_band"):
        a = src["anchor"]
        ok = (
            a == "session"
            or (isinstance(a, str) and _hhmm_ok(a))
            or (isinstance(a, Mapping) and set(a) == {"event"} and isinstance(a["event"], str) and a["event"])
        )
        if not ok:
            raise ValueError(f'{path}.anchor: "session", a time "HH:MM" or {{"event": label}}')
        if src["price"] not in _PRICES:
            raise ValueError(f"{path}.price: one of {list(_PRICES)}")
        if src["weight"] not in ("volume", "equal"):
            raise ValueError(f"{path}.weight: one of ['volume', 'equal']")
        if name == "vwap_band":
            if not (_num(src["k"]) and src["k"] > 0):
                raise ValueError(f"{path}.k: a positive number of standard deviations")
            if src["side"] not in ("upper", "lower"):
                raise ValueError(f"{path}.side: one of ['upper', 'lower']")
        return
    for key in need - {"output"}:
        if not _pos_int(src[key]):
            raise ValueError(f"{path}.{key}: must be a positive whole number")
    if "output" in need and src["output"] not in _OUTPUTS[name]:
        raise ValueError(f"{path}.output: one of {list(_OUTPUTS[name])}")


def validate(spec: object, path: str = "signal") -> None:
    """Refuse a spec that is not exactly a known kind with all its parameters and valid rules."""
    if not isinstance(spec, Mapping):
        raise ValueError(f"{path}: a signal is a mapping with a kind")
    kind = KINDS.get(spec.get("kind"))  # type: ignore[arg-type]
    if kind is None:
        raise ValueError(f"{path}.kind: unknown kind {spec.get('kind')!r}")
    keys = set(spec) - {"kind"}
    params = dict(kind.params)
    if spec["kind"] == "candle" and spec.get("pattern") in CANDLES:
        params.update({p: lambda v: _num(v) and v >= 0 for p in CANDLES[spec["pattern"]]})
    unknown = keys - set(params) - set(_RULES)
    if unknown:
        raise ValueError(f"{path}: unknown key {sorted(unknown)[0]!r}")
    for key, ok in params.items():
        if key not in spec:
            raise ValueError(f"{path}: {spec['kind']} needs {key!r}")
        if not ok(spec[key]):
            raise ValueError(f"{path}.{key}: bad value {spec[key]!r}")
    if spec["kind"] == "event" and (spec["days"] == 0) != (spec["timing"] == "on"):
        raise ValueError(f"{path}.days: 0 with timing 'on', at least 1 before or after")
    if spec["kind"] == "candle" and spec.get("confirm", 1) != 1:
        raise ValueError(f"{path}.confirm: a candle pattern is one bar and takes no confirmation")
    for key in ("a", "b"):
        if isinstance(spec.get(key), Mapping):
            _validate_source(spec[key], f"{path}.{key}")
    if spec["kind"] == "range" and spec["low"] > spec["high"]:
        raise ValueError(f"{path}: low is above high")
    for key in keys & set(_RULES):
        if not _RULES[key](spec[key]):
            raise ValueError(f"{path}.{key}: bad value {spec[key]!r}")
    if spec.get("reset", 0) and spec["kind"] in _NO_VALUE:
        raise ValueError(f"{path}.reset: {spec['kind']} has no value to reset by")
    for i, other in enumerate(spec.get("only_when", ())):
        validate(other, f"{path}.only_when[{i}]")


def _arm_mode(spec: Mapping) -> str:
    if spec["kind"] in ("new_extreme", "distance") and spec["lookback"] != "session":
        return "continuous"
    return KINDS[spec["kind"]].arm


Events = Mapping[str, Iterable[str]] | None


def _kind_series(spec: Mapping, bars: Sequence[Bar], events: Events = None) -> Series:
    """The kind's (state, value) per bar: the one place a definition is evaluated."""
    if spec["kind"] == "event":
        return _event(spec, bars, events)
    if spec["kind"] in ("cross", "range"):
        return KINDS[spec["kind"]].fn(spec, bars, events)
    return KINDS[spec["kind"]].fn(spec, bars)


# --- the two forms ----------------------------------------------------------------------------------


def state(spec: Mapping, bars: Sequence[Bar], events: Events = None) -> list[bool | None]:
    """The state form: whether the signal's condition holds at each bar (None: not yet defined).
    `events` maps an event label to its ISO dates, for the event kind."""
    validate(spec)
    return _kind_series(spec, bars, events)[0]


def _candidates(
    states: Sequence[bool | None],
    values: Sequence[float | None] | None,
    bars: Sequence[Bar],
    *,
    confirm: int,
    reset: float,
    session_armed: bool,
    carry: bool,
    every_bar: bool = False,
) -> list[int]:
    """The bars where the signal fires before the filters: a run of `confirm` true states that began
    while armed. Arming: at each session start for a session kind; otherwise on a false state (past the
    reset distance, if any), carried across sessions only when `carry`; on every bar with `every_bar`."""
    out: list[int] = []
    armed, run = False, 0
    for i, s in enumerate(states):
        new_session = i == 0 or bars[i].session != bars[i - 1].session
        if new_session:
            run = 0
            if session_armed:
                armed = True
            elif not carry:
                armed = False
        if every_bar:
            armed = True
        if s is None:
            run = 0
            continue
        if s:
            if armed:
                run += 1
                if run == confirm:
                    out.append(i)
                    armed, run = False, 0
            continue
        run = 0
        if reset and values is not None:
            v = values[i]
            if v is not None and v <= -reset:
                armed = True
        else:
            armed = True
    return out


def firings(spec: Mapping, bars: Sequence[Bar], events: Events = None) -> list[Firing]:
    """The event form: every bar where the signal fired, with the firing rules applied. `events` maps an
    event label to its ISO dates, for the event kind (here or in `only_when`)."""
    validate(spec)
    states, values = _kind_series(spec, bars, events)
    mode = _arm_mode(spec)
    picks = _candidates(
        states,
        values,
        bars,
        confirm=spec.get("confirm", 1),
        reset=float(spec.get("reset", 0)),
        session_armed=mode == "session",
        # An N-bar window has no `seed`: it spans the close by definition, so arming does too.
        carry=mode == "continuous" and bool(spec.get("seed", True)),
        every_bar=mode == "each",
    )
    filters = [state(other, bars, events) for other in spec.get("only_when", ())]
    picks = [i for i in picks if all(f[i] is True for f in filters)]
    latest = spec.get("latest")
    if latest is not None:
        cut = _minute(latest)
        picks = [i for i in picks if bars[i].end <= cut]
    if spec.get("first_of_day", False):
        seen: set[str] = set()
        kept = []
        for i in picks:
            if bars[i].session not in seen:
                seen.add(bars[i].session)
                kept.append(i)
        picks = kept
    return [Firing(i, bars[i].session, bars[i].end, None if values is None else values[i]) for i in picks]
