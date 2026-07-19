"""`cherrypick reconcile` — paper↔live isolation guard (broker-vs-DB drift).

This orchestrator is paper-only, and paper trades never hit the broker, so a naive "broker positions ==
paper DB positions" match is meaningless here. Reframed for this suite, reconciliation is a paper↔live
*isolation* check: a paper-only operation should leave the **real** broker account flat, so this
enumerates **every** account on the login (tastytrade returns multiple per user) and flags any open
positions / used buying power in any of them — catching the scary drift cases (a module flipped to live,
or a leftover/manual real position the all-paper dashboards would never show).

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


def _flies_open(conn) -> list[dict]:
    rows = conn.execute("SELECT symbol, arm FROM fly_positions WHERE status = 'open'").fetchall()
    return [{"symbol": r["symbol"], "profile": r["arm"]} for r in rows]


# Same registry shape as report._READERS, but for OPEN (not-yet-closed) rows, keyed by paper.trade_schema.
_OPEN_READERS = {"meic_ic": _meic_open, "earnings": _earnings_open, "fly_book": _flies_open}


def _paper_open_positions(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-module open (unclosed) paper positions — the paper ledger's own view. Files only; this is
    context, shown alongside the broker check, never the drift trigger (it's a different ledger)."""
    out: dict[str, dict[str, Any]] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = _OPEN_READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
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


def _tt(root, *argv: str) -> dict[str, Any]:
    """Run a read-only `tt.py` command in a module and return its parsed JSON ({} on any failure)."""
    return first_json(doctor._run(root, ["src/tt.py", *argv], timeout=35).stdout)


def _designated_numbers(cfg: dict[str, Any]) -> set[str]:
    """Full account numbers each enabled module has designated for live trading (its keyring
    `ACCOUNT_NUMBER`). Those accounts are *expected* to hold positions, so the isolation guard treats
    them as expected rather than drift. Lazy import of `accounts` avoids an import cycle at load."""
    from . import accounts  # local import: accounts imports reconcile._tt at module load

    out: set[str] = set()
    for name in cfgmod.enabled_modules(cfg):
        store = accounts.keyring_store(cfg, name)
        number = accounts._designated_number(store)
        if number:
            out.add(number)
    return out


def _account_entry(root, number: str | None, designated: set[str]) -> dict[str, Any]:
    """Positions (+ best-effort balances) for one account. `number` None = the module's default account
    (used only as a fallback when `list_accounts` yields nothing). The full number is passed to the
    broker query and matched against the designated set, but only the masked form is ever returned."""
    argv = ["get_positions"] + (["--account_number", str(number)] if number else [])
    payload = _tt(root, *argv)
    full = number if number is not None else payload.get("account_number")
    account = mask_account(full)
    is_designated = bool(full and full in designated)
    if not payload.get("ok") or "positions" not in payload:
        return {
            "account": account,
            "error": (payload.get("error") or "get_positions not ok")[:200],
            "open_positions": [],
            "open_count": 0,
            "balances": {},
            "designated": is_designated,
        }
    positions = payload.get("positions") or []
    ai_argv = ["get_account_info"] + (["--account_number", str(number)] if number else [])
    ai = _tt(root, *ai_argv)
    balances = _balances_summary(ai.get("balances")) if ai.get("ok") else {}
    return {
        "account": account,
        "open_positions": positions,
        "open_count": len(positions),
        "balances": balances,
        "designated": is_designated,
    }


def _query_broker(cfg: dict[str, Any], forced_module: str | None) -> dict[str, Any]:
    """Query **every** account on the real login (tastytrade returns multiple per user), via the first
    enabled module whose `tt.py` answers `get_positions` (MEIC has it; earnings does not). Enumerate
    accounts with `list_accounts`, then check positions per account — a leftover position could sit in
    any account, so a single-account check would miss it. Read-only broker calls; account numbers masked.
    Falls back to the module's default account only if `list_accounts` yields nothing."""
    modules = cfgmod.enabled_modules(cfg)
    if forced_module and forced_module in modules:
        modules = {forced_module: modules[forced_module]}
    designated = _designated_numbers(cfg)
    last_err = "no positions-capable module found"
    for name, mcfg in modules.items():
        root = cfgmod.module_root(mcfg, name)
        if not root.exists():
            continue
        try:
            numbers = [
                a.get("account_number")
                for a in (_tt(root, "list_accounts").get("accounts") or [])
                if a.get("account_number")
            ]
            entries = (
                [_account_entry(root, n, designated) for n in numbers]
                if numbers
                else [_account_entry(root, None, designated)]
            )
        except Exception as exc:  # noqa: BLE001 — a launch failure just means try the next module
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        # Every account erroring means this module can't answer get_positions (e.g. earnings) — try next.
        if entries and all(e.get("error") for e in entries):
            last_err = entries[0]["error"]
            continue
        return {
            "reachable": True,
            "module": name,
            "accounts": entries,
            "total_open": sum(e["open_count"] for e in entries),
            # Only positions in NON-designated (paper-only) accounts count as drift; a designated live
            # account is expected to hold positions.
            "undesignated_open": sum(e["open_count"] for e in entries if not e.get("designated")),
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
    elif broker.get("undesignated_open", 0) > 0:
        verdict = DRIFT
    else:
        # FLAT even if a *designated* live account holds positions — those are expected, not drift.
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
        lines.append(f"[WARN] broker accounts could not be checked: {broker.get('detail', 'unavailable')}")
    else:
        accounts = broker.get("accounts") or []
        lines.append(f"checked {len(accounts)} real account(s):")
        for a in accounts:
            if a.get("error"):
                lines.append(f"[WARN] account {a.get('account', '****')}: {a['error']}")
                continue
            positions = a.get("open_positions") or []
            is_desig = a.get("designated")
            tag = " (live - expected)" if is_desig else ""
            # A designated live account is expected to hold positions, so it's never DRIFT.
            mark = "[ OK ]" if (not positions or is_desig) else "[DRIFT]"
            if not positions:
                state = "is flat (no open positions)"
            elif is_desig:
                state = f"has {len(positions)} open position(s) (expected)"
            else:
                state = f"has {len(positions)} OPEN position(s)"
            lines.append(f"{mark} account {a.get('account', '****')}{tag} {state}")
            for p in positions[:20]:
                sym = p.get("symbol") or p.get("underlying-symbol") or p.get("instrument-type") or "?"
                qty = p.get("quantity") or p.get("quantity-direction") or ""
                lines.append(f"        - {sym} {qty}".rstrip())
            if a.get("balances"):
                lines.append(f"        buying power / balances: {a['balances']}")
    lines.append("-" * 60)
    for name, pv in (result.get("paper") or {}).items():
        if pv.get("ok"):
            syms = ", ".join(pv.get("symbols", [])) or "-"
            lines.append(f"  paper[{name}]: {pv.get('open_count', 0)} open (context) [{syms}]")
        else:
            lines.append(f"  paper[{name}]: {pv.get('reason', 'unavailable')}")
    lines += ["=" * 60, f"Result: {verdict}"]
    return "\n".join(lines), _VERDICT_RANK.get(verdict, 1)
