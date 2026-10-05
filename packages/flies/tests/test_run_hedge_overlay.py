"""Tests for the run-triggered book hedge replay (analytics.run_hedge_overlay)."""

import pytest

from cherrypick.flies import analytics, fly
from cherrypick.flies import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


OPEN_FEE = fly.single_leg_open_fee("SPX")


def hedge(premium=0.17, settle_value=0.0, best_mid=None):
    return {
        "strike": 5970.0,
        "delta": -0.05,
        "premium": premium,
        "fee": OPEN_FEE,
        "best_mid": best_mid,
        "settle_value": settle_value,
    }


def legged(
    conn,
    pid,
    *,
    day="2026-09-21",
    arm="control",
    side="put",
    entry_time="12:00:00",
    completed_at=None,
    pnl=60.0,
    hedge=None,
):
    """A settled legged row. `entry_time`/`completed_at` are clock times on `day` unless they
    already carry a date, so a test can write offset-aware stamps in full."""

    def stamp(t):
        return t if t is None or "T" in t else f"{day}T{t}"

    row = {
        "position_id": pid,
        "book_id": f"{day}:{arm}:SPX",
        "trade_date": day,
        "arm": arm,
        "entry_mode": "legged",
        "symbol": "SPX",
        "kind": "fly" if completed_at else "short_vertical",
        "side": side,
        "center": 6000.0,
        "wing_width": 5.0,
        "quantity": 1,
        "net": 0.6,
        "credit": 0.6,
        "fees": 6.89,
        "gross_pnl": pnl + 6.89,
        "pnl": pnl,
        "status": "settled",
        "entry_time": stamp(entry_time),
        "completed_at": stamp(completed_at),
    }
    if hedge:
        row.update({f"hedge_{k}": v for k, v in hedge.items()})
    dbmod.save_position(conn, row)


def cost(premium):
    return premium * 100 + OPEN_FEE


def bought(out, k):
    return [h["position_id"] for h in out["by_k"][str(k)]["hedges"]]


def test_a_spread_that_completed_before_the_next_entry_does_not_count_toward_the_run(conn):
    """The guard on `completed_at > t`. Session A: each spread completes before the next opens, so
    the open count never passes 1 and k=2 buys nothing. Session B: the first spread strands, so the
    second entry makes two open and buys ITS hedge; the second then completes before the third
    opens, so the count at the third is 2 again, never 3, and k=3 buys nothing. Count every prior
    entry as open and both sessions buy hedges they should not."""
    a, b = "2026-09-21", "2026-09-22"
    legged(conn, "A1", day=a, entry_time="10:00:00", completed_at="10:30:00", hedge=hedge(0.11))
    legged(conn, "A2", day=a, entry_time="11:00:00", completed_at="11:30:00", hedge=hedge(0.12))
    legged(conn, "A3", day=a, entry_time="12:00:00", pnl=-400.0, hedge=hedge(0.13))
    legged(conn, "B1", day=b, entry_time="10:00:00", pnl=-400.0, hedge=hedge(0.21, settle_value=10.0))
    legged(conn, "B2", day=b, entry_time="11:00:00", completed_at="11:30:00", hedge=hedge(0.22))
    legged(conn, "B3", day=b, entry_time="12:00:00", pnl=-400.0, hedge=hedge(0.23))

    out = analytics.run_hedge_overlay(conn, ks=(2, 3))
    assert bought(out, 2) == ["B2"]
    assert bought(out, 3) == []
    k2 = out["by_k"]["2"]
    assert k2["sessions_hedged"] == 1
    assert k2["on_losing_days"]["cost"] == pytest.approx(cost(0.22), abs=0.01)
    assert k2["hedged_net"] == pytest.approx(k2["unhedged_net"] - cost(0.22), abs=0.01)


def test_one_hedge_per_session_side_however_long_the_run(conn):
    """Four stranded puts in a row: k=2 buys once, on the second, and never again that session; the
    calls the same day are their own run."""
    for i, t in enumerate(("10:00:00", "10:30:00", "11:00:00", "11:30:00"), start=1):
        legged(conn, f"P{i}", entry_time=t, pnl=-300.0, hedge=hedge(0.10 + i / 100))
    legged(conn, "C1", side="call", entry_time="10:15:00", pnl=-300.0, hedge=hedge())
    legged(conn, "C2", side="call", entry_time="10:45:00", completed_at="11:00:00", hedge=hedge())

    out = analytics.run_hedge_overlay(conn, ks=(2, 3))
    assert bought(out, 2) == ["C2", "P2"]
    assert bought(out, 3) == ["P3"]


