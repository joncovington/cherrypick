"""The migration runner: order, idempotence, dry run, what is recorded, and skipping a release.

The real registry is empty in v0.12.0; these tests run the runner over a small registry of their own,
and a last test keeps the real registry and the release manifest saying the same thing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import migrations
from cherrypick.orchestrator.migrations import Migration, MigrationError

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path / "state")
    return tmp_path / "home"


def _rename_key(old: str, new: str, file: str, calls: list):
    """A trivial example migration: rename a key in a JSON file. Idempotent by looking first."""

    def apply(home: Path, dry_run: bool) -> str:
        calls.append((file, dry_run))
        path = home / file
        data = json.loads(path.read_text(encoding="utf-8"))
        if old not in data:
            return f"{file}: nothing to rename"
        if not dry_run:
            data[new] = data.pop(old)
            path.write_text(json.dumps(data), encoding="utf-8")
        return f"{file}: {old} -> {new}"

    return apply


def registry(home: Path, calls: list) -> list[Migration]:
    home.mkdir(parents=True, exist_ok=True)
    (home / "a.json").write_text('{"old_a": 1}', encoding="utf-8")
    (home / "b.json").write_text('{"old_b": 2}', encoding="utf-8")
    (home / "c.json").write_text('{"old_c": 3}', encoding="utf-8")
    return [
        Migration("v0.13.0-01-a", "rename a", _rename_key("old_a", "new_a", "a.json", calls)),
        Migration("v0.13.0-02-b", "rename b", _rename_key("old_b", "new_b", "b.json", calls)),
        Migration("v0.14.0-01-c", "rename c", _rename_key("old_c", "new_c", "c.json", calls)),
    ]


def read(home: Path, name: str) -> dict:
    return json.loads((home / name).read_text(encoding="utf-8"))


def test_the_shipped_registry_is_empty_and_valid():
    assert migrations.MIGRATIONS == ()
    migrations.validate(migrations.MIGRATIONS)
    assert migrations.run_pending()["results"] == []


def test_they_run_in_registry_order_and_each_is_recorded(home):
    calls: list = []
    reg = registry(home, calls)
    res = migrations.run_pending(registry=reg, home=home)
    assert res["ok"] and [r["id"] for r in res["results"]] == [m.id for m in reg]
    assert [c[0] for c in calls] == ["a.json", "b.json", "c.json"]
    assert migrations.applied_ids() == [m.id for m in reg]
    assert read(home, "a.json") == {"new_a": 1}


def test_a_second_run_does_nothing(home):
    calls: list = []
    reg = registry(home, calls)
    migrations.run_pending(registry=reg, home=home)
    calls.clear()
    again = migrations.run_pending(registry=reg, home=home)
    assert again["ok"] and again["results"] == [] and calls == []
    assert migrations.applied_ids() == [m.id for m in reg], "not recorded twice"


def test_each_migration_is_idempotent_on_already_migrated_data(home):
    calls: list = []
    reg = registry(home, calls)
    migrations.run_pending(registry=reg, home=home)
    state = migrations.state_path()
    state.unlink()  # the record lost: every migration runs again over data that is already migrated
    again = migrations.run_pending(registry=reg, home=home)
    assert again["ok"] and all("nothing to rename" in r["detail"] for r in again["results"])
    assert read(home, "a.json") == {"new_a": 1}


def test_a_dry_run_changes_and_records_nothing(home):
    calls: list = []
    reg = registry(home, calls)
    res = migrations.run_pending(dry_run=True, registry=reg, home=home)
    assert res["ok"] and res["dry_run"] and "old_a -> new_a" in res["results"][0]["detail"]
    assert all(dry for _, dry in calls)
    assert read(home, "a.json") == {"old_a": 1}
    assert migrations.applied_ids() == [] and not migrations.state_path().exists()


def test_a_failure_stops_there_with_the_earlier_ones_recorded(home):
    calls: list = []
    reg = registry(home, calls)

    def broken(home: Path, dry_run: bool) -> str:
        raise RuntimeError("disk full")

    reg[1] = Migration(reg[1].id, "breaks", broken)
    res = migrations.run_pending(registry=reg, home=home)
    assert not res["ok"] and res["failed"] == reg[1].id
    assert "disk full" in res["results"][-1]["error"]
    assert migrations.applied_ids() == [reg[0].id]
    assert [c[0] for c in calls] == ["a.json"], "the third never ran"
    assert [m.id for m in migrations.pending(reg)] == [reg[1].id, reg[2].id]


def test_skipping_a_release_runs_the_skipped_releases_migrations_first(home):
    calls: list = []
    reg = registry(home, calls)
    # A machine on v0.12 upgrades straight to v0.14: nothing recorded, so v0.13's two run, then v0.14's.
    assert [m.id for m in migrations.pending(reg)] == [m.id for m in reg]
    migrations.run_pending(registry=reg, home=home)
    assert [c[0] for c in calls] == ["a.json", "b.json", "c.json"]
    # A machine that took v0.13 on its own only runs v0.14's.
    migrations.state_path().write_text(
        json.dumps({"applied": [{"id": reg[0].id}, {"id": reg[1].id}]}), encoding="utf-8"
    )
    assert [m.id for m in migrations.pending(reg)] == [reg[2].id]


def test_the_record_survives_an_unreadable_file(home):
    migrations.state_path().parent.mkdir(parents=True, exist_ok=True)
    migrations.state_path().write_text("{not json", encoding="utf-8")
    assert migrations.applied_ids() == []


def test_a_malformed_or_repeated_id_is_refused():
    noop = lambda home, dry: ""  # noqa: E731
    with pytest.raises(MigrationError):
        migrations.validate([Migration("rename-things", "x", noop)])
    with pytest.raises(MigrationError):
        migrations.validate([Migration("v0.13.0-01-a", "x", noop), Migration("v0.13.0-01-a", "y", noop)])


def test_listing_names_applied_and_pending(home):
    reg = registry(home, [])
    migrations.run_pending(registry=reg[:1], home=home)
    listed = migrations.listing(reg)
    assert listed["applied"] == [reg[0].id]
    assert [p["id"] for p in listed["pending"]] == [reg[1].id, reg[2].id]


def test_the_manifest_lists_exactly_the_registry_in_order():
    """Every release's migration ids, oldest release first, are the registry's ids: a migration added
    without telling the person upgrading (or listed but not shipped) fails here."""
    manifest = json.loads((ROOT / "release" / "manifest.json").read_text(encoding="utf-8"))

    def key(tag: str) -> tuple[int, ...]:
        return tuple(int(p) for p in tag.lstrip("v").split("."))

    listed = [
        m for tag in sorted(manifest["releases"], key=key) for m in manifest["releases"][tag]["migrations"]
    ]
    assert listed == [m.id for m in migrations.MIGRATIONS]
    for tag, entry in manifest["releases"].items():
        assert all(m.startswith(tag + "-") for m in entry["migrations"]), (
            f"{tag} lists another release's migration"
        )
