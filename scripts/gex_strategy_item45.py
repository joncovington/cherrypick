"""Item 45, a published GEX share strategy out of sample (docs/edge-hypotheses-plan.md): its inputs.

    python scripts/gex_strategy_item45.py plan [--cell pilot|P|N] [--out FILE]      # open-interest plan
    python scripts/gex_strategy_item45.py requests [--cell pilot|P|N] [--out FILE]  # chain requests
    python scripts/gex_strategy_item45.py bars [--force]                             # broker 15m candles
    python scripts/gex_strategy_item45.py pilot [--out FILE]                         # the bars' basis

`plan` writes the input of `fetch-oi`: every session of the cell for its names, plus the probe's
sessions for the pilot. `requests` reads the open-interest store for each (name, session)'s listed
expiries and writes the input of `fetch-intraday-chains`. Both write under the backtest data dir
(results/research/item45/), never into the repo: the inputs are licensed. Counts only are printed.

`bars` fetches the broker's regular-hours 15-minute candles for the pilot names from `BARS_FROM`
(read-only, the shared credential, one DXLink connection) into `~/.cherrypick/data/research/item45/
bars.db`. The feed's 15-minute reach is rolling (back to 2026-08-02 on 2026-10-31), so it is run
when it lands rather than queued behind the vendor fetches; the span it got is recorded and the
pilot reports it.

`pilot` is the declared basis decision. For each pilot name and session of `PILOT_SPAN` the broker
holds, it builds 15-minute bars from the minute samples' underlying price (a sample stamped HH:MM
belongs to the bar that starts at or before it; the 5-minute sampling keeps the samples on minutes
divisible by five) and compares them with the broker's: the median ratio of built to true 14-bar
Wilder ATR, and the share of bars whose built high or low misses the true one by more than
`MISS_ATR` of the true ATR, a bar with no samples counted as a miss. A sampling is accepted when the
ratio lies in `RATIO_BAND` and the miss share is at most `MISS_MAX`, pooled over the three names
(each is reported). The probe's counts per interval come from the request table: its 5-minute write
replaces the 1-minute rows of the same window, so its rows are not read. Its result file holds
ratios and shares, never prices.

**What is asked for, per (name, session), as declared:**

- *Wide:* every expiry from the session through `GAUGE_DAYS` (60) calendar days, the whole chain, at
  `WIDE_INTERVAL` from 09:30 to 15:45 ET. The gauge, the walls and net GEX are read from it.
- *Minute samples:* one expiry beyond the gauge's reach (the nearest listed `SAMPLE_DAYS[0]` to
  `SAMPLE_DAYS[1]` days out), one strike either side of the money, at `SAMPLE_INTERVAL` from 09:30 to
  16:00. Only its underlying price is read: the stock bars are built from it. It is beyond 60 days so
  it never shares bars with a wide request (the fetch keeps one strike range per expiry's bars).
- *The probe* (pilot only): `PROBE_NAME` on `PROBE_SESSIONS`, wide as above and samples at both one
  and five minutes, to measure what the vendor serves for a single stock before 2026.

A (name, session) whose open-interest day is missing or not complete has no listed expiries, so it
gets no request and is counted. The stock bars' basis (1- or 5-minute samples) is the pilot's
decision against the broker's 15-minute candles; the `bars`, `pilot` and `study` steps land in their
own changes, before any of the rule's output is read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as cal

PILOT_NAMES = ("AAPL", "JPM", "KO")
CELL_P_NAMES = ("AAPL", "GOOG", "MSFT", "JPM", "WMT", "HD", "DIS", "KO", "CAT", "MS", "MCD", "COST")
CELL_N_NAMES = ("NEE", "JNJ", "BAC", "CCJ", "V", "PFE", "UNH", "PEP", "C", "XOM")
CELLS = {
    "pilot": (PILOT_NAMES, date(2026, 4, 13), date(2026, 10, 30)),
    "P": (CELL_P_NAMES, date(2026, 4, 13), date(2026, 10, 30)),
    "N": (CELL_N_NAMES, date(2018, 1, 16), date(2026, 4, 10)),
}
PROBE_NAME = "NEE"
PROBE_SESSIONS = (
    date(2018, 3, 15),
    date(2019, 9, 17),
    date(2021, 6, 15),
    date(2023, 2, 14),
    date(2025, 5, 13),
)
GAUGE_DAYS = 60
SAMPLE_DAYS = (61, 100)
WIDE_INTERVAL = "15m"
WIDE_WINDOW = ("09:30", "15:45")
SAMPLE_INTERVAL = "1m"
PROBE_SAMPLE_INTERVALS = ("1m", "5m")
SAMPLE_WINDOW = ("09:30", "16:00")
SAMPLE_STRIKES = 1

NY = ZoneInfo("America/New_York")
BARS_INTERVAL = "15m"
BARS_FROM = datetime(2026, 8, 1, tzinfo=UTC)
BAR_MINUTES = 15
FIRST_BAR, LAST_BAR = "09:30", "15:45"
PILOT_SPAN = (date(2026, 8, 3), date(2026, 10, 30))
SAMPLINGS = {"1m": 1, "5m": 5}
ATR_BARS = 14
MISS_ATR = 0.1
RATIO_BAND = (0.95, 1.05)
MISS_MAX = 0.10
QUIET_S = 8.0
MAX_FETCH_S = 180.0
SNAP_END = 0x08
REMOVE = 0x02


# ------------------------------------------------------------------------------------------------
# Pure.


def sessions(a: date, b: date) -> list[date]:
    d = a if cal.is_trading_day(a) else cal.next_trading_day(a)
    out = []
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def cell_days(cell: str) -> dict[str, list[str]]:
    """{session: [names]} for a cell; the pilot adds the probe's sessions for its one name."""
    names, a, b = CELLS[cell]
    out = {d.isoformat(): list(names) for d in sessions(a, b)}
    if cell == "pilot":
        for d in PROBE_SESSIONS:
            out.setdefault(d.isoformat(), []).append(PROBE_NAME)
    return dict(sorted(out.items()))


