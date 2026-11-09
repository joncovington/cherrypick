#!/usr/bin/env python
# ruff: noqa: E501  -- invented-data tables read better on one line than wrapped
"""Build a fake cherrypick home for documentation screenshots of the console.

A developer tool, not a feature: nothing in the installer or run.py calls it. Every number, name and
date in the home it writes is invented; see docs/demo-home.md for the rules and for how to serve the
console against it.

    python scripts/demo_home.py --out <dir> [--seed 7] [--day 2026-11-06] [--days 30]

The same seed and day always write the same files. The home is built from the packages' own writers
and pure functions where one exists (the manage evaluation, the screener and review file shapes), and
from each ledger's schema as the console's test fixtures pin it everywhere else.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGES = REPO / "packages"
FIXTURES = PACKAGES / "console" / "server" / "test" / "fixtures"

# The only names that may appear. Index/ETF trades and single names are drawn from these two lists.
INDEX_NAMES = ("SPX", "XSP", "SPY", "QQQ", "IWM")
SINGLE_NAMES = ("AAA", "BBB", "CCC", "DDD")
ACCOUNT = "****0000"
PORT = 5391
FAKE_REPO = (
    "/opt/cherrypick"  # what generated configs say the checkout is, so no screenshot shows a real path
)
MARKER = ".demo-home"

HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25), date(2026, 7, 3)}


def _trading_days(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5 or d in HOLIDAYS:
        d += timedelta(days=1)
    return d


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _friday_on_or_after(d: date) -> date:
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


class Ctx:
    """Everything a section needs: where to write, the seeded generator, and the calendar."""

    def __init__(self, out: Path, seed: int, day: date, days: int) -> None:
        self.out = out
        self.seed = seed
        self.day = day
        self.days = days
        self.sessions = _trading_days(day, days)
        self.start = self.sessions[0]
        self.rngs: dict[str, random.Random] = {}

    def rng(self, name: str) -> random.Random:
        """One generator per section, so adding a section never shifts another's numbers."""
        if name not in self.rngs:
            self.rngs[name] = random.Random(f"{self.seed}:{name}")
        return self.rngs[name]

    # -- paths ---------------------------------------------------------------------------
    def p(self, *parts: str) -> Path:
        path = self.out.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def iso(self, d: date, hhmm: str, seconds: int = 0) -> str:
        """An Eastern-time stamp with its offset, as the ledgers record them."""
        # Daylight time ends 2026-11-01 and begins 2027-03-14; the demo never spans anything else.
        offset = "-04:00" if date(2026, 3, 8) <= d < date(2026, 11, 1) else "-05:00"
        return f"{d.isoformat()}T{hhmm}:{seconds:02d}{offset}"

    def epoch(self, d: date, hhmm: str, seconds: int = 0) -> float:
        """The same stamp as epoch seconds, for the ledgers that keep floats."""
        return datetime.fromisoformat(self.iso(d, hhmm, seconds)).timestamp()


def use_this_checkout() -> None:
    """Import the suite's packages from this checkout, not from whichever one is pip-installed.

    The packages are namespace-style (`cherrypick.core`, `cherrypick.manage`, ...) and an editable
    install points them at one fixed tree, so a generator run from a worktree would otherwise build
    the home with another tree's writers.
    """
    sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in repr(f).lower()]
    paths = [PACKAGES / "core", *sorted(PACKAGES.glob("*/src"))]
    for p in reversed(paths):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    for name in [m for m in sys.modules if m == "cherrypick" or m.startswith("cherrypick.")]:
        del sys.modules[name]


