"""Small shared helpers."""

from __future__ import annotations

import json
import os
from typing import Any

# Windows: launch a *console* child (schtasks, git, dolt, …) without popping a console window when the
# parent is windowless (pythonw, as the scheduled tasks run). Pass as `subprocess.run(..., creationflags=
# CREATE_NO_WINDOW)`. 0 elsewhere (the subprocess default), so the same call is cross-platform-safe.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def first_json(text: str | None) -> dict[str, Any]:
    """Parse the first JSON object from command output.

    Some module CLIs print a JSON status line followed by extra log/diagnostic lines (e.g.
    streamer.py --status). A plain json.loads on the whole buffer then raises "Extra data". This
    tries the whole buffer first, then falls back to the first line that parses as a JSON object.
    Returns {} when nothing parses.
    """
    if not text:
        return {}
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            val = json.loads(line)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            continue
    return {}


def mask_account(value: Any) -> str:
    """Mask an account number to its last 4 digits (`****1234`) — the suite-wide rule for anything that
    surfaces in logs/output. `****` when there are fewer than 4 characters (or the value is empty/None),
    so a full account number is never emitted."""
    s = str(value or "").strip()
    return f"****{s[-4:]}" if len(s) >= 4 else "****"
