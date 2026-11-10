"""`run.py upgrade`: move an installed suite to a newer release, and `--rollback` to move it back.

The script is the source of truth; the `/cherrypick-upgrade` slash command only explains the target's
notes and then runs it. It drives the OLD install (the code that is running) up to the point the new
code exists, then runs the NEW code's own installer, migrations and start, because only the target
knows what it needs.

**The steps** (`upgrade`):

1. *Preflight.* Versions, then every refusal at once. The live checks (a loop armed today, an open
   live leg or pending order, a live book that cannot be read) are never overridable. The quiet
   window and the disk check are; `--force-window` waives the window only.
2. *Notes.* The target's manifest (a release asset, read before anything is downloaded) gives the
   Upgrading notes, manual steps and migration list of every release after the installed one, up to
   the target. A yes is required (`--yes` for scripts; the live refusals still apply).
3. *Backup.* `backup` now, verified, then copied to `pre-upgrade-<from>-to-<to>.zip` beside it:
   the nightly run replaces the latest backup, and a rollback needs the one taken here.
   `state/upgrade.json` records the old version, folder and commit, the target, the backup and the time.
4. *Fetch.* A git checkout fetches tags (the checkout itself waits until the suite is stopped). A ZIP
   install downloads the release archive and unzips it into a NEW sibling folder; the old folder is
   never written to.
5. *Stop* the suite the way `uninstall` and `stop --all` do.
6. *Install* the target: check out the tag (git), then its installer in `-Upgrade` mode, which asks
   nothing a first install asks.
7. *Migrate*: the target's `run.py migrations`, a dry run first, then for real.
8. *Start and verify*: the target's `run.py install`, then `doctor --fast`, the job table, the
   console's `/api/health`, and `/api/version` reporting the target.

A failure after the backup stops where it is and says so; it never rolls back by itself.

**Rollback** puts back what the upgrade replaced. Ledgers migrate forward when a newer release opens
them, so old code is never pointed at data the new code has touched: the pre-upgrade backup is
restored over the home (after a snapshot of the home as it is now), and the old code is brought back
(`git checkout` of the recorded commit, or the old folder, which a ZIP upgrade left alone).

Every outside effect goes through `Ports`, so the tests run the whole flow with fakes: no network, no
broker, no process or task touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core.jsonio import write_json_atomic

from . import backup, jobspec, timeutil
from . import config as cfgmod
from .timeutil import MARKET_CLOSE
from .util import CREATE_NO_WINDOW, first_json, read_json

REPO = "joncovington/cherrypick"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
MANIFEST_ASSET = "release-manifest.json"
RECORD = "upgrade.json"
FIRST_TOOLED = "v0.12.0"  # the first release that has this command

GIB = 1024**3
BUILD_BYTES = {"zip": 4 * GIB, "git": 1 * GIB}  # a fresh venv, node_modules and a console build vs. a rebuild
BACKUP_HEADROOM = GIB // 2

# Fixed-time jobs that are upkeep, not the morning's work: they do not mark where the quiet window
# ends (the upgrade makes its own backup, and a catch-up runs whatever it interrupted).
UPKEEP_JOBS = frozenset({"suite-backup", "log-archive", "config-backup"})
# The close's settle: a fixed-time trading-day job inside this many minutes after the 16:00 close is
# part of the session's wind-down (the review, the regime cuts, the close digest), and the window
# opens JOB_ALLOWANCE_MINUTES after the last of them starts.
SETTLE_BAND_MINUTES = 90
JOB_ALLOWANCE_MINUTES = 15
SESSION_END_FALLBACK = "17:30"
MORNING_FALLBACK = "09:00"

# A release is `v0.13.0`; a pre-release `v0.12.1-beta` or `v0.12.1-beta.2`, which GitHub never calls
# the latest, so it is reached only by naming it (`--to`).
_DESCRIBE = re.compile(r"^(v\d+\.\d+\.\d+(?:-beta(?:\.\d+)?)?)(?:-(\d+)-g[0-9a-f]+)?$")
_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta(?:\.(\d+))?)?$")
# Where a release sits against its own betas: after every one of them.
_FINAL = 1_000_000


class Refused(Exception):
    """The upgrade will not start, or stops, for stated reasons (one per line)."""

    def __init__(self, reasons: list[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


# --------------------------------------------------------------------------- the outside world
@dataclass
class CmdResult:
    rc: int
    out: str = ""


def _real_run(argv: list[str], cwd: Path | None = None, stream: bool = False) -> CmdResult:
    if stream:
        # The installer's output is for the person watching, so it keeps the terminal.
        return CmdResult(subprocess.run(argv, cwd=cwd, check=False, creationflags=0).returncode)  # noqa: S603
    p = subprocess.run(  # noqa: S603
        argv, cwd=cwd, capture_output=True, text=True, check=False, creationflags=CREATE_NO_WINDOW
    )
    return CmdResult(p.returncode, (p.stdout or "") + (p.stderr or ""))


def _real_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cherrypick-upgrade"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 -- https URLs built here
        return resp.read()


def _real_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "cherrypick-upgrade"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh)


def _real_ask(question: str) -> bool:
    try:
        return input(f"{question} [yes/no] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


@dataclass
class Ports:
    run: Callable[..., CmdResult] = _real_run
    http_get: Callable[[str], bytes] = _real_get
    download: Callable[[str, Path], None] = _real_download
    ask: Callable[[str], bool] = _real_ask
    say: Callable[[str], None] = print
    free_bytes: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free  # noqa: E731
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = timeutil.now_et
    # How long verify waits for the supervisor's table and the console to come up.
    verify_wait_s: float = 150.0


# --------------------------------------------------------------------------- versions
def parse_tag(tag: str | None) -> tuple[int, int, int, int] | None:
    """A sortable key: (major, minor, patch, stage). A beta's stage is its number (`-beta` is 0), a
    release's is above every beta, so `v0.12.1-beta` < `v0.12.1-beta.2` < `v0.12.1` < `v0.12.2`."""
    m = _TAG.match((tag or "").strip())
    if not m:
        return None
    beta = (tag or "").strip().find("-beta") >= 0
    stage = (int(m[4]) if m[4] else 0) if beta else _FINAL
    return (int(m[1]), int(m[2]), int(m[3]), stage)


