"""The `summary` verb of the shared calibration CLI (python -m cherrypick.core.metrics): the
one-normalisation-at-arbitrary-window-widths read behind the console's Reports summary page.

The discipline each test pins, in the suite's own vocabulary:
- the slices are ONE population (hero, by_arm and daily add up to the same rows);
- unknown capital is not free capital (`return.value` is None, coverage keeps the partial
  denominator visible) -- `return_on_capital`'s own refusal, at window width;
- `capture` follows `capture_rate`'s own FULL-coverage rule rather than a per-schema hand list;
- the ratio divides the UNROUNDED net sum while every displayed net is the cent-rounded key;
- filters narrow the slices and never the facets (the page's selects are driven by what is in
  the ledger, not by what survived the last filter).
"""

import argparse
import sqlite3

import pytest

from cherrypick.core.metrics import __main__ as cli


def _args(**kw):
    defaults = {"session": None, "symbol": None, "arm": None, "width": "all", "start": None, "end": None}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


@pytest.fixture
def meic_db(tmp_path):
    path = tmp_path / "meic_paper.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, "
        "exit_time TEXT, slippage_dollars REAL, wing_width REAL, net_credit REAL, "
        "quantity INTEGER, dollar_multiplier REAL)"
    )
    rows = [
        # control, day 1: +120 gross, capital (10-3)*100 = 700
        ("SPX", "control", 120.0, 5.0, "2026-08-20T15:45:00", 2.0, 10.0, 3.0, 1, 100.0),
        # control, day 2: -60 gross, same capital
        ("SPX", "control", -60.0, 5.0, "2026-08-21T15:45:00", 2.0, 10.0, 3.0, 1, 100.0),
        # aggressive, day 1: +300 gross, capital (20-6)*100 = 1400
        ("SPX", "aggressive", 300.0, 8.0, "2026-08-20T15:45:00", 3.0, 20.0, 6.0, 1, 100.0),
        # a row that cannot support the capital formula (no strikes recorded): still counts in
        # the hero's money, drops from the denominator, and shows up in the coverage count
        ("SPX", None, 10.0, 1.0, "2026-08-19T15:45:00", None, None, None, None, None),
        # still open (exit_time NULL) -- every closed reader excludes it already
        ("SPX", "control", 0.0, 0.0, None, None, None, None, None, None),
    ]
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, "
        "wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path)


def test_summary_slices_are_one_population(meic_db):
    out = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-21", width="week"))
    assert out["ok"] is True
    # Window: floor 2026-08-15 -> all three sessions. Money rows add up: gross - fees = net.
    hero = out["hero"]
    assert hero["trades"] == 4
    assert hero["sessions"] == 3
    assert hero["gross"] == pytest.approx(370.0)
    assert hero["fees"] == pytest.approx(19.0)
    assert hero["net"] == pytest.approx(hero["gross"] - hero["fees"])
    assert hero["wins"] == 3
    assert hero["win_pct"] == 75.0
    # Capital: 700 + 700 + 1400 over the three rows that can support it; the untagged row rides
    # in the coverage count so a partial denominator is never read as a whole one.
    assert hero["capital"] == pytest.approx(2800.0)
    assert hero["capital_coverage"] == "3/4"
    # by_arm nets add up to the hero's, per the same rows.
    assert sum(a["net"] for a in out["by_arm"].values()) == pytest.approx(hero["net"])


def test_return_on_risk_and_per_arm_returns(meic_db):
    out = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-21", width="week"))
    # 351.0 / 2800.0 = 12.5357% -- the unrounded numerator over the capital sum.
    assert out["return"]["value"] == pytest.approx(12.5357)
    assert out["return"]["coverage"] == "3/4"
    by_arm = out["by_arm"]
    assert by_arm["control"]["return"] == pytest.approx(3.5714)  # 50/1400, rounded to 4dp
    assert by_arm["aggressive"]["return"] == pytest.approx(20.8571)
    # An arm whose rows carry no capital refuses the ratio rather than reading zero.
    assert by_arm["unassigned"]["return"] is None
    assert by_arm["unassigned"]["capital"] is None


def test_unknown_capital_is_not_free_capital(meic_db):
    # Narrow to the untagged row alone: capital coverage 0/1 -> the window's ratio is None,
    # with the coverage string saying so -- never a zero.
    out = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-19", width="day"))
    assert out["hero"]["capital"] is None
    assert out["hero"]["capital_coverage"] == "0/1"
    assert out["return"]["value"] is None
    assert out["return"]["coverage"] == "0/1"


def test_capture_follows_full_coverage_rule(meic_db):
    out = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-21", width="week"))
    # Three of four rows carry max_profit -> NOT full coverage -> None (capture_rate's own rule).
    assert out["capture"]["n"] == 3
    assert out["capture"]["value"] is None
    # Day 2026-08-20: both rows carry max_profit (300 and 600) -> full coverage -> the value.
    # net over the day = (120-5) + (300-8) = 407; ceilings 300+600 = 900; capture_rate rounds
    # its ratio to 4dp.
    day = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-20", width="day"))
    assert day["capture"]["n"] == 2
    assert day["capture"]["value"] == pytest.approx(0.4522)


