"""Item 51, dropping the condor's call side when the VIX curve is stressed
(research/edge-hypotheses-plan.md): the same entries with the call side never opened, on the sessions (part 1,
the curve at the open) or the entries (part 2, the curve at the entry's hour) a stressed curve flags.

    python packages/backtest/scripts/meic_call_side_item51.py fetch-yahoo
    python packages/backtest/scripts/meic_call_side_item51.py validate
    python packages/backtest/scripts/meic_call_side_item51.py study --part 1|2 --window design|holdout [--json
    PATH] python packages/backtest/scripts/meic_call_side_item51.py report

No replay. It reads the per-entry ledgers of runs that already exist: item 42's `be`, item 43's `w15`,
and `live-1400` (item 43's design run; item 23's recorded run for the holdout, as items 43 and 50 read
it). Writes under the backtest data dir (results/research/item51/), never into the repo: the ledgers are
licensed. `fetch-yahoo` is the one step that reaches the network, so it lives here and not in a package;
what it fetches is public data for personal use, kept under ~/.cherrypick.

**The variant, as declared.** On a flagged session (part 1) or a flagged entry (part 2) the call side is
removed. Its net is the condor's net less the call side's: the call legs' P&L as they traded, less half
the trade's fees (`fees` is the whole trade's, settlement included). The put side keeps the stop it traded
with. The measure is the variant's net less the base's, the call side's net with the sign reversed, in
dollars as traded, summed over the session's flagged entries: one session, one observation. A flagged
session with no entry has no call side to drop and is not an observation (counted, `flagged_no_entry`).

**The flags.** `inverted`: VIX over VIX3M at 1 or above. `hump`: VIX9D over VIX at 1 or above with VIX under
VIX3M. Part 1 reads the OPEN prints of the exchange's daily files; part 2 reads the close of the last
hourly bar finished by the entry's time, from Yahoo's bars (`fetch-yahoo`). A missing print is no flag and
no observation (counted).

**The cells.** Three bases (`be`, `live-1400`, `w15`) by two flags. Per cell, all must hold: the paired
mean above zero and a one-sided t above 2; Holm across the part's six cells (a no-verdict cell enters at
p = 1); positive in each time-half of the design window; positive with the call side's fees NOT credited
back (the variant paying them, so the saving is not just fees); positive with the best year removed
(the calendar year with the largest summed difference). Fewer flagged sessions than the floor (30 in part
1, 20 in part 2) is no verdict. The holdout then asks only that the paired mean be above zero, for a cell
that passed the design window, over 2025-07-01..2026-01-23 and any session after 2026-10-12;
2026-01-26..2026-10-12 is reported and never judged.

**Reported, never judged.** By year; each stress episode removed in turn (an episode is a run of flagged
sessions with at most 5 sessions of the base's run between consecutive flagged ones); the move from the
open to the entry in bins fixed by the declaration (under 0.5%, 0.5-1%, 1% or more of SPX's open, which
is Yahoo's `^GSPC` daily open, since the exchange's SPX file holds closes only) and the overnight gap in
the same bins (open over the previous close in that file); net over buying power for both (condor:
(width - credit) x 100 x quantity; put side alone: (width - put credit) x 100 x quantity); the version with
the call side's fees credited (the measure itself) beside the one with them kept.

**Part 2's validation** (`validate`) over 2026-01-26..2026-11-05 against the broker's recorded bars,
`vol_curve_bars.db`. Part 2 refuses unless it passed. How the two are aligned is in `ALIGNMENT`.

**Ledger check.** Per-entry nets (`pnl - fees`) can differ from the session's recorded net where the
replay charged a session-level settlement fee; the count of such sessions is in the result, and only the
per-entry figures are used.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sqlite3
import sys
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home
from cherrypick.core import stats as core_stats

from cherrypick.backtest import paths

_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_item43", Path(__file__).resolve().with_name("meic_wing_width_item43.py")
)
i43 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i43)

#: The replay's corrections, oldest first (`orchestrator.studyversion`). None yet.
REPLAY_CORRECTIONS = ()

ITEM = 51
ET = ZoneInfo("America/New_York")
BASES = ("be", "live-1400", "w15")
STATES = ("inverted", "hump")
MIN_FLAGGED = {1: 30, 2: 20}
T_BAR = 2.0
ALPHA = 0.05
EPISODE_GAP = 5
EPISODE_DEFINITION = (
    f"runs of flagged sessions with at most {EPISODE_GAP} sessions of the base's run "
    "between consecutive flagged ones"
)
BIN_EDGES = (0.005, 0.01)
BIN_NAMES = ("<0.5%", "0.5-1%", ">=1%")

DESIGN = {1: ("2022-05-16", "2025-06-30"), 2: ("2024-01-19", "2025-06-30")}
HOLDOUT_START = "2025-07-01"
HOLDOUT_JUDGED_END = "2026-01-23"
EXPLORED_END = "2026-10-12"
VALIDATION_SPAN = ("2026-01-26", "2026-11-05")
CLOSE_TOL = 0.01
SHARE_BAR = 0.95
READ_HOURS = (11, 12, 13, 14, 15, 16)
CLOSE_OF_DAY = (16, 0)
SYMBOLS = ("VIX", "VIX9D", "VIX3M")
YAHOO = {"VIX": "^VIX", "VIX9D": "^VIX9D", "VIX3M": "^VIX3M"}
YAHOO_SPX = "^GSPC"

ALIGNMENT = (
    "Both sources are cut into bars with a start and an end. Yahoo's hourly VIX9D and VIX3M bars start at "
    ":30 (09:30, 10:30, ...) and its VIX bars on the hour; each ends an hour later, the last capped at "
    "16:00 ET, so all three end together only at 16:00 and the state comparison is read there. A bar "
    "starting at or after 16:00 (the indexes' after-close prints) is no bar of the session and is left "
    "out of every reading. A reading at time T is the close of the last bar whose end is at or before T "
    "on that day, never a bar still in progress. Judged (the declaration's 'closes on shared bars'): each "
    "Yahoo bar's close against the broker's close at the same instant, the broker's 5-minute bar ending "
    "exactly at that bar's end, for VIX, VIX9D and VIX3M, and the state each source gives at that "
    "instant. The broker's 5-minute history "
    "begins 2026-08-10 (VIX 2026-08-25), so the judged comparison covers the days both hold, inside the "
    "declared span. Reported only: the first build's reading of each source at whole hours 11:00-16:00 "
    "from the broker's hourly bars, where Yahoo's reading is up to 30 minutes older than the broker's."
)


# --------------------------------------------------------------------------- paths


def out_dir() -> Path:
    d = paths.results_dir() / "research" / f"item{ITEM}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def yahoo_db_path() -> Path:
    return out_dir() / "yahoo-bars.db"


def broker_db_path() -> Path:
    return home.data_dir("market-files") / "vol_curve_bars.db"


def cboe_path(name: str) -> Path:
    return home.data_dir("market-files") / "cboe" / f"{name}.csv"


# --------------------------------------------------------------------------- the exchange's daily files


def read_daily(path: Path) -> dict[date, dict[str, float]]:
    """A Cboe daily CSV as {day: {column: value}}, columns lower-cased. SPX's file has one price column
    (`spx`), which is a close, so it is also given as `close`."""
    out: dict[date, dict[str, float]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            low = {k.strip().lower(): v for k, v in row.items() if k}
            try:
                day = datetime.strptime(low["date"], "%m/%d/%Y").date()
            except (KeyError, ValueError):
                continue
            vals = {}
            for k, v in low.items():
                if k != "date" and v not in (None, ""):
                    try:
                        vals[k] = float(v)
                    except ValueError:
                        pass
            if "spx" in vals and "close" not in vals:
                vals["close"] = vals["spx"]
            out[day] = vals
    return out


def state_of(vix: float | None, vix3m: float | None, vix9d: float | None) -> str | None:
    """`inverted` (VIX/VIX3M >= 1), `hump` (VIX9D/VIX >= 1 with VIX under VIX3M), else `calm`; None when a
    print is missing or not positive."""
    if any(x is None or x <= 0 for x in (vix, vix3m, vix9d)):
        return None
    if vix / vix3m >= 1.0:
        return "inverted"
    if vix9d / vix >= 1.0:
        return "hump"
    return "calm"


def open_states(vix: dict, vix3m: dict, vix9d: dict) -> dict[str, str | None]:
    """Part 1's flag per day, from the OPEN prints."""
    days = set(vix) | set(vix3m) | set(vix9d)
    return {
        d.isoformat(): state_of(
            vix.get(d, {}).get("open"), vix3m.get(d, {}).get("open"), vix9d.get(d, {}).get("open")
        )
        for d in days
    }