def normalise_tag(tag: str) -> str:
    t = tag.strip()
    if parse_tag(t) is None:
        raise Refused(f"{tag!r} is not a release version like v0.13.0 (or v0.13.0-beta)")
    return t if t.startswith("v") else f"v{t}"


def parse_describe(describe: str | None) -> tuple[str | None, int | None]:
    """`v0.12.0`, `v0.12.1-beta` or either with `-14-gabc1234` (a `-dirty` suffix is ignored) ->
    (tag, commits past it)."""
    m = _DESCRIBE.match((describe or "").strip().removesuffix("-dirty"))
    return (m[1], int(m[2] or 0)) if m else (None, None)


@dataclass
class Install:
    kind: str  # "git" (a checkout) or "zip" (an unzipped release)
    root: Path
    describe: str | None
    tag: str | None
    commits_past: int | None
    ref: str | None  # the commit, for a git checkout


def detect_install(root: Path, ports: Ports) -> Install:
    """A folder with `.git` (a directory, or the file a worktree has) is a checkout; anything else is
    an unzipped release, whose version is the `.git_archival.txt` GitHub fills in when it builds the ZIP."""
    root = Path(root)
    if (root / ".git").exists():
        d = ports.run(["git", "describe", "--tags", "--match", "v[0-9]*", "--dirty"], cwd=root)
        describe = d.out.strip() if d.rc == 0 and d.out.strip() else None
        head = ports.run(["git", "rev-parse", "HEAD"], cwd=root)
        ref = head.out.strip() if head.rc == 0 and head.out.strip() else None
        kind = "git"
    else:
        describe, ref, kind = None, None, "zip"
        try:
            text = (root / ".git_archival.txt").read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r"^describe:\s*(.+)$", text, re.M)
        value = m[1].strip() if m else ""
        describe = None if not value or "$Format" in value or "%(" in value else value
    tag, past = parse_describe(describe)
    return Install(kind, root, describe, tag, past, ref)


# --------------------------------------------------------------------------- the release manifest
def manifest_url(tag: str) -> str:
    return f"https://github.com/{REPO}/releases/download/{tag}/{MANIFEST_ASSET}"


def archive_url(tag: str) -> str:
    return f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip"


