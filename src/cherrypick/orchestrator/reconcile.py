"""`cherrypick reconcile` — paper↔live isolation guard (broker-vs-DB drift).

This umbrella is paper-only, and paper trades never hit the broker, so a naive "broker positions ==
paper DB positions" match is meaningless here. Reframed for this suite, reconciliation is a paper↔live
*isolation* check: a paper-only operation should leave the **real** broker account flat, so this queries
the real account once and flags any open positions / used buying power — catching the scary drift cases
(a module flipped to live, or a leftover/manual real position the all-paper dashboards would never show).

Like `doctor`, this is an on-demand, broker-touching diagnostic — deliberately NOT on the watchdog
reliability path (stdlib/OS-only) and NOT a file-only read surface. It is read-only w.r.t. the broker
(only `get_positions` / `get_account_info`, never an order) and w.r.t. the paper DBs, and it is advisory
only: it never trades, cancels, closes, or mutates any config. Account numbers are masked (`****1234`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config as cfgmod
from . import doctor, report
from .util import first_json, mask_account

OK, WARN, FAIL = "ok", "warn", "fail"

# Verdicts (paper↔live isolation): the real account is flat / carries positions / couldn't be checked.
FLAT, DRIFT, UNKNOWN = "FLAT", "DRIFT", "UNKNOWN"
_VERDICT_RANK = {FLAT: 0, UNKNOWN: 1, DRIFT: 2}


# --------------------------------------------------------------------------- paper-DB open positions
def _meic_open(conn) -> list[dict]:
    rows = conn.execute("SELECT symbol, risk_profile FROM ic_trades WHERE exit_time IS NULL").fetchall()
    return [{"symbol": r["symbol"], "profile": r["risk_profile"]} for r in rows]


def _earnings_open(conn) -> list[dict]:
    rows = conn.execute("SELECT symbol, profile FROM trades WHERE closed_at IS NULL").fetchall()
    return [{"symbol": r["symbol"], "profile": r["profile"]} for r in rows]


# Same registry shape as report._READERS, but for OPEN (not-yet-closed) rows, keyed by paper.trade_schema.
_OPEN_READERS = {"meic_ic": _meic_open, "earnings": _earnings_open}


def _paper_open_positions(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-module open (unclosed) paper positions — the paper ledger's own view. Files only; this is
    context, shown alongside the broker check, never the drift trigger (it's a different ledger)."""
    out: dict[str, dict[str, Any]] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = _OPEN_READERS.get(schema)
        db_path = cfgmod.module_root(mcfg, name) / paper.get("paper_db", "data/paper_trades.db")
        if reader is None:
            out[name] = {"ok": False, "reason": f"unknown schema {schema!r}"}
            continue
        if not db_path.exists():
            out[name] = {"ok": False, "reason": "paper DB not found"}
            continue
        conn = report._connect_ro(db_path)
        try:
            rows = reader(conn)
        except sqlite3.Error as exc:  # empty/uninitialized DB — never crash the check
            out[name] = {"ok": False, "reason": f"read failed: {exc}"}
            continue
        finally:
            conn.close()
        out[name] = {
            "ok": True,
            "open_count": len(rows),
            "symbols": sorted({r["symbol"] for r in rows if r.get("symbol")}),
        }
    return out


# --------------------------------------------------------------------------- broker (real account)
def _balances_summary(balances: Any) -> dict[str, Any]:
    """A small, robust view of the account balances — pull any buying-power / maintenance / net-liq
    fields present (tastytrade uses hyphenated keys), tolerant of an absent or differently-shaped dict."""
    if not isinstance(balances, dict):
        return {}
    wanted = ("buying-power", "maintenance-requirement", "net-liquidating-value", "derivative-buying-power")
    summary: dict[str, Any] = {}
    for key, val in balances.items():
        k = str(key).lower()
        if any(w in k for w in wanted):
            summary[str(key)] = val
    return summary


