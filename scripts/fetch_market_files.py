"""Fetch the daily market files the morning pack reads: Cboe's index histories (SKEW, VIX, VIX1D,
VIX3M, VVIX, VXN; VIX3M for `scripts/contango_replay.py`, VIX1D for `cherrypick.meic.replay`), the
broker's SPX chain (reduced to the 30-day 25-delta risk reversal), Treasury's par yield curve, the
release calendars (BEA always, FRED when a
key is stored), and OCC's option volume by underlying with Nasdaq Trader's symbol directory (the
pack's `hot_options` ranking).

Why a script: these are network fetches, and `packages/overview` is network-free by rule. It writes
only `~/.cherrypick/data/market-files/`, and a failure leaves every file already there untouched.
The risk reversal is the one step that reaches the broker: read-only, one DXLink session for the
greeks and quotes of a few hundred SPXW options.

The risk reversal is read only between the close (plus SPX options' extra quarter hour) and 20:15
ET, when SPX options' overnight session opens and their IVs stop describing the close. The 18:45 run
sits in that window; the 07:45 retry cannot take a missed session, so it reports the gap instead.
The parsers are overview's own (`cherrypick.overview.files`), so a file is checked on arrival by the
code that will read it, and a download that parses to less than the file on disk (a truncated or
wrong file) is refused rather than written over good history.

Why these sources (packages/technicals/docs/reference.md, "The market report"): Cboe's daily SKEW file
replaces
the stream's SKEW history, which delivered five scattered rows in 270 days; Treasury posts its curve
by about 18:00 ET, a day ahead of FRED; BEA's release-dates file is keyless. BLS refuses scripts,
so CPI, jobs and PPI dates come only through FRED's release-dates API, which needs a free key
(`fred-key` stores it in the OS keyring). FRED's keyless CSV download hangs from here, so FRED is
reached only through its API.

OCC publishes a session's volume late that evening (2026-10-01's appeared between 23:12 and 23:22
ET), after the 18:45 run, so the 07:45 retry is the one that lands it; each run lands every one of
the last `occ.OCC_SESSIONS` sessions it lacks (one ~4 MB CSV each, reduced to sides by underlying
on arrival) and leaves an unpublished one for the next run. The first run backfills them all.

A handful of requests per run, a few seconds apart.

    python scripts/fetch_market_files.py [fetch]     # everything
    python scripts/fetch_market_files.py fred-key    # store a FRED API key once
    python scripts/fetch_market_files.py treasury-history --since 2015   # past years' curves, once
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

from cherrypick.core import progress
from cherrypick.overview import files, occ

# scripts/_common.py, beside this file (a test loading this script by path has no scripts/ on sys.path).
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _common  # noqa: E402

UA = "cherrypick-marketfiles/1.0"
PAUSE_RANGE_S = (2.0, 5.0)
# OCC is asked more slowly: a backfill is two dozen ~4 MB files from one host.
OCC_PAUSE_RANGE_S = (5.0, 10.0)
TIMEOUT_S = 60

FRED_SERVICE = "cherrypick-fred"
FRED_KEY = "api_key"
FRED_RELEASES_URL = "https://api.stlouisfed.org/fred/releases/dates"
# The releases worth a line in a pre-open pack, by FRED release id. Declared, not inferred: FRED
# lists ~300 releases a month and most move nothing.
FRED_RELEASES = {
    10: "Consumer Price Index",
    46: "Producer Price Index",
    50: "Employment Situation",
    180: "Unemployment Insurance Weekly Claims",
    9: "Advance Monthly Sales for Retail and Food Services",
    192: "Job Openings and Labor Turnover Survey",
    11: "Employment Cost Index",
    194: "ADP National Employment Report",
    13: "Industrial Production and Capacity Utilization",
    27: "New Residential Construction",
    91: "Surveys of Consumers (University of Michigan)",
}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def _pause(pause_range: tuple[float, float] = PAUSE_RANGE_S) -> None:
    time.sleep(random.uniform(*pause_range))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a file


def _replace_history(path: Path, text: str, parse) -> str | None:
    """Write `text` to `path` if it parses and holds at least what is already there. Returns why it
    was refused, or None."""
    new = parse(text)
    old = parse(path.read_text(encoding="utf-8")) if path.exists() else []
    problems = files.history_problems(old, new)
    if problems:
        return "; ".join(problems)
    _write(path, text)
    return None


def fetch_cboe(report: dict, rep=None) -> None:
    if rep is not None:
        rep.split("cboe indexes", files.CBOE_INDEXES, unit="indexes")
    for i, symbol in enumerate(files.CBOE_INDEXES):
        if i:
            _pause()
        if rep is not None:
            rep.advance("cboe indexes", key=symbol, current=symbol)
        try:
            text = _get(files.CBOE_URL.format(symbol=symbol)).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"Cboe {symbol}: {exc}")
            continue
        refused = _replace_history(files.cboe_path(symbol), text, files.parse_cboe)
        if refused:
            report["problems"].append(f"Cboe {symbol} not replaced: {refused}")
        else:
            rows = files.parse_cboe(text)
            report["cboe"][symbol] = {"rows": len(rows), "last": rows[-1][0].isoformat()}


# The strikes asked for: out-of-the-money puts from 80% of spot and calls to 110%. A 30-day 25-delta
# put sat 4.5% below spot on 2026-11-19 (VIX near 25); the band leaves room for a VIX past 50.
RR_STRIKE_BAND = (0.80, 1.10)
RR_SETTLE = timedelta(minutes=15)  # SPX options trade a quarter hour past the cash close
RR_OVERNIGHT_OPENS = dt_time(20, 15)
RR_BATCH = 100
RR_SUBSCRIBE_PACE_S = 1.0
RR_BATCH_TIMEOUT_S = 12.0


def rr_window(now_et: datetime) -> tuple[date, bool]:
    """(the last session whose close has passed, whether `now_et` is inside the window its risk
    reversal is read in): from the close plus `RR_SETTLE` to `RR_OVERNIGHT_OPENS` on that day."""
    from cherrypick.core import calendar as cal

    today = now_et.date()
    naive = now_et.replace(tzinfo=None)
    if cal.is_trading_day(today):
        settled = datetime.combine(today, dt_time.fromisoformat(cal.session_close_hhmm(today))) + RR_SETTLE
        if naive >= settled:
            return today, naive < datetime.combine(today, RR_OVERNIGHT_OPENS)
    return cal.previous_trading_day(today), False


def _rr_float(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def _rr_options(session, day: date) -> tuple[float | None, list[dict]]:
    """(SPX's last price, the SPXW options around the two expirations bracketing 30 days, each with
    the broker's IV, delta, bid and ask). Read-only: one chain listing, one DXLink session."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Greeks, Quote
    from tastytrade.instruments import NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type

    md = await get_market_data_by_type(session, indices=["SPX"])
    spot = next((_rr_float(q.last or q.mark or q.close) for q in md if q.symbol == "SPX"), None)
    if not spot:
        return None, []
    chains = await NestedOptionChain.get(session, "SPX")
    # SPXW only: PM-settled, one root for every date. SPX's own chain is the AM-settled monthly.
    by_exp = {
        e.expiration_date: e
        for c in (chains if isinstance(chains, list) else [chains])
        if getattr(c, "root_symbol", None) == "SPXW"
        for e in c.expirations
    }
    lo, hi = RR_STRIKE_BAND
    wanted: dict[str, dict] = {}
    for exp in files.rr_expirations(day, list(by_exp)):
        for k in by_exp[exp].strikes:
            strike = float(k.strike_price)
            if lo * spot <= strike < spot:
                wanted[k.put_streamer_symbol] = {"expiration": exp, "right": "P", "strike": strike}
            if spot <= strike <= hi * spot:
                wanted[k.call_streamer_symbol] = {"expiration": exp, "right": "C", "strike": strike}
    names = list(wanted)

    async def drain(streamer, cls, n: int, deadline: float) -> None:
        seen: set[str] = set()
        while len(seen) < n and time.monotonic() < deadline:
            try:
                ev = await asyncio.wait_for(streamer.get_event(cls), timeout=deadline - time.monotonic())
            except TimeoutError:
                return
            row = wanted.get(str(ev.event_symbol))
            if row is None:
                continue
            if cls is Greeks:
                row.update(iv=_rr_float(ev.volatility), delta=_rr_float(ev.delta))
            else:
                row.update(bid=_rr_float(ev.bid_price), ask=_rr_float(ev.ask_price))
            seen.add(str(ev.event_symbol))

    async with DXLinkStreamer(session) as streamer:
        for i in range(0, len(names), RR_BATCH):
            chunk = names[i : i + RR_BATCH]
            for cls in (Greeks, Quote):
                await streamer.subscribe(cls, chunk)
                await asyncio.sleep(RR_SUBSCRIBE_PACE_S)
            deadline = time.monotonic() + RR_BATCH_TIMEOUT_S
            await asyncio.gather(
                drain(streamer, Greeks, len(chunk), deadline), drain(streamer, Quote, len(chunk), deadline)
            )
            for cls in (Greeks, Quote):
                await streamer.unsubscribe(cls, chunk)
                await asyncio.sleep(RR_SUBSCRIBE_PACE_S)
    return spot, list(wanted.values())


def read_rr_options(day: date) -> tuple[float | None, list[dict]]:
    """`_rr_options` under the suite's broker session. Raises on missing credentials."""
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        raise RuntimeError("broker credentials are missing")
    return asyncio.run(_rr_options(SessionManager(store).get_session(), day))


def fetch_risk_reversal(report: dict, now_et: datetime | None = None, read=None) -> None:
    """The broker's SPX chain reduced to one row: the 30-day 25-delta risk reversal at the session's
    close (`files.risk_reversal`). Only the row is kept, in a file keyed by session, so a re-run in
    the window replaces that session's row and never touches another's. Outside the window nothing
    is read; a session left without a row is a problem."""
    read = read or read_rr_options
    session, open_ = rr_window(now_et or datetime.now(files.ET))
    path = files.risk_reversal_path()
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        rows = {}
    if not open_:
        if session.isoformat() not in rows:
            report["problems"].append(
                f"SPX risk reversal: no row for {session}; it is read between the close and 20:15 ET"
            )
        return
    try:
        spot, options = read(session)
    except Exception as exc:  # noqa: BLE001 -- the broker's failure is reported, never raised
        report["problems"].append(f"SPX risk reversal: broker: {type(exc).__name__}: {exc}"[:300])
        return
    row = files.risk_reversal(session, spot, options) if spot else None
    if row is None:
        report["problems"].append(
            f"SPX risk reversal: the broker's chain gave no 25-delta reading ({len(options)} options read)"
        )
        return
    rows[row["session"]] = row
    _write(path, json.dumps(dict(sorted(rows.items())), indent=1))
    report["risk_reversal"] = {
        k: row[k] for k in ("session", "rr_vol_pts", "call_25d_iv_pct", "put_25d_iv_pct")
    }


def fetch_treasury(report: dict, today: date) -> None:
    # The first sessions of a year need last year's file too; fetch it only if it is missing.
    years = [today.year] + ([today.year - 1] if not files.treasury_path(today.year - 1).exists() else [])
    for i, year in enumerate(years):
        if i:
            _pause()
        try:
            text = _get(files.TREASURY_URL.format(year=year)).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"Treasury {year}: {exc}")
            continue
        refused = _replace_history(files.treasury_path(year), text, files.parse_treasury)
        if refused:
            report["problems"].append(f"Treasury {year} not replaced: {refused}")
        else:
            rows = files.parse_treasury(text)
            report["treasury"][str(year)] = {"rows": len(rows), "last": rows[-1][0].isoformat()}


