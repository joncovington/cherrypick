"""The earnings calendar's forward record: what each announcement did, against what the calendar said.

Step 5 of docs/earnings-screener-plan.md. History found no edge in the navigator's spread (step 1);
this is the record that would show if that changes, built from what the calendar showed at the time
and what happened after -- local data only:

- **what was shown:** the calendar built the session BEFORE the report (`earnings-calendar/<day>.json`,
  the newest with a session earlier than the report date): direction, time of day, the expected
  move (the broker's 0.85 x straddle; one SD beside it).
- **the move:** the reaction-day close against the previous session's close, from the local Dolt
  clone's daily bars; its size over the expected move, and whether it stayed inside it. A row whose
  time of day was unknown is measured on the later reaction day (after the close) and says so.
- **the direction:** for a strong read, whether the reaction went its way.
- **the spread:** priced in `fetch_screener_greeks.py measure`'s last reading on the entry session
  (`screener-greeks/earnings-legs/`), marked on the same options in the reaction day's and the next
  session's last readings (`earnings-marks/`). Money in the suite's layout: signed whole-position
  cash (a credit received +, a debit paid -) at mid, exit likewise, gross = entry + exit, costs from
  `cherrypick.core.fees` (commissions and pass-through fees, and the modelled slippage, which fills
  at mid do not concede) as separate columns, net = gross - fees - slippage. One spread each; early
  assignment and settlement are not modelled (the marks are days before expiry).

Rebuilt whole from its inputs every run (`earnings-calendar/record.json`); nothing is accumulated, so
a fixed input fixes the record.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees as _fees

from . import earnings_calendar, paths
from . import land as _land

MULT = 100
SPREAD_ORDER = {"order": {"legs": [{"quantity": 1}, {"quantity": 1}]}}
STRONG = {"sb": "up", "sbe": "down"}


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_reading(folder: Path, day: str) -> tuple[str, dict] | None:
    """The day's last reading in a `<date>/<HHMM>.json` folder: ('<date>/<HHMM>.json', its doc)."""
    files = sorted((folder / day).glob("*.json"))
    return (f"{day}/{files[-1].name}", _read(files[-1])) if files else None


def calendar_before(days: list[str], report: str) -> str | None:
    """The newest calendar session strictly before the report date."""
    earlier = [d for d in days if d < report]
    return earlier[-1] if earlier else None


def cash(kind: str, value: float, opening: bool) -> float:
    """Signed whole-position cash for one spread at a per-share value: opening a credit spread
    receives it (+), opening a debit pays it (-); closing reverses."""
    sign = 1 if (kind == "credit") == opening else -1
    return round(sign * value * MULT, 2)


def leg_quotes(legs: list[dict]) -> list[dict] | None:
    out = []
    for leg in legs:
        q = leg.get("quote") or {}
        if q.get("bid") is None or q.get("ask") is None:
            return None
        out.append({"bid": q["bid"], "ask": q["ask"]})
    return out


def trade_result(entry: dict, mark: dict | None) -> dict | None:
    """One spread's money from its entry row and one mark row (each with legs, quotes and `mid`), or
    None when either is unpriced."""
    if mark is None or entry.get("mid") is None or mark.get("mid") is None:
        return None
    eq, mq = leg_quotes(entry["legs"]), leg_quotes(mark["legs"])
    if eq is None or mq is None:
        return None
    opened = _fees.apply_entry_costs(SPREAD_ORDER, eq, 1, {})
    closed = _fees.apply_exit_costs(SPREAD_ORDER, mq, 1, {})
    entry_cash = cash(entry["kind"], entry["mid"], opening=True)
    exit_cash = cash(entry["kind"], mark["mid"], opening=False)
    gross = round(entry_cash + exit_cash, 2)
    fees = round(
        opened["commission"]
        + opened["pass_through_fees"]
        + closed["commission"]
        + closed["pass_through_fees"],
        2,
    )
    slippage = round(opened["slippage"] + closed["slippage"], 2)
    # What one spread could lose: the debit, or the width less the credit, plus what opening it cost.
    max_loss = entry["mid"] * MULT if entry["kind"] == "debit" else (entry["width"] - entry["mid"]) * MULT
    return {
        "entry": entry_cash,
        "exit": exit_cash,
        "gross": gross,
        "fees": fees,
        "slippage": slippage,
        "net": round(gross - fees - slippage, 2),
        "risk": round(max_loss + opened["total_cost"], 2),
    }


def closes(cfg: dict, wanted: dict[str, set[str]]) -> dict[tuple[str, str], float]:
    """{(symbol, date): close} from the local Dolt clone's daily bars, one query a date."""
    if not wanted:
        return {}
    cfg = {**_land.DEFAULTS, **(cfg or {})}
    conn = _land._connect(cfg, cfg["stocks_db"])
    out = {}
    try:
        cur = conn.cursor()
        for d, syms in sorted(wanted.items()):
            marks = ",".join(["%s"] * len(syms))
            cur.execute(
                f"SELECT act_symbol, close FROM ohlcv WHERE date = %s AND act_symbol IN ({marks})",
                [d, *sorted(syms)],
            )
            for s, c in cur.fetchall():
                if c is not None:
                    out[(s, d)] = float(c)
    finally:
        conn.close()
    return out


