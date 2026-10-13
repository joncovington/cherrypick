"""The three evidence reads the console bridges: live vs paper paired per session, the gate arms
against control over each arm's own window, and the selector's sessions with its model's intervals.

Each is read-only and each is pinned on a shape where the obvious shortcut gives a different answer:
a pairing across the completion-rule break, a gate arm whose window starts after the era's, a
selector twin session that must not count as a departure.
"""

import inspect
import json
import sqlite3

from cherrypick.flies import analytics, cli, engine, selector
from cherrypick.flies import db as dbmod


def _ledger(path) -> sqlite3.Connection:
    dbmod.connect(str(path)).close()  # migrate once; the reads themselves only ever read
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _pos(conn, pid, day, arm, *, center, side="put", kind="fly", credit=2.4, debit=2.2, fees=7.0,
         pnl=10.0, status="settled", minute="10:15", recon=None, far=None):  # fmt: skip
    conn.execute(
        "INSERT INTO fly_positions (position_id, trade_date, arm, symbol, entry_mode, kind, side, center,"
        " wing_width, far_width, quantity, credit, debit, fees, gross_pnl, pnl, status, entry_time,"
        " broker_reconciliation_status)"
        " VALUES (?, ?, ?, 'SPX', 'legged', ?, ?, ?, 5, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, day, arm, kind, side, center, far, credit, debit if kind == "fly" else None, fees,
         pnl + fees, pnl, status, f"{day}T{minute}:00-04:00", recon),
    )  # fmt: skip
    conn.commit()


def _break(conn, day, kind, scope="*"):
    dbmod.record_measurement_break(conn, break_date=day, kind=kind, reason=f"{kind} test", scope=scope)
    conn.commit()


