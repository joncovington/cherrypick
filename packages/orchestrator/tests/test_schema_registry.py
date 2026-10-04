"""The schema-coverage invariant, executable. The prose rule ("extend all the registries
together") drifted: five registries plus an alias where the doc said four, and a schema
missing from one surface vanished silently. Now: every surface must account for every
schema in schemas.SCHEMAS — with a reader, or an explicit not-applicable declaration."""

import pytest

from cherrypick.orchestrator import (
    calibrate,
    eval_activity,
    reconcile,
    report,
    schemas,
    trade_notifier,
)

pytestmark = pytest.mark.unit

_ALL = set(schemas.SCHEMAS)


def test_report_readers_cover_every_schema():
    assert set(report._READERS) == _ALL


def test_report_open_readers_cover_every_schema():
    assert set(report._OPEN_READERS) == _ALL


def test_reconcile_open_readers_cover_every_schema():
    assert set(reconcile._OPEN_READERS) == _ALL


def test_trade_notifier_adapters_cover_every_schema():
    assert set(trade_notifier._SCHEMAS) == _ALL


def test_eval_activity_accounts_for_every_schema():
    """A schema must have an activity reader OR be declared not-applicable — never neither
    (the silent-None gap), and never both (a contradiction)."""
    wired = set(eval_activity._READERS)
    declared_na = set(eval_activity.NOT_APPLICABLE)
    assert wired | declared_na == _ALL
    assert not (wired & declared_na)


def test_latest_session_sql_covers_every_schema():
    assert set(report._LATEST_SQL) == _ALL


def test_calibrate_shares_reports_readers():
    """calibrate must never grow its own reader registry — it reads through report's, so
    the two can't drift (the audit found an alias here; pin that it stays one)."""
    assert calibrate.report._READERS is report._READERS


def test_every_dispatch_read_of_trade_schema_goes_through_canonical():
    """A config keeps a retired id forever (`pmcc_99`, every install before 2026-10-04). A new
    surface that reads `trade_schema` without `schemas.canonical` would silently skip that module,
    so this scans the package itself rather than trusting a list of known call sites."""
    import pathlib
    import re

    pkg = pathlib.Path(report.__file__).parent
    dispatch = re.compile(r"""get\(\s*["']trade_schema["']\s*,""")
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(pkg.glob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if dispatch.search(line) and "canonical(" not in line
    ]
    assert offenders == []


def test_a_retired_trade_schema_still_reaches_its_reader():
    assert schemas.canonical("pmcc_99") == "pmcc"
    assert report._READERS[schemas.canonical("pmcc_99")] is report._READERS["pmcc"]
    assert schemas.canonical("pmcc_99") in trade_notifier._SCHEMAS
