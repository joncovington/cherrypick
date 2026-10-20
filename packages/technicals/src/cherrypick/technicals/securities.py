"""The security list: every US-listed symbol's type, whether it has listed options, and a liquidity
rank -- built from public files, with the vendor's list preferred while the vendor is on.

**Why.** Four readers want one row per symbol saying what it is and how liquid its options are: the
IV-rank fetch's calendar names, the earnings calendar's who and liquidity filter, the screener greeks
plan, and the console's fund labels. Until 2026-10-17 the only such list was the vendor's, saved
nightly by the collector; the subscription ends 2026-10-24 and may be taken up again, so the vendor
is a configured source (`cherrypick.core.vendor`), and this list is the default.

**From what, all local** -- no broker, no network; the morning pack's fetcher lands both files:

- **type**: Nasdaq Trader's directory (`market-files/occ/nasdaqtraded.txt`), its ETF flag and the
  security's name, labelled in the vendor's words so a reader reads either list the same way.
  97.8% of the 11,817 symbols both lists hold agree (2026-10-16). Most misses are depositary
  receipts the directory names "Common Stock" (HMC, NVS, DEO) -- the readers keep stocks and
  receipts alike -- and commodity or crypto trusts it flags as ETFs, still funds.
- **hasOptions**: any option volume at OCC (`market-files/occ/<session>.json`) in the last
  `OPTION_SESSIONS` sessions. The vendor's 389 extra names traded none in that time, and every one
  of them ranks 3, so no reader's set changes.
- **liquidityRank**: OURS, not the vendor's. 2 needs `TOP_TWO_CONTRACTS` contracts a day (median
  over the sessions) and `TOP_TWO_DOLLARS` of stock a day (50-session median close x volume from
  the local Dolt, the options-tradable test's bar); 1 needs `TOP_CONTRACTS` and `TOP_DOLLARS`.
  Fitted on 5,942 optionable names against the vendor's 2026-10-16 list: its rank-1-or-2 set is
  matched at 78% precision and 78% recall, its rank 1 at about 0.77 F1. The vendor's rank is not
  volume alone (its rank-1 names run from 950 to 640,000 contracts a day), and no rule from these
  inputs gets closer, so this is a declared rule of our own, re-scored while both lists exist.

**Which list a reader gets** is decided once (`choose`) and written beside the lists as
`current.json`, for the console: the vendor's newest list when the collector is on and that list is
at most `VENDOR_MAX_AGE_DAYS` old, else the public one. A stale vendor list -- a failed sign-in --
falls back and says why, where before every reader quietly kept the last saved file.

    python -m cherrypick.technicals securities [--session YYYY-MM-DD]
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio as _jsonio
from cherrypick.core import vendor as _vendor

from . import paths

OPTION_SESSIONS = 20
DOLLAR_SESSIONS = 50
MIN_DOLLAR_SESSIONS = 20
TOP_CONTRACTS = 2_000
TOP_DOLLARS = 300e6
TOP_TWO_CONTRACTS = 200
TOP_TWO_DOLLARS = 100e6
# The vendor's list lands each trading evening; a weekend makes it three days old on Monday.
VENDOR_MAX_AGE_DAYS = 4
# The directory lists ~13,000 securities; fewer than this is not that file.
MIN_DIRECTORY_ROWS = 5_000

STOCK = "Stock"
RECEIPT = "Depository Receipt"
FUND = "Exchange Traded Fund"
PREFERRED = "Preferred Shares"
STRUCTURED = "Structured Product"
UNITS = "Units"
# Spelled by the vendor's list, never derived here.
COMMODITY = "Exchange Traded Commodity"
OTHER_FUND = "Fund"
INDEX = "Index"
# Types that never report earnings, so an unknown report date says nothing about them. Every other
# type is a company until shown otherwise ("unknown" is never "clear"). The console's screeners
# reader keeps the same set (`FUND_TYPES` in server/src/readers/screeners.ts) less STRUCTURED.
NO_EARNINGS = frozenset({FUND, COMMODITY, OTHER_FUND, INDEX, STRUCTURED})

_PREFERRED = re.compile(r"\b(preferred|pfd)\b", re.I)
_NOTE = re.compile(r"\b(exchange[- ]traded notes?|ETNs?|notes? due|debentures?)\b", re.I)
_UNITS = re.compile(r"\b(units?|warrants?|rights?)\b", re.I)
_RECEIPT = re.compile(r"\b(depositary|depository|ADSs?|ADRs?)\b", re.I)


# ------------------------------------------------------------------------------------------------
# Pure: the rule.


def kind(name: str, etf: bool) -> str:
    """The vendor's label for a security from its directory name and ETF flag. Order matters: a
    preferred depositary share is preferred, an exchange-traded note flagged ETF is structured."""
    if _NOTE.search(name):
        return STRUCTURED
    if etf:
        return FUND
    if _PREFERRED.search(name):
        return PREFERRED
    if _UNITS.search(name):
        return UNITS
    if _RECEIPT.search(name):
        return RECEIPT
    return STOCK


def rank(contracts: float | None, dollars: float | None) -> int | None:
    """1, 2 or 3 (see the module note); None when the stock's dollar volume is unknown."""
    if dollars is None:
        return None
    c = contracts or 0.0
    if c >= TOP_CONTRACTS and dollars >= TOP_DOLLARS:
        return 1
    if c >= TOP_TWO_CONTRACTS and dollars >= TOP_TWO_DOLLARS:
        return 2
    return 3


