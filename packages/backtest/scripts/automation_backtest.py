"""Backtest automations through the engine paper runs, over a stored history.

    python packages/backtest/scripts/automation_backtest.py run --arms iron-fly-1000,long-put-1030 \\
        --symbol SPX --name my-run --from 2026-10-13 --to 2026-11-13 [--source recorded-0dte]

The replay is `cherrypick.automations.driver` (the engine, the snapshot builder and paper's own arm
stacking), so a backtest's entries, exits, gates and fills are paper's. This script chooses the data and
writes the result:

- **The store** comes from the source registry (`cherrypick.core.sources`): a same-day-expiry chain for
  the symbol over the dates, the source that covers them, or the one named by `--source`. A vendor
  source is refused unless the licence is active (`licensing.status`): new work never reads the
  vendor's data after its subscription ends.
- **Daily bars** for a daily signal come from the recorder's index sessions (`index-sessions`).
- **The result** is descriptive (an explore run: no trial, no verdict) and goes where its inputs allow:
  under `results/automations/` when it read the vendor's store (licensed, purged with it), beside it
  otherwise (`paths.automations_dir`). Never into the repo.

Same-session arms read the recorder-schema stores through the day. Others read our own end-of-day
chains (Dolt) where they cover; else, while licensed, the full set's stores through the session
(`intraday_loader`, for its seven underlyings out to 90 days); else the vendor's end-of-day chains at
one tick a session, 15:59, where an arm whose entry is not at the close counts `needs_intraday`. Daily
bars come from the stock history (raw), or the recorder's index sessions for an index, with Cboe's
closes (SPX) and the same-day store's filling the sessions they lack on the intraday replay.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from cherrypick.automations import arms as ens_arms
from cherrypick.automations import config as ens_config
from cherrypick.automations import driver, enrolment, events, history
from cherrypick.core import chaindelta, home, jsonio, progress, volindex
from cherrypick.core import sources as src
from cherrypick.core.clock import ET
from cherrypick.core.sources import inventory as src_inventory

from cherrypick.backtest import fullset, licensing, paths, settings


class Refused(Exception):
    pass


def with_closes(bars: list[dict], closes: dict[str, float]) -> list[dict]:
    """Daily bars with a close-only bar for each session they lack that `closes` holds (an index the
    stock history does not carry, its closes kept by the same-day store)."""
    have = {b["session"] for b in bars}
    extra = [{"session": d, "open": None, "high": None, "low": None, "close": c}
             for d, c in closes.items() if d not in have]  # fmt: skip
    return sorted([*bars, *extra], key=lambda b: b["session"])


def store_path(source: src.Source, settings_doc: dict) -> Path:
    """The file a recorder-schema source reads: our recorder's under the data root, the vendor's in its
    licensed directory."""
    if source.probe.backend == "store_cache":
        return paths.vendor_dir(settings_doc) / source.probe.where
    return home.data_dir() / source.probe.where


def choose_source(
    symbol: str, start: str, end: str, named: str | None, *, licensed_ok: bool, inv: dict
) -> src.Source:
    """The 0DTE chain source for the run: `named`, or the registry's best cover. A licensed source only
    while the licence allows new work; one the registry cannot cover is refused with its reason."""
    if named:
        s = src.by_name(named)
        if s.licensed and not licensed_ok:
            raise Refused(f"{named} is the vendor's and the licence does not allow new work")
        if "0dte" not in s.expiries or s.probe.backend not in ("sqlite", "store_cache"):
            raise Refused(f"{named} is not a same-day chain store in the recorder's schema")
        return s
    cov = src.coverage(inv, "chain", symbol, start, end, expiry="0dte", licensed=licensed_ok, min_density=0.0)
    if not cov["best"]:
        why = "; ".join(f"{x['source']}: {x['why']}" for x in cov["sources"]) or "no source holds it"
        raise Refused(f"no store covers {symbol} 0DTE chains {start}..{end} ({why})")
    return src.by_name(cov["best"])


def index_daily(symbol: str) -> list[dict]:
    """The recorder's daily OHLC sessions for `symbol` (the `index-sessions` source), oldest first."""
    db = home.data_dir("gex") / "gex_history.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT trade_date, day_open, day_high, day_low, day_close FROM daily_sessions WHERE symbol = ? "
            "ORDER BY trade_date",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return [{"session": d, "open": o, "high": h, "low": lo, "close": c} for d, o, h, lo, c in rows]


