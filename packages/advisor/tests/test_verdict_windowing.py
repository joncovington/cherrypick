"""The verdict window: both sides of a pair read the SAME sessions.

`reading_pair`'s docstring always claimed the two books ran the same sessions; until 2026-08-20 the
base side actually pooled its entire ledger history against an advised tag that only exists for the
experiment's sessions. These pin the fix — an experiment's verdict starts at the first session it
could have issued advice for, and pre-experiment base history stays out of the pair.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.advisor import clock, store, verdicts
from tests.fakes import MEIC_DDL, insert, make_db

OLD_SESSION = "2026-07-01"  # pre-experiment history that must NOT reach the pair
SESSION = "2026-08-20"  # the experiment's created_session
WINDOW_START = clock.next_session(SESSION)  # 2026-08-21 — the first advised session


def _seed(home: Path) -> None:
    meic = make_db(home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    rows = []
    # Twenty old base sessions: hugely profitable, and entirely outside the window.
    for i in range(20):
        rows.append(
            {
                "trade_date": f"2026-07-{i + 1:02d}",
                "symbol": "SPX",
                "risk_profile": "control",
                "net_credit": 2.4,
                "wing_width": 20,
                "quantity": 1,
                "pnl": 500.0,
                "fees": 6.0,
                "status": "closed",
                "exit_time": f"2026-07-{i + 1:02d}T20:10:00",  # meic_ic: closed = exit_time set
                "ic_order_id": f"old-{i}",
                "created_at": f"2026-07-{i + 1:02d}T14:31:00",
            }
        )
    # One in-window session for both sides of the pair.
    for tag, pnl, oid in (("control", -50.0, "new-base"), ("advised:control", 25.0, "new-adv")):
        rows.append(
            {
                "trade_date": WINDOW_START,
                "symbol": "SPX",
                "risk_profile": tag,
                "net_credit": 2.4,
                "wing_width": 20,
                "quantity": 1,
                "pnl": pnl,
                "fees": 6.0,
                "status": "closed",
                "exit_time": f"{WINDOW_START}T20:10:00",
                "ic_order_id": oid,
                "created_at": f"{WINDOW_START}T14:31:00",
            }
        )
    insert(meic, "ic_trades", rows)


def test_the_base_side_is_windowed_to_the_experiment(tmp_home):
    _seed(tmp_home)
    experiment = {
        "id": "exp-x",
        "module": "meic",
        "base_profile": "control",
        "params_json": '{"stop_trigger_ratio": 1.1}',
        "created_session": SESSION,
        "sessions_run": 1,
    }
    body = verdicts.for_experiment(experiment)
    pair = body["pairs"][0]

    # The base reading holds ONLY the in-window session — the twenty profitable July sessions are
    # out, which is the whole point: without the window the base would show 21 samples of mostly
    # +500 against an advised book that never saw those markets.
    assert pair["base"] is not None
    assert pair["base"]["sample"] == 1
    assert pair["advised"]["sample"] == 1


def test_no_created_session_degrades_to_unwindowed(tmp_home):
    _seed(tmp_home)
    experiment = {
        "id": "exp-y",
        "module": "meic",
        "base_profile": "control",
        "params_json": "{}",
        "created_session": None,
        "sessions_run": 0,
    }
    pair = verdicts.for_experiment(experiment)["pairs"][0]
    # Every base row — the pre-fix behavior, kept for callers that have no window to apply.
    assert pair["base"]["sample"] == 21


def test_every_advisable_module_has_a_scoreable_ledger_schema():
    """bwb reached an active experiment (exp-2026-08-27-bwb-1, three sessions in) while
    arm_readings.bwb stayed an empty object: `verdicts.SCHEMAS` — a hand-kept map — lacked it, so
    `closed_records` found no reader and the experiment had nothing to be scored against. The same
    failure shape the review package had on 08-26. Driven off the advisor's own module list and
    core's reader registry, so a module is covered the moment it can be advised."""
    from cherrypick.core import ledgers as _ledgers

    from cherrypick.advisor import bounds, verdicts

    missing = [m for m in bounds.MODULES if m not in verdicts.SCHEMAS]
    assert not missing, f"advisable modules with no ledger schema in verdicts.SCHEMAS: {missing}"
    unreadable = [m for m, s in verdicts.SCHEMAS.items() if s not in _ledgers.READERS]
    assert not unreadable, f"schemas with no core.ledgers reader: {unreadable}"


