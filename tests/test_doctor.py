"""doctor's Dolt health classification: reachability alone is not health when required databases
are declared. Guards the 2026-07-11 regression where a Dolt server rooted at the wrong data dir
answered on the port while serving none of the earnings databases, and a port-only check stayed green."""

from cherrypick.orchestrator import doctor
from cherrypick.orchestrator.doctor import FAIL, OK, WARN


def test_unreachable_is_warn():
    status, detail = doctor._dolt_status(reachable=False, required=["earnings"], present=None)
    assert status == WARN
    assert "not reachable" in detail


def test_reachable_no_required_dbs_is_ok():
    # Backward compatible: no dolt_databases declared -> reachability is enough.
    status, detail = doctor._dolt_status(reachable=True, required=[], present=None)
    assert status == OK
    assert detail == "reachable"


def test_reachable_but_no_client_skips_db_check():
    status, detail = doctor._dolt_status(reachable=True, required=["earnings"], present=None)
    assert status == OK
    assert "skipped" in detail


def test_all_required_present_is_ok():
    status, detail = doctor._dolt_status(
        reachable=True,
        required=["earnings", "options", "stocks"],
        present={"earnings", "options", "stocks", "information_schema", "mysql"},
    )
    assert status == OK
    assert "present" in detail


def test_missing_database_is_fail():
    # The exact 2026-07-11 case: server up, but the earnings DB isn't served.
    status, detail = doctor._dolt_status(
        reachable=True,
        required=["earnings", "options", "stocks"],
        present={"information_schema", "mysql"},
    )
    assert status == FAIL
    assert "earnings" in detail and "options" in detail and "stocks" in detail


def test_dolt_databases_returns_none_without_client(monkeypatch):
    # Force the optional import to fail -> graceful None, never raises.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("mysql"):
            raise ImportError("no mysql client")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert doctor._dolt_databases("127.0.0.1", 3306) is None