def vendor_spelling(symbol: str) -> str:
    """The directory writes BRK.B and ABR$D; the vendor and the broker write BRK/B and ABR/PR/D."""
    return symbol.replace("$", "/PR/").replace(".", "/")


def occ_spelling(symbol: str) -> str:
    """OCC writes a share class with no separator: BRK.B is BRKB (overview's `occ.occ_symbol`)."""
    return symbol.replace(".", "").replace("/", "").replace("$", "")


def parse_directory(text: str) -> list[dict]:
    """[{symbol, name, etf}] from `nasdaqtraded.txt`, test issues left out; empty when the text is
    not that directory."""
    lines = text.lstrip("﻿").splitlines()
    if not lines:
        return []
    header = lines[0].split("|")
    try:
        sym_i, name_i, etf_i, test_i = (
            header.index(h) for h in ("Symbol", "Security Name", "ETF", "Test Issue")
        )
    except ValueError:
        return []
    out = []
    for line in lines[1:]:
        cols = line.split("|")
        if len(cols) != len(header) or cols[test_i] == "Y" or not cols[sym_i]:
            continue
        out.append({"symbol": cols[sym_i], "name": cols[name_i].strip(), "etf": cols[etf_i] == "Y"})
    return out if len(out) >= MIN_DIRECTORY_ROWS else []


def directory_as_of(text: str) -> str | None:
    """The directory's footer stamp (`File Creation Time: MMDDYYYYHH:MM`) as an ISO date."""
    for line in reversed(text.strip().splitlines()[-3:]):
        if line.startswith("File Creation Time:"):
            try:
                return datetime.strptime(line.split(":", 1)[1].strip()[:8], "%m%d%Y").date().isoformat()
            except ValueError:
                return None
    return None


def public_rows(
    directory: list[dict], contracts: dict[str, list[int]], dollars: dict[str, float]
) -> list[dict]:
    """One row per directory symbol. `contracts` is each OCC underlying's contracts over the
    sessions read (a session it is absent from traded none: OCC lists every underlying with
    volume, so those count as zeros); `dollars` the 50-session median close x volume by Dolt
    symbol (the directory's own spelling)."""
    sessions = max((len(v) for v in contracts.values()), default=0)
    rows = []
    for d in directory:
        vols = contracts.get(occ_spelling(d["symbol"])) or []
        vols = vols + [0] * (sessions - len(vols))
        median = statistics.median(vols) if vols else None
        dv = dollars.get(d["symbol"])
        rows.append(
            {
                "symbol": vendor_spelling(d["symbol"]),
                "type": kind(d["name"], d["etf"]),
                "hasOptions": any(v > 0 for v in vols),
                "liquidityRank": rank(median, dv),
                "option_contracts": median,
                "dollar_volume": None if dv is None else round(dv),
            }
        )
    return rows


