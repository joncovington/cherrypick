"""#1501: the console's save on the orchestrator target is held to the fields the Config page offers;
MEIC's stop and delta settings, its resting stop and its live arm's registry profile are guarded; and
MEIC cannot be switched off while one of its live accounts holds a book. Every refusal leaves the file
byte-identical, and each guard was shown to fail with it removed."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import configcli, configedit, livearm

pytestmark = pytest.mark.unit

ORCH = {
    "timezone": "America/New_York",
    "modules": {
        "meic": {"enabled": True, "path": "../meic"},
        "gex": {"enabled": True, "path": "../gex"},
    },
    "watchdog": {"interval_minutes": 10},
    "streamer": {"instances": []},
    "broker": {"logins": {"main": {"label": "Main", "keyring_service": "cherrypick-broker"}}},
    "supervisor": {"argv": ["run.py", "supervise"]},
}

MEIC = {
    "enable_live_trading": True,
    "stop_trigger_ratio": 0.95,
    "delta_target": 0.18,
    "delta_target_vix_low": 0.16,
    "symbols": ["SPX"],
    "live": {"symbol": "SPX", "arm": "pm-200", "resting_stop": {"enabled": False, "limit_offset_frac": 0.1}},
    "live_accounts": {"alt": {"enabled": True, "arm": "alt-arm"}},
}

REGISTRY = {
    "profiles": {
        "control": {"enabled": True, "stop_trigger_ratio": 0.95},
        "pm-200": {"enabled": False, "stop_trigger_ratio": 0.9, "min_iv_rank": 0.1},
        "alt-arm": {"enabled": False, "stop_trigger_ratio": 0.9},
    }
}

FLAT = {
    "date": "2026-11-27",
    "armed_for": None,
    "open_positions": 0,
    "pending_orders": [],
    "session_settled": True,
    "overdue_settlement": [],
    "broker_held": {},
    "orphaned_orders": [],
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    orch = tmp_path / "config.json"
    orch.write_text(json.dumps(ORCH, indent=2), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", orch)
    (tmp_path / "config" / "meic.json").write_text(json.dumps(MEIC, indent=2), encoding="utf-8")
    (tmp_path / "config" / "meic.risk.json").write_text(json.dumps(REGISTRY, indent=2), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "live_accounts", lambda cfg, module, module_doc=None: [])
    return tmp_path


FILES = {"orchestrator": "config.json", "meic": "config/meic.json", "meic-risk": "config/meic.risk.json"}


def save(home, target, pointer, value, status_fn=None, path="console"):
    """One edit through a write path: the console's save (`configcli`), or the settings tool's field
    edit or raw save (`configedit`), with `status_fn` standing in for MEIC's live status."""
    file = home / FILES[target]
    before = file.read_text(encoding="utf-8")
    saved = livearm.run_module_status
    livearm.run_module_status = status_fn or _never
    try:
        if path == "console":
            req = {"op": "save", "target": target, "edits": [{"pointer": pointer, "value": value}]}
            out = configcli._op_save(dict(ORCH), req)
        elif path == "field":
            out = configedit.apply_field_edit(dict(ORCH), target, pointer, value)
        else:
            text = configedit.splice_value(before, pointer, value)
            out = configedit.apply_raw_save(dict(ORCH), target, text)
    finally:
        livearm.run_module_status = saved
    if not out.get("ok"):
        assert file.read_text(encoding="utf-8") == before, "a refusal must change nothing"
    return out


def _never(cfg, login=None):
    raise AssertionError("the module's status was asked for an edit that does not switch MEIC off")


# --------------------------------------------------------------------------- the orchestrator allow-list
@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        ("/watchdog/interval_minutes", 15),
        ("/modules/gex/enabled", False),  # a pattern: every module's switch
        ("/broker/logins/main/label", "Main account"),
    ],
)
def test_a_field_the_config_page_offers_saves(home, pointer, value):
    assert save(home, "orchestrator", pointer, value)["ok"] is True


