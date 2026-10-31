"""Fetch vendor sessions into the store. The client does the network; everything here is pure over
the rows it returns, so it is tested with a fake client and no vendor.

How each recorder field is rebuilt from the vendor (the declared rules; validation measures each):

- **Samples and quotes**: the vendor's first-order greeks at the store's cadence, which carry the
  NBBO bid and ask at each instant. Each sample keeps the `strikes_each_side` strikes nearest its own
  spot on each side (the recorder's rule) and only quotes `core.streamcache.usable_quote` accepts.
  `age_s` is NULL: an interval row is the quote standing at that instant, and its age is not
  reported. The replay treats NULL as fresh; validation measures what that costs.
- **Delta**: the vendor's, at the same instant. NULL where its IV solver failed (IV reported 0).
- **Spot** (`gex_spot_history`): the vendor's underlying price on the same rows. The index tier is
  separate (and, on this account, end-of-day only).
- **Profile** (`gex_profile_history`), every `PROFILE_SECONDS` from the open: open interest from the
  vendor's daily OI, gamma computed from the vendor's IV (`greeks.bs_gamma`; gamma is not in the
  Standard tier), and IV in percent, the recorder's unit (the vendor reports a fraction). Volume is
  not fetched and stays NULL.
- **Regime rows** (`gex_regime_history`): derived from the stored profile by the recorder's own core
  functions (`regimes.py`), so flies' `wall-clear` gate reads walls on a vendor session too.
- **Daily closes**: the index's end-of-day close, source `thetadata:index_eod`, where the account's
  index tier allows it (a free index tier reaches only recent years). Elsewhere, the session's last
  underlying price before the close, source `thetadata:last_underlying`: within a fraction of a
  point of the print, and labelled so a reader can tell which it has.

A session is written whole or not at all: its rows and its `vendor_sessions` row commit together, and a
re-fetch clears the session first. A refusal writes only the `vendor_sessions` row, with the reason.
"""

from __future__ import annotations

import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET
from cherrypick.core.streamcache import usable_quote

from . import greeks, regimes, store
from .vendor import FATAL, VendorRefusal

#: The recorder's profile cadence (~5 minutes).
PROFILE_SECONDS = 300
OPEN = (9, 30)


def nearest_strikes(strikes, spot: float, each_side: int) -> list[float]:
    """The `each_side` strikes below spot and the `each_side` at or above it: the recorder's rule
    (gex `provider.nearest_strikes`, which this package may not import)."""
    distinct = sorted({float(k) for k in strikes if k is not None})
    below = [k for k in distinct if k < spot][-each_side:] if each_side else []
    return below + [k for k in distinct if k >= spot][:each_side]


def session_days(expirations: list[str], start: str, end: str) -> list[str]:
    """Trading days in [start, end] on which the root has a same-day expiration (0DTE sessions)."""
    have = set(expirations)
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= last:
        if _cal.is_trading_day(d) and d.isoformat() in have:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _finite(v) -> bool:
    return v is not None and math.isfinite(v)


def _at(day: str, hh: int, mm: int) -> float:
    return datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=ET).timestamp()


def build_session(rows: list[dict], oi: dict, *, day: str, each_side: int) -> dict:
    """The recorder-shaped content of one session from the vendor's rows. Pure."""
    hh, mm = (int(x) for x in _cal.session_close_hhmm(date.fromisoformat(day)).split(":"))
    open_ts, close_ts = _at(day, *OPEN), _at(day, hh, mm)
    by_ts: dict[float, list[dict]] = {}
    for r in rows:
        if open_ts <= r["ts"] < close_ts:
            by_ts.setdefault(r["ts"], []).append(r)
    samples, spots, profiles = [], [], []
    for ts in sorted(by_ts):
        group = by_ts[ts]
        spot = next((r["underlying"] for r in group if r["underlying"]), None)
        if not spot:
            continue  # no spot, no sample: the recorder writes none either
        spots.append((ts, spot))
        keep = set(nearest_strikes((r["strike"] for r in group), spot, each_side))
        quotes = []
        for r in group:
            if r["strike"] not in keep or r["right"] not in store.SIDES:
                continue
            # NaN passes every comparison usable_quote makes; a non-finite side is no quote.
            if not (_finite(r["bid"]) and _finite(r["ask"])):
                continue
            q = usable_quote(
                {"bid": r["bid"], "ask": r["ask"], "updated_at": ts, "mid": None}, ts, float("inf")
            )
            if q is None:
                continue
            delta = r["delta"] if r["iv"] and r["iv"] > 0 else None
            quotes.append((store.SIDES[r["right"]], r["strike"], q["bid"], q["ask"], q["mid"], None, delta))
        if quotes:
            samples.append((ts, quotes))
        if (ts - open_ts) % PROFILE_SECONDS == 0:
            t_years = greeks.year_fraction(close_ts - ts)
            per_strike: dict[float, dict] = {}
            for r in group:
                side = "call" if r["right"] == "CALL" else "put"
                iv = r["iv"] if r["iv"] and r["iv"] > 0 else None
                row = per_strike.setdefault(r["strike"], {})
                # The recorder stores IV in percent to two places (core.gex.compute_gex_profile);
                # the vendor reports a fraction. Gamma is computed from the fraction.
                row[f"{side}_iv"] = round(iv * 100.0, 2) if iv is not None else None
                row[f"{side}_gamma"] = greeks.bs_gamma(spot, r["strike"], iv, t_years)
                row[f"{side}_oi"] = oi.get((r["right"], r["strike"]))
            profiles.append((ts, per_strike))
    return {"samples": samples, "spots": spots, "profiles": profiles}