def test_with_k_1_and_one_entry_per_session_side_it_matches_hedge_overlay(conn):
    """k=1 buys the first entry's hedge on each (session, side); with one entry on each that is
    every hedge `hedge_overlay` prices, so the two must agree to the cent -- the check that they share
    one cost/recovery arithmetic, sell-at-N x included."""
    legged(conn, "P1", day="2026-09-21", pnl=-440.0, hedge=hedge(0.17, settle_value=20.0, best_mid=0.60))
    legged(
        conn, "C1", day="2026-09-21", side="call", completed_at="13:00:00", hedge=hedge(0.15, best_mid=0.2)
    )
    legged(conn, "P2", day="2026-09-22", completed_at="12:30:00", pnl=35.0, hedge=hedge(0.31, best_mid=0.5))
    legged(conn, "C2", day="2026-09-22", side="call", pnl=-200.0, hedge=hedge(0.09, settle_value=3.0))

    per_position = analytics.hedge_overlay(conn)
    run = analytics.run_hedge_overlay(conn, ks=(1,))["by_k"]["1"]
    assert run["hedges_bought"] == per_position["n"] == 4
    assert run["unhedged_net"] == per_position["unhedged_net"]
    assert run["hedged_net"] == per_position["hedged_net"]
    spent = run["on_losing_days"], run["on_other_days"]
    assert sum(s["cost"] for s in spent) == pytest.approx(per_position["hedge_cost"], abs=0.01)
    assert sum(s["recovered"] for s in spent) == pytest.approx(per_position["hedge_recovered"], abs=0.01)
    for n, cell in per_position["sell_at"].items():
        assert run["sell_at"][n]["sold"] == cell["sold"]
        assert run["sell_at"][n]["hedged_net"] == cell["hedged_net"]


def test_offset_aware_stamps_compare_as_instants_not_strings(conn):
    """A completion stamped in UTC (14:30Z = 10:30 ET) is before an 11:00 ET entry, though the
    strings sort the other way. Read as strings it would still look open and k=2 would buy."""
    day = "2026-09-21"
    legged(
        conn, "P1", entry_time=f"{day}T10:00:00-04:00", completed_at=f"{day}T14:30:00+00:00", hedge=hedge()
    )
    legged(conn, "P2", entry_time=f"{day}T11:00:00-04:00", pnl=-300.0, hedge=hedge())
    assert bought(analytics.run_hedge_overlay(conn, ks=(2,)), 2) == []


def test_drawdown_figures_and_the_losing_day_split(conn):
    """The hedge pays on the losing day and is pure cost on the winning one; worst session and
    losing-session counts are reported both ways, and robustness runs on the per-session
    difference (None here: two sessions is below the suite's thin line)."""
    win, lose = "2026-09-21", "2026-09-22"
    legged(conn, "W1", day=win, entry_time="10:00:00", pnl=20.0, hedge=hedge(0.20))
    legged(conn, "W2", day=win, entry_time="10:30:00", pnl=30.0, hedge=hedge(0.30))
    legged(conn, "L1", day=lose, entry_time="10:00:00", pnl=-300.0, hedge=hedge(0.20))
    legged(conn, "L2", day=lose, entry_time="10:30:00", pnl=-300.0, hedge=hedge(0.25, settle_value=5.0))

    k2 = analytics.run_hedge_overlay(conn, ks=(2,))["by_k"]["2"]
    payout = 5.0 * 100 - fly.expire_fee(1)
    assert k2["unhedged_net"] == pytest.approx(-550.0)
    assert k2["worst_session"]["unhedged"] == {"session": lose, "net": -600.0}
    assert k2["worst_session"]["hedged"]["net"] == pytest.approx(-600.0 + payout - cost(0.25), abs=0.01)
    assert k2["losing_sessions"] == {"unhedged": 1, "hedged": 1 if 50.0 - cost(0.30) >= 0 else 2}
    assert k2["on_losing_days"] == {
        "hedges": 1,
        "cost": pytest.approx(cost(0.25), abs=0.01),
        "recovered": pytest.approx(payout, abs=0.01),
    }
    assert k2["on_other_days"]["hedges"] == 1 and k2["on_other_days"]["recovered"] == 0.0
    assert k2["robustness"] is None


def test_unpriced_trigger_buys_nothing_and_unstamped_sessions_and_voids_are_left_out(conn):
    """The k-th entry carried no hedge: the run is counted unpriced, never priced off a later
    entry. A session with no stamped hedge at all is left out and counted; a voided row is gone."""
    legged(conn, "P1", entry_time="10:00:00", pnl=-300.0, hedge=hedge())
    legged(conn, "P2", entry_time="10:30:00", pnl=-300.0)  # the 2nd entry, unstamped
    legged(conn, "P3", entry_time="11:00:00", pnl=-300.0, hedge=hedge())
    legged(conn, "OLD", day="2026-08-11", pnl=-300.0)
    legged(conn, "V", day="2026-09-22", hedge=hedge())
    conn.execute("UPDATE fly_positions SET void_reason = 'test' WHERE position_id = 'V'")
    conn.commit()

    out = analytics.run_hedge_overlay(conn, ks=(2,))
    assert out["sessions"] == 1 and out["sessions_without_hedge"] == 1
    k2 = out["by_k"]["2"]
    assert k2["unpriced"] == 1 and k2["hedges_bought"] == 0
    assert k2["unhedged_net"] == k2["hedged_net"] == pytest.approx(-900.0)
