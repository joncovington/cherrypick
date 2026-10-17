"""The earnings calendar: every announcement in the next 30 days, in the vendor's calendar layout.

Step 2 of docs/earnings-screener-plan.md. One row per announcement, joined from data already held
locally -- never the broker, so this stays credential-free:

- **when:** the local Dolt calendar (`earnings.earnings_calendar`, date and before/after the open)
  and the broker's expected report date with its confirmed/estimated flag (`iv/tastytrade.json`,
  written by `scripts/fetch_iv_rank.py`). A confirmed broker date wins; otherwise Dolt's; otherwise
  an estimated broker date. Two dates more than a day apart are flagged. The time of day comes only
  from Dolt, and only when Dolt's date is the one shown: a blank is `null`, never assumed.
- **who:** the security list the collector saves nightly (`vendor-securities/<day>.json`) for the
  direction read, strength (1-10), IV rank, liquidity rank, optionable and type -- the vendor's own
  values, which describe the PREVIOUS session's close (plan, gap 4); Dolt's `stocks.symbol` for the
  company name; the market report's sector map where it has the name.
- **price:** Dolt's latest daily close (`stocks.ohlcv`), which covers every listed name, where the
  technicals store holds only ~1,000.
- **expected move:** one standard deviation to the first expiration on or after the reaction day,
  price x that expiration's implied vol x the root of its calendar days over 365 -- the broker's
  per-expiration IV from `iv/expiry-iv/<day>.json`. It matched the vendor's figures to about a third
  of a point (plan). The suite's own expected move, 0.85 x the ATM straddle, is about 0.68 of that
  (`EM_TO_SD`) and is what the suggested credit spread's long leg is placed by.
- **the suggested spread,** for strong reads only: the earnings-navigator rule. IV rank under the cut
  buys a debit spread in the direction (long at the money, short at the suite's expected move);
  otherwise sells a credit spread on the other side (short at the money, long just outside the
  suite's expected move -- the user's legs). Targets only: `fetch_screener_greeks.py` snaps them to
  listed strikes and prices them. History found no edge in it (plan, step 1), and every surface that
  shows it says so.

Written to `data/technicals/earnings-calendar/<day>.json`, one file a session; a rerun the same day
replaces that day's file, so each kept file is what the calendar said that evening.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal

from . import compass, paths
from . import land as _land

WINDOW_DAYS = 30
DEFAULTS = {"iv_rank_cut": 50, "trade_expiry": "cycle", "window_days": WINDOW_DAYS}
# An ATM straddle is worth about sqrt(2/pi) ~ 0.798 of one standard deviation, and the suite's
# expected move is 0.85 of the straddle: 0.85 x 0.798 ~ 0.678 SD.
EM_TO_SD = 0.85 * math.sqrt(2 / math.pi)
STRONG = {"sb": "bull", "sbe": "bear"}
BMO, AMC = "Before market open", "After market close"
CYCLE_PREFERRED, CYCLE_ALLOWED = (45, 60), (30, 60)
NAME_TAIL = re.compile(
    r"\s+(-\s.*|Common Stock.*|Ordinary Shares.*|American Depositary Shares.*"
    r"|Class [A-Z] .*|Common Shares.*)$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- pure pieces


def company(security_name: str | None) -> str | None:
    """Dolt's security name with the share-class boilerplate cut: 'Netflix, Inc. - Common Stock' ->
    'Netflix, Inc.'; 'Chubb Limited  Common Stock' -> 'Chubb Limited'."""
    if not security_name:
        return None
    return NAME_TAIL.sub("", " ".join(security_name.split())).strip() or None


def pick_date(dolt: tuple[date, str | None] | None, broker: tuple[date, bool | None] | None):
    """(date, time of day, source, confirmed, disagreement in days) for one name, or None.

    A confirmed broker date wins, then Dolt's, then an estimated broker date. The time of day is
    Dolt's, kept when Dolt's date is within a day of the one shown: Dolt can sit a day off a
    confirmed date with the right time (PM, 2026-10-21 before the open, which Dolt had on the 20th),
    and the disagreement is flagged on the row either way. Further apart, Dolt is describing another
    announcement (META: Dolt's Nov 4 against a confirmed Oct 21), and the time is unknown."""
    if dolt is None and broker is None:
        return None
    gap = abs((dolt[0] - broker[0]).days) if dolt and broker else None
    if broker and broker[1] is False:
        d, src, confirmed = broker[0], "broker", True
    elif dolt:
        d, src, confirmed = dolt[0], "dolt", None
    else:
        d, src, confirmed = broker[0], "broker", False
    when = dolt[1] if dolt and abs((dolt[0] - d).days) <= 1 else None
    timing = "bmo" if when == BMO else "amc" if when == AMC else None
    return d, timing, src, confirmed, gap


def reaction_day(d: date, timing: str | None) -> date:
    """The first session that trades on the news: the report day for before the open, else the next
    session. Unknown timing takes the later one, so the event expiry is never one that expires before
    the market could react."""
    if timing == "bmo" and cal.is_trading_day(d):
        return d
    return cal.next_trading_day(d)


def entry_session(d: date, timing: str | None) -> date | None:
    """The last close before the news: the report day for after the close, the session before for
    before the open; None when the time of day is unknown."""
    if timing == "amc":
        return d if cal.is_trading_day(d) else cal.previous_trading_day(d)
    if timing == "bmo":
        return cal.previous_trading_day(d)
    return None


def event_expiry(ivs: dict[str, float], react: date) -> tuple[str, float] | None:
    """The first listed expiration on or after the reaction day, and its implied vol."""
    after = sorted(e for e in ivs if date.fromisoformat(e) >= react)
    return (after[0], ivs[after[0]]) if after else None


def sd_move(price: float, iv: float, session: date, expiry: str) -> float:
    """One standard deviation to `expiry` from the `session` close, in dollars."""
    days = (date.fromisoformat(expiry) - session).days
    return price * iv * math.sqrt(max(days, 0) / 365.0)


def is_monthly(expiry: str) -> bool:
    """The month's standard expiration: the third Friday, or the session before it when that Friday
    is a market holiday."""
    d = date.fromisoformat(expiry)
    third = cal.nth_weekday(d.year, d.month, cal.FRI, 3)
    return d == (third if cal.is_trading_day(third) else cal.previous_trading_day(third))


def trade_expiry(ivs: dict[str, float], session: date, rule: str, event: str | None) -> str | None:
    """The suggested spread's expiration: `event` the event expiry; `cycle` the user's standard
    monthly, latest 45-60 days out, else 30-60, and never before the event expiry."""
    if rule == "event":
        return event
    monthly = [e for e in ivs if is_monthly(e) and (event is None or e >= event)]
    for lo, hi in (CYCLE_PREFERRED, CYCLE_ALLOWED):
        inside = [e for e in monthly if lo <= (date.fromisoformat(e) - session).days <= hi]
        if inside:
            return max(inside)
    return None


def suggestion(edge: str | None, iv_rank: float | None, price: float | None, em: float | None, cut: float):
    """The navigator's spread for a strong read, as target strikes (unsnapped), or None.

    `em` is the suite's expected move in dollars (0.85 x straddle): the debit's short leg and the
    credit's long leg sit there, which is what step 1 priced."""
    side = STRONG.get(edge or "")
    if side is None or iv_rank is None or price is None or em is None:
        return None
    s = 1 if side == "bull" else -1
    if iv_rank < cut:
        right = "C" if side == "bull" else "P"
        return {"kind": "debit", "right": right, "long": price, "short": price + s * em}
    right = "P" if side == "bull" else "C"
    return {"kind": "credit", "right": right, "short": price, "long": price - s * em}


# --------------------------------------------------------------------------- inputs


def _latest_file(folder: Path, day: str) -> Path | None:
    files = [p for p in sorted(folder.glob("????-??-??.json")) if p.stem <= day]
    return files[-1] if files else None


def _read(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def broker_dates(day: str) -> tuple[str | None, dict[str, tuple[date, bool | None]]]:
    """The latest IV-rank reading on or before `day`: (its session, {symbol: (date, estimated)})."""
    days = _read(paths.tastytrade_iv_rank()).get("days") or {}
    at = max((d for d in days if d <= day), default=None)
    out = {}
    for sym, row in (days.get(at) or {}).items():
        d = (row or {}).get("earnings_date")
        if d:
            out[sym] = (date.fromisoformat(d), row.get("earnings_estimated"))
    return at, out


def dolt_inputs(cfg: dict, start: date, end: date) -> dict:
    """The calendar, names and latest closes from the local Dolt clone, one connection each."""
    cfg = {**_land.DEFAULTS, "earnings_db": "earnings", **(cfg or {})}
    out: dict = {"events": {}, "names": {}, "closes": {}, "close_date": None}
    conn = _land._connect(cfg, cfg["earnings_db"])
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT act_symbol, date, `when` FROM earnings_calendar WHERE date BETWEEN %s AND %s",
            (start, end),
        )
        for sym, d, when in cur.fetchall():
            prev = out["events"].get(sym)
            if prev is None or d < prev[0]:  # the nearest announcement, if Dolt lists two
                out["events"][sym] = (d, when)
    finally:
        conn.close()
    syms = sorted(out["events"])
    if not syms:
        return out
    conn = _land._connect(cfg, cfg["stocks_db"])
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(syms))
        cur.execute(f"SELECT act_symbol, security_name FROM symbol WHERE act_symbol IN ({marks})", syms)
        out["names"] = {s: company(n) for s, n in cur.fetchall()}
        cur.execute(
            "SELECT MAX(date) FROM ohlcv WHERE date <= %s AND date >= %s", (start, start - timedelta(days=10))
        )
        last = cur.fetchone()[0]
        if last is not None:
            out["close_date"] = last.isoformat()
            cur.execute(
                f"SELECT act_symbol, close FROM ohlcv WHERE date = %s AND act_symbol IN ({marks})",
                [last, *syms],
            )
            out["closes"] = {s: float(c) for s, c in cur.fetchall() if c is not None}
    finally:
        conn.close()
    return out