def wide_expiries(session: str, expiries: list[str]) -> list[str]:
    last = (date.fromisoformat(session) + timedelta(days=GAUGE_DAYS)).isoformat()
    return sorted(e for e in set(expiries) if session <= e <= last)


def sample_expiry(session: str, expiries: list[str]) -> str | None:
    d = date.fromisoformat(session)
    lo, hi = ((d + timedelta(days=n)).isoformat() for n in SAMPLE_DAYS)
    near = sorted(e for e in set(expiries) if lo <= e <= hi)
    return near[0] if near else None


def requests_for(symbol: str, session: str, expiries: list[str], sample_intervals) -> list[dict]:
    """One (name, session)'s requests: pure."""
    out = [
        {
            "symbol": symbol,
            "session": session,
            "expiration": e,
            "start": WIDE_WINDOW[0],
            "end": WIDE_WINDOW[1],
            "interval": WIDE_INTERVAL,
        }
        for e in wide_expiries(session, expiries)
    ]
    far = sample_expiry(session, expiries)
    if far is not None:
        out += [
            {
                "symbol": symbol,
                "session": session,
                "expiration": far,
                "start": SAMPLE_WINDOW[0],
                "end": SAMPLE_WINDOW[1],
                "interval": iv,
                "strike_range": SAMPLE_STRIKES,
            }
            for iv in sample_intervals
        ]
    return out


