"""Record Yahoo's sector and industry for every earnings calendar name and every name in the vendor's
sector map.

**Why.** The earnings calendar (docs/earnings-screener-plan.md) took its sector only from the market
report's map (`universe/sectors.json`), the vendor's own sectors read from its report editions. That
covers about 500 names, so most of the calendar showed a dash. Yahoo's sector agreed with the
vendor's on all 120 names sampled from that map (2026-10-17), once three of its labels are renamed
to the vendor's (`RELABEL`); Nasdaq's stock screener agreed on about three in four.

**What.** Yahoo's public search endpoint (`query1.finance.yahoo.com/v1/finance/search`), one request
per name, paced, keeping only the quote whose symbol is the name itself (BRK.B is asked as BRK-B).
The names are the newest earnings calendar's (`technicals earnings-calendar`) and the vendor map's,
less those this file has read in `REFRESH_DAYS` (`RETRY_DAYS` for a name Yahoo gave no sector).
The vendor-map names are asked for their INDUSTRY (2026-10-17): the fundamentals score ranks a name
against its industry peers (`fundamentals.peer_groups`); their sector stays the vendor's. The file
accumulates: `market-report/universe/sectors.yahoo.json`, as
`{fetched_at, names: {symbol: {sector, industry, fetched_on}}, failed}`. A 403 or 429 stops the run;
what was read is still written. The vendor's map always wins for the sector where it has the name.

A script, not package code: it reaches the network. `packages/technicals` reads the file.

    python scripts/fetch_sectors.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime

from cherrypick.technicals import earnings_calendar, fundamentals, paths, symbols

PAUSE_S = 0.5
REFRESH_DAYS = 180  # a sector rarely changes
RETRY_DAYS = 30  # a name Yahoo had no sector for, asked again after this
URL = "https://query1.finance.yahoo.com/v1/finance/search?q={}&quotesCount=5&newsCount=0"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
# Yahoo's names for three sectors, in the vendor's words; the other eight are spelt the same.
RELABEL = {
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
}


def parse(body: dict, symbol: str) -> dict[str, str | None]:
    """The sector and industry of the quote that IS `symbol` (dotted, as the suite spells it), or
    nulls when the search returned no such quote."""
    want = symbol.replace(".", "-")
    for q in (body or {}).get("quotes") or []:
        if str(q.get("symbol") or "").upper() == want:
            sector = q.get("sector") or None
            return {"sector": RELABEL.get(sector, sector), "industry": q.get("industry") or None}
    return {"sector": None, "industry": None}


def due(names: list[str], have: dict, today: date) -> list[str]:
    """The names to ask for: those not read recently enough."""
    out = []
    for s in names:
        row = have.get(s)
        if row:
            age = (today - date.fromisoformat(row["fetched_on"])).days
            if age < (REFRESH_DAYS if row.get("sector") else RETRY_DAYS):
                continue
        out.append(s)
    return out


def calendar_names() -> list[str]:
    files = sorted(earnings_calendar.out_dir().glob("????-??-??.json"))
    if not files:
        return []
    rows = json.loads(files[-1].read_text(encoding="utf-8")).get("rows") or []
    return sorted({r["symbol"] for r in rows if isinstance(r.get("symbol"), str)})


def fetch(symbol: str) -> dict:
    url = URL.format(urllib.parse.quote(symbol.replace(".", "-")))
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as resp:
        return json.loads(resp.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=None, help="ask for at most this many names")
    args = ap.parse_args(argv)
    path = paths.yahoo_sectors()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    have = doc.get("names") or {}
    today = datetime.now(UTC).date()
    # The universe's candidates too (2026-10-20): the report's sector measures fall back to this
    # file for a name the vendor's map lacks, which every name added after 2026-10-24 will be.
    names = sorted({*calendar_names(), *fundamentals.sector_map(), *symbols.candidates()})
    todo = due(names, have, today)[: args.limit]
    failed: list[str] = []
    started = time.monotonic()
    for k, sym in enumerate(todo):
        if k:
            time.sleep(PAUSE_S)
        try:
            have[sym] = {**parse(fetch(sym), sym), "fetched_on": today.isoformat()}
        except urllib.error.HTTPError as exc:
            failed.append(f"{sym}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
        except Exception as exc:  # noqa: BLE001 -- one name failing is a gap, not the run
            failed.append(f"{sym}: {type(exc).__name__}: {str(exc)[:100]}")
    out = {"fetched_at": datetime.now(UTC).isoformat(), "names": dict(sorted(have.items())), "failed": failed}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(path)
    report = {
        "ok": not failed,
        "asked": len(todo),
        "with_sector": sum(1 for s in todo if (have.get(s) or {}).get("sector")),
        "held": len(have),
        "failed": failed[:20],
        "written": str(path),
        "seconds": round(time.monotonic() - started),
    }
    print(json.dumps(report, indent=1))
    return 0 if not failed or len(failed) < len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
