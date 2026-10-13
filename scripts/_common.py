"""Helpers more than one script in this directory carries, kept in one copy.

Not a package and not importable by a module: a script reaches it by putting its own directory on
`sys.path` (the tests load scripts by file path, where the directory is not on it already). Every
helper here takes what used to be a script global -- the prompt, the notifier source, the state
file -- as a parameter, so a script's behaviour is exactly what it was with the helper inline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Windows: run the console child (`claude`) without popping a window when the scheduler runs this
# windowless. 0 elsewhere, the subprocess default.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run_claude(
    payload: str, *, prompt: str, disallowed: list[str], timeout: int
) -> tuple[str | None, str | None]:
    """`claude -p <prompt>` over `payload` on stdin, tools in `disallowed` refused. Returns
    (text, None) or (None, why); never raises -- a narrative is never worth raising over."""
    exe = shutil.which("claude")
    if not exe:
        return None, "claude not on PATH"
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--disallowed-tools", *disallowed],
            input=payload,
            capture_output=True,
            text=True,
            # UTF-8 explicitly. `text=True` alone decodes with the LOCALE encoding, which is cp1252
            # on Windows -- so every em dash the model emits came back as mojibake and was then
            # written out faithfully as UTF-8, baking the corruption into the note.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"claude timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 -- a note is never worth raising over
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or "")[:500] or f"claude exited {proc.returncode}"
    text = (proc.stdout or "").strip()
    return (text, None) if text else (None, "claude returned nothing")


def warn(source: str, title: str, message: str) -> None:
    """Print a WARNING to stderr, then best-effort send it through the orchestrator's notifier as
    `source`. A missing orchestrator or notify config costs the message, never the run."""
    print(f"WARNING: {title}\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify("WARNING", source, title, message)
    except Exception:  # noqa: BLE001
        pass


def cooldown_until(state_path: Path) -> datetime | None:
    """The `cooldown_until` a collector's state file records, or None (absent, unreadable, unset)."""
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8")).get("cooldown_until")
        return datetime.fromisoformat(raw) if raw else None
    except (OSError, ValueError):
        return None


def start_cooldown(state_path: Path, reason: str, cooldown: timedelta) -> None:
    """Record a cooldown of `cooldown` from now, and why, in a collector's state file."""
    until = datetime.now(UTC) + cooldown
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"cooldown_until": until.isoformat(), "reason": reason}, indent=2), encoding="utf-8"
    )