def latest_tag(ports: Ports) -> str:
    try:
        tag = json.loads(ports.http_get(LATEST_URL)).get("tag_name")
    except Exception as exc:  # noqa: BLE001 -- any failure is "could not ask"
        raise Refused(
            f"could not ask GitHub for the latest release ({exc}); name one with --to vX.Y.Z"
        ) from exc
    return normalise_tag(str(tag))


def fetch_manifest(tag: str, ports: Ports) -> dict[str, Any]:
    try:
        manifest = json.loads(ports.http_get(manifest_url(tag)))
    except Exception as exc:  # noqa: BLE001
        raise Refused(
            f"{tag} has no {MANIFEST_ASSET} on its release page ({exc}). A release without one predates "
            "the upgrade tooling and has to be installed by hand (docs/guide/upgrading.md)."
        ) from exc
    if not isinstance(manifest.get("releases"), dict) or tag not in manifest["releases"]:
        raise Refused(f"{tag}'s manifest has no entry for {tag}")
    return manifest


def notes_between(
    manifest: dict[str, Any], current: str | None, target: str
) -> list[tuple[str, dict[str, Any]]]:
    """Each release after `current` up to and including `target`, oldest first, with its manifest entry."""
    lo, hi = parse_tag(current) or (0, 0, 0, 0), parse_tag(target)
    rows = [
        (v, e)
        for v, e in manifest["releases"].items()
        if (t := parse_tag(v)) is not None and hi is not None and lo < t <= hi
    ]
    return sorted(rows, key=lambda r: parse_tag(r[0]) or (0, 0, 0, 0))