def choose(day: str, vendor_on: bool, vendor_days: list[str], public_days: list[str]) -> dict:
    """Which list a reader gets for `day`: {source, day, reason}. Only lists dated on or before
    `day` count."""
    vendor = max((d for d in vendor_days if d <= day), default=None)
    public = max((d for d in public_days if d <= day), default=None)
    if vendor_on and vendor is not None:
        age = (date.fromisoformat(day) - date.fromisoformat(vendor)).days
        if age <= VENDOR_MAX_AGE_DAYS:
            return {"source": "vendor", "day": vendor, "reason": "the vendor is on and its list is current"}
        why = f"the vendor is on but its newest list ({vendor}) is {age} days old"
    elif vendor_on:
        why = "the vendor is on but no list of its is saved"
    else:
        why = "the vendor is off (market_report.collector)"
    if public is None:
        return {"source": None, "day": None, "reason": why + "; no public list either"}
    return {"source": "public", "day": public, "reason": why}


def compare(public: list[dict], vendor: list[dict]) -> dict:
    """How the public list reads against the vendor's, on the symbols both hold: type agreement,
    options agreement, and the rank-1-or-2 set's precision and recall."""
    v = {r["symbol"]: r for r in vendor if r.get("symbol")}
    both = [(p, v[p["symbol"]]) for p in public if p["symbol"] in v]
    if not both:
        return {"symbols": 0}
    top = [(p.get("liquidityRank") in (1, 2), x.get("liquidityRank") in (1, 2)) for p, x in both]
    hit = sum(1 for a, b in top if a and b)
    ours, theirs = sum(1 for a, _ in top if a), sum(1 for _, b in top if b)
    return {
        "symbols": len(both),
        "type_agreement": round(sum(p["type"] == x.get("type") for p, x in both) / len(both), 4),
        "options_agreement": round(
            sum(p["hasOptions"] == bool(x.get("hasOptions")) for p, x in both) / len(both), 4
        ),
        "top_two": {
            "ours": ours,
            "vendor": theirs,
            "both": hit,
            "precision": round(hit / ours, 3) if ours else None,
            "recall": round(hit / theirs, 3) if theirs else None,
        },
    }


# ------------------------------------------------------------------------------------------------
# Readers.


def _days(root: Path) -> list[str]:
    return sorted(p.stem for p in root.glob("????-??-??.json")) if root.is_dir() else []


