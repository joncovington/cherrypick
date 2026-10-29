"""Fetch Aswath Damodaran's public valuation inputs: the monthly implied equity risk premium and the
interest-coverage-to-rating table (docs/fair-value-model-plan.md).

**Why.** The fair-value model used a fixed 5% equity premium and priced debt at its coupon (interest
over the balance), which for a weak borrower is far below what a lender would charge (AAL, 2026-10-28).
Damodaran publishes both inputs free, from NYU Stern:
- `pc/implprem/ERPbymonth.xlsx`: the implied ERP at the start of each month. The model reads the
  "ERP (T12m with sustainable payout)" column, his headline figure. The workbook uses Excel's 1904
  date system.
- `New_Home_Page/datafile/ratings.html`: interest coverage bands, the synthetic rating each maps to
  and that rating's default spread over the risk-free rate, for large non-financial firms and for
  financial firms, with the table's own "as of" date.

Written to `paths.damodaran_dir()` as `erp.json` and `ratings.json`, each carrying its source URL and
date; packages/fundamentals `damodaran.py` (network-free) reads them. Fetched at most once every
`REFRESH_DAYS`:
the ERP moves monthly, the ratings table yearly. Two requests a run, no credential. The workbook is
read with the standard library (an .xlsx is a zip of XML), so no new dependency.

A script, not package code: it reaches the network.

    python scripts/fetch_damodaran.py [--force]
"""

from __future__ import annotations

import argparse
import html as _html
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from xml.etree import ElementTree as ET

from cherrypick.fundamentals import paths

REFRESH_DAYS = 7
ERP_URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
ERP_COLUMN = "ERP (T12 m with sustainable payout)"
HEADERS = {"User-Agent": "cherrypick personal research"}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EPOCH_1904 = date(1904, 1, 1)
EPOCH_1900 = date(1899, 12, 30)


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
        body = r.read()
    time.sleep(1.0)
    return body


def _col(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0)  # type: ignore[union-attr]


def xlsx_rows(body: bytes, sheet: int = 1) -> tuple[list[dict[str, str | None]], bool]:
    """A worksheet's rows as {column letter: text}, and whether the workbook counts dates from 1904."""
    z = zipfile.ZipFile(io.BytesIO(body))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    rows = []
    for row in ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml")).iter(f"{NS}row"):
        cells: dict[str, str | None] = {}
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v")
            val = None if v is None else v.text
            if c.get("t") == "s" and val is not None:
                val = shared[int(val)]
            cells[_col(c.get("r") or "")] = val
        rows.append(cells)
    return rows, 'date1904="1"' in z.read("xl/workbook.xml").decode("utf-8", "replace")


def parse_erp(body: bytes) -> dict[str, float]:
    """{first of the month (ISO): implied ERP} from the workbook's first sheet."""
    rows, d1904 = xlsx_rows(body)
    header = {v: k for k, v in rows[0].items() if v}
    col = header.get(ERP_COLUMN)
    if col is None:
        raise ValueError(f"no {ERP_COLUMN!r} column in the ERP workbook (headers {sorted(header)})")
    epoch = EPOCH_1904 if d1904 else EPOCH_1900
    out = {}
    for r in rows[1:]:
        serial, erp = r.get("A"), r.get(col)
        if serial is None or erp is None:
            continue
        try:
            day = _day(serial, epoch)
            out[day.isoformat()] = round(_rate(erp), 6)
        except ValueError:
            continue  # a note row or a cell the sheet left as words
    return out


def _day(text: str, epoch: date) -> date:
    """A date cell: an Excel serial (most rows) or text such as "1-Sep-24" (a few)."""
    try:
        return epoch + timedelta(days=int(float(text)))
    except ValueError:
        return datetime.strptime(text.strip(), "%d-%b-%y").date()


def _rate(text: str) -> float:
    """A cell as a fraction: most are numbers (0.042), a few older ones typed text ("4.05%")."""
    text = text.strip()
    return float(text.rstrip("%")) / 100 if text.endswith("%") else float(text)


def _cells(html: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", td))).strip()
                 for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]  # fmt: skip
        rows.append(cells)
    return rows


def parse_ratings(html: str) -> dict:
    """The coverage bands for large non-financial and for financial firms: [low, high, rating, spread]
    each, and the table's own date."""
    as_of = re.search(r"Data used is as of\s+([A-Za-z]+ \d{4})", re.sub(r"<[^>]+>", " ", html))
    bands: dict[str, list] = {"non_financial": [], "financial": []}
    num = re.compile(r"^-?\d+(\.\d+)?$")
    for cells in _cells(html):
        vals = [c for c in cells if c]
        if len(vals) < 8 or not (num.match(vals[0]) and num.match(vals[1]) and vals[3].endswith("%")):
            continue
        for key, (lo, hi, rating, spread) in zip(bands, (vals[0:4], vals[4:8]), strict=True):
            bands[key].append([float(lo), float(hi), rating, round(float(spread.rstrip("%")) / 100, 6)])
    if not bands["non_financial"]:
        raise ValueError("no coverage bands found on the ratings page")
    return {"as_of": as_of.group(1) if as_of else None, **bands}


def _fresh(path) -> bool:
    if not path.exists():
        return False
    return datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) < timedelta(days=REFRESH_DAYS)


def run(force: bool = False) -> dict:
    root = paths.damodaran_dir()
    root.mkdir(parents=True, exist_ok=True)
    out: dict = {"ok": True}
    erp_path, ratings_path = root / "erp.json", root / "ratings.json"
    now = datetime.now(UTC).isoformat()
    if force or not _fresh(erp_path):
        months = parse_erp(get(ERP_URL))
        erp_path.write_text(
            json.dumps(
                {"source": ERP_URL, "column": ERP_COLUMN, "fetched_at": now, "months": months}, indent=1
            ),
            encoding="utf-8",
        )
        out["erp_latest"] = max(months.items())
    if force or not _fresh(ratings_path):
        table = parse_ratings(get(RATINGS_URL).decode("latin-1"))
        ratings_path.write_text(
            json.dumps({"source": RATINGS_URL, "fetched_at": now, **table}, indent=1), encoding="utf-8"
        )
        out["ratings_as_of"] = table["as_of"]
        out["bands"] = len(table["non_financial"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(run(force=args.force)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
