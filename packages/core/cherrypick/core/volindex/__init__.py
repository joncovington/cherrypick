"""Each underlying's Cboe volatility index, and the index's daily closes from the files the suite keeps.

A vol index is the market's 30-day implied volatility for its underlying, published daily with decades
of history, so it is the IV history an entry gate ranks against (IV rank, IV percentile) and the IV it
compares with realized volatility, the same on a backtest and in paper. Where the suite needs an IV for
an underlying with no index, there is none here: a gate that reads one refuses that underlying.

The files are Cboe's daily history CSVs (`<data>/market-files/cboe/<INDEX>.csv`, written by the
`market-files` job; `DATE` as MM/DD/YYYY, then `OPEN,HIGH,LOW,CLOSE`, or one column named after the
index). Read-only.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from cherrypick.core import home

#: The underlying's vol index. SPX, SPY and XSP read the VIX; NDX and QQQ the VXN; IWM and RUT the RVX;
#: GLD the GVZ; SLV the VXSLV; USO the OVX.
VOL_INDEX: dict[str, str] = {
    "SPX": "VIX",
    "SPY": "VIX",
    "XSP": "VIX",
    "NDX": "VXN",
    "QQQ": "VXN",
    "IWM": "RVX",
    "RUT": "RVX",
    "GLD": "GVZ",
    "SLV": "VXSLV",
    "USO": "OVX",
}


def index_for(symbol: str) -> str | None:
    return VOL_INDEX.get(symbol.upper())


def path(index: str, folder: Path | None = None) -> Path:
    return (folder or home.data_dir("market-files") / "cboe") / f"{index.upper()}.csv"


def closes(index: str, folder: Path | None = None) -> dict[str, float]:
    """The index's closes by ISO session, oldest first; empty when the file is absent. A row whose date
    or close does not parse is skipped."""
    p = path(index, folder)
    if not p.exists():
        return {}
    out: dict[str, float] = {}
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row.get("CLOSE") or row.get(index.upper())
            try:
                day = datetime.strptime(row["DATE"].strip(), "%m/%d/%Y").date().isoformat()
                out[day] = float(raw)
            except (KeyError, TypeError, ValueError):
                continue
    return dict(sorted(out.items()))


def before(series: dict[str, float], session: str, n: int) -> list[float]:
    """The last `n` closes strictly before `session`, oldest first: what is known at an entry that day."""
    return [v for d, v in series.items() if d < session][-n:]