# ------------------------------------------------------------------------------ small writers
def write_json(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_db(path: Path, *schema_files: str) -> sqlite3.Connection:
    """A fresh SQLite ledger built from the console fixtures' schema (pinned to each module's own)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    for name in schema_files:
        con.executescript((FIXTURES / name).read_text(encoding="utf-8"))
    return con


def insert(con: sqlite3.Connection, table: str, row: dict) -> int:
    """Insert only the columns the table has, so a ledger that grew a column does not break the build."""
    have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    cols = [c for c in row if c in have]
    cur = con.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [row[c] for c in cols],
    )
    return int(cur.lastrowid or 0)


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def money(x: float) -> float:
    return round(x + 0.0, 2)


def occ(root: str, exp: date, right: str, strike: float) -> str:
    """An OCC symbol, root padded to six."""
    return f"{root:<6}{exp.strftime('%y%m%d')}{right}{int(round(strike * 1000)):08d}"


# ------------------------------------------------------------------------------ the invented market
class Market:
    """A small made-up market: one closing price a session for each name, a VIX, and intraday ranges.

    Everything is drawn from the seeded generator, so a home is reproducible and no figure is a real
    quote. SPX, XSP and SPY move together (they are the same index at different sizes).
    """

    START = {"SPX": 5880.0, "QQQ": 512.0, "IWM": 236.0, "AAA": 47.8, "BBB": 112.4, "CCC": 83.2, "DDD": 30.6}
    VOL = {"SPX": 0.0085, "QQQ": 0.011, "IWM": 0.0125, "AAA": 0.018, "BBB": 0.015, "CCC": 0.02, "DDD": 0.024}

    def __init__(self, ctx: Ctx) -> None:
        rng = ctx.rng("market")
        self.ctx = ctx
        # One more session before the window, so every session has a prior close.
        days = [_trading_days(ctx.start - timedelta(days=1), 1)[0], *ctx.sessions]
        self.days = days
        self.close: dict[str, dict[date, float]] = {}
        self.high: dict[str, dict[date, float]] = {}
        self.low: dict[str, dict[date, float]] = {}
        common = {d: rng.gauss(0.0003, 1.0) for d in days}
        for name, start in self.START.items():
            px = start
            self.close[name], self.high[name], self.low[name] = {}, {}, {}
            for d in days:
                shock = 0.6 * common[d] + 0.8 * rng.gauss(0, 1.0)
                if d == ctx.day:
                    shock = 0.35  # the headline session is an ordinary, mildly up day
                new = px * (1 + shock * self.VOL[name])
                swing = abs(rng.gauss(0.0, 1.0)) * self.VOL[name] * 0.6 + 0.0015
                self.close[name][d] = round(new, 2)
                self.high[name][d] = round(max(px, new) * (1 + swing), 2)
                self.low[name][d] = round(min(px, new) * (1 - swing), 2)
                px = new
        self.vix: dict[date, float] = {}
        level = 17.2
        for d in days:
            level = max(12.5, min(31.0, level + rng.gauss(0, 0.7) - 0.05 * (level - 17.0)))
            self.vix[d] = round(level, 2)

    def px(self, name: str, d: date) -> float:
        """A close for any name; XSP and SPY are scaled copies of SPX."""
        if name == "XSP":
            return round(self.close["SPX"][d] / 10, 2)
        if name == "SPY":
            return round(self.close["SPX"][d] / 10.03, 2)
        return self.close[name][d]

    def prev(self, d: date) -> date:
        return self.days[self.days.index(d) - 1]

    def last(self, name: str) -> float:
        base = {"XSP": "SPX", "SPY": "SPX"}.get(name, name)
        px = self.close[base][self.ctx.day]
        return round(px / 10 if name == "XSP" else px / 10.03 if name == "SPY" else px, 2)


# ------------------------------------------------------------------------------ configs
def _absolutise(node: object) -> object:
    """Point the example config at this checkout and at the home it is read from."""
    if isinstance(node, dict):
        return {k: _absolutise(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_absolutise(v) for v in node]
    if isinstance(node, str):
        if node.startswith("~/.cherrypick"):
            return "$CHERRYPICK_HOME" + node[len("~/.cherrypick") :]
        if node.startswith("../") and node.count("/") == 1:
            return f"{FAKE_REPO}/packages/{node[3:]}"
    return node


def write_configs(ctx: Ctx) -> None:
    cfg = _absolutise(read_json(PACKAGES / "orchestrator" / "config.example.json"))
    assert isinstance(cfg, dict)
    shipped_on = {"meic", "earnings", "calendars", "contango", "weeklyput"}
    for name, mod in cfg["modules"].items():
        mod["enabled"] = name in shipped_on
    cfg["capabilities"] = {"claude": False, "dolt": True}
    for key in ("quikoptions", "market_report", "advisor", "backtest", "config_backup"):
        if key in cfg and isinstance(cfg[key], dict):
            cfg[key]["enabled"] = False
    cfg["manage"]["enabled"] = True
    write_json(ctx.p("config.json"), cfg)

    def patch(doc: dict, edits: dict) -> dict:
        for dotted, value in edits.items():
            node = doc
            *head, leaf = dotted.split(".")
            for part in head:
                node = node.setdefault(part, {})
            node[leaf] = value
        return doc

    def example(pkg: str, name: str = "config.example.json") -> dict:
        return _absolutise(read_json(PACKAGES / pkg / name))  # type: ignore[return-value]

    write_json(ctx.p("config", "console.json"), {"serve": {"port": PORT}})
    write_json(ctx.p("config", "meic.json"), example("meic"))
    risk = example("meic", "config.risk.example.json")
    gate = json.loads(json.dumps(risk["profiles"]["control"]))
    gate.update(
        {
            "_summary": "Control with the GEX sign gate on: no entry while net gamma is negative.",
            "regime_gex_block_negative": True,
        }
    )
    risk["profiles"]["gex-gate"] = gate
    write_json(ctx.p("config", "meic.risk.json"), risk)
    earnings = example("earnings/config", "config.example.json")
    earnings["correlation_block_list"] = [list(SINGLE_NAMES)]  # the shipped list names real companies
    write_json(ctx.p("config", "earnings.json"), earnings)
    write_json(ctx.p("config", "calendars.json"), example("calendars"))
    contango = example("contango")
    patch(contango, {"defaults.risk_symbol": "QQQ", "defaults.cash_symbol": "IWM"})
    write_json(ctx.p("config", "contango.json"), contango)
    weeklyput = example("weeklyput")
    weeklyput["names"] = {k: v for k, v in weeklyput["names"].items() if k != "QQQ"}
    write_json(ctx.p("config", "weeklyput.json"), weeklyput)
    gex = example("gex")
    gex["symbols"] = ["SPX", "XSP", "QQQ"]
    write_json(ctx.p("config", "gex.json"), gex)
    write_json(ctx.p("config", "streamer.json"), example("streamer"))
    for sub in ("state", "logs", "data"):
        ctx.p(sub, ".keep").write_text("", encoding="utf-8")


# ------------------------------------------------------------------------------ MEIC
MEIC_ENTRY_TIMES = {"control": ["10:15", "12:30", "14:00"], "gex-gate": ["10:30", "13:00"]}


def write_meic(ctx: Ctx, mk: Market) -> dict:
    """Paper iron condors on SPX, two arms, every one held to settlement (no per-side stop)."""
    rng = ctx.rng("meic")
    con = make_db(ctx.p("data", "meic", "paper_trades.db"), "meic-ledger-schema.sql")
    width = 25.0
    n_trades = 0
    summary: dict[date, dict] = {}
    for d in ctx.sessions:
        prev = mk.close["SPX"][mk.prev(d)]
        close = mk.close["SPX"][d]
        vix = mk.vix[d]
        em = prev * vix / 100 / math.sqrt(252)
        gex_ok = rng.random() > 0.3  # the GEX gate blocks about three days in ten
        con.execute(
            "INSERT INTO market_context (context_date, vix, vix1d, vix1d_ratio, symbols_json, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                d.isoformat(),
                vix,
                round(vix * 0.93, 2),
                0.93,
                json.dumps({"SPX": {"last": close}}),
                ctx.iso(d, "16:05"),
            ),
        )
        total = filled = 0
        gross = fees_day = 0.0
        wins = 0
        for arm, times in MEIC_ENTRY_TIMES.items():
            for i, hhmm in enumerate(times):
                total += 1
                hh, mm = (int(x) for x in hhmm.split(":"))
                frac = ((hh - 9.5) * 60 + mm) / 390.0
                spot = round(prev + (close - prev) * frac * 0.8 + rng.gauss(0, em * 0.15), 2)
                if arm == "gex-gate" and not gex_ok:
                    insert(
                        con,
                        "entry_attempts",
                        {
                            "ts": ctx.iso(d, hhmm, 5),
                            "trade_date": d.isoformat(),
                            "arm": arm,
                            "symbol": "SPX",
                            "expiration": d.isoformat(),
                            "outcome": "gate_blocked",
                            "block_detail": "regime_gex_negative",
                            "underlying_price": spot,
                            "iv_rank": round(rng.uniform(0.18, 0.52), 3),
                            "gex_net": round(-rng.uniform(0.4, 2.2), 2),
                            "gex_positive": 0,
                        },
                    )
                    continue
                k = rng.uniform(0.6, 0.8)
                put_k = math.floor((spot - k * em) / 5) * 5
                call_k = math.ceil((spot + k * em) / 5) * 5
                pc = round(rng.uniform(1.7, 2.3) * vix / 17.0, 2)
                cc = round(rng.uniform(1.7, 2.3) * vix / 17.0, 2)
                credit = round(pc + cc, 2)
                put_loss = min(width, max(0.0, put_k - close))
                call_loss = min(width, max(0.0, close - call_k))
                put_pnl = money((pc - put_loss) * 100)
                call_pnl = money((cc - call_loss) * 100)
                pnl = money(put_pnl + call_pnl)
                exit_price = round(put_loss + call_loss, 2)
                fees = 2.6
                slip = round(rng.uniform(2.0, 5.0), 2)
                oid = f"P{d.strftime('%y%m%d')}{arm[0].upper()}{i + 1}"
                exp = d
                row = {
                    "trade_date": d.isoformat(),
                    "entry_time": ctx.iso(d, hhmm, 9),
                    "expiration": exp.isoformat(),
                    "symbol": "SPX",
                    "put_strike": put_k,
                    "call_strike": call_k,
                    "wing_width": width,
                    "put_symbol": occ("SPXW", exp, "P", put_k),
                    "call_symbol": occ("SPXW", exp, "C", call_k),
                    "long_put_symbol": occ("SPXW", exp, "P", put_k - width),
                    "long_call_symbol": occ("SPXW", exp, "C", call_k + width),
                    "put_credit": pc,
                    "call_credit": cc,
                    "net_credit": credit,
                    "quantity": 1,
                    "put_delta_at_entry": round(-rng.uniform(0.11, 0.17), 3),
                    "call_delta_at_entry": round(rng.uniform(0.11, 0.17), 3),
                    "underlying_price_entry": spot,
                    "iv_rank_at_entry": round(rng.uniform(0.18, 0.52), 3),
                    "session_quality": "normal",
                    "gex_net_at_entry": round(rng.uniform(0.2, 2.4), 2),
                    "gex_positive_at_entry": 1,
                    "slippage_dollars": slip,
                    "ic_order_id": oid,
                    "status": "expired",
                    "exit_time": ctx.iso(d, "16:00", 5),
                    "exit_price": exit_price,
                    "exit_reason": "expired_settlement",
                    "pnl": pnl,
                    "fees": fees,
                    "dollar_multiplier": 100,
                    "fill_confirmed_at": ctx.iso(d, hhmm, 9),
                    "arm": arm,
                    "execution_mode": "paper",
                    "era": "advisor",
                    "created_at": ctx.iso(d, hhmm, 9),
                    "updated_at": ctx.iso(d, "16:05"),
                    "put_wing": width,
                    "call_wing": width,
                    "settle_underlying": close,
                    "put_settle_value": round(put_loss, 2),
                    "call_settle_value": round(call_loss, 2),
                    "put_max_cost": round(put_loss + rng.uniform(0, 0.8), 2),
                    "call_max_cost": round(call_loss + rng.uniform(0, 0.8), 2),
                    "minutes_to_close": int(390 * (1 - frac)),
                }
                insert(con, "ic_trades", row)
                for side, sp in (("put", put_pnl), ("call", call_pnl)):
                    loss = put_loss if side == "put" else call_loss
                    insert(
                        con,
                        "ic_spread_legs",
                        {
                            "ic_order_id": oid,
                            "side": side,
                            "status": "expired",
                            "exit_time": ctx.iso(d, "16:00", 5),
                            "exit_reason": "expired_settlement",
                            "exit_price": round(loss, 2),
                            "pnl": sp,
                            "created_at": ctx.iso(d, hhmm, 9),
                            "updated_at": ctx.iso(d, "16:05"),
                        },
                    )
                insert(
                    con,
                    "entry_attempts",
                    {
                        "ts": ctx.iso(d, hhmm, 8),
                        "trade_date": d.isoformat(),
                        "arm": arm,
                        "symbol": "SPX",
                        "expiration": d.isoformat(),
                        "outcome": "filled",
                        "put_strike": put_k,
                        "call_strike": call_k,
                        "wing_width": width,
                        "underlying_price": spot,
                        "iv_rank": row["iv_rank_at_entry"],
                        "gex_net": row["gex_net_at_entry"],
                        "gex_positive": 1,
                        "session_quality": "normal",
                        "would_be_credit": credit,
                        "ic_order_id": oid,
                    },
                )
                filled += 1
                n_trades += 1
                gross += pnl
                fees_day += fees
                wins += 1 if pnl - fees > 0 else 0
        insert(
            con,
            "daily_summary",
            {
                "summary_date": d.isoformat(),
                "symbol": "SPX",
                "total_entries": total,
                "entries_filled": filled,
                "entries_stopped": 0,
                "entries_expired": filled,
                "entries_cancelled": total - filled,
                "gross_pnl": money(gross),
                "fees": money(fees_day),
                "net_pnl": money(gross - fees_day),
                "win_count": wins,
                "win_rate_pct": round(100 * wins / filled, 1) if filled else None,
                "created_at": ctx.iso(d, "16:05"),
                "updated_at": ctx.iso(d, "16:05"),
            },
        )
        insert(
            con,
            "loop_log",
            {
                "loop_time": ctx.iso(d, "15:59"),
                "loop_date": d.isoformat(),
                "symbol": "SPX",
                "action": "tick",
                "reasoning": "window closed",
                "open_trades_n": 0,
                "today_count": filled,
                "today_pnl": money(gross - fees_day),
                "iv_rank": 0.31,
                "underlying_price": close,
                "session_quality": "normal",
                "created_at": ctx.iso(d, "15:59"),
            },
        )
        summary[d] = {"net": gross - fees_day, "filled": filled}
    con.commit()
    con.close()
    return {"trades": n_trades, "summary": summary}


# ------------------------------------------------------------------------------ earnings
def write_earnings(ctx: Ctx, mk: Market) -> list[dict]:
    """Paper earnings iron flies on the four single names: close the next morning, plus one open."""
    rng = ctx.rng("earnings")
    con = make_db(ctx.p("data", "earnings", "paper_trades.db"), "earnings-ledger-schema.sql")
    names = list(SINGLE_NAMES)
    out: list[dict] = []
    entry_days = [d for i, d in enumerate(ctx.sessions[:-1]) if i % 3 == 1]
    for n, d in enumerate(entry_days):
        sym = names[n % len(names)]
        nxt = _next_trading_day(d)
        spot = mk.close[sym][d]
        em = round(spot * rng.uniform(0.045, 0.075), 2)
        step = 0.5 if spot < 50 else 1.0
        short = round(spot / step) * step
        wing = max(step, round(em / step) * step)
        credit = round(wing * rng.uniform(0.5, 0.62), 2)
        move = rng.gauss(0, 1) * em * 0.85
        value = max(0.0, wing - abs(move)) * rng.uniform(0.88, 1.0)
        debit = round(value, 2)
        gross = money((credit - debit) * 100)
        slip_e, slip_x = round(rng.uniform(2, 5), 2), round(rng.uniform(2, 5), 2)
        entry_cost, exit_cost = money(2.6 + slip_e), money(2.6 + slip_x)
        oid = f"E{d.strftime('%y%m%d')}-{sym}"
        exp = _friday_on_or_after(nxt)
        legs = [
            {"role": "short_call", "symbol": occ(sym, exp, "C", short), "action": "STO", "quantity": 1},
            {"role": "short_put", "symbol": occ(sym, exp, "P", short), "action": "STO", "quantity": 1},
            {"role": "long_call", "symbol": occ(sym, exp, "C", short + wing), "action": "BTO", "quantity": 1},
            {"role": "long_put", "symbol": occ(sym, exp, "P", short - wing), "action": "BTO", "quantity": 1},
        ]
        row = {
            "order_id": oid,
            "strategy": "iron_fly",
            "symbol": sym,
            "expiration": exp.isoformat(),
            "short_strike": short,
            "long_call_strike": short + wing,
            "long_put_strike": short - wing,
            "legs_json": json.dumps(legs),
            "entry_credit": credit,
            "exit_debit": debit,
            "pnl": gross,
            "opened_at": ctx.epoch(d, "15:46"),
            "closed_at": ctx.epoch(nxt, "09:46"),
            "arm": "default",
            "quantity": 1,
            "capital_at_risk": money((wing - credit) * 100),
            "entry_cost": entry_cost,
            "exit_cost": exit_cost,
            "entry_context": json.dumps(
                {
                    "iv_rv_ratio": round(rng.uniform(1.1, 1.7), 2),
                    "dispersion": round(rng.uniform(1.5, 4.5), 2),
                }
            ),
            "entry_iv": round(rng.uniform(0.55, 0.9), 3),
            "exit_iv": round(rng.uniform(0.3, 0.45), 3),
            "status": "closed",
            "exit_reason": "time_exit",
            "hold_days": 1,
            "entry_slippage": slip_e,
            "exit_slippage": slip_x,
        }
        insert(con, "trades", row)
        for leg in legs:
            insert(
                con,
                "trade_legs",
                {
                    "order_id": oid,
                    "leg_role": leg["role"],
                    "symbol": leg["symbol"],
                    "action": leg["action"],
                    "quantity": 1,
                    "status": "closed",
                },
            )
        out.append({"order_id": oid, "day": d, "pnl": gross, "net": money(gross - entry_cost - exit_cost)})
        insert(
            con,
            "entry_reviews",
            {
                "scan_date": d.isoformat(),
                "symbol": sym,
                "timing": "after_close",
                "strategy": "iron_fly",
                "price": spot,
                "winrate": round(rng.uniform(0.55, 0.8), 2),
                "iv_rv_ratio": json.loads(row["entry_context"])["iv_rv_ratio"],
                "expected_move": em,
                "expected_move_pct": round(100 * em / spot, 2),
                "selected": 1,
                "reason": "passed every gate",
                "logged_at": ctx.epoch(d, "15:40"),
                "arm": "default",
            },
        )
    # one position still open on the latest session: a name reporting after the close
    d = ctx.day
    sym = "BBB"
    spot = mk.close[sym][d]
    exp = _friday_on_or_after(_next_trading_day(d))
    short, wing, credit = round(spot), 6.0, 3.4
    legs = [
        {"role": "short_call", "symbol": occ(sym, exp, "C", short), "action": "STO", "quantity": 1},
        {"role": "short_put", "symbol": occ(sym, exp, "P", short), "action": "STO", "quantity": 1},
        {"role": "long_call", "symbol": occ(sym, exp, "C", short + wing), "action": "BTO", "quantity": 1},
        {"role": "long_put", "symbol": occ(sym, exp, "P", short - wing), "action": "BTO", "quantity": 1},
    ]
    oid = f"E{d.strftime('%y%m%d')}-{sym}"
    insert(
        con,
        "trades",
        {
            "order_id": oid,
            "strategy": "iron_fly",
            "symbol": sym,
            "expiration": exp.isoformat(),
            "short_strike": short,
            "long_call_strike": short + wing,
            "long_put_strike": short - wing,
            "legs_json": json.dumps(legs),
            "entry_credit": credit,
            "opened_at": ctx.epoch(d, "15:47"),
            "arm": "default",
            "quantity": 1,
            "capital_at_risk": money((wing - credit) * 100),
            "entry_cost": 5.9,
            "entry_slippage": 3.3,
            "entry_context": json.dumps({"iv_rv_ratio": 1.42, "dispersion": 2.8}),
            "entry_iv": 0.71,
            "status": "open",
        },
    )
    for leg in legs:
        insert(
            con,
            "trade_legs",
            {
                "order_id": oid,
                "leg_role": leg["role"],
                "symbol": leg["symbol"],
                "action": leg["action"],
                "quantity": 1,
            },
        )
    insert(
        con,
        "position_marks",
        {
            "order_id": oid,
            "marked_at": ctx.epoch(d, "15:58"),
            "session_date": d.isoformat(),
            "exit_debit": 3.2,
            "unrealized_pnl": 20.0,
            "spot": spot,
            "source": "stream",
            "quotes_fresh": 4,
            "quotes_stale": 0,
            "usable": 1,
        },
    )
    insert(
        con,
        "market_context",
        {
            "context_date": d.isoformat(),
            "vix": mk.vix[d],
            "vix1d": round(mk.vix[d] * 0.93, 2),
            "updated_at": ctx.epoch(d, "15:50"),
        },
    )
    for d2 in ctx.sessions:
        n_closed = [t for t in out if t["day"] == mk.prev(d2) or t["day"] == d2]
        insert(
            con,
            "daily_summary",
            {
                "summary_date": d2.isoformat(),
                "positions_opened": sum(1 for t in out if t["day"] == d2),
                "positions_closed": sum(1 for t in out if _next_trading_day(t["day"]) == d2),
                "net_pnl": money(sum(t["net"] for t in out if _next_trading_day(t["day"]) == d2)),
            },
        )
        del n_closed
    insert(
        con,
        "loop_iterations",
        {
            "ran_at": ctx.epoch(d, "15:59"),
            "session_date": d.isoformat(),
            "phase": "entry",
            "status": "ok",
            "open_positions": 1,
            "marks_written": 1,
            "actions_taken": 0,
            "quotes_fresh": 4,
            "quotes_stale": 0,
            "open_capital": 260.0,
            "duration_ms": 840,
        },
    )
    # what the management loop saw on the closed trades: the verdicts that were taken
    for t in out:
        nxt = _next_trading_day(t["day"])
        insert(
            con,
            "management_events",
            {
                "order_id": t["order_id"],
                "occurred_at": ctx.epoch(nxt, "09:46"),
                "session_date": nxt.isoformat(),
                "phase": "close",
                "action": "close",
                "reason": "time_exit",
                "executed": 1,
                "arm": "default",
            },
        )
    # the morning forward scan: who reports in the next few sessions, and how each reads
    watch = {}
    for i, sym in enumerate(SINGLE_NAMES):
        day_n = ctx.day
        for _ in range(i + 1):
            day_n = _next_trading_day(day_n)
        tier = ("recommended", "watch", "watch", "skip")[i]
        watch[sym] = {
            "symbol": sym,
            "earnings_date": day_n.isoformat(),
            "earnings_timing": "after_close" if i % 2 == 0 else "before_open",
            "price": mk.px(sym, ctx.day),
            "expected_move_pct": round(rng.uniform(4.5, 8.5), 2),
            "iv_rv_ratio": round(rng.uniform(1.0, 1.7), 2),
            "term_structure": round(rng.uniform(1.05, 1.35), 2),
            "winrate": round(rng.uniform(0.5, 0.8), 2),
            "iv_rank": round(rng.uniform(25, 80), 1),
            "tier": tier,
            "tier_reasons": ["every gate passed"]
            if tier == "recommended"
            else ["implied move under the average move"],
        }
    write_json(
        ctx.p("data", "earnings", "symbol_watch.json"),
        {
            "pass_started_at": ctx.epoch(ctx.day, "09:20"),
            "pass_completed_at": ctx.epoch(ctx.day, "09:31"),
            "done": len(watch),
            "total": len(watch),
            "symbols": watch,
        },
    )
    con.commit()
    con.close()
    return out


# ------------------------------------------------------------------------------ calendars
def _weeks(ctx: Ctx) -> list[list[date]]:
    """The window's sessions grouped by calendar week, in order."""
    weeks: dict[date, list[date]] = {}
    for d in ctx.sessions:
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)
    return [weeks[k] for k in sorted(weeks)]