# --------------------------------------------------------------------------- the calendar


def build(day: str, cfg: dict | None = None, settings: dict | None = None, dolt: dict | None = None) -> dict:
    """The calendar for the session `day` (YYYY-MM-DD). `dolt` stands in for the Dolt read (tests)."""
    s = {**DEFAULTS, **(settings or {})}
    session = date.fromisoformat(day)
    end = session + timedelta(days=int(s["window_days"]))
    root = paths.market_report_dir()
    sec_path = _latest_file(root / "vendor-securities", day)
    sec_doc = _read(sec_path)
    sec = {r["symbol"]: r for r in sec_doc.get("rows") or [] if r.get("symbol")}
    iv_path = _latest_file(paths.expiry_iv_dir(), day)
    iv_doc = _read(iv_path)
    expiry_ivs = iv_doc.get("names") or {}
    broker_at, broker = broker_dates(day)
    sectors = _sector_map()
    dolt = dolt if dolt is not None else dolt_inputs(cfg or {}, session, end)

    rows = []
    for sym in sorted(set(dolt["events"]) | {k for k, v in broker.items() if session <= v[0] <= end}):
        de = dolt["events"].get(sym)
        be = broker.get(sym)
        be = be if be and session <= be[0] <= end else None
        picked = pick_date(de, be)
        if picked is None:
            continue
        d, timing, src, confirmed, gap = picked
        if not session <= d <= end:
            continue
        r = sec.get(sym) or {}
        price = dolt["closes"].get(sym)
        react = reaction_day(d, timing)
        ev = event_expiry(expiry_ivs.get(sym) or {}, react)
        sd = sd_move(price, ev[1], session, ev[0]) if (ev and price) else None
        em = sd * EM_TO_SD if sd is not None else None
        sug = suggestion(r.get("directionalEdge"), r.get("ivRank"), price, em, float(s["iv_rank_cut"]))
        if sug is not None:
            sug["expiry"] = trade_expiry(
                expiry_ivs.get(sym) or {}, session, s["trade_expiry"], ev[0] if ev else None
            )
            sug = {k: round(v, 4) if isinstance(v, float) else v for k, v in sug.items()}
        rows.append(
            {
                "symbol": sym,
                "company": dolt["names"].get(sym),
                "date": d.isoformat(),
                "timing": timing,
                "date_source": src,
                "date_confirmed": confirmed,
                "date_gap_days": gap,
                "entry_session": (es.isoformat() if (es := entry_session(d, timing)) else None),
                "reaction_day": react.isoformat(),
                "type": r.get("type"),
                # The vendor's region filter (US / CA) leaves depository receipts out (SAP).
                "region": "intl" if r.get("type") == "Depository Receipt" else ("US" if r else None),
                "edge": r.get("directionalEdge"),
                "edge_label": compass.LABELS.get(r.get("directionalEdge") or ""),
                "strength": r.get("technicalRank"),
                "iv_rank": r.get("ivRank"),
                "liquidity_rank": r.get("liquidityRank"),
                "optionable": r.get("hasOptions"),
                "sector": sectors.get(sym),
                "price": price,
                "event_expiry": ev[0] if ev else None,
                "event_iv": ev[1] if ev else None,
                "sd_move": round(sd, 4) if sd is not None else None,
                "sd_move_pct": round(100 * sd / price, 2) if (sd is not None and price) else None,
                "em_move": round(em, 4) if em is not None else None,
                "suggestion": sug,
            }
        )
    rows.sort(key=lambda x: (x["date"], x["symbol"]))
    return {
        "session": day,
        "window_days": int(s["window_days"]),
        "settings": s,
        "inputs": {
            "security_list": sec_path.stem if sec_path else None,
            "expiry_iv": iv_path.stem if iv_path else None,
            "broker_dates": broker_at,
            "close_date": dolt.get("close_date"),
        },
        "rows": rows,
    }


def _sector_map() -> dict[str, str]:
    from . import fundamentals

    return fundamentals.sector_map()


def out_dir() -> Path:
    return paths.data_dir() / "earnings-calendar"


def write(result: dict) -> Path:
    folder = out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['session']}.json"
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(result, indent=1), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: the console never reads half a file
    return path


def summary(result: dict) -> dict:
    rows = result["rows"]
    strong = [r for r in rows if r["edge"] in STRONG]
    return {
        "session": result["session"],
        "rows": len(rows),
        "with_move": sum(1 for r in rows if r["sd_move"] is not None),
        "timing_unknown": sum(1 for r in rows if r["timing"] is None),
        "strong": len(strong),
        "suggested": sum(1 for r in rows if r["suggestion"]),
        "inputs": result["inputs"],
    }