# --------------------------------------------------------------------------- hourly bars: both sources

#: (symbol) -> {day: [(end, close), ...]} sorted by end, `end` a tz-aware ET datetime.
Series = dict[str, dict[str, list[tuple[datetime, float]]]]


def bar_end(day: str, hhmm: str, width_min: int = 60) -> datetime:
    """The end of a bar that starts at `hhmm` ET on `day`: its start plus its width, capped at 16:00."""
    start = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ET)
    close = start.replace(hour=CLOSE_OF_DAY[0], minute=CLOSE_OF_DAY[1])
    return min(start + timedelta(minutes=width_min), close)


def build_series(rows: Iterable[tuple[str, str, str, float]], width_min: int = 60) -> Series:
    """Series from (symbol, et_day, et_start 'HH:MM', close) rows of bars `width_min` wide."""
    out: Series = {}
    for sym, day, hhmm, close in rows:
        if hhmm >= "16:00":
            continue  # the after-close prints: no bar of the session
        out.setdefault(sym, {}).setdefault(day, []).append((bar_end(day, hhmm, width_min), float(close)))
    for by_day in out.values():
        for bars in by_day.values():
            bars.sort()
    return out


def read_at(series: Series, symbol: str, when: datetime) -> float | None:
    """The close of the last bar finished by `when` (its end at or before `when`), on `when`'s own day;
    None before the day's first bar has finished. A bar in progress is never read."""
    when = when.astimezone(ET)
    best = None
    for end, close in series.get(symbol, {}).get(when.date().isoformat(), []):
        if end <= when:
            best = close
        else:
            break
    return best


def curve_at(series: Series, when: datetime) -> str | None:
    return state_of(*(read_at(series, s, when) for s in ("VIX", "VIX3M", "VIX9D")))


def load_series(db: Path, table: str, interval: str | None = None, symbols=SYMBOLS) -> Series:
    conn = sqlite3.connect(db)
    try:
        marks = ",".join("?" * len(symbols))
        sql = f"SELECT symbol, et_day, et_time, close FROM {table} WHERE symbol IN ({marks})"
        args: list = list(symbols)
        if interval is not None:
            sql += " AND interval = ?"
            args.append(interval)
        return build_series(conn.execute(sql, args), 5 if interval == "5m" else 60)
    finally:
        conn.close()


