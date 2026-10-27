"""Our adjusted closes against Yahoo's: the corporate-action tripwire after the vendor's captures.

`check-vendor` compared our adjusted bars with the vendor's chart captures to the cent, and was the
only thing that caught an unmodelled split, a spin-off or a wrong dividend. The captures stopped on
2026-10-23. Yahoo's adjusted close states the same adjustment independently (proportional, as ours
and the vendor's are), and `scripts/fetch_yahoo_adjusted.py` keeps a year of it for every stored
name.

**What is compared is the shape, not the price.** The two series need not agree in level: Yahoo's
raw close is not always Dolt's, and a dividend that went ex after our newest bar re-scales every
bar of Yahoo's in the overlap by one constant. So the check reads the ratio ours / Yahoo's, session
by session, which must be FLAT between corporate actions. Where the two disagree about an event --
a split one side lacks, a dividend of a different amount, a spin-off one side adjusts for -- the
ratio steps on that event's ex-date by the event's size. A one-day difference in a raw close is a
spike, not a step, and the medians below ignore it.

**A step** at session i is the median log ratio over the `WINDOW` sessions from i against the median
over the `WINDOW` before it. Near either end of the overlap the windows shrink to what there is, and
a step there must clear the wider `EDGE_TOLERANCE`, so a split Dolt has not caught up with is seen
the morning after it lands, while a stray print at the end is not. A move beyond 3x restarts our
series (`adjust.series_break`) and leaves no step in the overlap, so it is read from the raw closes
(`series_restart`).

`size` is ours / Yahoo's before the step against after it: 2.0 means our bars before the date are
twice Yahoo's -- a 2-for-1 split we did not apply. Each step names a likely cause from its size and
both sides' events within `EVENT_DAYS` sessions, which is usually the whole diagnosis. **Yahoo
states a spin-off as a split** of the parent's price ratio (BDX 1272:1000 on 2026-02-10).

**`TOLERANCE` is read off the first full run** (2026-10-27, 933 names, a year each): the ratio is
flat to within 0.0005 on 90% of names and exactly flat on the median one, so 0.2% clears float
noise and still catches a missed quarterly dividend on a name yielding about 1%.

**Three causes never fail the check, by declared rule** (`NEVER_FAILS`), each listed in the result:
- `dividend: ours only` -- Yahoo's chart misses dividends (57 names on the first run; ACN's
  2026-01-13 1.63 is in neither its events nor its adjusted closes);
- `small split: ours only` -- a ratio within `SMALL_SPLIT` of 1 that we apply and Yahoo does not: a
  scrip dividend or a consolidation with a return of capital (PHG, NGG, FER, TRI, OPEN);
- excursions -- two steps that undo each other within 2 x `WINDOW` sessions with no event at either,
  a stretch of different raw closes.

**Every other known disagreement is declared, never silent**: `KNOWN_DIVIDEND_NAMES` excuses only a
name's `dividend: amounts differ` steps (a split on the same name still fails), `KNOWN_STEPS` one
dated step. Both carry their reason and are reported under `known`.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from datetime import date
from pathlib import Path

from . import adjust as _adjust
from . import paths, store, symbols

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    symbol TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    fetched_on TEXT NOT NULL,
    currency TEXT,
    first_date TEXT,
    last_date TEXT,
    dividends TEXT NOT NULL DEFAULT '[]',  -- [[ex_date, amount]]
    splits TEXT NOT NULL DEFAULT '[]'      -- [[ex_date, numerator, denominator]]
);
CREATE TABLE IF NOT EXISTS closes (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL NOT NULL,     -- split-adjusted, as Yahoo states it
    adjclose REAL NOT NULL,  -- split- and dividend-adjusted
    PRIMARY KEY (symbol, date)
);
"""

WINDOW = 5  # sessions either side of a candidate step
MIN_WINDOW = 3  # below this on either side, a step must clear EDGE_TOLERANCE
TOLERANCE = 0.002  # log ratio; see the module's notes
EDGE_TOLERANCE = 0.10
MIN_OVERLAP = 20  # sessions both hold, or the name is reported too short to read
SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20, 1.5)
SPLIT_FIT = 0.03
SMALL_SPLIT = 0.05
DIVIDEND_MAX = 0.10
EVENT_DAYS = 3
STALE_DAYS = 5  # calendar days Yahoo's newest session may trail the store's (a long weekend is 4)

YAHOO_GAP = "dividend: ours only"
SMALL_SPLIT_OURS = "small split: ours only"
EXCURSION = "excursion: raw closes differ for a stretch"
NEVER_FAILS = {YAHOO_GAP: "yahoo_gaps", SMALL_SPLIT_OURS: "small_splits", EXCURSION: "excursions"}

