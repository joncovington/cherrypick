"""scripts/boundary_2026_11_24.py against a throwaway home: dry run writes nothing, --apply adds a twin per
arm and retires live-shadow in the registry's own hand format, journals the breaks, and a second run finds
nothing to do."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "boundary_2026_11_24.py"


def _load():
    spec = importlib.util.spec_from_file_location("boundary_2026_11_24", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Hand-formatted on purpose: CRLF, an inline object, and a brace inside a note.
HAND = (
    '{\r\n  "_description": "arms {see notes}",\r\n  "active_profile": "control",\r\n  "profiles": {\r\n'
    '    "control": { "enabled": true, "min_iv_rank": 0.0 },\r\n'
    '    "live-shadow": {\r\n      "enabled": true,\r\n      "follows_live": true,\r\n'
    '      "_note": "follows live {always}"\r\n    },\r\n'
    '    "pm-200": { "enabled": false, "min_iv_rank": 0.1 },\r\n'
    '    "pm-1400": { "enabled": false, "min_iv_rank": 0.2 }\r\n  }\r\n}\r\n'
)


def _home(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "meic.risk.json").write_bytes(HAND.encode("utf-8"))
    ledger = tmp_path / "data" / "meic"
    ledger.mkdir(parents=True)
    conn = sqlite3.connect(str(ledger / "paper_trades.db"))
    conn.execute(
        "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY AUTOINCREMENT, break_date TEXT NOT NULL, "
        "scope TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL, "
        "UNIQUE (break_date, scope, kind))"
    )
    conn.commit()
    conn.close()
    return tmp_path


def _registry(home):
    return (home / "config" / "meic.risk.json").read_bytes().decode("utf-8")


def _breaks(home):
    conn = sqlite3.connect(str(home / "data" / "meic" / "paper_trades.db"))
    try:
        return sorted(conn.execute("SELECT scope, kind FROM measurement_breaks").fetchall())
    finally:
        conn.close()


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    mod = _load()
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert mod.main(["--arms", "pm-200", "--date", "2026-11-24"]) == 0
    assert _registry(home) == HAND
    assert _breaks(home) == []
    assert "dry run" in capsys.readouterr().out


def test_apply_adds_a_twin_per_arm_retires_live_shadow_and_is_idempotent(tmp_path, monkeypatch):
    mod = _load()
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert mod.main(["--arms", "pm-200", "pm-1400", "--apply", "--date", "2026-11-24"]) == 0

    text = _registry(home)
    doc = json.loads(text)["profiles"]
    assert doc["live-shadow-pm-200"] == {
        "enabled": True,
        "shadow_of": "pm-200",
        "_note": doc["live-shadow-pm-200"]["_note"],
    }
    assert doc["live-shadow-pm-1400"]["shadow_of"] == "pm-1400"
    assert doc["live-shadow"]["enabled"] is False and doc["live-shadow"]["follows_live"] is True
    assert "_retired" in doc["live-shadow"]
    # The file's own format survives: CRLF throughout, and the hand-formatted lines are untouched.
    assert "\n" not in text.replace("\r\n", "")
    assert '"control": { "enabled": true, "min_iv_rank": 0.0 },' in text
    assert _breaks(home) == [
        ("live-shadow", "arm_retired"),
        ("live-shadow-pm-1400", "arm_added"),
        ("live-shadow-pm-200", "arm_added"),
    ]
    assert list((home / "backups").glob("boundary-2026-11-24-*"))

    again = _registry(home)
    assert mod.main(["--arms", "pm-200", "pm-1400", "--apply", "--date", "2026-11-24"]) == 0
    assert _registry(home) == again
    assert len(_breaks(home)) == 3


def test_the_registry_it_writes_has_no_shadow_problems_and_resolves_to_live(tmp_path, monkeypatch):
    """The patched registry is read by the real loader's checks: twins resolve and none is flagged."""
    from cherrypick.meic import paper

    mod = _load()
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert mod.main(["--arms", "pm-200", "--apply", "--date", "2026-11-24"]) == 0
    profiles = json.loads(_registry(home))["profiles"]
    assert paper.shadow_problems(profiles) == []
    assert paper.twin_for("pm-200", profiles) == "live-shadow-pm-200"


@pytest.mark.parametrize(
    ("arms", "fragment"),
    [
        (["nope"], "not an arm in the registry"),
        (["live-shadow"], "follower or a twin"),
    ],
)
def test_it_refuses_an_arm_live_cannot_run(tmp_path, monkeypatch, capsys, arms, fragment):
    mod = _load()
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert mod.main(["--arms", *arms, "--apply", "--date", "2026-11-24"]) == 1
    assert fragment in capsys.readouterr().out
    assert _registry(home) == HAND
    assert _breaks(home) == []


def test_keep_live_shadow_leaves_it_enabled(tmp_path, monkeypatch):
    mod = _load()
    home = _home(tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    assert mod.main(["--arms", "pm-200", "--keep-live-shadow", "--apply", "--date", "2026-11-24"]) == 0
    doc = json.loads(_registry(home))["profiles"]
    assert doc["live-shadow"]["enabled"] is True and "_retired" not in doc["live-shadow"]
    assert ("live-shadow", "arm_retired") not in _breaks(home)