def write_calendars(ctx: Ctx, mk: Market) -> None:
    """Weekly SPY long calendars, one put side and one call side a week, closed in Friday's window."""
    rng = ctx.rng("calendars")
    con = make_db(ctx.p("data", "calendars", "paper_trades.db"), "calendars-ledger-schema.sql")
    for wk in _weeks(ctx):
        entry = wk[0]
        week_of = (entry - timedelta(days=entry.weekday())).isoformat()
        fri = _friday_on_or_after(entry)
        back = fri + timedelta(days=7)
        spot = round(mk.close["SPX"][mk.prev(entry)] / 10.03, 2)
        em = round(spot * 0.0125, 2)
        for side, right, sign in (("put", "P", -1), ("call", "C", 1)):
            strike = round(spot + sign * em)
            debit = round(rng.uniform(1.05, 1.65), 2)
            pid = f"C{entry.strftime('%y%m%d')}-{side[0].upper()}"
            closed = fri <= ctx.day
            s_t = mk.close["SPX"][fri] / 10.03 if closed else spot
            value = (
                round(debit * (0.30 + 1.55 * math.exp(-(((s_t - strike) / (em * 0.55)) ** 2))), 2)
                if closed
                else None
            )
            fee_e, fee_x = 1.3, 1.3
            slip_e = round(rng.uniform(2.0, 4.0), 2)
            slip_x = round(rng.uniform(2.0, 4.0), 2)
            row = {
                "position_id": pid,
                "week_of": week_of,
                "entry_session": entry.isoformat(),
                "arm": "control",
                "side": side,
                "symbol": "SPY",
                "structure": "calendar",
                "front_expiration": fri.isoformat(),
                "back_expiration": back.isoformat(),
                "strike": float(strike),
                "quantity": 1,
                "entry_time": ctx.iso(entry, "10:05", 12),
                "entry_debit": debit,
                "entry_cost": money(fee_e + slip_e),
                "entry_slippage": slip_e,
                "entry_spot": spot,
                "entry_em": em,
                "entry_em_pct": 1.25,
                "entry_front_iv": round(rng.uniform(0.12, 0.17), 4),
                "entry_back_iv": round(rng.uniform(0.13, 0.18), 4),
                "entry_term_structure": round(rng.uniform(0.02, 0.09), 4),
                "status": "closed" if closed else "open",
                "created_at": ctx.iso(entry, "10:05"),
                "updated_at": ctx.iso(fri if closed else ctx.day, "15:55"),
            }
            if closed:
                gross = money((value - debit) * 100)
                row.update(
                    {
                        "exit_reason": "fri_close",
                        "closed_at": ctx.iso(fri, "15:50", 20),
                        "closed_session": fri.isoformat(),
                        "exit_value": value,
                        "exit_cost": money(fee_x + slip_x),
                        "exit_slippage": slip_x,
                        "settlement_spot": round(s_t, 2),
                        "itm_settlements": 0,
                        "gross_pnl": gross,
                        "fees": money(fee_e + slip_e + fee_x + slip_x),
                        "settlement_fees": 0.0,
                    }
                )
            insert(con, "dc_positions", row)
            for which, exp_, act in (("front", fri, "STO"), ("back", back, "BTO")):
                mid = round(debit * (0.45 if which == "front" else 1.45), 2)
                insert(
                    con,
                    "dc_legs",
                    {
                        "position_id": pid,
                        "leg_role": f"{which}_{side}",
                        "occ_symbol": occ("SPY", exp_, right, strike),
                        "streamer_symbol": f".SPY{exp_.strftime('%y%m%d')}{right}{strike}",
                        "expiration": exp_.isoformat(),
                        "strike": float(strike),
                        "option_type": right,
                        "action": act,
                        "quantity": 1,
                        "entry_bid": round(mid - 0.02, 2),
                        "entry_ask": round(mid + 0.02, 2),
                        "entry_mid": mid,
                        "entry_iv": round(rng.uniform(0.12, 0.18), 4),
                        "entry_delta": round(sign * rng.uniform(0.3, 0.4), 3),
                        "status": "closed" if closed else "open",
                        "created_at": ctx.iso(entry, "10:05"),
                        "updated_at": ctx.iso(entry, "10:05"),
                    },
                )
        insert(
            con,
            "dc_entry_attempts",
            {
                "ts": ctx.iso(entry, "10:05", 10),
                "trade_date": entry.isoformat(),
                "week_of": week_of,
                "symbol": "SPY",
                "outcome": "filled",
                "spot": spot,
                "em": em,
                "put_target": round(spot - em, 2),
                "call_target": round(spot + em, 2),
                "put_strike": round(spot - em),
                "call_strike": round(spot + em),
                "put_debit": 1.2,
                "call_debit": 1.3,
            },
        )
    insert(
        con,
        "dc_loop_iterations",
        {
            "ran_at": ctx.epoch(ctx.day, "15:59"),
            "session_date": ctx.day.isoformat(),
            "phase": "marks",
            "status": "ok",
            "open_positions": 0,
            "marks_written": 0,
            "actions_taken": 0,
        },
    )
    con.commit()
    con.close()


