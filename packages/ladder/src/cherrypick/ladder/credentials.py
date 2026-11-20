"""Which account the ladder is designated to, read from the OS keyring. Presence only.

The live gates need to know whether an account is designated, and nothing else about the broker:
this module opens no session and holds no order code. The designation is the one `cherrypick
account` writes; it reads through to the suite-wide shared login, as every module's store does.
"""

from __future__ import annotations

from cherrypick.core.auth import SHARED_SERVICE, CredentialStore

SERVICE_NAME = "ladderagent"

store = CredentialStore(SERVICE_NAME, legacy_service_names=(SHARED_SERVICE,))
designated_account = store.designated_account