def build_requests(days: dict[str, list[str]], listed: dict[tuple[str, str], list[str]]) -> dict:
    """Every request for a cell from the expiries the open-interest store lists: pure. A (name, session)
    with nothing listed is counted, not guessed."""
    reqs, unlisted, no_sample = [], 0, 0
    for session, names in days.items():
        for sym in names:
            exps = listed.get((sym, session))
            if not exps:
                unlisted += 1
                continue
            probe = sym == PROBE_NAME and date.fromisoformat(session) in PROBE_SESSIONS
            got = requests_for(sym, session, exps, PROBE_SAMPLE_INTERVALS if probe else (SAMPLE_INTERVAL,))
            if not any("strike_range" in r for r in got):
                no_sample += 1
            reqs += got
    return {"requests": reqs, "unlisted": unlisted, "no_sample_expiry": no_sample}


def bar_of(hhmm: str) -> str | None:
    """The 15-minute bar a sample stamped HH:MM belongs to, or None outside FIRST_BAR..LAST_BAR."""
    h, m = map(int, hhmm.split(":"))
    m = h * 60 + m
    start = m - m % BAR_MINUTES
    out = f"{start // 60:02d}:{start % 60:02d}"
    return out if FIRST_BAR <= out <= LAST_BAR else None


def build_bars(samples: dict[str, float], step: int) -> dict[str, tuple[float, float, float, float]]:
    """{bar start: (open, high, low, close)} from one session's {HH:MM: price} samples, keeping the
    minutes divisible by `step`: pure."""
    out: dict[str, list[float]] = {}
    for hhmm in sorted(samples):
        if int(hhmm[3:]) % step:
            continue
        bar = bar_of(hhmm)
        if bar is not None:
            out.setdefault(bar, []).append(samples[hhmm])
    return {b: (p[0], max(p), min(p), p[-1]) for b, p in out.items()}


def wilder_atr(bars: list[tuple[float, float, float, float]], n: int = ATR_BARS) -> list[float | None]:
    """Each bar's n-bar Wilder ATR (None until n true ranges exist): pure."""
    out: list[float | None] = []
    trs: list[float] = []
    atr = None
    prev_close = None
    for _o, h, lo, c in bars:
        tr = h - lo if prev_close is None else max(h, prev_close) - min(lo, prev_close)
        prev_close = c
        if atr is None:
            trs.append(tr)
            if len(trs) == n:
                atr = sum(trs) / n
        else:
            atr = (atr * (n - 1) + tr) / n
        out.append(atr)
    return out


def compare(true: dict[tuple, tuple], built: dict[tuple, tuple]) -> dict:
    """One name's built bars against the broker's, keys (session, bar start) in time order: the
    built-to-true ATR ratios and the misses (a key with no built bar is a miss): pure."""
    t_keys = sorted(true)
    b_keys = sorted(built)
    t_atr = dict(zip(t_keys, wilder_atr([true[k] for k in t_keys]), strict=True))
    b_atr = dict(zip(b_keys, wilder_atr([built[k] for k in b_keys]), strict=True))
    ratios, judged, misses, missing = [], 0, 0, 0
    for k in t_keys:
        ta = t_atr[k]
        if not ta:
            continue
        judged += 1
        if k not in built:
            missing += 1
            misses += 1
            continue
        if b_atr.get(k):
            ratios.append(b_atr[k] / ta)
        th, tl = true[k][1], true[k][2]
        bh, bl = built[k][1], built[k][2]
        if abs(bh - th) > MISS_ATR * ta or abs(bl - tl) > MISS_ATR * ta:
            misses += 1
    return {"bars": judged, "ratios": ratios, "misses": misses, "missing": missing}


def verdict(parts: list[dict]) -> dict:
    """Pooled ratio and miss share over the names' `compare` results, and whether they pass."""
    ratios = [r for p in parts for r in p["ratios"]]
    bars = sum(p["bars"] for p in parts)
    misses = sum(p["misses"] for p in parts)
    median = statistics.median(ratios) if ratios else None
    share = misses / bars if bars else None
    ok = (
        median is not None
        and share is not None
        and RATIO_BAND[0] <= median <= RATIO_BAND[1]
        and share <= MISS_MAX
    )
    return {
        "bars": bars,
        "median_atr_ratio": None if median is None else round(median, 4),
        "miss_share": None if share is None else round(share, 4),
        "missing": sum(p["missing"] for p in parts),
        "accepted": ok,
    }