def fetch_bea(report: dict) -> None:
    try:
        text = _get(files.BEA_URL).decode("utf-8-sig", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        report["problems"].append(f"BEA: {exc}")
        return
    rows = files.parse_bea(text)
    if not rows:
        report["problems"].append("BEA: the release-dates file parsed to nothing; kept the old one")
        return
    _write(files.bea_path(), text)
    report["calendar"]["bea_releases"] = len(rows)


def fred_releases(body: dict) -> list[dict]:
    """FRED's release-dates reply, narrowed to the declared releases. FRED gives dates, not times."""
    out = []
    for row in body.get("release_dates") or []:
        try:
            rid = int(row.get("release_id"))
        except (TypeError, ValueError):
            continue
        if rid in FRED_RELEASES and row.get("date"):
            out.append({"name": FRED_RELEASES[rid], "at": str(row["date"]), "source": "FRED"})
    return sorted(out, key=lambda r: (r["at"], r["name"]))


FRED_PAGE = 1000  # the API's own ceiling per request
FRED_MAX_PAGES = 6


def fred_release_pages(key: str, today: date, get=None) -> list[dict]:
    """Every release date FRED lists from today through 45 days out, oldest first, one page at a
    time. FRED tracks ~40 releases a day, so the window runs to ~1,800 rows: one page of 1,000 in
    its default newest-first order silently dropped the nearest two weeks -- this week's jobs report
    and jobless claims included (found 2026-09-27). Paging oldest-first until `count` is reached is
    the fix; running past `FRED_MAX_PAGES` raises rather than returning a partial calendar."""
    get = get or (lambda url: json.loads(_get(url)))
    params = {
        "api_key": key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": (today + timedelta(days=45)).isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": str(FRED_PAGE),
    }
    pages: list[dict] = []
    seen = 0
    for page in range(FRED_MAX_PAGES):
        if page:
            _pause()
        body = get(FRED_RELEASES_URL + "?" + urllib.parse.urlencode({**params, "offset": str(seen)}))
        rows = body.get("release_dates") or []
        pages.append(body)
        seen += len(rows)
        if not rows or seen >= int(body.get("count") or 0):
            return pages
    raise RuntimeError(f"more than {FRED_MAX_PAGES} pages of release dates; not writing a partial calendar")


CENSUS_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"


def fetch_census(report: dict, get=None) -> None:
    """Census's economic-indicators calendar, parsed (`cherrypick.core.events.parse_census`) and
    folded into census.json, which never drops a date. The headline source for retail sales,
    housing starts, new home sales and durable goods: FRED lists other updates under the same
    release ids (retail sales on 2026-09-28, new home sales filed as housing starts). A page that
    parses to nothing is refused and the stored calendar kept."""
    from cherrypick.core import events as _events

    get = get or _get
    try:
        rows = _events.parse_census(get(CENSUS_URL).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        report["problems"].append(f"Census: {exc}")
        return
    if not rows:
        report["problems"].append("Census: the calendar parsed to nothing; kept the old one")
        return
    path = _events.census_path(files.store_dir() / "calendar")
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    _write(path, json.dumps(_events.merge_census(old, rows)))
    report["calendar"]["census_releases"] = len(rows)


UMICH_URL = "https://www.sca.isr.umich.edu/"


def fetch_umich(report: dict, today: date, get=None) -> None:
    """The Surveys of Consumers home page's "Next data release" note, parsed
    (`cherrypick.core.events.parse_umich`) and folded into umich.json, which never drops a date.
    The only source for the preliminary sentiment reading: FRED lists only the final, and Michigan
    publishes no year schedule. A page without the note is refused and the stored dates kept."""
    from cherrypick.core import events as _events

    get = get or _get
    try:
        rows = _events.parse_umich(get(UMICH_URL).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        report["problems"].append(f"UMich: {exc}")
        return
    if not rows:
        report["problems"].append("UMich: no next-release note on the page; kept the old dates")
        return
    path = _events.umich_path(files.store_dir() / "calendar")
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    _write(path, json.dumps(_events.merge_umich(old, rows, today.isoformat())))
    report["calendar"]["umich_next"] = rows[0]["at"]


def _fred_key() -> str | None:
    try:
        from cherrypick.core.auth.credentials import CredentialStore

        return CredentialStore(FRED_SERVICE).get_secret(FRED_KEY)
    except Exception:  # noqa: BLE001 -- no keyring or no key: FRED is optional
        return None


def _merge_fred_history(rows: list[dict], start: date) -> None:
    """Fold a fetch covering `start`..`start + 45 days` into fred_history.json. fred.json holds only
    the window ahead and is replaced each run, so without this every past release date is lost the
    day after it -- and a past session's events (`cherrypick.core.events`) could never be read."""
    from cherrypick.core import events as _events

    path = _events.fred_history_path(files.store_dir() / "calendar")
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    end = start + timedelta(days=_events.FRED_WINDOW_DAYS)
    _write(path, json.dumps(_events.merge_fred_history(old, rows, start.isoformat(), end.isoformat())))


def fetch_fred(report: dict, today: date) -> None:
    key = _fred_key()
    if not key:
        report["calendar"]["fred"] = "no key stored (run `fred-key`); CPI, jobs and PPI dates are absent"
        return
    try:
        pages = fred_release_pages(key, today)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # The key rides in the URL; never let it reach a report or a log line.
        report["problems"].append(f"FRED: {str(exc).replace(key, '****')}")
        return
    except RuntimeError as exc:
        report["problems"].append(f"FRED: {exc}")
        return
    body = {"release_dates": [row for page in pages for row in page.get("release_dates") or []]}
    rows = fred_releases(body)
    _write(
        files.fred_releases_path(),
        json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "releases": rows}),
    )
    _merge_fred_history(rows, today)
    report["calendar"]["fred_releases"] = len(rows)


def fetch_occ(report: dict, today: date, get=None) -> None:
    """Land OCC's daily volume for each recent session not yet stored, oldest first. A session OCC
    has not published yet (a tiny "no data" reply) is left for the next run, and is a problem only
    once it is two sessions old; a throttling reply ends the step with what it has.

    The one place OCC is fetched: the stock universe's evening harvest calls this too, so its
    volume screen reads the same files the pack ranks."""
    from cherrypick.core import calendar as cal

    get = get or _get
    day = today if cal.is_trading_day(today) else cal.previous_trading_day(today)
    wanted = []
    while len(wanted) < occ.OCC_SESSIONS:
        wanted.append(day)
        day = cal.previous_trading_day(day)
    stored = set(occ.stored_sessions())
    out = report["occ"] = {"landed": [], "unpublished": []}
    first = True
    for d in sorted(wanted):
        if d.isoformat() in stored:
            continue
        if not first:
            _pause(OCC_PAUSE_RANGE_S)
        first = False
        try:
            body = get(occ.OCC_URL.format(yyyymmdd=d.strftime("%Y%m%d")))
        except urllib.error.HTTPError as exc:
            report["problems"].append(f"OCC {d}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"OCC {d}: {exc}")
            continue
        if len(body) < occ.OCC_MIN_BYTES:
            out["unpublished"].append(d.isoformat())
            if cal.previous_trading_day(today) > d:
                report["problems"].append(f"OCC {d}: still not published")
            continue
        parsed = occ.parse_occ(body.decode("utf-8", errors="replace"))
        if parsed is None:
            report["problems"].append(f"OCC {d}: the file is not a daily volume report; nothing stored")
            continue
        if parsed["session"] != d.isoformat():
            report["problems"].append(f"OCC {d}: the file is for {parsed['session']}; nothing stored")
            continue
        parsed.update(source="occ_volume_query", fetched_at=datetime.now(UTC).isoformat())
        parsed["columns"] = occ.COLUMNS
        _write(occ.session_path(parsed["session"]), json.dumps(parsed, separators=(",", ":")))
        out["landed"].append(parsed["session"])
    out["stored"] = len(occ.stored_sessions())


def fetch_listings(report: dict, get=None) -> None:
    """Nasdaq Trader's directory of US-listed securities, for whether an underlying is a stock or a
    fund. A download that parses to too few rows, or to far fewer than the file on disk, is refused."""
    get = get or _get
    try:
        text = get(occ.LISTINGS_URL).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        report["problems"].append(f"Nasdaq Trader directory: {exc}")
        return
    new = occ.parse_listings(text)
    old, _ = occ.read_listings()
    if not new:
        report["problems"].append("Nasdaq Trader directory: parsed to nothing; kept the old one")
        return
    if old and len(new) < 0.9 * len(old):
        report["problems"].append(
            f"Nasdaq Trader directory not replaced: {len(new)} symbols, against {len(old)} on disk"
        )
        return
    _write(occ.listings_path(), text)
    report["listings"] = {"symbols": len(new), "as_of": occ.listings_as_of(text)}


def _warn(title: str, message: str) -> None:
    _common.warn("market_files", title, message)


INCOMPLETE = "Market files incomplete"


def _alert_path() -> Path:
    return files.store_dir() / "alert.json"


def _note_outcome(problems: list[str]) -> None:
    """Warn on an incomplete run and remember it; on the next clean run, say it recovered (as the
    watchdog's findings do) and forget it. FRED answered one evening's three tries (2026-10-21) with
    an empty body; the next morning's run fetched it, and the last word in the channel was still the
    warning."""
    path = _alert_path()
    if problems:
        _warn(INCOMPLETE, "\n".join(problems))
        try:
            path.write_text(json.dumps({"warned_at": datetime.now(UTC).isoformat()}), encoding="utf-8")
        except OSError:
            pass
        return
    if not path.exists():
        return
    _common.notify("INFO", "market_files", f"Recovered: {INCOMPLETE}", "every source fetched on this run")
    try:
        path.unlink()
    except OSError:
        pass


def cmd_fetch(_args) -> int:
    today = datetime.now(files.ET).date()
    report: dict = {"cboe": {}, "treasury": {}, "calendar": {}, "problems": []}
    with progress.Reporter("fetch_market_files-fetch") as rep:
        # Nine sources, none of them one symbol's: the step count is the overall, the Cboe indexes the split.
        rep.phase("sources", total=9, unit="sources")
        fetch_cboe(report, rep)
        rep.advance("sources", current="cboe")
        _pause()
        fetch_risk_reversal(report)
        rep.advance("sources", current="risk reversal")
        _pause()
        fetch_treasury(report, today)
        rep.advance("sources", current="treasury")
        _pause()
        fetch_bea(report)
        rep.advance("sources", current="bea")
        fetch_census(report)
        rep.advance("sources", current="census")
        fetch_umich(report, today)
        rep.advance("sources", current="umich")
        fetch_fred(report, today)
        rep.advance("sources", current="fred")
        _pause()
        fetch_listings(report)
        rep.advance("sources", current="listings")
        _pause()
        fetch_occ(report, today)
        rep.advance("sources", current="occ")
    report["ok"] = not report["problems"]
    print(json.dumps(report, indent=1))
    _note_outcome(report["problems"])
    return 0 if report["ok"] else 1


def cmd_fred_key(_args) -> int:
    from getpass import getpass

    from cherrypick.core.auth.credentials import CredentialStore, prompt_and_store

    written = prompt_and_store(
        CredentialStore(FRED_SERVICE), (FRED_KEY,), prompt_fn=lambda label: getpass(f"FRED {label}: ")
    )
    print(json.dumps({"ok": bool(written), "stored": list(written or [])}))
    return 0


def cmd_fred_history(args) -> int:
    """Seed fred_history.json from `--since` to today, one 45-day window at a time (read-only GETs;
    the key never reaches the output). Needed once: the daily fetch keeps it current after that."""
    key = _fred_key()
    if not key:
        print(json.dumps({"ok": False, "error": "no FRED key stored (run `fred-key`)"}))
        return 1
    start, today = date.fromisoformat(args.since), datetime.now(files.ET).date()
    windows = 0
    rep = progress.Reporter("fetch_market_files-fred-history")
    # FRED release dates carry no symbol: the window count is the overall.
    rep.phase("windows", total=len(range(0, max(0, (today - start).days) + 1, 45)), unit="windows")
    while start <= today:
        rep.advance("windows", current=start.isoformat())
        try:
            pages = fred_release_pages(key, start)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            rep.finish("failed")
            print(json.dumps({"ok": False, "error": str(exc).replace(key, "****"), "windows": windows}))
            return 1
        rows = fred_releases({"release_dates": [r for p in pages for r in p.get("release_dates") or []]})
        _merge_fred_history(rows, start)
        windows += 1
        start += timedelta(days=45)
        _pause()
    rep.finish("done")
    print(json.dumps({"ok": True, "since": args.since, "windows": windows}))
    return 0


def cmd_treasury_history(args) -> int:
    """Seed the Treasury curve files from `--since` (a year) through last year, one file a year,
    skipping a year already on disk. Needed once: the daily fetch keeps this year and last current
    after that, and a replay reading a rate before then refuses rather than reading zero."""
    today = datetime.now(files.ET).date()
    report: dict = {"treasury": {}, "problems": []}
    years = [y for y in range(args.since, today.year) if not files.treasury_path(y).exists()]
    # Treasury curve years carry no symbol: no key.
    for i, year in enumerate(
        progress.track(
            years, job="fetch_market_files-treasury-history", phase="years", unit="years", current=lambda y: y
        )
    ):
        if i:
            _pause()
        try:
            text = _get(files.TREASURY_URL.format(year=year)).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report["problems"].append(f"Treasury {year}: {exc}")
            continue
        refused = _replace_history(files.treasury_path(year), text, files.parse_treasury)
        if refused:
            report["problems"].append(f"Treasury {year} not written: {refused}")
        else:
            rows = files.parse_treasury(text)
            report["treasury"][str(year)] = {"rows": len(rows), "last": rows[-1][0].isoformat()}
    report["ok"] = not report["problems"]
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("fetch").set_defaults(fn=cmd_fetch)
    sub.add_parser("fred-key").set_defaults(fn=cmd_fred_key)
    hist = sub.add_parser("fred-history", help="seed the FRED release history back to --since (once)")
    hist.add_argument("--since", required=True, help="YYYY-MM-DD")
    hist.set_defaults(fn=cmd_fred_history)
    tre = sub.add_parser("treasury-history", help="seed the Treasury curve files back to --since (once)")
    tre.add_argument("--since", required=True, type=int, help="the first year, e.g. 2015")
    tre.set_defaults(fn=cmd_treasury_history)
    args = ap.parse_args(argv)
    return (getattr(args, "fn", None) or cmd_fetch)(args)


if __name__ == "__main__":
    sys.exit(main())