#: The end-of-day chain sources a multi-day automation reads (one tick a session, at 15:59).
EOD_SOURCES = ("dolt-chains", "vendor-eod-chains")


def same_day(arms: dict[str, dict]) -> bool:
    """Every arm expires the session it enters: the recorder-schema stores price it through the day."""
    return all(a["expiry"]["kind"] == "dte" and a["expiry"]["days"] == 0 for a in arms.values())


def choose_eod_source(
    symbol, start, end, named, *, licensed_ok: bool, inv: dict, min_density: float
) -> src.Source:
    """The end-of-day chain source for a multi-day run: `named`, or the first that covers the dates at
    `min_density` (Dolt's chains are about three days a week before 2025), the vendor's only while the
    licence allows new work."""
    if named:
        if named not in EOD_SOURCES:
            raise Refused(f"{named} is not an end-of-day chain source ({', '.join(EOD_SOURCES)})")
        s = src.by_name(named)
        if s.licensed and not licensed_ok:
            raise Refused(f"{named} is the vendor's and the licence does not allow new work")
        return s
    cov = src.coverage(
        inv, "chain", symbol, start, end, cadence="1d", licensed=licensed_ok, min_density=min_density
    )
    for x in cov["sources"]:
        if x["source"] in EOD_SOURCES and x["covers"]:
            return src.by_name(x["source"])
    why = "; ".join(f"{x['source']}: {x['why']}" for x in cov["sources"] if x["source"] in EOD_SOURCES)
    raise Refused(f"no end-of-day chains cover {symbol} {start}..{end} ({why or 'no source holds it'})")


def intraday_parts(arms: dict[str, dict]) -> list[str] | None:
    """The full-set stores an intraday replay of every arm reads (`enrolment.intraday_parts`): the
    widest any arm needs, or None when one of them has no intraday replay."""
    every = [enrolment.intraday_parts(a) for a in arms.values()]
    if not every or any(p is None for p in every):
        return None
    return max(every, key=len)


def choose_multi_day(
    symbol, start, end, named, arms, *, licensed_ok: bool, inv: dict, min_density: float
) -> tuple[str, list[src.Source]]:
    """The replay for a multi-day run, as the backtest suggestion orders it: our own end-of-day chains
    where they cover, else the full set's stores through the session (`intraday`, licensed), else the
    vendor's end-of-day chains. `named` pins it: an end-of-day source, or a full-set store, which
    stands for its whole replay. Returns ("intraday" | "eod", [sources])."""
    parts = intraday_parts(arms)
    if named and named.startswith(("vendor-near-", "vendor-mid-")):
        if not licensed_ok:
            raise Refused(f"{named} is the vendor's and the licence does not allow new work")
        if not parts or named != parts[0]:
            raise Refused(f"{named} is not the intraday replay these automations read ({parts and parts[0]})")
        return "intraday", [src.by_name(p) for p in parts]
    if named:
        return "eod", [choose_eod_source(symbol, start, end, named, licensed_ok=licensed_ok, inv=inv,
                                         min_density=min_density)]  # fmt: skip
    try:
        return "eod", [choose_eod_source(symbol, start, end, None, licensed_ok=False, inv=inv,
                                         min_density=min_density)]  # fmt: skip
    except Refused:
        if not licensed_ok:
            raise
    if parts:
        widest = max(arms.values(), key=lambda a: len(enrolment.intraday_parts(a)))
        cover = enrolment.intraday_cover(widest, inv, start, end, src.coverage)
        if cover and cover["covered"]:
            return "intraday", [src.by_name(p) for p in parts]
    return "eod", [choose_eod_source(symbol, start, end, None, licensed_ok=True, inv=inv,
                                     min_density=min_density)]  # fmt: skip


def _minute(bar: str) -> int:
    h, m = bar.split(":")
    return int(h) * 60 + int(m)


