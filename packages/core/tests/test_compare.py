"""cherrypick.core.compare: every arm beside its control, inside the module's own evidence window."""

import json
import sqlite3

import pytest

from cherrypick.core import compare, ledgers
from cherrypick.core.compare import __main__ as cli


# --------------------------------------------------------------------------- fixtures
def _flies_db(path, rows, breaks):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, fees REAL, "
        "trade_date TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, "
        "reason TEXT, detail TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO fly_positions VALUES ('SPX', ?, 'outright', ?, ?, ?, 'settled')",
        rows,
    )
    conn.executemany(
        "INSERT INTO measurement_breaks (break_date, scope, kind, reason) VALUES (?, ?, ?, ?)", breaks
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def flies_db(tmp_path):
    rows = []
    # Before the era: never read in era scope.
    rows += [("control", 1000.0, 0.0, "2026-09-01"), ("gate", -1000.0, 0.0, "2026-09-01")]
    for i, day in enumerate(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]):
        rows.append(("control", 100.0, 4.0, day))
        rows.append(("control", -20.0, 4.0, day))
        if day != "2026-10-07":  # the gate blocked all of 10-07
            rows.append(("gate", 150.0 + i, 4.0, day))
        if day >= "2026-10-07":
            rows.append(("late", 50.0, 2.0, day))
    rows.append(("late", 50.0, 2.0, "2026-10-06"))  # before its own arm_added break
    rows.append(("advised:try-it", 90.0, 4.0, "2026-10-08"))
    rows.append(("advised:try-it", 95.0, 4.0, "2026-10-09"))
    breaks = [
        ("2026-08-11", "*", "entry_rules", "older era"),
        ("2026-10-05", "*", "completion_rule", "the era"),
        ("2026-10-08", "*", "partial_session", "power outage"),  # never bounds
        ("2026-10-07", "late", "arm_added", "late enters"),
        ("2026-12-18", "*", "entry_rules", "a gate that binds later"),
    ]
    return _flies_db(tmp_path / "flies.db", rows, breaks)


