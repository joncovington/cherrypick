"""Tests for the EOD report files — the module's remaining read surface.

The dashboard and section-card cases went with those surfaces; the console reads the ledger through
`analytics.py` directly.
"""

import pytest
from test_analytics import position

from cherrypick.flies import analytics
from cherrypick.flies import db as dbmod
from cherrypick.flies import eod as eodmod


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """A temp paper DB, also exported as FLIES_DB_PATH, so nothing under test can reach the
    developer's real paper DB and pass or fail for reasons unrelated to the test."""
    path = tmp_path / "paper_trades.db"
    monkeypatch.setenv("FLIES_DB_PATH", str(path))
    return dbmod.connect(str(path))


DAY = "2026-07-20"


def seeded(conn):
    """A day with one completed risk-free fly, one miss the market never offered, and one miss our
    own buffer turned down — enough for every panel to have something real to say."""
    position(
        conn,
        "P1",
        day=DAY,
        arm="gex",
        kind="fly",
        net=1.05,
        credit=2.55,
        best_debit=1.50,
        latency=23.0,
        spot_at_completion=6006.0,
        gross=105.0,
        pnl=98.11,
        risk_free=1,
        window="09:45-10:15",
    )
    position(
        conn,
        "P2",
        day=DAY,
        arm="control",
        kind="short_vertical",
        credit=2.55,
        best_debit=2.60,
        net=2.55,
        gross=-200.0,
        pnl=-203.44,
        risk_free=0,
    )
    position(
        conn,
        "P3",
        day=DAY,
        arm="time_window",
        kind="short_vertical",
        credit=2.10,
        best_debit=2.02,
        net=2.10,
        gross=50.0,
        pnl=46.56,
        risk_free=0,
    )
    dbmod.save_book(
        conn,
        {
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "symbol": "SPX",
            "credit_collected": 255.0,
            "debits_paid": 150.0,
            "fees": 6.89,
            "net_cash": 98.11,
            "worst": 98.11,
            "worst_at": 5900.0,
            "floor_holds": 1,
            "band_low": None,
            "band_high": None,
            "unbounded_below": 0,
            "status": "settled",
        },
    )
    dbmod.save_book(
        conn,
        {
            "book_id": f"{DAY}:control:SPX",
            "trade_date": DAY,
            "arm": "control",
            "symbol": "SPX",
            "credit_collected": 255.0,
            "debits_paid": 0.0,
            "fees": 3.44,
            "net_cash": 251.56,
            "worst": -248.44,
            "worst_at": 5990.0,
            "floor_holds": 0,
            "band_low": 5997.0,
            "band_high": 6200.0,
            "unbounded_below": 1,
            "status": "settled",
        },
    )
    for ts, centers in [
        ("T1", {"gex": 6005.0, "control": 6000.0}),
        ("T2", {"gex": 6000.0, "control": 6000.0}),
    ]:
        for arm, center in centers.items():
            dbmod.record_iteration(
                conn,
                iteration_ts=ts,
                trade_date=DAY,
                symbol="SPX",
                arm=arm,
                center=center,
                center_reason="atm",
                underlying_price=6000.0,
            )
    dbmod.record_decision(
        conn,
        trade_date=DAY,
        arm="gex",
        symbol="SPX",
        mode="legged",
        reason="credit_below_floor",
        center=6000.0,
        when=f"{DAY}T09:50:00",
    )
    dbmod.record_decision(
        conn,
        trade_date=DAY,
        arm="gex",
        symbol="SPX",
        mode="legged",
        reason="entered",
        accepted=True,
        position_id="P1",
        when=f"{DAY}T11:25:00",
    )


# --------------------------------------------------------------------------- EOD files
def test_write_reports_creates_both_files(conn, tmp_path):
    seeded(conn)
    out = eodmod.write_reports(conn, DAY, tmp_path)
    assert (tmp_path / f"paper-eod-{DAY}.md").exists()
    assert (tmp_path / f"eod-analysis-{DAY}.md").exists()
    assert out["ok"] is True


def test_filenames_match_what_the_orchestrator_looks_for(conn, tmp_path):
    """The digest and insight discover these by convention alone. A rename here silently drops flies
    out of both surfaces with no error anywhere."""
    out = tmp_path / "logs"
    eodmod.write_reports(conn, DAY, out)
    names = {p.name for p in out.iterdir()}
    assert names == {f"paper-eod-{DAY}.md", f"eod-analysis-{DAY}.md"}


def test_logs_dir_follows_the_orchestrators_convention(monkeypatch, tmp_path):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert eodmod.logs_dir() == tmp_path / "logs" / "flies"


def test_paper_eod_leads_with_completion_not_pnl(conn):
    """P&L over a handful of 0DTE sessions is mostly noise; completion rate is the real signal, so it
    comes first on purpose."""
    seeded(conn)
    text = eodmod.build_paper_eod(conn, DAY)
    assert text.index("Completion rate") < text.index("Session P&L")
    assert "market never offered it" in text
    # The two gates are reported separately: completion needs D < C - fee_buffer AND a post-fee floor
    # over min_floor_dollars, and lumping them together points at the wrong knob.
    assert "blocked by fee_buffer" in text.lower()
    assert "blocked by min_floor_dollars" in text.lower()


def test_analysis_explains_the_counterfactual_split(conn):
    seeded(conn)
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "opposite responses" in text
    assert "1 never saw a completing debit" in text


def test_analysis_refuses_to_call_a_bounded_book_risk_free(conn):
    seeded(conn)
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "calling it risk-free would be wrong" in text
    assert "conditional on price staying inside those wings" in text


