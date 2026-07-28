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
