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

from cherrypick.core import ledgers as _ledgers

from . import config as cfgmod
from . import doctor, report, schemas
from .util import first_json, mask_account

OK, WARN, FAIL = "ok", "warn", "fail"

# Verdicts (paper↔live isolation): the real account is flat / carries positions / couldn't be checked.
FLAT, DRIFT, UNKNOWN = "FLAT", "DRIFT", "UNKNOWN"
_VERDICT_RANK = {FLAT: 0, UNKNOWN: 1, DRIFT: 2}


# --------------------------------------------------------------------------- paper-DB open positions
# What each paper ledger holds right now, keyed by paper.trade_schema: `core.ledgers.HELD_READERS`,
# the overnight open readers with the two 0DTE schemas answered intraday. This file kept eight
# readers of its own until 2026-10-12; they had drifted from core's on what "open" excludes (a
# cancelled bwb entry counted here as a held position).
_OPEN_READERS = _ledgers.HELD_READERS


def _paper_open_positions(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-module open (unclosed) paper positions — the paper ledger's own view. Files only; this is
    context, shown alongside the broker check, never the drift trigger (it's a different ledger)."""
    out: dict[str, dict[str, Any]] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = schemas.canonical(paper.get("trade_schema", "meic_ic"))
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
    fields present, tolerant of an absent or differently-shaped dict.

    Matched with `-` and `_` treated alike (2026-09-17). The wire form is hyphenated
    (`net-liquidating-value`) but the SDK's model dumps underscored (`net_liquidating_value`), and
    the filter had matched only the hyphens -- so `reconcile` and `positions` both reported an empty
    `balances` block on every real account since the SDK started dumping models, and nothing
    downstream could tell that from an account with no balances."""
    if not isinstance(balances, dict):
        return {}
    wanted = ("buying-power", "maintenance-requirement", "net-liquidating-value", "derivative-buying-power")
    summary: dict[str, Any] = {}
    for key, val in balances.items():
        k = str(key).lower().replace("_", "-")
        if any(w in k for w in wanted):
            summary[str(key)] = val
    return summary


def _tt(root, *argv: str, tool: list[str] | None = None, login: str | None = None) -> dict[str, Any]:
    """Run a read-only broker-tool command in a module and return its parsed JSON ({} on any
    failure). `tool` is the module's config-declared argv prefix (cfgmod.broker_tool); the
    default stays the historical `src/tt.py` so existing callers are unchanged. `login` names an
    extra broker login (`--login`); `main` or None adds nothing, so the argv is today's."""
    return first_json(doctor._run(root, [*(tool or []), *cfgmod.login_argv(login), *argv], timeout=35).stdout)


# What an account is expected to hold, which decides whether its positions are drift:
#   live        a module's designated live account -- positions expected
#   managed     on a login flagged `manage` (broker.logins): hand-traded, evaluated by manage --
#               positions expected
#   paper-only  anything else: a paper-only suite should leave it flat, so a position is DRIFT
LIVE, MANAGED, PAPER_ONLY = "live", "managed", "paper-only"


def _login_kw(login: str | None) -> dict[str, str]:
    """`{"login": name}` for an extra login, else nothing: main's calls stay exactly as before."""
    return {} if login in (None, "", "main") else {"login": str(login)}


def account_key(login: str | None, masked: str | None) -> str:
    """`<login>-<last4>`: one account across logins. The masked form only, so nothing new is exposed;
    the login keeps two accounts with the same last four digits on different logins apart."""
    return f"{login or 'main'}-{str(masked or '****')[-4:]}"


def account_label(entry: dict[str, Any]) -> str:
    """How a report names an account: `****1234` on the main login (as before), `margin ****1234`
    on another."""
    login = entry.get("login") or "main"
    masked = entry.get("account") or "****"
    return masked if login == "main" else f"{login} {masked}"


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


def _account_entry(
    root,
    number: str | None,
    designated: set[str],
    tool: list[str] | None = None,
    *,
    login: str | None = None,
    label: str | None = None,
    managed: bool = False,
) -> dict[str, Any]:
    """Positions (+ best-effort balances) for one account. `number` None = the module's default account
    (used only as a fallback when `list_accounts` yields nothing). The full number is passed to the
    broker query and matched against the designated set, but only the masked form is ever returned.
    `login` is the broker login it sits on (None = main); `managed` marks a login flagged `manage`."""
    login = login or "main"
    argv = ["get_positions"] + (["--account_number", str(number)] if number else [])
    payload = _tt(root, *argv, tool=tool, **_login_kw(login))
    full = number if number is not None else payload.get("account_number")
    account = mask_account(full)
    # Designation lives on the main login only: an extra login's account is never "live" here.
    is_designated = bool(full and full in designated and login == "main")
    role = LIVE if is_designated else (MANAGED if managed else PAPER_ONLY)
    who = {
        "account": account,
        "login": login,
        "login_label": label,
        "account_key": account_key(login, account),
        "role": role,
        "designated": is_designated,
    }
    if not payload.get("ok") or "positions" not in payload:
        return {
            **who,
            "error": (payload.get("error") or "get_positions not ok")[:200],
            "open_positions": [],
            "open_count": 0,
            "balances": {},
        }
    positions, dust = drop_dust(payload.get("positions") or [])
    ai_argv = ["get_account_info"] + (["--account_number", str(number)] if number else [])
    ai = _tt(root, *ai_argv, tool=tool, **_login_kw(login))
    balances = _balances_summary(ai.get("balances")) if ai.get("ok") else {}
    return {
        **who,
        "open_positions": positions,
        "open_count": len(positions),
        # Rows left out as dust (`drop_dust`): named, so a reader can see one was there.
        "dust_dropped": [str(p.get("symbol") or "?") for p in dust],
        "balances": balances,
        "account_type": _account_type(ai),
    }


# Below this many units a position is dust, not a holding: a crypto balance left at 3e-10 coins after
# a sale is listed by the broker as an open position worth nothing, which no source can price and no
# order can close. Options and shares are whole numbers, so they are never near it.
DUST_QUANTITY = 1e-8


def _quantity(row: dict[str, Any]) -> float | None:
    try:
        return abs(float(row.get("quantity")))
    except (TypeError, ValueError):
        return None


def drop_dust(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the positions held, the dust left out). A row whose quantity cannot be read is KEPT: unknown
    is not zero, and dropping it would hide a real position."""
    kept, dust = [], []
    for row in rows:
        qty = _quantity(row) if isinstance(row, dict) else None
        (dust if qty is not None and qty < DUST_QUANTITY else kept).append(row)
    return kept, dust


def _account_type(info: dict[str, Any]) -> dict[str, Any]:
    """Margin or cash, and the broker's name for the account type ("Roth IRA", "Individual"), from
    `get_account_info`. Both null when the read failed or the tool predates them: a reader must treat
    that as unknown, never as margin."""
    ok = bool(info.get("ok"))
    return {
        "margin_or_cash": info.get("margin_or_cash") if ok else None,
        "type_name": info.get("account_type_name") if ok else None,
    }


def _query_broker(cfg: dict[str, Any], forced_module: str | None) -> dict[str, Any]:
    """Query **every** account on **every** broker login (`broker.logins`; tastytrade returns several
    accounts per login), via the first enabled module whose `tt.py` answers `get_positions` (MEIC has
    it; earnings does not). Enumerate accounts with `list_accounts`, then check positions per account —
    a leftover position could sit in any account, so a single-account check would miss it. Read-only
    broker calls; account numbers masked. Falls back to the module's default account only if
    `list_accounts` yields nothing. Main unreachable is unreachable; another login that cannot be read
    is one erroring entry beside main's accounts."""
    modules = cfgmod.enabled_modules(cfg)
    if forced_module and forced_module in modules:
        modules = {forced_module: modules[forced_module]}
    designated = _designated_numbers(cfg)
    logins = cfgmod.broker_logins(cfg)
    main = logins[0]
    found = _query_login(modules, designated, main)
    if not found.get("ok"):
        return {"reachable": False, "detail": found.get("detail")}
    entries = list(found["accounts"])
    # Every further login, through the same module first (only a broker tool that knows `--login`
    # answers; others fail and the next is tried). One login that cannot be read is a WARN on that
    # login, never the whole check: main's accounts were read.
    order = {found["module"]: modules[found["module"]], **modules}
    for login in logins[1:]:
        extra = _query_login(order, designated, login)
        if extra.get("ok"):
            entries += extra["accounts"]
        else:
            entries.append(
                {
                    "account": "****",
                    "login": login["name"],
                    "login_label": login.get("label"),
                    "account_key": account_key(login["name"], None),
                    "role": MANAGED if login.get("manage") else PAPER_ONLY,
                    "designated": False,
                    "error": f"login {login['name']}: {str(extra.get('detail'))[:180]}",
                    "open_positions": [],
                    "open_count": 0,
                    "balances": {},
                }
            )
    return {
        "reachable": True,
        "module": found["module"],
        "logins": [entry["name"] for entry in logins],
        "accounts": entries,
        "total_open": sum(e["open_count"] for e in entries),
        # Only positions in paper-only accounts count as drift; a designated live account and an
        # account on a `manage` login are expected to hold positions. (The key keeps its old name.)
        "undesignated_open": sum(e["open_count"] for e in entries if e.get("role") == PAPER_ONLY),
    }


def _query_login(
    modules: dict[str, dict[str, Any]], designated: set[str], login: dict[str, Any]
) -> dict[str, Any]:
    """Every account on one login, through the first module whose broker tool answers it:
    `{ok, module, accounts}` or `{ok: False, detail}`."""
    name_ = login["name"]
    last_err = "no positions-capable module found"
    for name, mcfg in modules.items():
        root = cfgmod.module_root(mcfg, name)
        if not root.exists():
            continue
        tool = cfgmod.broker_tool(mcfg, name)
        kw = {"login": name_, "label": login.get("label"), "managed": bool(login.get("manage"))}
        try:
            numbers = [
                a.get("account_number")
                for a in (_tt(root, "list_accounts", tool=tool, **_login_kw(name_)).get("accounts") or [])
                if a.get("account_number")
            ]
            entries = (
                [_account_entry(root, n, designated, tool=tool, **kw) for n in numbers]
                if numbers
                else [_account_entry(root, None, designated, tool=tool, **kw)]
            )
        except Exception as exc:  # noqa: BLE001 — a launch failure just means try the next module
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        # Every account erroring means this module can't answer get_positions (e.g. earnings) — try next.
        if entries and all(e.get("error") for e in entries):
            last_err = entries[0]["error"]
            continue
        return {"ok": True, "module": name, "accounts": entries}
    return {"ok": False, "detail": last_err}


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile the real broker account against the paper-only expectation (it should be flat).
    Broker-touching and read-only; never trades. Returns a structured result with a verdict."""
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
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


def _role(entry: dict[str, Any]) -> str:
    """An entry's role; one written before roles existed reads from its designated flag."""
    if entry.get("role"):
        return str(entry["role"])
    return LIVE if entry.get("designated") else PAPER_ONLY


def summary(result: dict[str, Any]) -> str:
    """The pushed message: what is wrong, in a line or two (2026-10-09). The whole report --
    paper context, separators, a 900-character traceback with interpreter paths -- stays in the log
    and in `run.py reconcile`."""
    broker = result.get("broker") or {}
    if not broker.get("reachable"):
        detail = str(broker.get("detail") or "unavailable")
        if "TimeoutExpired" in detail:
            detail = "the broker did not answer within 35 s"
        return (
            f"Could not check the real accounts: {detail[:200]}. "
            "Nothing is known to be wrong; it retries tomorrow."
        )
    lines = []
    for a in broker.get("accounts") or []:
        acct = account_label(a)
        if a.get("error"):
            lines.append(f"{acct}: could not read ({str(a['error'])[:120]})")
        elif a.get("open_positions") and _role(a) == PAPER_ONLY:
            lines.append(f"{acct}: {len(a['open_positions'])} open position(s) in a paper-only account")
    return "; ".join(lines) or "See `run.py reconcile`."


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
                lines.append(f"[WARN] account {account_label(a)}: {a['error']}")
                continue
            positions = a.get("open_positions") or []
            role = _role(a)
            expected = role in (LIVE, MANAGED)
            tag = {LIVE: " (live - expected)", MANAGED: " (managed - expected)"}.get(role, "")
            # A designated live account and a managed one are expected to hold positions: never DRIFT.
            mark = "[ OK ]" if (not positions or expected) else "[DRIFT]"
            if not positions:
                state = "is flat (no open positions)"
            elif expected:
                state = f"has {len(positions)} open position(s) (expected)"
            else:
                state = f"has {len(positions)} OPEN position(s)"
            lines.append(f"{mark} account {account_label(a)}{tag} {state}")
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
