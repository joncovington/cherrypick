"""The signal board: each name's count of methods agreeing on one side, from our own engines.

**What it reproduces.** The vendor's daily report lists names under Bullish and Bearish with one tag
per method that agrees -- a stage, the sector's rotation, a scan signal, fundamentals, an analyst
revision, institutional flow. Read off the 15 saved boards (2026-09-23 to 10-14, 130 rows, ~430
tags), the rule is plain: **a row's score is its count of tags, every tag is on the row's side** (not
one of the 430 points the other way), and every listed row has at least three.

**What it holds.** Five methods, each from an engine this package already has, read on one session:

| method | engine | tag | found on the boards (2026-10-17) |
|---|---|---|---|
| stage | `stage.py` | early breakout ... confirmed underperformer | 69 of 73 |
| revision | `revisions.py` | an upgrade, downgrade, sided initiation, target move of 5%+ | 33 of 38 |
| rotation | `rotation.py` on the name's sector fund | into: leading; out: lagging or weakening | 89 of 96 |
| fundamentals | `fundamentals.market_percentiles` | compelling: top fifth; weak: bottom | 43/55; 16/40 |
| scan | `signals.py` trend-following rules, the session or the one before | trend following | 9 of 16 |

Rotation is the daily quadrant (refit 2026-10-17; the banded rule before it found 61 of 96).
Fundamentals is our score (four measures ranked within the sector, margin twice) placed across the
whole market on the newest weekly estimate snapshot: its compelling side reproduces well (2 extra),
its weak side poorly, so it is marked approximate. The fit on the boards is flat from the top
quarter to the top fifth; a fifth is the tail. The scan is the two trend-following rules only, over
two sessions (the saved scan lists put every tagged row's signal there; 5 of 16 on the day alone,
10 of 16 at 14 extras with the CCI rules); still approximate.

Not counted at all: fundamentals vs price and the three flow tags (paid data), so our counts run
lower than the vendor's, and no three-tag floor is applied. Fundamentals vs price was tried
2026-10-17 as our fundamentals percentile against our 1-10 rank, both as quadrants and as their
difference: about 23 of 50 tags at 7-10 extras, 8 of 22 on the last five boards -- under half, so
not counted (it rests on the fundamentals score, whose weak side is itself poor).

**A name's side** is the side with more tags; a tie has no side and is kept in the file unlisted.
Only that side's tags are listed, as the vendor lists them; the other side's count is kept.

Record-only, like every market-report reading: it feeds no gate, no sizing and no order. Written to
`data/technicals/board/<session>.json` by `python -m cherrypick.technicals board`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio as _jsonio

from . import fundamentals, liquidity, paths, revisions, rotation, signals, stage, store, symbols

BOARD_VERSION = 1

# The sector funds rotation is read on, by the sector names the maps use.
SECTOR_FUND = {
    "Technology": "XLK", "Financials": "XLF", "Healthcare": "XLV", "Industrials": "XLI",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Utilities": "XLU",
    "Basic Materials": "XLB", "Communication Services": "XLC", "Real Estate": "XLRE",
}  # fmt: skip

# The vendor's "into" is its leading quadrant (4 of 4 tagged; 1 of 14 improving rows tagged), its
# "out" lagging (38 of 38) or weakening (8 of 12). Read here on our own states.
ROTATION_SIDE = {"leading": 1, "lagging": -1, "weakening": -1}

# The trend-following rules only, on the session or the one before (`SCAN_SESSIONS`): read off the
# saved scan lists against the boards (2026-10-17), every tagged row's entry was a trend-following
# signal dated the session or the one before, and on-side CCI dips (DOCS, MU, PLTR) went untagged,
# as did NVDA's trend signal two sessions back. The counter-trend rules never count ("don't chase").
SCAN_SIDE = {
    "BullishTrendFollowing": 1,
    "BearishTrendFollowing": -1,
}
SCAN_SESSIONS = 2

STAGE_LABEL = {
    (1, "early"): "Early breakout",
    (1, "building"): "Building outperformance",
    (1, "confirmed"): "Confirmed outperformer",
    (-1, "early"): "Early breakdown",
    (-1, "building"): "Building underperformance",
    (-1, "confirmed"): "Confirmed underperformer",
}

# The fundamentals tails, on the market percentile (fit 2026-10-17 on the saved boards' 95 tags).
COMPELLING_AT = 0.80
WEAK_AT = 0.20

# How each method stands against the saved boards, for the page to say. Re-score with `score-board`.
METHODS = {
    "stage": {"label": "RS stage", "approximate": False},
    "rotation": {"label": "Sector rotation", "approximate": False},
    "fundamentals": {"label": "Fundamentals", "approximate": True},
    "scan": {"label": "Trade scan", "approximate": True},
    "revision": {"label": "Analyst revision", "approximate": False},
}
NOT_COUNTED = (
    "fundamentals vs price",
    "institutional call buying",
    "put selling",
    "put buying",
)


@dataclass(frozen=True)
class Tag:
    method: str
    side: int  # +1 bullish, -1 bearish
    label: str
    detail: str = ""


def revision_detail(t: revisions.Tag) -> str:
    """The event that decided the tag, in a few words: "Barclays downgrade", "Citigroup target +13%"."""
    lead = min(t.events, key=revisions._rank)
    if lead.action in revisions.RATING_ACTIONS:
        word = {"up": "upgrade", "down": "downgrade", "init": "initiation"}[lead.action]
        return f"{lead.firm} {word}"
    ch = revisions.pt_change(lead)
    return f"{lead.firm} target {ch * 100:+.0f}%" if ch is not None else lead.firm


def tags_on(
    day: str,
    bars: dict[str, list],
    bench: dict[str, float],
    fund_closes: dict[str, dict[str, float]],
    sector_of: dict[str, str],
    rev_tags: dict[str, revisions.Tag],
    fund_pct: dict[str, float] | None = None,
) -> dict[str, list[Tag]]:
    """Every name's tags on `day`, both sides. `bars` are each name's adjusted bars (any extent past
    `day` is cut here); a name without a bar on `day` gets no stage or scan. `fund_pct` is each
    name's fundamentals place across the market (`fundamentals.market_percentiles`)."""
    fund_pct = fund_pct or {}
    closes = {s: {b.date: b.close for b in bs if b.date <= day} for s, bs in bars.items()}
    stages = stage.stages_on(day, closes, bench)
    states = rotation.states_on(day, fund_closes, symbols.ASSET_ETFS)
    out: dict[str, list[Tag]] = {}
    for sym, bs in bars.items():
        tags: list[Tag] = []
        st = stages.get(sym)
        if st is not None:
            side = 1 if st.side == "leader" else -1
            tags.append(Tag("stage", side, STAGE_LABEL[(side, st.stage)]))
        fund = SECTOR_FUND.get(sector_of.get(sym, ""))
        rside = ROTATION_SIDE.get(states.get(fund)) if fund else None
        if rside:
            label = "Money rotating into the sector" if rside > 0 else "Money rotating out of the sector"
            tags.append(Tag("rotation", rside, label, f"{fund} {states.get(fund)}"))
        p = fund_pct.get(sym)
        if p is not None and (p >= COMPELLING_AT or p <= WEAK_AT):
            good = p >= COMPELLING_AT
            label = "Compelling fundamentals" if good else "Weak fundamentals"
            detail = f"score at the {_ordinal(round(100 * p))} percentile"
            tags.append(Tag("fundamentals", 1 if good else -1, label, detail))
        upto = [b for b in bs if b.date <= day]
        if upto and upto[-1].date == day:
            hits = []  # (session, rule), newest last
            for k in range(SCAN_SESSIONS, 0, -1):
                part = upto[: len(upto) - k + 1]
                r = signals.readings([b.high for b in part], [b.low for b in part], [b.close for b in part])
                hits += [
                    (part[-1].date, m)
                    for m in (signals.matches(r) if r is not None else [])
                    if m in SCAN_SIDE
                ]
            sides = {SCAN_SIDE[m] for _, m in hits}
            if len(sides) == 1:
                side = sides.pop()
                when, rule = hits[-1]
                label = "Bullish trade scan" if side > 0 else "Bearish trade scan"
                tags.append(Tag("scan", side, label, f"{rule} {when}"))
        rt = rev_tags.get(sym)
        if rt is not None and rt.direction:
            label = "Analyst bullish revision" if rt.direction > 0 else "Analyst bearish revision"
            tags.append(Tag("revision", rt.direction, label, revision_detail(rt)))
        if tags:
            out[sym] = tags
    return out


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fundamentals_on(
    day: str, sector_of: dict[str, str], cache: dict | None = None
) -> tuple[str | None, dict]:
    """(snapshot, {symbol: market percentile}) for `day`, from the history store's newest estimate
    snapshot on or before it; (None, {}) when the store holds none. `cache` keeps one per snapshot."""
    conn = fundamentals.connect()
    try:
        snap = fundamentals.snapshot_on(conn, day)
        if snap is None:
            return None, {}
        if cache is not None and snap in cache:
            return snap, cache[snap]
        pct = fundamentals.market_percentiles(conn, snap, sector_of)
    finally:
        conn.close()
    if cache is not None:
        cache[snap] = pct
    return snap, pct