# ------------------------------------------------------------------------------ contango
def write_contango(ctx: Ctx, mk: Market) -> None:
    """The VIX/VIX3M switch, two arms, each holding the risk fund below its threshold and the cash fund otherwise."""
    rng = ctx.rng("contango")
    con = make_db(ctx.p("data", "contango", "paper_trades.db"), "contango-ledger-schema.sql")
    risk, cash_sym = "QQQ", "IWM"
    ratio = 0.93
    ratios: dict[date, float] = {}
    for d in ctx.sessions:
        ratio = max(0.82, min(1.08, ratio + rng.gauss(0, 0.022) + 0.004 * (mk.vix[d] - 17.5) / 3))
        ratios[d] = round(ratio, 4)
        insert(
            con,
            "contango_regime",
            {
                "trade_date": d.isoformat(),
                "tick": ctx.iso(d, "15:50"),
                "ratio": ratios[d],
                "vix": mk.vix[d],
                "vix3m": round(mk.vix[d] / ratios[d], 2),
                "vix_age_s": 4.0,
                "vix3m_age_s": 5.0,
                "usable": 1,
            },
        )
    arms = {"control": (0.97, 0.97), "flipexit": (0.97, 1.0)}
    for arm, (enter_below, exit_at) in arms.items():
        capital = 10000.0
        cash = capital
        holding: dict | None = None
        insert(
            con,
            "contango_accounts",
            {
                "arm": arm,
                "starting_capital": capital,
                "cash": capital,
                "opened_session": ctx.start.isoformat(),
            },
        )
        n = 0
        in_risk = False
        for d in ctx.sessions:
            r = ratios[d]
            before = "risk" if holding and holding["role"] == "risk" else "cash"
            want_risk = (r < enter_below) if not in_risk else (r < exit_at)
            action = "hold"
            if holding is None or (want_risk != in_risk):
                # leave the old holding, then buy the new one
                if holding is not None:
                    px = mk.close[holding["sym"]][d]
                    exit_value = round(holding["shares"] * px, 2)
                    slip = round(holding["shares"] * 0.01, 2)
                    gross = round(exit_value - holding["entry_value"], 2)
                    net = round(gross - holding["entry_slip"] - slip, 2)
                    con.execute(
                        "UPDATE contango_positions SET status='closed', exit_session=?, exit_time=?, exit_bid=?, exit_ask=?, exit_mid=?, exit_value=?, exit_fees=0, exit_slippage=?, exit_ratio=?, exit_reason=?, gross_pnl=?, fees=0, slippage=?, net_pnl=? WHERE position_id=?",
                        (
                            d.isoformat(),
                            ctx.iso(d, "15:51"),
                            round(px - 0.01, 2),
                            round(px + 0.01, 2),
                            px,
                            exit_value,
                            slip,
                            r,
                            "ratio_above_exit" if holding["role"] == "risk" else "ratio_below_entry",
                            gross,
                            round(holding["entry_slip"] + slip, 2),
                            net,
                            holding["pid"],
                        ),
                    )
                    cash = round(cash + exit_value - 0.0, 2) if holding else cash
                    cash = round(cash + (net - gross), 2)
                in_risk = want_risk
                sym = risk if in_risk else cash_sym
                px = mk.close[sym][d]
                shares = int(cash // px)
                value = round(shares * px, 2)
                n += 1
                pid = f"K-{arm}-{n}"
                slip = round(shares * 0.01, 2)
                insert(
                    con,
                    "contango_positions",
                    {
                        "position_id": pid,
                        "arm": arm,
                        "symbol": sym,
                        "role": "risk" if in_risk else "cash",
                        "shares": shares,
                        "entry_session": d.isoformat(),
                        "entry_time": ctx.iso(d, "15:51"),
                        "entry_bid": round(px - 0.01, 2),
                        "entry_ask": round(px + 0.01, 2),
                        "entry_mid": px,
                        "entry_value": value,
                        "entry_fees": 0.0,
                        "entry_slippage": slip,
                        "entry_ratio": r,
                        "status": "open",
                        "created_at": ctx.iso(d, "15:51"),
                        "updated_at": ctx.iso(d, "15:51"),
                    },
                )
                holding = {
                    "pid": pid,
                    "sym": sym,
                    "role": "risk" if in_risk else "cash",
                    "shares": shares,
                    "entry_value": value,
                    "entry_slip": slip,
                }
                cash = round(cash - value, 2)
                action = "enter" if before == "cash" and in_risk else "exit" if not in_risk else "enter"
            px_now = mk.close[holding["sym"]][d]
            nav = round(cash + holding["shares"] * px_now, 2)
            insert(
                con,
                "contango_sessions",
                {
                    "trade_date": d.isoformat(),
                    "arm": arm,
                    "decided_at": ctx.iso(d, "15:50", 30),
                    "ratio": r,
                    "state_before": before,
                    "state_after": "risk" if in_risk else "cash",
                    "action": action,
                    "holding_symbol": holding["sym"],
                    "shares": holding["shares"],
                    "mark": px_now,
                    "cash": cash,
                    "nav": nav,
                    "created_at": ctx.iso(d, "15:50"),
                    "updated_at": ctx.iso(d, "15:50"),
                },
            )
            insert(
                con,
                "contango_decisions",
                {
                    "trade_date": d.isoformat(),
                    "arm": arm,
                    "symbol": holding["sym"],
                    "mode": "paper",
                    "reason": "switch" if action != "hold" else "no_change",
                    "accepted": 1 if action != "hold" else 0,
                    "first_ts": ctx.iso(d, "15:50"),
                    "last_ts": ctx.iso(d, "15:50"),
                },
            )
        con.execute("UPDATE contango_accounts SET cash=? WHERE arm=?", (cash, arm))
    for d in ctx.sessions[-3:]:
        for sym in (risk, cash_sym):
            px = mk.close[sym][d]
            insert(
                con,
                "contango_marks",
                {
                    "trade_date": d.isoformat(),
                    "symbol": sym,
                    "bid": round(px - 0.01, 2),
                    "ask": round(px + 0.01, 2),
                    "mid": px,
                    "age_s": 3.0,
                },
            )
    insert(
        con,
        "contango_loop_iterations",
        {
            "ran_at": ctx.epoch(ctx.day, "15:59"),
            "session_date": ctx.day.isoformat(),
            "phase": "decision",
            "status": "ok",
            "open_positions": 2,
            "marks_written": 2,
            "actions_taken": 0,
        },
    )
    con.commit()
    con.close()


# ------------------------------------------------------------------------------ weeklyput
def write_weeklyput(ctx: Ctx, mk: Market) -> None:
    """Item 57's unhedged weekly short put on SPX, SPY and IWM, entered each Friday, held to the next."""
    rng = ctx.rng("weeklyput")
    con = make_db(ctx.p("data", "weeklyput", "paper_trades.db"), "weeklyput-ledger-schema.sql")
    names = {
        "SPX": ("SPXW", "cash", "SPX"),
        "SPY": ("SPY", "physical", "SPY"),
        "IWM": ("IWM", "physical", "IWM"),
    }
    for e in [d for d in ctx.sessions if d.weekday() == 4]:
        exp = e + timedelta(days=7)
        if exp == _third_friday(exp.year, exp.month):
            continue  # the monthly expiry is a different trade
        for sym, (root, style, _fee) in names.items():
            spot = mk.px(sym, e)
            step = 5.0 if sym == "SPX" else 1.0
            strike = math.floor(spot * rng.uniform(0.955, 0.972) / step) * step
            mid = round(spot * rng.uniform(0.0011, 0.0019), 2)
            entry_value = money(mid * 100)
            settled = exp <= ctx.day
            s_t = mk.px(sym, exp) if settled else None
            intrinsic = max(0.0, strike - s_t) if s_t is not None else 0.0
            fees, slip = 1.3, round(rng.uniform(0.5, 2.0), 2)
            pid = f"W{e.strftime('%y%m%d')}-{sym}"
            row = {
                "position_id": pid,
                "arm": "unhedged",
                "symbol": sym,
                "root": root,
                "settlement_style": style,
                "expiration": exp.isoformat(),
                "entry_session": e.isoformat(),
                "entry_time": ctx.iso(e, "15:47"),
                "streamer_symbol": f".{root}{exp.strftime('%y%m%d')}P{int(strike)}",
                "occ_symbol": occ(root, exp, "P", strike),
                "strike": strike,
                "quantity": 1,
                "entry_spot": spot,
                "entry_forward": round(spot * 1.0008, 2),
                "entry_years": round(7 / 365, 5),
                "entry_bid": round(mid - 0.03, 2),
                "entry_ask": round(mid + 0.03, 2),
                "entry_mid": mid,
                "entry_iv": round(rng.uniform(0.15, 0.22), 4),
                "entry_delta": round(-rng.uniform(0.08, 0.12), 3),
                "entry_value": entry_value,
                "entry_fees": fees,
                "entry_slippage": slip,
                "notional": money(strike * 100),
                "status": "closed" if settled else "open",
                "created_at": ctx.iso(e, "15:47"),
                "updated_at": ctx.iso(e, "15:47"),
            }
            if settled:
                exit_value = money(-intrinsic * 100)
                gross = money(entry_value + exit_value)
                net = money(gross - fees - slip)
                row.update(
                    {
                        "settle_session": exp.isoformat(),
                        "settle_time": ctx.iso(exp, "16:00"),
                        "settle_price": round(s_t, 2),
                        "settle_source": "close",
                        "exit_value": exit_value,
                        "exit_reason": "expired"
                        if intrinsic == 0
                        else ("settled" if style == "cash" else "assigned"),
                        "settlement": 0.0,
                        "gross_pnl": gross,
                        "fees": fees,
                        "slippage": slip,
                        "net_pnl": net,
                        "net_bp": round(net / (strike * 100) * 10000, 2),
                        "worst_mark_value": round(mid * 1.2, 2),
                    }
                )
            insert(con, "weeklyput_positions", row)
            insert(
                con,
                "weeklyput_entries",
                {
                    "arm": "unhedged",
                    "symbol": sym,
                    "expiration": exp.isoformat(),
                    "entry_session": e.isoformat(),
                    "outcome": "filled",
                    "position_id": pid,
                    "decided_at": ctx.iso(e, "15:47"),
                },
            )
            if not settled:
                for hh in ("10:00", "13:00", "15:30"):
                    insert(
                        con,
                        "weeklyput_marks",
                        {
                            "position_id": pid,
                            "ts": ctx.iso(ctx.day, hh),
                            "session_date": ctx.day.isoformat(),
                            "usable": 1,
                            "bid": round(mid * 0.9, 2),
                            "ask": round(mid * 0.9 + 0.06, 2),
                            "mid": round(mid * 0.9 + 0.03, 2),
                            "spot": spot,
                            "mark_value": money(-(mid * 0.9 + 0.03) * 100),
                            "extrinsic": round(mid * 0.9, 2),
                        },
                    )
    insert(
        con,
        "weeklyput_loop_iterations",
        {
            "ran_at": ctx.epoch(ctx.day, "15:59"),
            "session_date": ctx.day.isoformat(),
            "phase": "marks",
            "status": "ok",
            "open_positions": 3,
            "marks_written": 3,
            "actions_taken": 0,
        },
    )
    con.commit()
    con.close()


# ------------------------------------------------------------------------------ manage (Live)
def _tx(
    order: int,
    when: str,
    action: str,
    symbol: str,
    qty: int,
    price: float,
    under: str,
    kind: str = "Equity Option",
) -> dict:
    """One trade row in the broker's shape (see packages/manage/tests/test_wheel.py for the originals)."""
    sign = -1 if action.startswith("Buy") else 1
    mult = 100 if kind == "Equity Option" else 1
    return {
        "id": f"{order}-{symbol.strip()}-{action}",
        "order_id": order,
        "executed_at": when,
        "transaction_type": "Trade",
        "transaction_sub_type": action,
        "action": action,
        "instrument_type": kind,
        "symbol": symbol,
        "underlying_symbol": under,
        "quantity": str(qty),
        "price": str(price),
        "value": str(round(sign * qty * price * mult, 2)),
        "commission": "-1.0",
        "clearing_fees": "-0.1",
        "regulatory_fees": "0.0",
        "proprietary_index_option_fees": "0.0",
    }


def _rd(
    when: str,
    sub: str,
    action: str,
    symbol: str,
    qty: int,
    under: str,
    price: float | None = None,
    value: float = 0.0,
    kind: str = "Equity Option",
) -> dict:
    """A Receive Deliver row: no order, the broker's own cash in `value`."""
    return {
        "id": f"rd-{when}-{symbol.strip()}-{sub}",
        "order_id": None,
        "executed_at": when,
        "transaction_type": "Receive Deliver",
        "transaction_sub_type": sub,
        "action": action,
        "instrument_type": kind,
        "symbol": symbol,
        "underlying_symbol": under,
        "quantity": str(qty),
        "price": None if price is None else str(price),
        "value": str(value),
        "commission": None,
        "clearing_fees": "-5.0" if sub == "Assignment" and kind == "Equity Option" else None,
        "regulatory_fees": None,
        "proprietary_index_option_fees": None,
    }


def _income_entry(pricing, spot: float, session: date, expiry: date, iv: float, step: float) -> dict:
    """One name's income reading: two-sided quotes the model prices, so every IV solves back to `iv`."""
    dte = (expiry - session).days
    years = dte / 365.0
    options = []
    k = math.floor(spot * 0.78 / step) * step
    while k <= spot * 1.22:
        for right in ("C", "P"):
            if (right == "P" and k > spot) or (right == "C" and k < spot):
                continue
            mid = pricing.price(spot, float(k), years, iv, right, 0.04)
            if mid < 0.05:
                continue
            options.append(
                {
                    "strike": float(k),
                    "right": right,
                    "option": f"{right}{k:g}",
                    "bid": round(mid - 0.02, 2),
                    "ask": round(mid + 0.02, 2),
                    "volume": 60,
                    "open_interest": 700,
                    "passes": True,
                }
            )
        k = round(k + step, 4)
    return {
        "spot": spot,
        "expiry": expiry.isoformat(),
        "dte": dte,
        "beta": 1.1,
        "earnings_date": None,
        "stress": {"sessions": 31, "p01": -0.18, "p99": 0.18, "windows": 700},
        "options": options,
    }


def write_manage(ctx: Ctx, mk: Market) -> None:
    """The day's position evaluation, produced by manage's own `evaluate` from an invented snapshot."""
    use_this_checkout()
    from cherrypick.core import bs
    from cherrypick.manage import __main__ as manage_main
    from cherrypick.manage import pricing

    class _Args:
        snapshot = ""
        rules = None

    D = ctx.day
    rate, vol = 0.04, 0.17

    def spot_of(name: str, d: date = D) -> float:
        return mk.px(name, d)

    def occ_leg(
        under: str, root: str, exp: date, right: str, strike: float, qty: int, opened: float, mid: float
    ) -> dict:
        sym = occ(root, exp, right, strike)
        return {
            "symbol": sym,
            "underlying": under,
            "instrument_type": "Equity Option",
            "quantity": qty,
            "expiration": exp.isoformat(),
            "right": right,
            "strike": float(strike),
            "open_price": opened,
            "mid": mid,
            "bid": round(mid - 0.02, 2),
            "ask": round(mid + 0.02, 2),
            "priced": True,
            "value": round(qty * mid * 100, 2),
            "open_pl": round(qty * (mid - opened) * 100, 2),
        }

    def model(under: str, spot: float, exp: date, right: str, strike: float, iv: float, on: date) -> float:
        years = max((exp - on).days, 0) / 365.0
        return round(pricing.price(spot, float(strike), years, iv, right, rate), 2)

    def z(d: date, hhmm: str = "15:00") -> str:
        return f"{d.isoformat()}T{hhmm}:00Z"

    jan = _third_friday(D.year + 2 if D.month >= 2 else D.year + 1, 1)
    monthly = _third_friday(D.year, D.month + 1) if D.month < 12 else _third_friday(D.year + 1, 1)
    monthly_next = _third_friday(monthly.year + (monthly.month // 12), monthly.month % 12 + 1)
    this_friday = _friday_on_or_after(D + timedelta(days=1))
    underlyings: list[dict] = []
    history: dict[str, list[dict]] = {}
    metrics: dict[str, dict] = {}
    closes_hist: dict[str, dict] = {}

    def metric(name: str, **kw) -> None:
        metrics[name] = {
            "last": spot_of(name),
            "beta": 1.0,
            "corr_spy_3month": 0.9,
            "iv_rank": 31.0,
            "hv_30_day_pct": 15.0,
            **kw,
        }

    # --- AAA: a put assigned into shares, no call written yet (the wheel) -----------------------------
    aaa = spot_of("AAA")
    k_put = round(aaa * 1.05 * 2) / 2
    put_exp = D - timedelta(days=14)
    sold = put_exp - timedelta(days=28)
    put_sym = occ("AAA", put_exp, "P", k_put)
    history["AAA"] = [
        _tx(11, z(sold), "Sell to Open", put_sym, 1, 0.95, "AAA"),
        _rd(z(put_exp, "22:00"), "Assignment", "Buy to Close", put_sym, 1, "AAA"),
        _rd(
            z(put_exp, "22:00"),
            "Buy to Open",
            "Buy to Open",
            "AAA",
            100,
            "AAA",
            k_put,
            -100 * k_put,
            "Equity",
        ),
    ]
    underlyings.append(
        {
            "underlying": "AAA",
            "legs": [
                {
                    "symbol": "AAA",
                    "underlying": "AAA",
                    "instrument_type": "Equity",
                    "quantity": 100,
                    "expiration": None,
                    "open_price": k_put,
                    "mid": aaa,
                    "priced": True,
                    "value": round(100 * aaa, 2),
                    "open_pl": round(100 * (aaa - k_put), 2),
                }
            ],
        }
    )
    metric("AAA", beta=1.25, iv_rank=44.0)

    # --- BBB: a PMCC, a deep long call with a monthly call sold against it -----------------------------
    bbb = spot_of("BBB")
    entry = D - timedelta(days=60)
    e_close = round(bbb * 0.97, 2)
    k_long = round(e_close * 0.78)
    years = (jan - entry).days / 365.0
    long_price = round(bs.price(e_close, k_long, years, 0.30, "C", rate, 0.0), 2)
    long_sym = occ("BBB", jan, "C", k_long)
    old_exp = D - timedelta(days=21)
    old_short = occ("BBB", old_exp, "C", round(e_close * 1.05))
    k_short = round(bbb * 1.06)
    short_exp = this_friday + timedelta(days=14)
    short_sym = occ("BBB", short_exp, "C", k_short)
    short_price = model("BBB", bbb, short_exp, "C", k_short, 0.30, D - timedelta(days=12)) + 0.15
    history["BBB"] = [
        _tx(21, z(entry), "Buy to Open", long_sym, 1, long_price, "BBB"),
        _tx(21, z(entry), "Sell to Open", old_short, 1, 1.50, "BBB"),
        _rd(z(old_exp, "22:00"), "Expiration", "Buy to Close", old_short, 1, "BBB"),
        _tx(22, z(D - timedelta(days=12)), "Sell to Open", short_sym, 1, round(short_price, 2), "BBB"),
    ]
    long_mid = model("BBB", bbb, jan, "C", k_long, 0.30, D)
    short_mid = model("BBB", bbb, short_exp, "C", k_short, 0.30, D)
    underlyings.append(
        {
            "underlying": "BBB",
            "legs": [
                occ_leg("BBB", "BBB", jan, "C", k_long, 1, long_price, long_mid),
                occ_leg("BBB", "BBB", short_exp, "C", k_short, -1, round(short_price, 2), short_mid),
            ],
        }
    )
    metric("BBB", beta=1.15, iv_rank=38.0)
    closes_hist["BBB"] = {
        "closes": {entry.isoformat(): e_close},
        "dividends": [],
        "splits": [],
        "through": mk.prev(D).isoformat(),
    }

    # --- CCC: shares with a covered call -------------------------------------------------------------------
    ccc = spot_of("CCC")
    k_cc = round(ccc * 1.07)
    cc_exp = monthly
    cc_open = model("CCC", ccc, cc_exp, "C", k_cc, 0.34, D - timedelta(days=9)) + 0.1
    cc_sym = occ("CCC", cc_exp, "C", k_cc)
    cost = round(ccc * 0.96, 2)
    history["CCC"] = [
        _tx(31, z(D - timedelta(days=40)), "Buy to Open", "CCC", 100, cost, "CCC", "Equity"),
        _tx(32, z(D - timedelta(days=9)), "Sell to Open", cc_sym, 1, round(cc_open, 2), "CCC"),
    ]
    underlyings.append(
        {
            "underlying": "CCC",
            "legs": [
                {
                    "symbol": "CCC",
                    "underlying": "CCC",
                    "instrument_type": "Equity",
                    "quantity": 100,
                    "expiration": None,
                    "open_price": cost,
                    "mid": ccc,
                    "priced": True,
                    "value": round(100 * ccc, 2),
                    "open_pl": round(100 * (ccc - cost), 2),
                },
                occ_leg(
                    "CCC",
                    "CCC",
                    cc_exp,
                    "C",
                    k_cc,
                    -1,
                    round(cc_open, 2),
                    model("CCC", ccc, cc_exp, "C", k_cc, 0.34, D),
                ),
            ],
        }
    )
    metric("CCC", beta=0.95, iv_rank=29.0)

    # --- SPY: a put credit spread ----------------------------------------------------------------------------
    spy = spot_of("SPY")
    open_day = ctx.sessions[max(-16, -len(ctx.sessions))]
    spy_open = spot_of("SPY", open_day)
    k1, k2 = round(spy * 0.94), round(spy * 0.94) - 5
    p1o, p2o = (
        model("SPY", spy_open, monthly_next, "P", k1, vol, open_day),
        model("SPY", spy_open, monthly_next, "P", k2, vol, open_day),
    )
    history["SPY"] = [
        _tx(41, z(open_day), "Sell to Open", occ("SPY", monthly_next, "P", k1), 1, p1o, "SPY"),
        _tx(41, z(open_day), "Buy to Open", occ("SPY", monthly_next, "P", k2), 1, p2o, "SPY"),
    ]
    underlyings.append(
        {
            "underlying": "SPY",
            "legs": [
                occ_leg(
                    "SPY",
                    "SPY",
                    monthly_next,
                    "P",
                    k1,
                    -1,
                    p1o,
                    model("SPY", spy, monthly_next, "P", k1, vol, D),
                ),
                occ_leg(
                    "SPY",
                    "SPY",
                    monthly_next,
                    "P",
                    k2,
                    1,
                    p2o,
                    model("SPY", spy, monthly_next, "P", k2, vol, D),
                ),
            ],
        }
    )
    metric("SPY", beta=1.0, corr_spy_3month=1.0, iv_rank=26.0, iv_index=0.166)

    # --- QQQ: a call debit spread ------------------------------------------------------------------------------
    qqq = spot_of("QQQ")
    q_open = spot_of("QQQ", open_day)
    kq1, kq2 = round(qqq / 5) * 5, round(qqq / 5) * 5 + 20
    c1o, c2o = (
        model("QQQ", q_open, monthly_next, "C", kq1, 0.21, open_day),
        model("QQQ", q_open, monthly_next, "C", kq2, 0.21, open_day),
    )
    history["QQQ"] = [
        _tx(51, z(open_day, "16:00"), "Buy to Open", occ("QQQ", monthly_next, "C", kq1), 1, c1o, "QQQ"),
        _tx(51, z(open_day, "16:00"), "Sell to Open", occ("QQQ", monthly_next, "C", kq2), 1, c2o, "QQQ"),
    ]
    underlyings.append(
        {
            "underlying": "QQQ",
            "legs": [
                occ_leg(
                    "QQQ",
                    "QQQ",
                    monthly_next,
                    "C",
                    kq1,
                    1,
                    c1o,
                    model("QQQ", qqq, monthly_next, "C", kq1, 0.21, D),
                ),
                occ_leg(
                    "QQQ",
                    "QQQ",
                    monthly_next,
                    "C",
                    kq2,
                    -1,
                    c2o,
                    model("QQQ", qqq, monthly_next, "C", kq2, 0.21, D),
                ),
            ],
        }
    )
    metric("QQQ", beta=1.2, corr_spy_3month=0.93, iv_rank=33.0)

    # --- IWM: a cash-secured put -------------------------------------------------------------------------------
    iwm = spot_of("IWM")
    i_open = spot_of("IWM", open_day)
    ki = round(iwm * 0.93)
    ipo = model("IWM", i_open, monthly, "P", ki, 0.24, open_day)
    history["IWM"] = [
        _tx(61, z(open_day, "14:30"), "Sell to Open", occ("IWM", monthly, "P", ki), 1, ipo, "IWM")
    ]
    underlyings.append(
        {
            "underlying": "IWM",
            "legs": [
                occ_leg("IWM", "IWM", monthly, "P", ki, -1, ipo, model("IWM", iwm, monthly, "P", ki, 0.24, D))
            ],
        }
    )
    metric("IWM", beta=1.3, corr_spy_3month=0.86, iv_rank=35.0)
    metric("DDD", beta=1.4, iv_rank=51.0)
    metrics["VIX"] = {"last": mk.vix[D]}

    # --- the readings the wheel and the candidates are priced from -----------------------------------------------
    income_symbols = {
        "AAA": _income_entry(pricing, aaa, D, monthly_next, 0.38, 0.5),
        "BBB": _income_entry(pricing, bbb, D, monthly_next, 0.30, 1.0),
        "CCC": _income_entry(pricing, ccc, D, monthly_next, 0.34, 1.0),
        "DDD": _income_entry(pricing, spot_of("DDD"), D, monthly_next, 0.46, 0.5),
    }
    bands = {
        "day": D.isoformat(),
        "names": {
            "AAA": {
                "spot": aaa,
                "rows": [
                    {
                        "side": "C",
                        "expiry": monthly_next.isoformat(),
                        "bands": {
                            "fixed": {"band": round(aaa * 1.07, 2)},
                            "by_expiry": {"band": round(aaa * 1.09, 2)},
                        },
                    }
                ],
            },
        },
    }
    nlv = 148_300.0
    snapshot = {
        "session": D.isoformat(),
        "generated_at": ctx.iso(D, "10:00", 7),
        "benchmark": "SPY",
        "version": 16,
        "positions": {
            "ok": True,
            "accounts": [
                {
                    "account": ACCOUNT,
                    "designated": True,
                    "underlyings": underlyings,
                    "account_type": {"margin_or_cash": "Margin", "type_name": "Individual"},
                    "balances": {
                        "net_liquidating_value": nlv,
                        "derivative_buying_power": 61_000.0,
                        "used_derivative_buying_power": 23_400.0,
                        "maintenance_requirement": 21_000.0,
                        "cash_balance": 38_900.0,
                    },
                }
            ],
        },
        "metrics": metrics,
        "transactions": history,
        "working_orders": [],
        "income": {
            "session": D.isoformat(),
            "taken_at": ctx.iso(D, "09:55"),
            "complete": True,
            "symbols": income_symbols,
        },
        "skate_bands": bands,
        "underlying_history": closes_hist,
        "live_known_orders": {},
        "account_transactions": [],
    }
    tmp = ctx.p("state", "demo-snapshot.json")
    write_json(tmp, snapshot)
    _Args.snapshot = str(tmp)
    result = manage_main.cmd_evaluate(_Args)
    tmp.unlink()
    if not result.get("ok"):
        raise SystemExit(f"manage evaluate failed: {result}")


# ------------------------------------------------------------------------------ stream cache
def write_stream_cache(ctx: Ctx, mk: Market) -> None:
    """The shared stream cache, built through core's own `connect`: last prices, daily OHLC, status."""
    use_this_checkout()
    from cherrypick.core import streamcache

    path = ctx.p("data", "marketdata", "stream_cache.db")
    con = streamcache.connect(path)
    stamp = ctx.epoch(ctx.day, "16:00")
    names = ["SPX", "XSP", "SPY", "QQQ", "IWM", *SINGLE_NAMES]
    for name in names:
        for d in mk.days:
            prev = mk.prev(d) if d != mk.days[0] else None
            con.execute(
                "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, day_close, prev_day_close, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    name,
                    d.isoformat(),
                    mk.px(name, prev) if prev else mk.px(name, d),
                    _scaled(mk, name, "high", d),
                    _scaled(mk, name, "low", d),
                    mk.px(name, d),
                    mk.px(name, prev) if prev else None,
                    ctx.epoch(d, "16:05"),
                ),
            )
        px = mk.px(name, ctx.day)
        con.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, bid_size, ask_size, updated_at) VALUES (?,?,?,?,?,?,?)",
            (name, px - 0.01, px + 0.01, px, 100, 100, stamp),
        )
        con.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, change, volume, updated_at, event_at) VALUES (?,?,?,?,?,?)",
            (name, px, round(px - mk.px(name, mk.prev(ctx.day)), 2), 1_000_000, stamp, stamp),
        )
    for name, level in (("VIX", mk.vix[ctx.day]), ("VIX3M", round(mk.vix[ctx.day] * 1.07, 2))):
        con.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, bid_size, ask_size, updated_at) VALUES (?,?,?,?,?,?,?)",
            (name, level, level, level, 1, 1, stamp),
        )
        con.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, change, volume, updated_at, event_at) VALUES (?,?,?,?,?,?)",
            (name, level, 0.0, 0, stamp, stamp),
        )
    streamcache.upsert_status(
        con,
        pid=0,
        connected_since=ctx.iso(ctx.day, "09:15"),
        last_event_at=ctx.iso(ctx.day, "16:00"),
        subscribed_symbols=len(names) + 2,
        reconnect_count=0,
    )
    con.commit()
    con.close()


