"""configedit: the settings surface's file model.

The load-bearing claims: a field edit touches exactly the bytes of the edited value (notes, headers,
key order, and formatting survive by construction); guarded live-trading pointers are refused in both
write paths and both directions; organize reorders without changing any parsed value; every write is
backed up and atomic.
"""

from __future__ import annotations

import difflib
import json

import pytest

from cherrypick.orchestrator import configedit

pytestmark = pytest.mark.unit

FLIES_LIKE = """{
  "_comment": "test fixture with {braces} and [brackets] inside a string",
  "source": {
    "stream_cache_db": "~/x.db"
  },
  "_live_note": "notes carry \\"quotes\\", commas, and : colons",
  "live": {
    "enabled": false,
    "gate0_confirmed": "",
    "daily_loss_halt_dollars": 200,
    "account_deploy_limit_pct": 50
  },
  "symbols": [
    "XSP"
  ],
  "defaults": {
    "wing_width": 1,
    "entry_windows": [
      [
        "10:00",
        "14:30"
      ]
    ]
  },
  "arms": {
    "gex": {
      "enabled": true,
      "entry_windows": [["10:00", "14:30"]]
    }
  }
}
"""


@pytest.fixture
def flies_target(tmp_path, monkeypatch):
    """A sandbox home with a flies config; returns (cfg, path)."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    path = cfg_dir / "flies.json"
    path.write_text(FLIES_LIKE, encoding="utf-8")
    return {"modules": {"flies": {"enabled": True, "path": "../flies"}}}, path


# --- locate/splice ------------------------------------------------------------------------------


def test_locate_value_nested_arrays_and_tricky_strings():
    text = FLIES_LIKE
    start, end = configedit.locate_value(text, "/live/daily_loss_halt_dollars")
    assert text[start:end] == "200"
    start, end = configedit.locate_value(text, "/defaults/entry_windows/0/1")
    assert text[start:end] == '"14:30"'
    start, end = configedit.locate_value(text, "/_comment")
    assert "braces" in text[start:end]
    with pytest.raises(KeyError):
        configedit.locate_value(text, "/live/nope")


def test_splice_touches_exactly_one_line():
    new_text = configedit.splice_value(FLIES_LIKE, "/defaults/wing_width", 3)
    diff = [
        ln
        for ln in difflib.unified_diff(FLIES_LIKE.splitlines(), new_text.splitlines(), lineterm="")
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    ]
    assert diff == ['-    "wing_width": 1,', '+    "wing_width": 3,']
    old_doc, new_doc = json.loads(FLIES_LIKE), json.loads(new_text)
    old_doc["defaults"]["wing_width"] = 3
    assert new_doc == old_doc


def test_splice_string_value_preserves_everything_else():
    new_text = configedit.splice_value(FLIES_LIKE, "/source/stream_cache_db", "~/other.db")
    assert new_text.count("\n") == FLIES_LIKE.count("\n")
    assert "_live_note" in new_text and "braces" in new_text


# --- write paths --------------------------------------------------------------------------------


def test_field_edit_writes_backup_and_only_that_key(flies_target):
    cfg, path = flies_target
    out = configedit.apply_field_edit(cfg, "flies", "/defaults/wing_width", 2)
    assert out["ok"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["defaults"]["wing_width"] == 2
    backups = list(configedit.backup_dir().glob("flies.*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == FLIES_LIKE
    assert not list(path.parent.glob("*.tmp"))


def test_noop_raw_save_is_byte_identical_and_backupless(flies_target):
    cfg, path = flies_target
    out = configedit.apply_raw_save(cfg, "flies", FLIES_LIKE)
    assert out["ok"] is True and out.get("unchanged") is True
    assert path.read_text(encoding="utf-8") == FLIES_LIKE
    assert not configedit.backup_dir().exists() or not list(configedit.backup_dir().iterdir())


def test_raw_save_mtime_conflict_refused(flies_target):
    cfg, path = flies_target
    stale = path.stat().st_mtime_ns - 1
    out = configedit.apply_raw_save(cfg, "flies", FLIES_LIKE.replace("200", "201"), expected_mtime=stale)
    assert out["ok"] is False and "changed on disk" in out["error"]
    assert path.read_text(encoding="utf-8") == FLIES_LIKE


def test_guarded_field_edit_rejected_both_directions(flies_target):
    cfg, path = flies_target
    for value in (True, False):
        out = configedit.apply_field_edit(cfg, "flies", "/live/enabled", value)
        assert out["ok"] is False and "guarded" in out["error"]
    out = configedit.apply_field_edit(cfg, "flies", "/live/daily_loss_halt_dollars", 5000)
    assert out["ok"] is False
    assert path.read_text(encoding="utf-8") == FLIES_LIKE


def test_guarded_raw_save_rejected(flies_target):
    cfg, path = flies_target
    doc = json.loads(FLIES_LIKE)
    doc["live"]["enabled"] = True
    out = configedit.apply_raw_save(cfg, "flies", json.dumps(doc, indent=2))
    assert out["ok"] is False and "guarded" in out["error"]
    assert path.read_text(encoding="utf-8") == FLIES_LIKE


def test_guard_violations_detects_removal():
    old = json.loads(FLIES_LIKE)
    new = json.loads(FLIES_LIKE)
    del new["live"]["gate0_confirmed"]
    assert configedit.guard_violations("flies", old, new) == ["/live/gate0_confirmed"]


def test_type_change_needs_force(flies_target):
    cfg, _ = flies_target
    out = configedit.apply_field_edit(cfg, "flies", "/defaults/wing_width", "wide")
    assert out["ok"] is False and "force" in out["error"]
    out = configedit.apply_field_edit(cfg, "flies", "/defaults/wing_width", "wide", force=True)
    assert out["ok"] is True


def test_coupled_key_change_warns():
    old = {"modules": {"meic": {"paper": {"paper_db": "a.db", "trade_schema": "meic_ic"}}}}
    new = {"modules": {"meic": {"paper": {"paper_db": "b.db", "trade_schema": "meic_ic"}}}}
    warns = configedit.coupled_warnings(old, new)
    assert len(warns) == 1 and "paper_db" in warns[0][1]


def test_orchestrator_validation_error_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text('{\n  "modules": {},\n  "timezone": "America/New_York"\n}\n', encoding="utf-8")
    monkeypatch.setattr(configedit.cfgmod, "CONFIG_PATH", path)
    monkeypatch.setattr(configedit.cfgmod, "LEGACY_CONFIG_PATH", tmp_path / "legacy.json")
    # warn-only issues (no enabled modules) still save
    out = configedit.apply_field_edit({"modules": {}}, "orchestrator", "/timezone", "America/Chicago")
    assert out["ok"] is True and any(lvl == "warn" for lvl, _ in out["issues"])
    # an error-level issue blocks: modules must stay an object
    out = configedit.apply_field_edit({"modules": {}}, "orchestrator", "/modules", "broken")
    assert out["ok"] is False


# --- organize -----------------------------------------------------------------------------------

EXAMPLE = """{
  "top_header": "===== Top =====",
  "_comment": "example fixture",
  "source": {"stream_cache_db": "example"},
  "live_header": "===== Live gates =====",
  "_live_note": "example note",
  "live": {"enabled": false},
  "trading_header": "===== Trading =====",
  "symbols": ["SPX"],
  "defaults": {"wing_width": 5},
  "arms": {}
}
"""


def test_organize_reorders_adds_headers_keeps_values(flies_target, tmp_path):
    _, path = flies_target
    live_text = path.read_text(encoding="utf-8")
    out_text = configedit.organize_text(live_text, EXAMPLE)
    old_doc, new_doc = json.loads(live_text), json.loads(out_text)
    added = set(new_doc) - set(old_doc)
    assert added == {"top_header", "live_header", "trading_header"}
    assert {k: v for k, v in new_doc.items() if k in old_doc} == old_doc
    keys = list(new_doc)
    assert keys.index("top_header") < keys.index("_comment") < keys.index("source")
    assert keys.index("live_header") < keys.index("_live_note") < keys.index("live")
    # idempotent: organizing the organized text is byte-identical
    assert configedit.organize_text(out_text, EXAMPLE) == out_text


def test_organize_retains_unknown_keys_at_end():
    live = '{\n  "known": 1,\n  "mystery": {"a": [1, 2]}\n}\n'
    example = '{\n  "s_header": "== S ==",\n  "known": 0\n}\n'
    out = configedit.organize_text(live, example)
    doc = json.loads(out)
    assert doc["mystery"] == {"a": [1, 2]} and list(doc) == ["s_header", "known", "mystery"]


def test_organize_apply_backs_up_and_roundtrips(flies_target, tmp_path, monkeypatch):
    cfg, path = flies_target
    example = tmp_path / "example.json"
    example.write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(configedit, "_example_path", lambda cfg, tid: example)
    dry = configedit.organize(cfg, "flies")
    assert dry["ok"] is True and dry["changed"] is True
    applied = configedit.organize(cfg, "flies", apply=True)
    assert applied["ok"] is True and applied["changed"] is True
    assert list(configedit.backup_dir().glob("flies.*.json"))
    again = configedit.organize(cfg, "flies", apply=True)
    assert again["ok"] is True and again["changed"] is False
