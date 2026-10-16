"""The vendor's own stages, read from the saved editions -- the fixture the stage rule is scored on.

Read-only over `~/.cherrypick/data/market-report/vendor-editions/`, which the collector script
writes. Each ticker in an edition's leaders/laggards table is coloured with one of six fixed values,
one per side and stage; decoded that way, every edition's totals equal the counts it states (the
check scripts/fetch_vendor_edition.py makes before saving one).

An edition dated D describes the close of the trading day before D.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

from cherrypick.core import calendar as _calendar

from . import paths
from .stage import Stage

COLOURS = {
    "1B5E20": Stage("leader", "confirmed"),
    "2E7D32": Stage("leader", "building"),
    "43A047": Stage("leader", "early"),
    "7A0030": Stage("laggard", "confirmed"),
    "C60651": Stage("laggard", "building"),
    "E5384F": Stage("laggard", "early"),
}
_TICKER = re.compile(r'color:\s*#([0-9A-Fa-f]{6})[^"]*"[^>]*href="[^"]*[?&]symbol=([A-Z][A-Z.]*)"')


def decode(page_html: str) -> dict[str, Stage]:
    i = page_html.find(">Sector</th>")
    j = page_html.find("The three shades", i)
    if i < 0 or j < 0:
        return {}
    out = {}
    for colour, sym in _TICKER.findall(page_html[i:j]):
        stage = COLOURS.get(colour.upper())
        if stage:
            out[sym] = stage
    return out


def session_of(edition_date: str) -> str:
    return _calendar.previous_trading_day(date.fromisoformat(edition_date)).isoformat()


def load() -> dict[str, dict[str, Stage]]:
    """{session described: {symbol: Stage}} for every saved edition."""
    out = {}
    for path in sorted((paths.market_report_dir() / "vendor-editions").glob("????-??-??.html")):
        stages = decode(path.read_text(encoding="utf-8"))
        if stages:
            out[session_of(path.stem)] = stages
    return out


# The rotation section's four headings, in the vendor's words, and the state each names.
ROTATION_HEADINGS = (
    ("Confirmed Leadership", "leading"),
    ("Early Rotation", "improving"),
    ("Maturing Leadership", "weakening"),
    ("Confirmed Weakness", "lagging"),
)
_FUND = re.compile(r'symbol=([A-Z]+)"[^>]*>\s*[A-Z]+\s*(?:</a>)?\s*,\s*(Industry|Sector|Asset)')


def decode_rotation(page_html: str) -> dict[str, str]:
    """{fund: state} from an edition's rotation section. Headings are found by their own markup
    (">Confirmed Weakness<"), never by the phrase: the paragraph above them uses the same words
    ("climbed out of Confirmed Weakness"), and matching the phrase put funds in the wrong state."""
    a = page_html.find("Sector Rotation")
    b = page_html.find("Relative Strength Leadership", a)
    if a < 0 or b < 0:
        return {}
    seg = page_html[a:b]
    marks = sorted(
        (m.start(), state)
        for heading, state in ROTATION_HEADINGS
        for m in [re.search(r">\s*" + heading + r"[^<]*<", seg)]
        if m
    )
    out = {}
    for i, (start, state) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(seg)
        for fund, _kind in _FUND.findall(seg[start:end]):
            out[fund] = state
    return out


def load_rotation() -> dict[str, dict[str, str]]:
    """{session described: {fund: state}} for every saved edition."""
    out = {}
    for path in sorted((paths.market_report_dir() / "vendor-editions").glob("????-??-??.html")):
        states = decode_rotation(path.read_text(encoding="utf-8"))
        if states:
            out[session_of(path.stem)] = states
    return out


@dataclass(frozen=True)
class BoardRow:
    side: str  # "bullish" or "bearish": the list the row sits in
    tags: tuple[str, ...]  # every tag, in the vendor's words, detail stripped
    revision: int  # +1 Analyst Bullish Revision, -1 Bearish, 0 none
    revision_detail: str  # the event the tag names ("KeyBanc upgrade"), or ""


_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_ROW_SYMBOL = re.compile(r'[?&]symbol=([A-Z][A-Z.]*)"')
_CHIP = re.compile(r"border-radius: 10px[^>]*>([^<]*)(?:<span[^>]*>([^<]*)</span>)?")


def decode_board(page_html: str) -> dict[str, BoardRow]:
    """{symbol: BoardRow} from an edition's Stacked Signals section (between its heading and Sector
    Rotation's). A row is a table row with a ticker link and at least one tag chip."""
    a = page_html.find("Stacked Signals")
    b = page_html.find("Sector Rotation", a)
    if a < 0 or b < 0:
        return {}
    seg = page_html[a:b]
    # The list headings: ">Bearish<" alone is also a D-Edge badge in the older editions, so the
    # bearish heading is the first one after the bullish heading.
    bull_at = seg.find(">Bullish<")
    bear_at = seg.find(">Bearish<", max(bull_at, 0))
    out = {}
    for m in _ROW.finditer(seg):
        sym = _ROW_SYMBOL.search(m.group(1))
        chips = _CHIP.findall(m.group(1))
        if not sym or not chips:
            continue
        tags, revision, detail = [], 0, ""
        for name, extra in chips:
            name = html.unescape(name).strip()
            tags.append(name)
            if name.startswith("Analyst Bullish Revision"):
                revision = 1
            elif name.startswith("Analyst Bearish Revision"):
                revision = -1
            else:
                continue
            detail = html.unescape(extra).replace("Â", "").strip(" ·")
        side = "bearish" if 0 <= bear_at < m.start() else "bullish"
        out[sym.group(1)] = BoardRow(side, tuple(tags), revision, detail)
    return out


def load_boards() -> dict[str, dict[str, BoardRow]]:
    """{session described: {symbol: BoardRow}} for every saved edition with a board."""
    out = {}
    for path in sorted((paths.market_report_dir() / "vendor-editions").glob("????-??-??.html")):
        rows = decode_board(path.read_text(encoding="utf-8", errors="replace"))
        if rows:
            out[session_of(path.stem)] = rows
    return out
