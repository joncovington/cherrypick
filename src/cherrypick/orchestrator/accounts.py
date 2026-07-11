"""Per-module live-trading account selection.

When a module (MEIC, earnings, …) is flipped to live, it resolves the account to trade in from its OWN
keyring secret `ACCOUNT_NUMBER` (service `meicagent` / `earningsagent`, via the shared
`cherrypick.core.auth.CredentialStore`). tastytrade returns multiple accounts per login, so an unset
`ACCOUNT_NUMBER` silently falls back to "the first account" — not a deliberate choice. This lets the user
list the login's accounts and **designate** which one a module trades in when it goes live.

Scope / safety: read-only w.r.t. positions and orders. It reads the broker's account list (`tt.py
list_accounts`) and writes the destination `ACCOUNT_NUMBER` into the module's keyring — nothing else. It
never places/cancels/closes an order, never flips `enable_live_trading`, and never edits a module's code
or config files. Account numbers are masked everywhere they surface; the full number is used only to
write the keyring value the module itself will read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cherrypick.core.auth import ACCOUNT_NUMBER, CredentialError, CredentialStore

from . import config as cfgmod
from .reconcile import _tt  # module tt.py invocation (doctor._run + first_json), reused
from .util import mask_account


def keyring_store(cfg: dict[str, Any], module: str) -> CredentialStore | None:
    """Build the shared `CredentialStore` for a module from its config-declared `keyring_service`
    (and optional read-only `keyring_legacy_services`). None when the module declares no service."""
    mcfg = cfg.get("modules", {}).get(module) or {}
    service = mcfg.get("keyring_service")
    if not service:
        return None
    legacy = tuple(mcfg.get("keyring_legacy_services") or ())
    return CredentialStore(service, legacy_service_names=legacy)


def _live_enabled(root: Path) -> bool | None:
    """Read the module's `enable_live_trading` (best-effort, read-only) so the stakes are visible."""
    for rel in ("config/config.json", "config.json"):
        p = root / rel
        if p.exists():
            try:
                return bool(json.loads(p.read_text(encoding="utf-8")).get("enable_live_trading", False))
            except (OSError, ValueError):
                return None
    return None


def _broker_accounts(root: Path) -> tuple[list[dict], str | None]:
    """The login's accounts via the module's read-only `tt.py list_accounts` — (accounts, error)."""
    payload = _tt(root, "list_accounts")
    if not payload.get("ok"):
        return [], (payload.get("error") or "list_accounts not ok")[:200]
    return payload.get("accounts") or [], None


def _designated_number(store: CredentialStore | None) -> str | None:
    if store is None:
        return None
    try:
        return store.get_secret(ACCOUNT_NUMBER)
    except CredentialError:
        return None


def _resolve(accounts: list[dict], selector: str) -> str | None:
    """Resolve a selector to a full account number. A 3-4+ digit selector is matched as an account-number
    *suffix* (last-4) when it hits exactly one account; otherwise a 1-based *index* into the list."""
    s = str(selector).strip()
    if len(s) >= 3:
        matches = [a for a in accounts if str(a.get("account_number") or "").endswith(s)]
        if len(matches) == 1:
            return matches[0].get("account_number")
        if len(matches) > 1:
            return None  # ambiguous suffix — caller reports
    if s.isdigit() and 1 <= int(s) <= len(accounts):
        return accounts[int(s) - 1].get("account_number")
    return None


def _context(cfg: dict[str, Any], module: str):
    """(mcfg, root, store) with a clean error dict when the module/service/checkout is unusable."""
    mcfg = cfg.get("modules", {}).get(module)
    if not mcfg:
        return None, None, None, {"ok": False, "error": f"unknown module {module!r}"}
    store = keyring_store(cfg, module)
    if store is None:
        return (
            None,
            None,
            None,
            {
                "ok": False,
                "error": f"module {module!r} has no 'keyring_service' configured (see config.example.json)",
            },
        )
    root = cfgmod.module_root(mcfg, module)
    if not root.exists():
        return None, None, None, {"ok": False, "error": f"module checkout not found at {root}"}
    return mcfg, root, store, None


def list_accounts(cfg: dict[str, Any], module: str) -> dict[str, Any]:
    """List the login's accounts (masked) with which one this module has designated for live trading."""
    _mcfg, root, store, err = _context(cfg, module)
    if err:
        return err
    accounts, aerr = _broker_accounts(root)
    if aerr:
        return {"ok": False, "error": aerr}
    designated_full = _designated_number(store)
    rows = [
        {
            "account": mask_account(a.get("account_number")),
            "nickname": a.get("nickname"),
            "type": a.get("account_type"),
            "designated": bool(designated_full and a.get("account_number") == designated_full),
        }
        for a in accounts
    ]
    return {
        "ok": True,
        "module": module,
        "accounts": rows,
        "designated": mask_account(designated_full) if designated_full else None,
        "live_enabled": _live_enabled(root),
    }


def set_account(cfg: dict[str, Any], module: str, selector: str) -> dict[str, Any]:
    """Designate the account this module trades in when live: write `ACCOUNT_NUMBER` to its keyring.
    The full number is used only for the write; only the masked form is returned."""
    _mcfg, root, store, err = _context(cfg, module)
    if err:
        return err
    accounts, aerr = _broker_accounts(root)
    if aerr:
        return {"ok": False, "error": aerr}
    full = _resolve(accounts, selector)
    if not full:
        return {
            "ok": False,
            "error": f"could not resolve {selector!r} to a single account (use a last-4 or a 1-based index)",
        }
    try:
        store.set_secret(ACCOUNT_NUMBER, full)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "module": module, "designated": mask_account(full)}


def clear_account(cfg: dict[str, Any], module: str) -> dict[str, Any]:
    """Unset the module's designated account (revert to the SDK's default account discovery)."""
    _mcfg, _root, store, err = _context(cfg, module)
    if err:
        return err
    try:
        store.delete_secret(ACCOUNT_NUMBER)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "module": module, "designated": None}