def write_session(
    conn, built: dict, *, symbol: str, day: str, vendor: str, root: str, cadence: float
) -> dict:
    sym = symbol.upper()
    store.clear_session(conn, sym, day)
    n_quotes = 0
    for ts, quotes in built["samples"]:
        sid = conn.execute(
            "INSERT INTO gex_quote_samples (symbol, trade_date, ts, expiration) VALUES (?,?,?,?)",
            (sym, day, ts, day),
        ).lastrowid
        conn.executemany(
            "INSERT INTO gex_quotes (sample_id, side, strike, bid, ask, mid, age_s, delta) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(sid, *q) for q in quotes],
        )
        n_quotes += len(quotes)
    conn.executemany(
        "INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)",
        [(sym, day, ts, spot) for ts, spot in built["spots"]],
    )
    cols = ("call_oi", "put_oi", "call_vol", "put_vol", "call_gamma", "put_gamma", "call_iv", "put_iv")
    conn.executemany(
        f"INSERT INTO gex_profile_history (symbol, trade_date, ts, strike, {', '.join(cols)}) "
        f"VALUES (?,?,?,?,{','.join('?' * len(cols))})",
        [
            (sym, day, ts, strike, *(row.get(c) for c in cols))
            for ts, per_strike in built["profiles"]
            for strike, row in sorted(per_strike.items())
        ],
    )
    meta = {"samples": len(built["samples"]), "quotes": n_quotes, "profiles": len(built["profiles"])}
    status, reason = (store.COMPLETE, None) if built["samples"] else (store.REFUSED, "no_usable_samples")
    conn.execute(
        "INSERT INTO vendor_sessions (symbol, trade_date, status, reason, vendor, root, expiration, "
        "cadence_seconds, samples, quotes, profiles, fetched_at, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sym,
            day,
            status,
            reason,
            vendor,
            root,
            day,
            cadence,
            meta["samples"],
            meta["quotes"],
            meta["profiles"],
            time.time(),
            None,
        ),
    )
    return {"status": status, **meta}


def record_refusal(conn, *, symbol: str, day: str, vendor: str, root: str, refusal: VendorRefusal) -> None:
    store.clear_session(conn, symbol, day)
    conn.execute(
        "INSERT INTO vendor_sessions (symbol, trade_date, status, reason, vendor, root, fetched_at, detail) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (symbol.upper(), day, store.REFUSED, refusal.reason, vendor, root, time.time(), refusal.detail[:500]),
    )


def _write_closes(conn, symbol: str, closes: dict, source: str) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO daily_closes (symbol, trade_date, close, recorded_at, source) "
        "VALUES (?,?,?,?,?)",
        [(symbol.upper(), d, c, time.time(), source) for d, c in sorted(closes.items())],
    )


def _dataset(settings: dict, symbol: str) -> dict:
    for d in settings["datasets"]:
        if d["symbol"].upper() == symbol.upper():
            return d
    raise ValueError(f"no dataset {symbol!r} in the backtest config")


#: The vendor's interval names for the cadences the store uses (a minute is "1m", never "60s").
_INTERVALS = {60: "1m", 300: "5m"}


def interval_name(cadence: float) -> str:
    c = int(cadence)
    return _INTERVALS.get(c, f"{c}s")