def format_notes(rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    out: list[str] = []
    for version, e in rows:
        out.append(f"--- {version} ---")
        for key, label in (
            ("upgrading", "Upgrading"),
            ("manual_steps", "Do by hand"),
            ("config_changes", "Config keys changed"),
            ("measurement_boundaries", "Measurement boundaries"),
            ("migrations", "Migrations that run"),
        ):
            items = e.get(key) or []
            if not items:
                continue
            out.append(f"{label}:")
            for item in items:
                if isinstance(item, dict):
                    item = f"{item.get('file', '')} {item.get('key', '')}: {item.get('note', '')}".strip()
                out.append(f"  - {item}")
    return out or ["(no notes)"]


# --------------------------------------------------------------------------- the quiet window
@dataclass(frozen=True)
class QuietWindow:
    start: str  # HH:MM ET, where the day's session jobs end
    end: str  # HH:MM ET, where the morning's jobs begin
    start_job: str
    end_job: str

    def text(self) -> str:
        return (
            f"{self.start} to {self.end} ET on a trading day (after {self.start_job} ends, before "
            f"{self.end_job} starts); a weekend or market holiday is quiet all day"
        )


def quiet_window(jobs: list[jobspec.JobSpec]) -> QuietWindow:
    """Where the job table leaves a gap: after the last enabled session job ends (the live loops, the
    streamer, the alert watchers: every windowed trading-day interval or resident job) and the close has
    settled (the fixed-time jobs just after 16:00), and before the first enabled fixed-time job of the
    morning (a daily job earlier than the session opens). Derived from the table, not written down, so
    a config that moves a job moves the window."""
    session = [
        j
        for j in jobs
        if j.enabled
        and j.kind in (jobspec.KIND_INTERVAL, jobspec.KIND_RESIDENT)
        and j.window_start
        and j.window_end
        and j.trading_days_only
        and not j.window_invert
    ]
    last = max(session, key=lambda j: jobspec._hhmm(j.window_end or "00:00"), default=None)
    first_open = min((jobspec._hhmm(j.window_start or "24:00") for j in session), default=None)
    start, start_job = (
        (last.window_end, last.id) if last and last.window_end else (SESSION_END_FALLBACK, "the session")
    )
    close = MARKET_CLOSE.hour * 60 + MARKET_CLOSE.minute
    for j in jobs:
        if not (j.enabled and j.kind == jobspec.KIND_DAILY and j.trading_days_only and j.at_et):
            continue
        h, m = jobspec._hhmm(j.at_et)
        if close <= h * 60 + m <= close + SETTLE_BAND_MINUTES:
            done = h * 60 + m + JOB_ALLOWANCE_MINUTES
            if done > jobspec._hhmm(str(start))[0] * 60 + jobspec._hhmm(str(start))[1]:
                start, start_job = f"{done // 60:02d}:{done % 60:02d}", j.id
    open_hhmm = f"{first_open[0]:02d}:{first_open[1]:02d}" if first_open else MORNING_FALLBACK
    morning = [
        j
        for j in jobs
        if j.enabled
        and j.kind == jobspec.KIND_DAILY
        and j.id not in UPKEEP_JOBS
        and j.at_et
        and jobspec._hhmm(j.at_et) < jobspec._hhmm(open_hhmm)
    ]
    first = min(morning, key=lambda j: jobspec._hhmm(j.at_et or "24:00"), default=None)
    end, end_job = (first.at_et, first.id) if first and first.at_et else (open_hhmm, "the session opens")
    return QuietWindow(str(start), str(end), start_job, end_job)


def in_quiet_window(window: QuietWindow, now: datetime, trading_day: bool) -> bool:
    if not trading_day:
        return True
    t = (now.hour, now.minute)
    s, e = jobspec._hhmm(window.start), jobspec._hhmm(window.end)
    return (t >= s or t < e) if s > e else (s <= t < e)


# --------------------------------------------------------------------------- live checks
def live_blockers(
    cfg: dict[str, Any], now: datetime, *, armed: list[str] | None = None, expected: dict | None = None
) -> list[str]:
    """Why no upgrade may touch the suite right now, from the readers the watchdog and `live-positions`
    use: the arm records (valid today), and each live module's own book (files and DB, no broker).
    A book that cannot be read counts: not knowing is not safe."""
    from . import livepositions, watchdog

    if armed is None:
        armed = watchdog.live_armed_modules(now)
    if expected is None:
        expected = livepositions._expected(cfg)
    reasons = [f"{m} has a live loop armed for today" for m in sorted(armed)]
    for m, payload in sorted(expected.items()):
        if not payload.get("ok"):
            reasons.append(
                f"{m}'s live book could not be read ({payload.get('error')}), so open positions are unknown"
            )
            continue
        legs, pending = len(payload.get("legs") or []), int(payload.get("pending") or 0)
        if legs:
            reasons.append(f"{m} holds {legs} open live leg(s)")
        if pending:
            reasons.append(f"{m} has {pending} live order(s) pending")
        if payload.get("armed_today") and f"{m} has a live loop armed for today" not in reasons:
            reasons.append(f"{m} has a live loop armed for today")
    return reasons


# --------------------------------------------------------------------------- the record
def record_path() -> Path:
    return cfgmod.STATE_DIR / RECORD


def read_record() -> dict[str, Any] | None:
    rec = read_json(record_path(), default=None)
    return rec if isinstance(rec, dict) and rec else None


def write_record(rec: dict[str, Any]) -> None:
    write_json_atomic(record_path(), rec)


# --------------------------------------------------------------------------- small helpers
def venv_python(root: Path) -> Path:
    rel = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    return Path(root) / ".venv" / rel


def run_py(root: Path) -> Path:
    return Path(root) / "packages" / "orchestrator" / "run.py"


def _cli(root: Path, *args: str) -> list[str]:
    py = venv_python(root)
    return [str(py if py.exists() else sys.executable), str(run_py(root)), *args]


def installer_argv(root: Path) -> list[str]:
    if os.name == "nt":
        script = str(Path(root) / "install.ps1")
        shell = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
        return [*shell, "-AcceptDisclaimer", "-Upgrade", "-NoStart"]
    return ["bash", str(Path(root) / "install.sh"), "--accept-disclaimer", "--upgrade", "--no-start"]


def sibling_for(root: Path, tag: str) -> Path:
    return Path(root).resolve().parent / f"cherrypick-{tag}"


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    base = dest.resolve()
    for name in zf.namelist():
        if not (base / name).resolve().is_relative_to(base):
            raise Refused(f"the release ZIP holds a path outside its folder ({name}); not unzipped")
    zf.extractall(dest)


def unzip_release(zip_path: Path, target_dir: Path) -> Path:
    """Unzip a release archive into `target_dir`, a folder that does not exist yet. GitHub wraps the
    release in one top folder; its contents become `target_dir`. Built beside the destination and
    renamed into place, so a failure never leaves a half-unzipped folder under the final name."""
    if target_dir.exists():
        raise Refused(f"{target_dir} already exists; the upgrade never unzips over an existing folder")
    stage = target_dir.parent / f".{target_dir.name}.partial"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, stage)
        tops = [p for p in stage.iterdir()]
        inner = tops[0] if len(tops) == 1 and tops[0].is_dir() else stage
        os.replace(inner, target_dir)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return target_dir


