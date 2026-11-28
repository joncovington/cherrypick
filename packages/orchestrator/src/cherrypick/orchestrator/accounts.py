"""Per-module live-trading account selection.

When a module (MEIC, …) is flipped to live, it resolves the account to trade in from its OWN
keyring secret `ACCOUNT_NUMBER` (service `meicagent`, via the shared
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
import subprocess
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import live as _live
from cherrypick.core.auth import ACCOUNT_NUMBER, SHARED_SERVICE, CredentialError, CredentialStore

from . import config as cfgmod
from . import doctor, timeutil
from .reconcile import _tt  # module tt.py invocation (doctor._run + first_json), reused
from .util import first_json, mask_account, read_json

PAPER_ONLY_REFUSAL = "{module} is paper-only since v0.12.0; it needs no trading account."

STATUS_TIMEOUT_S = 60


# --------------------------------------------------------------------------- the live-book guard
def module_status(cfg: dict[str, Any], module: str, login: str | None = None) -> dict[str, Any] | None:
    """The module's own `live_loop --status` JSON (files and the ledger, never the broker), or None
    when it cannot be run or does not answer `ok`. `login` reads another broker login's live
    account, as that account's own live job runs it. The orchestrator drives modules by subprocess."""
    mcfg = (cfg.get("modules") or {}).get(module) or {}
    try:
        r = doctor._run(
            cfgmod.module_root(mcfg, module),
            ["-m", f"cherrypick.{module}.live_loop", "--status", *cfgmod.login_argv(login)],
            timeout=STATUS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    payload = first_json(r.stdout)
    if not isinstance(payload, dict) or not payload or payload.get("ok") is False:
        return None
    return payload


def _count(status: dict[str, Any], *keys: str) -> int | None:
    """The first of `keys` the status carries, as a count (a list counts its length); None when none
    is there or it is null: unknown is not zero."""
    for key in keys:
        if key in status:
            value = status[key]
            if value is None:
                return None
            return len(value) if isinstance(value, (list, tuple, dict)) else int(value)
    return None


def book_reasons(status: dict[str, Any], record_date: str | None, today: str) -> list[str]:
    """Why a live account's designation may not change now, in words; empty when it is not armed
    today and holds no position and no working order. Every field is the module's own answer, and a
    missing one counts against: unknown is not empty."""
    out: list[str] = []
    if today in (record_date, status.get("armed_for")) or status.get("armed_today") is True:
        out.append("its live loop is armed for today")
    if status.get("ledger_unreadable"):
        out.append("its live ledger cannot be read")
    positions = _count(status, "open_positions", "open_lots")
    if positions is None:
        out.append("whether it holds a position is not known")
    elif positions:
        out.append(f"it holds {positions} open position(s)")
    working = _count(status, "pending_orders", "working_orders")
    if working is None:
        out.append("whether it has a working order is not known")
    elif working:
        out.append(f"it has {working} working order(s)")
    if status.get("entry_intents"):
        out.append("an entry was sent and is not yet reconciled with the broker")
    if status.get("orphaned_orders"):
        out.append("working orders at the broker are not in its ledger")
    held = status.get("broker_held", status.get("held")) or {}
    if isinstance(held, dict) and (held.get("unresolved") or held.get("unrecorded")):
        out.append("a broker submission is unresolved or unrecorded")
    return out


def live_book_refusal(
    cfg: dict[str, Any], targets: list[tuple[str, str | None]], designated: str | None
) -> dict[str, Any] | None:
    """A refusal when any of `targets` (module, login-or-None) has its live loop armed today or holds
    a position or working order, or None when every one is clear (#1513). A module with no live loop
    or no checkout has no book and is skipped. A status that cannot be read refuses: unknown is not
    flat. `designated` is the account being changed, masked in the message."""
    today = timeutil.now_et().date().isoformat()
    blockers: list[str] = []
    for module, login in targets:
        mcfg = (cfg.get("modules") or {}).get(module)
        if not mcfg or not cfgmod.has_live_path(module) or not cfgmod.module_root(mcfg, module).exists():
            continue
        where = module if login is None else f"{module} on login {login}"
        status = module_status(cfg, module, login)
        if status is None:
            blockers.append(
                f"{where}: its live status cannot be read, so whether it holds a book is not known"
            )
            continue
        key = module if login is None else f"{module}.{login}"
        blockers += [f"{where}: {why}" for why in book_reasons(status, _live.arm_record_date(key), today)]
    if not blockers:
        return None
    account = f" ({mask_account(designated)})" if designated else ""
    return {
        "ok": False,
        "error": f"the designated account{account} is left as it is: "
        + "; ".join(blockers)
        + ". Change it when the loop is disarmed and the book is flat.",
        "blockers": blockers,
    }


def _inheriting_modules(cfg: dict[str, Any]) -> list[str]:
    """The live-path modules that read the shared designation through: those with no designation of
    their own. A module whose own entry cannot be read counts as inheriting."""
    out = []
    for name, mcfg in (cfg.get("modules") or {}).items():
        service = cfgmod.module_keyring_service(mcfg, name) if cfgmod.has_live_path(name) else None
        if not service:
            continue
        try:
            own = CredentialStore(service).get_secret(ACCOUNT_NUMBER)
        except CredentialError:
            own = None
        if not own:
            out.append(name)
    return out


def keyring_store(cfg: dict[str, Any], module: str) -> CredentialStore | None:
    """Build the shared `CredentialStore` for a module from its config-declared `keyring_service`
    (plus optional read-only `keyring_legacy_services`, and always the shared suite login as the
    final fallback). None when the module declares no service.

    The shared-service fallback is unconditional, not config-driven, because every module's OWN
    credentials.py already hardcodes it the same way (`legacy_service_names=(..., SHARED_SERVICE)`)
    — that's what actually resolves at live-trading time. Before this fixed fallback, this store
    disagreed with that resolution for any module relying on the shared login rather than its own
    designated account (the common case): `cherrypick account`/the Live Ops card reported "no
    account designated" while the module would in fact trade the shared account once armed, and
    `reconcile` could raise a false DRIFT alert against that very account for the same reason."""
    mcfg = cfg.get("modules", {}).get(module) or {}
    service = cfgmod.module_keyring_service(mcfg, module)
    if not service:
        return None
    legacy = tuple(mcfg.get("keyring_legacy_services") or ())
    if SHARED_SERVICE not in legacy and service != SHARED_SERVICE:
        legacy = legacy + (SHARED_SERVICE,)
    return CredentialStore(service, legacy_service_names=legacy)


def _live_enabled(root: Path, module: str | None = None) -> bool | None:
    """Read the module's live-trading gate (best-effort, read-only) so the stakes are visible.
    Home config first (`~/.cherrypick/config/<module>.json`, where a migrated module keeps it —
    e.g. flies has no in-repo config.json at all, so checking only the repo locations always read
    as unknown for it), then the in-repo fallbacks. See `config.live_trading_enabled` for the two
    live-flag conventions checked once a document is found."""
    candidates = [_home.config_path(module)] if module else []
    candidates += [root / "config" / "config.json", root / "config.json"]
    for p in candidates:
        if p.exists():
            try:
                return cfgmod.live_trading_enabled(json.loads(p.read_text(encoding="utf-8")), module)
            except (OSError, ValueError):
                return None
    return None


def _broker_accounts(
    root: Path, tool: list[str] | None = None, login: str | None = None
) -> tuple[list[dict], str | None]:
    """The login's accounts via the module's read-only broker tool (`list_accounts`) —
    (accounts, error). `tool` is the module's config-declared argv (cfgmod.broker_tool); `login` an
    extra broker login (None = main, the argv unchanged)."""
    kw = {} if login in (None, "", "main") else {"login": login}
    payload = _tt(root, "list_accounts", tool=tool, **kw)
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


_NO_BROKER_MODULE = (
    "no enabled broker module (one with a keyring service) has a checkout to query the broker with"
)


def _first_broker_module(cfg: dict[str, Any]):
    """(name, mcfg, root, tool) for the first enabled BROKER module whose checkout exists — the
    probe the suite-wide account listing and `cherrypick connect` use (any broker module's tool can
    enumerate the login).

    A broker module is one with a keyring service. The credential-free modules (calendars, pmcc,
    curve) have none and no broker tool either: picked first, one became the probe and ran
    `-m cherrypick.<name>.tt`, a module that does not exist, and the listing failed with "list_accounts
    not ok" whenever such a module was enabled ahead of meic, flies, bwb and earnings."""
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if cfgmod.module_keyring_service(mcfg, name) is None:
            continue
        root = cfgmod.module_root(mcfg, name)
        if root.exists():
            # Every other call site passes the module name so the known-module default applies;
            # omitting it made the suite-wide listing run a bare argv for any module whose config
            # block does not declare broker_tool itself (meic ships without one) — "list_accounts
            # not ok" for every such machine, while per-module paths resolved fine.
            return name, mcfg, root, cfgmod.broker_tool(mcfg, name)
    return None, None, None, None


def list_shared(cfg: dict[str, Any]) -> dict[str, Any]:
    """The suite-wide view: the login's accounts with the SHARED designation (the default every
    module without its own designation inherits, via the store fallback chain)."""
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": _NO_BROKER_MODULE}
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
    return {
        "ok": True,
        "scope": "shared",
        "via_module": name,
        "accounts": rows,
        "designated": mask_account(designated_full) if designated_full else None,
    }


def set_shared_account(cfg: dict[str, Any], selector: str) -> dict[str, Any]:
    """Designate the SUITE-WIDE default live-trading account (the shared service's
    account_number). Every module without its own designation inherits it; a per-module
    `account --module X --set` still overrides. Caller is responsible for human confirmation.
    Refused, writing nothing, while any module that inherits it has its live loop armed today or
    holds a position or working order."""
    refusal = live_book_refusal(
        cfg, [(m, None) for m in _inheriting_modules(cfg)], _designated_number(_shared_store())
    )
    if refusal:
        return refusal
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": _NO_BROKER_MODULE}
    accounts_list, aerr = _broker_accounts(root, tool)
    if aerr:
        return {"ok": False, "error": aerr}
    full = _resolve(accounts_list, selector)
    if not full:
        return {"ok": False, "error": f"selector {selector!r} did not resolve to exactly one account"}
    _shared_store().set_secret(ACCOUNT_NUMBER, full)
    return {"ok": True, "scope": "shared", "designated": mask_account(full)}


def login_designation_store(login: str, module: str) -> CredentialStore:
    """Where `module`'s designated account on another broker login lives: that login's own keyring
    service, entry `<module>:account_number` -- what the module's live loop reads when it runs with
    `--login` (meic's `live_account.designation_store`; the two must agree)."""
    from cherrypick.core.auth import logins as _logins

    return CredentialStore(_logins.login_service(login), prefix=module)


def set_login_account(cfg: dict[str, Any], module: str, login: str, selector: str) -> dict[str, Any]:
    """Designate the account `module` trades on another broker login (`account --module <m> --login
    <name> --set <last4>`). Only a module whose own config lists that login under `live_accounts`
    may, and only an account the login itself lists. The caller is responsible for human
    confirmation; only the keyring write sees the full number."""
    from cherrypick.core.auth import logins as _logins

    if module in cfgmod.PAPER_ONLY_MODULES:
        return {"ok": False, "error": PAPER_ONLY_REFUSAL.format(module=module)}
    try:
        login = _logins.check_login_name(login)
    except _logins.LoginNameError as exc:
        return {"ok": False, "error": str(exc)}
    if login not in cfgmod.broker_login_names(cfg)[1:]:
        return {"ok": False, "error": f"login {login!r} is not in broker.logins"}
    doc = read_json(_home.config_path(module))
    if login not in (doc.get("live_accounts") or {}):
        return {"ok": False, "error": f"{module}'s config has no live_accounts.{login} entry"}
    refusal = live_book_refusal(
        cfg, [(module, login)], _designated_number(login_designation_store(login, module))
    )
    if refusal:
        return refusal
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": _NO_BROKER_MODULE}
    accounts_list, aerr = _broker_accounts(root, tool, login=login)
    if aerr:
        return {"ok": False, "error": aerr}
    full = _resolve(accounts_list, selector)
    if not full:
        return {"ok": False, "error": f"could not resolve {selector!r} to a single account on {login}"}
    try:
        login_designation_store(login, module).set_secret(ACCOUNT_NUMBER, full)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "module": module, "login": login, "designated": mask_account(full)}