def row(sym: str, tags: list[Tag], sector: str | None) -> dict[str, Any]:
    bull = sum(t.side > 0 for t in tags)
    bear = sum(t.side < 0 for t in tags)
    side = 1 if bull > bear else -1 if bear > bull else 0
    return {
        "symbol": sym,
        "sector": sector,
        "side": {1: "bullish", -1: "bearish", 0: None}[side],
        "count": max(bull, bear),
        "bullish": bull,
        "bearish": bear,
        # The side's tags only, as the board lists them; a tie lists none.
        "tags": [
            {"method": t.method, "label": t.label, "detail": t.detail}
            for t in tags
            if side and t.side == side
        ],
    }


def build(session: str | None = None, conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or store.connect()
    try:
        bench = {b.date: b.close for b in store.adjusted_bars(conn, stage.DEFAULT_RULE.benchmark)}
        if not bench:
            return {"ok": False, "reason": "no benchmark bars in the store"}
        day = session or max(bench)
        # Every stock the store lands that a listed view may name (the charts' set).
        names = liquidity.listed(store.stocks(conn, symbols.all_symbols()))
        bars = {s: bs for s in names if (bs := store.adjusted_bars(conn, s))}
        funds = sorted(
            {*SECTOR_FUND.values(), rotation.DEFAULT_RULE.benchmark, rotation.DEFAULT_RULE.asset_benchmark}
        )
        fund_closes = {
            f: {b.date: b.close for b in store.adjusted_bars(conn, f) if b.date <= day} for f in funds
        }
    finally:
        if own:
            conn.close()
    sector_of = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    rev = revisions.tags(revisions.load(), day)
    snap, pct = fundamentals_on(day, sector_of)
    by_name = tags_on(day, bars, bench, fund_closes, sector_of, rev, pct)
    rows = [row(s, t, sector_of.get(s)) for s, t in sorted(by_name.items())]
    rows.sort(key=lambda r: (-r["count"], r["symbol"]))
    return {
        "ok": True,
        "board_version": BOARD_VERSION,
        "session": day,
        "generated_at": datetime.now(UTC).isoformat(),
        "names": len(bars),
        "methods": METHODS,
        "not_counted": list(NOT_COUNTED),
        "revisions_through": _revisions_through(),
        "fundamentals_snapshot": snap,
        "rows": rows,
        "record_only": True,
    }


def _revisions_through() -> str | None:
    """When the revisions table was last fetched, so a stale one shows as stale."""
    import sqlite3

    path = paths.analyst_revisions()
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        (at,) = con.execute("SELECT MAX(fetched_at) FROM fetches").fetchone()
    except sqlite3.Error:
        at = None
    finally:
        con.close()
    return at


def out_dir() -> Path:
    return paths.data_dir() / "board"


def write(doc: dict) -> str:
    return str(_jsonio.write_json_atomic(out_dir() / f"{doc['session']}.json", doc, indent=1, default=None))


# ------------------------------------------------------------------------------------------- scoring


def score_boards(ours: dict[str, dict[str, list[Tag]]], boards: dict) -> dict[str, Any]:
    """Each method against the saved boards, counting only our tags on the row's own side (the board
    lists no other): `found` of the vendor's tags, and `extra` -- ours on a board row the vendor did
    not tag for that method. `ours` is {session: tags_on(...)}. Rows only: names the vendor left off
    its board are not scored, since its full list's cut is unknown."""
    want = {"bullish": 1, "bearish": -1}
    out: dict[str, Any] = {m: {"found": 0, "tagged": 0, "extra": 0} for m in METHODS}
    rows = 0
    same_side = 0
    for day, board in sorted(boards.items()):
        mine = ours.get(day) or {}
        for sym, br in board.items():
            rows += 1
            side = want[br.side]
            vendor = vendor_methods(br.tags)
            tags = mine.get(sym, [])
            ours_on_side = {t.method for t in tags if t.side == side}
            for m in METHODS:
                if m in vendor:
                    out[m]["tagged"] += 1
                    out[m]["found"] += m in ours_on_side
                elif m in ours_on_side:
                    out[m]["extra"] += 1
            bull = sum(t.side > 0 for t in tags)
            bear = sum(t.side < 0 for t in tags)
            same_side += (bull - bear) * side > 0
    return {"sessions": len(boards), "rows": rows, "same_side": same_side, "methods": out}


def vendor_methods(tags: tuple[str, ...]) -> set[str]:
    """Which of our methods a board row's tags name."""
    out = set()
    for t in tags:
        if t.startswith(("Early Break", "Building ", "Confirmed ")):
            out.add("stage")
        elif t in ("Compelling fundamentals", "Weak fundamentals"):
            out.add("fundamentals")
        elif t.startswith("Money rotating"):
            out.add("rotation")
        elif t.endswith("Trade Scan"):
            out.add("scan")
        elif t.startswith("Analyst "):
            out.add("revision")
    return out


def score(conn=None) -> dict[str, Any]:
    """Our tags on every saved board's session, scored (`score_boards`)."""
    from . import editions

    boards = editions.load_boards()
    if not boards:
        return {"ok": False, "reason": "no saved boards"}
    own = conn is None
    conn = conn or store.connect()
    try:
        bench = {b.date: b.close for b in store.adjusted_bars(conn, stage.DEFAULT_RULE.benchmark)}
        names = sorted({s for b in boards.values() for s in b})
        bars = {s: bs for s in names if (bs := store.adjusted_bars(conn, s))}
        funds = sorted(
            {*SECTOR_FUND.values(), rotation.DEFAULT_RULE.benchmark, rotation.DEFAULT_RULE.asset_benchmark}
        )
        fund_closes = {f: {b.date: b.close for b in store.adjusted_bars(conn, f)} for f in funds}
    finally:
        if own:
            conn.close()
    sector_of = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    events = revisions.load()
    ours = {}
    snapshots: dict[str, dict] = {}
    for day in boards:
        fc = {f: {d: c for d, c in s.items() if d <= day} for f, s in fund_closes.items()}
        _, pct = fundamentals_on(day, sector_of, snapshots)
        ours[day] = tags_on(day, bars, bench, fc, sector_of, revisions.tags(events, day), pct)
    return {"ok": True, **score_boards(ours, boards)}