def basis(accepted: dict[str, bool]) -> str:
    """The declared consequence: 5-minute accepted runs cell N on it; only 1-minute drops cell N;
    neither ends the study with no verdict."""
    if accepted.get("5m"):
        return "5m: cell N runs on 5-minute bars"
    if accepted.get("1m"):
        return "1m only: cell N dropped, cell P alone"
    return "neither: no verdict for want of data"


def candle_row(ev) -> tuple | None:
    """(symbol, NY bar start, o, h, l, c, v) from one DXLink Candle event, or None for a removal or
    a bar with a missing price."""
    from cherrypick.core import streamcache

    flags = int(ev.event_flags or 0)
    ms = streamcache.to_float(ev.time)
    vals = [streamcache.to_float(v) for v in (ev.open, ev.high, ev.low, ev.close)]
    if flags & REMOVE or ms is None or not all(v and v > 0 for v in vals):
        return None
    start = datetime.fromtimestamp(ms / 1000.0, tz=UTC).astimezone(NY).strftime("%Y-%m-%dT%H:%M")
    name = str(ev.event_symbol or "").split("{", 1)[0]
    return (name, start, *vals, streamcache.to_float(ev.volume) or 0.0)


# ------------------------------------------------------------------------------------------------
# The stores and the steps.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item45"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _oi_store() -> sqlite3.Connection:
    from cherrypick.backtest import oifetch, settings

    path = oifetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def listed_expiries(conn, days: dict[str, list[str]]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for session, names in days.items():
        for sym in names:
            done = conn.execute(
                "SELECT status FROM oi_days WHERE symbol = ? AND session = ?", (sym, session)
            ).fetchone()
            if not done or done[0] != "complete":
                continue
            out[(sym, session)] = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT expiration FROM oi_contracts WHERE symbol = ? AND session = ?",
                    (sym, session),
                )
            ]
    return out


def cmd_plan(args) -> int:
    days = cell_days(args.cell)
    path = Path(args.out) if args.out else _out_dir() / f"oi_plan_{args.cell}.json"
    path.write_text(json.dumps({"sessions": days}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "cell": args.cell,
                "sessions": len(days),
                "name_days": sum(map(len, days.values())),
                "file": str(path),
            }
        )
    )
    return 0


def cmd_requests(args) -> int:
    days = cell_days(args.cell)
    built = build_requests(days, listed_expiries(_oi_store(), days))
    path = Path(args.out) if args.out else _out_dir() / f"chain_requests_{args.cell}.json"
    path.write_text(json.dumps({"requests": built["requests"]}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "cell": args.cell,
                "requests": len(built["requests"]),
                "unlisted_name_days": built["unlisted"],
                "no_sample_expiry": built["no_sample_expiry"],
                "file": str(path),
            }
        )
    )
    return 0


def _bars_db() -> Path:
    from cherrypick.core.home import data_dir

    return data_dir("research") / "item45" / "bars.db"


def connect_bars(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bars (symbol TEXT, interval TEXT, start TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, interval, start));
        CREATE TABLE IF NOT EXISTS fetched (symbol TEXT, interval TEXT, fetched_on TEXT,
            rows INTEGER, first TEXT, last TEXT, PRIMARY KEY (symbol, interval));
        """
    )
    return conn


def save_bars(conn, symbol: str, rows: list[tuple], today: str) -> int:
    """Replace a name's series whole; a repeated start keeps the last."""
    rows = sorted({r[0]: r for r in rows}.values())
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = ? AND interval = ?", (symbol, BARS_INTERVAL))
        conn.executemany(
            "INSERT INTO bars (symbol, interval, start, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(symbol, BARS_INTERVAL, *r) for r in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO fetched (symbol, interval, fetched_on, rows, first, last) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                symbol,
                BARS_INTERVAL,
                today,
                len(rows),
                rows[0][0] if rows else None,
                rows[-1][0] if rows else None,
            ),
        )
    return len(rows)