def test_day_window_scopes_to_one_session_and_filters_narrow(meic_db):
    day = cli.cmd_summary(
        _args(db=meic_db, schema="meic_ic", session="2026-08-20", width="day", arm="control")
    )
    assert day["hero"]["trades"] == 1
    assert day["hero"]["net"] == pytest.approx(115.0)
    assert day["hero"]["capital"] == pytest.approx(700.0)
    # The facets stay WHOLE-LEDGER under filters -- the page's selects are driven by what the
    # ledger holds, not by what survived the last filter.
    assert day["facets"]["arms"] == ["aggressive", "control", "unassigned"]
    assert day["facets"]["sessions"] == ["2026-08-19", "2026-08-20", "2026-08-21"]
    assert day["by_arm"] == {"control": day["by_arm"]["control"]}
    # The window states the sessions it resolved to, not the calendar span asked for.
    assert day["window"] == {"from": "2026-08-20", "to": "2026-08-20"}
    sym = cli.cmd_summary(
        _args(db=meic_db, schema="meic_ic", session="2026-08-20", width="day", symbol="NQN")
    )
    assert sym["hero"]["trades"] == 0
    assert sym["return"]["coverage"] == "0/0"
    assert sym["window"] == {"from": None, "to": None}


def test_week_window_is_seven_inclusive_days(meic_db):
    # Anchor 2026-08-21 -> floor 2026-08-15: every session in the fixture is inside.
    week = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-21", width="week"))
    assert week["hero"]["trades"] == 4
    # The 19th is six days back from the 21st, so a five-day width would drop it -- the window
    # boundaries are inclusive calendar days, exactly the number the width name promises.
    five = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-08-20", width="week"))
    assert five["hero"]["trades"] == 3  # the 19th sits outside 2026-08-16..20
    assert len(five["daily"]) == 2


def test_start_end_bound_the_window_like_the_read_verb(meic_db):
    # The suite epoch's bound rides --start/--end for the schemas that carry no era stamp; the
    # reader takes them, then the window resolves on the dates that remain.
    out = cli.cmd_summary(
        _args(db=meic_db, schema="meic_ic", start="2026-08-20", end="2026-08-21", width="all")
    )
    assert out["hero"]["trades"] == 3  # the 19th is outside the bound, facets included
    assert out["facets"]["sessions"] == ["2026-08-20", "2026-08-21"]


def test_anchor_defaults_to_latest_session_and_unknown_resolves_to_it(meic_db):
    all_out = cli.cmd_summary(_args(db=meic_db, schema="meic_ic"))
    assert all_out["hero"]["trades"] == 4  # unbounded
    assert len(all_out["daily"]) == 3
    day = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", width="day"))
    assert day["hero"]["trades"] == 1  # the latest session (2026-08-21) alone
    unknown = cli.cmd_summary(_args(db=meic_db, schema="meic_ic", session="2026-01-01", width="day"))
    assert unknown["hero"]["trades"] == day["hero"]["trades"]


def test_empty_ledger_is_a_real_answer(meic_db, tmp_path):
    empty = str(tmp_path / "empty.db")
    conn = sqlite3.connect(empty)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, "
        "exit_time TEXT, slippage_dollars REAL, wing_width REAL, net_credit REAL, "
        "quantity INTEGER, dollar_multiplier REAL)"
    )
    conn.commit()
    conn.close()
    out = cli.cmd_summary(_args(db=empty, schema="meic_ic"))
    assert out["ok"] is True
    assert out["hero"]["trades"] == 0
    assert out["return"] == {"value": None, "coverage": "0/0"}
    assert out["by_arm"] == {}
    assert out["daily"] == []
    assert out["facets"] == {"sessions": [], "symbols": [], "arms": []}


def test_unreadable_db_reports_not_throws(tmp_path):
    out = cli.cmd_summary(_args(db=str(tmp_path / "absent.db"), schema="meic_ic"))
    assert out["ok"] is False
    assert "cannot read" in out["error"]


@pytest.mark.skipif(
    not sqlite3.connect("file:/home/pitufo/.cherrypick/data/meic/paper_trades.db?mode=ro", uri=True)
    .execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ic_trades'")
    .fetchone()[0],
    reason="no local meic paper ledger",
)
def test_live_ledger_matches_the_review_artifact():
    """Where their windows coincide -- the day resolved to the review's own session -- the
    summary's ratio is the fact set's `on_max_risk`, to the fourth decimal. Skipped, visibly,
    wherever the ledger or the artifact is not on this machine."""
    import json
    import os

    db = "/home/pitufo/.cherrypick/data/meic/paper_trades.db"
    sessions = [
        r[0]
        for r in sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
            "SELECT DISTINCT substr(exit_time, 1, 10) FROM ic_trades WHERE exit_time IS NOT NULL"
        )
    ]
    checked = 0
    for day in sessions:
        artifact = f"/home/pitufo/.cherrypick/data/review/eod-{day}.json"
        if not os.path.exists(artifact):
            continue
        facts = json.load(open(artifact))
        meic = facts.get("modules", {}).get("meic", {})
        if not meic.get("ok") or not isinstance(meic.get("return", {}).get("on_max_risk"), (int, float)):
            continue
        out = cli.cmd_summary(_args(db=db, schema="meic_ic", session=day, width="day"))
        assert out["hero"]["net"] == pytest.approx(meic["return"]["net"], abs=0.005)
        assert out["hero"]["capital"] == pytest.approx(meic["return"]["capital_at_risk"], abs=0.005)
        assert out["return"]["value"] == pytest.approx(meic["return"]["on_max_risk"] * 100, abs=1e-4)
        checked += 1
    assert checked > 0, "review artifacts exist but none carried a meic return"