def _scaled(mk: Market, name: str, kind: str, d: date) -> float:
    table = mk.high if kind == "high" else mk.low
    if name in table:
        return table[name][d]
    return round(table["SPX"][d] / (10 if name == "XSP" else 10.03), 2)


# ------------------------------------------------------------------------------ system health
JOBS = {
    "watchdog": ("interval", "every 10 min"),
    "streamer-health": ("interval", "every 60 s in session"),
    "trade-notify": ("interval", "every 30 s"),
    "console": ("resident", "always on"),
    "meic-paper": ("interval", "every 60 s"),
    "earnings-paper": ("interval", "every 60 s"),
    "calendars-paper": ("interval", "every 60 s"),
    "contango-paper": ("interval", "every 60 s"),
    "weeklyput-paper": ("interval", "every 60 s"),
    "review-provisional": ("fixed", "16:30 ET"),
    "review-final": ("fixed", "10:15 ET"),
    "morning-factpack": ("fixed", "08:30 ET"),
    "manage-eval": ("fixed", "10:00 ET"),
    "manage-marks": ("interval", "every 60 s"),
    "suite-backup": ("fixed", "01:30 ET"),
    "log-archive": ("fixed", "03:30 ET, day 1"),
    "live-positions": ("interval", "every 60 s"),
    "gex-recorder": ("resident", "in session"),
    "streamer": ("resident", "in session"),
    "technicals-land": ("fixed", "06:15 ET"),
    "technicals-report": ("fixed", "06:30 ET"),
    "market-files": ("fixed", "07:30 ET"),
}