def intraday_loader(chain_dbs: list[Path], zero_db: Path | None, symbol: str, root: str):
    """The loaders `history.IntradayChainHistory` reads, over the full set's intraday stores
    (`intraday_chain_quotes`, rights spelt `CALL`/`PUT`, bars "HH:MM") and its same-day store (the
    recorder's schema, for an expiry on its own day and the session's spot). An expiry two stores hold
    is read from the finer (they are passed finest first). The spot, in order: the same-day store's
    trail; the rows' own underlying price, where the store kept one (a Standard-tier fetch); else each
    bar's parity forward on the nearest expiry (`core.chaindelta`; Value-tier rows carry no underlying).
    Returns (expiries, quotes, spot, sessions, closes): `closes` are the same-day store's daily closes."""
    conns = [sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True) for p in chain_dbs if p.exists()]
    zero = history.StoreHistory(zero_db, symbol) if zero_db is not None and zero_db.exists() else None
    zero_days = set(zero.sessions) if zero else set()

    def expiries(session: str) -> list[str]:
        out = {e for c in conns for (e,) in c.execute(
            "SELECT DISTINCT expiration FROM intraday_chain_quotes WHERE symbol = ? AND session = ?",
            (root, session))}  # fmt: skip
        if session in zero_days:
            out.add(session)
        return sorted(out)

    def quotes(session: str, expiry: str) -> dict[int, list[dict]]:
        if expiry == session:
            return (
                {m: rows for m in zero.minutes(session) if (rows := zero.rows(session, expiry, m))}
                if zero
                else {}
            )
        for c in conns:
            by: dict[int, list[dict]] = {}
            for bar, k, right, bid, ask, delta, iv in c.execute(
                "SELECT bar, strike, right, bid, ask, delta, iv FROM intraday_chain_quotes "
                "WHERE symbol = ? AND session = ? AND expiration = ?",
                (root, session, expiry),
            ):
                r = "C" if str(right).upper().startswith("C") else "P"
                by.setdefault(_minute(bar), []).append(
                    {"strike": float(k), "right": r, "bid": bid, "ask": ask, "delta": delta, "iv": iv}
                )
            if by:
                return by
        return {}

    def spot(session: str) -> tuple[dict[int, float], str | None]:
        if session in zero_days:
            trail = dict(zero.trail(session, 24 * 60))
            if trail:
                return trail, "same-day store"
        nearest = next((e for e in expiries(session) if e > session), None)
        if nearest is None:
            return {}, None
        for c in conns:
            own = dict(
                c.execute(
                    "SELECT bar, MIN(underlying) FROM intraday_chain_quotes WHERE symbol = ? AND session = ? "
                    "AND expiration = ? AND underlying IS NOT NULL GROUP BY bar",
                    (root, session, nearest),
                )
            )
            if own:
                return {_minute(b): float(u) for b, u in own.items()}, "row underlying"
        fwd = {
            m: f
            for m, rows in quotes(session, nearest).items()
            if (f := chaindelta.forward_from_parity(rows))
        }
        return fwd, ("parity forward" if fwd else None)

    held = set(zero_days)
    for c in conns:
        held |= {d for (d,) in c.execute(
            "SELECT DISTINCT session FROM intraday_chain_requests WHERE symbol = ? AND status = 'complete'",
            (root,))}  # fmt: skip
    return expiries, quotes, spot, sorted(held), (dict(zero.closes) if zero else {})


def volume_symbols(arms: dict) -> set[str]:
    return {s for a in arms.values() for s in ens_arms.volume_symbols(a)}


def check_volume(symbols, start, end, *, inv: dict, min_density: float) -> None:
    """Refuse a run whose declared volume symbols the recorded one-minute bars (`profile-bars`) do not
    cover: a session without them would only count `no_volume`."""
    for sym in sorted(symbols):
        cov = src.coverage(
            inv, "volume", sym, start, end, cadence="1m", licensed=False, min_density=min_density
        )
        hit = [x for x in cov["sources"] if x["source"] == "profile-bars"]
        if not (hit and hit[0]["covers"]):
            why = hit[0]["why"] if hit else "not in the inventory"
            raise Refused(f"no one-minute volume covers {sym} {start}..{end} (profile-bars: {why})")


