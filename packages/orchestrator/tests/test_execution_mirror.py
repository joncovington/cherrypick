"""The paper-live gap's mirror follows the arm live ran: `live-shadow` until it retires, then each
session's own twin (`shadow_of`), and a session whose arm has no twin is left out and said so."""

from __future__ import annotations

import json
import sqlite3

from cherrypick.orchestrator import execution as ex


def _rec(session, arm, net=10.0, credit=100.0, cost=5.0, symbol="SPX"):
    return {
        "session": session,
        "arm": arm,
        "net_pnl": net,
        "entry_credit": credit,
        "cost": cost,
        "symbol": symbol,
    }


TWINS = {"pm-200": "live-shadow-pm-200", "pm-1400": "live-shadow-pm-1400"}


def test_before_live_shadow_retires_it_is_the_mirror_whatever_twins_exist():
    paper = [_rec("2026-11-20", "live-shadow"), _rec("2026-11-20", "live-shadow-pm-200")]
    rows, unmirrored = ex.per_arm_paper(paper, {"2026-11-20": {"pm-200"}}, TWINS, None)
    assert [r["arm"] for r in rows] == ["live-shadow"]
    assert unmirrored == []


def test_from_the_retirement_each_session_uses_the_twin_of_the_arm_live_ran_that_day():
    paper = [
        _rec("2026-11-23", "live-shadow"),  # before: the unpinned one's rows
        _rec("2026-11-24", "live-shadow-pm-200"),
        _rec("2026-11-24", "live-shadow-pm-1400"),
        _rec("2026-11-25", "live-shadow-pm-200"),
        _rec("2026-11-25", "live-shadow-pm-1400"),
    ]
    arms = {"2026-11-24": {"pm-200"}, "2026-11-25": {"pm-1400"}}
    rows, unmirrored = ex.per_arm_paper(paper, arms, TWINS, "2026-11-24")
    assert [(r["session"], r["arm"]) for r in rows] == [
        ("2026-11-23", "live-shadow"),
        ("2026-11-24", "live-shadow-pm-200"),
        ("2026-11-25", "live-shadow-pm-1400"),
    ]
    assert unmirrored == []


def test_a_session_whose_arm_has_no_twin_has_no_mirror_and_is_reported_never_borrowed():
    paper = [_rec("2026-11-24", "live-shadow-pm-200"), _rec("2026-11-24", "live-shadow")]
    rows, unmirrored = ex.per_arm_paper(paper, {"2026-11-24": {"control"}}, TWINS, "2026-11-24")
    assert rows == []
    assert unmirrored == ["2026-11-24"]


def test_the_unmirrored_session_is_left_out_of_the_gap_not_counted_as_a_miss():
    """Guard shown to matter: with the unmirrored session left IN, live's net would read as a pure
    gap against zero paper; out, the gap covers only the sessions that have a mirror."""
    live = [_rec("2026-11-24", None, net=40.0), _rec("2026-11-25", None, net=30.0)]
    paper = [_rec("2026-11-25", "live-shadow-pm-200", net=25.0)]
    arms = {"2026-11-24": {"control"}, "2026-11-25": {"pm-200"}}
    mirrored, unmirrored = ex.per_arm_paper(paper, arms, TWINS, "2026-11-24")
    working = {"2026-11-24", "2026-11-25"}
    left_in = ex.gap(live, mirrored, working)
    left_out = ex.gap(
        [r for r in live if r["session"] not in unmirrored], mirrored, working - set(unmirrored)
    )
    assert unmirrored == ["2026-11-24"]
    assert left_in["total"] == 45.0  # (40 - 0) + (30 - 25): the unmirrored day dressed as a gap
    assert left_out["total"] == 5.0