# --------------------------------------------------------------------------- the run
@dataclass
class Options:
    to: str | None = None
    yes: bool = False
    force_window: bool = False
    dry_run: bool = False
    rollback: bool = False


@dataclass
class Outcome:
    ok: bool
    steps: list[tuple[str, str]] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)

    def step(self, name: str, detail: str = "") -> None:
        self.steps.append((name, detail))


def _jobs(cfg: dict[str, Any], now: datetime) -> list[jobspec.JobSpec]:
    from cherrypick import cli

    jobs, _ = jobspec.derive_jobs(cfg, pythonw=cfgmod.pythonw_exe(), launcher=str(cli._LAUNCHER), now=now)
    return jobs


def preflight_common(cfg: dict[str, Any], opts: Options, ports: Ports, jobs) -> tuple[list[str], QuietWindow]:
    """The refusals every move shares: live state, then the window (waived by `force_window`)."""
    now = ports.now()
    reasons = live_blockers(cfg, now)
    window = quiet_window(jobs)
    holidays = timeutil.load_holidays()
    if not opts.force_window and not in_quiet_window(window, now, timeutil.is_trading_day(now, holidays)):
        reasons.append(
            f"it is {now:%H:%M} ET, outside the quiet window ({window.text()}); wait, or --force-window "
            "(which waives this check only)"
        )
    if _service_on(cfg):
        reasons.append(
            "the Windows service is installed and restarts a stopped supervisor, so this command cannot stop "
            "the suite; stop the service first (run.py service status shows the steps), then run it again"
        )
    return reasons, window


def _service_on(cfg: dict[str, Any]) -> bool:
    from cherrypick import cli

    return bool(cli._service_mode(cfg))


def _disk_reasons(cfg: dict[str, Any], install: Install, ports: Ports, *, build: bool) -> list[str]:
    dry = backup.run(cfg, dry_run=True)
    dest = cfgmod.backup_settings(cfg)["dest"]
    needs: list[tuple[Path, int, str]] = [(dest, int(dry.get("bytes") or 0) + BACKUP_HEADROOM, "the backup")]
    if build:
        needs.append(
            (
                install.root.parent if install.kind == "zip" else install.root,
                BUILD_BYTES[install.kind],
                "the new release",
            )
        )
    reasons = []
    by_volume: dict[Any, list[tuple[Path, int, str]]] = {}
    for p, n, why in needs:
        probe = p
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        by_volume.setdefault(_volume(probe), []).append((probe, n, why))
    for rows in by_volume.values():
        need, free = sum(n for _, n, _ in rows), ports.free_bytes(rows[0][0])
        if free < need:
            what = " and ".join(w for _, _, w in rows)
            gap = f"{free / GIB:.1f} GiB free, {need / GIB:.1f} GiB needed"
            reasons.append(f"not enough free disk space for {what}: {gap}")
    return reasons


def _volume(p: Path) -> Any:
    try:
        return os.stat(p).st_dev
    except OSError:
        return str(p)