# --------------------------------------------------------------------------- the far end


def _row(tag, session, pnl, oid, experiment_id=None):
    return {
        "trade_date": session,
        "symbol": "SPX",
        "risk_profile": tag,
        "net_credit": 2.4,
        "wing_width": 20,
        "quantity": 1,
        "pnl": pnl,
        "fees": 6.0,
        "status": "closed",
        "exit_time": f"{session}T20:10:00",
        "ic_order_id": oid,
        "created_at": f"{session}T14:31:00",
        "experiment_id": experiment_id,
    }


def test_a_concluded_experiment_is_windowed_to_its_last_session(tmp_home):
    """A verdict is recomputed whenever a recommendation is attached, and the window had no far
    end -- a closing verdict on an expired experiment, attached after its successor had started,
    pooled the successor's rows into the old experiment's book. The concluding session (from the
    journal) closes it, inclusive: rows entered on the kill day are still the killed one's."""
    meic = make_db(tmp_home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    after = clock.next_session(WINDOW_START)
    insert(
        meic,
        "ic_trades",
        [
            _row("control", WINDOW_START, -50.0, "b1"),
            _row("advised:control", WINDOW_START, 25.0, "a1"),
            _row("control", after, 999.0, "b2"),  # the successor's session
            _row("advised:control", after, 999.0, "a2"),
        ],
    )
    experiment = {
        "id": "exp-x",
        "module": "meic",
        "base_profile": "control",
        "params_json": '{"stop_trigger_ratio": 1.1}',
        "created_session": SESSION,
        "status": "expired",
        "sessions_run": 1,
    }
    conn = store.connect()
    store.insert_experiment(conn, {**experiment, "expires_after_sessions": 1})
    store.journal(conn, "exp-x", "expired", session=WINDOW_START)
    conn.close()
    assert verdicts.concluded_session(experiment) == WINDOW_START
    pair = verdicts.for_experiment(experiment)["pairs"][0]
    assert pair["advised"]["sample"] == 1 and pair["base"]["sample"] == 1
    assert pair["advised"]["net_pnl"] == 19.0  # 25 - 6, never the 999 after the end
    # Still running: the far end stays open.
    assert verdicts.concluded_session({**experiment, "status": "active"}) is None
    assert verdicts.for_experiment({**experiment, "status": "active"})["pairs"][0]["base"]["sample"] == 2


def test_the_advised_side_keeps_only_this_experiments_rows_or_unstamped_ones(tmp_home):
    """One `advised:<base>` tag serves every experiment on that base in turn (2026-09-16): inside
    a shared window the stamp tells them apart. Unstamped rows (before the stamp) still count; a
    row stamped with another experiment never does. The base side is untouched by the filter."""
    meic = make_db(tmp_home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    insert(
        meic,
        "ic_trades",
        [
            _row("control", WINDOW_START, -50.0, "b1"),
            _row("advised:control", WINDOW_START, 25.0, "mine", experiment_id="exp-x"),
            _row("advised:control", WINDOW_START, 10.0, "unstamped"),
            _row("advised:control", WINDOW_START, 500.0, "theirs", experiment_id="exp-other"),
        ],
    )
    experiment = {
        "id": "exp-x",
        "module": "meic",
        "base_profile": "control",
        "params_json": '{"stop_trigger_ratio": 1.1}',
        "created_session": SESSION,
        "status": "active",
        "sessions_run": 1,
    }
    pair = verdicts.for_experiment(experiment)["pairs"][0]
    assert pair["advised"]["sample"] == 2
    assert pair["advised"]["net_pnl"] == (25.0 - 6.0) + (10.0 - 6.0)
    assert pair["base"]["sample"] == 1
