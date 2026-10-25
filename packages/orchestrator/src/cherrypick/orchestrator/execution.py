"""`cherrypick execution` — the execution record of each live arm, read weekly beside its P&L.

`docs/trading-book-review-plan.md` B10. The loops follow their rules by construction, so the process
breaks in two other places: the fill, and a person stepping in. This reads both from what the live
ledgers already record -- files only, no broker, nothing written.

- **Orders:** every live entry against the price its own model expected (the mid at submit), in
  dollars and as a share of the structure's width, whether it filled or missed, and how long it took.
  A credit filled above the mid is positive (better than the model).
- **Overrides,** flagged per week: a kill-criteria block cleared by hand (`state/kill/history.jsonl`),
  a module config committed during a session (the config-backup history, so at its cadence), and a
  manual close. Desk orders keep no ledger, so they cannot be read here.
- **The paper-live gap:** live net against the paper arm that mirrors it (meic `live-shadow`, bwb its
  `live.arm`) since the first live order, split into missed entries, fill, costs and the rest, with
  the largest named.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import home as core_home
from cherrypick.core import killcriteria as _kill
from cherrypick.core import ledgers

from . import config as cfgmod
from .util import CREATE_NO_WINDOW

ET = ZoneInfo("America/New_York")
MODULES = ("meic", "bwb")
SCHEMA = {"meic": "meic_ic", "bwb": "bwb_132"}
MEIC_MIRROR = "live-shadow"


# --------------------------------------------------------------------------- parsing
def parse_ts(value) -> datetime | None:
    """The ledgers' timestamps come in several spellings ('2026-06-29 10:54:03 ET', ISO with or
    without an offset). Naive or 'ET' reads as Eastern. None when unreadable."""
    if not value:
        return None
    s = str(value).strip()
    for suffix in (" EDT", " EST", " ET"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s)
    except ValueError:
        return None
    return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt


def week_of(session: str) -> str:
    y, w, _ = date.fromisoformat(session[:10]).isocalendar()
    return f"{y}-W{w:02d}"


# --------------------------------------------------------------------------- orders
def order(*, module, session, placed, filled, missed, expected, actual, width, quantity, mult=100.0) -> dict:
    """One live entry's execution row. `expected`/`actual` are per-share credits."""
    out = {
        "module": module,
        "session": session,
        "outcome": "missed" if missed else ("filled" if filled or actual is not None else "pending"),
        "expected": expected,
        "actual": None if missed else actual,
        "slip_dollars": None,
        "slip_pct_width": None,
        "minutes_to_fill": None,
    }
    if out["outcome"] == "filled" and expected is not None and actual is not None:
        out["slip_dollars"] = round((actual - expected) * mult * (quantity or 1), 2)
        if width:
            out["slip_pct_width"] = round((actual - expected) / width * 100, 2)
    p, f = parse_ts(placed), parse_ts(filled)
    if out["outcome"] == "filled" and p and f and f >= p:
        out["minutes_to_fill"] = round((f - p).total_seconds() / 60, 1)
    return out


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def working_sessions(conn, orders: list[dict]) -> set[str]:
    """Sessions live was working: any order, or any journalled entry decision (meic from 2026-10-23)."""
    out = {o["session"][:10] for o in orders if o.get("session")}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'entry_attempts'").fetchone():
        out |= {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM entry_attempts") if r[0]}
    return out


def meic_orders(conn) -> list[dict]:
    cols = _cols(conn, "ic_trades")
    mid = "entry_mid_at_submit" if "entry_mid_at_submit" in cols else "NULL"
    rows = conn.execute(
        f"SELECT trade_date, created_at, fill_confirmed_at, status, net_credit, {mid} AS mid, wing_width,"
        " quantity, dollar_multiplier FROM ic_trades ORDER BY id"
    ).fetchall()
    return [
        order(
            module="meic",
            session=r["trade_date"],
            placed=r["created_at"],
            filled=r["fill_confirmed_at"],
            missed=r["status"] == "cancelled",
            expected=r["mid"],
            actual=r["net_credit"] if r["fill_confirmed_at"] else None,
            width=r["wing_width"],
            quantity=r["quantity"],
            mult=r["dollar_multiplier"] or 100.0,
        )
        for r in rows
    ]


def bwb_orders(conn) -> list[dict]:
    if "entry_order_id" not in _cols(conn, "bwb_positions"):
        return []
    rows = conn.execute(
        "SELECT entry_session, entry_placed_at, entry_time, status, entry_fill_status, entry_credit,"
        " entry_mid_at_submit, entry_wide_width, quantity FROM bwb_positions"
        " WHERE entry_order_id IS NOT NULL ORDER BY entry_placed_at"
    ).fetchall()
    return [
        order(
            module="bwb",
            session=r["entry_session"],
            placed=r["entry_placed_at"],
            filled=r["entry_time"]
            if r["status"] != "cancelled" and r["entry_fill_status"] != "pending"
            else None,
            missed=r["status"] == "cancelled",
            expected=r["entry_mid_at_submit"],
            actual=r["entry_credit"] if r["status"] != "cancelled" else None,
            width=r["entry_wide_width"],
            quantity=r["quantity"],
        )
        for r in rows
    ]


ORDER_READERS = {"meic": meic_orders, "bwb": bwb_orders}


# --------------------------------------------------------------------------- overrides
def kill_clears(module: str) -> list[dict]:
    path = _kill.history_path()
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("module") == module and e.get("event") == "cleared":
            at = parse_ts(e.get("at"))
            if at:
                out.append(
                    {"kind": "kill_cleared", "session": at.astimezone(ET).date().isoformat(), "at": e["at"]}
                )
    return out


def in_session(at: datetime) -> bool:
    et = at.astimezone(ET)
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)


