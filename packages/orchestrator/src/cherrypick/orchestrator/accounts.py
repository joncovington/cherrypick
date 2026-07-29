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
    service = cfgmod.module_keyring_service(mcfg, module)
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


def _broker_accounts(root: Path, tool: list[str] | None = None) -> tuple[list[dict], str | None]:
    """The login's accounts via the module's read-only broker tool (`list_accounts`) —
    (accounts, error). `tool` is the module's config-declared argv (cfgmod.broker_tool)."""
    payload = _tt(root, "list_accounts", tool=tool)
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


def _shared_store() -> CredentialStore:
    from cherrypick.core.auth import SHARED_SERVICE
    return CredentialStore(SHARED_SERVICE)


def _first_broker_module(cfg: dict[str, Any]):
    """(name, mcfg, root, tool) for the first enabled module whose checkout exists — the probe
    the suite-wide account listing uses (any module's broker tool can enumerate the login)."""
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        root = cfgmod.module_root(mcfg, name)
        if root.exists():
            return name, mcfg, root, cfgmod.broker_tool(mcfg)
    return None, None, None, None


def list_shared(cfg: dict[str, Any]) -> dict[str, Any]:
    """The suite-wide view: the login's accounts with the SHARED designation (the default every
    module without its own designation inherits, via the store fallback chain)."""
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": "no enabled module checkout found to query the broker"}
    accounts_list, aerr = _broker_accounts(root, tool)
    if aerr:
        return {"ok": False, "error": aerr}
    designated_full = _designated_number(_shared_store())
    rows = [
        {
            "account": mask_account(a.get("account_number")),
            "nickname": a.get("nickname"),
            "type": a.get("account_type"),
            "designated": bool(designated_full and a.get("account_number") == designated_full),
        }
        for a in accounts_list
    ]
    return {"ok": True, "scope": "shared", "via_module": name, "accounts": rows,
            "designated": mask_account(designated_full) if designated_full else None}


def set_shared_account(cfg: dict[str, Any], selector: str) -> dict[str, Any]:
    """Designate the SUITE-WIDE default live-trading account (the shared service's
    account_number). Every module without its own designation inherits it; a per-module
    `account --module X --set` still overrides. Caller is responsible for human confirmation."""
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": "no enabled module checkout found to query the broker"}
    accounts_list, aerr = _broker_accounts(root, tool)
    if aerr:
        return {"ok": False, "error": aerr}
    full = _resolve(accounts_list, selector)
    if not full:
        return {"ok": False, "error": f"selector {selector!r} did not resolve to exactly one account"}
    _shared_store().set_secret(ACCOUNT_NUMBER, full)
    return {"ok": True, "scope": "shared", "designated": mask_account(full)}


def clear_shared_account() -> dict[str, Any]:
    _shared_store().delete_secret(ACCOUNT_NUMBER)
    return {"ok": True, "scope": "shared", "designated": None}


def onboarding_status(cfg: dict[str, Any], store_factory=CredentialStore) -> dict[str, Any]:
    """Per-module onboarding panel data — keyring ONLY (presence and source, never values, no
    broker). The `source` distinction (own/shared/missing) is what keeps the shared-credential
    model legible: an "own" entry overrides; "shared" means the module inherits the suite login.
    `store_factory` is injectable so tests never touch a real keyring."""
    from cherrypick.core.auth import REQUIRED_SECRETS, SHARED_SERVICE
    try:
        shared = store_factory(SHARED_SERVICE)
        shared_creds = all(shared.get_secret(k) for k in REQUIRED_SECRETS)
        shared_acct = shared.get_secret(ACCOUNT_NUMBER)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    modules = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        service = cfgmod.module_keyring_service(mcfg, name)
        if not service:
            modules.append({"module": name, "credentials": "n/a", "account": None,
                            "account_source": None})
            continue
        try:
            own = store_factory(service)  # plain store: measures the OWN layer, no fallback
            own_creds = all(own.get_secret(k) for k in REQUIRED_SECRETS)
            own_acct = own.get_secret(ACCOUNT_NUMBER)
        except CredentialError as exc:
            modules.append({"module": name, "credentials": f"error: {exc}", "account": None,
                            "account_source": None})
            continue
        acct = own_acct or shared_acct
        modules.append({
            "module": name,
            "credentials": "own" if own_creds else ("shared" if shared_creds else "missing"),
            "account": mask_account(acct) if acct else None,
            "account_source": "own" if own_acct else ("shared" if shared_acct else None),
        })
    return {"ok": True,
            "shared": {"credentials": shared_creds,
                       "account": mask_account(shared_acct) if shared_acct else None},
            "modules": modules}


def list_accounts(cfg: dict[str, Any], module: str) -> dict[str, Any]:
    """List the login's accounts (masked) with which one this module has designated for live trading."""
    _mcfg, root, store, err = _context(cfg, module)
    if err:
        return err
    accounts, aerr = _broker_accounts(root, cfgmod.broker_tool(_mcfg or {}, module))
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
    accounts, aerr = _broker_accounts(root, cfgmod.broker_tool(_mcfg or {}, module))
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
