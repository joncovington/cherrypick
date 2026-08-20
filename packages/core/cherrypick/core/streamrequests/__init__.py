"""The consumer side of the streamer's subscription registry.

Every module that reads the shared stream cache declares what it needs by writing one file,
``<state>/stream_requests/<module>.json``; the standalone streamer (``packages/streamer``) reads the
union across every file and streams exactly that. This module owns the WRITE — the path convention, the
symbol cleaning, and the atomic write-then-rename (so a concurrent reader in the streamer never sees a
partial file) — and the ``union_*`` READ. It consolidates three byte-similar per-module writers (flies,
gex, meic) that each carried a "candidate to consolidate into cherrypick.core.streamrequests" note —
this is that module.

The union read lives here rather than in the streamer because two packages consume it and must not
disagree: the streamer unions the files to decide what to *subscribe*, and the orchestrator unions the
same files to decide whether a running producer's subscription set has gone stale (a producer binds its
underlyings once, at startup). Two implementations of "what did every module ask for" would recycle the
producer on a difference it does not actually see, or miss one it does.

Consumers keep a thin ``stream_request.py`` adapter for their module name, logger, and (MEIC) their
``leg_sources`` spec; the adapter's ``register(config)`` stays best-effort — a failed write must never
break a paper loop or a CLI read, because an unregistered symbol is a data-availability problem the
readers already surface, not a reason to crash.

Payload shape (see ``packages/streamer/src/registry.py``, the reader):
  - ``symbols``: underlyings to stream (spot + ATM window + GEX + opening range).
  - ``legs``: optional explicit static extra streamer-symbols.
  - ``leg_sources``: ``{"db": path, "query": select}`` specs — the streamer opens each DB read-only and
    re-runs the query every subscription poll, treating each non-null result cell as an extra symbol to
    keep subscribed beyond the ATM window (how MEIC keeps its open IC legs fresh).
  - ``window_hints``: optional ``{symbol: strike_count}`` — a module's request for a WIDER-than-default
    per-symbol ATM window (e.g. flies escalating after repeated ``missing_leg_quotes`` refusals). The
    streamer takes the max hint per symbol across every module's file, so one module's need is never
    narrowed by another's silence on that symbol. Absent/empty is the common case (accept the default).
  - ``expirations``: optional ``{symbol: [ISO dates]}`` — extra option expirations a module needs chain
    metadata and an ATM quote window for, beyond the nearest expiration the producer serves by default
    (e.g. a weekly calendar's 4DTE short / 7DTE long legs). Unioned per symbol across every module's
    file; the streamer re-reads the union every window pass, so a newly requested date is served with no
    restart. Dates already past (ET) are dropped at union time, so a file nobody rewrote over a weekend
    cannot pin dead subscriptions.
  - ``history_days``: optional ``{symbol: days}`` — how many COMPLETED daily OHLC rows the module needs
    ``stream_summary`` to hold for a symbol (e.g. pmcc's Keltner channel needing ~40 sessions of bars).
    The producer backfills a deficit once from DXLink daily candles — filling only dates the live
    Summary feed has not written, never overwriting a row — so a newly requested symbol's indicator
    history exists on day one instead of accruing over a month of sessions. Max per symbol across every
    module's file, same reasoning as ``window_hints``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import home as _home

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core.clock import ET as _ET


def requests_dir() -> Path:
    """The directory holding one request file per module. Not created — readers must tolerate its
    absence (a suite with no stream consumers installed has no directory, not an error)."""
    return _home.state_dir() / "stream_requests"


def request_path(module: str) -> Path:
    """Where this module's request file lives (directory created if absent)."""
    return _home.ensure(requests_dir()) / f"{module}.json"


def clean_symbols(symbols) -> list[str]:
    """Deduped, uppercased, stripped, sorted — junk entries dropped rather than crashed on."""
    out: set[str] = set()
    for s in symbols or []:
        if isinstance(s, str) and s.strip():
            out.add(s.strip().upper())
    return sorted(out)


def clean_window_hints(window_hints) -> dict[str, int]:
    """Deduped/uppercased/validated ``{symbol: strike_count}`` — non-string symbols, non-positive or
    non-integer counts are dropped rather than crashed on, same posture as `clean_symbols`."""
    out: dict[str, int] = {}
    for symbol, count in (window_hints or {}).items():
        if isinstance(symbol, str) and symbol.strip() and isinstance(count, int) and count > 0:
            out[symbol.strip().upper()] = count
    return out


def clean_history_days(history_days) -> dict[str, int]:
    """Deduped/uppercased/validated ``{symbol: days}`` — same posture (and same shape) as
    `clean_window_hints`: junk symbols and non-positive or non-integer counts are dropped."""
    out: dict[str, int] = {}
    for symbol, days in (history_days or {}).items():
        if isinstance(symbol, str) and symbol.strip() and isinstance(days, int) and days > 0:
            out[symbol.strip().upper()] = days
    return out


