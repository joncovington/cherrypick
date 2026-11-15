"""A signal studied on the underlying alone: what the price did after each firing (#1179).

Before any option is priced, a signal can be asked whether the market behaves differently after it
fires. A signal with no edge on the underlying will not be rescued by a structure built on it. This
is the pure half: a request and the bars in, a result dict out. `scripts/signal_study.py` reads the
bars and writes the result; the console reads the file.

**The request** (`validate` refuses an unknown key, a missing one or a bad value, naming its path):

- `name`: the result's file name (lower case, digits, `-` and `_`).
- `signal`: a `core.signals` spec. Event kinds are refused: the suite's events calendar does not
  reach the history (2022-2024 read unknown), so an event study would be judged on whichever years
  happen to be known.
- `symbol`, `source`: whose bars, and from where (`SOURCES`). `history` is the daily bars technicals
  keeps (2011 on, public), `recorder` the gex recorder's own spot minutes, `vendor` the licensed
  minute store, whose results are licensed and kept apart (`licensed`).
- `bar_minutes`: the bar width for an intraday source; refused for a daily one.
- `horizons`: sessions after the firing's own session at whose close a move is read; `0` is the
  firing session's close (intraday only).
- `distances`: `{"unit": "points"|"pct"|"em", "value": x}`. A distance is breached when the price
  went that far above (or below) the firing's price at any bar after it, through the horizon's close.
- `from`, `through` (optional): the sessions a firing or a control may sit in. Bars before `from` still
  warm the signal's indicators.

**Each observation** enters at a bar's close and reads, for each horizon, the move to the horizon's
close and the highest high and lowest low of the bars after the entry through that close. Its
expected move is the price x the vol index's last close known at entry / 100 x sqrt(t / 252), t the
sessions to the horizon's close counting the rest of the entry's session as a share of 390 minutes
(`EM_INDEX`; a symbol with no index has no expected move, and an `em` distance is refused for it).

**Sessions read** (`sessions_kept`): exchange trading days only, and for intraday bars only sessions
whose bars reach that day's close (13:00 on an early close). The rest are dropped before the signal is
evaluated and counted in `dropped_sessions`, so a horizon of N sessions counts the sessions kept.

**The control** is the same window on sessions where the signal had not fired: for every distinct
minute the signal fired at, every session that had not fired by that minute, with a bar ending then
and a defined state there (so warm-up sessions are excluded from both). A session that fires later
the same day is in the control at the earlier minutes: whether it fires later is the future, and a
control of days that never fired is chosen by its own outcome (a day that never broke out of its
range is one that stayed in it). Each control observation is weighted so every firing minute counts
as often as the signal fired at it; for daily bars that is every session without a firing, equally
weighted.

**Descriptive only.** Observations overlap when a horizon is longer than the spacing between firings,
and the difference's t takes no account of that. A verdict goes through a declaration, never here.
"""

from __future__ import annotations

import bisect
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date

from cherrypick.core import calendar as cal
from cherrypick.core import signals as sg

#: Where bars come from, and whether a result over them is derived from licensed data.
SOURCES = {
    "history": {"intraday": False, "licensed": False},
    "recorder": {"intraday": True, "licensed": False},
    "vendor": {"intraday": True, "licensed": True},
}

#: The vol index whose close prices a symbol's expected move.
EM_INDEX = {"SPX": "VIX", "SPY": "VIX", "XSP": "VIX", "NDX": "VXN", "QQQ": "VXN"}

