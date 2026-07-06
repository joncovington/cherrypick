"""Lazy tastytrade OAuth session management.

Unlike MEICAgent's version, this project has no persistent streamer daemon
running DXLink alongside a separate REST-polling thread, so a single
process-wide cached session (no thread-local) is sufficient -- every
invocation here is a short-lived `tt.py` subprocess.
"""

from __future__ import annotations

from tastytrade import Session

from credentials import CredentialError, get_secret, CLIENT_SECRET, REFRESH_TOKEN, missing_secrets

_session: Session | None = None


def get_session() -> Session:
    global _session
    if _session is None:
        missing = missing_secrets()
        if missing:
            raise CredentialError(
                f"Missing credentials: {', '.join(missing)}. "
                "Run `python src/tt.py secrets_set` to store them."
            )
        client_secret = get_secret(CLIENT_SECRET)
        refresh_token = get_secret(REFRESH_TOKEN)
        _session = Session(client_secret, refresh_token, is_test=False)
    return _session


def reset_session() -> None:
    global _session
    _session = None