def refresh_live(out: Path, day: date, now: datetime | None, pid: int) -> None:
    """Write the files that say 'the suite is running right now'.

    The generation pass calls it with a fixed stamp (so the home is reproducible and its pids are
    dead); `--keepalive` calls it every few seconds with the clock and its own pid, which is what
    makes the console's System page read healthy while a screenshot is taken.
    """
    from datetime import timezone

    live = now is not None
    if now is None:
        now = datetime(day.year, day.month, day.day, 21, 10, tzinfo=timezone.utc)
    iso = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    epoch = now.timestamp()
    state = out / "state"
    state.mkdir(parents=True, exist_ok=True)
    jobs = {}
    for jid, (kind, schedule) in JOBS.items():
        resident = kind == "resident"
        jobs[jid] = {
            "kind": kind,
            "enabled": True,
            "schedule": schedule,
            "last_start": iso,
            "last_exit_code": None if resident else 0,
            "last_exit_at": None if resident else iso,
            "consecutive_failures": 0,
            "running_pid": pid if resident else None,
            "pid_started_at": epoch - 3600 if resident else None,
            "resident_state": "running" if resident else None,
            "last_fire_day": day.isoformat(),
            "next_run": None,
        }
    write_json(state / "supervisor-jobs.json", {"written_at": iso, "jobs": jobs, "derive_errors": {}})
    write_json(
        state / "supervisor.last.json",
        {
            "pid": pid,
            "ts": iso,
            "started_at": iso,
            "loop_seq": 1200,
            "jobs": len(jobs),
            "resident_children": 3,
            "rss_mb": 74.2,
        },
    )
    findings = [
        {"key": "streamer", "status": "OK", "title": "Streamer", "message": "quotes fresh, 9 symbols"},
        {
            "key": "service.gex-recorder",
            "status": "OK",
            "title": "GEX recorder",
            "message": "sampling every 15 s",
        },
        {
            "key": "supervisor.anchor",
            "status": "OK",
            "title": "Supervisor anchor",
            "message": "registered, last probe passed",
        },
    ]
    for m in ("meic", "earnings", "calendars", "contango", "weeklyput"):
        findings.append(
            {
                "key": f"{m}.heartbeat",
                "status": "OK",
                "title": f"{m} heartbeat",
                "message": "ticking on schedule",
            }
        )
        findings.append(
            {
                "key": f"{m}.ledger",
                "status": "OK",
                "title": f"{m} ledger",
                "message": "writable, integrity check passed",
            }
        )
    write_json(state / "watchdog.last.json", {"ts": iso, "overall": "OK", "findings": findings})
    write_json(state / "watchdog_state.json", {})
    for name in ("console", *("meic", "earnings", "calendars", "contango", "weeklyput")):
        (state / f"{name}.heartbeat" if name != "console" else state / "console.heartbeat").write_text(
            iso + "\n", encoding="utf-8"
        )
    for rel, content in (
        ("data/marketdata/streamer.pid", str(pid)),
        ("data/gex/recorder.pid", str(pid)),
        ("data/gex/recorder.heartbeat", iso),
    ):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content + "\n", encoding="utf-8")
    write_json(state / "service-gex-recorder.launch.json", {"stamped_at": epoch - 3600})
    write_json(state / "service-streamer.launch.json", {"stamped_at": epoch - 3600})
    stamp = epoch
    for db in [*out.glob("data/*/paper_trades.db"), *out.glob("data/gex/gex_history.db")]:
        os.utime(db, (stamp, stamp))
    if live:
        # The loops' own "last tick" stamps, so the footer chips read ticking rather than a day old.
        et = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S-05:00")
        for table, col, value, db_name in (
            ("loop_log", "loop_time", et, "meic"),
            ("loop_iterations", "ran_at", epoch, "earnings"),
            ("dc_loop_iterations", "ran_at", epoch, "calendars"),
            ("contango_loop_iterations", "ran_at", epoch, "contango"),
            ("weeklyput_loop_iterations", "ran_at", epoch, "weeklyput"),
        ):
            db = out / "data" / db_name / "paper_trades.db"
            if db.exists():
                con = sqlite3.connect(db)
                con.execute(f"UPDATE {table} SET {col}=? WHERE id=(SELECT MAX(id) FROM {table})", (value,))
                con.commit()
                con.close()
    cache = out / "data" / "marketdata" / "stream_cache.db"
    if cache.exists():
        con = sqlite3.connect(cache)
        con.execute("UPDATE stream_status SET pid=?, last_event_at=? WHERE id=1", (pid, iso))
        con.execute("UPDATE stream_quotes SET updated_at=?", (stamp,))
        con.execute("UPDATE stream_trades SET updated_at=?, event_at=?", (stamp, stamp))
        con.commit()
        con.close()
        os.utime(cache, (stamp, stamp))


