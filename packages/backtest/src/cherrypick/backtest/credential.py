"""The vendor API key: in the OS keyring only, never a file or an environment variable.

This is the only module that touches the keyring (a source scan in tests/test_fence.py holds it to
that). The key is handed to the vendor client by `vendor/thetadata.py` explicitly, so the client
library never goes looking for it in the environment or a `.env` file on its own.

Where the key comes from: the vendor's user portal (for ThetaData, sign in at thetadata.net and open
the account dashboard, where the API key is shown). Store it with
`python -m cherrypick.backtest key set`.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from cherrypick.core.auth.credentials import CredentialError, CredentialStore, prompt_and_store

#: The environment variable the vendor's own tools read. Imported from once, never read afterwards.
IMPORT_ENV_VAR = {"thetadata": "THETADATA_API_KEY"}

PORTAL_HINT = {
    "thetadata": "Sign in at https://www.thetadata.net, open the account dashboard and copy the API key.",
}


def _store(settings: dict) -> tuple[CredentialStore, str]:
    cred = settings["vendor"]["credential"]
    return CredentialStore(cred["keyring_service"]), cred["key"]


def key_present(settings: dict) -> bool | None:
    """True or False, or None when the keyring cannot be read: "could not check" is not "absent"."""
    store, key = _store(settings)
    try:
        return bool(store.get_secret(key))
    except CredentialError:
        return None


def read_key(settings: dict) -> str | None:
    """The key itself. For `vendor/thetadata.py` only; nothing else may hold the value."""
    store, key = _store(settings)
    return store.get_secret(key)


def set_key(settings: dict, prompt_fn: Callable[[str], str] | None = None) -> bool:
    """Prompt (hidden) and store. A blank entry keeps the current key. Returns whether one was written."""
    store, key = _store(settings)
    kwargs = {"prompt_fn": prompt_fn} if prompt_fn else {}
    return bool(prompt_and_store(store, (key,), **kwargs))


def import_env(settings: dict, environ: dict | None = None) -> dict:
    """Copy the vendor's environment variable into the keyring once. The variable is not removed
    (a process cannot edit the user's environment); the reply says to remove it."""
    environ = os.environ if environ is None else environ
    var = IMPORT_ENV_VAR[settings["vendor"]["name"]]
    value = (environ.get(var) or "").strip()
    if not value:
        return {"ok": False, "reason": f"{var} is not set in this process's environment"}
    store, key = _store(settings)
    store.set_secret(key, value)
    return {
        "ok": True,
        "stored_in": f"keyring service {store.service_name!r}",
        "next": f"remove {var} from your environment; the suite reads the keyring only",
    }


def delete_key(settings: dict) -> None:
    store, key = _store(settings)
    store.delete_secret(key)
