"""The headline fetch: what it keeps from a feed, and what it drops.

Beside overview's tests because the morning narrative, which reads the file, sits beside the pack;
the script is standard library only.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_headlines.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_headlines", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fh = _module()

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>Fed holds rates</title><link>https://x/1</link>
  <pubDate>Mon, 28 Sep 2026 12:00:00 GMT</pubDate><description>body text</description></item>
<item><title>  Stocks   slip  </title><link>https://x/2</link>
  <pubDate>Sun, 27 Sep 2026 20:00:00 -0400</pubDate></item>
<item><title>Old news</title><link>https://x/3</link><pubDate>Wed, 23 Sep 2026 12:00:00 GMT</pubDate></item>
<item><title>No date</title><link>https://x/4</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>fed holds rates</title><link href="https://y/1"/><updated>2026-09-28T11:00:00Z</updated></entry>
</feed>"""


def test_items_carry_title_link_source_and_time_only():
    items = fh.parse(RSS, "Feed")
    assert items[0] == {
        "title": "Fed holds rates",
        "link": "https://x/1",
        "source": "Feed",
        "published": "2026-09-28T12:00:00+00:00",
    }
    assert items[1]["title"] == "Stocks slip" and items[1]["published"] == "2026-09-28T00:00:00+00:00"


def test_atom_parses_too():
    assert fh.parse(ATOM, "Atom")[0]["link"] == "https://y/1"


def test_the_window_keeps_recent_and_undated_items_and_one_per_title():
    now = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    kept = fh.select(fh.parse(RSS, "Feed") + fh.parse(ATOM, "Atom"), now, 36)
    assert [i["title"] for i in kept] == ["Fed holds rates", "Stocks slip", "No date"]
    assert kept[0]["source"] == "Feed", "the newer copy of a syndicated story wins"
