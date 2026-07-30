"""Keyring operations for the settings surface (`cherrypick settings`).

This is the one place a bearer secret transits an orchestrator process (the documented settings
exception to the "orchestrator never sees a bearer secret" invariant): a value arrives from the
request handler, goes straight to `CredentialStore.set_secret` / `notify.secrets.set_webhook`, and is
dropped — never logged, never written to a file, never returned. Every function's return value is
status-shaped: `secrets_status()` booleans, webhook set/not-set strings, and masked account numbers.

The writable service list is DERIVED (the shared suite login plus each configured module's
`keyring_service`), never taken free-form from a client, and keys are restricted to the store's own
`ALL_SECRETS`. Legacy read-only services (e.g. `tastytrade-mcp`) show through the stores' existing
fallback chain but are never a write target. Pure keyring logic only: no HTTP here.
"""

from __future__ import annotations

from typing import Any

from cherrypick.core.auth import (
    ACCOUNT_NUMBER,
    ALL_SECRETS,
    SHARED_SERVICE,
    CredentialError,
    CredentialStore,
)

from ..notify import secrets as notify_secrets
from . import accounts
from . import config as cfgmod
from .util import mask_account


def services(cfg: dict[str, Any], store_factory=CredentialStore) -> dict[str, dict[str, Any]]:
    """The writable credential services: the shared suite login plus each configured module's own
    service. {service: {label, modules, store}} — `store` is a CredentialStore ready to use."""
    out: dict[str, dict[str, Any]] = {
        SHARED_SERVICE: {
            "label": "shared suite login",
            "modules": [],
            "store": store_factory(SHARED_SERVICE),
        }
    }
    for name, mcfg in (cfg.get("modules") or {}).items():
        if not isinstance(mcfg, dict):
            continue
        service = cfgmod.module_keyring_service(mcfg, name)
        if not service:
            continue
        if service not in out:
            legacy = tuple(mcfg.get("keyring_legacy_services") or ()) + (SHARED_SERVICE,)
            out[service] = {
                "label": f"{name} module",
                "modules": [],
                "store": store_factory(service, legacy_service_names=legacy),
            }
        out[service]["modules"].append(name)
    return out


def status(cfg: dict[str, Any], store_factory=CredentialStore) -> dict[str, Any]:
    """The full secret-free settings panel: per-service key booleans + masked account, webhook
    set/not-set, and the onboarding panel (credential source per module)."""
    svc_out: dict[str, Any] = {}
    try:
        for service, info in services(cfg, store_factory).items():
            store = info["store"]
            entry: dict[str, Any] = {
                "label": info["label"],
                "modules": info["modules"],
                "status": store.secrets_status(),
            }
            acct = store.get_secret(ACCOUNT_NUMBER)
            entry["account"] = mask_account(acct) if acct else None
            svc_out[service] = entry
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "services": svc_out,
        "webhooks": notify_secrets.status(),
        "onboarding": accounts.onboarding_status(cfg, store_factory=store_factory),
    }


def _store_for(cfg: dict[str, Any], service: str, store_factory=CredentialStore) -> CredentialStore:
    known = services(cfg, store_factory)
    if service not in known:
        raise KeyError(f"unknown or non-writable service: {service!r} (known: {sorted(known)})")
    return known[service]["store"]


def _result(service: str, key: str, store: CredentialStore) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "service": service, "key": key, "status": store.secrets_status()}
    if key == ACCOUNT_NUMBER:
        acct = store.get_secret(ACCOUNT_NUMBER)
        out["account"] = mask_account(acct) if acct else None
        out["hint"] = "for broker-verified designation use: cherrypick account --module <m> --set <last4>"
    return out


def set_secret(cfg: dict[str, Any], service: str, key: str, value: str, store_factory=CredentialStore):
    """Store one secret in one service's own namespace. The value is used exactly once, for the
    keyring write, and never appears in the return value or anywhere else."""
    if key not in ALL_SECRETS:
        return {"ok": False, "error": f"unknown secret key: {key!r} (known: {list(ALL_SECRETS)})"}
    if not isinstance(value, str) or not value.strip():
        return {"ok": False, "error": "value must be a non-empty string"}
    try:
        store = _store_for(cfg, service, store_factory)
        store.set_secret(key, value.strip())
        return _result(service, key, store)
    except (KeyError, CredentialError) as exc:
        return {"ok": False, "error": str(exc)}


def delete_secret(cfg: dict[str, Any], service: str, key: str, store_factory=CredentialStore):
    if key not in ALL_SECRETS:
        return {"ok": False, "error": f"unknown secret key: {key!r} (known: {list(ALL_SECRETS)})"}
    try:
        store = _store_for(cfg, service, store_factory)
        store.delete_secret(key)
        return _result(service, key, store)
    except (KeyError, CredentialError) as exc:
        return {"ok": False, "error": str(exc)}


def set_webhook(channel: str, url: str) -> dict[str, Any]:
    if channel not in notify_secrets.SUPPORTED:
        known = list(notify_secrets.SUPPORTED)
        return {"ok": False, "error": f"unknown channel: {channel!r} (known: {known})"}
    if not isinstance(url, str) or not url.strip().lower().startswith("https://"):
        return {"ok": False, "error": "webhook must be an https:// URL"}
    notify_secrets.set_webhook(channel, url.strip())
    return {"ok": True, "webhooks": notify_secrets.status()}


def delete_webhook(channel: str) -> dict[str, Any]:
    if channel not in notify_secrets.SUPPORTED:
        known = list(notify_secrets.SUPPORTED)
        return {"ok": False, "error": f"unknown channel: {channel!r} (known: {known})"}
    notify_secrets.delete_webhook(channel)
    return {"ok": True, "webhooks": notify_secrets.status()}
