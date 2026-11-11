"""One live account per process: the main login's, or another broker login's (`--login <name>`).

MEIC trades live in a LIVE ACCOUNT -- one (broker login, account) pair. The main login's is the top
level `live` block, as it always was. Each further login that trades has its own entry under
`live_accounts.<login>` in MEIC's config, a FULL live block of its own: its symbol, arm, caps, kill
criteria and gate-0 attestation are written for it, never inherited from `live`, because a cap carried
across from another account (another symbol's dollars, another account's size) is exactly the
"widen it once armed" failure the guarded live config exists to stop. Only the timing keys, which are
the same wall clock for every account, are taken from `live` when the entry leaves them out.

A process names its live account once, at startup (`activate`): from then on
- every live file is that account's (`paths.live_data_dir`, `paths.live_key`, `paths.live_log_name`);
- the broker session is that login's own (`core.auth.logins.login_store`, no fallback to main);
- the designated account is that login's for this module (keyring entry `meic:account_number` in the
  login's service, written by `cherrypick account --module meic --login <name> --set <last4>`);
- the config the loop reads is `config_view(cfg, login)`: `live` replaced by the entry, and
  `_live_account` recording which one, so `readiness` can require the entry's own `enabled`.

Main's process never calls any of this beyond `activate(None)`, which changes nothing.
"""

from __future__ import annotations

import copy
from typing import Any

from cherrypick.meic import paths as _paths

# Keys an entry may leave out and take from `live`: the same clock for every account. Nothing that
# sizes, selects or permits a trade is here.
TIMING_KEYS = ("disarm_time", "disarm_grace_minutes", "entry_window_start", "entry_window_end")

# Top-level keys a live account carries for itself. `account_deploy_limit_pct` is a PERCENT of this
# account's own capacity, so main's figure is a safe default for it; anything in dollars is not.
ACCOUNT_TOP_KEYS = ("account_deploy_limit_pct",)


class LiveAccountError(ValueError):
    pass


def login_name(name: str | None) -> str:
    from cherrypick.core.auth import logins as _logins

    try:
        return _logins.check_login_name(name)
    except _logins.LoginNameError as exc:
        raise LiveAccountError(str(exc)) from exc


def entries(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`live_accounts` as {login: entry}, valid login names only (`main` is the `live` block, never
    an entry)."""
    raw = cfg.get("live_accounts")
    out: dict[str, dict[str, Any]] = {}
    for name, entry in raw.items() if isinstance(raw, dict) else []:
        if str(name).startswith("_") or not isinstance(entry, dict):
            continue
        try:
            login = login_name(name)
        except LiveAccountError:
            continue
        if login != _paths.MAIN_LOGIN:
            out[login] = entry
    return out


def config_view(cfg: dict[str, Any], login: str | None) -> dict[str, Any]:
    """The config one live account's loop reads. Main: `cfg` itself. Another login: a copy with
    `live` replaced by its `live_accounts` entry (plus the timing keys it leaves out), its own
    `account_deploy_limit_pct` when it names one, and `_live_account` = {login, enabled}. A login
    with no entry raises: there is nothing for that process to trade."""
    login = login_name(login)
    if login == _paths.MAIN_LOGIN:
        return cfg
    entry = entries(cfg).get(login)
    if entry is None:
        raise LiveAccountError(f"no live_accounts.{login} entry in MEIC's config")
    view = copy.deepcopy(cfg)
    base = cfg.get("live") or {}
    live = {k: v for k, v in entry.items() if k not in ("enabled",) + ACCOUNT_TOP_KEYS}
    for key in TIMING_KEYS:
        if key not in live and key in base:
            live[key] = base[key]
    view["live"] = live
    for key in ACCOUNT_TOP_KEYS:
        if key in entry:
            view[key] = entry[key]
    view["_live_account"] = {"login": login, "enabled": entry.get("enabled") is True}
    return view


def designation_store(login: str):
    """Where this module's designated account on `login` lives: the login's own keyring service,
    entry `meic:account_number` -- beside the login's secrets, apart from any other module's."""
    from cherrypick.core.auth import CredentialStore
    from cherrypick.core.auth import logins as _logins

    return CredentialStore(_logins.login_service(login), prefix="meic")


def activate(login: str | None) -> str:
    """Make this process the live account of `login` (None or `main`: the main login, a no-op).
    Rebinds the paths, the credentials the loop reads its designation from, and the broker session,
    so every caller that looks them up at call time -- all of them in the live path -- gets this
    account's. Returns the login in force."""
    login = login_name(login)
    _paths.set_live_login(login)
    if login == _paths.MAIN_LOGIN:
        return login
    from types import SimpleNamespace

    from cherrypick.core.auth import SessionManager
    from cherrypick.core.auth import logins as _logins

    from cherrypick.meic import credentials as _creds
    from cherrypick.meic import db as _db
    from cherrypick.meic import session as _session

    secrets = _logins.login_store(login)
    designation = designation_store(login)
    # The loop reads `_creds.store.designated_account()`; the order path's other reads stay on the
    # login's own secrets. Nothing here can fall back to main's login.
    _creds.store = SimpleNamespace(
        designated_account=designation.designated_account,
        get_secret=secrets.get_secret,
        secrets_present=secrets.secrets_present,
        service_name=secrets.service_name,
    )
    manager = SessionManager(secrets, thread_local=True)
    _session._manager = manager
    _session.get_session = manager.get_session
    _session.reset_session = manager.reset_session
    _db._DB_PATH = str(_paths.live_db_path())
    return login


def enabled_problem(cfg: dict[str, Any]) -> str | None:
    """Why a non-main live account's view may not act, or None: its own `enabled` must be true (the
    module's `enable_live_trading` stays the master switch over every account)."""
    acct = cfg.get("_live_account")
    if not acct:
        return None
    if not acct.get("enabled"):
        return f"live_accounts.{acct.get('login')}.enabled is not true"
    return None