_CAD = (
    "dividends stated in CAD on the USD listing (Dolt and tastytrade alike); Yahoo converts, so ours "
    "over-adjusts by the exchange rate (known since 2026-10-17)"
)
_ADR = "an ADR's dividend: Dolt's and tastytrade's amount is not Yahoo's (currency, withholding or fee)"

# Names whose `dividend: amounts differ` steps are a known source difference. Only those steps.
KNOWN_DIVIDEND_NAMES: dict[str, str] = {
    **{
        s: _CAD
        for s in (
            "BMO", "BNS", "CM", "CNI", "CNQ", "CVE", "ENB", "FTS", "IMO", "MFC", "PBA", "RY", "SLF",
            "SU", "TD", "TRP",
        )
    },
    **{s: _ADR for s in ("CCEP", "DB", "PBR", "TTE")},
}  # fmt: skip

_SPIN = (
    "a spin-off Yahoo states as a split; our adjustment does not model spin-offs, so our series "
    "drops on the day (a split_history row would fix it)"
)

# Dated known steps, (symbol, step date) -> reason. Each was looked at on the first run, 2026-10-27.
KNOWN_STEPS: dict[tuple[str, str], str] = {
    ("BDX", "2026-02-10"): _SPIN,
    ("CMCSA", "2026-01-05"): _SPIN,
    ("CTVA", "2026-10-01"): _SPIN + "; raw 77.65 -> 12.57, so our series restarts",
    ("FDX", "2026-06-01"): _SPIN,
    ("HON", "2025-10-30"): _SPIN,
    ("HON", "2026-06-29"): _SPIN,
    ("SPGI", "2026-07-01"): _SPIN,
    ("BNY", "2026-05-21"): (
        "the ticker changed hands (BNY Mellon took BNY): our series rightly starts over, Yahoo's "
        "carries the new owner's history back"
    ),
    ("AZN", "2026-02-02"): (
        "OURS IS WRONG: the ADR ratio changed (raw 92.77 -> 188.41) and Dolt has no split; Yahoo "
        "applies 1:2. Fix: scripts/fetch_split_history.py AZN, or a hand row"
    ),
    ("AZN", "2026-02-19"): "the dividend on the AZN share basis after the 2026-02-02 ratio change",
    ("HON", "2025-11-14"): "OURS IS WRONG: tastytrade states 2.38, twice Dolt's 1.19, and reconcile kept it",
    ("HON", "2026-02-27"): "OURS IS WRONG: as 2025-11-14 (tastytrade's 2.38 against Dolt's 1.19)",
    ("HON", "2026-05-15"): "OURS IS WRONG: as 2025-11-14 (tastytrade's 2.38 against Dolt's 1.19)",
    ("CME", "2026-03-10"): (
        "OURS IS WRONG: tastytrade's 7.45 (the annual variable dividend with the regular 1.30) lost "
        "to the usual payout in reconcile"
    ),
    ("ITUB", "2025-12-11"): "OURS IS WRONG: the December extraordinary dividend is missing (0.069 vs 0.393)",
}