# --------------------------------------------------------------------------- the Yahoo fetch

YAHOO_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS yahoo_bars (symbol TEXT NOT NULL, ts_ms INTEGER NOT NULL, "
    "et_day TEXT NOT NULL, et_time TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL, "
    "fetched_at REAL NOT NULL, PRIMARY KEY (symbol, ts_ms))",
    "CREATE TABLE IF NOT EXISTS yahoo_daily (symbol TEXT NOT NULL, day TEXT NOT NULL, open REAL, "
    "close REAL, fetched_at REAL NOT NULL, PRIMARY KEY (symbol, day))",
)


def regular_hours_rows(
    symbol: str, bars: Iterable[tuple[datetime, float, float, float, float]]
) -> list[dict]:
    """Yahoo bars as stored rows: keyed by the bar's start in epoch ms, with its ET day and start time,
    regular session only (a start from 09:30 up to, not including, 16:00 ET; the VIX series also carries
    overnight bars) and never a bar without a positive close. A naive timestamp is read as UTC."""
    out = []
    for ts, o, h, low, c in bars:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        et = ts.astimezone(ET)
        if not ((9, 30) <= (et.hour, et.minute) < CLOSE_OF_DAY):
            continue
        if c is None or not c > 0:
            continue
        out.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts.timestamp() * 1000),
                "et_day": et.date().isoformat(),
                "et_time": et.strftime("%H:%M"),
                "open": o,
                "high": h,
                "low": low,
                "close": float(c),
            }
        )
    return out


def upsert_bars(conn: sqlite3.Connection, rows: list[dict], now: float) -> int:
    for stmt in YAHOO_SCHEMA:
        conn.execute(stmt)
    conn.executemany(
        "INSERT OR REPLACE INTO yahoo_bars "
        "(symbol, ts_ms, et_day, et_time, open, high, low, close, fetched_at) "
        "VALUES (:symbol, :ts_ms, :et_day, :et_time, :open, :high, :low, :close, :now)",
        [{**r, "now": now} for r in rows],
    )
    conn.commit()
    return len(rows)


def upsert_daily(
    conn: sqlite3.Connection, symbol: str, rows: list[tuple[str, float | None, float | None]], now: float
) -> int:
    for stmt in YAHOO_SCHEMA:
        conn.execute(stmt)
    conn.executemany(
        "INSERT OR REPLACE INTO yahoo_daily (symbol, day, open, close, fetched_at) VALUES (?, ?, ?, ?, ?)",
        [(symbol, d, o, c, now) for d, o, c in rows],
    )
    conn.commit()
    return len(rows)


def _frame_bars(frame) -> list[tuple[datetime, float, float, float, float]]:
    out = []
    for ts, row in frame.iterrows():
        out.append((ts.to_pydatetime(), row["Open"], row["High"], row["Low"], row["Close"]))
    return out


def cmd_fetch_yahoo(args) -> int:
    """Hourly bars for the three curve indexes and the daily ^GSPC, upserted under the item's results
    dir. Hourly history is Yahoo's 730-day period (it reaches back to 2024-01-19 as of 2026-11-05); rows
    are upserted by bar start, so repeated runs extend the history as Yahoo's window moves on."""
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance is not installed", file=sys.stderr)
        return 2
    now = time.time()
    conn = sqlite3.connect(yahoo_db_path())
    summary: dict[str, dict] = {}
    try:
        for sym, ticker in YAHOO.items():
            tk = yf.Ticker(ticker)
            rows: list[dict] = []
            failed = 0
            try:
                frame = tk.history(period="730d", interval="1h", auto_adjust=False, actions=False)
            except Exception as exc:  # noqa: BLE001  one symbol failing is counted, not fatal
                failed += 1
                print(f"{sym}: fetch failed: {exc}", file=sys.stderr)
                frame = None
            if frame is not None and len(frame):
                rows += regular_hours_rows(sym, _frame_bars(frame))
            kept = upsert_bars(conn, rows, now) if rows else 0
            days = sorted({r["et_day"] for r in rows})
            summary[sym] = {
                "rows": kept,
                "first": days[0] if days else None,
                "last": days[-1] if days else None,
                "failed_windows": failed,
            }
        daily = yf.Ticker(YAHOO_SPX).history(period="max", interval="1d", auto_adjust=False, actions=False)
        recs = [
            (ts.date().isoformat(), float(r["Open"]), float(r["Close"]))
            for ts, r in daily.iterrows()
            if r["Open"] and r["Open"] > 0
        ]
        summary["SPX_daily"] = {"rows": upsert_daily(conn, "SPX", recs, now) if recs else 0}
    finally:
        conn.close()
    if not any(v["rows"] for v in summary.values()):
        print("nothing was fetched", file=sys.stderr)
        return 1
    print(json.dumps({"written": str(yahoo_db_path()), **summary}, indent=1))
    return 0


# --------------------------------------------------------------------------- validation


def _close_ending_at(series: Series, symbol: str, when: datetime) -> float | None:
    """The close of the bar that ends exactly at `when`, or None."""
    for end, close in series.get(symbol, {}).get(when.date().isoformat(), []):
        if end == when:
            return close
    return None


