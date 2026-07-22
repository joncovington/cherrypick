"""Tests for the read surfaces: dashboard API, section card, and the EOD report files."""

import json

import pytest
from test_analytics import position

import analytics
import dashboard
import db as dbmod
import eod as eodmod
import section


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """A temp paper DB, also exported as FLIES_DB_PATH.

    The env var matters: `section.build_section` opens its own connection (it is invoked as a
    subprocess by the orchestrator, so it cannot be handed one). Without this the card would silently
    read the developer's real paper DB instead of the fixture — passing or failing for reasons that
    have nothing to do with the test.
    """
    path = tmp_path / "paper_trades.db"
    monkeypatch.setenv("FLIES_DB_PATH", str(path))
    return dbmod.connect(str(path))


DAY = "2026-07-20"


def seeded(conn):
    """A day with one completed risk-free fly, one miss the market never offered, and one miss our
    own buffer turned down — enough for every panel to have something real to say."""
    position(conn, "P1", day=DAY, arm="gex", kind="fly", net=1.05, credit=2.55, best_debit=1.50,
             latency=23.0, spot_at_completion=6006.0, gross=105.0, pnl=98.11, risk_free=1,
             window="09:45-10:15")
    position(conn, "P2", day=DAY, arm="control", kind="short_vertical", credit=2.55,
             best_debit=2.60, net=2.55, gross=-200.0, pnl=-203.44, risk_free=0)
    position(conn, "P3", day=DAY, arm="time_window", kind="short_vertical", credit=2.10,
             best_debit=2.02, net=2.10, gross=50.0, pnl=46.56, risk_free=0)
    dbmod.save_book(conn, {"book_id": f"{DAY}:gex:SPX", "trade_date": DAY, "arm": "gex",
                           "symbol": "SPX", "credit_collected": 255.0, "debits_paid": 150.0,
                           "fees": 6.89, "net_cash": 98.11, "worst": 98.11, "worst_at": 5900.0,
                           "floor_holds": 1, "band_low": None, "band_high": None,
                           "unbounded_below": 0, "status": "settled"})
    dbmod.save_book(conn, {"book_id": f"{DAY}:control:SPX", "trade_date": DAY, "arm": "control",
                           "symbol": "SPX", "credit_collected": 255.0, "debits_paid": 0.0,
                           "fees": 3.44, "net_cash": 251.56, "worst": -248.44, "worst_at": 5990.0,
                           "floor_holds": 0, "band_low": 5997.0, "band_high": 6200.0,
                           "unbounded_below": 1, "status": "settled"})
    for ts, centers in [("T1", {"gex": 6005.0, "control": 6000.0}),
                        ("T2", {"gex": 6000.0, "control": 6000.0})]:
        for arm, center in centers.items():
            dbmod.record_iteration(conn, iteration_ts=ts, trade_date=DAY, symbol="SPX", arm=arm,
                                   center=center, center_reason="atm", underlying_price=6000.0)
    dbmod.record_decision(conn, trade_date=DAY, arm="gex", symbol="SPX", mode="legged",
                          reason="credit_below_floor", center=6000.0, when=f"{DAY}T09:50:00")
    dbmod.record_decision(conn, trade_date=DAY, arm="gex", symbol="SPX", mode="legged",
                          reason="entered", accepted=True, position_id="P1",
                          when=f"{DAY}T11:25:00")


# --------------------------------------------------------------------------- dashboard
def test_resolve_port_precedence(monkeypatch):
    monkeypatch.delenv("FLIES_DASHBOARD_PORT", raising=False)
    assert dashboard.resolve_port(None) == dashboard.DEFAULT_PORT
    monkeypatch.setenv("FLIES_DASHBOARD_PORT", "9111")
    assert dashboard.resolve_port(None) == 9111
    assert dashboard.resolve_port(8123) == 8123, "an explicit flag must win over the environment"


def test_resolve_port_ignores_junk_env(monkeypatch):
    monkeypatch.setenv("FLIES_DASHBOARD_PORT", "not-a-port")
    assert dashboard.resolve_port(None) == dashboard.DEFAULT_PORT


def test_api_payload_is_json_serializable_and_complete(conn):
    seeded(conn)
    payload = dashboard.build_api_data(conn, DAY)
    json.dumps(payload, default=str)  # must survive the wire
    assert payload["ok"] is True
    assert set(payload["arms"]) >= {"gex", "control", "time_window"}
    for view in ("today", "history", "performance"):
        assert payload[view], f"{view} view has no data"
    assert payload["today"]["curves"]["gex"]["empty"] is False