def _query_broker(cfg: dict[str, Any], forced_module: str | None) -> dict[str, Any]:
    """Query the real account's positions + balances once, via the first enabled module whose `tt.py`
    answers `get_positions` (MEIC has it; earnings does not). Read-only broker calls; account masked."""
    modules = cfgmod.enabled_modules(cfg)
    if forced_module and forced_module in modules:
        modules = {forced_module: modules[forced_module]}
    last_err = "no positions-capable module found"
    for name, mcfg in modules.items():
        root = cfgmod.module_root(mcfg, name)
        if not root.exists():
            continue
        try:
            r = doctor._run(root, ["src/tt.py", "get_positions"], timeout=35)
        except Exception as exc:  # noqa: BLE001 — a launch failure just means try the next module
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        payload = first_json(r.stdout)
        if not payload.get("ok") or "positions" not in payload:
            last_err = (payload.get("error") or r.stderr or r.stdout or "get_positions not ok")[:200]
            continue
        positions = payload.get("positions") or []
        balances: dict[str, Any] = {}
        try:
            ai = first_json(doctor._run(root, ["src/tt.py", "get_account_info"], timeout=35).stdout)
            balances = _balances_summary(ai.get("balances")) if ai.get("ok") else {}
        except Exception:  # noqa: BLE001 — balances are best-effort context, positions drive the verdict
            balances = {}
        return {
            "reachable": True,
            "module": name,
            "account": mask_account(payload.get("account_number")),
            "open_positions": positions,
            "balances": balances,
        }
    return {"reachable": False, "detail": last_err}


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile the real broker account against the paper-only expectation (it should be flat).
    Broker-touching and read-only; never trades. Returns a structured result with a verdict."""
    cfg = cfg or cfgmod.load_config()
    forced = cfg.get("reconcile", {}).get("broker_module")
    broker = _query_broker(cfg, forced)
    paper = _paper_open_positions(cfg)

    if not broker.get("reachable"):
        verdict = UNKNOWN
    elif broker.get("open_positions"):
        verdict = DRIFT
    else:
        verdict = FLAT

    return {
        "ok": verdict != UNKNOWN,
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "broker": broker,
        "paper": paper,
    }


def format_report(result: dict[str, Any]) -> tuple[str, int]:
    """Human-readable CLI report + a worst-rank (0 flat, 1 unknown, 2 drift) for the exit code."""
    verdict = result.get("verdict", UNKNOWN)
    broker = result.get("broker", {})
    # ASCII-only body: this prints to the terminal, which on Windows is cp1252 (can't encode ↔).
    lines = ["cherrypick reconcile - paper/live isolation", "=" * 60, f"verdict: {verdict}"]
    if not broker.get("reachable"):
        lines.append(f"[WARN] broker account could not be checked: {broker.get('detail', 'unavailable')}")
    else:
        positions = broker.get("open_positions") or []
        mark = "[ OK ]" if not positions else "[DRIFT]"
        lines.append(
            f"{mark} real account {broker.get('account', '****')} "
            + ("is flat (no open positions)" if not positions else f"has {len(positions)} OPEN position(s)")
        )
        for p in positions[:20]:
            sym = p.get("symbol") or p.get("underlying-symbol") or p.get("instrument-type") or "?"
            qty = p.get("quantity") or p.get("quantity-direction") or ""
            lines.append(f"        - {sym} {qty}".rstrip())
        if broker.get("balances"):
            lines.append(f"        buying power / balances: {broker['balances']}")
    lines.append("-" * 60)
    for name, pv in (result.get("paper") or {}).items():
        if pv.get("ok"):
            syms = ", ".join(pv.get("symbols", [])) or "-"
            lines.append(f"  paper[{name}]: {pv.get('open_count', 0)} open (context) [{syms}]")
        else:
            lines.append(f"  paper[{name}]: {pv.get('reason', 'unavailable')}")
    lines += ["=" * 60, f"Result: {verdict}"]
    return "\n".join(lines), _VERDICT_RANK.get(verdict, 1)