def write_logs(ctx: Ctx) -> None:
    """A few plausible log lines, so System's log tabs show something quiet and healthy."""
    lines = {
        "supervisor.log": "supervisor started, 22 jobs derived\nwatchdog: OK, 0 of 13 not OK\n",
        "watchdog.log": "all checks OK\n",
        "notify.log": "[log] earnings: closed one position\n[log] contango: held, ratio below the threshold\n",
        "meic/paper_loop.log": "tick: 5 condors held to settlement, none open\n",
        "streamer/streamer.log": "connected, 9 symbols subscribed\n",
        "gex/recorder.log": "sampled 3 symbols\n",
    }
    for rel, text in lines.items():
        ctx.p("logs", *rel.split("/")).write_text(text, encoding="utf-8")


# ------------------------------------------------------------------------------ screeners
SCREEN_NAMES = {
    "AAA": "Stock",
    "BBB": "Stock",
    "CCC": "Stock",
    "DDD": "Stock",
    "SPY": "Exchange Traded Fund",
    "QQQ": "Exchange Traded Fund",
    "IWM": "Exchange Traded Fund",
}
SCREEN_IV = {"AAA": 0.38, "BBB": 0.30, "CCC": 0.34, "DDD": 0.46, "SPY": 0.16, "QQQ": 0.21, "IWM": 0.24}


def _strike_step(spot: float) -> float:
    return 0.5 if spot < 50 else 1.0 if spot < 200 else 5.0


