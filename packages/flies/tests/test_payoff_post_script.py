"""scripts/flies_payoff_post.py: post each ledger's payoff chart once, only after it has settled.

The capture (a browser) and the post (a webhook) are faked; everything between them is the script's
own logic over real flies ledgers in the test home.
"""

import importlib.util
from pathlib import Path

import pytest

from cherrypick.flies import db as dbmod

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_payoff_post.py"
DAY = "2026-09-30"


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("flies_payoff_post", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = {"captured": [], "posted": []}

    def capture(mode, session, out):
        out.write_bytes(b"png")
        calls["captured"].append((mode, session))
        return None

    def post(image, text, **record):
        calls["posted"].append(text)
        return None

    monkeypatch.setattr(mod, "capture", capture)
    monkeypatch.setattr(mod, "post", post)
    mod.calls = calls
    return mod


def _book(conn, arm, *, source="yahoo", status="settled", price=7651.56):
    conn.execute(
        "INSERT INTO fly_books (book_id, trade_date, arm, symbol, status, settlement_price, settlement_source) "
        "VALUES (?, ?, ?, 'SPX', ?, ?, ?)",
        (f"{DAY}:{arm}:SPX", DAY, arm, status, price, source),
    )
    conn.commit()


def _position(conn, arm, gross, fees, n=1):
    conn.execute(
        "INSERT INTO fly_positions (position_id, trade_date, arm, symbol, status, gross_pnl, fees) "
        "VALUES (?, ?, ?, 'SPX', 'settled', ?, ?)",
        (f"P-{arm}-{n}", DAY, arm, gross, fees),
    )
    conn.commit()


def test_live_waits_for_the_official_print(script):
    conn = dbmod.connect(dbmod.live_db_path())
    _book(conn, "control", source="tastytrade_last_provisional")
    assert script.run_mode("live", DAY, dry_run=False, force=False, keep=None) == "skipped"
    assert script.calls["captured"] == []


def test_posts_once_per_ledger_per_session_and_says_the_net(script):
    conn = dbmod.connect(dbmod.live_db_path())
    _book(conn, "control")
    _position(conn, "control", -155.0, 77.89)
    assert script.run_mode("live", DAY, dry_run=False, force=False, keep=None) == "posted"
    assert "`control` net -$232.89" in script.calls["posted"][0]
    assert "SPX settled 7651.56 (yahoo)" in script.calls["posted"][0]
    assert script.run_mode("live", DAY, dry_run=False, force=False, keep=None) == "skipped"
    assert len(script.calls["posted"]) == 1
    assert script.run_mode("live", DAY, dry_run=False, force=True, keep=None) == "posted"


def test_paper_lists_every_book_best_first_including_one_that_traded_nothing(script):
    conn = dbmod.connect(dbmod.default_db_path())
    for arm in ("control", "bwb-up", "debit-first-up"):
        _book(conn, arm, source=None, price=7651.54)
    _position(conn, "control", 100.0, 14.45)
    _position(conn, "debit-first-up", -300.0, 68.14)
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None) == "posted"
    lines = script.calls["posted"][0].splitlines()
    assert lines[1:] == ["`control` net +$85.55", "`bwb-up` net +$0.00", "`debit-first-up` net -$368.14"]


def test_an_unsettled_book_is_not_posted(script):
    conn = dbmod.connect(dbmod.default_db_path())
    _book(conn, "control", status="open", source=None)
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None) == "skipped"


def test_a_failed_capture_fails_the_run_and_leaves_it_unposted(script, monkeypatch):
    conn = dbmod.connect(dbmod.live_db_path())
    _book(conn, "control")
    monkeypatch.setattr(script, "capture", lambda mode, session, out: "ui-check exit 1: no card")
    assert script.main(["--mode", "live", "--session", DAY]) == 1
    assert not script.already_posted(DAY, "live")


def test_no_book_for_the_session_is_nothing_to_do(script):
    dbmod.connect(dbmod.live_db_path())
    assert script.main(["--mode", "live", "--session", DAY]) == 0
    assert script.calls["captured"] == []


# --------------------------------------------------------------------------- --intraday
# The same chart while the book is open, hourly through the session. It never waits for settlement,
# stops once a ledger has settled, and posts at most once per ledger per ET hour.


def _open_book(conn, arm, *, net_cash, worst, band=(7710.0, 7782.0), unbounded=0):
    conn.execute(
        "INSERT INTO fly_books (book_id, trade_date, arm, symbol, status, net_cash, worst, band_low, band_high,"
        " unbounded_below) VALUES (?, ?, ?, 'SPX', 'open', ?, ?, ?, ?, ?)",
        (f"{DAY}:{arm}:SPX", DAY, arm, net_cash, worst, band[0], band[1], unbounded),
    )
    conn.commit()


def _open_position(conn, arm, n):
    conn.execute(
        "INSERT INTO fly_positions (position_id, trade_date, arm, symbol, status) VALUES (?, ?, ?, 'SPX', 'open')",
        (f"O-{arm}-{n}", DAY, arm),
    )
    conn.commit()


@pytest.fixture
def at(script, monkeypatch):
    """Set the script's ET wall clock to HH:MM on DAY."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def set_clock(hhmm: str):
        h, m = map(int, hhmm.split(":"))
        now = datetime(2026, 9, 30, h, m, tzinfo=ZoneInfo("America/New_York"))
        monkeypatch.setattr(script, "_now_et", lambda: now)

    return set_clock


def test_intraday_posts_the_open_book_and_says_it_is_not_a_result(script, at):
    at("11:01")
    conn = dbmod.connect(dbmod.default_db_path())
    _open_book(conn, "control", net_cash=347.1, worst=-207.9)
    _open_book(conn, "bwb-up", net_cash=0.0, worst=0.0, band=(None, None))
    _open_book(conn, "debit-first-down", net_cash=-574.73, worst=-578.73, band=(7655.0, 7784.0), unbounded=1)
    for n in range(3):
        _open_position(conn, "control", n)
    _open_position(conn, "debit-first-down", 0)
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None, intraday=True) == "posted"
    lines = script.calls["posted"][0].splitlines()
    assert "11:01 ET (INTRADAY, open book)" in lines[0]
    assert lines[1:] == [
        "`control` 3 open · net cash +$347.10 · worst at expiry -$207.90 · ≥0 7710–7782",
        "`debit-first-down` 1 open · net cash -$574.73 · worst at expiry -$578.73 · ≥0 7655–7784 (unbounded below)",
        "no open flies: `bwb-up`",
    ]


def test_intraday_posts_once_an_hour_and_never_marks_the_settled_post(script, at):
    conn = dbmod.connect(dbmod.default_db_path())
    _open_book(conn, "control", net_cash=10.0, worst=-5.0)
    _open_position(conn, "control", 0)
    at("11:01")
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None, intraday=True) == "posted"
    at("11:40")  # a supervisor restart re-fires the job inside the same hour
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None, intraday=True) == "skipped"
    at("12:01")
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None, intraday=True) == "posted"
    assert len(script.calls["posted"]) == 2
    assert not script.already_posted(DAY, "paper")  # the after-bell post still has its turn


def test_intraday_stands_down_once_the_ledger_has_settled(script, at):
    at("16:01")
    conn = dbmod.connect(dbmod.default_db_path())
    _book(conn, "control")
    assert script.run_mode("paper", DAY, dry_run=False, force=False, keep=None, intraday=True) == "skipped"
    assert script.calls["captured"] == []