def validate_same_instant(yahoo: Series, broker5m: Series, span: tuple[str, str]) -> dict | None:
    """The judged validation (`ALIGNMENT`): each Yahoo bar's close against the broker's 5-minute close
    ending at the same instant, and the two sources' states there, on the days the broker's 5-minute
    series holds all three symbols inside `span`. None when no instant is shared."""
    days = sorted(
        d
        for d in set.intersection(*(set(yahoo.get(s, {})) & set(broker5m.get(s, {})) for s in SYMBOLS))
        if span[0] <= d <= span[1]
    )
    close_n = close_ok = state_n = state_ok = 0
    for d in days:
        ends = sorted({end for s in SYMBOLS for end, _ in yahoo[s].get(d, [])})
        for end in ends:
            for s in SYMBOLS:
                y, b = _close_ending_at(yahoo, s, end), _close_ending_at(broker5m, s, end)
                if y is None or b is None:
                    continue
                close_n += 1
                close_ok += abs(y / b - 1.0) <= CLOSE_TOL
            ys = state_of(*(_close_ending_at(yahoo, s, end) for s in ("VIX", "VIX3M", "VIX9D")))
            bs = state_of(*(_close_ending_at(broker5m, s, end) for s in ("VIX", "VIX3M", "VIX9D")))
            if ys is not None and bs is not None:
                state_n += 1
                state_ok += ys == bs
    if not close_n or not state_n:
        return None
    close_share, state_share = close_ok / close_n, state_ok / state_n
    return {
        "span": [days[0], days[-1]],
        "days": len(days),
        "close_within_1pct": {"n": close_n, "ok": close_ok, "share": round(close_share, 4), "bar": SHARE_BAR},
        "state_agreement": {"n": state_n, "ok": state_ok, "share": round(state_share, 4), "bar": SHARE_BAR},
        "passed": close_share >= SHARE_BAR and state_share >= SHARE_BAR,
    }


def validate_series(yahoo: Series, broker: Series, span: tuple[str, str]) -> dict | None:
    """Reported only: Yahoo against the broker's hourly bars over `span` at each whole hour 11:00-16:00
    ET, where Yahoo's reading is up to 30 minutes older (`ALIGNMENT`). None when no day is shared."""
    days = sorted(
        d
        for d in set.intersection(*(set(yahoo.get(s, {})) & set(broker.get(s, {})) for s in SYMBOLS))
        if span[0] <= d <= span[1]
    )
    if not days:
        return None
    close_n = close_ok = state_n = state_ok = 0
    same_n = same_ok = 0
    by_hour: dict[int, dict[str, int]] = {h: {"n": 0, "ok": 0} for h in READ_HOURS}
    for d in days:
        for h in READ_HOURS:
            when = datetime.fromisoformat(f"{d}T{h:02d}:00:00").replace(tzinfo=ET)
            for s in SYMBOLS:
                y, b = read_at(yahoo, s, when), read_at(broker, s, when)
                if y is None or b is None:
                    continue
                ok = abs(y / b - 1.0) <= CLOSE_TOL
                close_n += 1
                close_ok += ok
                by_hour[h]["n"] += 1
                by_hour[h]["ok"] += ok
                if h == 16:
                    same_n += 1
                    same_ok += ok
            ys, bs = curve_at(yahoo, when), curve_at(broker, when)
            if ys is not None and bs is not None:
                state_n += 1
                state_ok += ys == bs
    if not close_n or not state_n:
        return None
    close_share, state_share = close_ok / close_n, state_ok / state_n
    return {
        "span": list(span),
        "days": len(days),
        "close_within_1pct": {"n": close_n, "ok": close_ok, "share": round(close_share, 4), "bar": SHARE_BAR},
        "state_agreement": {"n": state_n, "ok": state_ok, "share": round(state_share, 4), "bar": SHARE_BAR},
        "by_hour": {
            str(h): {**v, "share": round(v["ok"] / v["n"], 4) if v["n"] else None} for h, v in by_hour.items()
        },
        "same_end_16_00": {
            "n": same_n,
            "ok": same_ok,
            "share": round(same_ok / same_n, 4) if same_n else None,
        },
        "alignment": ALIGNMENT,
        "passed": close_share >= SHARE_BAR and state_share >= SHARE_BAR,
    }


def cmd_validate(args) -> int:
    y_path, b_path = yahoo_db_path(), broker_db_path()
    for p in (y_path, b_path):
        if not p.exists():
            print(f"missing {p}: nothing to validate", file=sys.stderr)
            return 2
    yahoo = load_series(y_path, "yahoo_bars")
    judged = validate_same_instant(
        yahoo, load_series(b_path, "vol_curve_bars", interval="5m"), VALIDATION_SPAN
    )
    if judged is None:
        print(
            "no shared instant with all three symbols in both sources: validation not written",
            file=sys.stderr,
        )
        return 2
    hourly = validate_series(yahoo, load_series(b_path, "vol_curve_bars", interval="1h"), VALIDATION_SPAN)
    result = {
        "item": ITEM,
        "verdict": "pass" if judged["passed"] else "fail",
        **judged,
        "alignment": ALIGNMENT,
        "reported_whole_hours_hourly_bars": hourly,
    }
    path = out_dir() / "validation.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": result["verdict"]}, indent=1))
    return 0


def require_validation() -> None:
    path = out_dir() / "validation.json"
    if not path.exists():
        raise SystemExit("part 2 needs `validate` to have run: no validation.json")
    if not json.loads(path.read_text(encoding="utf-8")).get("passed"):
        raise SystemExit(
            "part 2 refuses: Yahoo's bars did not validate against the broker's. The declaration then runs "
            "part 2 on the broker's recorded bars only, forward; that reading is not built yet."
        )


# --------------------------------------------------------------------------- the ledgers


