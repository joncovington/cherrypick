"""Generate the shared Market Profile fixture, `packages/core/tests/fixtures/marketprofile_cases.json`.

Both `cherrypick.core.marketprofile` (packages/core/tests/test_marketprofile.py) and the console's
TypeScript mirror (packages/console/web/test/marketProfile.test.ts) answer this file, so the two
cannot drift apart without a red test. The hand-worked cases are worked out again, by hand, in the
Python test; the rest are seeded random walks whose expected answers the module produces here, so
they pin the two implementations to each other rather than prove either right.

**Regenerate only when a definition changes on purpose**, and then in both implementations in the
same PR. `--check` regenerates in memory and exits 1 if the committed file differs: a test runs it,
so a change to the module that moves any expected answer, or a hand edit of the file, fails there
first.

    python scripts/make_marketprofile_fixture.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import marketprofile as mp

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "packages" / "core" / "tests" / "fixtures" / "marketprofile_cases.json"


def epoch(d, hh, mm):
    return int(dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).timestamp())


def walk(seed, start, vol, d, bar_min, start_hm=(9, 30), end_hm=(16, 0), volume=True):
    """A seeded random walk of `bar_min`-minute bars from `start_hm` to `end_hm` ET on `d`."""
    rnd = random.Random(seed)
    bars = []
    t = epoch(d, *start_hm)
    end = epoch(d, *end_hm)
    if end <= t:
        end += 86400
    p = start
    while t < end:
        o = p
        path = [o]
        for _ in range(bar_min):
            path.append(path[-1] + rnd.gauss(0, vol))
        h, low, c = max(path), min(path), path[-1]
        p = c
        v = rnd.randint(500, 5000) if volume else 0
        bars.append(
            {"t": t, "o": round(o, 2), "h": round(h, 2), "l": round(low, 2), "c": round(c, 2), "v": v}
        )
        t += bar_min * 60
    return bars


def compact(xs):
    return "[\n" + ",\n".join("    " + json.dumps(x) for x in xs) + "\n   ]"


def build_text() -> str:
    """The fixture file's whole text, one bar or row per line so a diff reads."""

    d0 = dt.date(2026, 11, 13)
    cases = [
        {
            "name": "hand-worked: four 30-minute periods, row 1",
            "row_size": 1,
            "bar_seconds": 1800,
            "session": "2026-11-13",
            "bars": [
                {"t": epoch(d0, 9, 30), "o": 101.0, "h": 103.5, "l": 100.2, "c": 102.0, "v": 400},
                {"t": epoch(d0, 10, 0), "o": 102.0, "h": 104.9, "l": 101.0, "c": 103.0, "v": 500},
                {"t": epoch(d0, 10, 30), "o": 102.8, "h": 102.9, "l": 102.5, "c": 102.6, "v": 100},
                {"t": epoch(d0, 11, 0), "o": 101.0, "h": 101.2, "l": 99.5, "c": 100.0, "v": 300},
            ],
        }
    ]

    d1, d2 = dt.date(2026, 11, 12), dt.date(2026, 11, 13)
    spx = walk(11, 6800, 1.1, d1, 15, volume=False) + walk(12, 6812, 1.3, d2, 15, volume=False)
    cases.append(
        {
            "name": "SPX-like, 15m, two sessions, no volume (newest by default)",
            "row_size": mp.default_row_size(6800),
            "bar_seconds": 900,
            "session": None,
            "bars": spx,
        }
    )
    cases.append(
        {
            "name": "SPX-like, 15m, the prior session named",
            "row_size": mp.default_row_size(6800),
            "bar_seconds": 900,
            "session": "2026-11-12",
            "bars": spx,
        }
    )
    cases.append(
        {
            "name": "NDX-like, 5m, no volume",
            "row_size": mp.default_row_size(25000),
            "bar_seconds": 300,
            "session": "2026-11-13",
            "bars": walk(21, 25000, 6.0, d2, 5, volume=False),
        }
    )
    cases.append(
        {
            "name": "QQQ-like, 5m, with volume",
            "row_size": mp.default_row_size(600),
            "bar_seconds": 300,
            "session": "2026-11-13",
            "bars": walk(31, 600, 0.14, d2, 5),
        }
    )
    nq = walk(41, 25010, 5.0, d1, 15, start_hm=(18, 0), end_hm=(9, 30))
    nq += walk(42, nq[-1]["c"], 6.0, d2, 15, start_hm=(9, 30), end_hm=(17, 0))
    cases.append(
        {
            "name": "/NQ-like, 15m, overnight and after-hours bars ignored",
            "row_size": mp.default_row_size(25000),
            "bar_seconds": 900,
            "session": None,
            "bars": nq,
        }
    )
    d3 = dt.date(2026, 7, 15)
    dev = [b for b in walk(51, 680, 0.15, d3, 1) if b["t"] < epoch(d3, 12, 0)]
    cases.append(
        {
            "name": "SPY-like, 1m, EDT, developing through E",
            "row_size": mp.default_row_size(680),
            "bar_seconds": 60,
            "session": None,
            "bars": dev,
        }
    )

    for c in cases:
        sess = dt.date.fromisoformat(c["session"]) if c["session"] else None
        p = mp.build_profile(c["bars"], row_size=c["row_size"], bar_seconds=c["bar_seconds"], session=sess)
        c["expected"] = p.as_dict()

    row_sizes = [[p, mp.default_row_size(p)] for p in (6800, 25000, 600, 680, 100, 50, 21000, 5.3)]
    symbol_row_sizes = [
        [s, p, mp.row_size_for(s, p)]
        for s, p in (
            ("SPX", 7449.0),
            ("/ES", 7460.0),
            ("NDX", 25000.0),
            ("/NQ", 27900.0),
            ("SPY", 680.0),
            ("QQQ", 676.0),
            ("IWM", 230.0),
        )
    ]

    # Composites: bars inline, or `bars_from` naming a case above (its bars).
    d_a, d_b = dt.date(2026, 11, 12), dt.date(2026, 11, 13)
    composites = [
        {
            "name": "hand-worked composite: two sessions, row 1",
            "row_size": 1,
            "bar_seconds": 1800,
            "sessions": ["2026-11-12", "2026-11-13", "2026-11-11"],
            "bars": [
                {"t": epoch(d_a, 9, 30), "o": 101.0, "h": 102.5, "l": 100.0, "c": 102.0, "v": 30},
                {"t": epoch(d_b, 9, 30), "o": 102.0, "h": 103.2, "l": 101.0, "c": 101.5, "v": 30},
                {"t": epoch(d_b, 10, 0), "o": 101.5, "h": 101.9, "l": 101.1, "c": 101.8, "v": 5},
            ],
        },
        {
            "name": "SPX-like, both sessions merged",
            "row_size": mp.default_row_size(6800),
            "bar_seconds": 900,
            "sessions": ["2026-11-12", "2026-11-13"],
            "bars_from": "SPX-like, 15m, two sessions, no volume (newest by default)",
        },
        {
            "name": "/NQ-like, one session (the composite of one is its profile)",
            "row_size": mp.default_row_size(25000),
            "bar_seconds": 900,
            "sessions": ["2026-11-13"],
            "bars_from": "/NQ-like, 15m, overnight and after-hours bars ignored",
        },
    ]
    by_name = {c["name"]: c for c in cases}
    for c in composites:
        bars = c["bars"] if "bars" in c else by_name[c["bars_from"]]["bars"]
        comp = mp.build_composite(
            bars,
            sessions=[dt.date.fromisoformat(s) for s in c["sessions"]],
            row_size=c["row_size"],
            bar_seconds=c["bar_seconds"],
        )
        c["expected"] = comp.as_dict()

    # Untested POCs: bars inline, or `bars_from` naming a case above.
    d10, d11, d12, d13 = (dt.date(2026, 11, d) for d in (10, 11, 12, 13))
    untested = [
        {
            "name": "hand-worked untested POCs: an overnight touch and an edge that misses",
            "row_size": 1,
            "bar_seconds": 1800,
            "sessions": ["2026-11-10", "2026-11-11", "2026-11-12"],
            "bars": [
                {"t": epoch(d10, 9, 30), "o": 101.0, "h": 102.8, "l": 100.2, "c": 101.5, "v": 0},  # POC 101
                {"t": epoch(d10, 10, 0), "o": 101.5, "h": 101.9, "l": 101.1, "c": 101.6, "v": 0},
                {"t": epoch(d11, 9, 30), "o": 106.0, "h": 107.9, "l": 105.1, "c": 106.2, "v": 0},  # POC 106
                {"t": epoch(d11, 10, 0), "o": 106.2, "h": 106.5, "l": 106.0, "c": 106.4, "v": 0},
                {
                    "t": epoch(d11, 16, 0),
                    "o": 105.9,
                    "h": 105.99,
                    "l": 105.5,
                    "c": 105.6,
                    "v": 0,
                },  # misses row 106
                {"t": epoch(d12, 9, 30), "o": 104.0, "h": 104.5, "l": 103.0, "c": 103.5, "v": 0},  # POC 103
                {"t": epoch(d12, 10, 0), "o": 103.5, "h": 103.8, "l": 103.2, "c": 103.4, "v": 0},
                {
                    "t": epoch(d12, 20, 0),
                    "o": 100.8,
                    "h": 100.9,
                    "l": 100.5,
                    "c": 100.6,
                    "v": 0,
                },  # overnight, misses
                {
                    "t": epoch(d12, 21, 0),
                    "o": 107.2,
                    "h": 107.5,
                    "l": 107.0,
                    "c": 107.1,
                    "v": 0,
                },  # low at row 106's top: misses
                {
                    "t": epoch(d13, 3, 0),
                    "o": 101.0,
                    "h": 101.0,
                    "l": 101.0,
                    "c": 101.0,
                    "v": 0,
                },  # touches 101
            ],
        },
        {
            "name": "SPX-like, two sessions",
            "row_size": mp.default_row_size(6800),
            "bar_seconds": 900,
            "sessions": ["2026-11-12", "2026-11-13"],
            "bars_from": "SPX-like, 15m, two sessions, no volume (newest by default)",
        },
        {
            "name": "/NQ-like, overnight bars count as later",
            "row_size": mp.default_row_size(25000),
            "bar_seconds": 900,
            "sessions": ["2026-11-12", "2026-11-13"],
            "bars_from": "/NQ-like, 15m, overnight and after-hours bars ignored",
        },
    ]
    for c in untested:
        bars = c["bars"] if "bars" in c else by_name[c["bars_from"]]["bars"]
        got = mp.untested_pocs(
            bars,
            sessions=[dt.date.fromisoformat(s) for s in c["sessions"]],
            row_size=c["row_size"],
            bar_seconds=c["bar_seconds"],
        )
        c["expected"] = [[d.isoformat(), poc] for d, poc in got]

    # Overnight profiles: bars inline, or `bars_from` naming a case above.
    overnight = [
        {
            "name": "hand-worked overnight: Thu 18:00 to Fri 09:30, a bar either side ignored",
            "row_size": 1,
            "bar_seconds": 1800,
            "session": "2026-11-13",
            "bars": [
                {"t": epoch(d12, 17, 30), "o": 92.0, "h": 95.0, "l": 90.0, "c": 93.0, "v": 9},  # before 18:00
                {"t": epoch(d12, 18, 0), "o": 101.0, "h": 102.5, "l": 100.2, "c": 102.0, "v": 30},
                {"t": epoch(d12, 18, 30), "o": 101.5, "h": 101.8, "l": 101.0, "c": 101.2, "v": 10},
                {"t": epoch(d13, 3, 0), "o": 102.0, "h": 103.1, "l": 101.5, "c": 103.0, "v": 30},
                {"t": epoch(d13, 9, 0), "o": 102.2, "h": 102.4, "l": 102.0, "c": 102.3, "v": 10},
                {"t": epoch(d13, 9, 30), "o": 110.0, "h": 111.0, "l": 110.0, "c": 110.5, "v": 9},  # the open
            ],
        },
        {
            "name": "/NQ-like, the overnight before 11-13",
            "row_size": mp.default_row_size(25000),
            "bar_seconds": 900,
            "session": "2026-11-13",
            "bars_from": "/NQ-like, 15m, overnight and after-hours bars ignored",
        },
    ]
    for c in overnight:
        bars = c["bars"] if "bars" in c else by_name[c["bars_from"]]["bars"]
        got = mp.overnight_profile(
            bars,
            session=dt.date.fromisoformat(c["session"]),
            row_size=c["row_size"],
            bar_seconds=c["bar_seconds"],
        )
        c["expected"] = got.as_dict()

    out = {
        "_note": (
            "Market Profile cases for core.marketprofile.build_profile, build_composite, untested_pocs, "
            "overnight_profile and default_row_size. "
            "Read by packages/core/tests/test_marketprofile.py and by the console's "
            "web/test/marketProfile.test.ts, "
            "which pins its TypeScript port (web/src/lib/marketProfile.ts) to the same answers. Change both "
            "implementations or neither. The first case and the first composite are also worked by hand in "
            "the "
            "Python test; the rest are seeded random walks whose expected profiles the module produced when "
            "this "
            "file was made, so they pin the two implementations to each other. A composite's `bars_from` "
            "names "
            "the case whose bars it reads."
        ),
        "row_sizes": row_sizes,
        "symbol_row_sizes": symbol_row_sizes,
        "cases": cases,
        "composites": composites,
        "untested": untested,
        "overnight": overnight,
    }
    parts = [
        '{\n "_note": ' + json.dumps(out["_note"]) + ",",
        ' "row_sizes": ' + json.dumps(row_sizes) + ",",
        ' "symbol_row_sizes": ' + json.dumps(symbol_row_sizes) + ",",
        ' "cases": [',
    ]
    case_txt = []
    for c in cases:
        e = dict(c["expected"])
        rows = e.pop("rows")
        exp = "{\n    " + ",\n    ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in e.items())
        exp += ',\n    "rows": ' + compact(rows) + "\n   }"
        head = ",\n   ".join(
            f"{json.dumps(k)}: {json.dumps(c[k])}" for k in ("name", "row_size", "bar_seconds", "session")
        )
        case_txt.append(
            "  {\n   " + head + ',\n   "bars": ' + compact(c["bars"]) + ',\n   "expected": ' + exp + "\n  }"
        )
    comp_txt = []
    for c in composites:
        e = dict(c["expected"])
        rows = e.pop("rows")
        exp = "{\n    " + ",\n    ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in e.items())
        exp += ',\n    "rows": ' + compact(rows) + "\n   }"
        keys = ["name", "row_size", "bar_seconds", "sessions"] + (["bars_from"] if "bars_from" in c else [])
        head = ",\n   ".join(f"{json.dumps(k)}: {json.dumps(c[k])}" for k in keys)
        if "bars" in c:
            head += ',\n   "bars": ' + compact(c["bars"])
        comp_txt.append("  {\n   " + head + ',\n   "expected": ' + exp + "\n  }")
    unt_txt = []
    for c in untested:
        keys = ["name", "row_size", "bar_seconds", "sessions"] + (["bars_from"] if "bars_from" in c else [])
        head = ",\n   ".join(f"{json.dumps(k)}: {json.dumps(c[k])}" for k in keys)
        if "bars" in c:
            head += ',\n   "bars": ' + compact(c["bars"])
        unt_txt.append("  {\n   " + head + ',\n   "expected": ' + json.dumps(c["expected"]) + "\n  }")
    on_txt = []
    for c in overnight:
        e = dict(c["expected"])
        rows = e.pop("rows")
        exp = "{\n    " + ",\n    ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in e.items())
        exp += ',\n    "rows": ' + compact(rows) + "\n   }"
        keys = ["name", "row_size", "bar_seconds", "session"] + (["bars_from"] if "bars_from" in c else [])
        head = ",\n   ".join(f"{json.dumps(k)}: {json.dumps(c[k])}" for k in keys)
        if "bars" in c:
            head += ',\n   "bars": ' + compact(c["bars"])
        on_txt.append("  {\n   " + head + ',\n   "expected": ' + exp + "\n  }")
    text = (
        "\n".join(parts)
        + "\n"
        + ",\n".join(case_txt)
        + '\n ],\n "composites": [\n'
        + ",\n".join(comp_txt)
        + '\n ],\n "untested": [\n'
        + ",\n".join(unt_txt)
        + '\n ],\n "overnight": [\n'
        + ",\n".join(on_txt)
        + "\n ]\n}\n"
    )
    assert json.loads(text) == json.loads(json.dumps(out))
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--out", type=Path, default=FIXTURE, help="where to write (default: the committed fixture)"
    )
    ap.add_argument("--check", action="store_true", help="exit 1 if the file differs from a fresh build")
    args = ap.parse_args(argv)
    text = build_text()
    if args.check:
        held = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        same = json.loads(held) == json.loads(text) if held else False
        print("fixture up to date" if same else f"fixture differs from a fresh build: {args.out}")
        return 0 if same else 1
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
