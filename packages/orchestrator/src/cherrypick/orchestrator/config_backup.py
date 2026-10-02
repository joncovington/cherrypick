"""`run.py config-backup` — keep a git history of this machine's cherrypick configuration.

The suite's configuration is changed from many places: the console's Config page, `run.py
capabilities`, `/set-risk-profile`, the installer, a person with an editor. None of them commit, so a
history of the config only existed when someone remembered. This makes the cherrypick home
(`~/.cherrypick`) a git repository whose `.gitignore` is an ALLOW-LIST — `config.json` and
`config/*.json` only — and the scheduled `config-backup` job commits whatever changed there and, when
a remote is set, pushes it.

- `--init [--remote URL]` creates the repository with the allow-list (never touching one that
  already exists, except to add a missing remote) and makes the first commit. A machine with no git
  identity gets a repository-local one, so the job never fails on `user.name`.
- `--enable` / `--disable` flip `config_backup.enabled` in config.json (the console's Config page
  does the same).
- With no flag, one pass: commit if anything the allow-list tracks changed, then push if `push` is on
  and a remote exists. Data, state and logs are never committed: the repository's own .gitignore
  decides, and `--init` writes one that admits only config.

It never fails the supervisor: a missing repository is "not set up", a failed push is reported and
retried on the next pass (the commit is already safe locally). Pushing never prompts: it runs with
`GIT_TERMINAL_PROMPT=0` and a timeout, so a credential problem is an error message, not a hang.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import config as cfgmod
from . import util
from .util import CREATE_NO_WINDOW

STATE_NAME = "config_backup.json"

GITIGNORE = """# Allow-list: this repository keeps this machine's cherrypick CONFIG only.
# Ledgers (data/), runtime state (state/), logs, archives and backups never belong here, and
# credentials are not in these files at all (they live in the OS keyring).
/*
!/.gitignore
!/README.md
!/config.json
!/config/
/config/*
!/config/*.json
"""

README = """# cherrypick configuration history

This machine's cherrypick configuration: the suite config (`config.json`) and each module's own
config (`config/<module>.json`). The `config-backup` job commits changes here on its own schedule and
pushes them when a remote is set (`run.py config-backup --init --remote <url>`).

Not here, by design: credentials and webhooks (the OS keyring), ledgers (`data/`), runtime state
(`state/`), logs and backups. `.gitignore` is an allow-list, so a new file is ignored unless it is a
config JSON.
"""


def _git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )


def _is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def _state_path() -> Path:
    return cfgmod.state_file(STATE_NAME)


def last_state() -> dict[str, Any] | None:
    return util.read_json(_state_path(), None)


def _record(result: dict[str, Any]) -> dict[str, Any]:
    stamp = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **result}
    try:
        util.atomic_write_json(_state_path(), stamp)
    except OSError:
        pass  # the history itself is in git; a missing stamp only costs doctor its age
    return result


def init(root: Path | None = None, remote: str | None = None) -> dict[str, Any]:
    """Make the home a config-only git repository and commit what is there. Idempotent: an
    existing repository is left as it is, apart from adding `remote` as origin when it has none."""
    root = root or _home.home()
    if shutil.which("git") is None:
        return {"ok": False, "error": "git is not installed or not on PATH"}
    root.mkdir(parents=True, exist_ok=True)
    created = False
    if not _is_repo(root):
        r = _git(root, "init", "-q", "-b", "main")
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
        created = True
        gi = root / ".gitignore"
        if not gi.exists():
            gi.write_text(GITIGNORE, encoding="utf-8")
        rd = root / "README.md"
        if not rd.exists():
            rd.write_text(README, encoding="utf-8")
    # A machine with no git identity would fail every commit; give the repository its own.
    if _git(root, "config", "user.name").returncode != 0:
        _git(root, "config", "user.name", "cherrypick")
        _git(root, "config", "user.email", "cherrypick@localhost")
    remote_added = False
    if remote:
        if _git(root, "remote", "get-url", "origin").returncode != 0:
            r = _git(root, "remote", "add", "origin", remote)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
            remote_added = True
    first = run(root, push=bool(remote), message="Start the configuration history")
    return {
        "ok": first.get("ok", False),
        "created": created,
        "remote_added": remote_added,
        "first_pass": first,
    }


def run(root: Path | None = None, *, push: bool = True, message: str | None = None) -> dict[str, Any]:
    """One pass: commit what the allow-list tracks if anything changed, then push if asked and a
    remote exists. Never raises."""
    root = root or _home.home()
    try:
        if shutil.which("git") is None:
            return _record({"ok": False, "error": "git is not installed or not on PATH"})
        if not _is_repo(root):
            return _record({"ok": True, "skipped": "not set up (run.py config-backup --init)"})
        status = _git(root, "status", "--porcelain")
        if status.returncode != 0:
            return _record({"ok": False, "error": (status.stderr or status.stdout).strip()[:300]})
        changed = [line[3:].strip().strip('"') for line in status.stdout.splitlines() if line.strip()]
        committed = None
        if changed:
            _git(root, "add", "-A")
            names = ", ".join(sorted(changed)[:6]) + (
                f" and {len(changed) - 6} more" if len(changed) > 6 else ""
            )
            msg = message or f"Config changed: {names}"
            c = _git(root, "commit", "-q", "-m", msg)
            if c.returncode != 0:
                return _record(
                    {"ok": False, "error": (c.stderr or c.stdout).strip()[:300], "changed": changed}
                )
            committed = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
        pushed = None
        push_error = None
        if push and _git(root, "remote", "get-url", "origin").returncode == 0:
            ahead = _git(root, "rev-list", "--count", "@{u}..HEAD")
            needs_push = (
                committed is not None or ahead.returncode != 0 or ahead.stdout.strip() not in ("", "0")
            )
            if needs_push:
                p = _git(root, "push", "-q", "-u", "origin", "HEAD", timeout=120)
                pushed = p.returncode == 0
                if not pushed:
                    push_error = (p.stderr or p.stdout).strip()[:300]
        return _record(
            {
                "ok": push_error is None,
                "changed": changed,
                "commit": committed,
                "pushed": pushed,
                **({"push_error": push_error} if push_error else {}),
            }
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _record({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def set_enabled(on: bool, path: Path | None = None) -> dict[str, Any]:
    """Flip `config_backup.enabled` in the suite config through configedit's splice + backup."""
    import json

    from . import configedit

    path = path or cfgmod.effective_config_path()
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    if isinstance(doc.get("config_backup"), dict):
        new_text = configedit.splice_value(text, "/config_backup/enabled", on)
    else:
        brace = text.index("{")
        nl = "\r\n" if "\r\n" in text else "\n"
        block = json.dumps({"enabled": on, "interval_minutes": 15, "push": True})
        new_text = text[: brace + 1] + nl + f'  "config_backup": {block},' + text[brace + 1 :]
    json.loads(new_text)
    configedit.backup_and_write(path, new_text, "orchestrator")
    return {"ok": True, "enabled": on}
