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

**Arming.** A *session* kind (gap, change, opening range, prior session, a session extreme) starts
every session armed: a gap is news at the open. A *continuous* kind (crosses, ranges, consecutive
closes, an N-bar extreme) is armed only after it has been seen false, so a series that is already
above 70 when its window fills has not crossed anything. With `seed: false` its indicator is
computed within each session and arming starts over each session; with `seed: true` earlier
sessions' bars warm the indicator and arming carries across the close. An N-bar extreme's window
spans the close by definition, so its arming always carries.

The defaults above are the identity of each rule (confirm once, no reset, no limit), not choices a
study inherits: a kind's own parameters have no defaults at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

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
    """A session's spot trail, `(minute, price)` pairs, as `size`-minute bars from 09:30, oldest
    first. Ticks before the open or at or after the close are ignored, and a bucket with no tick is
    absent, never filled in. With `until`, only bars that have completed by then are returned (a bar
    from `start` completes at `start + size`), so a live reading never uses a half-formed bar."""
    if size <= 0:
        raise ValueError("size must be a positive number of minutes")
    buckets: dict[int, list[float]] = {}
    for minute, price in sorted(trail, key=lambda p: p[0]):
        if not OPEN_MINUTE <= minute < CLOSE_MINUTE:
            continue
        start = OPEN_MINUTE + ((minute - OPEN_MINUTE) // size) * size
        buckets.setdefault(start, []).append(float(price))
    out = []
    for start in sorted(buckets):
        end = min(start + size, CLOSE_MINUTE)
        if until is not None and end > until:
            continue
        p = buckets[start]
        out.append(Bar(session, start, end, p[0], max(p), min(p), p[-1]))
    return out


def daily(rows: Iterable[Mapping]) -> list[Bar]:
    """One bar per session from rows with `session`, `open`, `high`, `low`, `close`, oldest first."""
    out = [
        Bar(
            str(r["session"]),
            OPEN_MINUTE,
            CLOSE_MINUTE,
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
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
}
_OUTPUTS = {"stochastic": ("k", "d"), "macd": ("line", "signal", "histogram")}


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


def source(src: Mapping, bars: Sequence[Bar], *, seed: bool) -> list[float | None]:
    """An indicator over the bars. With `seed`, computed over all of them, so earlier sessions warm it;
    without, computed within each session, so every session starts cold."""
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


def _cross(spec: Mapping, bars: Sequence[Bar]) -> Series:
    a = source(spec["a"], bars, seed=spec["seed"])
    b_spec = spec["b"]
    b = (
        [float(b_spec)] * len(bars)
        if isinstance(b_spec, (int, float))
        else source(b_spec, bars, seed=spec["seed"])
    )
    sense = _sign(spec["direction"])
    value = [None if x is None or y is None else sense * (x - y) for x, y in zip(a, b, strict=True)]
    return [None if v is None else v > 0 for v in value], value


def _range(spec: Mapping, bars: Sequence[Bar]) -> Series:
    lo, hi = float(spec["low"]), float(spec["high"])
    a = source(spec["a"], bars, seed=spec["seed"])
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


@dataclass(frozen=True)
class _Kind:
    fn: Callable[[Mapping, Sequence[Bar]], Series]
    params: dict[str, Callable[[object], bool]]
    session: bool  # armed afresh at every session's first bar


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


KINDS: dict[str, _Kind] = {
    "cross": _Kind(
        _cross,
        {
            "a": lambda v: isinstance(v, Mapping),
            "b": _src_or_num,
            "direction": _is_dir("above", "below"),
            "seed": _flag,
        },
        False,
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
        False,
    ),
    "gap": _Kind(_gap, {"direction": _is_dir("up", "down"), "pct": _num}, True),
    "gap_fill": _Kind(_gap_fill, {"min_pct": _num}, True),
    "change": _Kind(
        _change,
        {"from": _is_dir("open", "prior_close"), "direction": _is_dir("up", "down"), "pct": _num},
        True,
    ),
    "opening_range": _Kind(_opening_range, {"minutes": _pos_int, "side": _is_dir("above", "below")}, True),
    "prior_session": _Kind(_prior_session, {"side": _is_dir("high", "low")}, True),
    "new_extreme": _Kind(
        _new_extreme,
        {"side": _is_dir("high", "low"), "lookback": lambda v: v == "session" or _pos_int(v)},
        True,  # a session lookback; an N-bar lookback is continuous (see _is_session)
    ),
    "consecutive": _Kind(
        _consecutive, {"n": _pos_int, "direction": _is_dir("up", "down"), "seed": _flag}, False
    ),
}

#: Kinds whose state has no distance to measure, so `reset` means nothing for them.
_NO_VALUE = frozenset({"gap_fill", "consecutive"})

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
    unknown = keys - set(kind.params) - set(_RULES)
    if unknown:
        raise ValueError(f"{path}: unknown key {sorted(unknown)[0]!r}")
    for key, ok in kind.params.items():
        if key not in spec:
            raise ValueError(f"{path}: {spec['kind']} needs {key!r}")
        if not ok(spec[key]):
            raise ValueError(f"{path}.{key}: bad value {spec[key]!r}")
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


def _is_session(spec: Mapping) -> bool:
    if spec["kind"] == "new_extreme":
        return spec["lookback"] == "session"
    return KINDS[spec["kind"]].session


def _kind_series(spec: Mapping, bars: Sequence[Bar]) -> Series:
    """The kind's (state, value) per bar: the one place a definition is evaluated."""
    return KINDS[spec["kind"]].fn(spec, bars)


# --- the two forms ----------------------------------------------------------------------------------


def state(spec: Mapping, bars: Sequence[Bar]) -> list[bool | None]:
    """The state form: whether the signal's condition holds at each bar (None: not yet defined)."""
    validate(spec)
    return _kind_series(spec, bars)[0]


def _candidates(
    states: Sequence[bool | None],
    values: Sequence[float | None] | None,
    bars: Sequence[Bar],
    *,
    confirm: int,
    reset: float,
    session_armed: bool,
    carry: bool,
) -> list[int]:
    """The bars where the signal fires before the filters: a run of `confirm` true states that began
    while armed. Arming: at each session start for a session kind; otherwise on a false state (past the
    reset distance, if any), carried across sessions only when `carry`."""
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


def firings(spec: Mapping, bars: Sequence[Bar]) -> list[Firing]:
    """The event form: every bar where the signal fired, with the firing rules applied."""
    validate(spec)
    states, values = _kind_series(spec, bars)
    session_kind = _is_session(spec)
    picks = _candidates(
        states,
        values,
        bars,
        confirm=spec.get("confirm", 1),
        reset=float(spec.get("reset", 0)),
        session_armed=session_kind,
        # An N-bar extreme has no `seed`: its window spans the close by definition, so arming does too.
        carry=not session_kind and bool(spec.get("seed", True)),
    )
    filters = [state(other, bars) for other in spec.get("only_when", ())]
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