def test_analysis_flags_high_arm_agreement_as_a_problem(conn):
    """If the arms agree most of the time the experiment cannot separate them, and the report has to
    say so rather than let a month of useless data accumulate."""
    for ts in ("T1", "T2", "T3", "T4", "T5"):
        for arm in ("gex", "control"):
            dbmod.record_iteration(
                conn,
                iteration_ts=ts,
                trade_date=DAY,
                symbol="SPX",
                arm=arm,
                center=6000.0,
                center_reason="atm",
                underlying_price=6000.0,
            )
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "problem for the experiment" in text


def test_analysis_on_an_empty_day_still_says_something_useful(conn):
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "No legged entries today" in text
    assert "a day without data" in text


def test_analysis_distinguishes_no_trades_from_no_data(conn):
    """The distinction that decides whether a barren day means anything: was it the market, or was it
    our plumbing?"""
    dbmod.record_decision(
        conn,
        trade_date=DAY,
        arm="gex",
        symbol="SPX",
        mode="legged",
        reason="missing_leg_quotes",
        when=f"{DAY}T10:00:00",
    )
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "we had no data, not that there was no trade" in text


def test_reports_are_deterministic(conn):
    """Same input, same bytes — these files get diffed across days."""
    seeded(conn)
    assert eodmod.build_paper_eod(conn, DAY) == eodmod.build_paper_eod(conn, DAY)


def test_every_report_number_comes_from_analytics(conn):
    """The EOD report must not compute its own headline figure. MEIC grew three call sites that
    disagreed about what "net" means; here every surface reads `analytics.py`, so the report's money
    line has to be exactly what that layer returns."""
    seeded(conn)
    stats = analytics.stats_for_period(conn, DAY, DAY)
    text = eodmod.build_paper_eod(conn, DAY)
    from cherrypick.core import viz

    assert viz.fmt_money(stats["net_pnl"]) in text


# --------------------------------------------------------------------------- regime section
def test_analysis_reports_regime_coverage_and_warns_on_a_degenerate_dimension(conn):
    """The daily read has to say when a tag carries no information. `entry_gex_bucket` was 'thin'
    60 times out of 60 for a month and nothing on any surface said so."""
    position(conn, "A", day=DAY, pnl=10.0, regime={"gex_bucket": "thin", "skew_bucket": "put_skew"})
    position(conn, "B", day=DAY, pnl=-20.0, regime={"gex_bucket": "thin", "skew_bucket": "flat"})
    text = eodmod.build_eod_analysis(conn, DAY)

    assert "## What regimes did we trade into?" in text
    assert "cannot be backfilled" in text  # the coverage caveat is stated, not implied
    assert "gex" in text and "landed every tagged row in a single bucket" in text
    # A degenerate dimension must NOT get a P&L table -- that would read as a finding.
    assert "**gex** split" not in text
    # One that genuinely separates does.
    assert "**skew** split" in text


def test_analysis_regime_section_survives_a_book_with_no_tags(conn):
    position(conn, "A", day=DAY, pnl=10.0)  # pre-tagging row, no regime at all
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "## What regimes did we trade into?" in text
    assert "No rows tagged yet for:" in text


def test_paper_eod_carries_the_refusal_ledger_and_its_two_warnings(tmp_path):
    """The refusals have to reach the deterministic file, because the EOD insight layer reads these
    files and nothing else — a refusal recorded only in the database is invisible to the narrative.

    Both warning lines are asserted because both come from specific failures: a debrief read a
    two-position arm as a validated thesis, and an arm added mid-session was compared against a twin
    that had traded all day.
    """
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    for i in range(9):
        dbmod.record_entry_attempt(
            conn,
            trade_date="2026-08-11",
            arm="control",
            symbol="SPX",
            outcome="filled" if i < 6 else "cadence_blocked",
            block_detail=None if i < 6 else "entry_cadence_wait",
            seconds_until_cadence_clear=None if i < 6 else 120.0,
        )
    for _ in range(4):
        dbmod.record_entry_attempt(
            conn,
            trade_date="2026-08-11",
            arm="thin-arm",
            symbol="SPX",
            outcome="filled",
        )
    dbmod.record_decision(
        conn,
        trade_date="2026-08-11",
        arm="thin-arm",
        symbol="*",
        mode="cadence",
        reason="arm added mid-session; first session is PARTIAL",
    )

    text = eodmod.build_paper_eod(conn, "2026-08-11")
    assert "## Entry attempts (the refusal ledger)" in text
    # The counts an arm's own row must carry.
    assert "| control | 9 | 6 | 3 |" in text.replace("  ", " ") or "| control | 9 | 6 | 3 " in text
    assert "entry_cadence_wait x3" in text
    assert "~120s avg" in text, "the cadence wait is the measured cost of the spacing"
    # Six fills is a sample; four is not.
    assert "Too few entries to read: thin-arm" in text
    assert "control" not in text.split("Too few entries to read:")[1].split("\n")[0]
    assert "Measurement break (thin-arm)" in text


def test_the_refusal_ledger_degrades_on_a_pre_2026_08_11_ledger(tmp_path):
    """A ledger written before the attempts table existed is a legitimate state, not an error — the
    section says so rather than failing the whole report."""

    conn = dbmod.connect(str(tmp_path / "old.db"))
    conn.execute("DROP TABLE fly_entry_attempts")
    conn.commit()
    text = eodmod.build_paper_eod(conn, "2026-08-11")
    assert "a ledger written before 2026-08-11" in text
    assert isinstance(text, str) and len(text) > 0