def clear_login_account(cfg: dict[str, Any], module: str, login: str) -> dict[str, Any]:
    """Unset the account `module` trades on another login; refused like the set while that account
    is armed today or holds a position or working order."""
    refusal = live_book_refusal(
        cfg, [(module, login)], _designated_number(login_designation_store(login, module))
    )
    if refusal:
        return refusal
    try:
        login_designation_store(login, module).delete_secret(ACCOUNT_NUMBER)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "module": module, "login": login, "designated": None}


def list_login(cfg: dict[str, Any], login: str) -> dict[str, Any]:
    """An extra broker login's accounts, masked. Nothing is designated on it: designation stays on
    the main login, so this is a listing only (`account --login NAME`)."""
    from cherrypick.core.auth import logins as _logins

    try:
        login = _logins.check_login_name(login)
    except _logins.LoginNameError as exc:
        return {"ok": False, "error": str(exc)}
    if _logins.is_default(login):
        return list_shared(cfg)
    if login not in cfgmod.broker_login_names(cfg):
        return {
            "ok": False,
            "error": f"login {login!r} is not in broker.logins (run `connect --login {login}`)",
        }
    name, _mcfg, root, tool = _first_broker_module(cfg)
    if root is None:
        return {"ok": False, "error": _NO_BROKER_MODULE}
    accounts_list, aerr = _broker_accounts(root, tool, login=login)
    if aerr:
        return {"ok": False, "login": login, "error": aerr}
    return {
        "ok": True,
        "scope": "login",
        "login": login,
        "via_module": name,
        "accounts": [
            {
                "account": mask_account(a.get("account_number")),
                "nickname": a.get("nickname"),
                "type": a.get("account_type"),
                "designated": False,
            }
            for a in accounts_list
        ],
        "designated": None,
    }