def upgrade(cfg: dict[str, Any], opts: Options, ports: Ports, root: Path) -> Outcome:
    """The forward move. Raises Refused for a refusal; any later failure is reported in the Outcome."""
    say = ports.say
    out = Outcome(ok=False)
    install = detect_install(root, ports)
    if install.tag is None:
        raise Refused(
            "cannot tell which release this folder holds (no git tag, no .git_archival.txt). "
            "Install a release ZIP or a tagged checkout first."
        )
    if install.commits_past:
        raise Refused(
            f"this checkout is {install.commits_past} commit(s) past {install.tag} ({install.describe}): a "
            "development checkout, which updates with git, not with this command."
        )
    target = normalise_tag(opts.to) if opts.to else latest_tag(ports)
    cur, tgt = parse_tag(install.tag), parse_tag(target)
    reasons: list[str] = []
    if cur == tgt:
        raise Refused(f"already at {target}; nothing to upgrade")
    if cur and tgt and tgt < cur:
        raise Refused(
            f"{target} is older than the installed {install.tag}. Going back is --rollback, after an upgrade."
        )
    manifest = fetch_manifest(target, ports)
    say(f"Installed: {install.tag} ({install.kind} install at {install.root})")
    say(f"Target:    {target}")

    if install.kind == "git":
        dirty = ports.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=install.root)
        if dirty.rc != 0 or dirty.out.strip():
            reasons.append(
                "the checkout has uncommitted changes to tracked files; commit or stash them first"
            )
    else:
        dest_dir = sibling_for(install.root, target)
        if dest_dir.exists():
            reasons.append(
                f"{dest_dir} already exists; the upgrade unzips into a new folder and never over one"
            )
    now = ports.now()
    jobs = _jobs(cfg, now)
    common, window = preflight_common(cfg, opts, ports, jobs)
    reasons += common
    reasons += _disk_reasons(cfg, install, ports, build=True)
    if reasons:
        raise Refused(reasons)
    say(f"Quiet window: {window.text()}")

    rows = notes_between(manifest, install.tag, target)
    say("")
    for line in format_notes(rows):
        say(line)
    say("")
    if opts.dry_run:
        say("Dry run: nothing was changed.")
        out.ok = True
        out.step("dry run", "preflight passed")
        return out
    if not opts.yes and not ports.ask(
        f"Upgrade {install.tag} -> {target} now? The suite is stopped for the upgrade."
    ):
        raise Refused("not confirmed; nothing was changed")

    # --- backup
    say("Backing up ...")
    bk = backup.run(cfg)
    if not bk.get("ok"):
        raise Refused(
            f"the backup had problems, so nothing was changed: {bk.get('problems') or bk.get('error')}"
        )
    ver = backup.verify(cfg)
    if not ver.get("ok"):
        raise Refused(
            f"the backup did not verify, so nothing was changed: {ver.get('problems') or ver.get('error')}"
        )
    dest = cfgmod.backup_settings(cfg)["dest"]
    kept = dest / f"pre-upgrade-{install.tag}-to-{target}.zip"
    for old in dest.glob("pre-upgrade-*.zip"):
        old.unlink()
    shutil.copy2(backup.latest_path(cfg), kept)
    rec = {
        "from_version": install.tag,
        "from_describe": install.describe,
        "from_root": str(install.root),
        "from_kind": install.kind,
        "from_ref": install.ref,
        "to_version": target,
        "backup": str(kept),
        "at": datetime.now(timezone.utc).isoformat(),
        "status": "backed-up",
    }
    write_record(rec)
    out.step("backup", f"{kept} ({ver.get('files')} files verified)")

    def mark(status: str, **extra: Any) -> None:
        rec.update(status=status, **extra)
        write_record(rec)

    def fail(step: str, detail: str) -> Outcome:
        mark(f"failed at {step}")
        out.refusals.append(f"{step}: {detail}")
        say(f"STOPPED at {step}: {detail}")
        say(f"State is recorded in {record_path()}.")
        say("`run.py upgrade --rollback` puts the old release and the backup back.")
        return out

    # --- fetch
    say("Fetching the release ...")
    new_root = install.root
    if install.kind == "git":
        f = ports.run(["git", "fetch", "--tags", "origin"], cwd=install.root)
        have = ports.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{target}"], cwd=install.root)
        if f.rc != 0 or have.rc != 0:
            return fail("fetch", f"git could not fetch {target}: {f.out.strip()[-300:]}")
    else:
        with tempfile.TemporaryDirectory(prefix="cp-upgrade-") as tmp:
            zip_path = Path(tmp) / f"{target}.zip"
            try:
                ports.download(archive_url(target), zip_path)
                new_root = unzip_release(zip_path, sibling_for(install.root, target))
            except Exception as exc:  # noqa: BLE001
                return fail("fetch", f"{type(exc).__name__}: {exc}")
        mark("fetched", new_root=str(new_root))
    out.step("fetch", str(new_root))

    # --- stop
    say("Stopping the suite ...")
    for argv in (["uninstall"], ["stop", "--all"]):
        r = ports.run(_cli(install.root, *argv))
        if r.rc != 0:
            return fail("stop", f"`run.py {' '.join(argv)}` exited {r.rc}: {r.out.strip()[-300:]}")
    mark("stopped")
    out.step("stop")

    # --- install the target
    if install.kind == "git":
        c = ports.run(["git", "checkout", "--detach", target], cwd=install.root)
        if c.rc != 0:
            return fail("checkout", c.out.strip()[-300:])
    say("Running the new release's installer (a few minutes) ...")
    i = ports.run(installer_argv(new_root), cwd=new_root, stream=True)
    if i.rc != 0:
        return fail("install", f"the installer exited {i.rc}")
    mark("installed")
    out.step("install")

    # --- migrations
    say("Migrating data ...")
    for args in (["migrations", "--dry-run"], ["migrations", "--apply"]):
        m = ports.run(_cli(new_root, *args))
        if m.rc != 0:
            return fail("migrations", f"`run.py {' '.join(args)}` failed: {m.out.strip()[-400:]}")
    mark("migrated")
    ran = len(first_json(m.out).get("results") or [])
    out.step("migrations", f"{ran} applied" if ran else "none pending")

    # --- start and verify
    say("Starting the suite ...")
    s = ports.run(_cli(new_root, "install"))
    if s.rc != 0:
        return fail("start", f"`run.py install` exited {s.rc}: {s.out.strip()[-300:]}")
    checks = verify(new_root, target, jobs, ports)
    for name, ok, detail in checks:
        say(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
        out.step(f"verify {name}", f"{'ok' if ok else 'FAIL'} {detail}")
    if not all(ok for _, ok, _ in checks):
        return fail("verify", "; ".join(f"{n}: {d}" for n, ok, d in checks if not ok))
    mark("complete", new_root=str(new_root))
    say("")
    say(f"Upgraded {install.tag} -> {target}. The pre-upgrade backup is {kept}.")
    if install.kind == "zip":
        say(f"The old folder {install.root} is untouched; delete it once you are happy.")
    out.ok = True
    return out


def verify(root: Path, tag: str, jobs, ports: Ports) -> list[tuple[str, bool, str]]:
    """doctor (local checks), the job table, the console's health and its reported version."""
    checks: list[tuple[str, bool, str]] = []
    d = ports.run(_cli(root, "doctor", "--fast"))
    checks.append(
        (
            "doctor",
            d.rc == 0,
            "no FAIL lines" if d.rc == 0 else (d.out.strip().splitlines() or ["failed"])[-1][:200],
        )
    )

    deadline = time.monotonic() + ports.verify_wait_s
    table = (False, "the supervisor never registered its jobs")
    while True:
        st = first_json(ports.run(_cli(root, "status")).out) or {}
        sup = st.get("supervisor") or {}
        n = len(sup.get("jobs") or {})
        errs = sup.get("derive_errors") or {}
        if (sup.get("supervisor") or {}).get("running") and n:
            table = (not errs, f"{n} jobs" if not errs else f"derivation errors: {', '.join(sorted(errs))}")
            break
        if time.monotonic() >= deadline:
            break
        ports.sleep(3)
    checks.append(("job table", *table))

    if not any(j.id == "console" and j.enabled for j in jobs):
        checks.append(("console", True, "not enabled in this config; skipped"))
        return checks
    base = f"http://127.0.0.1:{cfgmod.console_serve_port()}"
    health: tuple[bool, str] = (False, "no answer from /api/health")
    while True:
        try:
            if json.loads(ports.http_get(f"{base}/api/health")).get("ok"):
                health = (True, "ok")
                break
        except Exception as exc:  # noqa: BLE001 -- not up yet
            health = (False, f"no answer from /api/health ({type(exc).__name__})")
        if time.monotonic() >= deadline:
            break
        ports.sleep(3)
    checks.append(("console health", *health))
    try:
        got = (json.loads(ports.http_get(f"{base}/api/version")).get("installed") or {}).get("tag")
    except Exception as exc:  # noqa: BLE001
        got = f"unreadable ({type(exc).__name__})"
    checks.append(("version chip", got == tag, f"reports {got}, expected {tag}"))
    return checks


# --------------------------------------------------------------------------- rollback
def rollback(cfg: dict[str, Any], opts: Options, ports: Ports, root: Path) -> Outcome:
    say = ports.say
    out = Outcome(ok=False)
    rec = read_record()
    if not rec:
        raise Refused(f"there is no {record_path()}: no upgrade from this tooling to roll back")
    if str(rec.get("status")) == "rolled-back":
        raise Refused("the last upgrade was already rolled back")
    backup_zip = Path(str(rec.get("backup") or ""))
    if not backup_zip.is_file():
        raise Refused(f"the pre-upgrade backup {backup_zip} is gone, so the data cannot be put back")
    old_root = Path(str(rec["from_root"]))
    if rec.get("from_kind") == "zip" and not old_root.is_dir():
        raise Refused(
            f"the old folder {old_root} is gone; unzip {rec.get('from_version')} again to roll back"
        )
    now = ports.now()
    reasons, window = preflight_common(cfg, opts, ports, _jobs(cfg, now))
    if reasons:
        raise Refused(reasons)
    say(f"Rolling back {rec.get('to_version')} -> {rec.get('from_version')} (status: {rec.get('status')}).")
    say(f"The data goes back to the backup taken before the upgrade ({backup_zip}).")
    say("What the suite recorded since then leaves the live home; a snapshot of it is taken first.")
    if opts.dry_run:
        say("Dry run: nothing was changed.")
        out.ok = True
        return out
    if not opts.yes and not ports.ask("Roll back now?"):
        raise Refused("not confirmed; nothing was changed")

    say("Stopping the suite ...")
    for argv in (["uninstall"], ["stop", "--all"]):
        r = ports.run(_cli(root, *argv))
        if r.rc != 0:
            raise Refused(f"stop failed (`run.py {' '.join(argv)}` exited {r.rc}); nothing was changed")
    out.step("stop")

    snap_dest = cfgmod.backup_settings(cfg)["dest"] / "pre-rollback"
    snap = backup.run({**cfg, "backup": {**(cfg.get("backup") or {}), "dest": str(snap_dest)}})
    if not snap.get("ok"):
        why = snap.get("problems") or snap.get("error")
        raise Refused(f"could not snapshot the current data first ({why}); nothing was restored")
    out.step("snapshot", str(snap.get("path")))

    res = backup.restore_over_home(backup_zip)
    if not res.get("ok"):
        out.refusals.append(f"restore: {res.get('error')}")
        say(f"STOPPED at restore: {res.get('error')}. The pre-rollback snapshot is {snap.get('path')}.")
        return out
    if "state/migrations.json" not in res["paths"]:
        (cfgmod.STATE_DIR / "migrations.json").unlink(missing_ok=True)
    out.step("restore", f"{res['files']} files from the {res.get('night')} backup")

    if rec.get("from_kind") == "git":
        c = ports.run(
            ["git", "checkout", "--detach", str(rec.get("from_ref") or rec["from_version"])], cwd=old_root
        )
        if c.rc != 0:
            out.refusals.append(f"checkout: {c.out.strip()[-300:]}")
            say(f"STOPPED at checkout: {c.out.strip()[-300:]}. The data is restored; the code is not.")
            return out
        say("Rebuilding the old release (a few minutes) ...")
        i = ports.run(installer_argv(old_root), cwd=old_root, stream=True)
        if i.rc != 0:
            out.refusals.append(f"install: the installer exited {i.rc}")
            say("STOPPED: the old release's installer failed. The data is restored.")
            return out
    out.step("code", f"{rec.get('from_version')} at {old_root}")

    say("Starting the suite ...")
    s = ports.run(_cli(old_root, "install"))
    if s.rc != 0:
        out.refusals.append(f"start: `run.py install` exited {s.rc}")
        say("STOPPED: could not start the old release.")
        return out
    checks = verify(old_root, str(rec["from_version"]), _jobs(cfg, ports.now()), ports)
    for name, ok, detail in checks:
        say(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
        out.step(f"verify {name}", f"{'ok' if ok else 'FAIL'} {detail}")
    rec.update(status="rolled-back", rolled_back_at=datetime.now(timezone.utc).isoformat())
    write_record(rec)
    out.ok = all(ok for _, ok, _ in checks)
    say(f"Rolled back to {rec.get('from_version')}.")
    return out


def run(cfg: dict[str, Any], opts: Options, *, ports: Ports | None = None, root: Path | None = None) -> int:
    """The CLI's entry: 0 done, 1 refused or stopped."""
    ports = ports or Ports()
    root = root or Path(__file__).resolve().parents[5]
    try:
        out = rollback(cfg, opts, ports, root) if opts.rollback else upgrade(cfg, opts, ports, root)
    except Refused as exc:
        ports.say("Not upgrading:" if not opts.rollback else "Not rolling back:")
        for r in exc.reasons:
            ports.say(f"  - {r}")
        return 1
    return 0 if out.ok else 1
