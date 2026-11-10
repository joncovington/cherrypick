"""`run.py upgrade` and `--rollback`, end to end over fakes: no network, no broker, no process or
scheduled task touched. The backup and the unzip are real, against a throwaway home.

What must not happen: an upgrade that starts while a live loop is armed or a live position is open
(however the window is waived); one that writes over the old folder; one that goes on after a step
failed; a rollback that leaves new-release data under old-release code."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import jobspec, upgrade

ET = ZoneInfo("America/New_York")
TUE_EVENING = datetime(2026, 11, 10, 19, 0, tzinfo=ET)
TUE_NOON = datetime(2026, 11, 10, 12, 0, tzinfo=ET)
SAT_NOON = datetime(2026, 11, 14, 12, 0, tzinfo=ET)

MANIFEST = {
    "schema": 1,
    "releases": {
        "v0.12.0": {"upgrading": ["first"], "migrations": []},
        "v0.13.0": {
            "upgrading": ["Thirteen notes."],
            "manual_steps": ["Do the thirteen thing."],
            "migrations": ["v0.13.0-01-thing"],
        },
        "v0.14.0": {"upgrading": ["Fourteen notes."], "migrations": []},
    },
}


def _job(job_id, kind, *, start=None, end=None, at=None, trading=True, enabled=True):
    return jobspec.JobSpec(
        id=job_id,
        argv=("x",),
        kind=kind,
        interval_seconds=60 if kind != jobspec.KIND_DAILY else 0,
        at_et=at,
        window_start=start,
        window_end=end,
        trading_days_only=trading,
        enabled=enabled,
    )


JOBS = [
    _job("streamer-health", jobspec.KIND_INTERVAL, start="09:00", end="16:00"),
    _job("meic-live", jobspec.KIND_INTERVAL, start="09:30", end="17:30"),
    _job("account-alerts", jobspec.KIND_RESIDENT, start="09:00", end="18:00"),
    _job("console", jobspec.KIND_RESIDENT, trading=False),
    _job("suite-backup", jobspec.KIND_DAILY, at="01:30", trading=False),
    _job("morning-factpack", jobspec.KIND_DAILY, at="08:30"),
    _job("market-files-retry", jobspec.KIND_DAILY, at="07:45"),
    _job("earlier-but-off", jobspec.KIND_DAILY, at="06:00", enabled=False),
    _job("universe-daily", jobspec.KIND_DAILY, at="18:00"),
]


class Runner:
    """Records every command and answers it like a healthy machine, unless told to fail a label."""

    def __init__(self, describe="v0.12.0", ref="abc123", dirty="", fail=None):
        self.calls: list[tuple[str, list[str]]] = []
        self.describe, self.ref, self.dirty, self.fail = describe, ref, dirty, fail or {}

    def labels(self) -> list[str]:
        return [label for label, _ in self.calls]

    def __call__(self, argv, cwd=None, stream=False):
        a = [str(x) for x in argv]
        if a[0] == "git":
            label = "git " + " ".join(a[1:3])
        elif a[0] in ("powershell", "bash"):
            label = "installer"
        else:
            label = " ".join(a[2:])
        self.calls.append((label, a))
        rc = self.fail.get(label, 0)
        if a[0] == "git" and a[1] == "describe":
            return upgrade.CmdResult(0, self.describe + "\n")
        if a[0] == "git" and a[1] == "rev-parse" and "HEAD" in a:
            return upgrade.CmdResult(0, self.ref + "\n")
        if a[0] == "git" and a[1] == "status":
            return upgrade.CmdResult(0, self.dirty)
        if label.startswith("status"):
            body = {
                "supervisor": {"supervisor": {"running": True}, "jobs": {"watchdog": {}}, "derive_errors": {}}
            }
            return upgrade.CmdResult(rc, json.dumps(body))
        if label.startswith("migrations"):
            return upgrade.CmdResult(rc, json.dumps({"ok": rc == 0, "results": []}))
        return upgrade.CmdResult(rc, "boom" if rc else "")


def release_zip(version="0.13.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        top = f"cherrypick-{version}/"
        zf.writestr(top + "packages/orchestrator/run.py", "# run")
        zf.writestr(top + "install.ps1", "# install")
        zf.writestr(top + ".git_archival.txt", f"describe: v{version}\n")
    return buf.getvalue()


class World:
    """One fake machine: a home, an install folder, a runner and the ports wired to them."""

    def __init__(self, tmp_path, monkeypatch, *, kind="zip"):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        (self.home / "config").mkdir(parents=True)
        (self.home / "config" / "x.json").write_text('{"v": "old"}', encoding="utf-8")
        monkeypatch.setenv("CHERRYPICK_HOME", str(self.home))
        monkeypatch.setattr(cfgmod, "STATE_DIR", self.home / "state")
        (self.home / "state").mkdir()
        self.root = tmp_path / "cherrypick"
        self.root.mkdir()
        if kind == "git":
            (self.root / ".git").mkdir()
        else:
            (self.root / ".git_archival.txt").write_text("describe: v0.12.0\n", encoding="utf-8")
        (self.root / "old-code.py").write_text("v0.12.0", encoding="utf-8")
        self.kind = kind
        self.armed: list[str] = []
        self.expected: dict = {}
        self.runner = Runner()
        self.said: list[str] = []
        self.http_tag = "v0.13.0"
        self.free = 10**12
        self.now = TUE_EVENING
        self.answer = True
        monkeypatch.setattr(upgrade, "_jobs", lambda cfg, now: JOBS)
        monkeypatch.setattr(upgrade, "_service_on", lambda cfg: False)
        monkeypatch.setattr(upgrade.timeutil, "load_holidays", lambda: set())
        from cherrypick.orchestrator import livepositions, watchdog

        monkeypatch.setattr(watchdog, "live_armed_modules", lambda now=None: list(self.armed))
        monkeypatch.setattr(livepositions, "_expected", lambda cfg: self.expected)
        monkeypatch.setattr(cfgmod, "console_serve_port", lambda: 5070)

    def get(self, url):
        if url == upgrade.LATEST_URL:
            return json.dumps({"tag_name": "v0.13.0"}).encode()
        if url.endswith(upgrade.MANIFEST_ASSET):
            return json.dumps(MANIFEST).encode()
        if url.endswith("/api/health"):
            return b'{"ok": true}'
        if url.endswith("/api/version"):
            return json.dumps({"installed": {"tag": self.http_tag}}).encode()
        raise OSError(f"unexpected {url}")

    def download(self, url, dest):
        assert url == upgrade.archive_url("v0.13.0")
        Path(dest).write_bytes(release_zip())

    def ports(self) -> upgrade.Ports:
        return upgrade.Ports(
            run=self.runner,
            http_get=self.get,
            download=self.download,
            ask=lambda q: self.answer,
            say=self.said.append,
            free_bytes=lambda p: self.free,
            sleep=lambda s: None,
            now=lambda: self.now,
            verify_wait_s=0,
        )

    def upgrade(self, **kw) -> upgrade.Outcome:
        return upgrade.upgrade({}, upgrade.Options(yes=True, **kw), self.ports(), self.root)

    def rollback(self, root=None, **kw) -> upgrade.Outcome:
        return upgrade.rollback(
            {}, upgrade.Options(yes=True, rollback=True, **kw), self.ports(), root or self.root
        )

    def acted(self) -> bool:
        """True once anything beyond reading happened: a stop, a record, a backup."""
        return bool(
            {"uninstall", "stop --all"} & set(self.runner.labels())
            or upgrade.record_path().exists()
            or (self.home / "backups").exists()
        )


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def gitworld(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch, kind="git")


def refusal(world: World, **kw) -> list[str]:
    with pytest.raises(upgrade.Refused) as ei:
        world.upgrade(**kw)
    return ei.value.reasons


# --------------------------------------------------------------------------- versions and install kind
def test_a_git_checkout_and_a_unzipped_release_are_told_apart(tmp_path):
    git, zip_ = tmp_path / "g", tmp_path / "z"
    (git / ".git").mkdir(parents=True)
    zip_.mkdir()
    (zip_ / ".git_archival.txt").write_text("describe: v0.12.0-3-gabc1234\n", encoding="utf-8")
    run = Runner(describe="v0.12.0")
    g = upgrade.detect_install(git, upgrade.Ports(run=run))
    z = upgrade.detect_install(zip_, upgrade.Ports(run=run))
    assert (g.kind, g.tag, g.commits_past, g.ref) == ("git", "v0.12.0", 0, "abc123")
    assert (z.kind, z.tag, z.commits_past) == ("zip", "v0.12.0", 3)


def test_a_worktree_style_git_file_still_counts_as_a_checkout(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    assert upgrade.detect_install(tmp_path, upgrade.Ports(run=Runner())).kind == "git"


def test_an_archival_template_that_was_never_filled_in_is_an_unknown_version(tmp_path):
    (tmp_path / ".git_archival.txt").write_text(
        "describe: $Format:%(describe:tags=true)$\n", encoding="utf-8"
    )
    inst = upgrade.detect_install(tmp_path, upgrade.Ports(run=Runner()))
    assert (inst.kind, inst.describe, inst.tag) == ("zip", None, None)


def test_it_will_not_guess_the_installed_version(world):
    (world.root / ".git_archival.txt").write_text("describe: $Format:x$\n", encoding="utf-8")
    assert "cannot tell which release" in " ".join(refusal(world))


def test_a_development_checkout_past_its_tag_is_left_to_git(gitworld):
    gitworld.runner.describe = "v0.12.0-14-gabc1234"
    assert "development checkout" in " ".join(refusal(gitworld))


def test_a_beta_sorts_before_its_release_and_after_the_one_before():
    order = ["v0.12.0", "v0.12.1-beta", "v0.12.1-beta.2", "v0.12.1", "v0.13.0-beta", "v0.13.0"]
    assert sorted(reversed(order), key=upgrade.parse_tag) == order
    assert upgrade.parse_describe("v0.12.1-beta") == ("v0.12.1-beta", 0)
    assert upgrade.parse_describe("v0.12.1-beta.2-3-gabc1234-dirty") == ("v0.12.1-beta.2", 3)
    assert upgrade.normalise_tag("0.12.1-beta") == "v0.12.1-beta"
    assert upgrade.parse_tag("v0.12.1-rc1") is None
    # Notes from a beta up to the release include the release, not the beta the person already has.
    manifest = {"releases": {v: {} for v in order}}
    assert [v for v, _ in upgrade.notes_between(manifest, "v0.12.1-beta", "v0.12.1")] == [
        "v0.12.1-beta.2",
        "v0.12.1",
    ]


def test_an_installed_beta_is_read_and_its_release_is_newer(world):
    (world.root / ".git_archival.txt").write_text("describe: v0.12.1-beta\n", encoding="utf-8")
    assert "older than" in " ".join(refusal(world, to="v0.12.0"))
    assert "already at" in " ".join(refusal(world, to="v0.12.1-beta"))


def test_same_version_and_older_version_are_refused(world):
    assert "already at" in " ".join(refusal(world, to="v0.12.0"))
    world.runner.describe = "v0.13.0"
    (world.root / ".git_archival.txt").write_text("describe: v0.13.0\n", encoding="utf-8")
    assert "older than" in " ".join(refusal(world, to="v0.12.0"))


# --------------------------------------------------------------------------- the live refusals
def test_an_armed_live_loop_refuses_and_nothing_happens(world):
    world.armed = ["meic"]
    assert any("meic has a live loop armed" in r for r in refusal(world))
    assert not world.acted()


def test_an_open_live_position_refuses_and_nothing_happens(world):
    world.expected = {"meic": {"ok": True, "armed_today": False, "legs": [{"x": 1}, {"x": 2}], "pending": 0}}
    assert any("meic holds 2 open live leg(s)" in r for r in refusal(world))
    assert not world.acted()


def test_a_pending_live_order_refuses(world):
    world.expected = {"flies": {"ok": True, "legs": [], "pending": 1}}
    assert any("1 live order(s) pending" in r for r in refusal(world))


def test_a_live_book_that_cannot_be_read_refuses(world):
    world.expected = {"bwb": {"ok": False, "error": "no JSON"}}
    assert any("bwb's live book could not be read" in r for r in refusal(world))


def test_a_flat_unarmed_book_does_not_refuse(world):
    world.expected = {"meic": {"ok": True, "armed_today": False, "legs": [], "pending": 0}}
    assert world.upgrade(dry_run=True).ok


# --------------------------------------------------------------------------- the window
def test_the_window_is_derived_from_the_job_table():
    w = upgrade.quiet_window(JOBS)
    # The last session job ends at 18:00; the first morning job (not the 01:30 backup, not a disabled
    # 06:00 one) is the 07:45 retry.
    assert (w.start, w.end) == ("18:00", "07:45")
    assert (w.start_job, w.end_job) == ("account-alerts", "market-files-retry")
    assert "18:00 to 07:45 ET" in w.text()


def test_the_window_follows_a_changed_table():
    moved = [j for j in JOBS if j.id != "account-alerts"]
    assert upgrade.quiet_window(moved).start == "17:30"


def test_the_close_has_to_settle_before_the_window_opens():
    jobs = [
        _job("streamer-health", jobspec.KIND_INTERVAL, start="09:00", end="16:00"),
        _job("review-provisional", jobspec.KIND_DAILY, at="16:30"),
        _job("meic-regime-cuts", jobspec.KIND_DAILY, at="16:40"),
        _job("universe-daily", jobspec.KIND_DAILY, at="18:00"),  # the evening batch is not the settle
        _job("market-files-retry", jobspec.KIND_DAILY, at="07:45"),
    ]
    w = upgrade.quiet_window(jobs)
    assert (w.start, w.start_job) == ("16:55", "meic-regime-cuts")
    assert w.end == "07:45"


def test_a_real_job_table_yields_a_window():
    """The real derivation (nothing patched) over the shipped example config."""
    example = Path(__file__).resolve().parents[1] / "config.example.json"
    jobs = upgrade._jobs(json.loads(example.read_text(encoding="utf-8")), TUE_NOON)
    w = upgrade.quiet_window(jobs)
    assert "16:00" <= w.start <= "19:00" and "05:00" <= w.end <= "09:00"
    assert upgrade._service_on({}) in (True, False)


def test_inside_the_window_wraps_midnight_and_a_weekend_is_quiet_all_day():
    w = upgrade.quiet_window(JOBS)
    at = lambda h, m, d=10: datetime(2026, 11, d, h, m, tzinfo=ET)  # noqa: E731
    assert upgrade.in_quiet_window(w, at(19, 0), True)
    assert upgrade.in_quiet_window(w, at(3, 0), True)
    assert not upgrade.in_quiet_window(w, at(12, 0), True)
    assert not upgrade.in_quiet_window(w, at(7, 45), True)
    assert upgrade.in_quiet_window(w, at(12, 0, 14), False)


def test_outside_the_window_refuses_and_says_the_window(world):
    world.now = TUE_NOON
    reasons = refusal(world)
    assert any("outside the quiet window" in r and "18:00 to 07:45 ET" in r for r in reasons)
    assert not world.acted()


def test_a_weekend_noon_is_inside_the_window(world):
    world.now = SAT_NOON
    assert world.upgrade(dry_run=True).ok


def test_force_window_waives_the_window(world):
    world.now = TUE_NOON
    assert world.upgrade(dry_run=True, force_window=True).ok


def test_force_window_never_waives_the_live_checks(world):
    world.now = TUE_NOON
    world.armed = ["meic"]
    reasons = refusal(world, force_window=True)
    assert any("armed" in r for r in reasons)
    assert not any("quiet window" in r for r in reasons), "the window was waived, only the live check remains"
    world.armed = []
    world.expected = {"meic": {"ok": True, "legs": [{"x": 1}], "pending": 0}}
    assert any("open live leg" in r for r in refusal(world, force_window=True))
    assert not world.acted()


def test_service_mode_refuses(world, monkeypatch):
    monkeypatch.setattr(upgrade, "_service_on", lambda cfg: True)
    assert any("Windows service" in r for r in refusal(world))


def test_low_disk_space_refuses(world):
    world.free = 1024
    assert any("not enough free disk space" in r for r in refusal(world))
    assert not world.acted()


def test_a_dirty_checkout_refuses(gitworld):
    gitworld.runner.dirty = " M packages/core/x.py\n"
    assert any("uncommitted changes" in r for r in refusal(gitworld))


def test_every_refusal_is_listed_together(world):
    world.now = TUE_NOON
    world.armed = ["meic"]
    world.free = 1
    assert len(refusal(world)) == 3


# --------------------------------------------------------------------------- notes and confirmation
def test_the_notes_of_every_release_up_to_the_target_are_shown_including_skipped_ones(world):
    rows = upgrade.notes_between(MANIFEST, "v0.12.0", "v0.14.0")
    assert [v for v, _ in rows] == ["v0.13.0", "v0.14.0"]
    text = "\n".join(upgrade.format_notes(rows))
    assert "Thirteen notes." in text and "Do the thirteen thing." in text and "v0.13.0-01-thing" in text
    assert "first" not in text, "the installed release's own notes are not repeated"


def test_the_notes_are_printed_before_the_question(world):
    world.upgrade(dry_run=True)
    assert any("Thirteen notes." in line for line in world.said)
    assert any("Quiet window: 18:00 to 07:45" in line for line in world.said)


def test_declining_changes_nothing(world):
    world.answer = False
    with pytest.raises(upgrade.Refused):
        upgrade.upgrade({}, upgrade.Options(), world.ports(), world.root)
    assert not world.acted()


def test_a_dry_run_changes_nothing(world):
    assert world.upgrade(dry_run=True).ok
    assert not world.acted()
    assert not (world.tmp / "cherrypick-v0.13.0").exists()


def test_a_release_with_no_manifest_is_refused_as_pre_tooling(world):
    ports = world.ports()
    ports.http_get = lambda url: (_ for _ in ()).throw(OSError("404"))
    with pytest.raises(upgrade.Refused) as ei:
        upgrade.fetch_manifest("v0.11.2", ports)
    assert "predates" in str(ei.value)


# --------------------------------------------------------------------------- the zip path
def test_a_zip_install_unzips_into_a_new_sibling_and_never_over_the_old_folder(world):
    out = world.upgrade()
    assert out.ok, out.refusals
    new = world.tmp / "cherrypick-v0.13.0"
    assert (new / "packages" / "orchestrator" / "run.py").is_file()
    assert (world.root / "old-code.py").read_text(encoding="utf-8") == "v0.12.0"
    assert not (world.root / "packages").exists(), "nothing of the new release landed in the old folder"
    assert not list(world.tmp.glob(".*.partial"))


def test_it_refuses_to_unzip_over_an_existing_folder(world):
    (world.tmp / "cherrypick-v0.13.0").mkdir()
    assert any("already exists" in r for r in refusal(world))
    assert not world.acted()


def test_unzip_itself_refuses_an_existing_destination(tmp_path):
    existing = tmp_path / "cherrypick-v0.13.0"
    existing.mkdir()
    (existing / "keep.txt").write_text("mine", encoding="utf-8")
    zip_path = tmp_path / "r.zip"
    zip_path.write_bytes(release_zip())
    with pytest.raises(upgrade.Refused):
        upgrade.unzip_release(zip_path, existing)
    assert [p.name for p in existing.iterdir()] == ["keep.txt"]


def test_a_zip_that_reaches_outside_its_folder_is_not_unzipped(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escaped.txt", "x")
    with pytest.raises(upgrade.Refused):
        upgrade.unzip_release(bad, tmp_path / "out" / "new")
    assert not (tmp_path / "out" / "escaped.txt").exists() and not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "out" / "new").exists()


# --------------------------------------------------------------------------- the whole run
def test_the_zip_upgrade_runs_every_step_in_order_against_the_new_folder(world):
    out = world.upgrade()
    assert out.ok, out.refusals
    new = str(world.tmp / "cherrypick-v0.13.0")
    labels = world.runner.labels()
    assert labels.index("uninstall") < labels.index("stop --all") < labels.index("installer")
    assert (
        labels.index("installer") < labels.index("migrations --dry-run") < labels.index("migrations --apply")
    )
    assert labels.index("migrations --apply") < labels.index("install") < labels.index("doctor --fast")
    run = dict(world.runner.calls)
    assert str(world.root) in run["uninstall"][1], "the suite is stopped by the old code"
    assert new in run["migrations --apply"][1] and new in run["install"][1], "migrated and started by the new"
    assert new in " ".join(run["installer"])
    assert "-Upgrade" in run["installer"] or "--upgrade" in run["installer"]


def test_a_git_upgrade_fetches_before_stopping_and_checks_out_after(gitworld):
    out = gitworld.upgrade()
    assert out.ok, out.refusals
    labels = gitworld.runner.labels()
    assert (
        labels.index("git fetch --tags") < labels.index("uninstall") < labels.index("git checkout --detach")
    )
    assert labels.index("git checkout --detach") < labels.index("installer")
    assert gitworld.runner.calls[labels.index("git checkout --detach")][1][-1] == "v0.13.0"


def test_upgrade_json_records_the_old_version_folder_target_backup_and_time(world):
    world.upgrade()
    rec = upgrade.read_record()
    assert rec["from_version"] == "v0.12.0" and rec["from_root"] == str(world.root)
    assert rec["to_version"] == "v0.13.0" and rec["status"] == "complete"
    assert rec["at"].startswith("20")
    assert Path(rec["backup"]).name == "pre-upgrade-v0.12.0-to-v0.13.0.zip" and Path(rec["backup"]).is_file()
    assert rec["new_root"] == str(world.tmp / "cherrypick-v0.13.0")
    with zipfile.ZipFile(rec["backup"]) as zf:
        assert "config/x.json" in zf.namelist()


def test_the_pre_upgrade_backup_survives_the_nightly_backup(world):
    from cherrypick.orchestrator import backup

    world.upgrade()
    kept = Path(upgrade.read_record()["backup"])
    (world.home / "config" / "x.json").write_text('{"v": "new"}', encoding="utf-8")
    assert backup.run({})["ok"]
    with zipfile.ZipFile(kept) as zf:
        assert json.loads(zf.read("config/x.json")) == {"v": "old"}


@pytest.mark.parametrize("step", ["uninstall", "installer", "migrations --apply", "install"])
def test_a_failed_step_stops_the_run_and_never_goes_on(world, step):
    world.runner.fail = {step: 1}
    out = world.upgrade()
    assert not out.ok and out.refusals
    labels = world.runner.labels()
    order = [
        "uninstall",
        "stop --all",
        "installer",
        "migrations --dry-run",
        "migrations --apply",
        "install",
        "doctor --fast",
    ]
    for later in order[order.index(step) + 1 :]:
        assert later not in labels, f"{later} ran after {step} failed"
    assert upgrade.read_record()["status"].startswith("failed at")
    assert any("--rollback" in line for line in world.said)


def test_a_failed_migration_dry_run_stops_before_anything_is_changed(world):
    world.runner.fail = {"migrations --dry-run": 1}
    assert not world.upgrade().ok
    assert "migrations --apply" not in world.runner.labels()


def test_a_wrong_version_in_the_chip_fails_verification(world):
    world.http_tag = "v0.12.0"
    out = world.upgrade()
    assert not out.ok
    assert any("version chip" in r for r in out.refusals)


def test_a_bad_backup_changes_nothing(world, monkeypatch):
    from cherrypick.orchestrator import backup

    monkeypatch.setattr(
        backup, "run", lambda cfg, dry_run=False: {"ok": dry_run, "bytes": 1, "problems": ["bad"]}
    )
    with pytest.raises(upgrade.Refused):
        world.upgrade()
    assert "uninstall" not in world.runner.labels() and not upgrade.record_path().exists()


# --------------------------------------------------------------------------- rollback
def _upgraded(world) -> None:
    assert world.upgrade().ok
    world.runner.calls.clear()
    world.http_tag = "v0.12.0"


def test_rollback_restores_the_backup_and_points_at_the_old_code(world):
    _upgraded(world)
    new_root = world.tmp / "cherrypick-v0.13.0"
    (world.home / "config" / "x.json").write_text('{"v": "migrated"}', encoding="utf-8")
    (world.home / "state" / "migrations.json").write_text(
        '{"applied": [{"id": "v0.13.0-01-thing"}]}', encoding="utf-8"
    )
    out = world.rollback(root=new_root)
    assert out.ok, out.refusals
    assert json.loads((world.home / "config" / "x.json").read_text(encoding="utf-8")) == {"v": "old"}
    assert not (world.home / "state" / "migrations.json").exists()
    snap = world.home / "backups" / "pre-rollback" / "cherrypick-backup.zip"
    with zipfile.ZipFile(snap) as zf:
        assert json.loads(zf.read("config/x.json")) == {"v": "migrated"}, "the new data is kept, not lost"
    calls = dict(world.runner.calls)
    assert str(new_root) in calls["uninstall"][1], "stopped by the code that is running"
    assert str(world.root) in calls["install"][1], "started by the old folder's code"
    assert world.runner.labels().index("stop --all") < world.runner.labels().index("install")
    assert upgrade.read_record()["status"] == "rolled-back"


def test_rollback_of_a_git_install_checks_out_the_recorded_commit_after_the_stop_and_rebuilds(gitworld):
    _upgraded(gitworld)
    gitworld.runner.ref = "def456"
    out = gitworld.rollback()
    assert out.ok, out.refusals
    labels = gitworld.runner.labels()
    assert labels.index("stop --all") < labels.index("git checkout --detach") < labels.index("installer")
    assert gitworld.runner.calls[labels.index("git checkout --detach")][1][-1] == "abc123"


def test_rollback_never_runs_old_code_on_new_data_if_the_restore_fails(world, monkeypatch):
    from cherrypick.orchestrator import backup

    _upgraded(world)
    monkeypatch.setattr(backup, "restore_over_home", lambda p: {"ok": False, "error": "damaged"})
    out = world.rollback(root=world.tmp / "cherrypick-v0.13.0")
    assert not out.ok
    assert "install" not in world.runner.labels(), "nothing was started"


def test_rollback_restores_nothing_when_the_snapshot_of_the_current_data_fails(world, monkeypatch):
    from cherrypick.orchestrator import backup

    _upgraded(world)
    (world.home / "config" / "x.json").write_text('{"v": "migrated"}', encoding="utf-8")
    monkeypatch.setattr(backup, "run", lambda cfg, dry_run=False: {"ok": False, "problems": ["disk full"]})
    with pytest.raises(upgrade.Refused, match="snapshot"):
        world.rollback(root=world.tmp / "cherrypick-v0.13.0")
    assert json.loads((world.home / "config" / "x.json").read_text(encoding="utf-8")) == {"v": "migrated"}


def test_rollback_refuses_while_a_live_loop_is_armed(world):
    _upgraded(world)
    world.armed = ["meic"]
    with pytest.raises(upgrade.Refused) as ei:
        world.rollback(root=world.tmp / "cherrypick-v0.13.0", force_window=True)
    assert "armed" in str(ei.value)
    assert world.runner.labels() == []


def test_rollback_without_a_record_or_without_the_backup_refuses(world):
    with pytest.raises(upgrade.Refused, match="no upgrade"):
        world.rollback()
    _upgraded(world)
    Path(upgrade.read_record()["backup"]).unlink()
    with pytest.raises(upgrade.Refused, match="backup"):
        world.rollback()


def test_a_second_rollback_is_refused(world):
    _upgraded(world)
    assert world.rollback(root=world.tmp / "cherrypick-v0.13.0").ok
    with pytest.raises(upgrade.Refused, match="already"):
        world.rollback()


def test_a_damaged_backup_restores_nothing(world):
    from cherrypick.orchestrator import backup

    _upgraded(world)
    kept = Path(upgrade.read_record()["backup"])
    buf = io.BytesIO()
    with zipfile.ZipFile(kept) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, b"tampered" if name == "config/x.json" else data)
    kept.write_bytes(buf.getvalue())
    (world.home / "config" / "x.json").write_text('{"v": "migrated"}', encoding="utf-8")
    res = backup.restore_over_home(kept)
    assert not res["ok"] and "sha256" in res["error"]
    assert json.loads((world.home / "config" / "x.json").read_text(encoding="utf-8")) == {"v": "migrated"}


def test_restoring_over_a_sqlite_file_drops_the_newer_databases_sidecars(world):
    import sqlite3

    from cherrypick.orchestrator import backup

    db = world.home / "data" / "ledger.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("create table t (i)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    assert backup.run({})["ok"]
    (world.home / "data" / "ledger.db-wal").write_bytes(b"newer database's log")
    assert backup.restore_over_home(backup.latest_path({}))["ok"]
    assert not (world.home / "data" / "ledger.db-wal").exists()
    assert sqlite3.connect(db).execute("select count(*) from t").fetchone()[0] == 1
