"""P&L broken down by something known when a trade was entered: one engine, one column set.

docs/strategy-analysis-plan.md, Phase 3. Every table has the same columns -- trades, sessions, net,
win rate, average net, best, worst -- so a reader learns one table, not one per dimension. Every
row carries its sample, and a row with fewer than `MIN_SESSIONS` sessions is marked `thin`: trades
entered on one session are not independent, so it is sessions, not trades, that say how much a row
can carry. A thin row is shown, never hidden, and never read as a finding.

Dimensions, each a function from a record to a bucket name or None (not bucketable, counted):

- `weekday`  the session's day of the week
- `hour`     the hour of entry, in the timezone the module stamped (ET for every module here);
             None where the schema records no entry time
- `month`    the session's month
- `event`    the major scheduled releases on the session (`core.events`), joined; "none" when the
             calendar vouches for a day with no major release, and "unknown" when it cannot speak
             for the day -- an unknown day is never counted as a quiet one

These are historical associations over the trades the window holds, not predictions, and a
breakdown of a short sample is mostly noise: the `thin` mark is the reader's guard against it.

Two regime dimensions, for the modules that write no regime cuts of their own (2026-10-17):

- `vix`      VIX at entry, in fixed bands (`VIX_EDGES`)
- `term`     VIX/VIX3M at entry: `contango` under 0.97 (curve's `contango_max`), `flat` to 1.0,
             `backwardation` from 1.0 (contango's `flipexit` line)

Both read `r["regime"]`, the market sample `core.regime.markets_at` joined to the record's entry
time (at-or-before, staleness-bounded) and attached by `attach_regime`. A record whose join came
back unmeasured -- an entry before the recorder's first sample on 2026-08-24, a recorder outage,
a reading refused at the time -- is the bucket `unmeasured`, shown and never read as a calm day.
A record with no entry time is not bucketable and is counted as skipped.

Flies and MEIC get neither. They cut their own P&L by the recorded regime nightly
(`core.regimecuts`), era-scoped and stamped thin, fragile and paired, which this engine would only
weaken; the analysis page shows those cuts beside these tables and links to them. A second,
weaker cut of the same thing beside them would be two answers to one question.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from functools import lru_cache

from cherrypick.core import events as _events
from cherrypick.core.clock import ET as _ET

#: A row with fewer sessions than this is marked thin.
MIN_SESSIONS = 5

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _session_date(r: Mapping) -> date | None:
    try:
        return date.fromisoformat(str(r.get("session"))[:10])
    except ValueError:
        return None


def weekday(r: Mapping) -> str | None:
    d = _session_date(r)
    return _WEEKDAYS[d.weekday()] if d else None


def hour(r: Mapping) -> str | None:
    t = r.get("entry_time")
    if not t:
        return None
    try:
        return f"{datetime.fromisoformat(str(t)).hour:02d}:00"
    except ValueError:
        return None


def month(r: Mapping) -> str | None:
    d = _session_date(r)
    return d.isoformat()[:7] if d else None


@lru_cache(maxsize=4096)
def _event_bucket(day: date) -> str:
    doc = _events.day_events(day)
    if not doc["known"]:
        return "unknown"
    major = sorted({e["label"] for e in doc["events"] if e.get("major")})
    return "+".join(major) if major else "none"


def event(r: Mapping) -> str | None:
    d = _session_date(r)
    return _event_bucket(d) if d else None


#: VIX band edges; a band is `[low, high)`.
VIX_EDGES = (15.0, 20.0, 25.0)
#: VIX/VIX3M: under the first is contango, under the second flat, at or above it backwardation.
TERM_EDGES = (0.97, 1.0)
TERM_BUCKETS = ("contango", "flat", "backwardation")
UNMEASURED = "unmeasured"


def _vix_label(value: float) -> str:
    lo = None
    for edge in VIX_EDGES:
        if value < edge:
            return f"<{edge:g}" if lo is None else f"{lo:g}-{edge:g}"
        lo = edge
    return f">={VIX_EDGES[-1]:g}"


_VIX_BUCKETS = tuple(_vix_label(v) for v in (0.0, *VIX_EDGES))


def _market_value(r: Mapping, name: str) -> tuple[bool, float | None]:
    """(joined, value) for one market figure the record was entered into. Not joined at all means
    the record is not bucketable; joined but unmeasured or refused is a value of None."""
    sample = r.get("regime")
    if not isinstance(sample, Mapping):
        return False, None
    if sample.get("status") != "measured":
        return True, None
    return True, sample.get(name)


def vix(r: Mapping) -> str | None:
    joined, value = _market_value(r, "vix")
    if not joined:
        return None
    return UNMEASURED if value is None else _vix_label(float(value))


def term(r: Mapping) -> str | None:
    joined, value = _market_value(r, "vix_vix3m_ratio")
    if not joined:
        return None
    if value is None:
        return UNMEASURED
    for edge, name in zip(TERM_EDGES, TERM_BUCKETS[:-1], strict=True):
        if value < edge:
            return name
    return TERM_BUCKETS[-1]


DIMENSIONS: dict[str, Callable[[Mapping], str | None]] = {
    "weekday": weekday,
    "hour": hour,
    "month": month,
    "event": event,
}

#: Present in a reading only when its records were joined to the regime (`attach_regime`).
REGIME_DIMENSIONS: dict[str, Callable[[Mapping], str | None]] = {
    "vix": vix,
    "term": term,
}


def _entry_ts(entry_time) -> float | None:
    if not entry_time:
        return None
    try:
        t = datetime.fromisoformat(str(entry_time))
    except ValueError:
        return None
    return t.replace(tzinfo=_ET).timestamp() if t.tzinfo is None else t.timestamp()


def attach_regime(records: Sequence[dict], markets_at: Callable[[list[float]], list[Mapping]]) -> int:
    """Join each record's entry time to the recorded market and keep the two figures the regime
    dimensions read, as `r["regime"]`. `markets_at` is `core.regime.markets_at` (injected, so this
    stays a function over what it is handed). A record with no readable entry time gets no
    `regime` key. Returns how many records were joined."""
    stamped = [(r, ts) for r in records if (ts := _entry_ts(r.get("entry_time"))) is not None]
    samples = markets_at([ts for _, ts in stamped]) if stamped else []
    for (r, _), m in zip(stamped, samples, strict=True):
        if m.get("status") != "measured":
            r["regime"] = {"status": UNMEASURED, "reason": m.get("reason")}
            continue
        vix_reading = (m.get("readings") or {}).get("vix") or {}
        r["regime"] = {
            "status": "measured",
            "vix": vix_reading.get("value") if vix_reading.get("usable") else None,
            "vix_vix3m_ratio": (m.get("derived") or {}).get("vix_vix3m_ratio"),
        }
    return len(stamped)


def _order(dimension: str, name: str) -> tuple:
    if name == UNMEASURED:
        return (99, name)
    if dimension == "weekday":
        return (_WEEKDAYS.index(name) if name in _WEEKDAYS else 99, name)
    if dimension == "event":
        return ({"none": 0, "unknown": 2}.get(name, 1), name)
    if dimension == "vix":
        return (_VIX_BUCKETS.index(name) if name in _VIX_BUCKETS else 98, name)
    if dimension == "term":
        return (TERM_BUCKETS.index(name) if name in TERM_BUCKETS else 98, name)
    return (0, name)


def table(records: Sequence[Mapping], key: Callable[[Mapping], str | None], dimension: str = "") -> dict:
    """One breakdown: a row per bucket, and how many records had no bucket (`skipped`)."""
    groups: dict[str, list[Mapping]] = {}
    skipped = 0
    for r in records:
        if r.get("net_pnl") is None:
            continue
        name = key(r)
        if name is None:
            skipped += 1
            continue
        groups.setdefault(name, []).append(r)
    rows = []
    for name, rs in groups.items():
        nets = [r["net_pnl"] for r in rs]
        sessions = len({r.get("session") for r in rs if r.get("session")})
        rows.append(
            {
                "bucket": name,
                "trades": len(nets),
                "sessions": sessions,
                "net": round(sum(nets), 2),
                "win_rate": round(sum(1 for v in nets if v > 0) / len(nets), 4),
                "avg_net": round(sum(nets) / len(nets), 2),
                "best": round(max(nets), 2),
                "worst": round(min(nets), 2),
                "thin": sessions < MIN_SESSIONS,
            }
        )
    rows.sort(key=lambda row: _order(dimension, row["bucket"]))
    return {"rows": rows, "skipped": skipped}


def breakdowns(records: Sequence[Mapping]) -> dict:
    """Every dimension's table for one arm's records, and the thin threshold they were marked at.
    The regime dimensions are included only where the records were joined to the regime, so a
    module that cuts its own (flies, MEIC) carries no empty regime table."""
    dims = dict(DIMENSIONS)
    if any("regime" in r for r in records):
        dims.update(REGIME_DIMENSIONS)
    return {
        "min_sessions": MIN_SESSIONS,
        **{name: table(records, key, name) for name, key in dims.items()},
    }
