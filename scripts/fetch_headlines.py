"""The morning's headlines, for the narrative only: title, link, source and time, nothing else.

Phase 7 of docs/market-report-plan.md. The report's prior-session movers and week-ahead sections are
prose over headlines; the movers and the calendar are data the suite already computes, and this
supplies the headlines. It fetches the RSS feeds the plan verified (Fed press releases, CNBC, the
MarketWatch and WSJ markets feeds -- Reuters' is dead) once each, keeps what was published in the
last `WINDOW_HOURS`, drops duplicates across feeds, and writes
`~/.cherrypick/data/market-report/headlines/<date>.json`.

**Title, link and time only**, as the plan says: no article bodies are fetched or stored. The one
reader is `scripts/morning_narrative.py`, outside every package, so a failed feed costs a line of
context in a note and nothing else. A feed that fails is recorded with its error, never retried in
a loop.

Standard library only. One request per feed, a second apart.

    python scripts/fetch_headlines.py [--hours 36]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = (
    ("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("cnbc_markets", "CNBC Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("cnbc_top", "CNBC Top News", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("mw_top", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("mw_pulse", "MarketWatch Pulse", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("wsj_markets", "WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
)  # fmt: skip
WINDOW_HOURS = 36
PAUSE_S = 1.0
TIMEOUT_S = 20
USER_AGENT = "cherrypick-headlines/1 (personal market report; title and link only)"


def _published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse(xml: bytes | str, source: str) -> list[dict]:
    """Items from an RSS 2.0 or Atom document: title, link, published (UTC ISO or None)."""
    root = ET.fromstring(xml)
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for node in root.iter():
        if node.tag == "item":
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            when = _published(
                node.findtext("pubDate") or node.findtext("{http://purl.org/dc/elements/1.1/}date")
            )
        elif node.tag == f"{atom}entry":
            title = (node.findtext(f"{atom}title") or "").strip()
            el = node.find(f"{atom}link")
            link = (el.get("href") if el is not None else "") or ""
            when = _published(node.findtext(f"{atom}updated") or node.findtext(f"{atom}published"))
        else:
            continue
        if title:
            items.append(
                {
                    "title": " ".join(title.split()),
                    "link": link or None,
                    "source": source,
                    "published": when.astimezone(UTC).isoformat() if when else None,
                }
            )
    return items


def select(items: list[dict], now: datetime, hours: int = WINDOW_HOURS) -> list[dict]:
    """Within the window, newest first, one per title (feeds syndicate each other's stories).
    An undated item is kept, after the dated ones: a missing time is not a reason to drop news."""
    cutoff = now - timedelta(hours=hours)
    seen, out = set(), []
    dated = sorted((i for i in items if i["published"]), key=lambda i: i["published"], reverse=True)
    for item in dated + [i for i in items if not i["published"]]:
        if item["published"] and datetime.fromisoformat(item["published"]) < cutoff:
            continue
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def out_path(day: str) -> Path:
    home = Path(os.environ.get("CHERRYPICK_HOME") or (Path.home() / ".cherrypick"))
    return home / "data" / "market-report" / "headlines" / f"{day}.json"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 -- fixed https URLs
        return resp.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--hours", type=int, default=WINDOW_HOURS)
    args = ap.parse_args(argv)
    now = datetime.now(UTC)
    feeds, items = [], []
    for n, (fid, source, url) in enumerate(FEEDS):
        if n:
            time.sleep(PAUSE_S)
        try:
            got = parse(fetch(url), source)
            feeds.append({"id": fid, "source": source, "ok": True, "items": len(got), "error": None})
            items += got
        except Exception as exc:  # noqa: BLE001 -- recorded, never retried
            feeds.append(
                {
                    "id": fid,
                    "source": source,
                    "ok": False,
                    "items": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
    kept = select(items, now, args.hours)
    day = now.astimezone().date().isoformat()
    doc = {"generated_at": now.isoformat(), "window_hours": args.hours, "feeds": feeds, "items": kept}
    path = out_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)
    print(
        json.dumps(
            {
                "ok": any(f["ok"] for f in feeds),
                "items": len(kept),
                "feeds_ok": sum(f["ok"] for f in feeds),
                "path": str(path),
            }
        )
    )
    return 0 if any(f["ok"] for f in feeds) else 1


if __name__ == "__main__":
    sys.exit(main())
