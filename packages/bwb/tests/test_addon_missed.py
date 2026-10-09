"""A missed add-on is skipped for good, and counted as missed -- never as a fire or a "never triggered".

2026-10-08: a power outage took the loop down 10:35-16:00 ET while SPX traded down through some near
wings, so some triggers may have been met with no tick to measure them. The module guesses no trigger
(rules 4 and 7), so those add-ons are marked missed (`bwb addon-missed`) instead: management never
arms or fires them, even when a later measured tick meets the trigger, because that would be a
different, later trade than the rule would have made.
"""

from __future__ import annotations

from cherrypick.bwb import analytics, cli, db, management

PARAMS = {**management.PARAM_DEFAULTS, "arm": "delta"}
MET = {"abs_delta": 0.60, "spot": 100.0, "gamma_flip": 90.0}  # the delta trigger, plainly met


def _position(**overrides):
    base = {"arm": "delta", "advice_params": None, "armed_at": None, "addon_fired_at": None}
    base.update(overrides)
    return base


def test_a_missed_add_on_never_arms_even_when_the_trigger_is_met():
    decision, latches = management.evaluate(
        _position(addon_missed_at="2026-10-08T20:30:00-04:00"),
        PARAMS,
        trigger_state={},
        tick=MET,
        addon_credit=None,
    )
    assert (decision.action, decision.reason) == ("hold", "addon_missed")
    assert latches["peak_abs_delta"] == 0.60  # the latches still update, as on control


def test_an_unmarked_position_still_arms():
    decision, _ = management.evaluate(_position(), PARAMS, trigger_state={}, tick=MET, addon_credit=None)
    assert decision.action == "arm"


def _ledger(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    for pid, arm, status, fired in [
        ("SPX:delta:2026-10-08", "delta", "open", None),
        ("SPX:delta:2026-10-07", "delta", "open", None),
        ("SPX:delta:2026-10-06", "delta", "open", "2026-10-07T10:11:09-04:00"),
        ("SPX:delta:2026-10-01", "delta", "closed", None),
    ]:
        db.save_position(
            conn,
            {
                "position_id": pid,
                "symbol": "SPX",
                "arm": arm,
                "entry_session": pid.rsplit(":", 1)[1],
                "structure_signature": pid,
                "quantity": 1,
                "expiration": "2026-10-09",
                "near_strike": 7750.0,
                "body_strike": 7745.0,
                "far_strike": 7735.0,
                "status": status,
                "addon_fired_at": fired,
            },
        )
    return conn


def test_the_plan_refuses_what_cannot_be_missed(tmp_path):
    conn = _ledger(tmp_path)
    plan = {
        p["position_id"]: p
        for p in cli.addon_missed_plan(
            conn, ["SPX:delta:2026-10-08", "SPX:delta:2026-10-06", "SPX:delta:2026-10-01", "nope"]
        )
    }
    assert plan["SPX:delta:2026-10-08"]["ok"] is True
    assert plan["SPX:delta:2026-10-06"]["reason"] == "add-on already fired"
    assert plan["SPX:delta:2026-10-01"]["reason"] == "status closed"
    assert plan["nope"]["reason"] == "no such position"


def test_applying_marks_the_row_journals_it_and_is_dry_run_by_default(tmp_path, capsys):
    conn = _ledger(tmp_path)
    path = str(tmp_path / "paper_trades.db")
    base = ["--db", path, "addon-missed", "--position-id", "SPX:delta:2026-10-08", "--reason", "outage"]

    assert cli.main(base) == 0  # dry run
    row = conn.execute(
        "SELECT addon_missed_at FROM bwb_positions WHERE position_id = 'SPX:delta:2026-10-08'"
    ).fetchone()
    assert row["addon_missed_at"] is None

    assert cli.main([*base, "--apply"]) == 0
    row = conn.execute(
        "SELECT addon_missed_at, addon_missed_reason FROM bwb_positions WHERE position_id = 'SPX:delta:2026-10-08'"
    ).fetchone()
    assert row["addon_missed_at"] and row["addon_missed_reason"] == "outage"
    journal = conn.execute(
        "SELECT mode, reason, detail FROM bwb_decisions WHERE reason = 'addon_missed'"
    ).fetchall()
    assert [(j["mode"], j["detail"]) for j in journal] == [("addon", "SPX:delta:2026-10-08: outage")]

    assert cli.main([*base, "--apply"]) == 1  # already marked: refused, nothing written twice


def test_a_missed_add_on_is_counted_apart_from_fires(tmp_path):
    conn = _ledger(tmp_path)
    cli.main(
        ["--db", str(tmp_path / "paper_trades.db"), "addon-missed", "--position-id", "SPX:delta:2026-10-08",
         "--reason", "outage", "--apply"]
    )  # fmt: skip
    delta = analytics.fire_counts(conn)["delta"]
    assert delta == {"positions": 4, "fired": 1, "missed": 1, "fire_rate": round(1 / 3, 4)}
