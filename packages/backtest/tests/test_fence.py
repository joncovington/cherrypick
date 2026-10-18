"""The package's fence, enforced by reading the source rather than by trusting prose.

1. **The network is reached from one file only**, `vendor/thetadata.py`, and only by the fetch. A run
   reads the local store; a client library or socket appearing anywhere else would let a backtest
   depend on what the vendor's servers said today.
2. **The keyring is touched from one file only**, `credential.py`, so the key's value has exactly
   one way out (to the vendor client), and no other module can print, log or store it.
3. **No AI, no broker.** This package measures; it never asks a model and never reaches an account.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SRC = PACKAGE_ROOT / "src" / "cherrypick" / "backtest"

NETWORK_FILE = "vendor/thetadata.py"
KEYRING_FILE = "credential.py"

NETWORK = {
    "thetadata",
    "grpc",
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "http.client",
    "socket",
    "websocket",
    "websockets",
}
KEYRING = {"keyring", "cherrypick.core.auth"}
NEVER = {
    "anthropic",
    "openai",
    "tastytrade",
    "cherrypick.core.broker",
    "cherrypick.core.execution",
    "cherrypick.core.dxfeed",
    "cherrypick.core.streamer",
}


#: The modules a backtest replays or reads, always by subprocess: importing one would run its code in
#: this process (and MEIC's pulls the broker in transitively, which the NEVER scan cannot see).
STRATEGY = {
    "cherrypick.flies",
    "cherrypick.meic",
    "cherrypick.bwb",
    "cherrypick.earnings",
    "cherrypick.calendars",
    "cherrypick.pmcc",
    "cherrypick.curve",
    "cherrypick.contango",
    "cherrypick.gex",
    "cherrypick.advisor",
    "cherrypick.desk",
}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def _hits(names: set[str], path: Path) -> list[str]:
    return [n for n in _imports(path) if n in names or any(n.startswith(x + ".") for x in names)]


def _offenders(names: set[str], allowed: str | None, src: Path = SRC) -> list[str]:
    return [
        f"{py.relative_to(src).as_posix()}: {hit}"
        for py in sorted(src.rglob("*.py"))
        if py.relative_to(src).as_posix() != allowed
        for hit in _hits(names, py)
    ]


def test_only_the_vendor_client_reaches_the_network():
    offenders = _offenders(NETWORK, NETWORK_FILE)
    assert not offenders, f"network imports outside {NETWORK_FILE}: {offenders}"


def test_only_the_credential_module_touches_the_keyring():
    offenders = _offenders(KEYRING, KEYRING_FILE)
    assert not offenders, f"keyring access outside {KEYRING_FILE}: {offenders}"


def test_no_ai_and_no_broker_anywhere():
    offenders = _offenders(NEVER, None)
    assert not offenders, f"this package never asks a model or reaches an account: {offenders}"


def test_no_strategy_module_is_imported():
    offenders = _offenders(STRATEGY, None)
    assert not offenders, f"a module is replayed by subprocess, never imported: {offenders}"


def test_the_strategy_fence_fires_on_a_planted_import(tmp_path):
    (tmp_path / "runner.py").write_text(
        "from cherrypick.meic import paper\nimport cherrypick.flies.replay\nimport cherrypick.core.ledgers\n",
        encoding="utf-8",
    )
    assert _offenders(STRATEGY, None, tmp_path) == [
        "runner.py: cherrypick.meic",
        "runner.py: cherrypick.flies.replay",
    ]


def test_the_fence_fires_on_a_planted_import(tmp_path):
    """A guard has to be shown to fail: plant forbidden imports in a run module and watch it report,
    while the same imports in the one allowed file stay quiet."""
    (tmp_path / "vendor").mkdir()
    (tmp_path / "runner.py").write_text("import httpx\nfrom keyring import get_password\n", encoding="utf-8")
    (tmp_path / "vendor" / "thetadata.py").write_text("import thetadata\n", encoding="utf-8")
    (tmp_path / "credential.py").write_text("import keyring\n", encoding="utf-8")
    assert _offenders(NETWORK, NETWORK_FILE, tmp_path) == ["runner.py: httpx"]
    assert _offenders(KEYRING, KEYRING_FILE, tmp_path) == ["runner.py: keyring"]
