"""Every verdict pair carries the paired, session-by-session read of the advised book against its base.

`delta` subtracts two totals; it cannot say whether the advised book was ahead on most days or on
one. `paired` (core.compare.pair_with_control, the console compare page's rule) can: the mean
difference per session with a seeded interval, the exact sign test, and how many more sessions the
interval needs. It sits beside the qualification and never moves it.
"""

from __future__ import annotations

from pathlib import Path

from fakes import MEIC_DDL, insert, make_db

from cherrypick.advisor import clock, factpack, verdicts

SESSION = "2026-08-20"
DAYS = [clock.next_session(SESSION)]
for _ in range(7):
    DAYS.append(clock.next_session(DAYS[-1]))


def _row(day: str, tag: str, pnl: float, oid: str) -> dict:
    return {
        "trade_date": day,
        "symbol": "SPX",
        "risk_profile": tag,
        "net_credit": 2.4,
        "wing_width": 20,
        "quantity": 1,
        "pnl": pnl,
        "fees": 6.0,
        "status": "closed",
        "exit_time": f"{day}T20:10:00",
        "ic_order_id": oid,
        "created_at": f"{day}T14:31:00",
    }


def _seed(home: Path) -> None:
    meic = make_db(home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    rows = []
    for i, day in enumerate(DAYS):
        rows.append(_row(day, "control", 100.0, f"b-{i}"))
        # advised ahead by 40 every day but one, where it is behind by 10
        rows.append(_row(day, "advised:control", 90.0 if i == 3 else 140.0, f"a-{i}"))
    rows.append(_row(DAYS[-1], "control", 50.0, "b-extra"))  # a second base trade on the last day
    insert(meic, "ic_trades", rows)


def _pair(tmp_home):
    _seed(tmp_home)
    experiment = {
        "id": "exp-p",
        "module": "meic",
        "base_profile": "control",
        "params_json": '{"stop_trigger_ratio": 1.1}',
        "created_session": SESSION,
        "sessions_run": len(DAYS),
    }
    return verdicts.for_experiment(experiment)["pairs"][0]


def test_the_pair_carries_a_paired_session_read(tmp_home):
    paired = _pair(tmp_home)["paired"]
    assert paired["sessions"] == len(DAYS) and paired["paired_on"] == "session"
    # net of $6 fees a trade: +40 on six days, -10 on day 3, and -4 on the last (134 - 94 - 44)
    assert paired["advised_better"] == 6 and paired["base_better"] == 2 and paired["ties"] == 0
    assert paired["mean_diff"] == (6 * 40 - 10 - 4) / 8
    assert paired["total_diff"] == 226.0
    assert paired["sign_test_p"] == round((1 + 8 + 28) * 2 / 256, 4)  # exact, two-sided
    assert paired["ci"][0] <= paired["mean_diff"] <= paired["ci"][1]
    assert paired["ci_level"] == 0.95
    assert isinstance(paired["sessions_to_decision"], int)
    assert paired["advised_only_sessions"] == 0 and paired["base_only_sessions"] == 0


def test_the_qualification_and_delta_are_unchanged_by_it(tmp_home):
    pair = _pair(tmp_home)
    assert set(pair["delta"]) == {"net_pnl", "win_rate", "return_on_capital", "session_sharpe"}
    assert pair["underpowered"] is True  # 8 sessions: below the 14-session gate, as before


def test_the_fact_pack_does_not_carry_it(tmp_home):
    """The model's input is a declared-boundary surface; the paired block reaches the stored verdict
    and the console, not the pack, until a boundary says otherwise."""
    pair = _pair(tmp_home)
    compact = factpack._compact_verdict({"pairs": [pair]})
    assert "paired" not in compact["pairs"][0]
    assert "paired" not in (compact["pairs"][0]["delta"] or {})


def test_no_rows_means_no_paired_read(tmp_home):
    make_db(tmp_home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    body = verdicts.for_experiment(
        {
            "id": "exp-e",
            "module": "meic",
            "base_profile": "control",
            "params_json": "{}",
            "created_session": SESSION,
            "sessions_run": 0,
        }
    )
    assert body["pairs"][0]["paired"] is None