def profile_volumes(symbol: str, db: Path | None = None) -> dict[str, dict[int, float]]:
    """{session: {minute: volume of the one-minute bar starting then}} for the regular session, from the
    recorded bars (`profile-bars`)."""
    db = db or home.data_dir("market-files") / "profile_bars.db"
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT et_day, et_time, volume FROM profile_bars WHERE symbol = ?", (symbol.upper(),)
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[int, float]] = {}
    for day, hhmm, vol in rows:
        h, m = (int(x) for x in hhmm.split(":"))
        minute = h * 60 + m
        if 570 <= minute < 960 and vol is not None:
            out.setdefault(day, {})[minute] = float(vol)
    return out


def stock_daily(symbol: str) -> list[dict]:
    """Raw daily OHLC from the stock history (`stock-history`): the prices option strikes are quoted
    against, never adjusted."""
    db = home.data_dir("technicals") / "history.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close FROM bars WHERE symbol = ? ORDER BY date", (symbol,)
        ).fetchall()
    finally:
        conn.close()
    return [{"session": d, "open": o, "high": h, "low": lo, "close": c} for d, o, h, lo, c in rows]


def _years(session: str, expiry: str) -> float:
    return max((date.fromisoformat(expiry) - date.fromisoformat(session)).days, 0) / 365.0