def write_screeners(ctx: Ctx, mk: Market) -> None:
    """Skate bands, our short-put / covered-call / credit-spread lists, written in the builders' file shapes."""
    use_this_checkout()
    from cherrypick.manage import pricing

    D = ctx.day
    rate = 0.04
    expiry = _third_friday(D.year, D.month + 1) if D.month < 12 else _third_friday(D.year + 1, 1)
    expiry = _third_friday(expiry.year + (expiry.month // 12), expiry.month % 12 + 1)
    dte = (expiry - D).days
    years = dte / 365.0
    rng = ctx.rng("screeners")

    def opt(spot: float, strike: float, iv: float, right: str) -> tuple[float, float]:
        mid = pricing.price(spot, strike, years, iv, right, rate)
        g = pricing.greeks(spot, strike, years, iv, right, rate) or {}
        return round(mid, 2), round(float(g.get("delta", 0.0)), 3)

    def snap(spot: float, frac: float) -> float:
        step = _strike_step(spot)
        return round(round(spot * frac / step) * step, 2)

    # --- skate -----------------------------------------------------------------------------------------
    rows = []
    for sym, kind in SCREEN_NAMES.items():
        spot = mk.px(sym, D)
        for side in ("P", "C"):
            iv = SCREEN_IV[sym]
            strike = snap(spot, 0.92 if side == "P" else 1.08)
            mid, delta = opt(spot, strike, iv, side)
            bid, ask = round(mid - 0.03, 2), round(mid + 0.03, 2)
            band = {}
            legs = []
            for key, haircut in (("fixed", 0.07), ("by_expiry", 0.085)):
                s_strike = snap(spot, 1 - haircut if side == "P" else 1 + haircut)
                s_mid, s_delta = opt(spot, s_strike, iv, side)
                per_day = round(max(s_mid - 0.03, 0.01) / dte, 4)
                skate = round(per_day * rng.uniform(1.1, 1.8), 4)
                band[key] = {
                    "expiry": expiry.isoformat(),
                    "strike": s_strike,
                    "per_day_bid": per_day,
                    "skate": skate,
                    "monthly": expiry.isoformat(),
                    "vs_monthly": 1.0,
                }
                legs.append(
                    {
                        "band": key,
                        "role": "pick",
                        "expiry": expiry.isoformat(),
                        "days": dte,
                        "option": f"{sym}{side}{s_strike:g}",
                        "same_band": True,
                        "strike": s_strike,
                        "offset": round(abs(s_strike - spot) / spot, 4),
                        "outside_grid": False,
                        "quote": {
                            "bid": round(s_mid - 0.03, 2),
                            "ask": round(s_mid + 0.03, 2),
                            "volume": 140,
                            "open_interest": 1800,
                        },
                        "mid": s_mid,
                        "iv": iv,
                        "delta": s_delta,
                        "implied_skate": round(per_day, 4),
                        "skate": skate,
                        "skate_recent": round(skate * 0.9, 4),
                        "gap": round(skate - per_day, 4),
                        "yield_bid": round(s_mid / s_strike, 4),
                        "yield_mid": round(s_mid / s_strike, 4),
                        "per_day_bid": per_day,
                        "history_windows": 640,
                        "history_windows_recent": 120,
                        "fee_open": 1.3,
                        "net_premium_bid": round(s_mid * 100 - 1.3, 2),
                        "net_yield_bid": round(s_mid / s_strike * 0.97, 4),
                        "tested_per_cycle": 0.08,
                        "tested_per_month": 0.09,
                        "hard": True,
                        "soft": {"skate": True, "skate_recent": True, "yield": True, "all": True},
                    }
                )
            rows.append(
                {
                    "symbol": sym,
                    "side": side,
                    "spot": spot,
                    "fund": kind != "Stock",
                    "earnings": None,
                    "div_yield": 0.012 if kind != "Stock" else 0.0,
                    "stage": 1,
                    "monthly": expiry.isoformat(),
                    "best": band,
                    "legs": legs,
                }
            )
            del bid, ask, delta
    skate_doc = {
        "measured_at": ctx.iso(D, "15:20"),
        "band_day": D.isoformat(),
        "complete": True,
        "problem": None,
        "config": {
            "haircut": 0.07,
            "by_expiry": [[30, 0.06], [45, 0.075], [63, 0.085]],
            "hard": {"min_open_interest": 100, "max_spread_pct": 0.1, "max_spread_abs": 0.1},
            "soft": {"skate": 1.0, "skate_recent": 1.0, "yield": 0.01},
            "stage_two": 0,
            "month_days": 30,
        },
        "rows": rows,
    }
    write_json(
        ctx.p("data", "market-report", "screener-greeks", "skate", D.isoformat(), "1520.json"), skate_doc
    )

    # --- income lists ----------------------------------------------------------------------------------------
    def common(sym: str, spot: float, strike: float, right: str, iv: float, window: str) -> dict:
        mid, delta = opt(spot, strike, iv, right)
        bid, ask = round(mid - 0.03, 2), round(mid + 0.03, 2)
        working = round(mid - 0.25 * (ask - bid), 2)
        basis = strike if right == "P" else spot
        ret = working / basis
        return {
            "symbol": sym,
            "type": SCREEN_NAMES[sym],
            "spot": spot,
            "expiry": expiry.isoformat(),
            "dte": dte,
            "window": window,
            "strike": strike,
            "delta": delta,
            "iv": iv,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "working": working,
            "premium": round(working * 100, 2),
            "return": round(ret, 4),
            "annual": round(ret * 365 / dte, 4),
            "distance": round(abs(spot - strike) / spot, 4),
            "open_interest": 1800,
            "volume": 140,
            "passes": True,
            "quiet": False,
            "earnings": "none" if SCREEN_NAMES[sym] != "Stock" else "clear",
            "earnings_date": None,
            "earnings_estimated": False,
            "ex_dividend_in_trade": False,
            "ex_dividend": "after",
            "ex_dividend_date": None,
            "leveraged": None,
        }

    puts = [
        common(s, mk.px(s, D), snap(mk.px(s, D), 0.915), "P", SCREEN_IV[s], "45-60") for s in SCREEN_NAMES
    ]
    calls = [
        common(s, mk.px(s, D), snap(mk.px(s, D), 1.07), "C", SCREEN_IV[s], "45-60")
        for s in SCREEN_NAMES
        if s not in ("DDD",)
    ]
    spreads = []
    for sym in ("AAA", "BBB", "CCC", "SPY", "QQQ", "IWM"):
        spot = mk.px(sym, D)
        iv = SCREEN_IV[sym]
        short_k = snap(spot, 0.915)
        s_mid, s_delta = opt(spot, short_k, iv, "P")
        width = _strike_step(spot) * (5 if spot < 100 else 5)
        long_k = round(short_k - width, 2)
        l_mid, l_delta = opt(spot, long_k, iv, "P")
        credit = round(max(s_mid - l_mid - 0.04, 0.05), 2)
        price = {
            "long": long_k,
            "long_delta": l_delta,
            "width": width,
            "credit": credit,
            "credit_mid": round(s_mid - l_mid, 2),
            "credit_width": round(credit / width, 3),
            "max_loss": round(width - credit, 2),
            "credit_max_loss": round(credit / (width - credit), 3),
            "passes": True,
        }
        spreads.append(
            {
                "symbol": sym,
                "type": SCREEN_NAMES[sym],
                "spread": "put",
                "setups": [],
                "signals": [],
                "conflict": False,
                "spot": spot,
                "expiry": expiry.isoformat(),
                "dte": dte,
                "move": round(spot * iv * math.sqrt(dte / 365.0), 2),
                "short": {
                    "strike": short_k,
                    "delta": s_delta,
                    "iv": iv,
                    "bid": round(s_mid - 0.03, 2),
                    "ask": round(s_mid + 0.03, 2),
                },
                "pick": price,
                "window": "45-60",
                "widths": [{"unit": "strikes", "size": 5, **price}],
                "earnings": "clear" if SCREEN_NAMES[sym] == "Stock" else "none",
                "earnings_date": None,
                "earnings_estimated": False,
                "ex_dividend_date": None,
                "leveraged": None,
            }
        )
    income_doc = {
        "version": 3,
        "built_at": ctx.iso(D, "16:20"),
        "chain_taken_at": ctx.iso(D, "15:55"),
        "chain_complete": True,
        "chain_problem": None,
        "names": len(SCREEN_NAMES),
        "dropped": {
            "short-puts": {"thin": 2},
            "covered-calls": {"thin": 2},
            "credit-spreads": {"no_signal": 1},
        },
        "rule": {
            "short_put_delta": 0.16,
            "covered_call_delta": 0.16,
            "credit_spreads": {
                "short_delta": 0.16,
                "credit_share": [0.2, 0.4],
                "recent_sessions": 10,
                "tested": "provisional",
                "selections": {},
            },
        },
        "lists": {"short-puts": puts, "covered-calls": calls, "credit-spreads": spreads},
    }
    write_json(ctx.p("data", "technicals", "income-lists", f"{D.isoformat()}.json"), income_doc)

    # --- the signal board ---------------------------------------------------------------------------------
    tags = {
        "AAA": [
            ("stage", "Early breakout", ""),
            ("rotation", "Money rotating into the sector", "Sector one leading"),
        ],
        "BBB": [("stage", "Advancing", ""), ("scan", "Pullback to support", "pullback-20")],
        "CCC": [("fvp", "Below fair value", "")],
        "DDD": [("revision", "Estimates revised up", "two upgrades this week"), ("stage", "Basing", "")],
    }
    sectors = {"AAA": "Sector one", "BBB": "Sector two", "CCC": "Sector three", "DDD": "Sector four"}
    board_rows = [
        {
            "symbol": sym,
            "sector": sectors[sym],
            "side": "bullish",
            "count": len(items),
            "bullish": len(items),
            "bearish": 0,
            "tags": [{"method": m, "label": label, "detail": detail} for m, label, detail in items],
        }
        for sym, items in tags.items()
    ]
    board = {
        "names": len(board_rows),
        "methods": {
            "stage": {"label": "Stage", "approximate": False},
            "rotation": {"label": "Rotation", "approximate": False},
            "scan": {"label": "Scan", "approximate": False},
            "fvp": {"label": "Fair value", "approximate": True},
            "revision": {"label": "Revisions", "approximate": False},
        },
        "not_counted": [],
        "revisions_through": D.isoformat(),
        "fundamentals_snapshot": None,
        "rows": board_rows,
    }
    write_json(ctx.p("data", "technicals", "board", f"{D.isoformat()}.json"), board)


# ------------------------------------------------------------------------------ GEX
GEX_SYMBOLS = {"SPX": ("SPXW", 5.0, 0.14), "XSP": ("XSP", 1.0, 0.14), "QQQ": ("QQQ", 1.0, 0.20)}


def write_gex(ctx: Ctx, mk: Market) -> None:
    """The GEX recorder's history, and a one-expiry option chain with gamma and open interest in the stream cache."""
    use_this_checkout()
    from cherrypick.gex import regime as gex_regime
    from cherrypick.gex import service as gex_service
    from cherrypick.manage import pricing

    rng = ctx.rng("gex")
    hist_path = ctx.p("data", "gex", "gex_history.db")
    hist = sqlite3.connect(hist_path)
    gex_service._ensure_history_table(hist)
    gex_regime.ensure_tables(hist)
    cache = sqlite3.connect(ctx.p("data", "marketdata", "stream_cache.db"))
    stamp = ctx.epoch(ctx.day, "16:00")
    for sym, (root, step, iv) in GEX_SYMBOLS.items():
        for d in ctx.sessions[-8:]:
            spot0, spot1 = mk.px(sym, mk.prev(d)), mk.px(sym, d)
            for i in range(0, 79):  # 09:30 to 16:00, five-minute samples
                minutes = 570 + 5 * i
                hh, mm = divmod(minutes, 60)
                hhmm = f"{hh:02d}:{mm:02d}"
                frac = i / 78
                spot = round(spot0 + (spot1 - spot0) * frac + rng.gauss(0, spot0 * 0.0004), 2)
                ts = ctx.epoch(d, hhmm)
                hist.execute(
                    "INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES (?,?,?,?)",
                    (sym, d.isoformat(), ts, spot),
                )
                if i % 6 == 0:
                    net = round(rng.uniform(-0.8, 2.6) * 1e9, 0)
                    hist.execute(
                        "INSERT INTO gex_regime_history (symbol, trade_date, ts, spot, net_gex, net_gex_vol, zero_gamma, call_wall, put_wall, expiration) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            sym,
                            d.isoformat(),
                            ts,
                            spot,
                            net,
                            round(net * 0.4, 0),
                            round(spot * 0.985 / step) * step,
                            round(spot * 1.02 / step) * step,
                            round(spot * 0.97 / step) * step,
                            d.isoformat(),
                        ),
                    )
            hist.execute(
                "INSERT OR REPLACE INTO daily_closes (symbol, trade_date, close, recorded_at, source) VALUES (?,?,?,?,?)",
                (sym, d.isoformat(), spot1, ctx.epoch(d, "16:10"), "stream"),
            )
        # the chain on the last session's expiry
        spot = mk.px(sym, ctx.day)
        exp = ctx.day
        k = round(spot * 0.95 / step) * step
        while k <= spot * 1.05:
            for right in ("C", "P"):
                g = pricing.greeks(spot, k, 1 / 365.0, iv, right, 0.04) or {}
                gamma = float(g.get("gamma", 0.0))
                sym_id = f".{root}{exp.strftime('%y%m%d')}{right}{k:g}"
                meta = {
                    "strike_price": k,
                    "option_type": right,
                    "shares_per_contract": 100,
                    "expiration_date": exp.isoformat(),
                    "underlying_symbol": sym,
                    "streamer_symbol": sym_id,
                }
                cache.execute(
                    "INSERT OR REPLACE INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, updated_at) VALUES (?,?,?,?,?)",
                    (sym_id, exp.isoformat(), sym, json.dumps(meta), stamp),
                )
                dist = (k - spot) / spot
                base = 9000 * math.exp(-(((dist) / 0.02) ** 2)) + rng.uniform(300, 1500)
                oi = int(base * (1.4 if (right == "C" and dist > 0) or (right == "P" and dist < 0) else 0.6))
                cache.execute(
                    "INSERT OR REPLACE INTO stream_greeks (symbol, delta, gamma, theta, vega, rho, iv, price, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sym_id, g.get("delta"), gamma, g.get("theta"), g.get("vega"), 0.0, iv, 1.0, stamp),
                )
                cache.execute(
                    "INSERT OR REPLACE INTO stream_oi (symbol, open_interest, updated_at) VALUES (?,?,?)",
                    (sym_id, oi, stamp),
                )
                cache.execute(
                    "INSERT OR REPLACE INTO stream_trades (symbol, last, change, volume, updated_at, event_at) VALUES (?,?,?,?,?,?)",
                    (sym_id, 1.0, 0.0, int(oi * 0.3), stamp, stamp),
                )
            k = round(k + step, 4)
    hist.commit()
    cache.commit()
    hist.close()
    cache.close()


# ------------------------------------------------------------------------------ reports
def write_reports(ctx: Ctx) -> None:
    """The end-of-day review and the morning pack, built by those packages from this home's own files."""
    use_this_checkout()
    from cherrypick.overview import facts as ov_facts
    from cherrypick.overview import render as ov_render
    from cherrypick.review import facts as rv_facts
    from cherrypick.review import render as rv_render

    for s in ctx.sessions[-12:]:
        final = s != ctx.day
        status = rv_facts.STATUS_FINAL if final else rv_facts.STATUS_PROVISIONAL
        rv_facts.write(rv_facts.build(s.isoformat(), status=status))
        rv_render.write(s.isoformat())
    # The package declares real sector funds, futures and index legs; read the demo's own names instead.
    sy = ov_facts._symbols
    sy.SECTOR_ETFS.clear()
    sy.SECTOR_ETFS.update(
        {"AAA": "Sector one", "BBB": "Sector two", "CCC": "Sector three", "DDD": "Sector four"}
    )
    sy.PREMARKET_FUTURES.clear()
    sy.INDEX_LEGS.clear()
    sy.INDEX_LEGS.update(
        {"ndx": ("QQQ", "Nasdaq-100 proxy (QQQ ETF)"), "iwm": ("IWM", "Small caps (IWM ETF)")}
    )
    pack = ov_facts.build(ctx.day.isoformat())
    ov_facts.write(pack)
    ov_render.write(pack["session"])


def scrub(ctx: Ctx) -> None:
    """Make what the packages wrote reproducible and portable: their wall-clock stamps and the absolute
    home path they recorded become a fixed stamp and `$CHERRYPICK_HOME`."""
    import re

    fixed = ctx.iso(ctx.day, "16:30")
    stamp = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+00:00)")
    forms = {str(ctx.out), ctx.out.as_posix(), json.dumps(str(ctx.out))[1:-1]}
    for path in sorted(ctx.out.glob("data/*/*")) + sorted(ctx.out.glob("data/manage/*.json")):
        if path.suffix not in {".json", ".md"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = stamp.sub(fixed, text)
        for form in forms:
            new = new.replace(form, "$CHERRYPICK_HOME")
        if new != text:
            path.write_text(new, encoding="utf-8")


def leaks_in(root: Path) -> list[str]:
    """Files that carry this machine's checkout path or the OS username, as text or inside a database."""
    import getpass

    needles = {getpass.getuser(), str(REPO), REPO.as_posix(), json.dumps(str(REPO))[1:-1]}
    needles = {n for n in needles if len(n) >= 3}
    found = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if any(n.encode() in data or n.encode("utf-16-le") in data for n in needles):
            found.append(path.relative_to(root).as_posix())
    return found


def check_no_leaks(root: Path) -> None:
    bad = leaks_in(root)
    if bad:
        raise SystemExit(f"generation failed: the real repo path or username appears in {bad[:8]}")


def build(out: Path, seed: int, day: date, days: int) -> Ctx:
    os.environ["CHERRYPICK_HOME"] = str(out)
    if out.exists():
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("a generated demo home; see docs/demo-home.md" + chr(10), encoding="utf-8")
    ctx = Ctx(out, seed, day, days)
    mk = Market(ctx)
    write_configs(ctx)
    write_meic(ctx, mk)
    write_earnings(ctx, mk)
    write_calendars(ctx, mk)
    write_contango(ctx, mk)
    write_weeklyput(ctx, mk)
    write_manage(ctx, mk)
    write_stream_cache(ctx, mk)
    write_screeners(ctx, mk)
    write_gex(ctx, mk)
    write_reports(ctx)
    write_logs(ctx)
    scrub(ctx)
    refresh_live(out, day, None, 0)
    check_no_leaks(out)
    # @@SECTIONS@@
    return ctx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the directory to write the fake home into (emptied first)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--day", default="2026-11-06", help="the latest session, YYYY-MM-DD (a trading day)")
    ap.add_argument("--days", type=int, default=30, help="how many sessions of history")
    ap.add_argument(
        "--keepalive",
        action="store_true",
        help="do not build: keep an existing home's heartbeats fresh (with this process's pid) until interrupted",
    )
    args = ap.parse_args(argv)
    day = date.fromisoformat(args.day)
    if day.weekday() >= 5 or day in HOLIDAYS:
        ap.error("--day must be a trading day")
    out = Path(args.out).resolve()
    if out == Path.home() / ".cherrypick" or out.name == ".cherrypick":
        ap.error("refusing to write over a real home")
    if args.keepalive:
        from datetime import timezone

        print(f"keeping {out} alive (pid {os.getpid()}); Ctrl-C to stop", flush=True)
        try:
            while True:
                refresh_live(out, day, datetime.now(timezone.utc), os.getpid())
                time.sleep(15)
        except KeyboardInterrupt:
            return 0
    build(out, args.seed, day, args.days)
    print(f"demo home written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
