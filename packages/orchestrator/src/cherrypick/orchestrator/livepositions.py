"""`run.py live-positions` -- the live accounts' positions against what the live ledgers hold.

Safeguard 2(c) from the 2026-10-08 audit. The live loops already refuse to enter beside an order
they never recorded (the orphan sweeps) and hold every submit whose outcome is unknown. Neither sees
a FILL the ledger missed -- an entry that filled after the loop gave up on it, a completion recorded
against the wrong strike, a position assigned or closed by hand -- and from then on the loop decides
from a book that is not the account's. This compares the two.

Each live module reports the contracts its ledger says are filled and open (`live_loop
--expected-legs`, files and DB only); each designated account's positions come from the broker tool
(`get_positions`, read-only, as `reconcile` reads them). Only the underlyings a live module trades are
compared, netted per account (`cherrypick.core.livepositions`). The verdict lands in a state file the
watchdog reads, so the alert travels the watchdog's normal notify path:

- IDLE      nothing armed and no ledger leg open -- the broker is not asked.
- MATCH     every compared contract agrees.
- SETTLING  a disagreement seen once: possibly the minute between a broker fill and the tick that
            records it. Not alerted.
- MISMATCH  the same disagreement on two consecutive checks. CRITICAL.
- UNKNOWN   a module or an account could not be read. WARN while something is live.

Broker-touching but read-only: it never places, cancels or changes anything. Accounts are masked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import livepositions as _lp

from . import config as cfgmod
from . import doctor, reconcile, timeutil
from .util import first_json, mask_account

LIVE_MODULES = ("flies", "bwb", "meic")
IDLE, MATCH, SETTLING, MISMATCH, UNKNOWN = "IDLE", "MATCH", "SETTLING", "MISMATCH", "UNKNOWN"
DEFAULTS = {"enabled": True, "interval_seconds": 300, "start": "09:31", "end": "16:10"}


def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(cfg.get("live_positions") or {})}


def state_path():
    return _home.state_dir() / "live_positions.last.json"


def read_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _expected(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{module: its --expected-legs payload, or {"ok": False, "error": ...}} for each enabled live
    module."""
    out: dict[str, dict[str, Any]] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if name not in LIVE_MODULES:
            continue
        root = cfgmod.module_root(mcfg, name)
        try:
            r = doctor._run(root, ["-m", f"cherrypick.{name}.live_loop", "--expected-legs"], timeout=30)
            payload = first_json(r.stdout)
        except Exception as exc:  # noqa: BLE001 -- one module failing is an UNKNOWN, not a crash
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out[name] = (
            payload if payload.get("ok") else {"ok": False, "error": payload.get("error") or "no JSON"}
        )
    return out


def _designated(cfg: dict[str, Any], module: str) -> str | None:
    from . import accounts  # local: accounts imports reconcile at load

    return accounts._designated_number(accounts.keyring_store(cfg, module))


def _broker_positions(cfg: dict[str, Any], number: str) -> dict[str, Any]:
    """`get_positions` for one account through the first module whose broker tool answers it."""
    last = "no positions-capable module"
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        root = cfgmod.module_root(mcfg, name)
        if not root.exists():
            continue
        entry = reconcile._account_entry(root, number, {number}, tool=cfgmod.broker_tool(mcfg, name))
        if not entry.get("error"):
            return {"ok": True, "positions": entry.get("open_positions") or []}
        last = entry["error"]
    return {"ok": False, "error": last}


def evaluate(
    expected: dict[str, dict[str, Any]],
    accounts_of: dict[str, str | None],
    fetch_positions,
    previous: dict[str, Any] | None,
    now_et: datetime | None = None,
) -> dict[str, Any]:
    """The whole rule, over injected reads (tests pass fakes). `accounts_of` maps module -> its
    designated account number; `fetch_positions(number)` returns `{ok, positions}`."""
    failed = {m: p.get("error") for m, p in expected.items() if not p.get("ok")}
    live = {m: p for m, p in expected.items() if p.get("ok") and (p.get("armed_today") or p.get("legs"))}
    if not live and not failed:
        return {"verdict": IDLE, "accounts": [], "modules": sorted(expected)}

    by_account: dict[str, list[str]] = {}
    unknown: list[str] = [f"{m}: {e}" for m, e in failed.items()]
    for m in live:
        number = accounts_of.get(m)
        if not number:
            unknown.append(f"{m}: no designated account")
            continue
        by_account.setdefault(number, []).append(m)

    prev_by_account = {a["account"]: a.get("diffs") or [] for a in (previous or {}).get("accounts") or []}
    results = []
    for number, modules in sorted(by_account.items()):
        masked = mask_account(number)
        broker = fetch_positions(number)
        if not broker.get("ok"):
            unknown.append(f"{masked}: {broker.get('error')}")
            results.append({"account": masked, "modules": modules, "error": broker.get("error")})
            continue
        underlyings = sorted({u for m in modules for u in live[m].get("underlyings") or []})
        exp = _lp.expected_legs(leg for m in modules for leg in live[m].get("legs") or [])
        held = _lp.broker_legs(broker["positions"], underlyings)
        when = now_et or timeutil.now_et()
        today, after_close = when.date().isoformat(), when.hour >= 16
        exp, _ = _lp.drop_expired(exp, today, after_close)
        held, expired = _lp.drop_expired(held, today, after_close)
        diffs = _lp.compare(exp, held)
        results.append(
            {
                "account": masked,
                "modules": modules,
                "underlyings": underlyings,
                "pending_orders": sum(int(live[m].get("pending") or 0) for m in modules),
                "expired_unprocessed": expired,
                "diffs": diffs,
                "confirmed": _lp.confirmed(prev_by_account.get(masked, []), diffs),
            }
        )
    if any(r.get("confirmed") for r in results):
        verdict = MISMATCH
    elif unknown:
        verdict = UNKNOWN
    elif any(r.get("diffs") for r in results):
        verdict = SETTLING
    else:
        verdict = MATCH
    return {"verdict": verdict, "accounts": results, "unknown": unknown, "modules": sorted(expected)}


# "Two checks running" means minutes apart: a verdict older than this (yesterday's last run, or one
# from before an outage) is not the previous check, and must not confirm today's first difference.
PREVIOUS_MAX_AGE_SECONDS = 15 * 60


def recent_state(now: datetime | None = None) -> dict[str, Any] | None:
    state = read_state()
    try:
        at = datetime.fromisoformat(str((state or {}).get("generated_at")))
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return state if (now - at).total_seconds() <= PREVIOUS_MAX_AGE_SECONDS else None


def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfgmod.load_config() if cfg is None else cfg
    expected = _expected(cfg)
    accounts_of = {m: _designated(cfg, m) for m in expected}
    result = evaluate(expected, accounts_of, lambda n: _broker_positions(cfg, n), recent_state())
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return result


def describe(diff: dict[str, Any]) -> str:
    """`SPX 2026-10-08 5790P: broker +1, ledger 0 (unrecorded)`."""
    strike = f"{diff['strike']:g}"
    return (
        f"{diff['underlying']} {diff['expiry']} {strike}{diff['right']}: broker {diff['broker']:+d}, "
        f"ledger {diff['expected']:+d} ({diff['kind']})"
    )