def vendor_eod_loader(db: Path, symbol: str):
    """The vendor's end-of-day store (`eod_chains.db`): each expiry's quotes on a session, with delta
    computed from the chain's own mids (`core.chaindelta`), the store keeping no greeks."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)

    def load(session: str) -> dict[str, list[dict]]:
        by_exp: dict[str, list[dict]] = {}
        for exp, k, right, bid, ask in conn.execute(
            "SELECT expiration, strike, right, bid, ask FROM eod_chain_quotes "
            "WHERE symbol = ? AND session = ?",
            (symbol, session),
        ):
            r = "C" if str(right).upper().startswith("C") else "P"
            by_exp.setdefault(exp, []).append({"strike": float(k), "right": r, "bid": bid, "ask": ask})
        return {
            e: chaindelta.with_computed_delta(rows, years=_years(session, e)) for e, rows in by_exp.items()
        }

    return load


def dolt_loader(symbol: str):
    """Dolt's `options.option_chain` (`dolt-chains`): each expiry's quotes on a session with the
    dataset's own delta and IV. Read a date at a time, the table's key leading with the date."""
    import mysql.connector

    conn = mysql.connector.connect(
        host="127.0.0.1", port=3306, user="root", database="options", connection_timeout=10
    )

    def load(session: str) -> dict[str, list[dict]]:
        cur = conn.cursor()
        cur.execute(
            "SELECT expiration, strike, call_put, bid, ask, delta, vol FROM option_chain "
            "WHERE date = %s AND act_symbol = %s",
            (session, symbol),
        )
        out: dict[str, list[dict]] = {}
        for exp, k, cp, bid, ask, delta, vol in cur.fetchall():
            out.setdefault(str(exp), []).append(
                {
                    "strike": float(k),
                    "right": "C" if str(cp).upper().startswith("C") else "P",
                    "bid": None if bid is None else float(bid),
                    "ask": None if ask is None else float(ask),
                    "delta": None if delta is None else float(delta),
                    "iv": None if vol is None else float(vol),
                }
            )
        return out

    return load


def summarise(book) -> dict:
    out = {}
    for name, why in sorted(book.why.items()):
        trades = [t for t in book.trades if t["arm"] == name]
        nets = [t["net"]["verdict"] for t in trades]
        out[name] = {
            "sessions": sum(why.values()),
            "trades": len(trades),
            "net": round(sum(nets), 2),
            "mean_net": round(sum(nets) / len(nets), 2) if nets else None,
            "reasons": dict(why),
        }
    return out


def cmd_run(args) -> int:
    s = settings.load(None)
    cfg = ens_config.load_config()
    every = ens_config.load_arms(cfg)
    names = [n.strip() for n in args.arms.split(",") if n.strip()]
    missing = [n for n in names if n not in every]
    if missing:
        print(json.dumps({"ok": False, "error": f"no arm {missing[0]!r}"}))
        return 1
    chosen = {n: {**every[n], "enabled": True} for n in names}
    symbol = args.symbol.upper()
    start, end = args.start, args.end
    licensed_ok, inv = licensing.status(s)["active"], src_inventory.load()
    replay, parts = "same_day", []
    try:
        if same_day(chosen):
            source = choose_source(symbol, start, end, args.source, licensed_ok=licensed_ok, inv=inv)
        else:
            replay, parts = choose_multi_day(symbol, start, end, args.source, chosen, licensed_ok=licensed_ok,
                                             inv=inv, min_density=args.min_density)  # fmt: skip
            source = parts[0]
        check_volume(volume_symbols(chosen), start, end, inv=inv, min_density=args.min_density)
    except Refused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    vix_daily = volindex.closes("VIX")
    volumes = {s: profile_volumes(s) for s in volume_symbols(chosen)}
    if same_day(chosen):
        store = store_path(source, s)
        h = history.StoreHistory(store, symbol, daily=index_daily(symbol))
    elif replay == "intraday":
        # Finest first, so an expiry both intraday stores hold is read at one minute.
        chain_dbs = [
            store_path(p, s) for p in sorted(parts[:-1], key=lambda p: src.CADENCE_SECONDS[p.cadence])
        ]
        zero_db = store_path(parts[-1], s)
        store = store_path(source, s)
        exps, quotes, spot, held, closes = intraday_loader(
            chain_dbs, zero_db, symbol, fullset.SPEC[symbol]["root"]
        )
        # An index's official closes from Cboe's file where there is one (SPX), over the same-day store's
        # (each 0DTE session's last underlying, and only on sessions with a same-day expiry).
        bars = with_closes(stock_daily(symbol) or index_daily(symbol), {**closes, **volindex.closes(symbol)})
        h = history.IntradayChainHistory(symbol, bars, expiries=exps, quotes=quotes, spot=spot,
                                         vix_daily=vix_daily, sessions=held)  # fmt: skip
    else:
        store = (
            store_path(source, s) if source.name == "vendor-eod-chains" else Path("dolt/options.option_chain")
        )
        loader = (
            vendor_eod_loader(store, symbol) if source.name == "vendor-eod-chains" else dolt_loader(symbol)
        )
        bars = stock_daily(symbol) or index_daily(symbol)
        h = history.DailyChainHistory(symbol, bars, loader, vix_daily=vix_daily)
    sessions = [d for d in h.sessions if start <= d <= end]
    calendar = events.load_calendar((cfg.get("events") or {}).get("path"))
    book = driver.Book()
    for session in progress.track(
        sessions,
        job="automation_backtest-run",
        phase="sessions",
        unit="days",
        key=lambda _: symbol,
        current=lambda d: d,
    ):
        book = driver.run(
            chosen, h, [session], events=calendar, costs=cfg, vix_daily=vix_daily, volumes=volumes, book=book
        )
    result = {
        "name": args.name,
        "kind": "explore",
        "symbol": symbol,
        "arms": chosen,
        "signal_versions": ens_config.signal_versions(cfg, names),
        "source": source.name,
        "store": store.relative_to(home.data_dir()).as_posix()
        if store.is_relative_to(home.data_dir())
        else store.as_posix(),
        "cadence": "1d at 15:59" if getattr(h, "close_only", False) else "stored minutes",
        "replay": replay,
        "licensed": source.licensed,
        "sessions": [sessions[0], sessions[-1]] if sessions else None,
        "session_count": len(sessions),
        "summary": summarise(book),
        "trades": book.trades,
        "still_open": len(book.open),
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
    }
    if replay == "intraday":
        result["stores"] = [p.probe.where for p in parts]
        # Where each session's spot came from (`intraday_loader`): a parity forward is not a print.
        result["spot_sources"] = dict(Counter(h.spot_sources.get(d, "none") for d in sessions))
    out = paths.automations_dir(source.licensed) / f"{args.name}.json"
    jsonio.write_json_atomic(out, result)
    print(json.dumps({"ok": True, "path": str(out), "source": source.name, "trades": len(book.trades)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="backtest arms over a stored history and write the result")
    rn.add_argument("--arms", required=True, help="comma-separated arm names (shipped or this machine's)")
    rn.add_argument("--symbol", required=True)
    rn.add_argument("--name", required=True, help="the result's file name")
    rn.add_argument("--source", default=None, help="a source registry name; default: the best that covers")
    rn.add_argument("--from", dest="start", required=True)
    rn.add_argument("--to", dest="end", required=True)
    rn.add_argument(
        "--min-density",
        type=float,
        default=0.95,
        help="end-of-day: accept a thinner source (Dolt before 2025)",
    )
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
