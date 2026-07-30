"""The onboarding redesign's orchestrator half: known-module defaults, the keyring-only
status panel (own/shared/missing), the yellow doctor check, and the suite wizard's flow.

No real keyring, no broker, no subprocess: stores are faked, children are stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cherrypick.core.auth import ACCOUNT_NUMBER, CLIENT_SECRET, REFRESH_TOKEN

from cherrypick.orchestrator import accounts, connect
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


class FakeStore:
    """A CredentialStore stand-in over a shared dict keyed by (service, key)."""

    data: dict = {}

    def __init__(self, service, legacy_service_names=()):
        self.service = service

    def get_secret(self, key):
        return FakeStore.data.get((self.service, key))


CFG = {
    "modules": {
        "meic": {"enabled": True},  # keyring_service via known defaults
        "flies": {"enabled": True},
        "gex": {"enabled": True},  # no service, not even a default -> n/a
    }
}


@pytest.fixture(autouse=True)
def _clean_store():
    FakeStore.data = {}
    yield
    FakeStore.data = {}


# --------------------------------------------------------------------------- known defaults
def test_known_module_defaults_apply_and_config_wins():
    assert cfgmod.module_keyring_service({}, "meic") == "meicagent"
    assert cfgmod.module_keyring_service({"keyring_service": "custom"}, "meic") == "custom"
    assert cfgmod.module_keyring_service({}, "gex") is None
    assert cfgmod.broker_tool({}, "flies") == ["src/broker_cli.py"]
    assert cfgmod.broker_tool({}, "meic") == ["src/tt.py"]
    assert cfgmod.broker_tool({"broker_tool": ["x.py"]}, "flies") == ["x.py"]
    assert cfgmod.broker_tool({}, "unknown-module") == ["src/tt.py"]


# --------------------------------------------------------------------------- status panel
def _seed_shared(creds=True, account=None):
    if creds:
        FakeStore.data[("cherrypick-broker", CLIENT_SECRET)] = "c"
        FakeStore.data[("cherrypick-broker", REFRESH_TOKEN)] = "r"
    if account:
        FakeStore.data[("cherrypick-broker", ACCOUNT_NUMBER)] = account


def test_status_distinguishes_own_shared_missing():
    _seed_shared(creds=True, account="5WT12375")
    FakeStore.data[("meicagent", CLIENT_SECRET)] = "c"
    FakeStore.data[("meicagent", REFRESH_TOKEN)] = "r"
    FakeStore.data[("meicagent", ACCOUNT_NUMBER)] = "5WT12375"
    ob = accounts.onboarding_status(CFG, store_factory=FakeStore)
    rows = {m["module"]: m for m in ob["modules"]}
    assert rows["meic"]["credentials"] == "own"
    assert rows["meic"]["account_source"] == "own"
    assert rows["flies"]["credentials"] == "shared"  # inherits the suite login
    assert rows["flies"]["account"] == "****2375" and rows["flies"]["account_source"] == "shared"
    assert rows["gex"]["credentials"] == "n/a"
    assert ob["shared"]["account"] == "****2375"
    # Presence only — no secret value anywhere in the payload.
    assert "5WT12375" not in str(ob)


def test_status_missing_when_neither_layer_has_credentials():
    ob = accounts.onboarding_status(CFG, store_factory=FakeStore)
    rows = {m["module"]: m for m in ob["modules"]}
    assert rows["meic"]["credentials"] == "missing" and rows["meic"]["account"] is None


# --------------------------------------------------------------------------- doctor: yellow
def test_doctor_onboarding_is_warn_never_fail(monkeypatch):
    from cherrypick.orchestrator import doctor

    monkeypatch.setattr(
        accounts,
        "onboarding_status",
        lambda cfg: {
            "ok": True,
            "shared": {"credentials": False, "account": None},
            "modules": [
                {"module": "meic", "credentials": "missing", "account": None, "account_source": None}
            ],
        },
    )
    checks = {c.name: c for c in doctor.run({"modules": {}}, fast=True)}
    ob = checks["onboarding"]
    # The confirmed decision: yellow, not red — paper collection runs fine without credentials.
    assert ob.status == doctor.WARN
    assert "cherrypick connect" in ob.detail


# --------------------------------------------------------------------------- wizard flow
def test_wizard_migration_offer_respects_no(monkeypatch):
    FakeStore.data[("meicagent", CLIENT_SECRET)] = "c"
    monkeypatch.setattr(connect, "subprocess", None)  # any child launch would explode
    import cherrypick.core.auth as _auth

    monkeypatch.setattr(_auth, "CredentialStore", FakeStore)
    out = connect._offer_migration(CFG, prompt_fn=lambda _: "n")
    assert out == []  # declined -> nothing launched, nothing migrated


def test_wizard_migration_runs_per_service_on_yes(monkeypatch):
    FakeStore.data[("meicagent", CLIENT_SECRET)] = "c"
    FakeStore.data[("fliesagent", REFRESH_TOKEN)] = "r"
    import cherrypick.core.auth as _auth

    monkeypatch.setattr(_auth, "CredentialStore", FakeStore)
    launched = []

    class R:
        stdout = '{"ok": true, "migrated": ["client_secret"]}'

    class FakeSub:
        @staticmethod
        def run(argv, **kw):
            launched.append(argv)
            return R()

    monkeypatch.setattr(connect, "subprocess", FakeSub)
    monkeypatch.setattr(connect, "_core_env", lambda: {})
    out = connect._offer_migration(CFG, prompt_fn=lambda _: "y")
    assert len(out) == 2
    flat = [" ".join(map(str, a)) for a in launched]
    assert any("--from-service meicagent" in f for f in flat)
    assert any("--from-service fliesagent" in f for f in flat)
    assert all("migrate" in f for f in flat)


def test_core_env_pythonpath_actually_imports_the_core_package():
    """The regression the wizard shipped with: the child's PYTHONPATH must point at the _core
    ROOT (three levels above auth/__init__.py), not at the cherrypick dir inside it — the
    stubbed-out wizard tests never exercised the real path computation."""
    import os
    import subprocess
    import sys

    env = connect._core_env()
    root = env["PYTHONPATH"].split(os.pathsep)[0]
    assert (Path(root) / "cherrypick" / "core" / "auth" / "__init__.py").exists()
    r = subprocess.run(
        [sys.executable, "-m", "cherrypick.core.auth", "status"], capture_output=True, text=True, env=env
    )
    assert r.returncode == 0 and '"service": "cherrypick-broker"' in r.stdout