def test_api_payload_on_an_empty_database(conn):
    """Every morning starts here, so the empty case must be a clean payload, not an exception."""
    payload = dashboard.build_api_data(conn, DAY)
    assert payload["ok"] is True
    assert payload["arms"] == []
    assert payload["today"]["positions"] == []


def test_api_arm_filter_narrows_history(conn):
    seeded(conn)
    everything = dashboard.build_api_data(conn, DAY)
    only_gex = dashboard.build_api_data(conn, DAY, "gex")
    assert len(only_gex["history"]["trades"]) < len(everything["history"]["trades"])
    assert {t["arm"] for t in only_gex["history"]["trades"]} == {"gex"}


def test_page_is_self_contained(conn):
    """A loopback page that fetched from a CDN would break offline and add a third-party dependency
    to a surface whose only job is reading a local SQLite file."""
    assert "<canvas" in dashboard.HTML
    for remote in ("http://", "https://", "cdn."):
        assert remote not in dashboard.HTML, f"page reaches out to {remote}"


# --------------------------------------------------------------------------- section card
def test_section_renders_the_payoff_curve_as_bars(conn):
    seeded(conn)
    payload = section.build_section(None, DAY, "gex")
    assert payload["ok"] is True
    assert payload["bars"]["series"][0]["tone_by_sign"] is True
    assert len(payload["bars"]["labels"]) == len(payload["bars"]["series"][0]["values"])
    labels = [m["label"] for m in payload["metrics"]]
    assert "Book floor" in labels and "Completion" in labels


def test_section_states_the_band_alongside_the_floor(conn):
    """A floor without the band it holds over is the claim this module exists to avoid making."""
    position(conn, "P1", day=DAY, arm="control", kind="short_vertical", net=2.55, status="open")
    dbmod.save_book(conn, {"book_id": f"{DAY}:control:SPX", "trade_date": DAY, "arm": "control",
                           "symbol": "SPX", "credit_collected": 255.0, "debits_paid": 0.0,
                           "fees": 3.44, "status": "open"})
    payload = section.build_section(None, DAY, "control")
    assert any(m["label"] == "Floor holds" for m in payload["metrics"])
    assert "loses outside the band" in payload["subtitle"]


def test_section_on_an_empty_day_is_ok_not_an_error(conn):
    """A card that shouted 'error' every morning would train the operator to ignore it."""
    payload = section.build_section(None, DAY)
    assert payload["ok"] is True
    assert "no positions" in payload["title"].lower()


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
    assert "buffer too tight" in text.lower()


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
            dbmod.record_iteration(conn, iteration_ts=ts, trade_date=DAY, symbol="SPX", arm=arm,
                                   center=6000.0, center_reason="atm", underlying_price=6000.0)
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "problem for the experiment" in text


def test_analysis_on_an_empty_day_still_says_something_useful(conn):
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "No legged entries today" in text
    assert "a day without data" in text


def test_analysis_distinguishes_no_trades_from_no_data(conn):
    """The distinction that decides whether a barren day means anything: was it the market, or was it
    our plumbing?"""
    dbmod.record_decision(conn, trade_date=DAY, arm="gex", symbol="SPX", mode="legged",
                          reason="missing_leg_quotes", when=f"{DAY}T10:00:00")
    text = eodmod.build_eod_analysis(conn, DAY)
    assert "we had no data, not that there was no trade" in text


def test_reports_are_deterministic(conn):
    """Same input, same bytes — these files get diffed across days."""
    seeded(conn)
    assert eodmod.build_paper_eod(conn, DAY) == eodmod.build_paper_eod(conn, DAY)


def test_every_report_number_comes_from_analytics(conn):
    """The reports and the dashboard must never disagree. Both read the same layer, so this checks the
    headline figure agrees across surfaces."""
    seeded(conn)
    stats = analytics.stats_for_period(conn, DAY, DAY)
    text = eodmod.build_paper_eod(conn, DAY)
    assert f"${stats['net_pnl']:,.2f}" in text
    payload = dashboard.build_api_data(conn, DAY)
    assert payload["today"]["stats"]["net_pnl"] == stats["net_pnl"]