def test_twins_come_from_the_registry_file_plain_ones_only_enabled_or_not(tmp_path):
    reg = tmp_path / "meic.risk.json"
    reg.write_text(
        json.dumps(
            {
                "profiles": {
                    "pm-200": {"enabled": False},
                    "live-shadow": {"enabled": True, "follows_live": True},
                    "live-shadow-pm-200": {"enabled": True, "shadow_of": "pm-200"},
                    "old-pm-1400": {"enabled": False, "shadow_of": "pm-1400", "_note": "retired"},
                    "pm-200-plus": {"enabled": True, "shadow_of": "pm-200", "min_iv_rank": 0.0},
                }
            }
        ),
        encoding="utf-8",
    )
    assert ex.meic_twins(reg) == {"pm-200": "live-shadow-pm-200", "pm-1400": "old-pm-1400"}
    assert ex.meic_twins(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert ex.meic_twins(tmp_path / "bad.json") == {}


def _attempts_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, outcome TEXT)"
    )
    conn.executemany("INSERT INTO entry_attempts (trade_date, arm, outcome) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_live_arms_by_session_come_from_the_attempts_not_the_ledgers_live_tag(tmp_path):
    db = tmp_path / "meic_trades.db"
    _attempts_db(
        db,
        [
            ("2026-11-24", "pm-200", "gate_blocked"),
            ("2026-11-24", "pm-200", "filled"),
            ("2026-11-25", "pm-1400", "filled"),
            ("2026-11-26", None, "filled"),
        ],
    )
    conn = ex._ro(db)
    try:
        assert ex.meic_live_arms(conn) == {"2026-11-24": {"pm-200"}, "2026-11-25": {"pm-1400"}}
    finally:
        conn.close()
    empty = tmp_path / "old.db"
    sqlite3.connect(str(empty)).close()
    conn = ex._ro(empty)
    try:
        assert ex.meic_live_arms(conn) == {}
    finally:
        conn.close()


def _breaks_db(path, rows):
    """A paper ledger holding only its measurement_breaks: (break_date, scope, kind) rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS measurement_breaks "
        "(id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT, reason TEXT)"
    )
    conn.executemany(
        "INSERT INTO measurement_breaks (break_date, scope, kind, reason) VALUES (?, ?, ?, 'x')", rows
    )
    conn.commit()
    conn.close()


def test_the_retirement_date_is_the_live_shadow_arm_retired_break(tmp_path):
    db = tmp_path / "paper_trades.db"
    _breaks_db(db, [("2026-11-10", "control", "arm_retired")])
    assert ex.shadow_retired_on(db) is None
    _breaks_db(db, [("2026-11-24", "live-shadow", "arm_retired")])
    assert ex.shadow_retired_on(db) == "2026-11-24"
    assert ex.shadow_retired_on(tmp_path / "missing.db") is None


def test_one_reads_the_per_arm_mirror_end_to_end(tmp_path, monkeypatch):
    """`_one` over a live ledger with attempts and a paper ledger carrying the retirement break: live's net
    is set against the twin of the arm it ran each day, and the report names an unmirrored day."""
    live_db = tmp_path / "meic_trades.db"
    _attempts_db(live_db, [("2026-11-24", "pm-200", "filled"), ("2026-11-25", "control", "filled")])
    paper_db = tmp_path / "paper_trades.db"
    _breaks_db(paper_db, [("2026-11-24", "live-shadow", "arm_retired")])
    live = [_rec("2026-11-24", "live", net=40.0), _rec("2026-11-25", "live", net=30.0)]
    paper = [
        _rec("2026-11-24", "live-shadow-pm-200", net=35.0),
        _rec("2026-11-24", "live-shadow-pm-1400", net=99.0),
    ]
    monkeypatch.setitem(ex.ORDER_READERS, "meic", lambda conn: [])
    monkeypatch.setattr(ex, "manual_closes", lambda conn: [])
    monkeypatch.setattr(ex, "kill_clears", lambda key: [])
    monkeypatch.setattr(ex, "config_commits", lambda name, **kw: [])
    monkeypatch.setattr(ex, "_records", lambda path, schema: live if path == live_db else paper)
    out = ex._one("meic", "meic", live_db, paper_db, ex.MEIC_MIRROR, twins=TWINS)
    assert out["unmirrored"] == ["2026-11-25"]
    assert out["gap"]["total"] == 5.0  # 40 - 35 on the mirrored day; the control day is left out
    assert "live-shadow before 2026-11-24" in out["mirror"]
    assert "2026-11-25" in ex.format_report({"meic": out})