def connect_yahoo(path: Path | None = None) -> sqlite3.Connection:
    path = path or paths.yahoo_adjusted()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load(conn: sqlite3.Connection) -> dict[str, dict]:
    """{symbol: {fetched_on, currency, last_date, closes: {date: adjclose}, dividends, splits}}."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM series"):
        out[r["symbol"]] = {
            "fetched_on": r["fetched_on"],
            "currency": r["currency"],
            "last_date": r["last_date"],
            "dividends": json.loads(r["dividends"] or "[]"),
            "splits": json.loads(r["splits"] or "[]"),
            "closes": {},
        }
    for r in conn.execute("SELECT symbol, date, adjclose FROM closes"):
        if r["symbol"] in out:
            out[r["symbol"]]["closes"][r["date"]] = r["adjclose"]
    return out


def steps(dates: list[str], ours: list[float], theirs: list[float], tolerance: float) -> list[dict]:
    """Every step in the ratio ours / theirs, oldest first: {date, size, window}. `size` is the
    ratio before the date over the ratio from it (2.0: our earlier bars are twice theirs). Runs of
    neighbouring candidates are one step, placed where the jump is largest."""
    logs = [math.log(a / b) for a, b in zip(ours, theirs, strict=True)]
    n = len(logs)

    def place(run: list[dict]) -> dict:
        # A step clears the medians at several neighbouring sessions with the same size; the means'
        # difference peaks exactly on the session the ratio moved.
        return max(run, key=lambda c: (abs(c["mean"]), -c["i"]))

    found: list[dict] = []
    run: list[dict] = []
    for i in range(1, n):
        k = min(WINDOW, i, n - i)
        before, after = logs[i - k : i], logs[i : i + k]
        jump = statistics.median(before) - statistics.median(after)
        limit = tolerance if k >= MIN_WINDOW else max(tolerance, EDGE_TOLERANCE)
        if abs(jump) > limit:
            mean = statistics.fmean(before) - statistics.fmean(after)
            run.append({"i": i, "jump": jump, "mean": mean, "window": k})
            continue
        if run:
            found.append(place(run))
            run = []
    if run:
        found.append(place(run))
    return [{"date": dates[c["i"]], "size": math.exp(c["jump"]), "window": c["window"]} for c in found]


def split_sized(size: float) -> bool:
    return any(abs(size / ratio - 1) <= SPLIT_FIT for r in SPLIT_RATIOS for ratio in (r, 1 / r))


def cause(size: float, ours: dict, yahoo: dict) -> str:
    """The likely cause of one step, from its size and the events each side lists near it."""
    if ours["splits"] or yahoo["splits"]:
        if not ours["splits"]:
            return "split: Yahoo's only"
        if not yahoo["splits"]:
            small = all(abs(math.log(s[1] / s[2])) <= math.log(1 + SMALL_SPLIT) for s in ours["splits"])
            return (
                SMALL_SPLIT_OURS
                if small and abs(math.log(size)) <= math.log(1 + SMALL_SPLIT)
                else "split: ours only"
            )
        return "split: both list one, ratio or date differs"
    if split_sized(size):
        return "split-sized: neither lists a split"
    if abs(math.log(size)) <= math.log(1 + DIVIDEND_MAX):
        if ours["dividends"] and yahoo["dividends"]:
            return "dividend: amounts differ"
        if ours["dividends"]:
            return "dividend: ours only"
        if yahoo["dividends"]:
            return "dividend: Yahoo's only"
        return "dividend-sized: neither lists an event"
    return "unexplained"


def excursions(found: list[dict], dates: list[str], tolerance: float) -> set[int]:
    """Indexes of steps that undo each other within 2 x WINDOW sessions with no event at either: a
    stretch of sessions where one source's raw closes differ, not an adjustment."""
    out: set[int] = set()
    pos = {d: i for i, d in enumerate(dates)}
    for k in range(len(found) - 1):
        a, b = found[k], found[k + 1]
        quiet = not any(
            a[side][kind] or b[side][kind] for side in ("ours", "yahoo") for kind in ("splits", "dividends")
        )
        close = pos[b["date"]] - pos[a["date"]] <= 2 * WINDOW
        if quiet and close and abs(math.log(a["size"]) + math.log(b["size"])) <= max(tolerance, 1e-4):
            out |= {k, k + 1}
    return out


def _near(events: list, day: str, dates: list[str]) -> list:
    """The events whose ex-date is within EVENT_DAYS sessions of `day` (sessions from `dates`)."""
    if day not in dates:
        return []
    i = dates.index(day)
    lo, hi = dates[max(0, i - EVENT_DAYS)], dates[min(len(dates) - 1, i + EVENT_DAYS)]
    return [e for e in events if lo <= e[0] <= hi]


def series_restart(raw: list, common: list, theirs: dict[str, float]) -> list[dict]:
    """The step hidden by `adjust.series_break`: a one-day move beyond 3x starts our series over,
    so a missed 10-for-1 split leaves no step in the overlap -- only a series that begins on the
    split. Where our raw bars and Yahoo's both reach back past our first adjusted session, the
    ratio is read across that day from the raw closes (one session, so no event of ours lies
    between them): (raw before / raw on) x (Yahoo on / Yahoo before). A ticker that changed hands
    moved on Yahoo's series too, and reads as 1."""
    if not common:
        return []
    first = common[0].date
    prior = [b for b in raw if b.date < first and b.date in theirs and b.close > 0]
    on = next((b for b in raw if b.date == first), None)
    if not prior or on is None or on.close <= 0:
        return []
    before = prior[-1]
    size = (before.close / on.close) * (theirs[first] / theirs[before.date])
    if abs(math.log(size)) <= EDGE_TOLERANCE:
        return []
    return [{"date": first, "size": size, "window": 1, "series_restart": True}]


