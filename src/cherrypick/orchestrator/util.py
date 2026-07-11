"""Small shared helpers."""

from __future__ import annotations

import json
from typing import Any


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