def credited_diff(e: dict) -> float:
    """The variant's net less the base's for one entry: minus the call side's net (its legs' P&L less half
    the trade's fees), so the call side's half of the fees is credited back."""
    return -(e["call_pnl"] - e["fees"] / 2.0)


def kept_diff(e: dict) -> float:
    """The same with the call side's fees NOT credited back: the variant still pays the whole fee."""
    return -e["call_pnl"]


def entry_net(e: dict) -> float:
    return e["pnl"] - e["fees"]


def condor_risk(e: dict) -> float:
    return (e["wing"] - e["net_credit"]) * 100 * e["qty"]


def put_risk(e: dict) -> float:
    return (e["wing"] - e["put_credit"]) * 100 * e["qty"]


def read_entries(ledger_dir: Path, arm: str, start: str, end: str) -> tuple[list[dict], dict]:
    """The arm's entries in `start..end` from a run's per-session ledgers, with the counts of entries left
    out (no result yet, or no call leg result) and of entries whose legs do not add to the trade's P&L."""
    entries: list[dict] = []
    skipped = leg_mismatch = non_unit = 0
    for db in sorted(Path(ledger_dir).glob("*.db")):
        if not (start <= db.stem <= end):
            continue
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                "SELECT t.trade_date, t.entry_time, t.pnl, t.fees, t.put_credit, t.net_credit, t.wing_width, "
                "t.quantity, t.underlying_price_entry, "
                "(SELECT SUM(pnl) FROM ic_spread_legs l "
                "WHERE l.ic_order_id = t.ic_order_id AND l.side = 'call') AS call_pnl, "
                "(SELECT SUM(pnl) FROM ic_spread_legs l "
                "WHERE l.ic_order_id = t.ic_order_id AND l.side = 'put') AS put_pnl "
                "FROM ic_trades t WHERE t.arm = ?",
                (arm,),
            ):
                if r["pnl"] is None or r["call_pnl"] is None or not r["entry_time"]:
                    skipped += 1
                    continue
                when = datetime.fromisoformat(r["entry_time"])
                when = when.replace(tzinfo=ET) if when.tzinfo is None else when.astimezone(ET)
                qty = int(r["quantity"] or 1)
                non_unit += qty != 1
                if r["put_pnl"] is not None and abs(r["put_pnl"] + r["call_pnl"] - r["pnl"]) > 0.01:
                    leg_mismatch += 1
                entries.append(
                    {
                        "session": r["trade_date"],
                        "when": when,
                        "pnl": float(r["pnl"]),
                        "fees": float(r["fees"] or 0.0),
                        "call_pnl": float(r["call_pnl"]),
                        "put_credit": float(r["put_credit"] or 0.0),
                        "net_credit": float(r["net_credit"] or 0.0),
                        "wing": float(r["wing_width"] or 0.0),
                        "qty": qty,
                        "spot": r["underlying_price_entry"],
                    }
                )
        finally:
            conn.close()
    entries.sort(key=lambda e: e["when"])
    return entries, {
        "skipped_entries": skipped,
        "leg_sum_mismatch_entries": leg_mismatch,
        "quantity_not_one": non_unit,
    }


def net_mismatch_sessions(entries: list[dict], recorded: dict[str, dict], tol: float = 0.01) -> int:
    """Sessions whose per-entry net differs from the run's recorded session net by more than `tol`."""
    by_day: dict[str, float] = {}
    for e in entries:
        by_day[e["session"]] = by_day.get(e["session"], 0.0) + entry_net(e)
    return sum(1 for d, s in recorded.items() if d in by_day and abs(by_day[d] - s["net"]) > tol)


def _recorded_run_id(file: Path, key: str = "run_id") -> str | None:
    return json.loads(file.read_text(encoding="utf-8")).get(key)


def run_for(base: str, window: str) -> str | None:
    """The run whose ledgers hold `base` for `window`: item 42's for `be`, item 43's for `w15`, item 43's
    design run for `live-1400` and, for the holdout, item 23's recorded run (as items 43 and 50 read it)."""
    research = paths.results_dir() / "research"
    item = {"be": 42, "w15": 43, "live-1400": 43}[base]
    if base == "live-1400" and window == "holdout":
        item = 23
    files = sorted(
        f
        for f in (research / f"item{item}").glob(f"study-{window}-*.json")
        if not f.name.endswith(".version.json")
    )
    return _recorded_run_id(files[-1]) if files else None


# --------------------------------------------------------------------------- statistics


def stats_block(diffs: dict[str, float], start: str, end: str) -> dict:
    """The paired differences of one cell (one per session): mean, one-sided t, halves of `start..end`,
    by year and the mean with the best year (the largest summed difference) removed."""
    days = sorted(diffs)
    xs = [diffs[d] for d in days]
    t = i43.t_stat(xs)
    mid = i43.midpoint(start, end)
    years: dict[str, list[float]] = {}
    for d in days:
        years.setdefault(d[:4], []).append(diffs[d])
    best = max(years, key=lambda y: sum(years[y])) if years else None
    rest = [diffs[d] for d in days if d[:4] != best]
    return {
        "n": len(xs),
        "mean": i43._mean(xs),
        "t": None if t is None else round(t, 3),
        "p": i43.p_one(t, len(xs)),
        "halves": {
            "first": i43._mean([diffs[d] for d in days if d < mid]),
            "second": i43._mean([diffs[d] for d in days if d >= mid]),
        },
        "by_year": {
            y: {"n": len(v), "sum": round(sum(v), 2), "mean": i43._mean(v)} for y, v in sorted(years.items())
        },
        "best_year": best,
        "mean_without_best_year": i43._mean(rest),
    }