def build(today: date, cfg: dict | None = None, closes_fn=None) -> dict:
    """The record as of `today`: every announcement a calendar showed whose reaction day is past.
    `closes_fn` stands in for the Dolt read (tests)."""
    cal_dir = earnings_calendar.out_dir()
    days = sorted(p.stem for p in cal_dir.glob("????-??-??.json"))
    greeks = paths.market_report_dir() / "screener-greeks"

    # Every announcement once, as the calendar the session before it showed it.
    shown: dict[tuple[str, str], tuple[str, dict]] = {}
    for d in days:
        for r in _read(cal_dir / f"{d}.json").get("rows") or []:
            key = (r["symbol"], r["date"])
            if r["reaction_day"] < today.isoformat() and calendar_before(days, r["date"]) == d:
                shown[key] = (d, r)

    wanted: dict[str, set[str]] = {}
    for (sym, _), (_, r) in shown.items():
        react = date.fromisoformat(r["reaction_day"])
        for d in (react, cal.previous_trading_day(react)):
            wanted.setdefault(d.isoformat(), set()).add(sym)
    px = (closes_fn or (lambda w: closes(cfg or {}, w)))(wanted)

    rows = []
    for (sym, report), (cal_day, r) in sorted(shown.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        react = date.fromisoformat(r["reaction_day"])
        before = cal.previous_trading_day(react).isoformat()
        c0, c1 = px.get((sym, before)), px.get((sym, react.isoformat()))
        move = round(c1 - c0, 4) if c0 is not None and c1 is not None else None
        # Judged against the expected move the calendar showed: the broker's earnings rule, 0.85 x
        # the ATM straddle (`em_move`). One SD is kept beside it.
        em = r.get("em_move")
        out = {
            "symbol": sym,
            "company": r.get("company"),
            "date": report,
            "calendar_day": cal_day,
            "timing": r.get("timing"),
            "timing_known": r.get("timing") is not None,
            "reaction_day": r["reaction_day"],
            "edge": r.get("edge"),
            "edge_label": r.get("edge_label"),
            "liquidity_rank": r.get("liquidity_rank"),
            "region": r.get("region"),
            "close_before": c0,
            "close_after": c1,
            "move": move,
            "move_pct": round(100 * move / c0, 2) if move is not None and c0 else None,
            "em_move": em,
            "em_move_pct": r.get("em_move_pct"),
            "sd_move": r.get("sd_move"),
            "sd_move_pct": r.get("sd_move_pct"),
            "move_over_em": round(abs(move) / em, 3) if move is not None and em else None,
            "inside_em": (abs(move) <= em) if move is not None and em else None,
            "direction_right": None,
            "spread": None,
        }
        if move is not None and r.get("edge") in STRONG and move != 0:
            out["direction_right"] = (move > 0) == (STRONG[r["edge"]] == "up")
        out["spread"] = _spread(greeks, sym, report, r)
        rows.append(out)
    return {
        "built_at": datetime.now(UTC).isoformat(),
        "as_of": today.isoformat(),
        "calendars": [days[0], days[-1]] if days else None,
        "summary": summary(rows),
        "rows": rows,
    }


def _spread(greeks: Path, sym: str, report: str, r: dict) -> dict | None:
    """The suggested spread's record: entered on the entry session's last reading, marked on the
    reaction day's and the next session's last readings."""
    es = r.get("entry_session")
    if not es or not r.get("suggestion"):
        return None
    got = last_reading(greeks / "earnings-legs", es)
    if got is None:
        return {"state": "not read on the entry session"}
    entry_file, doc = got
    entry = next(
        (x for x in doc.get("rows") or [] if x.get("symbol") == sym and x.get("date") == report), None
    )
    if entry is None or not entry.get("legs"):
        return {"state": "not listed on the entry session"}
    react = date.fromisoformat(r["reaction_day"])
    out = {
        "state": "entered",
        "kind": entry["kind"],
        "right": entry["right"],
        "expiry": entry["expiry"],
        "strikes": [leg["strike"] for leg in entry["legs"]],
        "entry_reading": entry_file,
        "entry_mid": entry.get("mid"),
        "liquid_at_entry": entry.get("passes"),
    }
    for which, day in (("react", react), ("day2", cal.next_trading_day(react))):
        got = last_reading(greeks / "earnings-marks", day.isoformat())
        mark = None
        if got is not None:
            mark = next(
                (
                    m
                    for m in got[1].get("rows") or []
                    if m.get("symbol") == sym
                    and m.get("which") == which
                    and m.get("entry_reading") == entry_file
                ),
                None,
            )
        out[which] = trade_result(entry, mark)
    return out


def summary(rows: list[dict]) -> dict:
    moved = [r for r in rows if r["move_over_em"] is not None]
    strong = [r for r in rows if r["direction_right"] is not None]
    out = {
        "announcements": len(rows),
        "measured": len(moved),
        "inside_em": round(sum(1 for r in moved if r["inside_em"]) / len(moved), 3) if moved else None,
        "median_move_over_em": sorted(r["move_over_em"] for r in moved)[len(moved) // 2] if moved else None,
        "strong_reads": len(strong),
        "direction_right": round(sum(1 for r in strong if r["direction_right"]) / len(strong), 3)
        if strong
        else None,
    }
    for which in ("react", "day2"):
        done = [r["spread"][which] for r in rows if r["spread"] and r["spread"].get(which)]
        out[f"spreads_{which}"] = {
            "n": len(done),
            "net": round(sum(t["net"] for t in done), 2),
            "won": round(sum(1 for t in done if t["net"] > 0) / len(done), 3) if done else None,
            "return_on_risk": round(sum(t["net"] for t in done) / sum(t["risk"] for t in done), 4)
            if done and sum(t["risk"] for t in done)
            else None,
        }
    return out


def out_path() -> Path:
    return earnings_calendar.out_dir() / "record.json"


def write(result: dict) -> Path:
    path = out_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(result, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path