def compare_symbol(conn, symbol: str, yahoo: dict, tolerance: float) -> dict:
    """One name: the overlap read, its steps with their likely cause and both sides' nearby events."""
    as_of = yahoo.get("last_date")
    bars = store.adjusted_bars(conn, symbol, as_of)
    theirs = yahoo["closes"]
    common = [b for b in bars if b.date in theirs and b.close > 0]
    found = series_restart(store.raw_bars(conn, symbol), common, theirs)
    if len(common) < MIN_OVERLAP and not found:
        return {"overlap": len(common), "status": "too_short"}
    dates = [b.date for b in common]
    if len(common) >= 2:
        found += steps(dates, [b.close for b in common], [theirs[d] for d in dates], tolerance)
    if not found:
        return {"overlap": len(common), "status": "agree", "from": dates[0], "to": dates[-1]}
    # What adjusted_bars applies: Dolt's and the public history's splits, deduplicated, and the
    # reconciled dividends.
    applied = _adjust.dedupe_splits(store.raw_bars(conn, symbol), store.splits(conn, symbol))
    our_splits = [[s.ex_date, s.to_factor, s.for_factor] for s in applied]
    our_divs = [[d.ex_date, round(d.amount, 6)] for d in store.dividends(conn, symbol, applied)[0]]
    out = []
    for s in found:
        ours = {"splits": _near(our_splits, s["date"], dates), "dividends": _near(our_divs, s["date"], dates)}
        theirs_near = {
            "splits": _near(yahoo.get("splits") or [], s["date"], dates),
            "dividends": _near(yahoo.get("dividends") or [], s["date"], dates),
        }
        out.append(
            {
                "date": s["date"],
                "size": round(s["size"], 5),
                "cause": cause(s["size"], ours, theirs_near),
                "window": s["window"],
                **({"series_restart": True} if s.get("series_restart") else {}),
                "ours": ours,
                "yahoo": theirs_near,
            }
        )
    for k in excursions(out, dates, tolerance):
        out[k]["cause"] = EXCURSION
    return {"overlap": len(common), "status": "steps", "from": dates[0], "to": dates[-1], "steps": out}


def default_symbols() -> list[str]:
    """The names the store lands, less the cash indexes (charted raw, never adjusted)."""
    return [s for s in symbols.all_symbols() if s not in symbols.INDEXES]


def check(tolerance: float | None = None, names: list[str] | None = None) -> dict:
    """Every name the store lands, read against Yahoo's file. A name with an undeclared step
    disagrees; a declared one is listed under `known`. `stale` is set when Yahoo's newest session is
    more than STALE_DAYS behind the store's, so a fetch that stopped working cannot pass as a clean
    check. The CLI exits non-zero on either."""
    tol = TOLERANCE if tolerance is None else tolerance
    path = paths.yahoo_adjusted()
    yahoo: dict[str, dict] = {}
    if path.exists():
        yconn = connect_yahoo(path)
        try:
            yahoo = load(yconn)
        finally:
            yconn.close()
    conn = store.connect()
    latest = store.latest_dates(conn)
    wanted = [s for s in (names or default_symbols()) if s not in symbols.INDEXES]
    read: list[str] = []
    disagree: dict[str, dict] = {}
    known: dict[str, list] = {}
    excused: dict[str, dict[str, list]] = {key: {} for key in NEVER_FAILS.values()}
    too_short, not_held, not_fetched = [], [], []
    for sym in wanted:
        if sym not in latest:
            not_held.append(sym)
            continue
        if sym not in yahoo:
            not_fetched.append(sym)
            continue
        result = compare_symbol(conn, sym, yahoo[sym], tol)
        if result["status"] == "too_short":
            too_short.append(sym)
            continue
        read.append(sym)
        fresh = []
        for s in result.get("steps", []):
            reason = KNOWN_STEPS.get((sym, s["date"]))
            if not reason and s["cause"] == "dividend: amounts differ":
                reason = KNOWN_DIVIDEND_NAMES.get(sym)
            if reason:
                known.setdefault(sym, []).append({**s, "known": reason})
            elif s["cause"] in NEVER_FAILS:
                excused[NEVER_FAILS[s["cause"]]].setdefault(sym, []).append(s)
            else:
                fresh.append(s)
        if fresh:
            disagree[sym] = {**{k: v for k, v in result.items() if k != "steps"}, "steps": fresh}
    conn.close()
    yahoo_through = max((y["last_date"] or "" for y in yahoo.values()), default="") or None
    store_through = max(latest.values(), default=None)
    stale = bool(
        store_through
        and (
            yahoo_through is None
            or (date.fromisoformat(store_through) - date.fromisoformat(yahoo_through)).days > STALE_DAYS
        )
    )

    def worst(item) -> float:
        return -max(abs(math.log(s["size"])) for s in item[1]["steps"])

    return {
        "tolerance": tol,
        "yahoo_through": yahoo_through,
        "store_through": store_through,
        "stale": stale,
        "symbols": len(read),
        "agree": len(read) - len(disagree),
        "disagree": len(disagree),
        "known": known,
        **excused,
        "too_short": too_short,
        "not_held": not_held,
        "not_fetched": not_fetched,
        "by_symbol": dict(sorted(disagree.items(), key=worst)),
    }