@pytest.fixture
def advisor_db(tmp_path):
    path = tmp_path / "advisor.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE experiments (id TEXT, module TEXT, base_profile TEXT, name TEXT, status TEXT, tag TEXT)"
    )
    conn.executemany(
        "INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("exp-1", "flies", "control", "try-it", "active", "advised:try-it"),
            ("exp-2", "earnings", "strat_test", "fly-early", "active", "advised:fly-early"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def _arm(doc, module, arm):
    m = next(m for m in doc["modules"] if m["module"] == module)
    return next(a for a in m["arms"] if a["arm"] == arm)


def _doc(flies_db, advisor_db, scope="era", session="2026-10-12"):
    return compare.compare(
        ["flies"], session=session, scope=scope, db_paths={"flies": flies_db}, advisor_db=advisor_db
    )


# --------------------------------------------------------------------------- coverage
def test_every_ledger_schema_is_compared():
    """A reader core.ledgers can run that compare does not reach is a module the page cannot see."""
    assert set(compare.MODULE_SCHEMAS.values()) == set(ledgers.READERS)


# --------------------------------------------------------------------------- the window
def test_era_starts_at_the_latest_book_wide_break_and_ignores_damage_and_future_breaks(flies_db, advisor_db):
    doc = _doc(flies_db, advisor_db)
    m = doc["modules"][0]
    assert m["status"] == "ok"
    assert m["era"]["start"] == "2026-10-05"
    assert m["era"]["bounding_break"]["kind"] == "completion_rule"
    assert [c["kind"] for c in m["era"]["caveats"]] == ["partial_session"]
    assert [f["break_date"] for f in m["era"]["ignored_future"]] == ["2026-12-18"]
    control = _arm(doc, "flies", "control")
    assert control["first"] == "2026-10-05" and control["trades"] == 10  # the 09-01 row is out
    assert control["start"] == "2026-10-05" and control["start_reason"] == "era"


def test_an_arm_added_later_starts_at_its_own_break(flies_db, advisor_db):
    late = _arm(_doc(flies_db, advisor_db), "flies", "late")
    assert late["start"] == "2026-10-07" and late["start_reason"] == "arm_added"
    assert late["first"] == "2026-10-07" and late["trades"] == 3  # its 10-06 row is before it


def test_scope_all_reads_every_closed_row(flies_db, advisor_db):
    doc = _doc(flies_db, advisor_db, scope="all")
    assert _arm(doc, "flies", "control")["trades"] == 11
    assert _arm(doc, "flies", "late")["trades"] == 4
    assert _arm(doc, "flies", "control")["start"] is None


def test_a_session_after_the_as_of_date_is_not_read(flies_db, advisor_db):
    doc = _doc(flies_db, advisor_db, session="2026-10-06")
    assert _arm(doc, "flies", "control")["last"] == "2026-10-06"


# --------------------------------------------------------------------------- the reading
def test_money_rows_add_up_and_drawdown_is_per_session(flies_db, advisor_db):
    control = _arm(_doc(flies_db, advisor_db), "flies", "control")
    assert control["gross"] - control["costs"] == pytest.approx(control["net"])
    assert control["net"] == pytest.approx(5 * (100 - 20 - 8))
    assert control["session_nets"][0] == ["2026-10-05", 72.0]
    assert control["max_drawdown"] == 0.0  # every session positive
    assert sorted(control["trade_nets"]) == [-24.0] * 5 + [96.0] * 5
    assert control["session_net_ci"] == [72.0, 72.0] and control["session_net_ci_excludes_zero"] is True


def test_gate_is_paired_on_common_sessions_and_the_blocked_day_is_counted(flies_db, advisor_db):
    gate = _arm(_doc(flies_db, advisor_db), "flies", "gate")
    vs = gate["vs_control"]
    assert gate["control"] == "control"
    assert vs["sessions"] == 4 and vs["control_only_sessions"] == 1 and vs["arm_only_sessions"] == 0
    # gate nets 146+i on its days against control's 72: i in (0, 1, 3, 4)
    assert vs["mean_diff"] == pytest.approx(sum(146 + i - 72 for i in (0, 1, 3, 4)) / 4)
    assert vs["arm_better"] == 4 and vs["sign_test_p"] == pytest.approx(0.125)
    assert vs["paired_on"] == "session"


def test_an_advised_book_pairs_with_its_experiments_base(flies_db, advisor_db):
    adv = _arm(_doc(flies_db, advisor_db), "flies", "advised:try-it")
    assert adv["control"] == "control" and adv["experiment_id"] == "exp-1" and adv["advised"] is True
    assert adv["vs_control"]["sessions"] == 2  # its own window, not control's whole era
    assert adv["vs_control"]["first"] == "2026-10-08"


def test_a_control_has_no_pair(flies_db, advisor_db):
    assert _arm(_doc(flies_db, advisor_db), "flies", "control")["vs_control"] is None


# --------------------------------------------------------------------------- controls
def test_control_resolution_rules():
    exps = {("earnings", "advised:fly-early"): {"base_profile": "strat_test"}}
    arms = ["control", "path", "friday:control", "friday:path", "strat_test:iron_fly", "advised:x"]
    assert compare.control_for("calendars", "path", arms, {}) == "control"
    assert compare.control_for("calendars", "friday:path", arms, {}) == "friday:control"
    assert compare.control_for("calendars", "control", arms, {}) is None
    assert compare.control_for("calendars", "friday:control", arms, {}) is None
    assert compare.control_for("flies", "advised:x", arms, {}) is None  # no experiment, no `x` book
    assert compare.control_for("flies", "advised:control", arms, {}) == "control"  # legacy advised:<base>
    earnings = ["strat_test:iron_fly", "strat_test:iron_condor", "advised:fly-early:iron_fly"]
    assert (
        compare.control_for("earnings", "advised:fly-early:iron_fly", earnings, exps) == "strat_test:iron_fly"
    )
    assert compare.control_for("earnings", "strat_test:iron_fly", earnings, exps) is None


# --------------------------------------------------------------------------- key/value journals
def _bwb_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL, "
        "quantity INTEGER, closed_session TEXT, entry_session TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, old_value TEXT, "
        "new_value TEXT, note TEXT, recorded_at REAL)"
    )
    rows = []
    for entry, ctl_close, arm_close in [
        ("2026-09-01", "2026-09-08", "2026-09-05"),
        ("2026-09-02", "2026-09-09", "2026-09-09"),
        ("2026-09-03", "2026-09-10", "2026-09-04"),
    ]:
        rows.append(("control", 10.0, 1.0, entry, ctl_close))
        rows.append(("delta", 30.0, 1.0, entry, arm_close))
    conn.executemany(
        "INSERT INTO bwb_positions VALUES ('SPX', ?, ?, ?, 5.0, 1, ?, ?, 'closed')",
        [(a, g, f, close, entry) for a, g, f, entry, close in rows],
    )
    conn.executemany(
        "INSERT INTO measurement_breaks (break_date, key, note) VALUES (?, ?, ?)",
        [
            ("2026-08-28", "trigger_ticks", "a real boundary"),
            ("2026-09-04", "wall_book_added", "a roster note"),
            ("2026-09-05", "partial_session", "outage"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_key_value_breaks_bound_except_roster_and_damage_notes(tmp_path):
    db = _bwb_db(tmp_path / "bwb.db")
    m = compare.module_comparison("bwb", db_path=db, session="2026-10-12")
    assert m["era"]["start"] == "2026-08-28"
    assert {c["kind"] for c in m["era"]["caveats"]} == {"wall_book_added", "partial_session"}


def test_a_multi_day_structure_pairs_on_the_entry_it_shares(tmp_path):
    """Control and delta close on different days for the same entries; on the close they share one
    session, on the entry all three."""
    db = _bwb_db(tmp_path / "bwb.db")
    delta = next(
        a
        for a in compare.module_comparison("bwb", db_path=db, session="2026-10-12")["arms"]
        if a["arm"] == "delta"
    )
    vs = delta["vs_control"]
    assert vs["paired_on"] == "entry_session"
    assert vs["sessions"] == 3 and vs["mean_diff"] == 20.0 and vs["arm_better"] == 3
    assert delta["first"] == "2026-09-04"  # its own reading stays on the close


# --------------------------------------------------------------------------- size normalisation
def _sized_bwb_db(path):
    """A control taking ten entries a session beside an arm taking one, every entry netting the
    same: the dollar pair is all size, the normalised pairs are zero."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL, "
        "quantity INTEGER, closed_session TEXT, entry_session TEXT, status TEXT)"
    )
    rows = []
    for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-03"]):
        gross = 10.0 + i
        rows += [("control", gross, 1.0, 5.0, day, day)] * 10
        rows.append(("capped", gross, 1.0, 5.0, day, day))
        # A third arm: the same count as the capped one, half the risk per entry.
        rows.append(("narrow", gross, 1.0, 2.5, day, day))
    conn.executemany(
        "INSERT INTO bwb_positions VALUES ('SPX', ?, ?, ?, ?, 1, ?, ?, 'closed')",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _bwb_arm(db, arm):
    arms = compare.module_comparison("bwb", db_path=db, session="2026-10-12")["arms"]
    return next(a for a in arms if a["arm"] == arm)


def test_a_capped_arm_is_compared_per_entry_and_per_dollar_of_risk(tmp_path):
    db = _sized_bwb_db(tmp_path / "bwb.db")
    capped, control = _bwb_arm(db, "capped"), _bwb_arm(db, "control")
    assert control["entries_per_session"] == 10.0 and capped["entries_per_session"] == 1.0
    assert capped["net_per_entry"] == control["net_per_entry"] == 10.0
    assert capped["capital"] == 1500.0 and capped["capital_known_trades"] == 3
    assert capped["net_per_100_risk"] == control["net_per_100_risk"] == 2.0  # $10 on $500 of max loss

    vs = capped["vs_control"]
    assert vs["mean_diff"] == -90.0, "in dollars a session, the cap reads as a loss of nine entries"
    assert vs["per_entry"]["mean_diff"] == 0.0 and vs["per_entry"]["sessions"] == 3
    assert vs["per_100_risk"]["mean_diff"] == 0.0 and vs["per_100_risk"]["ties"] == 3


def test_per_dollar_of_risk_separates_what_per_entry_cannot(tmp_path):
    narrow = _bwb_arm(_sized_bwb_db(tmp_path / "bwb.db"), "narrow")
    assert narrow["net_per_entry"] == 10.0 and narrow["net_per_100_risk"] == 4.0
    assert narrow["vs_control"]["per_entry"]["mean_diff"] == 0.0
    assert narrow["vs_control"]["per_100_risk"]["mean_diff"] == 2.0


def test_no_recorded_capital_is_no_risk_figure_never_zero(flies_db, advisor_db):
    gate = _arm(_doc(flies_db, advisor_db), "flies", "gate")
    assert gate["capital"] is None and gate["capital_known_trades"] == 0 and gate["net_per_100_risk"] is None
    assert gate["vs_control"]["per_100_risk"] is None
    assert gate["vs_control"]["per_entry"]["sessions"] == gate["vs_control"]["sessions"]


def test_normalised_values_drop_a_session_with_no_known_capital():
    rows = [
        {"session": "a", "net_pnl": 10.0, "capital": 100.0},
        {"session": "a", "net_pnl": 5.0, "capital": None},
        {"session": "b", "net_pnl": 7.0, "capital": None},
    ]
    assert compare.normalised_session_values(rows, "risk") == {"a": 10.0}
    assert compare.normalised_session_values(rows, "entry") == {"a": 7.5, "b": 7.0}


# --------------------------------------------------------------------------- the bridge
def test_absent_ledger_is_reported_not_raised(tmp_path):
    m = compare.module_comparison("curve", db_path=tmp_path / "nope.db", session="2026-10-12")
    assert m["status"] == "absent" and m["arms"] == []


def test_cli_json_round_trips(flies_db, advisor_db, monkeypatch, capsys):
    real = compare.compare
    monkeypatch.setattr(
        compare,
        "compare",
        lambda modules, **kw: real(["flies"], db_paths={"flies": flies_db}, advisor_db=advisor_db, **kw),
    )
    assert cli.main(["--json", "--session", "2026-10-12"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["scope"] == "era" and doc["schema"] == compare.SCHEMA_VERSION
    assert {a["arm"] for a in doc["modules"][0]["arms"]} == {"control", "gate", "late", "advised:try-it"}


def test_cli_reports_a_failure_as_json(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(compare, "compare", boom)
    assert cli.main(["--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "RuntimeError: no"}
