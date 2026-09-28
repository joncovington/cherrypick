"""The vendor's own stages, read from the saved editions -- the fixture the stage rule is scored on.

Read-only over `~/.cherrypick/data/market-report/vendor-editions/`, which the collector script
writes. Each ticker in an edition's leaders/laggards table is coloured with one of six fixed values,
one per side and stage; decoded that way, every edition's totals equal the counts it states (the
check scripts/fetch_vendor_edition.py makes before saving one).

An edition dated D describes the close of the trading day before D.
"""

from __future__ import annotations

import re
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