def fetch(
    settings: dict,
    client,
    symbol: str,
    start: str,
    end: str,
    *,
    refetch: bool = False,
    cadence: float | None = None,
    workers: int = 1,
    stop=None,
    log=print,
) -> dict:
    """Fetch every 0DTE session of `symbol`'s dataset in [start, end] not already complete, at
    `cadence` (default the store's). `workers` sessions are pulled at a time, never more than
    `max_concurrent_requests`: a session's two requests (greeks, then open interest) run one after
    the other, so each holds one slot. Past the limit the vendor queues a request rather than refusing
    it (its Concurrent Requests page). Writes happen here, one session per transaction.
    A fatal refusal (`vendor.FATAL`) stops the fetch rather than recording every remaining session
    as refused. `stop`, asked as each session lands, cancels the sessions not yet pulled; those in
    flight are still written, and the report says `stopped` so a queued fetch resumes later.

    One worker by default; two is measured to work, and four (Standard's limit) runs the wide store's
    fetch from 2026-10-31. What first looked like the vendor stalling under
    concurrency (2026-10-17) was this loop: a session that failed to write ended it, and the pool
    then pulled every queued day on its way out and wrote none of them. The loop now records such a
    session as `build_error` and goes on, and the pool cancels its queue whatever ends the loop."""
    ds = _dataset(settings, symbol)
    if int(ds.get("dte", 0)) != 0:
        raise ValueError("only 0DTE datasets are built so far")
    st = settings["store"]
    cadence = float(store.cadence_of(settings, cadence))
    root, vendor = ds["root"], settings["vendor"]["name"]
    if (PROFILE_SECONDS % int(cadence)) != 0:
        raise ValueError(f"cadence {cadence:g}s does not divide the {PROFILE_SECONDS}s profile cadence")
    conn = store.connect(settings, symbol, cadence)
    if hasattr(client, "on_retry"):
        client.on_retry = log  # a stalled call and its reconnect show in the fetch's own log
    try:
        days = session_days(client.expirations(root), max(start, ds["from"]), end)
        todo = [d for d in days if refetch or store.session_status(conn, symbol, d) != store.COMPLETE]
        closes: dict = {}
        if days:
            prior = _cal.previous_trading_day(date.fromisoformat(days[0])).isoformat()
            try:
                closes = client.index_closes(symbol, prior, days[-1])
            except VendorRefusal as refusal:
                # The index tier is separate from the options tier; closes it refuses fall back to
                # each session's last underlying price below. A refusal here never stops the fetch.
                log(f"index closes refused ({refusal.reason}); using each session's last underlying price")
            _write_closes(conn, symbol, closes, f"{vendor}:index_eod")
            conn.commit()

        def pull(day: str):
            hh, mm = _cal.session_close_hhmm(date.fromisoformat(day)).split(":")
            rows = client.greeks(
                root,
                day,
                interval=interval_name(cadence),
                strike_range=int(st["request_strike_range"]),
                start_time="09:30:00",
                end_time=f"{hh}:{mm}:00",
            )
            return rows, client.open_interest(root, day)

        report = {
            "symbol": symbol.upper(),
            "sessions": len(days),
            "fetched": 0,
            "refused": 0,
            "skipped": len(days) - len(todo),
        }
        cap = max(1, int(settings["vendor"]["max_concurrent_requests"]))  # one request in flight a session
        pool = ThreadPoolExecutor(max_workers=max(1, min(int(workers), cap)))
        # Shut down cancelling what is queued, whatever ends the loop. A plain `with` waits for every
        # queued pull on the way out: an error writing one session once left a fetch pulling the
        # rest of history and writing none of it, which looked exactly like a stalled vendor.
        try:
            futures = {pool.submit(pull, d): d for d in todo}
            for fut in as_completed(futures):
                day = futures[fut]
                if report.get("aborted") or fut.cancelled():
                    continue
                if stop is not None and not report.get("stopped") and stop():
                    report["stopped"] = True
                    for other in futures:
                        other.cancel()
                    log(f"stop-by reached at {day}: the sessions not yet pulled wait for the next run")
                try:
                    rows, oi = fut.result()
                    built = build_session(rows, oi, day=day, each_side=int(st["strikes_each_side"]))
                    res = write_session(
                        conn, built, symbol=symbol, day=day, vendor=vendor, root=root, cadence=cadence
                    )
                    if res["status"] == store.COMPLETE:
                        res["regimes"] = regimes.derive(conn, symbol, day)
                    if day not in closes and built["spots"]:
                        # No index close for the day: the session's last underlying price, under its
                        # own source label so a reader can tell the two apart.
                        _write_closes(conn, symbol, {day: built["spots"][-1][1]}, f"{vendor}:last_underlying")
                except VendorRefusal as refusal:
                    if refusal.reason in FATAL:
                        report["aborted"] = {"reason": refusal.reason, "at": day, "detail": refusal.detail}
                        for other in futures:
                            other.cancel()
                        log(f"{day} fatal {refusal.reason}: stopping; nothing further is recorded")
                        continue
                    record_refusal(conn, symbol=symbol, day=day, vendor=vendor, root=root, refusal=refusal)
                    res = {"status": store.REFUSED, "reason": refusal.reason}
                except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                    # A session the vendor answered but that cannot be built or written: recorded
                    # against that session with what went wrong, and the fetch goes on.
                    conn.rollback()
                    bad = VendorRefusal("build_error", f"{type(exc).__name__}: {exc}")
                    record_refusal(conn, symbol=symbol, day=day, vendor=vendor, root=root, refusal=bad)
                    res = {"status": store.REFUSED, "reason": bad.reason, "detail": bad.detail}
                conn.commit()
                report["fetched" if res["status"] == store.COMPLETE else "refused"] += 1
                log(f"{day} {res}")
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        report["coverage"] = store.coverage(conn, symbol)
        return report
    finally:
        conn.close()