def clean_expirations(expirations) -> dict[str, list[str]]:
    """Deduped/uppercased/validated ``{symbol: [ISO dates]}`` — non-string symbols, unparseable dates
    and empty lists are dropped rather than crashed on, same posture as `clean_symbols`. Dates come
    back normalized (``date.fromisoformat`` then ``.isoformat()``) and sorted, so two writers of the
    same calendar date can never disagree byte-wise."""
    out: dict[str, list[str]] = {}
    for symbol, dates in (expirations or {}).items():
        if not (isinstance(symbol, str) and symbol.strip()):
            continue
        cleaned: set[str] = set()
        for value in dates if isinstance(dates, (list, tuple)) else []:
            if not isinstance(value, str):
                continue
            try:
                cleaned.add(date.fromisoformat(value.strip()).isoformat())
            except ValueError:
                continue
        if cleaned:
            out[symbol.strip().upper()] = sorted(cleaned)
    return out


def write_request(
    module: str, symbols, legs=(), leg_sources=(), window_hints=None, expirations=None, history_days=None
) -> Path:
    """Atomically (over)write a module's request file and return its path.

    Write-then-rename so a concurrent reader never sees a partial file. Raises on I/O failure —
    best-effort behavior (log and continue) belongs in the module's ``register()`` adapter, which
    knows its own logger.
    """
    path = request_path(module)
    payload = {
        "symbols": clean_symbols(symbols),
        "legs": [str(leg) for leg in legs],
        "leg_sources": [dict(source) for source in leg_sources],
        "window_hints": clean_window_hints(window_hints),
        "expirations": clean_expirations(expirations),
        "history_days": clean_history_days(history_days),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path


def read_all() -> list[dict]:
    """Every module's request payload, in filename order.

    A file that is missing, half-written, or corrupt is skipped rather than raised on: one bad request
    file must never take down the producer that reads it — nor the watchdog tick that checks it.
    """
    out: list[dict] = []
    directory = requests_dir()
    try:
        if not directory.is_dir():
            return out
        files = sorted(directory.glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def union_symbols(seed_symbols=None) -> list[str]:
    """Underlyings to stream: every module's ``symbols`` plus the operator's configured seed."""
    symbols: set[str] = set(clean_symbols(seed_symbols))
    for data in read_all():
        symbols.update(clean_symbols(data.get("symbols")))
    return sorted(symbols)


def union_window_hints() -> dict[str, int]:
    """Per-symbol widened ATM windows: the MAX hint per symbol across every module's file, so one
    module's need is never narrowed by another module's silence on that symbol."""
    hints: dict[str, int] = {}
    for data in read_all():
        for symbol, count in clean_window_hints(data.get("window_hints")).items():
            hints[symbol] = max(hints.get(symbol, 0), count)
    return hints


def union_history_days() -> dict[str, int]:
    """Per-symbol daily-history need: the MAX days per symbol across every module's file, so one
    module's need is never narrowed by another module's silence on that symbol. Deliberately absent
    from `subscription_snapshot` — the engine re-reads this and backfills with no restart, so a
    changed request must never look like a reason to recycle a healthy producer."""
    out: dict[str, int] = {}
    for data in read_all():
        for symbol, days in clean_history_days(data.get("history_days")).items():
            out[symbol] = max(out.get(symbol, 0), days)
    return out


def union_expirations(*, today: date | None = None) -> dict[str, list[str]]:
    """Per-symbol extra expirations: the set union across every module's file, with dates already past
    (ET) dropped — a request file nobody rewrote over a weekend must not pin dead subscriptions. The
    streamer's engine re-reads this every window pass, so growth is served with no restart.

    ``today`` is injectable for tests; the default is the current ET calendar date (a date is dropped
    only once it is over — an expiration IS its own last valid day)."""
    cutoff = (today or datetime.now(tz=_ET).date()).isoformat()
    out: dict[str, set[str]] = {}
    for data in read_all():
        for symbol, dates in clean_expirations(data.get("expirations")).items():
            out.setdefault(symbol, set()).update(d for d in dates if d >= cutoff)
    return {symbol: sorted(dates) for symbol, dates in out.items() if dates}


def subscription_snapshot(seed_symbols=None) -> dict:
    """The half of the registry union that only a producer **restart** can change.

    A producer binds its underlyings once, when it builds its streamer, and sizes each symbol's ATM
    window at the same time — so a new symbol or a widened hint reaches the file and never the running
    process. ``legs``/``leg_sources`` are deliberately absent: the engine re-reads those every
    subscription poll, so a position opening or closing is served with no restart and must not be made
    to look like a reason for one. ``expirations`` are absent for the same reason — the engine re-reads
    the union every window pass, and a calendar module's request *rolls forward every week by design*,
    so tracking it here would recycle a healthy producer once a week for nothing.
    """
    return {"symbols": union_symbols(seed_symbols), "window_hints": union_window_hints()}