# --------------------------------------------------------------------------- live vs paper
def test_live_paper_gap_pairs_by_structure_and_splits_at_the_completion_rule(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _break(paper, "2026-10-05", "completion_rule")
    # 10-02: one live entry, its paper twin 12 minutes earlier; paper also holds one live never took.
    _pos(live, "L1", "2026-10-02", "control", center=7700, credit=2.45, debit=2.15, fees=6.5, pnl=20.0,
         minute="10:22", recon="reconciled")  # fmt: skip
    _pos(paper, "P1", "2026-10-02", "control", center=7700, credit=2.30, debit=2.25, fees=7.0, pnl=-5.0,
         minute="10:10")  # fmt: skip
    _pos(paper, "P2", "2026-10-02", "control", center=7710, kind="short_vertical", pnl=-300.0)
    # 10-06: the same structure 45 minutes apart is NOT a twin; a different centre never is.
    _pos(live, "L2", "2026-10-06", "control", center=7750, minute="11:00", pnl=15.0)
    _pos(paper, "P3", "2026-10-06", "control", center=7750, minute="10:15", pnl=12.0)
    _pos(paper, "P4", "2026-10-06", "control", center=7755, minute="11:00", pnl=11.0)

    out = analytics.live_paper_gap(live, paper, arm="control")
    assert out["completion_rule_from"] == "2026-10-05"
    first, second = out["sessions"]
    assert (first["session"], first["completion_rule"]) == ("2026-10-02", "modelled_debit")
    assert (second["session"], second["completion_rule"]) == ("2026-10-06", "pays_limit")

    assert (first["matched"], first["unmatched_live"], first["unmatched_paper"]) == (1, 0, 1)
    (pair,) = first["pairs"]
    assert pair["paper_position"] == "P1" and pair["gap_min"] == 12.0
    assert pair["credit_gap"] == 0.15  # live's broker credit over paper's modelled credit
    assert pair["debit_gap"] == 0.1  # live paid less to complete
    assert pair["fee_gap"] == 0.5 and pair["live_fees_source"] == "broker"
    assert pair["net_gap"] == 25.0
    # The book-level gap is over whole books of different sizes: 20 against -5 - 300.
    assert first["net_gap"] == 325.0
    assert first["completion_gap"] == 0.5 - 1.0  # paper 1 of 2 completed, live 1 of 1

    assert (second["matched"], second["unmatched_live"]) == (0, 1)
    # Never pooled across the break.
    assert out["eras"]["modelled_debit"]["sessions"] == 1 and out["eras"]["pays_limit"]["sessions"] == 1
    assert out["eras"]["pays_limit"]["credit_gap_sum"] is None


def test_live_paper_gap_leaves_voided_rows_out_on_both_sides(tmp_path):
    """A voided row stays `settled` with its pnl (`void_reason` alone marks it): the 10-08 outage left
    22 such paper rows. Counted, P2 would pair as live's twin, halve paper's completion and take
    -300 off its net; a session whose only live row is void is no session at all."""
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _pos(live, "L1", "2026-10-08", "control", center=7700, minute="10:22", pnl=20.0)
    _pos(live, "LV", "2026-10-07", "control", center=7700, pnl=5.0)
    _pos(paper, "P1", "2026-10-08", "control", center=7700, minute="10:40", pnl=12.0)
    _pos(paper, "P2", "2026-10-08", "control", center=7700, kind="short_vertical", minute="10:20", pnl=-300.0)
    _pos(paper, "P7", "2026-10-07", "control", center=7700, pnl=9.0)
    live.execute("UPDATE fly_positions SET void_reason = 'outage' WHERE position_id = 'LV'")
    paper.execute("UPDATE fly_positions SET void_reason = 'outage' WHERE position_id = 'P2'")
    live.commit()
    paper.commit()

    (day,) = analytics.live_paper_gap(live, paper, arm="control")["sessions"]
    assert day["session"] == "2026-10-08"
    assert day["paper"]["entries"] == 1 and day["completion_gap"] == 0.0
    assert day["net_gap"] == 8.0
    assert day["pairs"][0]["paper_position"] == "P1"


def test_live_paper_gap_withholds_net_until_the_session_settles(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _pos(live, "L1", "2026-10-12", "control", center=7700, status="open", kind="short_vertical")
    _pos(paper, "P1", "2026-10-12", "control", center=7700)
    (s,) = analytics.live_paper_gap(live, paper)["sessions"]
    assert s["live"]["net"] is None and s["net_gap"] is None
    assert s["pairs"][0]["net_gap"] is None  # an open row has no net to compare


# --------------------------------------------------------------------------- gate arms
def test_every_named_refusal_is_one_the_engine_returns():
    """The gate map names reasons by string; a renamed engine reason would silently count zero."""
    source = inspect.getsource(engine)
    for arm, spec in analytics.GATE_ARMS.items():
        assert arm in engine.ARMS, arm
        for reason in spec.get("refusals", ()):
            assert f'"{reason}"' in source, f"{arm}: {reason} is not an engine reason"


def test_gate_sessions_use_each_arms_own_window(tmp_path):
    conn = _ledger(tmp_path / "paper.db")
    _break(conn, "2026-10-05", "completion_rule")
    _break(conn, "2026-10-05", "arm_added", scope="vol-floor")
    _break(conn, "2026-10-12", "arm_added", scope="restack")
    _break(conn, "2026-10-06", "gex_surface")  # never bounds an era
    for day, ctl in (("2026-10-02", 50.0), ("2026-10-06", 40.0), ("2026-10-12", -30.0)):
        _pos(conn, f"C-{day}", day, "control", center=7700, pnl=ctl)
    _pos(conn, "V-02", "2026-10-02", "vol-floor", center=7700, pnl=999.0)  # before its window
    _pos(conn, "V-06", "2026-10-06", "vol-floor", center=7700, pnl=10.0)
    conn.execute(
        "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, mode, outcome, block_detail)"
        " VALUES ('2026-10-06T10:01:00', '2026-10-06', 'vol-floor', 'SPX', 'legged', 'gate_blocked',"
        " 'straddle_below_floor')"
    )
    # 10-12: the arm ran (it recorded a window refusal) and its own gate never fired.
    conn.execute(
        "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, mode, outcome, block_detail)"
        " VALUES ('2026-10-12T09:31:00', '2026-10-12', 'vol-floor', 'SPX', 'legged', 'window_blocked',"
        " 'before_open_gate')"
    )
    _pos(conn, "R1", "2026-10-12", "restack", center=7700, pnl=5.0, minute="10:15")
    _pos(conn, "R2", "2026-10-12", "restack", center=7700, pnl=5.0, minute="11:15")
    conn.commit()

    out = analytics.gate_sessions(conn, session="2026-10-12")
    arms = {a["arm"]: a for a in out["arms"]}
    assert out["era_start"] == "2026-10-05"
    vf = arms["vol-floor"]
    assert vf["window_start"] == "2026-10-05"
    assert [s["session"] for s in vf["sessions"]] == ["2026-10-06", "2026-10-12"]
    acted = vf["sessions"][0]
    assert acted["acted"] and acted["refused_ticks"] == 1 and acted["diff"] == -30.0
    quiet = vf["sessions"][1]
    assert not quiet["acted"] and quiet["arm_net"] == 0.0 and quiet["diff"] == 30.0
    assert vf["acted"]["diff"] == -30.0 and vf["quiet"]["diff"] == 30.0

    rs = arms["restack"]
    assert rs["window_start"] == "2026-10-12"
    (day,) = rs["sessions"]
    assert day["restacks"] == 1 and day["acted"] and day["diff"] == 40.0


def test_gate_sessions_leave_voided_rows_out(tmp_path):
    """A voided row stays `settled` (`void_reason` alone marks it). Counted, a session whose only
    arm row is void would be listed, and a void row's close tag would read as the gate acting."""
    conn = _ledger(tmp_path / "paper.db")
    _pos(conn, "C-08", "2026-10-08", "control", center=7700, pnl=10.0)
    _pos(conn, "C-09", "2026-10-09", "control", center=7700, pnl=10.0)
    _pos(conn, "T-08", "2026-10-08", "trend-rule", center=7700, pnl=-200.0)
    _pos(conn, "T-09", "2026-10-09", "trend-rule", center=7700, pnl=10.0)
    _pos(conn, "TV-09", "2026-10-09", "trend-rule", center=7710, pnl=-50.0, minute="11:00")
    conn.execute("UPDATE fly_positions SET close_tag_at = '2026-10-09T11:30:00' WHERE position_id = 'TV-09'")
    conn.execute("UPDATE fly_positions SET void_reason = 'outage' WHERE position_id IN ('T-08', 'TV-09')")
    conn.commit()

    arms = {a["arm"]: a for a in analytics.gate_sessions(conn, session="2026-10-09")["arms"]}
    (day,) = arms["trend-rule"]["sessions"]
    assert day["session"] == "2026-10-09"
    assert day["close_tags"] == 0 and not day["acted"]
    assert day["arm_entries"] == 1 and day["diff"] == 0.0


# --------------------------------------------------------------------------- selector
def _choice(conn, day, chosen, reason):
    dbmod.record_selector_choice(
        conn, ts=f"{day}T10:00:00", trade_date=day, symbol="SPX", model_id=None, candidates=[],
        chosen=chosen, reason=reason,
    )  # fmt: skip


def test_selector_twin_sessions_are_not_departures(tmp_path):
    conn = _ledger(tmp_path / "paper.db")
    # 10-19: an exact twin of control. 10-20: it skipped control's second entry.
    for day in ("2026-10-19", "2026-10-20"):
        _pos(conn, f"C1-{day}", day, "control", center=7700, pnl=10.0)
        _pos(conn, f"S1-{day}", day, "selector", center=7700, pnl=10.0)
        _choice(conn, day, "control+vol-floor", "default")
    _pos(conn, "C2", "2026-10-20", "control", center=7710, pnl=-50.0, minute="11:00")
    _choice(conn, "2026-10-20", None, "default_cell_negative")

    out = analytics.selector_sessions(conn)
    twin, dep = out["sessions"]
    assert not twin["departed"] and twin["departing_ticks"] == 0
    assert dep["departed"] and dep["departing_ticks"] == 1 and dep["diff"] == 50.0
    assert out["departing"]["sessions"] == 1 and out["departing"]["first_read"] is None
    assert out["departing"]["first_read_at"] == analytics.SELECTOR_FIRST_READ_DEPARTING


def test_selector_model_readout_marks_clear_cells():
    model = selector.fit(
        [
            {"arm": "control", "trade_date": f"2026-10-{d:02d}", "pnl": 100.0 + d, "kind": "fly",
             "entry_vol_bucket": "normal", "entry_trend_bucket": "flat"}
            for d in range(5, 12)
        ],
        through="2026-10-13",
        sources=["control"],
    )  # fmt: skip
    out = analytics.selector_sessions(_empty(), latest_model=model)
    latest = out["latest_model"]
    assert latest["ok"] and latest["model_id"] == "selector-2026-10-13-v2"
    (cell,) = latest["cells"]
    assert (
        cell["cell"] == "normal|flat" and cell["eligible"] and cell["clear_above"] and not cell["clear_below"]
    )

    stale = {**model, "procedure_version": 1}
    out = analytics.selector_sessions(_empty(), latest_model=stale)
    assert out["latest_model"]["reason"] == "model_wrong_procedure" and out["latest_model"]["cells"] == []


def _empty():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE fly_positions (trade_date TEXT, arm TEXT)")
    return conn


# --------------------------------------------------------------------------- the verbs
def test_verbs_never_write_a_ledger(tmp_path, capsys):
    path = tmp_path / "paper.db"
    conn = _ledger(path)
    _pos(conn, "C1", "2026-10-12", "control", center=7700)
    conn.close()
    before = path.read_bytes()
    for verb in ("gate-sessions", "selector-sessions"):
        assert cli.main(["--db", str(path), verb]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["--db", str(path), "live-gap", "--live-db", str(tmp_path / "absent.db")]) == 0
    assert json.loads(capsys.readouterr().out)["ledger"] == "absent"
    assert path.read_bytes() == before
