"""The skate screen's forward record (docs/skate-plan.md, step 4).

The 2026-10-18 study could only estimate, from overlapping history, whether history-minus-implied picks
strikes that skate more often than the market prices. This keeps what the screen actually showed and
reads what happened, so that question gets an answer that was declared before the data came in.

- **Record.** Each session's newest scheduled quote run (`screener-greeks/skate/<session>/<HHMM>.json`,
  never a `*.trial.json`) gives, per name-side and band, the band's pick on its best expiry -- the row
  the Skate tab shows, and only when it passed the hard filters. Each is kept once, the first session
  it was shown: the same strike seen again next day is the same bet, and counting it twice would
  shrink the noise the test is judged against.
- **Settle.** Once the history store has the expiry's bar, the outcome is read from split-adjusted
  closes (skate's own reading, `skate.price_bars`): `skated` (the close at expiry on the safe side of
  the strike, as the skate rate counts it), `tested` (skated, but the price reached the strike on
  some session between), or `through` (finished past the strike).
- **Verdict.** The declared test (plan, step 4): rows with history minus implied above 5 points must
  skate more often than their implied rate AND more often than rows below zero, each by more than the
  binomial noise at that count (one-sided z > 1.645), judged once 20 distinct expiries have settled.
  Until then the reading says it is not yet decision-grade, and the tab says the gap is a history
  estimate.

Network-free and credential-free: local files and the history store only.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from . import paths, skate, store

SCHEMA = """
CREATE TABLE IF NOT EXISTS shown (
    symbol TEXT NOT NULL, side TEXT NOT NULL, band TEXT NOT NULL,
    expiry TEXT NOT NULL, strike REAL NOT NULL,
    session TEXT NOT NULL, run TEXT NOT NULL,
    spot REAL, delta REAL, implied REAL, history REAL, gap REAL, soft_all INTEGER,
    per_day_bid REAL,
    outcome TEXT, settle_close REAL, settled_on TEXT,
    PRIMARY KEY (symbol, side, band, expiry, strike));
CREATE TABLE IF NOT EXISTS recorded (session TEXT PRIMARY KEY, run TEXT NOT NULL, rows INTEGER NOT NULL);
"""

FILE_BAND = {"fixed": "fixed", "by_expiry": "by_expiry"}
RUN = re.compile(r"^(\d{4})\.json$")
# The declared test (docs/skate-plan.md, step 4).
GAP_HIGH = 0.05
GAP_LOW = 0.0
MIN_EXPIRIES = 20
Z_ONE_SIDED = 1.645
# The first session recorded. Runs before it scored strikes against the band day's close rather than
# the run's price (fixed 2026-10-19, #435), so some of their picks were already in the money; they
# would seed the test with rows the screen no longer shows.
RECORD_FROM = "2026-10-20"


def record_db() -> Path:
    return skate.skate_dir() / "record.db"


def quotes_dir() -> Path:
    return paths.market_report_dir() / "screener-greeks" / "skate"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or record_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------------------------------------
# Pure.


def newest_run(session_dir: Path) -> str | None:
    """The session's last scheduled run (`1505`), never a trial."""
    runs = sorted(m.group(1) for p in session_dir.glob("*.json") if (m := RUN.match(p.name)))
    return runs[-1] if runs else None


def shown_rows(doc: dict) -> list[dict]:
    """The rows the Skate tab shows: per name-side and band, the band's pick on its best expiry, when
    that leg passed the hard filters. The best expiry and every figure are the run file's."""
    out = []
    for row in doc.get("rows") or []:
        for band, key in FILE_BAND.items():
            best = ((row.get("best") or {}).get(key)) or None
            if not best:
                continue
            leg = next(
                (
                    g
                    for g in row.get("legs") or []
                    if g.get("band") == key
                    and g.get("role") == "pick"
                    and g.get("expiry") == best["expiry"]
                    and g.get("strike") == best["strike"]
                ),
                None,
            )
            if leg is None or leg.get("hard") is not True:
                continue
            out.append(
                {
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "band": band,
                    "expiry": best["expiry"],
                    "strike": float(best["strike"]),
                    "spot": row.get("spot"),
                    "delta": leg.get("delta"),
                    "implied": leg.get("implied_skate"),
                    "history": leg.get("skate"),
                    "gap": leg.get("gap"),
                    "soft_all": bool((leg.get("soft") or {}).get("all")),
                    "per_day_bid": leg.get("per_day_bid"),
                }
            )
    return out


def outcome(side: str, strike: float, path: list[tuple[str, float, float, float]]) -> str:
    """`skated`, `tested` or `through` from the bars after the session shown through the expiry:
    (date, high, low, close), the last being the expiry's. Skated as the skate rate counts it: a call's
    close below the strike, a put's above. Tested: skated, but some bar (the expiry's included)
    reached the strike."""
    last = path[-1]
    if (last[3] >= strike) if side == "C" else (last[3] <= strike):
        return "through"
    touched = any((h >= strike) if side == "C" else (lo <= strike) for _, h, lo, _ in path)
    return "tested" if touched else "skated"


def _z_vs_implied(rows: list[dict]) -> dict[str, Any]:
    """Skates against the sum of each row's own implied odds (a Poisson-binomial, normal approx)."""
    n = len(rows)
    k = sum(1 for r in rows if r["outcome"] in ("skated", "tested"))
    expected = sum(r["implied"] for r in rows)
    var = sum(r["implied"] * (1 - r["implied"]) for r in rows)
    return {
        "n": n,
        "skated": k,
        "rate": round(k / n, 4) if n else None,
        "implied": round(expected / n, 4) if n else None,
        "z": round((k - expected) / math.sqrt(var), 2) if var > 0 else None,
    }