def _rows(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return []


def latest(day: str | None = None, vendor_on: bool | None = None) -> tuple[list[dict], dict]:
    """(rows, the choice) for `day` (default today): what every Python reader reads."""
    day = day or date.today().isoformat()
    on = _vendor.suite_collector_on() if vendor_on is None else vendor_on
    pick = choose(day, on, _days(paths.vendor_securities_dir()), _days(paths.securities_dir()))
    if pick["source"] is None:
        return [], pick
    root = paths.vendor_securities_dir() if pick["source"] == "vendor" else paths.securities_dir()
    return _rows(root / f"{pick['day']}.json"), pick


# ------------------------------------------------------------------------------------------------
# The nightly build.


def _occ_contracts(day: str) -> tuple[dict[str, list[int]], list[str]]:
    """{OCC underlying: contracts per session} over the last OPTION_SESSIONS stored sessions on or
    before `day`. The file is overview's (`occ.py`): sides summed, halved because OCC states both
    sides of every trade."""
    root = paths.market_files_dir() / "occ"
    read: list[tuple[str, dict]] = []
    for s in [d for d in _days(root) if d <= day][-OPTION_SESSIONS:]:
        try:
            unds = json.loads((root / f"{s}.json").read_text(encoding="utf-8")).get("underlyings")
        except (OSError, ValueError):
            continue
        if isinstance(unds, dict):
            read.append((s, unds))
    names = {u for _, unds in read for u in unds}
    out = {u: [sum(unds[u]) // 2 if u in unds else 0 for _, unds in read] for u in names}
    return out, [s for s, _ in read]


def _dolt_dollars(cfg: dict, day: str) -> tuple[dict[str, float], str | None]:
    """{Dolt symbol: 50-session median close x volume} through `day`, from one date-window read (the
    table's key leads with date, so a symbol filter would walk it)."""
    from . import land

    cfg = {**land.DEFAULTS, **cfg}
    db = land._connect(cfg, cfg["stocks_db"])
    try:
        cur = db.cursor()
        cur.execute(
            "SELECT act_symbol, date, close, volume FROM ohlcv "
            "WHERE date <= %s AND date > DATE_SUB(%s, INTERVAL 100 DAY) ORDER BY date",
            (day, day),
        )
        by: dict[str, list[float]] = {}
        through = None
        for sym, d, close, volume in cur.fetchall():
            by.setdefault(sym, []).append(float(close) * float(volume or 0))
            through = str(d)[:10]
    finally:
        db.close()
    dollars = {
        s: statistics.median(v[-DOLLAR_SESSIONS:]) for s, v in by.items() if len(v) >= MIN_DOLLAR_SESSIONS
    }
    return dollars, through


def build(dolt_cfg: dict | None = None, day: str | None = None) -> dict[str, Any]:
    """Build `day`'s public list, write it and the pointer, and score it against the vendor's list
    of the same day or the one before it, when one is saved."""
    day = day or date.today().isoformat()
    text_path = paths.market_files_dir() / "occ" / "nasdaqtraded.txt"
    try:
        text = text_path.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "reason": f"no symbol directory at {text_path}"}
    directory = parse_directory(text)
    if not directory:
        return {"ok": False, "reason": "the symbol directory is not readable"}
    contracts, sessions = _occ_contracts(day)
    if not sessions:
        return {"ok": False, "reason": "no OCC volume sessions"}
    dollars, through = _dolt_dollars(dolt_cfg or {}, day)
    rows = public_rows(directory, contracts, dollars)
    doc = {
        "session": day,
        "built_at": datetime.now(UTC).isoformat(),
        "source": "public",
        "inputs": {
            "directory_as_of": directory_as_of(text),
            "occ_sessions": [sessions[0], sessions[-1], len(sessions)],
            "dolt_through": through,
        },
        "rule": {
            "option_sessions": OPTION_SESSIONS,
            "rank_1": {"contracts": TOP_CONTRACTS, "dollar_volume": TOP_DOLLARS},
            "rank_2": {"contracts": TOP_TWO_CONTRACTS, "dollar_volume": TOP_TWO_DOLLARS},
        },
        "rows": rows,
    }
    _jsonio.write_json_atomic(paths.securities_dir() / f"{day}.json", doc, indent=None, default=None)
    on = _vendor.suite_collector_on()
    pick = choose(day, on, _days(paths.vendor_securities_dir()), _days(paths.securities_dir()))
    _jsonio.write_json_atomic(
        paths.securities_dir() / "current.json",
        {**pick, "for": day, "vendor_on": on, "written_at": datetime.now(UTC).isoformat()},
        indent=1,
        default=None,
    )
    vendor_day = max((d for d in _days(paths.vendor_securities_dir()) if d <= day), default=None)
    score = compare(rows, _rows(paths.vendor_securities_dir() / f"{vendor_day}.json")) if vendor_day else None
    return {
        "ok": True,
        "rows": len(rows),
        "optionable": sum(1 for r in rows if r["hasOptions"]),
        "ranks": {k: sum(1 for r in rows if r["liquidityRank"] == k) for k in (1, 2, 3)},
        "current": pick,
        "against_vendor": {"day": vendor_day, **score} if score else None,
    }