async def _fetch_candles(names: list[str]) -> dict[str, list[tuple]]:
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    session = SessionManager(CredentialStore(SHARED_SERVICE)).get_session()
    bars: dict[str, list[tuple]] = {n: [] for n in names}
    ended: set[str] = set()
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle(
            names, interval=BARS_INTERVAL, start_time=BARS_FROM, extended_trading_hours=False
        )
        deadline = time.monotonic() + MAX_FETCH_S
        while time.monotonic() < deadline and len(ended) < len(names):
            # get_event per wait: a wait_for timeout around a listen() generator cancels the generator.
            try:
                ev = await asyncio.wait_for(streamer.get_event(Candle), timeout=QUIET_S)
            except TimeoutError:
                break
            name = str(ev.event_symbol or "").split("{", 1)[0]
            if name not in bars:
                continue
            if int(ev.event_flags or 0) & SNAP_END:
                ended.add(name)
            row = candle_row(ev)
            if row is not None:
                bars[name].append(row[1:])
    return bars


def cmd_bars(args) -> int:
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore

    missing = CredentialStore(SHARED_SERVICE).missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    today = date.today().isoformat()
    conn = connect_bars(_bars_db())
    done = {
        s
        for (s,) in conn.execute(
            "SELECT symbol FROM fetched WHERE interval = ? AND fetched_on = ?", (BARS_INTERVAL, today)
        )
    }
    todo = [n for n in PILOT_NAMES if args.force or n not in done]
    got = asyncio.run(_fetch_candles(todo)) if todo else {}
    out = {"db": str(_bars_db()), "skipped": len(PILOT_NAMES) - len(todo), "series": {}}
    for name, rows in got.items():
        save_bars(conn, name, rows, today)
    for name, rows, first, last in conn.execute(
        "SELECT symbol, rows, first, last FROM fetched WHERE interval = ? ORDER BY symbol", (BARS_INTERVAL,)
    ):
        out["series"][name] = {"rows": rows, "first": first, "last": last}
    print(json.dumps(out))
    return 0 if all(out["series"].get(n, {}).get("rows") for n in PILOT_NAMES) else 1


def broker_bars(conn, name: str) -> dict[tuple[str, str], tuple]:
    """{(session, bar start): ohlc} inside PILOT_SPAN and the rule's bars."""
    a, b = (d.isoformat() for d in PILOT_SPAN)
    out = {}
    for start, o, h, lo, c in conn.execute(
        "SELECT start, open, high, low, close FROM bars WHERE symbol = ? AND interval = ? ORDER BY start",
        (name, BARS_INTERVAL),
    ):
        day, hhmm = start[:10], start[11:16]
        if a <= day <= b and FIRST_BAR <= hhmm <= LAST_BAR:
            out[(day, hhmm)] = (o, h, lo, c)
    return out


def sample_windows(conn, name: str, days: list[str]) -> tuple[dict[str, str], list[str]]:
    """({session: sample expiry} of the complete 1-minute sample requests, [sessions whose sample
    request is absent or refused]). An empty answer is the vendor's, and its bars count as missed."""
    found, unfinished = {}, []
    for day in days:
        row = conn.execute(
            "SELECT expiration, status FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
            'AND interval = ? AND start = ? AND "end" = ?',
            (name, day, SAMPLE_INTERVAL, *SAMPLE_WINDOW),
        ).fetchone()
        if row is None or row[1] not in ("complete", "empty"):
            unfinished.append(day)
        elif row[1] == "complete":
            found[day] = row[0]
    return found, unfinished


