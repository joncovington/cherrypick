"""`python -m cherrypick.core.auth` — the suite's SHARED broker-credential CLI.

The onboarding redesign's entry point (docs/onboarding-redesign.md): the tastytrade login is
entered ONCE into the shared keyring service (`cherrypick-broker`), which every module's
store reads through as a fallback; per-module services remain the override/rotation layer.

This is core code run as a child process with the tty inherited — the same fencing the
per-module tools use, so an orchestrator that invokes it never sees a bearer secret; only
keyring *status* (present/absent) crosses process boundaries.

Commands:
    setup [--login NAME]        hidden-input entry of client_secret / refresh_token (blank keeps)
    status [--login NAME]       {key: present} for the login's service — never values
    remove --login NAME         delete an extra login's entries (never `main`'s)
    migrate --from-service X    move a module service's secrets INTO the shared service and
                                delete the module copies (the confirmed redesign decision), so
                                one rotation point remains. Values never leave this process.
                                A key whose shared value already exists and DIFFERS is skipped
                                and reported, never silently clobbered (--overwrite to force);
                                --keep-source copies without deleting.

`--login` names a broker login (`logins.py`): absent or `main` is the shared service, any other name
its own `cherrypick-broker.<name>` service with no fallback.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import ALL_SECRETS, REQUIRED_SECRETS, SHARED_SERVICE, CredentialStore
from . import logins as _logins


def _login(args) -> str:
    return _logins.check_login_name(getattr(args, "login", None))


def cmd_setup(args) -> dict:
    login = _login(args)
    store = _logins.login_store(login)
    for key in args.keys:
        value = getpass.getpass(f"{key} (input hidden, blank to keep current): ").strip()
        if value:
            store.set_secret(key, value)
    return {"ok": True, "login": login, "service": store.service_name, "secrets": store.secrets_status()}


def cmd_status(args) -> dict:
    login = _login(args)
    store = _logins.login_store(login)
    return {"ok": True, "login": login, "service": store.service_name, "secrets": store.secrets_status()}


def cmd_remove(args) -> dict:
    """Delete an extra login's entries. `main` is refused: it is the shared login every module reads
    through, and removing it is the privacy guide's deliberate per-service step, not a flag here."""
    login = _login(args)
    if _logins.is_default(login):
        return {"ok": False, "login": login, "error": "refusing to remove the main login here"}
    store = _logins.login_store(login)
    for key in ALL_SECRETS:
        store.delete_secret(key)
    return {"ok": True, "login": login, "service": store.service_name, "secrets": store.secrets_status()}


def cmd_migrate(args) -> dict:
    # Plain stores, no fallback chains: migrate reads exactly the named service, and a key
    # the source never held is simply absent (not pulled in from somewhere else).
    src = CredentialStore(args.from_service)
    dst = CredentialStore(SHARED_SERVICE)
    migrated, conflicts, absent = [], [], []
    for key in ALL_SECRETS:
        value = src.get_secret(key)
        if value is None:
            absent.append(key)
            continue
        existing = dst.get_secret(key)
        if existing is not None and existing != value and not args.overwrite:
            conflicts.append(key)
            continue
        dst.set_secret(key, value)
        if not args.keep_source:
            src.delete_secret(key)
        migrated.append(key)
    return {
        "ok": not conflicts,
        "from": args.from_service,
        "to": SHARED_SERVICE,
        "migrated": migrated,
        "skipped_conflicts": conflicts,  # shared holds a DIFFERENT value; use --overwrite deliberately
        "absent": absent,
        "deleted_source_copies": not args.keep_source,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.auth", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup")
    st.add_argument("--keys", nargs="+", default=list(REQUIRED_SECRETS))
    st.add_argument("--login", default=None)
    ss = sub.add_parser("status")
    ss.add_argument("--login", default=None)
    rm = sub.add_parser("remove")
    rm.add_argument("--login", required=True)
    mg = sub.add_parser("migrate")
    mg.add_argument("--from-service", required=True)
    mg.add_argument("--keep-source", action="store_true")
    mg.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    fn = {"setup": cmd_setup, "status": cmd_status, "remove": cmd_remove, "migrate": cmd_migrate}[args.cmd]
    try:
        result = fn(args)
    except _logins.LoginNameError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