@pytest.mark.parametrize(
    "pointer",
    ["/supervisor/argv", "/modules/gex/path", "/broker/logins/main/keyring_service", "/timezone"],
)
def test_a_field_the_page_does_not_offer_is_refused(home, pointer):
    out = save(home, "orchestrator", pointer, "x")
    assert (
        out["ok"] is False and out["code"] == "guarded" and "not a field this surface edits" in out["error"]
    )


@pytest.mark.parametrize("value", [[{"login": "alt", "argv": ["cmd.exe"]}], {"login": "alt"}, ["alt", 3]])
def test_an_allowed_field_takes_no_shape_the_page_never_sends(home, value):
    """`/streamer/instances` is a string list on the page; an object under it is a crafted save."""
    out = save(home, "orchestrator", "/streamer/instances", value)
    assert out["ok"] is False and out["code"] == "guarded" and "not this shape" in out["error"]


def test_an_allowed_field_takes_the_shapes_the_page_sends(home):
    assert save(home, "orchestrator", "/streamer/instances", ["alt"])["ok"] is True
    assert save(home, "orchestrator", "/watchdog/interval_minutes", 12)["ok"] is True


def test_a_module_target_is_not_held_to_the_orchestrators_list(home):
    assert configedit.editable("meic", "/symbols") is True
    assert save(home, "meic", "/symbols", ["XSP"])["ok"] is True


def test_the_allow_list_is_exactly_what_the_config_page_offers():
    """The page and the server must not drift: a field the page offers and the server refuses is a
    broken form, and one the server takes but the page does not offer is the hole this closes."""
    meta = (
        Path(__file__).resolve().parents[2] / "console" / "web" / "src" / "pages" / "Config" / "fieldMeta.ts"
    )
    if not meta.exists():
        pytest.skip("the console is not in this checkout")
    src = meta.read_text(encoding="utf-8")
    offered = set()
    for m in re.finditer(r"\{[^{}]*?target: \"orchestrator\"[^{}]*?(?:\{[^{}]*\}[^{}]*?)?\}", src, re.S):
        block = m.group(0)
        fixed = re.search(r"pointer: \"([^\"]+)\"", block)
        dyn = re.search(r"dynamic: \{ under: \"([^\"]+)\", child: \"([^\"]+)\"", block)
        assert fixed or dyn, block
        offered.add(fixed.group(1) if fixed else f"{dyn.group(1)}/*{dyn.group(2)}")
    assert offered == set(configedit.EDITABLE["orchestrator"])


# --------------------------------------------------------------------------- MEIC's stop and delta settings
@pytest.mark.parametrize(
    "pointer", ["/stop_trigger_ratio", "/delta_target", "/delta_target_vix_low", "/live/resting_stop"]
)
def test_meics_stop_and_delta_settings_are_guarded(home, pointer):
    out = save(home, "meic", pointer, 0.5)
    assert out["ok"] is False and out["code"] == "guarded"


def test_a_key_inside_the_resting_stop_is_guarded_with_it(home):
    out = save(home, "meic", "/live/resting_stop/enabled", True)
    assert out["ok"] is False and out["code"] == "guarded" and "/live/resting_stop" in out["error"]


# --------------------------------------------------------------------------- the live arm's registry profile
@pytest.mark.parametrize("arm", ["pm-200", "alt-arm"])  # the main account's live.arm, and another login's
def test_a_live_arms_profile_is_guarded(home, arm):
    out = save(home, "meic-risk", f"/profiles/{arm}/stop_trigger_ratio", 0.5)
    assert out["ok"] is False and out["code"] == "guarded" and f"/profiles/{arm}" in out["error"]


def test_a_live_arms_paper_switch_still_saves(home):
    assert save(home, "meic-risk", "/profiles/pm-200/enabled", True)["ok"] is True


def test_an_arm_live_does_not_trade_still_saves(home):
    assert save(home, "meic-risk", "/profiles/control/stop_trigger_ratio", 0.9)["ok"] is True