def logins_status(cfg: dict[str, Any], store_factory=None) -> list[dict[str, Any]]:
    """Each extra login's keyring state (presence only, never values, no broker): `[{login, label,
    manage, credentials}]`, credentials `present` / `missing` / `error: ...`."""
    from cherrypick.core.auth import REQUIRED_SECRETS
    from cherrypick.core.auth import logins as _logins

    out = []
    for entry in cfgmod.broker_logins(cfg)[1:]:
        try:
            store = (
                store_factory(_logins.login_service(entry["name"]))
                if store_factory
                else _logins.login_store(entry["name"])
            )
            state = "present" if all(store.get_secret(k) for k in REQUIRED_SECRETS) else "missing"
        except CredentialError as exc:
            state = f"error: {exc}"
        out.append(
            {"login": entry["name"], "label": entry["label"], "manage": entry["manage"], "credentials": state}
        )
    return out


def clear_shared_account(cfg: dict[str, Any]) -> dict[str, Any]:
    """Unset the suite-wide designation; refused like the set while any module that inherits it is
    armed today or holds a position or working order."""
    refusal = live_book_refusal(
        cfg, [(m, None) for m in _inheriting_modules(cfg)], _designated_number(_shared_store())
    )
    if refusal:
        return refusal
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
            modules.append({"module": name, "credentials": "n/a", "account": None, "account_source": None})
            continue
        try:
            own = store_factory(service)  # plain store: measures the OWN layer, no fallback
            own_creds = all(own.get_secret(k) for k in REQUIRED_SECRETS)
            own_acct = own.get_secret(ACCOUNT_NUMBER)
        except CredentialError as exc:
            modules.append(
                {"module": name, "credentials": f"error: {exc}", "account": None, "account_source": None}
            )
            continue
        acct = own_acct or shared_acct
        modules.append(
            {
                "module": name,
                "credentials": "own" if own_creds else ("shared" if shared_creds else "missing"),
                "account": mask_account(acct) if acct else None,
                "account_source": "own" if own_acct else ("shared" if shared_acct else None),
            }
        )
    return {
        "ok": True,
        "shared": {
            "credentials": shared_creds,
            "account": mask_account(shared_acct) if shared_acct else None,
        },
        "modules": modules,
        "logins": logins_status(cfg, store_factory),
    }


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
        "live_enabled": _live_enabled(root, module),
    }


def set_account(cfg: dict[str, Any], module: str, selector: str) -> dict[str, Any]:
    """Designate the account this module trades in when live: write `ACCOUNT_NUMBER` to its keyring.
    The full number is used only for the write; only the masked form is returned. A paper-only
    module (`config.PAPER_ONLY_MODULES`) refuses and writes nothing."""
    if module in cfgmod.PAPER_ONLY_MODULES:
        return {"ok": False, "error": PAPER_ONLY_REFUSAL.format(module=module)}
    _mcfg, root, store, err = _context(cfg, module)
    if err:
        return err
    refusal = live_book_refusal(cfg, [(module, None)], _designated_number(store))
    if refusal:
        return refusal
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
    """Unset the module's designated account (revert to the SDK's default account discovery).
    Refused, writing nothing, while its live loop is armed today or it holds a position or working order."""
    _mcfg, _root, store, err = _context(cfg, module)
    if err:
        return err
    refusal = live_book_refusal(cfg, [(module, None)], _designated_number(store))
    if refusal:
        return refusal
    try:
        store.delete_secret(ACCOUNT_NUMBER)
    except CredentialError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "module": module, "designated": None}