def config_commits(module: str, *, git_log=None) -> list[dict]:
    """Config-backup commits touching the module's config files during a session. `git_log` is the
    seam for a test: it returns `git log --format=%cI -- <files>` lines."""
    files = [f"config/{module}.json", f"config/{module}.risk.json"]
    if git_log is None:

        def git_log(paths):
            try:
                res = subprocess.run(
                    ["git", "-C", str(core_home.home()), "log", "--format=%cI", "--", *paths],
                    capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
                )  # fmt: skip
            except (OSError, subprocess.SubprocessError):
                return []
            return res.stdout.splitlines() if res.returncode == 0 else []

    out = []
    for line in git_log(files):
        at = parse_ts(line.strip())
        if at and in_session(at):
            out.append(
                {
                    "kind": "config_changed_in_session",
                    "session": at.astimezone(ET).date().isoformat(),
                    "at": line.strip(),
                }
            )
    return out


def manual_closes(conn) -> list[dict]:
    """meic ICs whose exit reason says a person closed them; bwb has no closing orders."""
    if "exit_reason" not in _cols(conn, "ic_trades"):
        return []
    rows = conn.execute(
        "SELECT trade_date, exit_time FROM ic_trades WHERE lower(COALESCE(exit_reason, '')) LIKE 'manual%'"
    ).fetchall()
    return [{"kind": "manual_close", "session": r["trade_date"], "at": r["exit_time"]} for r in rows]


# --------------------------------------------------------------------------- the paper-live gap
def gap(live: list[dict], paper: list[dict], working: set[str]) -> dict:
    """Live net against its paper mirror over the sessions live was WORKING (`working`: it placed an
    order or journalled an entry decision) -- a session live was never armed is not a miss. `live`
    and `paper` are `core.ledgers` records. Parts: missed (paper's net on working sessions live never
    filled), fill (the credit difference on shared sessions), costs (paper cost less live cost
    there), other."""
    if not working:
        return {"sessions": 0, "total": None, "parts": {}, "largest": None}
    start = min(working)
    lv = {s: v for s, v in _group(live, start).items() if s in working}
    pp = {s: v for s, v in _group(paper, start).items() if s in working}
    parts = {"missed": 0.0, "fill": 0.0, "costs": 0.0, "other": 0.0}
    total = 0.0
    for s in sorted(set(lv) | set(pp)):
        ln, pn = lv.get(s), pp.get(s)
        diff = (ln["net"] if ln else 0.0) - (pn["net"] if pn else 0.0)
        total += diff
        if pn and not ln:
            parts["missed"] -= pn["net"]
            continue
        if ln and pn:
            fill = (
                (ln["credit"] - pn["credit"])
                if ln["credit"] is not None and pn["credit"] is not None
                else 0.0
            )
            costs = pn["cost"] - ln["cost"]
            parts["fill"] += fill
            parts["costs"] += costs
            parts["other"] += diff - fill - costs
        else:
            parts["other"] += diff
    parts = {k: round(v, 2) for k, v in parts.items()}
    largest = max(parts, key=lambda k: abs(parts[k])) if any(parts.values()) else None
    return {
        "since": start,
        "sessions": len(set(lv) | set(pp)),
        "total": round(total, 2),
        "parts": parts,
        "largest": largest,
    }


