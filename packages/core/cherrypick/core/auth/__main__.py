"""`python -m cherrypick.core.auth` — the suite's SHARED broker-credential CLI.

The onboarding redesign's entry point (docs/onboarding-redesign.md): the tastytrade login is
entered ONCE into the shared keyring service (`cherrypick-broker`), which every module's
store reads through as a fallback; per-module services remain the override/rotation layer.

This is core code run as a child process with the tty inherited — the same fencing the
per-module tools use, so an orchestrator that invokes it never sees a bearer secret; only
keyring *status* (present/absent) crosses process boundaries.

Commands:
    setup                       hidden-input entry of client_secret / refresh_token (blank keeps)
    status                      {key: present} for the shared service — never values
    migrate --from-service X    move a module service's secrets INTO the shared service and
                                delete the module copies (the confirmed redesign decision), so
                                one rotation point remains. Values never leave this process.
                                A key whose shared value already exists and DIFFERS is skipped
                                and reported, never silently clobbered (--overwrite to force);
                                --keep-source copies without deleting.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import ALL_SECRETS, REQUIRED_SECRETS, SHARED_SERVICE, CredentialStore


def cmd_setup(args) -> dict:
    store = CredentialStore(SHARED_SERVICE)
    for key in args.keys:
        value = getpass.getpass(f"{key} (input hidden, blank to keep current): ").strip()
        if value:
            store.set_secret(key, value)
    return {"ok": True, "service": SHARED_SERVICE, "secrets": store.secrets_status()}


def cmd_status(_args) -> dict:
    return {
        "ok": True,
        "service": SHARED_SERVICE,
        "secrets": CredentialStore(SHARED_SERVICE).secrets_status(),
    }


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
    sub.add_parser("status")
    mg = sub.add_parser("migrate")
    mg.add_argument("--from-service", required=True)
    mg.add_argument("--keep-source", action="store_true")
    mg.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    fn = {"setup": cmd_setup, "status": cmd_status, "migrate": cmd_migrate}[args.cmd]
    result = fn(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
