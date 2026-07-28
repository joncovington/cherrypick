#!/usr/bin/env python3
"""Flies broker credentials — OS keyring only, service `fliesagent`.

Part of the live scaffold (docs/live-trading-plan.md). The store/session pattern is MEIC's,
through the shared `cherrypick.core.auth`: OAuth secrets live in the OS keyring, never in
files/env/logs, and the orchestrator's `connect`/`account` onboarding delegates to this
module's hidden-input CLI (`keyring_service: "fliesagent"` on the orchestrator side).

CLI:
    python src/credentials.py secrets_status
    python src/credentials.py secrets_set     # prompts hidden for client_secret / refresh_token
"""

from __future__ import annotations

import getpass
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(_HERE, "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from cherrypick.core.auth import (  # noqa: E402
    ACCOUNT_NUMBER,
    CredentialError,
    CredentialStore,
    SessionManager,
)

SERVICE_NAME = "fliesagent"

store = CredentialStore(SERVICE_NAME)
sessions = SessionManager(store)


def get_session():
    """The cached tastytrade session for this module's keyring credentials."""
    return sessions.get_session()


def designated_account() -> str | None:
    """The account this module is designated to trade in when live (set via
    `cherrypick account --module flies --set <last4>`), or None."""
    try:
        return store.get_secret(ACCOUNT_NUMBER)
    except CredentialError:
        return None


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "secrets_status"
    if cmd == "secrets_status":
        print(json.dumps({"ok": True, "service": SERVICE_NAME, "secrets": store.secrets_status()}))
        return
    if cmd == "secrets_set":
        for key in ("client_secret", "refresh_token"):
            value = getpass.getpass(f"{key} (input hidden, blank to keep current): ").strip()
            if value:
                store.set_secret(key, value)
        print(json.dumps({"ok": True, "service": SERVICE_NAME, "secrets": store.secrets_status()}))
        return
    print(json.dumps({"ok": False, "error": f"unknown command {cmd!r}"}))
    sys.exit(2)


if __name__ == "__main__":
    main()