def episodes(flagged: Iterable[str], sessions: list[str], gap: int = EPISODE_GAP) -> list[list[str]]:
    """Runs of flagged sessions with at most `gap` sessions of `sessions` between consecutive flagged ones."""
    order = {d: i for i, d in enumerate(sorted(sessions))}
    days = sorted((d for d in flagged if d in order), key=order.get)
    out: list[list[str]] = []
    for d in days:
        if out and order[d] - order[out[-1][-1]] - 1 <= gap:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def without_each_episode(diffs: dict[str, float], sessions: list[str], start: str, end: str) -> list[dict]:
    out = []
    for ep in episodes(diffs, sessions):
        rest = {d: v for d, v in diffs.items() if d not in set(ep)}
        s = stats_block(rest, start, end)
        out.append(
            {
                "from": ep[0],
                "to": ep[-1],
                "flagged_sessions": len(ep),
                "n_left": s["n"],
                "mean_left": s["mean"],
                "t_left": s["t"],
                "positive_left": (s["mean"] or 0) > 0,
            }
        )
    return out


def bin_of(x: float) -> str:
    a = abs(x)
    return BIN_NAMES[0] if a < BIN_EDGES[0] else BIN_NAMES[1] if a < BIN_EDGES[1] else BIN_NAMES[2]


def binned(items: Iterable[tuple[float | None, float]]) -> dict[str, dict]:
    """(measure, difference) pairs by `bin_of`; a missing measure goes to `unknown`."""
    acc: dict[str, list[float]] = {}
    for x, v in items:
        acc.setdefault("unknown" if x is None else bin_of(x), []).append(v)
    return {
        k: {"n": len(acc[k]), "mean": i43._mean(acc[k]), "sum": round(sum(acc[k]), 2)}
        for k in (*BIN_NAMES, "unknown")
        if k in acc
    }


def per_buying_power(entries: list[dict]) -> dict:
    """Net over buying power over `entries`: the condor's, and the put side alone with the call side's
    fees credited and kept."""
    base_net = sum(entry_net(e) for e in entries)
    base_bp = sum(condor_risk(e) for e in entries)
    var_bp = sum(put_risk(e) for e in entries)
    credited = base_net + sum(credited_diff(e) for e in entries)
    kept = base_net + sum(kept_diff(e) for e in entries)

    def ratio(a: float, b: float) -> float | None:
        return round(a / b, 5) if b > 0 else None

    return {
        "condor": ratio(base_net, base_bp),
        "put_only_fees_credited": ratio(credited, var_bp),
        "put_only_fees_kept": ratio(kept, var_bp),
    }


# --------------------------------------------------------------------------- the cells


def gap_pct(day: str, spx_open: dict[str, float], spx_close: dict[date, dict]) -> float | None:
    """The overnight gap: SPX's open over the previous session's close in the exchange's file."""
    o = spx_open.get(day)
    prev = [d for d in spx_close if d < date.fromisoformat(day)]
    if o is None or not prev:
        return None
    return o / spx_close[max(prev)]["close"] - 1.0


def build_cell(
    flagged: dict[str, list[dict]],
    sessions: list[str],
    start: str,
    end: str,
    spx_open: dict[str, float],
    spx_close: dict[date, dict],
    flagged_total: int,
) -> dict:
    """One cell from the flagged entries by session. Observations are the sessions with at least one."""
    credited = {d: sum(credited_diff(e) for e in es) for d, es in flagged.items() if es}
    kept = {d: sum(kept_diff(e) for e in es) for d, es in flagged.items() if es}
    flat = [e for d in sorted(flagged) for e in flagged[d]]
    move = []
    for e in flat:
        o = spx_open.get(e["session"])
        move.append(((e["spot"] / o - 1.0) if o and e["spot"] else None, credited_diff(e)))
    gaps = [(gap_pct(d, spx_open, spx_close), v) for d, v in credited.items()]
    return {
        "flagged_sessions": flagged_total,
        "flagged_no_entry": flagged_total - len(credited),
        "entries": len(flat),
        "paired": stats_block(credited, start, end),
        "fees_not_credited": stats_block(kept, start, end),
        "by_episode_removed": without_each_episode(credited, sessions, start, end),
        "by_move_from_open": binned(move),
        "by_overnight_gap": binned(gaps),
        "net_over_buying_power": per_buying_power(flat),
    }