def test_an_unreadable_meic_config_holds_every_profile(home):
    (home / "config" / "meic.json").write_text("{ not json", encoding="utf-8")
    out = save(home, "meic-risk", "/profiles/control/stop_trigger_ratio", 0.9)
    assert out["ok"] is False and out["code"] == "guarded"
    assert save(home, "meic-risk", "/profiles/control/enabled", False)["ok"] is True


# --------------------------------------------------------------------------- switching MEIC off
def test_meic_goes_off_when_every_live_account_is_flat_and_settled(home):
    asked = []

    def status(cfg, login=None):
        asked.append(login)
        return dict(FLAT)

    assert save(home, "orchestrator", "/modules/meic/enabled", False, status)["ok"] is True
    assert asked == [None]


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"open_positions": 2}, "2 position(s) open"),
        ({"armed_for": "2026-11-27"}, "armed for today"),
        ({"pending_orders": [{"id": 1}]}, "order(s) working"),
    ],
)
def test_meic_does_not_go_off_while_its_book_is_held(home, over, words):
    out = save(home, "orchestrator", "/modules/meic/enabled", False, lambda cfg, login=None: {**FLAT, **over})
    assert out["ok"] is False and out["code"] == "not_flat" and words in out["error"]
    assert out["blockers"] and out["pointer"] == "/modules/meic/enabled"


def test_meic_does_not_go_off_when_its_status_cannot_be_read(home):
    out = save(home, "orchestrator", "/modules/meic/enabled", False, lambda cfg, login=None: None)
    assert out["ok"] is False and out["code"] == "status_unreadable"


def test_another_logins_held_book_stops_the_switch_too(home, monkeypatch):
    monkeypatch.setattr(cfgmod, "live_accounts", lambda cfg, module, module_doc=None: ["alt"])

    def status(cfg, login=None):
        return {**FLAT, "open_positions": 1} if login == "alt" else dict(FLAT)

    out = save(home, "orchestrator", "/modules/meic/enabled", False, status)
    assert out["ok"] is False and out["code"] == "not_flat" and "login alt" in out["error"]


@pytest.mark.parametrize("path", ["field", "raw"])  # the settings tool's two write paths
def test_every_write_path_refuses_meic_off_under_a_held_book(home, path):
    held = lambda cfg, login=None: {**FLAT, "open_positions": 1}  # noqa: E731
    out = save(home, "orchestrator", "/modules/meic/enabled", False, held, path=path)
    assert out["ok"] is False and out["code"] == "not_flat"
    flat = lambda cfg, login=None: dict(FLAT)  # noqa: E731
    assert save(home, "orchestrator", "/modules/meic/enabled", False, flat, path=path)["ok"] is True


def test_removing_meics_block_is_switching_it_off(home):
    doc = json.loads((home / "config.json").read_text(encoding="utf-8"))
    del doc["modules"]["meic"]
    held = lambda cfg, login=None: {**FLAT, "open_positions": 1}  # noqa: E731
    saved = livearm.run_module_status
    livearm.run_module_status = held
    try:
        out = configedit.apply_raw_save(dict(ORCH), "orchestrator", json.dumps(doc, indent=2))
    finally:
        livearm.run_module_status = saved
    assert out["ok"] is False and out["code"] == "not_flat"


def test_switching_meic_on_never_asks_the_module(home):
    doc = json.loads((home / "config.json").read_text(encoding="utf-8"))
    doc["modules"]["meic"]["enabled"] = False
    (home / "config.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert save(home, "orchestrator", "/modules/meic/enabled", True)["ok"] is True  # _never not called


def test_another_modules_switch_never_asks_meic(home):
    assert save(home, "orchestrator", "/modules/gex/enabled", False)["ok"] is True


def test_the_status_reader_passes_a_login_through(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv

        class R:
            stdout = "{}"

        return R()

    monkeypatch.setattr(livearm.subprocess, "run", run)
    livearm.run_module_status({"modules": {"meic": {"path": "."}}}, "alt")
    assert seen["argv"][-3:] == ["--status", "--login", "alt"]
