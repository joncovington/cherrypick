"""Read QuikOptions' Hot Options Report and its Resources > Calendars page with our own login.

**Why this exists.** `docs/quikoptions-plan.md`: the report adds what OCC's cleared volume cannot
(trade counts by size, the day's largest single-leg trades by contracts, sweeps and spreads, volume
against open interest) for the console and a daily Discord series, and the site's calendar is an
independent check on `cherrypick.core.events`. A check, never a source.

A script rather than package code: it reaches the network, and nothing on a decision path may. It
writes only its own store, `~/.cherrypick/data/quikoptions/`, and a failure leaves every saved day
exactly as it was.

**What it reads.** The site is Blazor Server: pages are drawn on its server and arrive as binary
render batches over one websocket, with no JSON data call (the 2026-10-02 probe). So the capture
reads the rendered tables — exactly what a person sees — and keeps their HTML beside the parsed
rows, so a parser fix can be re-run over every saved day without another visit. Only the tables
are kept: the full page carries the account's name and email.

**A person's pace.** The installed Chrome on a persistent profile a person signed in to by hand,
one session per run, the calendar reached through the menu, a dwell and a slow scroll. No request
is sent that the page would not send. No stealth plugins, no fingerprint changes, no captcha
solving.

**It stops rather than fights.** A 429, or a 403 once signed in, ends the run and starts a 24-hour
cooldown later runs respect. An expired session ends the run with a notification: it never signs
in by itself, and a person runs `login`.

**A capture proves itself before it counts**: the identities the page already satisfies (below,
`validate_report`). One that fails is kept as `YYYY-MM-DD.rejected.*`, never as the day's file. A
saved day is never overwritten.

    python scripts/fetch_quikoptions.py login                 # sign in by hand; the session is kept
    python scripts/fetch_quikoptions.py hot-options [--jitter MIN] [--headless] [--no-calendar]
    python scripts/fetch_quikoptions.py validate FILE...      # re-check saved captures, offline
    python scripts/fetch_quikoptions.py reparse [YYYY-MM-DD]  # rebuild saved JSON from its HTML
    python scripts/fetch_quikoptions.py probe [--linger 120]  # record both pages, a person present
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import random
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

SITE = "https://app.quikoptions.com"
REPORT_URL = f"{SITE}/Market/Options/THOR/Stock"
REPORT_MARKER = "Hot Options Report"
SIGN_IN_TIMEOUT_S = 600
TABLES_TIMEOUT_S = 90
ET = ZoneInfo("America/New_York")

# Pacing, as the vendor collector's: a person reading, not a crawler.
PAUSE_RANGE_S = (20.0, 45.0)
DWELL_S = (30.0, 60.0)
SCROLL_STEP_PX = (350, 650)
SCROLL_PAUSE_S = (1.2, 3.0)
COOLDOWN = timedelta(hours=24)
MAX_JITTER_MIN = 60

# The probe's recorder: bodies worth keeping; images, fonts and stylesheets by URL only.
BODY_TYPES = ("json", "text/plain", "text/html", "javascript")
BODY_RESOURCE_TYPES = ("xhr", "fetch", "document", "eventsource", "other")
MAX_BODY = 2_000_000

# A saved fragment must never carry an address (the full page names the account; see above).
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ------------------------------------------------------------------------------------------------
# The tables: pure functions over HTML. No network, no browser.
# ------------------------------------------------------------------------------------------------

# Each table's columns as the site draws them, by header text. A header is matched by prefix
# ("Symbol (10)", "Heatmap Off On"), and an empty one must be empty: the site's unlabelled columns
# are the row menu, the call/put badge and the side dot. A change here is the site changing its
# table, and a capture that does not match is rejected rather than read by position.
SKIP, SYMBOL, COUNT, INT, NUM, EXPIRY, CP, SIDE, TIME, TEXT, TICKER = (
    "skip", "symbol", "count", "int", "num", "expiry", "cp", "side", "time", "text", "ticker",
)  # fmt: skip
BUCKETS = (
    "1s",
    "2s",
    "3s",
    "4s",
    "5s",
    "6s",
    "7s",
    "8s",
    "9s",
    "10s",
    "<20",
    "<50",
    "<100",
    "<500",
    "<1K",
    "=>1K",
)
TABLES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "birdseye": (
        "Birdseye",
        [("", "menu", SKIP), ("Heatmap", "symbol", SYMBOL)]
        + [(b, b, COUNT) for b in BUCKETS]
        + [("Calls", "calls", COUNT), ("Puts", "puts", COUNT), ("Total", "total", COUNT)],
    ),
    "outrights": (
        "Top Outrights",
        [
            ("", "menu", SKIP), ("Symbol", "symbol", SYMBOL), ("Time (ET)", "time_et", TIME),
            ("Size", "size", INT), ("Expires", "expires", EXPIRY), ("Strike", "strike", NUM),
            ("", "cp", CP), ("Price", "price", NUM), ("", "side", SIDE),
        ],
    ),
    "sweeps": (
        "Top Sweeps",
        [
            ("", "menu", SKIP), ("Symbol", "symbol", SYMBOL), ("Size", "size", INT),
            ("Expires", "expires", EXPIRY), ("Strike", "strike", NUM), ("", "cp", CP),
            ("Price", "price", NUM), ("", "side", SIDE), ("Premium", "premium", NUM),
        ],
    ),
    "spreads": (
        "Top Spreads",
        [
            ("", "menu", SKIP), ("Symbol", "symbol", SYMBOL), ("Time (ET)", "time_et", TIME),
            ("Size", "size", INT), ("Expires", "expires", EXPIRY), ("Type", "type", TEXT),
            ("", "cp", CP), ("Spread", "spread", TEXT), ("Price", "price", NUM),
            ("Delta", "delta", NUM), ("Premium", "premium", NUM), ("*", "flag", TEXT),
            ("Ticker", "underlying", TICKER), ("Exchange", "exchange", TEXT),
        ],
    ),
    "voloi": (
        "Top VolOverOI (OI > 100)",
        [
            ("", "menu", SKIP), ("Symbol", "symbol", SYMBOL), ("Volume", "volume", INT),
            ("OI", "oi", INT), ("V/OI", "v_oi", NUM), ("Expires", "expires", EXPIRY),
            ("Strike", "strike", NUM), ("", "cp", CP),
        ],
    ),
    "openings": (
        "Top VolOverOI (Openings)",
        [
            ("", "menu", SKIP), ("Symbol", "symbol", SYMBOL), ("Volume", "volume", INT),
            ("OI", "oi", INT), ("V/OI", "v_oi", NUM), ("Expires", "expires", EXPIRY),
            ("Strike", "strike", NUM), ("", "cp", CP),
        ],
    ),
}  # fmt: skip
CALENDAR_HEADING = "Economic"
CALENDAR_COLUMNS = [
    ("Date", "when", TEXT), ("Impact", "impact", TEXT), ("Event", "event", TEXT),
    ("Country", "country", TEXT), ("Currency", "currency", TEXT), ("Previous", "previous", TEXT),
    ("Estimate", "estimate", TEXT), ("Actual", "actual", TEXT), ("Change", "change", TEXT),
    ("Change%", "change_pct", TEXT),
]  # fmt: skip
HEADINGS = tuple(heading for heading, _ in TABLES.values())
PAGE_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")

# Elements whose text is never a cell's value: the row menu's items, the paid badge count, icons.
_HIDDEN_CLASSES = {"dropdown-menu", "qe-badge"}
_HIDDEN_TAGS = {"svg", "script", "style"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


# A report table. The site's 2026-10-09 redesign dropped the `quikgrid` class (now `w-full
# border-none`); the `table-<uuid>` id is what both layouts share, and stored fragments from before
# it still carry the class.
_GRID_ID_RE = re.compile(r"^table-[0-9a-f-]+$")


def _is_grid(table_id: str, classes: set[str]) -> bool:
    return "quikgrid" in classes or bool(_GRID_ID_RE.match(table_id))


class _Grids(HTMLParser):
    """Every report table (`_is_grid`) with the heading text last seen before it, and the first m/d/yyyy
    text outside a table (the report's session date). A cell is its visible text plus the `title`
    and `data-bs-title` attributes inside it, which is where the site keeps the full company name,
    the call/put word, the full timestamp, the underlying's bid/ask and the side tooltip."""

    def __init__(self, headings: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self.headings = headings
        self.heading: str | None = None
        self.page_date: str | None = None
        self.tables: list[dict] = []
        self._table: dict | None = None
        self._section = "body"
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._stack: list[tuple[str, bool]] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in _VOID:
            return
        classes = set(a.get("class", "").split())
        hide = self._hidden > 0 or tag in _HIDDEN_TAGS or bool(classes & _HIDDEN_CLASSES)
        self._stack.append((tag, hide))
        if hide:
            self._hidden += 1
            return
        if tag == "table" and _is_grid(a.get("id", ""), classes):
            self._table = {"heading": self.heading, "columns": [], "rows": []}
            self.tables.append(self._table)
        if self._table is None:
            return
        if tag in ("thead", "tbody"):
            self._section = tag[1:]
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {"text": [], "titles": [], "tips": []}
        if self._cell is not None:
            if a.get("title"):
                self._cell["titles"].append(a["title"])
            if a.get("data-bs-title"):
                self._cell["tips"].append(a["data-bs-title"])

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        while self._stack:
            open_tag, hide = self._stack.pop()
            if hide:
                self._hidden -= 1
            if open_tag == tag:
                break
        if self._table is None or self._hidden:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["parts"] = list(self._cell["text"])
            self._cell["text"] = " ".join(" ".join(self._cell["text"]).split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._section == "head":
                self._table["columns"] = [c["text"] for c in self._row]
            elif self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table":
            self._table = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or self._hidden:
            return
        if self._table is None:
            if text in self.headings:
                self.heading = text
            elif self.page_date is None and PAGE_DATE_RE.match(text):
                self.page_date = text
        elif self._cell is not None:
            self._cell["text"].append(text)


def grids(page_html: str, headings: tuple[str, ...]) -> tuple[str | None, list[dict]]:
    parser = _Grids(headings)
    parser.feed(page_html)
    parser.close()
    return parser.page_date, parser.tables


def number(text: str) -> float | None:
    """'421.8K' -> 421800.0, '1,135.05' -> 1135.05, '-921,800' -> -921800.0; None when blank."""
    t = text.replace(",", "").strip()
    if not t:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(t[-1].upper(), 1.0)
    if mult != 1.0:
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def rounding(text: str) -> float:
    """Half a unit in the last place the site showed: '421.8K' -> 50, '83' -> 0 (a whole count is
    exact), '0.37' -> 0.005."""
    t = text.replace(",", "").strip()
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(t[-1:].upper(), 1.0)
    if mult != 1.0:
        t = t[:-1]
    decimals = len(t.split(".")[1]) if "." in t else 0
    if mult == 1.0 and decimals == 0:
        return 0.0
    return 0.5 * 10.0**-decimals * mult


def parse_side(tip: str) -> dict | None:
    """The side dot's tooltip, "<div><span class='fw-bold'>Bullish</span><br/>On Ask<br/>Edge:
    1.00</div>" -> {"sentiment": "Bullish", "fill": "On Ask", "edge": 1.0}. The site's own
    classification, recorded as its word."""
    parts = [
        " ".join(re.sub(r"<[^>]+>", " ", p).split()) for p in re.split(r"<br\s*/?>", htmlmod.unescape(tip))
    ]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    side = {"sentiment": parts[0], "fill": parts[1], "edge": None}
    for p in parts[2:]:
        m = re.match(r"Edge:\s*(-?[\d.]+)", p)
        if m:
            side["edge"] = float(m.group(1))
    return side


def parse_side_parts(parts: list[str]) -> dict | None:
    """The same three facts since the 2026-10-09 redesign, drawn as a popover's text rather than a
    tooltip: ["Neutral", "Mid Market", "Edge:", "0.43"]."""
    if len(parts) < 2:
        return None
    side = {"sentiment": parts[0], "fill": parts[1], "edge": None}
    m = re.search(r"Edge:\s*(-?[\d.]+)", " ".join(parts[2:]))
    if m:
        side["edge"] = float(m.group(1))
    return side


def _expiry(text: str) -> str | None:
    try:
        return datetime.strptime(text, "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


def _cell_value(kind: str, cell: dict):
    text, titles = cell["text"], [t for t in cell["titles"] if t != "Add to Favorites"]
    if kind == SYMBOL:
        return {"symbol": text.split(" ", 1)[0] if text else None, "name": titles[-1] if titles else None}
    if kind == COUNT:
        return number(text)
    if kind == INT:
        value = number(text)
        return int(value) if value is not None else None
    if kind == NUM:
        return number(text)
    if kind == EXPIRY:
        return _expiry(text)
    if kind == CP:
        # M is the site's own word for a structure with both a call and a put leg (a risk reversal,
        # a strangle), first seen 2026-10-07: read, not missing.
        return {"C": "call", "P": "put", "M": "mixed"}.get(text)
    if kind == SIDE:
        if cell["tips"]:
            return parse_side(cell["tips"][0])
        return parse_side_parts(cell.get("parts") or [])
    if kind == TIME:
        return text or None
    if kind == TICKER:
        bid_ask = re.match(r"\s*([\d.]+)\s*/\s*([\d.]+)", titles[0]) if titles else None
        return {
            "last": number(text),
            "bid": float(bid_ask.group(1)) if bid_ask else None,
            "ask": float(bid_ask.group(2)) if bid_ask else None,
        }
    return text


def columns_problem(name: str, spec: list[tuple[str, str, str]], columns: list[str]) -> str | None:
    want = [h for h, _, _ in spec]
    ok = len(columns) == len(want) and all(
        (c == "" if w == "" else c.startswith(w)) for c, w in zip(columns, want, strict=True)
    )
    return None if ok else f"{name}: columns changed: {columns} (expected {want})"


def read_rows(spec: list[tuple[str, str, str]], table: dict) -> list[dict]:
    out = []
    for cells in table["rows"]:
        row: dict = {}
        for (_, key, kind), cell in zip(spec, cells, strict=False):
            if kind == SKIP:
                continue
            value = _cell_value(kind, cell)
            if kind in (COUNT, INT, NUM):
                # What the site printed, so every check allows exactly the site's own rounding.
                row.setdefault("shown", {})[key] = cell["text"]
            if kind == SYMBOL:
                row.update(value)
            elif kind == COUNT:
                if key in BUCKETS:
                    row.setdefault("buckets", {})[key] = value
                else:
                    row[key] = value
            else:
                row[key] = value
        out.append(row)
    return out


def parse_report(page_html: str) -> dict:
    """{"session", "tables": {name: [row, ...]}, "problems": [...]} from the report's HTML. Reading
    problems (a missing table, changed columns) are returned, never raised: the caller keeps the
    capture aside as rejected, with the reason."""
    page_date, found = grids(page_html, HEADINGS)
    by_heading = {t["heading"]: t for t in found if t["heading"]}
    problems: list[str] = []
    session = None
    if page_date:
        try:
            session = datetime.strptime(page_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            problems.append(f"session date did not read: {page_date!r}")
    else:
        problems.append("no session date on the page")
    tables: dict[str, list[dict]] = {}
    for name, (heading, spec) in TABLES.items():
        table = by_heading.get(heading)
        if table is None:
            problems.append(f"{name}: table missing ({heading!r})")
            continue
        why = columns_problem(name, spec, table["columns"])
        if why:
            problems.append(why)
            continue
        tables[name] = read_rows(spec, table)
    return {"session": session, "tables": tables, "problems": problems}


def validate_report(doc: dict, expected: date | None = None) -> list[str]:
    """Every identity the page already satisfies (2026-10-02, checked on the live page):
    Birdseye's size buckets sum to Total and Calls + Puts = Total, to the site's own display
    rounding; Premium = price x size x 100 on sweeps and spreads (signed, on spreads); V/OI =
    Volume / OI, and Openings rows have OI 0 and V/OI = Volume. Plus: the session the page states
    is the one expected, every table is there with at least one row, every expiry and call/put
    reads. A mixed spread (call and put legs, `M`) is the one row the premium identity is not
    checked on: the site's premium for one does not satisfy it."""
    problems = list(doc.get("problems", []))
    if expected is not None and doc.get("session") != expected.isoformat():
        problems.append(f"page shows session {doc.get('session')}, expected {expected.isoformat()}")
    tables = doc.get("tables", {})
    for name in TABLES:
        if name in tables and not tables[name]:
            problems.append(f"{name}: no rows")

    for row in tables.get("birdseye", []):
        sym, shown = row.get("symbol"), row.get("shown", {})
        values = [row.get("buckets", {}).get(b) for b in BUCKETS] + [
            row.get(k) for k in ("calls", "puts", "total")
        ]
        if not sym or any(v is None for v in values):
            problems.append(f"birdseye {sym}: a count did not read")
            continue
        total, total_tol = row["total"], rounding(shown.get("total", ""))
        bucket_sum = sum(row["buckets"].values())
        tol = sum(rounding(shown.get(b, "")) for b in BUCKETS) + total_tol + 1e-6
        if abs(bucket_sum - total) > tol:
            problems.append(
                f"birdseye {sym}: buckets sum to {bucket_sum:,.0f}, total {total:,.0f} (±{tol:,.0f})"
            )
        tol = rounding(shown.get("calls", "")) + rounding(shown.get("puts", "")) + total_tol + 1e-6
        if abs(row["calls"] + row["puts"] - total) > tol:
            problems.append(
                f"birdseye {sym}: calls + puts = {row['calls'] + row['puts']:,.0f}, total {total:,.0f}"
            )

    for name in ("sweeps", "spreads"):
        for row in tables.get(name, []):
            sym, price, size, premium = (
                row.get("symbol"),
                row.get("price"),
                row.get("size"),
                row.get("premium"),
            )
            shown = row.get("shown", {})
            tol = (
                rounding(shown.get("premium", ""))
                + abs(size or 0) * 100 * rounding(shown.get("price", ""))
                + 0.5
            )
            if None in (price, size, premium):
                problems.append(f"{name} {sym}: price, size or premium did not read")
            elif row.get("cp") == "mixed":
                # The site's premium for a mixed structure is not price x size x 100 (2026-10-07:
                # RUN 6/12 RR at 0.16 x 113,000 printed 1,130; VALE and BABA off by other factors),
                # and no formula fits all three. It is kept as the site's figure and marked
                # unverified in `derive`, never checked against an identity it does not satisfy.
                pass
            elif abs(price * size * 100 - premium) > tol:
                problems.append(f"{name} {sym}: {price} x {size} x 100 != premium {premium:,.0f}")

    for name in ("voloi", "openings"):
        for row in tables.get(name, []):
            sym, vol, oi, v_oi = row.get("symbol"), row.get("volume"), row.get("oi"), row.get("v_oi")
            shown = row.get("shown", {})
            r_ratio, r_vol = rounding(shown.get("v_oi", "")), rounding(shown.get("volume", ""))
            if None in (vol, oi, v_oi):
                problems.append(f"{name} {sym}: volume, OI or V/OI did not read")
            elif name == "openings" and (oi != 0 or abs(v_oi - vol) > r_ratio + r_vol + 1e-9):
                problems.append(f"openings {sym}: OI {oi}, V/OI {v_oi} (an opening has OI 0, V/OI = volume)")
            elif name == "voloi" and (
                oi <= 0
                # The ratio's own rounding, plus what rounding in volume and OI can move it by.
                or abs(vol / oi - v_oi)
                > r_ratio + r_vol / oi + vol * rounding(shown.get("oi", "")) / oi**2 + 1e-9
            ):
                problems.append(f"voloi {sym}: {vol} / {oi} != V/OI {v_oi}")

    for name, rows in tables.items():
        for row in rows:
            if "expires" in row and row["expires"] is None:
                problems.append(f"{name} {row.get('symbol')}: expiry did not read")
            if "cp" in row and row["cp"] is None:
                problems.append(f"{name} {row.get('symbol')}: call/put did not read")
    return problems


# What the console and the Discord series show beyond the site's own cells. Computed here, at
# capture, because the console derives no verdicts of its own; recomputable for every saved day
# from its HTML (`reparse`), so a change to a rule here is a re-run, never a lost day.
DERIVED_VERSION = 2  # 2: spreads carry premium_unverified (mixed), kept out of largest_trade
SIZE_BANDS = {
    "1": ("1s",),
    "2-10": ("2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s", "10s"),
    "11-99": ("<20", "<50", "<100"),
    "100+": ("<500", "<1K", "=>1K"),
}


def spread_direction(price: float | None, delta: float | None, cp: str | None) -> str | None:
    """'bought' or 'sold', from the site's own signs: the price's sign is the side (positive a debit
    paid, negative a credit taken), and the delta's sign has to agree with it for the kind of spread.
    A call spread bought is long delta, so price and delta share a sign; a put spread bought is short
    delta, so they are opposite (2026-10-05, the first put spreads seen: NVDA 220/170 PS at +7.02
    with delta -0.23). Where the signs do not fit, one is missing or zero, or call or put is not
    known, the direction is not read."""
    if price is None or delta is None or price == 0 or delta == 0 or cp not in ("call", "put"):
        return None
    if ((price > 0) == (delta > 0)) != (cp == "call"):
        return None
    return "bought" if price > 0 else "sold"


def derive(doc: dict) -> dict:
    """The capture plus `derived`: Birdseye's four size bands and call share, each outright's
    premium (size x price x 100, which the site does not print for outrights), each spread's
    direction and the trades printed together (same symbol, time and size: a roll is two rows),
    premium by the site's own side for outrights and sweeps, the largest trade by premium, and
    every name in more than one table. Pure; the parsed values are left as they were."""
    tables = doc.get("tables", {})
    for row in tables.get("birdseye", []):
        buckets, total = row.get("buckets") or {}, row.get("total")
        row["bands"] = {
            band: (sum(buckets[k] for k in keys) if all(buckets.get(k) is not None for k in keys) else None)
            for band, keys in SIZE_BANDS.items()
        }
        calls = row.get("calls")
        row["call_share"] = calls / total if calls is not None and total else None
    for row in tables.get("outrights", []):
        price, size = row.get("price"), row.get("size")
        row["premium"] = round(price * size * 100, 2) if price is not None and size is not None else None
        row["premium_derived"] = True
    groups: dict[tuple, int] = {}
    for row in tables.get("spreads", []):
        row["direction"] = spread_direction(row.get("price"), row.get("delta"), row.get("cp"))
        row["premium_unverified"] = row.get("cp") == "mixed"
        key = (row.get("symbol"), row.get("time_et"), row.get("size"))
        groups[key] = groups.get(key, 0) + 1
    for row in tables.get("spreads", []):
        key = (row.get("symbol"), row.get("time_et"), row.get("size"))
        row["group"] = f"{key[0]} {key[1]} {key[2]}" if groups[key] > 1 else None
    by_side: dict[str, float] = {}
    trades_by_side: dict[str, int] = {}
    for name in ("outrights", "sweeps"):
        for row in tables.get(name, []):
            sentiment = (row.get("side") or {}).get("sentiment")
            if sentiment and row.get("premium") is not None:
                by_side[sentiment] = by_side.get(sentiment, 0.0) + abs(row["premium"])
                trades_by_side[sentiment] = trades_by_side.get(sentiment, 0) + 1
    seen: dict[str, dict] = {}
    for name in TABLES:
        for row in tables.get(name, []):
            sym = row.get("symbol")
            if not sym:
                continue
            entry = seen.setdefault(sym, {"symbol": sym, "name": row.get("name"), "tables": []})
            if name not in entry["tables"]:
                entry["tables"].append(name)
    names = sorted(
        (e for e in seen.values() if len(e["tables"]) > 1), key=lambda e: (-len(e["tables"]), e["symbol"])
    )
    largest = None
    for name in ("outrights", "sweeps", "spreads"):
        for row in tables.get(name, []):
            if (
                row.get("premium") is not None
                and not row.get("premium_unverified")
                and (largest is None or abs(row["premium"]) > largest["premium"])
            ):
                largest = {"table": name, "symbol": row.get("symbol"), "premium": abs(row["premium"])}
    doc["derived"] = {
        "version": DERIVED_VERSION,
        "names": names,
        "premium_by_side": by_side,
        "trades_by_side": trades_by_side,
        "largest_trade": largest,
    }
    return doc


# A time, or none: the site may print a word ("All Day", "Tentative") where an event has no time.
_CAL_WHEN = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2})/(\d{1,2})"
    r"(?: (?:(\d{1,2}):(\d{2}) ([AP]M)|[A-Za-z][A-Za-z ]*))?$"
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _calendar_when(text: str, captured: date) -> tuple[str | None, str | None]:
    """'Fri 10/2 8:30 AM' -> ('2026-10-02', '08:30'). The site gives no year: it is the capture's,
    or the next one for a January date seen in December. The weekday the site prints must agree with
    the date that gives, which catches a wrong year as well as a misread."""
    m = _CAL_WHEN.match(text)
    if not m:
        return None, None
    month, day = int(m.group(2)), int(m.group(3))
    year = captured.year + (1 if month < captured.month - 6 else 0) - (1 if month > captured.month + 6 else 0)
    try:
        when = date(year, month, day)
    except ValueError:
        return None, None
    if _WEEKDAYS[when.weekday()] != m.group(1):
        return None, None
    hhmm = None
    if m.group(4):
        hour = int(m.group(4)) % 12 + (12 if m.group(6) == "PM" else 0)
        hhmm = f"{hour:02d}:{m.group(5)}"
    return when.isoformat(), hhmm


def parse_calendar(page_html: str, captured: date) -> dict:
    """{"captured", "events": [...], "problems": [...]} from the Economic calendar's HTML."""
    _, found = grids(page_html, (CALENDAR_HEADING,))
    problems: list[str] = []
    table = next((t for t in found if t["heading"] == CALENDAR_HEADING), None)
    if table is None:
        return {"captured": captured.isoformat(), "events": [], "problems": ["calendar: table missing"]}
    why = columns_problem("calendar", CALENDAR_COLUMNS, table["columns"])
    if why:
        return {"captured": captured.isoformat(), "events": [], "problems": [why]}
    events = []
    for cells in table["rows"]:
        if len(cells) < len(CALENDAR_COLUMNS):
            # A one-cell separator ("no events") is a row of the view, not an event; anything else
            # short is the table changing shape.
            if len(cells) > 1:
                problems.append(f"calendar: a row of {len(cells)} cells: {[c['text'] for c in cells]}")
            continue
        row = {key: cell["text"] or None for (_, key, _), cell in zip(CALENDAR_COLUMNS, cells, strict=False)}
        row["date"], row["time_et"] = _calendar_when(cells[0]["text"], captured)
        if row["date"] is None:
            problems.append(f"calendar: date did not read: {cells[0]['text']!r}")
        row["country"] = (cells[3]["titles"] or [None])[0]
        events.append(row)
    return {"captured": captured.isoformat(), "events": events, "problems": problems}


def validate_calendar(doc: dict) -> list[str]:
    problems = list(doc.get("problems", []))
    events = doc.get("events", [])
    if not events:
        problems.append("calendar: no events")
    captured = date.fromisoformat(doc["captured"])
    for e in events:
        if e.get("date") and not (
            captured - timedelta(days=7) <= date.fromisoformat(e["date"]) <= captured + timedelta(days=21)
        ):
            problems.append(f"calendar: {e.get('event')} on {e['date']} is outside the view's window")
        if e.get("impact") not in ("H", "M", "L"):
            problems.append(f"calendar: {e.get('event')} has impact {e.get('impact')!r}")
    return problems


def tables_fragment(page_html: str, headings: tuple[str, ...], with_date: bool) -> str:
    """Only what is kept: the session date, each heading and its table, verbatim but for the icons
    and Blazor's `<!--!-->` markers, which are half the bytes and none of the data. Never the page:
    the page names the account."""
    page_html = re.sub(r"<svg\b.*?</svg>", "", page_html, flags=re.S).replace("<!--!-->", "")
    parts = []
    if with_date:
        page_date, _ = grids(page_html, headings)
        if page_date:
            parts.append(f"<p>{page_date}</p>")
    for m in re.finditer(
        r"<table\b[^>]*(?:\bquikgrid\b|\bid=\"table-[0-9a-f-]+\")[^>]*>.*?</table>", page_html, re.S
    ):
        before = page_html[: m.start()]
        spots = {h: before.rfind(">" + htmlmod.escape(h, quote=False) + "<") for h in headings}
        heading = max(spots, key=spots.get)
        if spots[heading] >= 0:
            parts.append(f"<h2>{htmlmod.escape(heading, quote=False)}</h2>\n{m.group(0)}")
    return "\n".join(parts)


# ------------------------------------------------------------------------------------------------
# The store, the cooldown, notifications.
# ------------------------------------------------------------------------------------------------


def store_dir() -> Path:
    from cherrypick.core import home

    return home.data_dir("quikoptions")


def save_capture(
    kind: str, day: str, doc: dict, fragment: str, problems: list[str], root: Path | None = None
) -> str:
    """'saved', 'exists' (never overwritten) or 'rejected' (kept aside with its reasons)."""
    from cherrypick.core.jsonio import write_json_atomic

    out = (root or store_dir()) / kind
    out.mkdir(parents=True, exist_ok=True)
    if EMAIL_RE.search(fragment):
        problems = [*problems, "the fragment carries an email address; not kept"]
        fragment = ""
    stem = f"{day}.rejected" if problems else day
    if not problems and (out / f"{day}.json").exists():
        return "exists"
    doc = {**doc, "problems": problems, "saved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (out / f"{stem}.html").write_text(fragment, encoding="utf-8")
    write_json_atomic(out / f"{stem}.json", doc)
    return "rejected" if problems else "saved"


def _state_path() -> Path:
    return store_dir() / "collector_state.json"


def cooldown_until() -> datetime | None:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8")).get("cooldown_until")
        return datetime.fromisoformat(raw) if raw else None
    except (OSError, ValueError):
        return None


def start_cooldown(reason: str) -> None:
    until = datetime.now(UTC) + COOLDOWN
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps({"cooldown_until": until.isoformat(), "reason": reason}, indent=2), encoding="utf-8"
    )


def _warn(title: str, message: str) -> None:
    """Best-effort WARNING through the orchestrator's notifier, to the suite's own channels — never
    the hot-options Discord channel."""
    print(f"WARNING: {title}\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify("WARNING", "quikoptions", title, message)
    except Exception:  # noqa: BLE001
        pass


def _log(line: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {line}", flush=True)


def _pause(bounds: tuple[float, float] = PAUSE_RANGE_S) -> None:
    time.sleep(random.uniform(*bounds))


class Throttled(RuntimeError):
    pass


class NeedsPerson(RuntimeError):
    """Signed out, a challenge, or the page never drew: a person has to look."""


# ------------------------------------------------------------------------------------------------
# The browser.
# ------------------------------------------------------------------------------------------------


_SHARED = None


def _shared():
    """The vendor collector's module, loaded from beside this script: the two collectors share its
    browser rules (the user-agent, the profile lock, the smoke check) so they cannot drift apart."""
    global _SHARED
    if _SHARED is None:
        import importlib.util

        path = Path(__file__).resolve().with_name("fetch_vendor_edition.py")
        spec = importlib.util.spec_from_file_location("fetch_vendor_edition", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SHARED = module
    return _SHARED


def _chrome_user_agent(version: str) -> str:
    """The user-agent a regular (headed) Chrome of this version sends -- the vendor collector's rule."""
    return _shared().chrome_user_agent(version)


def _installed_chrome_version(pw) -> str:
    """The installed Chrome's own version (a local launch, no page, no network), so the user-agent
    keeps matching the browser after Chrome updates itself."""
    browser = pw.chromium.launch(channel="chrome", headless=True)
    try:
        return browser.version
    finally:
        browser.close()


def _open_browser(pw, headed: bool = True):
    """The installed Chrome, not Playwright's bundled Chromium, on the store's own profile — so the
    site sees the same browser a person uses, and the session a person left behind.

    Headless (the scheduled default since 2026-10-08) presents as the headed Chrome it is: headless
    Chrome says "HeadlessChrome" in its user-agent, so a site that ever began refusing headless
    browsers would refuse the capture for that word alone. Only the user-agent changes --
    `navigator.webdriver` is left as the browser sets it, the vendor collector's rule."""
    profile = store_dir() / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    extra = {} if headed else {"user_agent": _chrome_user_agent(_installed_chrome_version(pw))}
    return pw.chromium.launch_persistent_context(
        str(profile),
        channel="chrome",
        headless=not headed,
        viewport={"width": 1600, "height": 1000},
        **extra,
    )


def _sign_in_showing(page) -> bool:
    return (
        "auth0.com" in page.url
        or "/Account/Login" in page.url
        or page.locator("input[type=password]").count() > 0
    )


def _report_showing(page) -> bool:
    return page.get_by_text(REPORT_MARKER, exact=False).count() > 0


def _wait_for_report(page, timeout_s: float) -> bool:
    """Wait until the report is on screen. The site passes through its sign-in (Auth0) on the way
    in and usually comes straight back signed in, so only a sign-in form that stays is reported."""
    deadline = time.monotonic() + timeout_s
    since: float | None = None
    told = False
    while time.monotonic() < deadline:
        if _report_showing(page) and not _sign_in_showing(page):
            return True
        if _sign_in_showing(page):
            since = since or time.monotonic()
            if not told and time.monotonic() - since > 10:
                _log(f"the site is asking for a sign-in; waiting up to {timeout_s / 60:.0f} min for one")
                told = True
        else:
            since = None
        time.sleep(2)
    return False


def _goto_report(page, timeout_s: float = SIGN_IN_TIMEOUT_S) -> bool:
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=90_000)
    if not _wait_for_report(page, timeout_s):
        return False
    # The site may land a fresh sign-in somewhere else; come back the way a person would.
    if "/THOR/" not in page.url:
        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=90_000)
        return _wait_for_report(page, 90)
    return True


def _slow_scroll(page, to_bottom: bool = True) -> None:
    """Wheel down (or up) in uneven steps until the page will not move further. Ends on the page's
    own limit, never on a fixed margin: a margin taller than the gap a viewport leaves never closes
    (the first real capture, 2026-10-02, scrolled in place for seven minutes on one)."""
    for _ in range(200):
        before = page.evaluate("window.scrollY")
        step = random.randint(*SCROLL_STEP_PX)
        page.mouse.wheel(0, step if to_bottom else -step)
        time.sleep(random.uniform(*SCROLL_PAUSE_S))
        if abs(page.evaluate("window.scrollY") - before) < 2:
            return


def _click_text(page, text: str, *, exact: bool = True) -> bool:
    """Click the first VISIBLE element with this text. The site renders a hidden copy of its menu
    (the 2026-10-02 probe timed out on one), so the first match in the DOM is not the one to use."""
    target = page.get_by_text(text, exact=exact).filter(visible=True).first
    if not target.count():
        _log(f"no visible '{text}' on the page")
        return False
    target.scroll_into_view_if_needed()
    target.hover()
    time.sleep(random.uniform(0.4, 1.2))
    target.click()
    return True


def _wait_for_tables(page, ready, hits: list[str], timeout_s: float = TABLES_TIMEOUT_S) -> str:
    """The page's HTML once `ready(html)` says every table is drawn with rows. A refusal while
    waiting is throttling, not a page that needs a person: tables that never draw because the site
    is refusing us must start the cooldown (review of #21)."""
    deadline = time.monotonic() + timeout_s
    while True:
        if any(h.startswith("429 ") for h in hits):
            raise Throttled(next(h for h in hits if h.startswith("429 ")))
        content = page.content()
        if ready(content):
            return content
        if time.monotonic() > deadline:
            if hits:
                raise Throttled(hits[0])
            raise NeedsPerson(f"the tables were not drawn within {timeout_s:.0f} s ({page.url})")
        time.sleep(2)


def _report_ready(content: str) -> bool:
    _, found = grids(content, HEADINGS)
    drawn = {t["heading"] for t in found if t["rows"]}
    return set(HEADINGS) <= drawn


def _calendar_ready(content: str) -> bool:
    _, found = grids(content, (CALENDAR_HEADING,))
    return any(t["heading"] == CALENDAR_HEADING and t["rows"] for t in found)


def is_refusal(status: int, url: str) -> bool:
    """A refusal that means the site is limiting us: a 403 or 429 from the site itself, or a 429
    from its sign-in (Auth0). Never a third party's: an ad-blocked analytics pixel answering 403
    must not throw away a good capture and the next day's with it."""
    if status == 429 and ".auth0.com/" in url:
        return True
    return status in (403, 429) and url.startswith(SITE + "/")


def _watch(page, hits: list[str]) -> None:
    def on_response(resp):
        if is_refusal(resp.status, resp.url):
            hits.append(f"{resp.status} {resp.url}")

    page.on("response", on_response)


# ------------------------------------------------------------------------------------------------
# Commands.
# ------------------------------------------------------------------------------------------------


def _today_et() -> date:
    return datetime.now(ET).date()


def capture_report(page, hits: list[str]) -> str:
    if not _goto_report(page, timeout_s=60):
        raise NeedsPerson("signed out (or the report never showed): run `login`")
    # A 403 on the way in is the site asking who we are, not slowing us down; a 429 still counts.
    hits[:] = [h for h in hits if not h.startswith("403 ")]
    _wait_for_tables(page, _report_ready, hits)
    end = time.monotonic() + random.uniform(*DWELL_S)
    _slow_scroll(page)
    while time.monotonic() < end:
        time.sleep(1)
    if hits:
        raise Throttled(hits[0])
    content = page.content()  # after the dwell: the tables as they finally stand
    fragment = tables_fragment(content, HEADINGS, with_date=True)
    doc = derive(parse_report(fragment))
    problems = validate_report(doc)
    session = doc.get("session")
    if session:
        from cherrypick.core import calendar as tcal

        day = date.fromisoformat(session)
        if day > _today_et() or not tcal.is_trading_day(day):
            problems.append(f"page session {session} is not a trading day on or before today")
    result = save_capture("hot-options", session or _today_et().isoformat(), doc, fragment, problems)
    _log(f"hot options {session}: {result}" + (f" — {'; '.join(problems[:3])}" if problems else ""))
    if result == "rejected":
        _warn("QuikOptions capture rejected", f"{session}: " + "; ".join(problems[:5]))
    return result


def capture_calendar(page, hits: list[str]) -> str:
    if not _click_text(page, "RESOURCES"):
        raise NeedsPerson("the Resources menu was not found")
    time.sleep(random.uniform(1.0, 2.5))
    if not _click_text(page, "Calendars", exact=False):
        raise NeedsPerson("the Calendars link was not found")
    _wait_for_tables(page, _calendar_ready, hits)
    time.sleep(random.uniform(8, 15))
    if hits:
        raise Throttled(hits[0])
    content = page.content()
    fragment = tables_fragment(content, (CALENDAR_HEADING,), with_date=False)
    captured = _today_et()
    doc = parse_calendar(fragment, captured)
    problems = validate_calendar(doc)
    result = save_capture("calendar", captured.isoformat(), doc, fragment, problems)
    _log(f"calendar {captured}: {result}, {len(doc['events'])} events")
    return result


def cmd_hot_options(args) -> int:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    until = cooldown_until()
    if until and datetime.now(UTC) < until:
        _log(f"in cooldown until {until.isoformat(timespec='minutes')}; nothing requested")
        return 0
    jitter = min(max(args.jitter, 0), MAX_JITTER_MIN)
    if jitter:
        wait = random.uniform(0, jitter * 60)
        _log(f"starting in {wait / 60:.1f} min")
        time.sleep(wait)
    hits: list[str] = []
    try:
        with sync_playwright() as pw:
            ctx = _open_browser(pw, headed=not args.headless)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _watch(page, hits)
            try:
                capture_report(page, hits)
                if not args.no_calendar:
                    _pause()
                    capture_calendar(page, hits)
            finally:
                ctx.close()
    except Throttled as exc:
        start_cooldown(str(exc))
        _warn("QuikOptions throttled: 24-hour cooldown", str(exc))
        return 2
    except NeedsPerson as exc:
        _warn("QuikOptions needs a person", str(exc))
        return 1
    except PlaywrightError as exc:
        _warn("QuikOptions capture failed", str(exc).splitlines()[0])
        return 1
    return 0


def cmd_validate(args) -> int:
    """Re-read saved captures from their HTML and re-run every check, offline."""
    bad = 0
    for name in args.files:
        path = Path(name)
        fragment = path.read_text(encoding="utf-8")
        stem = path.name.split(".")[0]
        if path.parent.name == "calendar":
            doc = parse_calendar(fragment, date.fromisoformat(stem))
            problems = validate_calendar(doc)
        else:
            doc = parse_report(fragment)
            problems = validate_report(doc, date.fromisoformat(stem))
        bad += bool(problems)
        print(f"{path}: " + ("ok" if not problems else "; ".join(problems)))
    return 1 if bad else 0


def cmd_reparse(args) -> int:
    """Rebuild saved days' JSON from their own HTML: the parsed tables and `derived`, after a parser
    or derivation change. The HTML (what the site showed) is never touched; a day whose HTML no
    longer passes every check keeps its JSON as it was and is reported."""
    from cherrypick.core.jsonio import write_json_atomic

    folder = store_dir() / "hot-options"
    days = sorted(folder.glob("????-??-??.html")) if folder.exists() else []
    if args.session:
        days = [p for p in days if p.stem in args.session]
    bad = 0
    # A rejected day whose own HTML now passes (a parser fix, as on 2026-10-07) becomes the day:
    # its tables and JSON are written under the day's name and the rejected pair is removed. A day
    # that already has a saved capture is never replaced by a rejected one.
    rejected = sorted(folder.glob("????-??-??.rejected.html")) if folder.exists() else []
    for html_path in rejected:
        day = html_path.name.split(".", 1)[0]
        if args.session and day not in args.session:
            continue
        if (folder / f"{day}.json").exists():
            continue
        fragment = html_path.read_text(encoding="utf-8")
        doc = derive(parse_report(fragment))
        problems = validate_report(doc, date.fromisoformat(day))
        if problems:
            bad += 1
            print(f"{day}: still rejected — {'; '.join(problems[:3])}")
            continue
        try:
            old = json.loads(html_path.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        now = datetime.now(UTC).isoformat(timespec="seconds")
        (folder / f"{day}.html").write_text(fragment, encoding="utf-8")
        write_json_atomic(
            folder / f"{day}.json",
            {
                **doc,
                "problems": [],
                "saved_at": old.get("saved_at"),
                "reparsed_at": now,
                "promoted_from_rejected": True,
            },
        )
        html_path.unlink()
        html_path.with_suffix(".json").unlink(missing_ok=True)
        print(f"{day}: promoted from rejected")
    for html_path in days:
        json_path = html_path.with_suffix(".json")
        doc = derive(parse_report(html_path.read_text(encoding="utf-8")))
        problems = validate_report(doc, date.fromisoformat(html_path.stem))
        if problems:
            bad += 1
            print(f"{html_path.stem}: kept as it was — {'; '.join(problems[:3])}")
            continue
        try:
            old = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        now = datetime.now(UTC).isoformat(timespec="seconds")
        write_json_atomic(
            json_path, {**doc, "problems": [], "saved_at": old.get("saved_at"), "reparsed_at": now}
        )
        print(f"{html_path.stem}: rebuilt")
    return 1 if bad else 0


def cmd_login(_args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _open_browser(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = _goto_report(page)
        ctx.close()
    print("session saved" if ok else "the report never showed; sign in again")
    return 0 if ok else 1


class Recorder:
    """The probe's: every response and text websocket frame the page receives, one JSON line each,
    tagged with the step the probe was on. Request headers and bodies are never written, nor any
    sign-in response body, so no password, cookie or token reaches disk beyond the browser profile
    itself."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.step = "start"
        self.throttled: list[str] = []
        self.binary_frames: dict[str, int] = {}
        self._fh = (out / "responses.jsonl").open("a", encoding="utf-8")

    def _write(self, entry: dict) -> None:
        entry = {"t": datetime.now().isoformat(timespec="milliseconds"), "step": self.step, **entry}
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()

    def on_response(self, resp) -> None:
        ctype = resp.headers.get("content-type", "")
        req = resp.request
        entry = {
            "kind": "response",
            "status": resp.status,
            "method": req.method,
            "resource": req.resource_type,
            "url": resp.url,
            "type": ctype,
        }
        # A request body is never kept, only its size: the sign-in form posts the username and
        # password, and the callback an id_token (the first probe, 2026-10-02, kept both).
        if req.method != "GET" and req.post_data:
            entry["post_bytes"] = len(req.post_data)
        sign_in = ".auth0.com/" in resp.url or "/callback" in resp.url
        if not sign_in and req.resource_type in BODY_RESOURCE_TYPES and any(t in ctype for t in BODY_TYPES):
            try:
                body = resp.text()
                entry["bytes"] = len(body)
                entry["body"] = body[:MAX_BODY]
            except Exception as exc:  # noqa: BLE001 - a redirect or an aborted body
                entry["body_error"] = str(exc)[:200]
        if resp.status == 429 and is_refusal(resp.status, resp.url):
            self.throttled.append(resp.url)
        self._write(entry)

    def on_websocket(self, ws) -> None:
        self._write({"kind": "ws-open", "url": ws.url})

        def frame(direction: str):
            def handler(payload) -> None:
                # The site's frames are Blazor's binary render batches: counted, not kept (the
                # 2026-10-02 probe wrote 3,400 lines of "<n bytes>"). Text frames are kept.
                if isinstance(payload, str):
                    self._write({"kind": f"ws-{direction}", "url": ws.url, "body": payload[:MAX_BODY]})
                else:
                    self.binary_frames[direction] = self.binary_frames.get(direction, 0) + 1

            return handler

        ws.on("framereceived", frame("in"))
        ws.on("framesent", frame("out"))
        ws.on("close", lambda _ws: self._write({"kind": "ws-close", "url": ws.url}))

    def snapshot(self, page, name: str) -> None:
        (self.out / f"{name}.txt").write_text(page.inner_text("body"), encoding="utf-8")
        (self.out / f"{name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)
        self._write({"kind": "snapshot", "name": name, "url": page.url})

    def check(self) -> None:
        if self.throttled:
            raise Throttled(f"429 from {self.throttled[0]}")

    def close(self) -> None:
        self._write({"kind": "ws-binary-frames", "counts": self.binary_frames})
        self._fh.close()


def cmd_probe(args) -> int:
    """Record the report and the calendars page — every response, the text, the HTML and a
    screenshot of each — for designing the parsers. One visit to each, a person present. The
    probe's files hold the whole page (the account's name included) and stay on this machine."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    until = cooldown_until()
    if until and datetime.now(UTC) < until:
        _log(f"in cooldown until {until.isoformat(timespec='minutes')}; nothing requested")
        return 0
    out = store_dir() / "probe" / f"{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(out)
    _log(f"recording to {out}")
    try:
        with sync_playwright() as pw:
            ctx = _open_browser(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", rec.on_response)
            page.on("websocket", rec.on_websocket)
            try:
                rec.step = "report-load"
                if not _goto_report(page):
                    _log("the report never showed (not signed in?); nothing more recorded")
                    return 1
                time.sleep(random.uniform(12, 20))
                rec.check()
                rec.snapshot(page, "report-top")

                rec.step = "report-scroll"
                _slow_scroll(page)
                time.sleep(random.uniform(5, 10))
                rec.check()
                rec.snapshot(page, "report-scrolled")

                # The rest is best effort: a person may be clicking around the site during a
                # probe (2026-10-02), and a miss here must not cost the linger that follows.
                try:
                    rec.step = "birdseye-toggles"
                    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
                    time.sleep(random.uniform(2, 4))
                    for label in ("VOLUME", "PREMIUM", "TRADES"):
                        rec.step = f"birdseye-{label.lower()}"
                        if _click_text(page, label):
                            time.sleep(random.uniform(5, 10))
                            rec.check()
                            if label != "TRADES":
                                rec.snapshot(page, f"birdseye-{label.lower()}")
                except PlaywrightError as exc:
                    _log(f"{rec.step} missed: {str(exc).splitlines()[0]}")

                rec.step = "pause"
                _pause()

                try:
                    rec.step = "calendars-open"
                    if _click_text(page, "RESOURCES"):
                        time.sleep(random.uniform(1.0, 2.5))
                        if _click_text(page, "Calendars", exact=False):
                            page.wait_for_load_state("domcontentloaded")
                            time.sleep(random.uniform(12, 20))
                            rec.check()
                            rec.snapshot(page, "calendars")
                        else:
                            rec.snapshot(page, "resources-menu")
                except PlaywrightError as exc:
                    _log(f"{rec.step} missed: {str(exc).splitlines()[0]}")

                if args.linger > 0:
                    rec.step = "linger"
                    _log(
                        f"recording for {args.linger} s more: click through the calendar tabs "
                        "(and anything else worth seeing); every response is kept"
                    )
                    end = time.monotonic() + args.linger
                    while time.monotonic() < end:
                        time.sleep(2)
                        rec.check()
                    rec.snapshot(page, "linger-end")
            finally:
                ctx.close()
    except Throttled as exc:
        start_cooldown(str(exc))
        _log(f"STOPPED: {exc}. A 24-hour cooldown has started.")
        return 2
    finally:
        rec.close()
    _log(f"done: {out}")
    return 0


def cmd_smoke(_args) -> int:
    """Is the installed Chrome able to start here, and is the session still signed in? Local only."""
    from urllib.parse import urlparse

    return _shared().smoke(
        lambda pw: _open_browser(pw, headed=False), urlparse(SITE).hostname or "", "QuikOptions", _warn
    )


# The commands that open the browser profile, and so take its lock first.
BROWSER_COMMANDS = {"login", "hot-options", "probe", "smoke"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke").set_defaults(fn=cmd_smoke)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    ho = sub.add_parser("hot-options")
    ho.add_argument(
        "--jitter", type=float, default=0, help=f"start after a random 0..N minutes (max {MAX_JITTER_MIN})"
    )
    ho.add_argument("--headless", action="store_true")
    ho.add_argument("--no-calendar", action="store_true", help="the report only")
    ho.set_defaults(fn=cmd_hot_options)
    rp = sub.add_parser("reparse")
    rp.add_argument("session", nargs="*", help="YYYY-MM-DD (default: every saved day)")
    rp.set_defaults(fn=cmd_reparse)
    va = sub.add_parser("validate")
    va.add_argument("files", nargs="+", help="saved .html captures (hot-options/ or calendar/)")
    va.set_defaults(fn=cmd_validate)
    pr = sub.add_parser("probe")
    pr.add_argument("--linger", type=int, default=120, help="seconds to keep recording at the end")
    pr.set_defaults(fn=cmd_probe)
    args = ap.parse_args(argv)
    if args.cmd not in BROWSER_COMMANDS:
        return args.fn(args)
    shared = _shared()
    try:
        with shared.profile_lock(store_dir() / "browser-profile"):
            return args.fn(args)
    except shared.ProfileBusy as exc:
        print(f"not run: {exc}", file=sys.stderr)
        return shared.PROFILE_BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