def session_samples(conn, name: str, day: str, expiration: str) -> dict[str, float]:
    """{HH:MM: underlying}: the median of the sampled contracts' underlying at each minute."""
    by: dict[str, list[float]] = {}
    for bar, px in conn.execute(
        "SELECT bar, underlying FROM intraday_chain_quotes WHERE symbol = ? AND session = ? "
        "AND expiration = ? AND underlying > 0",
        (name, day, expiration),
    ):
        by.setdefault(bar, []).append(px)
    return {b: statistics.median(v) for b, v in by.items()}


def probe_counts(conn) -> list[dict]:
    """What the vendor served the probe, per session and sampling: status and bars, no rows."""
    out = []
    for d in PROBE_SESSIONS:
        day = d.isoformat()
        item = {"session": day}
        wide = conn.execute(
            "SELECT status, COUNT(*) FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
            'AND interval = ? AND start = ? AND "end" = ? GROUP BY status',
            (PROBE_NAME, day, WIDE_INTERVAL, *WIDE_WINDOW),
        ).fetchall()
        item["wide"] = dict(wide)
        for iv in PROBE_SAMPLE_INTERVALS:
            row = conn.execute(
                "SELECT status, bars FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
                'AND interval = ? AND start = ? AND "end" = ?',
                (PROBE_NAME, day, iv, *SAMPLE_WINDOW),
            ).fetchone()
            item[iv] = {"status": row[0], "bars": row[1]} if row else None
        out.append(item)
    return out


def cmd_pilot(args) -> int:
    from cherrypick.backtest import intradayfetch, settings

    path = intradayfetch.db_path(settings.load(None))
    chains = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    broker = sqlite3.connect(f"file:{_bars_db().as_posix()}?mode=ro", uri=True)
    per: dict[str, dict[str, dict]] = {s: {} for s in SAMPLINGS}
    spans, refused = {}, {}
    for name in PILOT_NAMES:
        true = broker_bars(broker, name)
        days = sorted({k[0] for k in true})
        spans[name] = {
            "sessions": len(days),
            "first": days[0] if days else None,
            "last": days[-1] if days else None,
        }
        windows, unfinished = sample_windows(chains, name, days)
        if unfinished:
            refused[name] = len(unfinished)
            continue
        samples = {d: session_samples(chains, name, d, e) for d, e in windows.items()}
        for label, step in SAMPLINGS.items():
            built = {(d, b): v for d, s in samples.items() for b, v in build_bars(s, step).items()}
            per[label][name] = compare(true, built)
    if refused:
        print(json.dumps({"ok": False, "reason": "sample requests not finished", "sessions": refused}))
        return 1
    result = {
        "declared": {
            "span": [d.isoformat() for d in PILOT_SPAN],
            "atr_bars": ATR_BARS,
            "miss_atr": MISS_ATR,
            "ratio_band": list(RATIO_BAND),
            "miss_max": MISS_MAX,
        },
        "broker_span": spans,
        "samplings": {},
        "probe": probe_counts(chains),
    }
    for label, parts in per.items():
        result["samplings"][label] = {
            **verdict(list(parts.values())),
            "names": {
                n: {k: v for k, v in verdict([p]).items() if k != "accepted"} for n, p in parts.items()
            },
        }
    result["basis"] = basis({k: v["accepted"] for k, v in result["samplings"].items()})
    out = Path(args.out) if args.out else _out_dir() / "pilot.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"basis": result["basis"], "file": str(out)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("requests", cmd_requests)):
        sp = sub.add_parser(name)
        sp.add_argument("--cell", choices=sorted(CELLS), default="pilot")
        sp.add_argument("--out")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("bars")
    sp.add_argument("--force", action="store_true", help="refetch a series already fetched today")
    sp.set_defaults(fn=cmd_bars)
    sp = sub.add_parser("pilot")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_pilot)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