def judge(cells: dict[str, dict], floor: int) -> None:
    """Verdicts for the part's cells, Holm across all of them."""
    ps = {k: (v["paired"]["p"] if v["paired"]["n"] >= floor else 1.0) for k, v in cells.items()}
    holm = core_stats.holm(ps, alpha=ALPHA)
    for k, v in cells.items():
        d = v["paired"]
        v["holm_ok"] = holm[k]
        if d["n"] < floor:
            v["verdict"] = "no verdict"
            v["why"] = f"{d['n']} flagged sessions < {floor}"
            continue
        checks = {
            "paired_mean_positive": (d["mean"] or 0) > 0,
            "paired_t_above_bar": (d["t"] or 0) > T_BAR,
            "holm": holm[k],
            "paired_both_halves_positive": all((x or 0) > 0 for x in d["halves"].values()),
            "positive_with_fees_not_credited": (v["fees_not_credited"]["mean"] or 0) > 0,
            "positive_without_best_year": (d["mean_without_best_year"] or 0) > 0,
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def judge_holdout(cells: dict[str, dict], design: dict, floor: int) -> None:
    """The holdout: a cell that passed the design window passes if its paired mean is above zero over at
    least `floor` flagged sessions; any other cell keeps its design verdict."""
    for k, v in cells.items():
        dv = design.get("arms", {}).get(k, {}).get("verdict", "no verdict")
        v["design_verdict"] = dv
        n, mean = v["paired"]["n"], v["paired"]["mean"]
        if dv != "pass":
            v["verdict"] = dv
            v["why"] = f"design window: {dv}"
        elif n < floor:
            v["verdict"] = "no verdict"
            v["why"] = f"{n} flagged sessions < {floor}"
        else:
            v["checks"] = {"holdout_mean_positive": (mean or 0) > 0}
            v["verdict"] = "pass" if (mean or 0) > 0 else "fail"


def overall(cells: dict[str, dict]) -> str:
    verdicts = [v["verdict"] for v in cells.values()]
    if "pass" in verdicts:
        return "pass"
    return "no verdict" if verdicts and all(x == "no verdict" for x in verdicts) else "fail"


def period_of(day: str) -> str:
    """`judged` for the holdout's sessions (up to 2026-01-23, and any after 2026-10-12), `explored` for
    2026-01-26..2026-10-12, `design` before the holdout."""
    if day < HOLDOUT_START:
        return "design"
    if day <= HOLDOUT_JUDGED_END or day > EXPLORED_END:
        return "judged"
    return "explored"


def flag_entries(
    part: int, entries: list[dict], states: dict[str, str | None], series: Series | None
) -> tuple[dict, int]:
    """{state: {session: [flagged entries]}} for the two stressed states, and the entries left unread (no
    open print for the day in part 1, no finished bar yet in part 2)."""
    out: dict[str, dict[str, list[dict]]] = {s: {} for s in STATES}
    unread = 0
    for e in entries:
        st = states.get(e["session"]) if part == 1 else curve_at(series, e["when"])
        if st is None:
            unread += 1
        elif st in out:
            out[st].setdefault(e["session"], []).append(e)
    return out, unread


def session_flags(sessions: list[str], states: dict[str, str | None]) -> dict[str, int]:
    """Part 1's flagged sessions per state, entries or not (the no-entry ones are reported)."""
    return {s: sum(1 for d in sessions if states.get(d) == s) for s in STATES}


# --------------------------------------------------------------------------- the study


def load_spx(path_open: Path) -> dict[str, float]:
    if not path_open.exists():
        return {}
    conn = sqlite3.connect(path_open)
    try:
        return {
            d: o
            for d, o in conn.execute("SELECT day, open FROM yahoo_daily WHERE symbol = 'SPX' AND open > 0")
        }
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def study(part: int, window: str) -> dict:
    for name in ("VIX", "VIX3M", "VIX9D"):
        if not cboe_path(name).exists():
            raise SystemExit(f"missing {cboe_path(name)}: the exchange's daily file is an input")
    vix, vix3m, vix9d = (read_daily(cboe_path(n)) for n in ("VIX", "VIX3M", "VIX9D"))
    states = open_states(vix, vix3m, vix9d)
    series = None
    if part == 2:
        require_validation()
        if not yahoo_db_path().exists():
            raise SystemExit("part 2 needs `fetch-yahoo` to have run")
        series = load_series(yahoo_db_path(), "yahoo_bars")
    spx_close = read_daily(cboe_path("SPX")) if cboe_path("SPX").exists() else {}
    spx_open = load_spx(yahoo_db_path())

    design_doc = None
    if window == "holdout":
        files = sorted(
            f
            for f in out_dir().glob(f"study-part{part}-design-*.json")
            if not f.name.endswith(".version.json")
        )
        if not files:
            raise SystemExit(
                f"the part {part} design result is missing: the holdout reads only cells that passed it"
            )
        design_doc = json.loads(files[-1].read_text(encoding="utf-8"))
    d_start, d_end = DESIGN[part]
    start, end = (d_start, d_end) if window == "design" else (HOLDOUT_START, "9999-12-31")

    ids, checks, per_base = {}, {}, {}
    for base in BASES:
        rid = run_for(base, window)
        ledger = paths.results_dir() / "runs" / rid if rid else None
        if not rid or not ledger.is_dir():
            raise SystemExit(f"no run for {base} in the {window} window: its ledgers are an input")
        ids[base] = rid
        entries, counts = read_entries(ledger, base, start, end)
        recorded = {d: s for d, s in i43.session_rows(rid).get(base, {}).items() if start <= d <= end}
        counts["net_mismatch_sessions"] = net_mismatch_sessions(entries, recorded)
        counts["sessions"] = len(recorded)
        checks[base] = counts
        per_base[base] = (entries, sorted(recorded))
    if not any(ss for _, ss in per_base.values()):
        raise SystemExit(f"no sessions in the {window} window")
    span_end = d_end if window == "design" else max(ss[-1] for _, ss in per_base.values() if ss)

    cells: dict[str, dict] = {}
    explored: dict[str, dict] = {}
    labels = ("design",) if window == "design" else ("judged", "explored")
    for base, (entries, sessions) in per_base.items():
        for label in labels:
            es = [e for e in entries if label == "design" or period_of(e["session"]) == label]
            ss = [d for d in sessions if label == "design" or period_of(d) == label]
            flagged, unread = flag_entries(part, es, states, series)
            totals = session_flags(ss, states) if part == 1 else {s: len(flagged[s]) for s in STATES}
            for st in STATES:
                cell = build_cell(flagged[st], ss, start, span_end, spx_open, spx_close, totals[st])
                cell["entries_unread"] = unread
                (explored if label == "explored" else cells)[f"{base}:{st}"] = cell
    floor = MIN_FLAGGED[part]
    if window == "design":
        judge(cells, floor)
    else:
        judge_holdout(cells, design_doc, floor)
    return {
        "item": ITEM,
        "part": part,
        "window": window,
        "span": [start, span_end],
        "run_ids": ids,
        "provisional": True,
        "floor": floor,
        "episode_definition": EPISODE_DEFINITION,
        "move_bins": list(BIN_NAMES),
        "ledger_check": checks,
        "arms": cells,
        "explored_2026_01_26_to_10_12": explored if window == "holdout" else None,
        "verdict": overall(cells),
    }


# --------------------------------------------------------------------------- results.md


def _f(x, nd=2) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def report(doc: dict) -> str:
    lines = [
        f"# Item {ITEM}, part {doc['part']}, {doc['window']} window {doc['span'][0]} to {doc['span'][1]}",
        "",
        f"**Verdict: {doc['verdict']}.** Provisional: MEIC history has not unlocked. The measure is the "
        "variant's net less the base's per flagged session, in dollars as traded; one session one "
        f"observation. Floor {doc['floor']} flagged sessions. Episodes: {doc['episode_definition']}.",
        "",
        "| Cell | Verdict | Flagged | With entry | Mean | t | 1st half | 2nd half | Fees kept "
        "| No best year |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in doc["arms"].items():
        p = v["paired"]
        lines.append(
            f"| {k} | {v['verdict']} | {v['flagged_sessions']} | {p['n']} | {_f(p['mean'])} "
            f"| {_f(p['t'], 2)} | {_f(p['halves']['first'])} | {_f(p['halves']['second'])} "
            f"| {_f(v['fees_not_credited']['mean'])} | "
            f"{_f(p['mean_without_best_year'])} |"
        )
    for k, v in doc["arms"].items():
        lines += ["", f"## {k}", ""]
        if v.get("why"):
            lines.append(f"{v['verdict']}: {v['why']}.")
        if v.get("checks"):
            lines.append(
                "Checks: " + ", ".join(f"{c} {'yes' if ok else 'NO'}" for c, ok in v["checks"].items()) + "."
            )
        lines.append(
            "By year (sessions, summed difference): "
            + (
                ", ".join(f"{y} {b['n']} / {_f(b['sum'])}" for y, b in v["paired"]["by_year"].items())
                or "none"
            )
            + "."
        )
        if v["by_episode_removed"]:
            lines.append("Each episode removed in turn (left mean, t):")
            for e in v["by_episode_removed"]:
                lines.append(
                    f"- {e['from']} to {e['to']} ({e['flagged_sessions']} sessions): "
                    f"{_f(e['mean_left'])}, t {_f(e['t_left'])}"
                )
        for title, key in (
            ("Move from the open, per entry", "by_move_from_open"),
            ("Overnight gap, per session", "by_overnight_gap"),
        ):
            lines.append(
                f"{title} (n, mean): "
                + (", ".join(f"{b} {x['n']} / {_f(x['mean'])}" for b, x in v[key].items()) or "none")
                + "."
            )
        bp = v["net_over_buying_power"]
        lines.append(
            f"Net over buying power: condor {_f(bp['condor'], 4)}, "
            f"put side alone {_f(bp['put_only_fees_credited'], 4)} "
            f"(fees credited), {_f(bp['put_only_fees_kept'], 4)} (fees kept)."
        )
    lines += ["", "Ledger check (counts only):"]
    for base, c in doc["ledger_check"].items():
        lines.append(f"- {base}: " + ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in c.items()))
    return "\n".join(lines) + "\n"


def study_path(doc: dict) -> Path:
    return out_dir() / f"study-part{doc['part']}-{doc['window']}-{doc['span'][0]}-{doc['span'][1]}.json"


def cmd_study(args) -> int:
    doc = study(args.part, args.window)
    path = Path(args.json) if args.json else study_path(doc)
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(doc), encoding="utf-8")
    print(
        json.dumps(
            {
                "written": str(path),
                "verdict": doc["verdict"],
                **{k: v["verdict"] for k, v in doc["arms"].items()},
            },
            indent=1,
        )
    )
    return 0


def cmd_report(args) -> int:
    files = [f for f in sorted(out_dir().glob("study-part*.json")) if not f.name.endswith(".version.json")]
    if not files:
        print("no study results to report on", file=sys.stderr)
        return 1
    for f in files:
        f.with_suffix(".md").write_text(report(json.loads(f.read_text(encoding="utf-8"))), encoding="utf-8")
    print(json.dumps({"reported": [f.name for f in files]}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch-yahoo", help="hourly VIX, VIX9D, VIX3M and daily ^GSPC into ~/.cherrypick")
    fp.set_defaults(fn=cmd_fetch_yahoo)
    vp = sub.add_parser("validate", help="Yahoo's bars against the broker's recorded ones")
    vp.set_defaults(fn=cmd_validate)
    sp = sub.add_parser("study", help="one part, one window")
    sp.add_argument("--part", type=int, choices=(1, 2), required=True)
    sp.add_argument("--window", choices=("design", "holdout"), required=True)
    sp.add_argument("--json", default=None)
    sp.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md again from the existing result files")
    rp.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