def _z_two(a: dict, b: dict) -> float | None:
    """Two-proportion z of a's skate rate over b's."""
    if not a["n"] or not b["n"]:
        return None
    p = (a["skated"] + b["skated"]) / (a["n"] + b["n"])
    se = math.sqrt(p * (1 - p) * (1 / a["n"] + 1 / b["n"]))
    return round((a["rate"] - b["rate"]) / se, 2) if se > 0 else None


def verdict(rows: list[dict]) -> dict[str, Any]:
    """The declared test over settled rows, per band."""
    settled = [
        r for r in rows if r.get("outcome") and r.get("implied") is not None and r.get("gap") is not None
    ]
    expiries = sorted({r["expiry"] for r in settled})
    out: dict[str, Any] = {
        "settled": len(settled),
        "expiries": len(expiries),
        "decision_grade": len(expiries) >= MIN_EXPIRIES,
        "bands": {},
    }
    for band in FILE_BAND:
        mine = [r for r in settled if r["band"] == band]
        high = _z_vs_implied([r for r in mine if r["gap"] > GAP_HIGH])
        low = _z_vs_implied([r for r in mine if r["gap"] < GAP_LOW])
        vs_low = _z_two(high, low)
        passes = (
            out["decision_grade"]
            and high["z"] is not None
            and high["z"] > Z_ONE_SIDED
            and vs_low is not None
            and vs_low > Z_ONE_SIDED
        )
        out["bands"][band] = {
            "high_gap": high,
            "below_zero": low,
            "z_high_over_low": vs_low,
            "passes": passes if out["decision_grade"] else None,
            "tested": sum(1 for r in mine if r["outcome"] == "tested"),
        }
    return out


# ------------------------------------------------------------------------------------------------
# The nightly step: record what was shown, settle what has expired.


def record(conn: sqlite3.Connection, root: Path | None = None, through: str | None = None) -> dict:
    """Record every session's newest scheduled run not yet recorded, up to `through` inclusive."""
    root = root or quotes_dir()
    added: dict[str, int] = {}
    done = {r["session"] for r in conn.execute("SELECT session FROM recorded")}
    for session_dir in sorted(p for p in root.glob("????-??-??") if p.is_dir()):
        session = session_dir.name
        if session in done or session < RECORD_FROM or (through and session > through):
            continue
        run = newest_run(session_dir)
        if run is None:
            continue
        doc = json.loads((session_dir / f"{run}.json").read_text(encoding="utf-8"))
        rows = shown_rows(doc)
        n = 0
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO shown (symbol, side, band, expiry, strike, session, run, spot, delta,"
                " implied, history, gap, soft_all, per_day_bid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["symbol"],
                    r["side"],
                    r["band"],
                    r["expiry"],
                    r["strike"],
                    session,
                    run,
                    r["spot"],
                    r["delta"],
                    r["implied"],
                    r["history"],
                    r["gap"],
                    int(r["soft_all"]),
                    r["per_day_bid"],
                ),
            )
            n += cur.rowcount
        conn.execute("INSERT INTO recorded VALUES (?, ?, ?)", (session, run, n))
        added[session] = n
    conn.commit()
    return added


def settle(conn: sqlite3.Connection, history: sqlite3.Connection) -> dict:
    """Read the outcome of every recorded row whose expiry the history store now holds."""
    pending = conn.execute("SELECT rowid, * FROM shown WHERE outcome IS NULL").fetchall()
    bars_of: dict[str, list] = {}
    counts = {"skated": 0, "tested": 0, "through": 0, "waiting": 0}
    for r in pending:
        if r["symbol"] not in bars_of:
            store_sym, _ = skate._spellings(r["symbol"])
            bars_of[r["symbol"]] = skate.price_bars(history, store_sym)
        bars = bars_of[r["symbol"]]
        path = [(b.date, b.high, b.low, b.close) for b in bars if r["session"] < b.date <= r["expiry"]]
        if not path or path[-1][0] != r["expiry"]:
            counts["waiting"] += 1
            continue
        result = outcome(r["side"], r["strike"], path)
        conn.execute(
            "UPDATE shown SET outcome = ?, settle_close = ?, settled_on = ? WHERE rowid = ?",
            (result, path[-1][3], date.today().isoformat(), r["rowid"]),
        )
        counts[result] += 1
    conn.commit()
    return counts


def run(
    db: Path | None = None,
    history_db: Path | None = None,
    root: Path | None = None,
    through: str | None = None,
) -> dict:
    """Record, settle and read the verdict, writing it beside the bands as `record.json`."""
    conn = connect(db)
    hist = store.connect(history_db or paths.history_db())
    try:
        added = record(conn, root, through)
        settled = settle(conn, hist)
        rows = [dict(r) for r in conn.execute("SELECT * FROM shown")]
    finally:
        conn.close()
        hist.close()
    reading = {
        "recorded": len(rows),
        "added": added,
        "settled_now": settled,
        "verdict": verdict(rows),
        "test": {
            "gap_high": GAP_HIGH,
            "gap_low": GAP_LOW,
            "min_expiries": MIN_EXPIRIES,
            "z_one_sided": Z_ONE_SIDED,
        },
    }
    out = (db or record_db()).with_name("record.json")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(reading, indent=1), encoding="utf-8")
    tmp.replace(out)
    return {"ok": True, **reading}
