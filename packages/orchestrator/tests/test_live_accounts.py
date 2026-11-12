"""Live accounts on further broker logins (a module's `live_accounts.<login>`), orchestrator side.

Each live account is its own job, arm, settlement, fee reconcile, positions check and set of findings,
keyed `<module>.<login>`; an order on one login wakes only that login's loop; and with no entry every
job, argv and finding is what it was.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from cherrypick.orchestrator import accounts, jobspec, livefills, livesettle, supervisor, watchdog
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
SUITE = {
    "broker": {"logins": [{"name": "margin"}]},
    "modules": {
        "meic": {
            "enabled": True,
            "path": ".",
            "live": {
                "task_name": "cherrypick-meic-live-loop",
                "status_argv": ["-m", "cherrypick.meic.live_loop", "--status"],
            },
            "paper": {
                "fee_reconcile_at": "17:15",
                "fee_reconcile_argv": ["-m", "cherrypick.meic.fee_reconcile", "--apply"],
            },
        }
    },
}


def test_live_accounts_need_a_listed_login_and_an_enabled_entry_or_a_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    doc = {"live_accounts": {"margin": {"enabled": False}, "ghost": {"enabled": True}}}
    assert cfgmod.live_accounts(SUITE, "meic", doc) == []  # off and never traded: no job at all
    doc["live_accounts"]["margin"]["enabled"] = True
    assert cfgmod.live_accounts(SUITE, "meic", doc) == ["margin"]
    doc["live_accounts"]["margin"]["enabled"] = False
    ledger = tmp_path / "data" / "meic" / "margin" / "meic_trades.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"")
    assert cfgmod.live_accounts(SUITE, "meic", doc) == []  # an empty file is not a book
    conn = sqlite3.connect(ledger)
    conn.execute("CREATE TABLE ic_trades (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO entry_attempts DEFAULT VALUES")
    conn.commit()
    # Only a schema and a blocked entry attempt: it never traded, so it is not a book.
    assert cfgmod.live_accounts(SUITE, "meic", doc) == []
    conn.execute("INSERT INTO ic_trades DEFAULT VALUES")
    conn.commit()
    conn.close()
    assert cfgmod.live_accounts(SUITE, "meic", doc) == ["margin"]  # off but holds history: still managed
    ledger.write_bytes(b"not a database, but not empty")
    assert cfgmod.live_accounts(SUITE, "meic", doc) == ["margin"]  # unreadable: never dropped


def _jobs(live_accounts, arm_records=None):
    jobs, errors = jobspec.derive_jobs(
        SUITE,
        pythonw="pythonw",
        launcher="run.py",
        now=NOW,
        arm_records=arm_records or {},
        pipelines=False,
        live_accounts=live_accounts,
    )
    assert not {k: v for k, v in errors.items() if "meic" in k}, errors
    return {j.id: j for j in jobs}


def test_no_live_accounts_is_the_old_job_table():
    jobs = _jobs({})
    assert "meic-live" in jobs and not any(j.startswith("meic-live.") for j in jobs)
    assert jobs["meic-live"].argv == ("pythonw", "-m", "cherrypick.meic.live_loop", "--once", "--live")


def test_each_live_account_is_its_own_job_arm_and_fee_reconcile():
    arm = {
        "date": "2026-11-11",
        "companions": {
            "alert-daemon": {
                "argv": ["-m", "cherrypick.meic.alert_daemon", "--login", "margin"],
                "until": "17:00",
            }
        },
    }
    jobs = _jobs({"meic": ["margin"]}, {"meic.margin": arm})
    margin = jobs["meic-live.margin"]
    assert margin.argv[-2:] == ("--login", "margin")
    assert jobs["meic-live"].argv[-1] == "--live"  # main's unchanged
    assert jobs["meic-alert-daemon.margin"].argv[-2:] == ("--login", "margin")
    assert "meic-alert-daemon" not in jobs  # main is not armed: its companion is not derived
    assert jobs["meic-fee-reconcile.margin"].argv[-2:] == ("--login", "margin")
    assert jobs["meic-fee-reconcile"].argv[-1] == "--apply"


def test_the_supervisor_reads_each_live_account_s_arm_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "live_accounts", lambda cfg, name, doc=None: ["margin"])
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: SUITE["modules"])
    (tmp_path / "meic.margin-live-arm.json").write_text(json.dumps({"date": "2026-11-11"}), encoding="utf-8")
    assert supervisor.read_arm_records(SUITE) == {"meic.margin": {"date": "2026-11-11"}}


def test_settlement_runs_once_per_live_account(monkeypatch):
    runs = []

    class _R:
        stdout = '{"ok": true, "settled": []}'
        stderr = ""

    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: SUITE["modules"])
    monkeypatch.setattr(cfgmod, "live_accounts", lambda cfg, name, doc=None: ["margin"])
    monkeypatch.setattr(livesettle.doctor, "_run", lambda root, argv, timeout=0: runs.append(argv) or _R())
    out = livesettle.run(SUITE, notifier=type("N", (), {"notify": lambda *a, **k: None})())
    assert set(out["modules"]) == {"meic", "meic.margin"}
    assert runs[1][-2:] == ["--login", "margin"] and "--login" not in runs[0]


def test_an_order_wakes_only_its_own_live_account(monkeypatch):
    loops = {"meic"}
    assert livefills._owner_on_login("meic", "main", loops)
    assert livefills._owner_on_login("meic.margin", "margin", loops)
    assert not livefills._owner_on_login("meic.margin", "main", loops)
    assert not livefills._owner_on_login("meic", "margin", loops)
    assert not livefills._owner_on_login("manual (margin)", "margin", loops)
    owners = {"9": "meic.margin", "8": "meic"}
    assert livefills._who_on_login("margin", {"order_id": "9"}, owners)[0] == "meic.margin"
    assert livefills._who_on_login("margin", {"order_id": "8"}, owners)[0] == "manual (margin)"


def test_the_designation_lives_in_the_login_s_own_service():
    store = accounts.login_designation_store("margin", "meic")
    assert store.service_name == "cherrypick-broker.margin" and store.prefix == "meic"


def test_designating_needs_the_module_s_own_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "meic.json").write_text(json.dumps({"live_accounts": {}}), encoding="utf-8")
    out = accounts.set_login_account(SUITE, "meic", "margin", "4222")
    assert out["ok"] is False and "live_accounts.margin" in out["error"]
    assert accounts.set_login_account(SUITE, "meic", "ghost", "4222")["ok"] is False


def test_the_live_check_keys_everything_by_its_live_account(monkeypatch):
    seen = {}

    class _R:
        returncode = 0
        stdout = json.dumps({"ok": True, "open_ics": [], "unmet_gates": [], "criticals_today": []})

    def run(root, argv, timeout=0):
        seen["argv"] = argv
        return _R()

    monkeypatch.setattr(watchdog, "_run_module", run)
    monkeypatch.setattr(cfgmod, "module_root", lambda mcfg, name=None: ".")
    findings = watchdog._check_live("meic", SUITE["modules"]["meic"], NOW, False, login="margin")
    assert seen["argv"][-2:] == ["--login", "margin"]
    assert findings and all(f.key.startswith("meic.margin.") for f in findings), [f.key for f in findings]


def _ic_ledger(path):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, trade_date TEXT, created_at TEXT,"
        " fill_confirmed_at TEXT, status TEXT, net_credit REAL, wing_width REAL, quantity INTEGER,"
        " dollar_multiplier REAL)"
    )
    conn.execute(
        "INSERT INTO ic_trades (trade_date, created_at, fill_confirmed_at, status, net_credit, wing_width,"
        " quantity, dollar_multiplier) VALUES ('2026-11-11', '2026-11-11 12:30:00', '2026-11-11 12:31:00',"
        " 'closed', 0.4, 2.0, 1, 100)"
    )
    conn.commit()
    conn.close()


def test_the_execution_record_reads_each_live_account_from_its_own_ledger(tmp_path, monkeypatch):
    from cherrypick.orchestrator import execution as ex

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    doc = {
        "live": {"arm": "pm-200"},
        "live_accounts": {"margin": {"enabled": True, "arm": "pm-200", "symbol": "xsp"}},
    }
    (tmp_path / "config" / "meic.json").write_text(json.dumps(doc), encoding="utf-8")
    _ic_ledger(tmp_path / "data" / "meic" / "margin" / "meic_trades.db")
    paper = [
        {"arm": "pm-200", "symbol": "XSP", "session": "2026-11-11", "net_pnl": 10.0},
        {"arm": "pm-200", "symbol": "SPX", "session": "2026-11-11", "net_pnl": 99.0},
        {"arm": "live-shadow", "symbol": "SPX", "session": "2026-11-11", "net_pnl": 50.0},
    ]
    monkeypatch.setattr(ex, "_records", lambda path, schema: paper if path and "paper" in str(path) else [])
    monkeypatch.setattr(ex, "config_commits", lambda module: [])
    paper_db = str(tmp_path / "paper.db")
    cfg = {**SUITE, "modules": {"meic": {"path": str(tmp_path), "paper": {"paper_db": paper_db}}}}
    out = ex.run(cfg)
    assert out["meic"] == {"ok": False, "reason": "no live ledger"}  # main has none: unchanged
    m = out["meic.margin"]
    assert m["ok"] and m["mirror"] == "pm-200"
    assert [w["orders"] for w in m["weeks"]] == [1]
    # Its own arm on its own symbol: never main's shadow, never the arm's SPX book.
    assert m["window"]["baseline"] == 10.0


def test_the_execution_record_has_no_extra_key_without_a_live_account(tmp_path, monkeypatch):
    from cherrypick.orchestrator import execution as ex

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    out = ex.run({"modules": {"meic": {"path": str(tmp_path)}}})
    assert sorted(out) == ["bwb", "meic"]
