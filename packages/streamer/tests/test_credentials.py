"""The credential chain must include the suite's shared broker login.

Regression for the 2026-07-29 cutover: `python -m cherrypick.core.auth migrate` moves the per-module
keyring copies into the shared `cherrypick-broker` service and DELETES the source, so any consumer whose
CredentialStore chain stops at the pre-rename legacy name silently loses its login. The streamer was the
one consumer missed when the module stores gained the shared fallback — it reconnect-looped all night on
"Missing credentials" while every module authenticated fine.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import credentials as _credentials  # noqa: E402
import daemon as _daemon  # noqa: E402


def _shared_service() -> str:
    from cherrypick.core.auth import SHARED_SERVICE

    return SHARED_SERVICE


def test_daemon_chain_reaches_shared_service():
    shared = _shared_service()
    assert shared in _daemon._LEGACY
    # Own service wins; legacy names are read-only fallbacks tried in order.
    assert _daemon._SERVICE == "meicagent"
    assert _daemon._LEGACY.index("tastytrade-mcp") < _daemon._LEGACY.index(shared)


def test_credentials_tool_uses_the_same_chain():
    store = _credentials.store()
    assert store.service_name == _daemon._SERVICE
    assert store.legacy_service_names == tuple(_daemon._LEGACY)
    assert _shared_service() in store.legacy_service_names
