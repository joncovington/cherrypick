"""OCC's daily option volume by underlying, and the "hot options" ranking the pack reads from it.

The Options Insider's *Hot Options Report* counts down, each day, the five index products (VIX,
SPY, SPX, IWM, QQQ) and the ten most active single-name equity options. Its numbers are contract
volumes, and the primary source for those is public: OCC clears every listed US option and its
volume query (`marketdata.theocc.com/volume-query`) answers one keyless CSV per session covering
every underlying (~4,400 of them, ~130k rows), split by account type, call/put and exchange. This
module is that ranking, rebuilt from OCC's file so it is reproducible for any past session.

**OCC counts both sides of every trade.** Each contract traded has a buyer and a seller, each
cleared under an account type -- customer (`C`), firm (`F`) or market maker (`M`) -- so the file's
quantities sum to twice the contracts traded. Verified 2026-09-30 against Cboe's own daily
statistics: VIX 1,070,156 in OCC's file against Cboe's 535,078 (exactly 2x), SPX+SPXW 2.008x (the
odd-lot roots `2SPX`/`4SPX` are OCC's alone). Every underlying's total and every call and put
subtotal was even that day, so halving is exact. The store holds the sides as OCC states them;
contracts are halved on read.

**OCC publishes a session the next day, not after its close.** 2026-10-01's file was still a "no
data" reply at 22:20 ET that evening; 2026-09-30's was up by 18:00 ET on 10-01. So the pack reads
the newest session strictly before its own, says which session that is, and counts how many
sessions it lags.

**Stock or fund comes from Nasdaq Trader's symbol directory** (`nasdaqtraded.txt`), every
US-listed security with an `ETF` flag. OCC's file does not say what an underlying is, and the
ranking is the show's split: single-name equities apart from funds. An underlying the directory does
not list (the cash indexes, a delisting) is in neither list; where one ranks high it is named, never
guessed into one.

Fetched by `scripts/fetch_market_files.py` into `~/.cherrypick/data/market-files/occ/`; read here
read-only. The parsers live here and the fetcher imports them, so a file is validated on arrival by
the code that reads it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import calendar as _calendar

from . import files as _files

OCC_URL = (
    "https://marketdata.theocc.com/volume-query?reportDate={yyyymmdd}&format=csv&volumeQueryType=O"
    "&symbolType=ALL&symbol=&reportType=D&accountType=ALL&productKind=ALL&porc=BOTH"
)
# A real session runs to ~4 MB; a "no data" reply (a session not yet published) is tens of bytes.
OCC_MIN_BYTES = 100_000
# Fewer underlyings than this is a truncated or wrong file, not a quiet day (a real one has ~4,400).
OCC_MIN_UNDERLYINGS = 1_000
# Sessions the fetcher keeps landed: the ranked one plus a baseline for relative volume.
OCC_SESSIONS = 25

LISTINGS_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
# The directory lists ~13,000 securities; a file with fewer than this is not that directory.
LISTINGS_MIN_ROWS = 5_000

OCC_HEADER = ["quantity", "underlying", "symbol", "actype", "porc", "exchange", "actdate"]
# Account types, in OCC's codes. Any other code lands in `other`, so no volume is dropped.
ACCOUNT_TYPES = ("C", "F", "M")
# The stored row for an underlying: these sides, in this order.
COLUMNS = (
    "call_customer",
    "call_firm",
    "call_market_maker",
    "call_other",
    "put_customer",
    "put_firm",
    "put_market_maker",
    "put_other",
)

# The show's index segment, in its own running order. Always reported, ranked or not.
INDEXES = ("VIX", "SPY", "SPX", "IWM", "QQQ")
TOP_EQUITIES = 10
TOP_FUNDS = 5
BASELINE_SESSIONS = 20
# Relative volume needs at least this many baseline sessions; fewer is None, never a ratio of one.
MIN_BASELINE = 5
# A file more than this many sessions behind the pack's prior session is refused, not shown.
MAX_LAG_SESSIONS = 3
# An unclassified underlying ranking this high overall is named in the block.
UNCLASSIFIED_WATCH = 25


def store_dir() -> Path:
    return _files.store_dir() / "occ"


def session_path(session: str) -> Path:
    return store_dir() / f"{session}.json"


def listings_path() -> Path:
    return store_dir() / "nasdaqtraded.txt"


# --------------------------------------------------------------------------- parsers (pure)


def parse_occ(text: str) -> dict | None:
    """One OCC daily volume-query CSV as `{session, csv_rows, underlyings: {und: [sides...]}}`,
    the sides in `COLUMNS` order and summed over every option root and exchange. None when the text
    is not that file: a wrong header, rows from more than one session, or too few underlyings."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header or [h.strip().lower() for h in header[:7]] != OCC_HEADER:
        return None
    sessions: set[str] = set()
    out: dict[str, list[int]] = {}
    rows = 0
    for row in reader:
        if len(row) < 7:
            continue
        try:
            qty = int(row[0])
        except ValueError:
            continue
        und = row[1].strip()
        porc = row[4].strip().upper()
        if not und or porc not in ("C", "P"):
            continue
        sessions.add(row[6].strip())
        acct = row[3].strip().upper()
        idx = ACCOUNT_TYPES.index(acct) if acct in ACCOUNT_TYPES else 3
        sides = out.setdefault(und, [0] * len(COLUMNS))
        sides[idx + (0 if porc == "C" else 4)] += qty
        rows += 1
    if len(sessions) != 1 or len(out) < OCC_MIN_UNDERLYINGS:
        return None
    try:
        session = datetime.strptime(sessions.pop(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None
    return {"session": session, "csv_rows": rows, "underlyings": out}


def occ_symbol(symbol: str) -> str:
    """OCC writes a share class with no separator: BRK.B is `BRKB`."""
    return symbol.replace(".", "").replace("/", "").replace("$", "")


def parse_listings(text: str) -> dict[str, str]:
    """{OCC-spelled symbol: "etf" | "stock"} from Nasdaq Trader's `nasdaqtraded.txt`, test issues
    left out. Empty when the text is not that directory or holds too few rows to be it."""
    lines = text.lstrip("﻿").splitlines()
    if not lines:
        return {}
    header = lines[0].split("|")
    try:
        sym_i, etf_i, test_i = header.index("Symbol"), header.index("ETF"), header.index("Test Issue")
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        cols = line.split("|")
        if len(cols) != len(header) or cols[test_i] == "Y" or not cols[sym_i]:
            continue
        out[occ_symbol(cols[sym_i])] = "etf" if cols[etf_i] == "Y" else "stock"
    return out if len(out) >= LISTINGS_MIN_ROWS else {}


def listings_as_of(text: str) -> str | None:
    """The directory's own footer stamp (`File Creation Time: MMDDYYYYHH:MM`) as an ISO date."""
    for line in reversed(text.strip().splitlines()[-3:]):
        if line.startswith("File Creation Time:"):
            stamp = line.split(":", 1)[1].strip().split("|", 1)[0][:8]
            try:
                return datetime.strptime(stamp, "%m%d%Y").date().isoformat()
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- readers


def stored_sessions() -> list[str]:
    return sorted(p.stem for p in store_dir().glob("????-??-??.json"))


def read_session(session: str) -> dict | None:
    try:
        doc = json.loads(session_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("underlyings"), dict) else None


def contracts_by_session() -> dict[str, dict[str, int]]:
    """{session: {underlying: contracts}} for every stored session: the stock universe's option
    volume screen (scripts/build_stock_universe.py), which once fetched this same file itself."""
    out = {}
    for session in stored_sessions():
        doc = read_session(session)
        if doc is not None:
            out[session] = {und: _contracts(sides)[0] for und, sides in doc["underlyings"].items()}
    return out


def read_listings() -> tuple[dict[str, str], str | None]:
    try:
        text = listings_path().read_text(encoding="utf-8")
    except OSError:
        return {}, None
    return parse_listings(text), listings_as_of(text)


# --------------------------------------------------------------------------- ranking (pure)


def _contracts(sides: list[int]) -> tuple[int, int, int]:
    """(contracts, calls, puts): half the sides, since each contract traded is two."""
    calls, puts = sum(sides[:4]), sum(sides[4:])
    return (calls + puts) // 2, calls // 2, puts // 2


def _row(und: str, sides: list[int], rank: int | None, baseline: list[dict[str, list[int]]]) -> dict:
    contracts, calls, puts = _contracts(sides)
    total_sides = sum(sides)
    avg = None
    rel = None
    if len(baseline) >= MIN_BASELINE:
        # A session the name is absent from traded none of it: OCC lists every underlying with volume.
        avg = sum(_contracts(day.get(und) or [0] * len(COLUMNS))[0] for day in baseline) / len(baseline)
        rel = round(contracts / avg, 2) if avg > 0 else None
        avg = round(avg)
    return {
        "symbol": und,
        "rank": rank,
        "contracts": contracts,
        "calls": calls,
        "puts": puts,
        "put_call": round(puts / calls, 2) if calls else None,
        # A share of SIDES, not of contracts: a customer-to-customer trade is two customer sides.
        "customer_side_pct": round(100.0 * (sides[0] + sides[4]) / total_sides, 1) if total_sides else None,
        "avg_contracts": avg,
        "relative_volume": rel,
    }


def hot_options(
    day: dict[str, list[int]],
    baseline: list[dict[str, list[int]]],
    kinds: dict[str, str] | None,
) -> dict:
    """The ranking for one session: the declared index segment, the top single-name equities and
    the top funds outside it, each row with its overall rank by contracts, call/put split, the
    customer share of sides and volume against the baseline sessions' mean.

    `kinds` maps an underlying to "stock" or "etf"; None (no directory on file) leaves the equity
    and fund lists unranked with a reason rather than guessing what a name is."""
    order = sorted(day, key=lambda u: (-sum(day[u]), u))
    rank = {u: i for i, u in enumerate(order, 1)}
    all_sides = [sum(col) for col in zip(*day.values(), strict=True)] if day else [0] * len(COLUMNS)
    total, calls, puts = _contracts(all_sides)
    indexes = [
        _row(u, day[u], rank[u], baseline) if u in day else {"symbol": u, "rank": None, "contracts": 0}
        for u in INDEXES
    ]
    block: dict = {
        "total_contracts": total,
        "total_put_call": round(puts / calls, 2) if calls else None,
        "underlyings": len(day),
        "baseline_sessions": len(baseline),
        "indexes": indexes,
        "equities": None,
        "funds": None,
        "unclassified": [],
        "classification": None,
    }
    if not kinds:
        block["classification"] = "no_listings_file"
        return block
    rest = [u for u in order if u not in INDEXES]

    def top(kind: str, n: int) -> list[dict]:
        return [_row(u, day[u], rank[u], baseline) for u in rest if kinds.get(u) == kind][:n]

    block["equities"] = top("stock", TOP_EQUITIES)
    block["funds"] = top("etf", TOP_FUNDS)
    block["unclassified"] = [u for u in rest[:UNCLASSIFIED_WATCH] if u not in kinds]
    block["classification"] = "nasdaq_trader_directory"
    return block


def lag_sessions(occ_session: str, session: str) -> int:
    """Trading sessions between the ranked OCC session and the session before the pack's: 0 means
    the pack has the newest session it could have."""
    target = _calendar.previous_trading_day(date.fromisoformat(session))
    day, lag = date.fromisoformat(occ_session), 0
    while day < target:
        day = _calendar.next_trading_day(day)
        lag += 1
    return lag


def block_for(session: str) -> dict:
    """The pack's `hot_options` block: the newest stored OCC session strictly before `session`,
    ranked against the stored sessions before it."""
    days = [d for d in stored_sessions() if d < session]
    if not days:
        return {"session": None, "reason": "no_occ_file"}
    occ_session = days[-1]
    lag = lag_sessions(occ_session, session)
    if lag > MAX_LAG_SESSIONS:
        return {"session": occ_session, "lag_sessions": lag, "reason": "stale_occ_file"}
    doc = read_session(occ_session)
    if doc is None:
        return {"session": occ_session, "reason": "unreadable_occ_file"}
    baseline = []
    for d in days[-1 - BASELINE_SESSIONS : -1]:
        prior = read_session(d)
        if prior is not None:
            baseline.append(prior["underlyings"])
    kinds, kinds_as_of = read_listings()
    return {
        "session": occ_session,
        "source": "occ_volume_query",
        "basis": "prior",
        "lag_sessions": lag,
        "listings_as_of": kinds_as_of,
        **hot_options(doc["underlyings"], baseline, kinds or None),
        "record_only": True,
        "reason": None,
    }
