"""cherrypick.core.logs — the shared module-log line format.

The point of this module is that a log line is an unambiguous instant. Three shapes had drifted apart
across the suite, two of them writing naive local time while the third wrote UTC, and the dashboard's
log card silently mis-ordered and then dropped whole sources as a result. So the tests here are
mostly about the timestamp: that it carries an offset, that it survives a round trip to a real
instant, and that it does so on any machine rather than only the one it was written on.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import pytest

from cherrypick.core import logs

LINE = re.compile(r"^(?P<ts>\S+) (?P<level>[A-Z]+) (?P<msg>.*)$")


@pytest.fixture
def logger(tmp_path, request):
    """A logger unique to the test, so handlers from one case never leak into another."""
    path = tmp_path / "module.log"
    lg = logs.get_logger(f"probe.{request.node.name}", path, console=False)
    yield lg, path
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def _lines(path):
    return [LINE.match(ln).groupdict() for ln in path.read_text(encoding="utf-8").splitlines()]


def test_the_timestamp_is_offset_aware_and_parses_to_a_real_instant(logger):
    lg, path = logger
    lg.info("settled at 748.97")
    (rec,) = _lines(path)

    dt = datetime.fromisoformat(rec["ts"])
    assert dt.tzinfo is not None, "a naive stamp is the ambiguity this module exists to remove"
    assert abs(dt.timestamp() - datetime.now().astimezone().timestamp()) < 60


def test_the_level_is_on_the_line(logger):
    """A reader guessing severity from prose is why the log card's filters were unreliable."""
    lg, path = logger
    lg.warning("streamer stale")
    (rec,) = _lines(path)
    assert rec["level"] == "WARNING"
    assert rec["msg"] == "streamer stale"


def test_lines_from_different_zones_still_order_by_instant():
    """The concrete defect: '2026-08-02 15:42:02' (naive local) sorted before
    '2026-08-02T21:42:02+00:00' (UTC) because a space sorts before 'T' — while being the same
    instant. With an offset on every line, parsing gives the true order."""
    a = "2026-08-02T15:42:02-06:00"
    b = "2026-08-02T21:43:00+00:00"
    assert a < b  # same-shape strings happen to compare correctly here
    assert datetime.fromisoformat(a) < datetime.fromisoformat(b)  # and genuinely do so as instants


def test_configure_is_idempotent(logger):
    """flies calls setup on every log call so a redirected home takes effect mid-process; that must
    not stack a new handler each time."""
    lg, path = logger
    before = len(lg.handlers)
    for _ in range(5):
        logs.configure(lg, path, console=False)
    assert len(lg.handlers) == before
    lg.info("once")
    assert len(_lines(path)) == 1, "a duplicated handler would write the line more than once"


def test_a_moved_path_replaces_the_handler_rather_than_duplicating(tmp_path):
    """The logger is process-global state, so a redirected CHERRYPICK_HOME would otherwise keep
    writing to the old file for the life of the process."""
    lg = logging.getLogger("probe.moved")
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    logs.configure(lg, first, console=False)
    lg.info("to the first")
    logs.configure(lg, second, console=False)
    lg.info("to the second")

    assert "to the first" in first.read_text(encoding="utf-8")
    assert "to the first" not in second.read_text(encoding="utf-8")
    assert "to the second" in second.read_text(encoding="utf-8")
    assert "to the second" not in first.read_text(encoding="utf-8"), "the old handler must be closed"
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def test_no_console_handler_without_a_real_tty(tmp_path):
    """The scheduled tasks run under pythonw.exe, where stdout can be invalid and writing to it can
    take the daemon down after its work is already done."""
    lg = logging.getLogger("probe.tty")
    logs.configure(lg, tmp_path / "c.log", console=True)  # pytest captures stdout: not a tty
    assert not any(type(h) is logging.StreamHandler for h in lg.handlers)
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def test_rotation_is_configured(logger):
    """A loop logging every two minutes runs unbounded otherwise."""
    lg, _ = logger
    handler = next(h for h in lg.handlers if hasattr(h, "maxBytes"))
    assert handler.maxBytes == logs.MAX_BYTES
    assert handler.backupCount == logs.BACKUP_COUNT
