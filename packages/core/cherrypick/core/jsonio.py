"""cherrypick.core.jsonio — the suite's one atomic JSON writer.

A reader of a JSON artifact (a loop reading its advice, the producer reading request files, the
console reading a fact set) must never see a half-written document. Every writer of one used to
carry its own three-line write-then-rename; they differed only in formatting, tmp naming and
whether they created the directory. This is the shared copy, and its parameters are exactly the
formatting differences that change the bytes on disk.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# On Windows a replace onto a file another process holds open (a reader without delete-sharing,
# antivirus scanning it) fails with PermissionError for a moment. Unretried, that one moment killed
# the supervisor: seven "FATAL: unhandled PermissionError" exits in supervisor.log, 2026-09-21 to
# 2026-10-08, every one in this replace. Moved here from the orchestrator's copy so every writer
# gets it; a file held for good still raises after the last try.
_REPLACE_TRIES = 20
_REPLACE_PAUSE_S = 0.1


def replace_with_retry(
    src: Path | str, dst: Path | str, *, sleep: Callable[[float], Any] | None = None
) -> None:
    """`os.replace(src, dst)`, retried on PermissionError up to `_REPLACE_TRIES` times."""
    sleep = sleep or time.sleep
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            sleep(_REPLACE_PAUSE_S)


def write_json_atomic(
    path: Path | str,
    payload: Any,
    *,
    indent: int | None = 2,
    default: Callable[[Any], Any] | None = str,
) -> Path:
    """Write `payload` as JSON to `path` via `<name>.tmp` beside it and `os.replace`, creating the
    directory if needed. Returns the target path.

    `indent=None` writes the compact one-line form. `default=None` makes a non-JSON value raise
    `TypeError` instead of being written as its `str()` -- for artifacts whose schema is validated
    on read, where a silently stringified value would be a worse failure than a refused write.
    The text is encoded in full before the tmp file is opened, so a refused payload leaves nothing
    behind. Text mode, UTF-8, `ensure_ascii` left on: the bytes match every copy this replaced.
    The replace is retried on a Windows PermissionError (`replace_with_retry`)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=indent, default=default)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    replace_with_retry(tmp, target)
    return target


__all__ = ["replace_with_retry", "write_json_atomic"]
