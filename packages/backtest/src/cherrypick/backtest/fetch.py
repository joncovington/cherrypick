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
- **Daily closes**: the index's end-of-day close, source `thetadata:index_eod`.

A session is written whole or not at all: its rows and its `vendor_sessions` row commit together, and a
re-fetch clears the session first. A refusal writes only the `vendor_sessions` row, with the reason.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET
from cherrypick.core.streamcache import usable_quote

from . import greeks, store
from .vendor import VendorRefusal

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


def _dataset(settings: dict, symbol: str) -> dict:
    for d in settings["datasets"]:
        if d["symbol"].upper() == symbol.upper():
            return d
    raise ValueError(f"no dataset {symbol!r} in the backtest config")


def fetch(
    settings: dict, client, symbol: str, start: str, end: str, *, refetch: bool = False, log=print
) -> dict:
    """Fetch every 0DTE session of `symbol`'s dataset in [start, end] not already complete. Requests run
    `max_concurrent_requests` at a time; writes happen here, one session per transaction."""
    ds = _dataset(settings, symbol)
    if int(ds.get("dte", 0)) != 0:
        raise ValueError("only 0DTE datasets are built so far")
    st = settings["store"]
    root, cadence, vendor = ds["root"], float(st["cadence_seconds"]), settings["vendor"]["name"]
    conn = store.connect(settings, symbol)
    try:
        days = session_days(client.expirations(root), max(start, ds["from"]), end)
        todo = [d for d in days if refetch or store.session_status(conn, symbol, d) != store.COMPLETE]
        if days:
            prior = _cal.previous_trading_day(date.fromisoformat(days[0])).isoformat()
            closes = client.index_closes(symbol, prior, days[-1])
            conn.executemany(
                "INSERT OR IGNORE INTO daily_closes (symbol, trade_date, close, recorded_at, source) "
                "VALUES (?,?,?,?,?)",
                [
                    (symbol.upper(), d, c, time.time(), f"{vendor}:index_eod")
                    for d, c in sorted(closes.items())
                ],
            )
            conn.commit()

        def pull(day: str):
            hh, mm = _cal.session_close_hhmm(date.fromisoformat(day)).split(":")
            rows = client.greeks(
                root,
                day,
                interval=f"{int(cadence)}s",
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
        workers = max(1, int(settings["vendor"]["max_concurrent_requests"]) // 2)  # two requests a session
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(pull, d): d for d in todo}
            for fut in as_completed(futures):
                day = futures[fut]
                try:
                    rows, oi = fut.result()
                    built = build_session(rows, oi, day=day, each_side=int(st["strikes_each_side"]))
                    res = write_session(
                        conn, built, symbol=symbol, day=day, vendor=vendor, root=root, cadence=cadence
                    )
                except VendorRefusal as refusal:
                    record_refusal(conn, symbol=symbol, day=day, vendor=vendor, root=root, refusal=refusal)
                    res = {"status": store.REFUSED, "reason": refusal.reason}
                conn.commit()
                report["fetched" if res["status"] == store.COMPLETE else "refused"] += 1
                log(f"{day} {res}")
        report["coverage"] = store.coverage(conn, symbol)
        return report
    finally:
        conn.close()
