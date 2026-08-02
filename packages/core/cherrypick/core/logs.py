"""cherrypick.core.logs — one line format for every module log in the suite.

Before this, each package set up its own ``logging`` and they drifted into three shapes::

    meic      2026-08-02 15:42:02 INFO outside trading window (17:42 ET) - skipping.
    flies     [2026-07-31T21:00:01] 2026-07-31 settled — idle until the next session
    orchestrator/watchdog/notify/earnings   {"ts": "...+00:00", "overall": "OK", ...}

That is the same failure ``home.py`` exists to prevent, one layer up, and it cost real debugging:

* The suite dashboard's log card had to reverse-engineer all three. It only dated the JSON shape, so
  meic's and flies' lines parsed as *undated* — and since undated entries sort last and the card
  keeps the newest N, they crowded out every dated source. The card showed 50 stale lines from one
  module while the watchdog and earnings were pushed out entirely.
* Both text shapes wrote **naive local** time while the JSON shape wrote **UTC**, so ``15:42`` and
  ``21:42`` were the same instant with nothing on the line to say so. Worse, a space sorts before
  ``T``, so a naive string comparison put *every* meic line ahead of *every* watchdog line whatever
  the real time.

The fix is the timestamp, not the serialization. These logs are operational narrative that people
tail and read; forcing them into JSON would add noise for no gain, while the orchestrator's records
are genuinely structured and stay JSON. So this normalizes what actually caused the trouble:

    <ISO-8601 with offset> <LEVEL> <message>
    2026-08-02T15:42:02-06:00 INFO outside trading window (17:42 ET) - skipping.

Offset-aware, so a line is an unambiguous instant no matter which machine or season wrote it, and
one obvious regex reads it. Both remaining shapes (this and JSON-with-``ts``) now carry a real
instant, which is all any reader needs.

The handler setup folds in what each package had learned separately:

* **Rotation** (10 MB x 5), from both — a trading loop logging every 2 minutes runs unbounded.
* **Rebuild when the resolved path moves** (flies) — the logger is process-global, so a redirected
  ``CHERRYPICK_HOME`` would otherwise keep writing to the old file for the life of the process.
* **Console only on a real TTY** (meic) — the scheduled tasks run under ``pythonw.exe`` where stdout
  can be invalid, and writing to it can take the daemon down.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: 10 MB x 5 backups, the value both packages independently picked.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

#: `<ISO-8601 with offset> <LEVEL> <message>`. The level is included because a reader that has to
#: guess severity from prose is the reason the log card's filter buttons were unreliable.
LINE_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class IsoOffsetFormatter(logging.Formatter):
    """Formats the timestamp as offset-aware ISO-8601, seconds precision.

    ``logging``'s ``datefmt`` cannot express a UTC offset portably (``%z`` is empty for the naive
    localtime struct it hands to ``strftime``), which is why both packages ended up emitting naive
    stamps — the ambiguity was a consequence of the stdlib's default, not a decision either made.
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created).astimezone()
        return dt.isoformat(timespec="seconds")


def _file_handler_on(logger: logging.Logger) -> RotatingFileHandler | None:
    return next((h for h in logger.handlers if isinstance(h, RotatingFileHandler)), None)


def configure(
    logger: logging.Logger,
    path: Path | str,
    *,
    console: bool = True,
    level: int = logging.INFO,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> logging.Logger:
    """Attach a rotating file handler writing :data:`LINE_FORMAT` to ``path``.

    Idempotent, and safe to call on every log call — which is how flies uses it, so a redirected home
    takes effect mid-process. If a file handler is already attached to this same path, nothing
    happens; if it points somewhere else, it is replaced rather than duplicated.
    """
    path = Path(path)
    attached = _file_handler_on(logger)
    if attached is not None:
        if os.path.abspath(attached.baseFilename) == os.path.abspath(str(path)):
            return logger
        logger.removeHandler(attached)
        attached.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(IsoOffsetFormatter(LINE_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(level)
    # Module loggers own their handlers; propagating would also hand every line to the root logger,
    # which a caller may have pointed somewhere else entirely.
    logger.propagate = False

    if console and not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        # Only a real terminal. Under pythonw.exe stdout is None or invalid, and a StreamHandler on
        # it can kill the process after its work is already done.
        stream = sys.stdout
        if stream is not None and getattr(stream, "isatty", lambda: False)():
            sh = logging.StreamHandler(stream)
            sh.setFormatter(IsoOffsetFormatter(LINE_FORMAT))
            logger.addHandler(sh)
    return logger


def get_logger(name: str, path: Path | str, **kwargs) -> logging.Logger:
    """`configure` against the named logger — the usual entry point."""
    return configure(logging.getLogger(name), path, **kwargs)