def _group(rows: list[dict], start: str) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        s = (r.get("session") or "")[:10]
        if s < start or r.get("net_pnl") is None:
            continue
        g = out.setdefault(s, {"net": 0.0, "credit": 0.0, "cost": 0.0})
        g["net"] += float(r["net_pnl"])
        g["cost"] += float(r.get("cost") or 0.0)
        if g["credit"] is not None:
            g["credit"] = None if r.get("entry_credit") is None else g["credit"] + float(r["entry_credit"])
    return out


# --------------------------------------------------------------------------- the weekly read
def weekly(orders: list[dict], overrides: list[dict], live_records: list[dict]) -> list[dict]:
    weeks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": [], "overrides": [], "net": 0.0, "trades": 0}
    )
    for o in orders:
        if o["session"]:
            weeks[week_of(o["session"])]["orders"].append(o)
    for v in overrides:
        if v.get("session"):
            weeks[week_of(v["session"])]["overrides"].append(v)
    for r in live_records:
        if r.get("session") and r.get("net_pnl") is not None:
            w = weeks[week_of(r["session"])]
            w["net"] += float(r["net_pnl"])
            w["trades"] += 1
    out = []
    for wk in sorted(weeks):
        w = weeks[wk]
        filled = [o for o in w["orders"] if o["outcome"] == "filled"]
        decided = [o for o in w["orders"] if o["outcome"] in ("filled", "missed")]
        slips = [o["slip_dollars"] for o in filled if o["slip_dollars"] is not None]
        pcts = [o["slip_pct_width"] for o in filled if o["slip_pct_width"] is not None]
        mins = [o["minutes_to_fill"] for o in filled if o["minutes_to_fill"] is not None]
        out.append({
            "week": wk,
            "net": round(w["net"], 2),
            "trades": w["trades"],
            "orders": len(w["orders"]),
            "fill_rate": round(len(filled) / len(decided), 3) if decided else None,
            "slip_dollars": round(sum(slips), 2) if slips else None,
            "slip_pct_width_mean": round(sum(pcts) / len(pcts), 2) if pcts else None,
            "minutes_to_fill_mean": round(sum(mins) / len(mins), 1) if mins else None,
            "overrides": w["overrides"],
            "clean": not w["overrides"],
        })  # fmt: skip
    return out


def _records(path: Path | None, schema: str) -> list[dict]:
    if path is None or not path.exists():
        return []
    conn = _ro(path)
    try:
        return ledgers.READERS[schema](conn)
    finally:
        conn.close()


def run(cfg: dict) -> dict:
    out = {}
    for name in MODULES:
        mcfg = (cfg.get("modules") or {}).get(name) or {}
        live_path = cfgmod.live_db_path(mcfg, name)
        if live_path is None or not live_path.exists():
            out[name] = {"ok": False, "reason": "no live ledger"}
            continue
        conn = _ro(live_path)
        try:
            orders = ORDER_READERS[name](conn)
            closes = manual_closes(conn) if name == "meic" else []
            working = working_sessions(conn, orders)
        finally:
            conn.close()
        live = _records(live_path, SCHEMA[name])
        try:
            mod_cfg = json.loads(core_home.config_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mod_cfg = {}
        mirror = MEIC_MIRROR if name == "meic" else (mod_cfg.get("live") or {}).get("arm")
        paper = [
            r for r in _records(cfgmod.paper_db_path(mcfg, name), SCHEMA[name]) if r.get("arm") == mirror
        ]
        overrides = kill_clears(name) + config_commits(name) + closes
        out[name] = {
            "ok": True,
            "mirror": mirror,
            "weeks": weekly(orders, overrides, live),
            "gap": gap(live, paper, working),
        }
    return out


def format_report(result: dict) -> str:
    lines = []
    for name, m in result.items():
        if not m.get("ok"):
            lines.append(f"{name}: {m.get('reason')}")
            continue
        lines.append(f"{name} (paper mirror: {m['mirror']})")
        for w in m["weeks"]:
            flags = ", ".join(sorted({o["kind"] for o in w["overrides"]})) or "clean"
            fr = "n/r" if w["fill_rate"] is None else f"{w['fill_rate']:.0%}"
            slip = "n/r" if w["slip_dollars"] is None else f"{w['slip_dollars']:+.2f}"
            lines.append(
                f"  {w['week']}  net {w['net']:+.2f} over {w['trades']}  orders {w['orders']}  fill {fr}"
                f"  slip vs mid {slip}  overrides: {flags}"
            )
        g = m["gap"]
        if g.get("total") is not None:
            parts = "  ".join(f"{k} {v:+.2f}" for k, v in g["parts"].items())
            lines.append(
                f"  live - paper since {g['since']}: {g['total']:+.2f}  ({parts}); largest: {g['largest']}"
            )
    return "\n".join(lines)