UNITS = ("points", "pct", "em")
MAX_HORIZON = 63
SESSION_MINUTES = sg.CLOSE_MINUTE - sg.OPEN_MINUTE
YEAR_SESSIONS = 252
_EVENT_KINDS = frozenset({"event"})
_KEYS = {"name", "signal", "symbol", "source", "bar_minutes", "horizons", "distances", "from", "through"}
_REQUIRED = {"name", "signal", "symbol", "source", "horizons", "distances"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# --- the request -------------------------------------------------------------------------------------


def _kinds(spec: Mapping) -> set[str]:
    out = {spec.get("kind")}
    for other in spec.get("only_when", ()) or ():
        if isinstance(other, Mapping):
            out |= _kinds(other)
    return out


def _iso(text: object) -> bool:
    if not isinstance(text, str):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def validate(request: object) -> None:
    """Refuse a request that is not exactly what `study` reads, naming the path."""
    if not isinstance(request, Mapping):
        raise ValueError("request: must be an object")
    for key in request:
        if key not in _KEYS:
            raise ValueError(f"request.{key}: unknown key")
    for key in sorted(_REQUIRED - set(request)):
        raise ValueError(f"request.{key}: required")
    if not isinstance(request["name"], str) or not _NAME.match(request["name"]):
        raise ValueError("request.name: lower-case letters, digits, '-' and '_', at most 64")
    sg.validate(request["signal"], "request.signal")
    if _kinds(request["signal"]) & _EVENT_KINDS:
        raise ValueError(
            "request.signal: event signals are refused; the events calendar does not reach the history"
        )
    if not isinstance(request["symbol"], str) or not request["symbol"]:
        raise ValueError("request.symbol: a symbol")
    source = request["source"]
    if source not in SOURCES:
        raise ValueError(f"request.source: one of {', '.join(SOURCES)}")
    intraday = SOURCES[source]["intraday"]
    width = request.get("bar_minutes")
    if intraday:
        if not isinstance(width, int) or isinstance(width, bool) or not 1 <= width <= SESSION_MINUTES:
            raise ValueError(f"request.bar_minutes: a whole number of minutes, 1 to {SESSION_MINUTES}")
    elif "bar_minutes" in request:
        raise ValueError("request.bar_minutes: a daily source has one bar a session")
    horizons = request["horizons"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(not isinstance(h, int) or isinstance(h, bool) or not 0 <= h <= MAX_HORIZON for h in horizons)
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError(f"request.horizons: distinct whole numbers of sessions, 0 to {MAX_HORIZON}")
    if not intraday and 0 in horizons:
        raise ValueError("request.horizons: 0 (the firing session's close) needs an intraday source")
    distances = request["distances"]
    if not isinstance(distances, list):
        raise ValueError("request.distances: a list")
    for i, d in enumerate(distances):
        path = f"request.distances[{i}]"
        if not isinstance(d, Mapping) or set(d) != {"unit", "value"}:
            raise ValueError(f"{path}: exactly unit and value")
        if d["unit"] not in UNITS:
            raise ValueError(f"{path}.unit: one of {', '.join(UNITS)}")
        v = d["value"]
        if not isinstance(v, int | float) or isinstance(v, bool) or not v > 0:
            raise ValueError(f"{path}.value: above zero")
        if d["unit"] == "em" and request["symbol"] not in EM_INDEX:
            raise ValueError(f"{path}.unit: {request['symbol']} has no vol index for an expected move")
    for key in ("from", "through"):
        if key in request and not _iso(request[key]):
            raise ValueError(f"request.{key}: an ISO date")
    if "from" in request and "through" in request and request["from"] > request["through"]:
        raise ValueError("request.from: after through")


# --- one observation ---------------------------------------------------------------------------------


class _Index:
    """The bars' sessions in order, each session's last bar, and from each bar the highest high and
    lowest low through its session's close, so a path's extremes cost one step a session."""

    def __init__(self, bars: Sequence[sg.Bar]) -> None:
        self.sessions: list[str] = []
        self.last: dict[str, int] = {}
        self.at: dict[tuple[str, int], int] = {}
        for i, b in enumerate(bars):
            if self.sessions and b.session < self.sessions[-1]:
                raise ValueError("bars: oldest first")
            if not self.sessions or self.sessions[-1] != b.session:
                self.sessions.append(b.session)
            self.last[b.session] = i
            self.at[(b.session, b.end)] = i
        self.pos = {s: k for k, s in enumerate(self.sessions)}
        self.hi_to_close = [0.0] * len(bars)
        self.lo_to_close = [0.0] * len(bars)
        for i in range(len(bars) - 1, -1, -1):
            b = bars[i]
            same = i + 1 < len(bars) and bars[i + 1].session == b.session
            self.hi_to_close[i] = max(b.high, self.hi_to_close[i + 1]) if same else b.high
            self.lo_to_close[i] = min(b.low, self.lo_to_close[i + 1]) if same else b.low
        self.first = {
            s: (self.last[self.sessions[k - 1]] + 1 if k else 0) for k, s in enumerate(self.sessions)
        }


def _vol_at(vol: Mapping[str, float] | None, dates: list[str], session: str, minute: int) -> float | None:
    """The vol index's last close known at an entry: the session's own at 16:00, else the one before."""
    if not vol:
        return None
    k = (
        bisect.bisect_right(dates, session)
        if minute >= sg.CLOSE_MINUTE
        else bisect.bisect_left(dates, session)
    )
    return float(vol[dates[k - 1]]) if k > 0 else None


def observe(
    bars: Sequence[sg.Bar],
    index: _Index,
    i: int,
    horizon: int,
    vol_close: float | None,
) -> dict | None:
    """One entry at bar `i`'s close read at `horizon`, or None when the bars end first or nothing
    follows the entry before the horizon's close."""
    entry = bars[i]
    k = index.pos[entry.session] + horizon
    if k >= len(index.sessions):
        return None
    j = index.last[index.sessions[k]]
    if j <= i:
        return None
    price = entry.close
    start = index.pos[entry.session]
    if i + 1 < len(bars) and bars[i + 1].session == entry.session:
        hi, lo = index.hi_to_close[i + 1], index.lo_to_close[i + 1]
    else:
        hi, lo = -math.inf, math.inf
    for s in index.sessions[start + 1 : k + 1]:
        f = index.first[s]
        hi, lo = max(hi, index.hi_to_close[f]), min(lo, index.lo_to_close[f])
    t = horizon + max(sg.CLOSE_MINUTE - entry.end, 0) / SESSION_MINUTES
    em = price * vol_close / 100.0 * math.sqrt(t / YEAR_SESSIONS) if vol_close and t > 0 else None
    up, down = max(hi - price, 0.0), max(price - lo, 0.0)
    return {
        "move_pts": bars[j].close - price,
        "move_pct": (bars[j].close / price - 1.0) * 100.0,
        "up_pts": up,
        "down_pts": down,
        "up_pct": up / price * 100.0,
        "down_pct": down / price * 100.0,
        "em_pts": em,
        "range_em": max(up, down) / em if em else None,
    }


def breached(obs: Mapping, distance: Mapping) -> tuple[bool, bool] | None:
    """Whether the price went `distance` above and below the entry, or None without an expected move."""
    v, unit = float(distance["value"]), distance["unit"]
    if unit == "points":
        return obs["up_pts"] >= v, obs["down_pts"] >= v
    if unit == "pct":
        return obs["up_pct"] >= v, obs["down_pct"] >= v
    if obs["em_pts"] is None:
        return None
    return obs["up_pts"] >= v * obs["em_pts"], obs["down_pts"] >= v * obs["em_pts"]


# --- summaries ---------------------------------------------------------------------------------------


def _wmean(xs: Sequence[float], ws: Sequence[float]) -> float | None:
    total = sum(ws)
    return sum(x * w for x, w in zip(xs, ws, strict=True)) / total if total > 0 else None


def _wmedian(xs: Sequence[float], ws: Sequence[float]) -> float | None:
    pairs = sorted(zip(xs, ws, strict=True))
    total = sum(ws)
    if total <= 0:
        return None
    run = 0.0
    for x, w in pairs:
        run += w
        if run >= total / 2.0:
            return x
    return pairs[-1][0]


def _wsd(xs: Sequence[float], ws: Sequence[float]) -> float | None:
    """The weighted standard deviation, with the reliability-weight correction (equal weights give the
    sample sd)."""
    total = sum(ws)
    if len(xs) < 2 or total <= 0:
        return None
    m = _wmean(xs, ws)
    sq = sum(w * w for w in ws)
    denom = total - sq / total
    if denom <= 0:
        return None
    return math.sqrt(sum(w * (x - m) ** 2 for x, w in zip(xs, ws, strict=True)) / denom)


def summarize(obs: Sequence[Mapping], weights: Sequence[float], distances: Sequence[Mapping]) -> dict:
    """One side's outcomes at one horizon: n, the move's mean, median and sd in percent, the share that
    closed up, the range against the expected move, and each distance's breach shares."""
    n = len(obs)
    if n == 0:
        return {"n": 0}
    moves = [o["move_pct"] for o in obs]
    out: dict = {
        "n": n,
        "mean_pct": _wmean(moves, weights),
        "median_pct": _wmedian(moves, weights),
        "sd_pct": _wsd(moves, weights),
        "up_share": _wmean([1.0 if m > 0 else 0.0 for m in moves], weights),
        "mean_up_pct": _wmean([o["up_pct"] for o in obs], weights),
        "mean_down_pct": _wmean([o["down_pct"] for o in obs], weights),
    }
    with_em = [(o["range_em"], w) for o, w in zip(obs, weights, strict=True) if o["range_em"] is not None]
    out["em_n"] = len(with_em)
    if with_em:
        r, w = zip(*with_em, strict=True)
        out["mean_range_em"] = _wmean(r, w)
        out["beyond_em_share"] = _wmean([1.0 if x >= 1.0 else 0.0 for x in r], w)
    rows = []
    for d in distances:
        hits = [(breached(o, d), w) for o, w in zip(obs, weights, strict=True)]
        hits = [(h, w) for h, w in hits if h is not None]
        row = {"unit": d["unit"], "value": d["value"], "n": len(hits)}
        if hits:
            hw = [w for _, w in hits]
            row["up"] = _wmean([1.0 if h[0] else 0.0 for h, _ in hits], hw)
            row["down"] = _wmean([1.0 if h[1] else 0.0 for h, _ in hits], hw)
            row["either"] = _wmean([1.0 if h[0] or h[1] else 0.0 for h, _ in hits], hw)
        rows.append(row)
    out["distances"] = rows
    return out


def difference(fired: Mapping, control: Mapping) -> dict | None:
    """The firings' mean move less the control's, and a Welch t on it (descriptive: overlap ignored)."""
    if not fired.get("n") or not control.get("n"):
        return None
    diff = fired["mean_pct"] - control["mean_pct"]
    s1, s2 = fired.get("sd_pct"), control.get("sd_pct")
    t = None
    if s1 is not None and s2 is not None:
        se = math.sqrt(s1**2 / fired["n"] + s2**2 / control["n"])
        t = diff / se if se > 0 else None
    return {"mean_pct": diff, "t": t}


# --- the study ---------------------------------------------------------------------------------------


def in_span(request: Mapping, session: str) -> bool:
    return request.get("from", "") <= session <= request.get("through", "9999-12-31")


def control_entries(
    request: Mapping,
    index: _Index,
    states: Sequence[bool | None],
    minutes: Mapping[int, int],
    first_fired: Mapping[str, int],
) -> list[tuple[int, float]]:
    """The control's entries, `(bar index, weight)`: for each minute the signal fired at (`minutes`, how
    often), the bar ending then on every session in the span that had not fired by then
    (`first_fired`: each session's first firing minute) and has a defined state there, each weighted so
    that minute's entries sum to its firing count. "Had not fired by then", never "did not fire that
    day": whether a session fires later is the future, and a session that never broke out is one that
    stayed in its range, so the second control would be chosen by its own outcome."""
    out: list[tuple[int, float]] = []
    for minute, count in sorted(minutes.items()):
        at = [
            index.at[(s, minute)]
            for s in index.sessions
            if first_fired.get(s, sg.CLOSE_MINUTE + 1) > minute
            and in_span(request, s)
            and (s, minute) in index.at
            and states[index.at[(s, minute)]] is not None
        ]
        out.extend((i, count / len(at)) for i in at)
    return out


def sessions_kept(bars: Sequence[sg.Bar], *, intraday: bool) -> tuple[list[sg.Bar], dict[str, int]]:
    """The bars of the sessions a study reads, and how many sessions were dropped and why: a day the
    exchange was shut (a recorder writes weekends too, at a flat price), and for intraday bars a
    session whose bars stop before that day's close (a recording that started late or broke off),
    whose "close" would be wherever it stopped."""
    by: dict[str, list[sg.Bar]] = {}
    for b in bars:
        by.setdefault(b.session, []).append(b)
    kept: list[sg.Bar] = []
    dropped = {"not_trading_day": 0, "incomplete": 0}
    for session, rows in by.items():
        day = date.fromisoformat(session)
        if not cal.is_trading_day(day):
            dropped["not_trading_day"] += 1
            continue
        if intraday:
            hh, mm = cal.session_close_hhmm(day).split(":")
            if rows[-1].end < int(hh) * 60 + int(mm):
                dropped["incomplete"] += 1
                continue
        kept.extend(rows)
    return kept, dropped


def study(request: Mapping, bars: Sequence[sg.Bar], *, vol: Mapping[str, float] | None = None) -> dict:
    """The study's result: each firing's outcomes, and per horizon the firings against the control,
    overall and by year. `vol` maps a session to the symbol's vol index close (`EM_INDEX`), or None."""
    validate(request)
    spec = request["signal"]
    horizons = sorted(request["horizons"])
    distances = request["distances"]
    bars, dropped = sessions_kept(bars, intraday=SOURCES[request["source"]]["intraday"])
    index = _Index(bars)
    dates = sorted(vol) if vol else []
    fired_all = sg.firings(spec, bars)
    fired = [f for f in fired_all if in_span(request, f.session)]
    states = sg.state(spec, bars)
    first_fired: dict[str, int] = {}
    for f in fired_all:
        first_fired.setdefault(f.session, f.minute)
    minutes: dict[int, int] = {}
    for f in fired:
        minutes[f.minute] = minutes.get(f.minute, 0) + 1

    controls = control_entries(request, index, states, minutes, first_fired)

    def vol_of(i: int) -> float | None:
        return _vol_at(vol, dates, bars[i].session, bars[i].end)

    firing_rows = []
    per_h: dict[int, dict] = {h: {"fired": [], "control": [], "cw": []} for h in horizons}
    years: dict[str, dict[int, dict]] = {}
    for f in fired:
        v = vol_of(f.index)
        row = {"session": f.session, "minute": f.minute, "price": bars[f.index].close, "value": f.value}
        for h in horizons:
            o = observe(bars, index, f.index, h, v)
            row[f"h{h}"] = (
                None if o is None else {k: o[k] for k in ("move_pct", "up_pct", "down_pct", "range_em")}
            )
            if o is not None:
                per_h[h]["fired"].append(o)
                years.setdefault(f.session[:4], {}).setdefault(h, {"fired": [], "control": [], "cw": []})[
                    "fired"
                ].append(o)
        firing_rows.append(row)
    for i, w in controls:
        v = vol_of(i)
        for h in horizons:
            o = observe(bars, index, i, h, v)
            if o is None:
                continue
            per_h[h]["control"].append(o)
            per_h[h]["cw"].append(w)
            y = years.setdefault(bars[i].session[:4], {}).setdefault(
                h, {"fired": [], "control": [], "cw": []}
            )
            y["control"].append(o)
            y["cw"].append(w)

    out_h = []
    for h in horizons:
        side = per_h[h]
        a = summarize(side["fired"], [1.0] * len(side["fired"]), distances)
        b = summarize(side["control"], side["cw"], distances)
        out_h.append(
            {
                "horizon": h,
                "fired": a,
                "control": b,
                "difference": difference(a, b),
                "incomplete": len(fired) - a["n"],
            }
        )
    by_year = []
    for y in sorted(years):
        row = {"year": y, "horizons": []}
        for h in horizons:
            side = years[y].get(h, {"fired": [], "control": [], "cw": []})
            a = summarize(side["fired"], [1.0] * len(side["fired"]), [])
            b = summarize(side["control"], side["cw"], [])
            row["horizons"].append(
                {
                    "horizon": h,
                    "fired_n": a["n"],
                    "fired_mean_pct": a.get("mean_pct"),
                    "control_n": b["n"],
                    "control_mean_pct": b.get("mean_pct"),
                    "difference": difference(a, b),
                }
            )
        by_year.append(row)
    span_sessions = [s for s in index.sessions if in_span(request, s)]
    return {
        "ok": True,
        "name": request["name"],
        "request": dict(request),
        "licensed": SOURCES[request["source"]]["licensed"],
        "em_index": EM_INDEX.get(request["symbol"]),
        "bars": {
            "count": len(bars),
            "first": index.sessions[0] if index.sessions else None,
            "last": index.sessions[-1] if index.sessions else None,
            "sessions": len(span_sessions),
        },
        "dropped_sessions": dropped,
        "firings_n": len(fired),
        "firing_sessions": len({f.session for f in fired}),
        "control_n": len(controls),
        "horizons": out_h,
        "by_year": by_year,
        "firings": firing_rows,
    }
