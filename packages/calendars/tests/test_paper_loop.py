"""The session driver end-to-end against a seeded real-DDL cache: gates, entry, marks,
settlement, disposition, the status contract, and the stream request it writes."""

import json
import time
from datetime import datetime

from cherrypick.core import streamcache, streamrequests

from cherrypick.calendars import clock, db, paper_loop, stream_request

FRONT = "2026-08-21"
BACK = "2026-08-24"


class _Opt:
    def __init__(self, streamer_symbol, occ, strike, otype, expiration, underlying="SPX"):
        self._d = {
            "streamer_symbol": streamer_symbol,
            "symbol": occ,
            "strike_price": strike,
            "option_type": otype,
            "expiration_date": expiration,
            "underlying_symbol": underlying,
        }
        self.streamer_symbol = streamer_symbol

    def model_dump(self, mode="json"):
        return dict(self._d)


def _seed_cache(tmp_path, *, spot=6500.0, symbol="SPX", root="SPXW"):
    """Front quotes at 20 mid, back at 25 — a 5.00 debit calendar at every strike."""
    cache = tmp_path / "stream_cache.db"
    conn = streamcache.connect(cache)
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        (symbol, spot, 0.0, 0.0, now),
    )
    chain = {}
    for expiration, tag, mid in ((FRONT, "F", 20.0), ((BACK), "B", 25.0)):
        for i in range(-20, 21):
            strike = spot + 5 * i
            for otype in ("put", "call"):
                sym = f".{root}{tag}{otype[0].upper()}{strike:g}"
                occ = f"{root:<6}26{tag}{otype[0].upper()}{strike:08.0f}"
                chain[sym] = _Opt(sym, occ, strike, otype, expiration, underlying=symbol)
                conn.execute(
                    "INSERT OR REPLACE INTO stream_quotes"
                    "(symbol, bid, ask, mid, bid_size, ask_size, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (sym, mid - 0.2, mid + 0.2, mid, 1, 1, now),
                )
    streamcache.write_chain(conn, chain)
    conn.commit()
    conn.close()
    return str(cache)


def _at(day: str, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=clock.ET)


def test_non_trading_day_is_a_clean_no_op(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    out = paper_loop.run_once({}, conn, cache_path=str(tmp_path / "none.db"), when=_at("2026-08-22", "12:00"))
    assert out["skipped"] == "not_a_trading_day"


def test_outside_rth_is_a_clean_no_op(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    out = paper_loop.run_once({}, conn, cache_path=str(tmp_path / "none.db"), when=_at("2026-08-18", "08:00"))
    assert out["skipped"] == "outside_rth"


def test_monday_entry_then_friday_settle_then_monday_disposition(tmp_path):
    cache = _seed_cache(tmp_path)
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_roots": {"SPX": "SPXW"}}

    # Monday inside the entry window: both books enter both sides, and get marked the same tick.
    out = paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert out["ok"]
    positions = conn.execute(
        "SELECT book, side, structure, entry_debit, strike FROM dc_positions ORDER BY book, side"
    ).fetchall()
    assert [(r["book"], r["side"]) for r in positions] == [
        ("control", "call"),
        ("control", "put"),
        ("path", "call"),
        ("path", "put"),
    ]
    assert all(r["structure"] == "dc_4_7" for r in positions)
    assert all(abs(r["entry_debit"] - 5.0) < 0.01 for r in positions)
    # EM = 0.85 * 40 = 34 -> put 6465, call 6535.
    strikes = {(r["side"], r["strike"]) for r in positions}
    assert ("put", 6465.0) in strikes and ("call", 6535.0) in strikes
    assert conn.execute("SELECT COUNT(*) FROM dc_marks").fetchone()[0] == 8  # 4 positions x 2 legs
    attempts = conn.execute("SELECT outcome FROM dc_entry_attempts").fetchall()
    assert [a["outcome"] for a in attempts] == ["filled"]

    # A second tick in the window does not double-enter.
    paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:07"))
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 4

    # Friday after the settle time: shorts cash-settle off the (fresh) cache spot.
    out = paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-21", "16:25"))
    assert out.get("settled_session")
    statuses = {
        r["book"]: r["status"]
        for r in conn.execute("SELECT book, status FROM dc_positions WHERE side = 'put'")
    }
    # Spot 6500: the 6465 put and 6535 call both finish OTM; every book still holds its longs.
    assert statuses == {"control": "short_settled", "path": "short_settled"}

    # Next Monday morning: longs dispose at their marks.
    out = paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-24", "09:50"))
    assert out["ok"]
    remaining = conn.execute("SELECT COUNT(*) FROM dc_positions WHERE status != 'closed'").fetchone()[0]
    assert remaining == 0
    reasons = {r["exit_reason"] for r in conn.execute("SELECT exit_reason FROM dc_positions")}
    assert reasons == {"long_disposition"}


def test_control_scheduled_exit_closes_at_the_friday_bell(tmp_path):
    cache = _seed_cache(tmp_path)
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_roots": {"SPX": "SPXW"}}
    paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    out = paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-21", "15:50"))
    assert out["ok"]
    control = {r["side"]: r for r in conn.execute("SELECT * FROM dc_positions WHERE book = 'control'")}
    assert all(r["status"] == "closed" for r in control.values())
    assert all(r["exit_reason"] == "scheduled_exit" for r in control.values())
    # The path book held straight through the same tick.
    path = conn.execute(
        "SELECT COUNT(*) FROM dc_positions WHERE book = 'path' AND status = 'open'"
    ).fetchone()[0]
    assert path == 2
    events = conn.execute(
        "SELECT executed FROM dc_management_events WHERE reason = 'scheduled_exit'"
    ).fetchall()
    assert all(e["executed"] == 1 for e in events)


def test_status_contract(tmp_path):
    cache = _seed_cache(tmp_path)
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_roots": {"SPX": "SPXW"}}
    status = paper_loop.run_status(config, conn, cache_path=cache)
    for key in (
        "session_settled",
        "positions_today",
        "open_positions",
        "data_ok",
        "stream_cache_present",
        "week_plan",
    ):
        assert key in status
    assert status["session_settled"] is True  # nothing expires today
    assert json.loads(json.dumps(status, default=str))  # one JSON-serializable object


def test_stream_request_carries_expirations_and_leg_source(tmp_path, managed_home):
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"]}
    stream_request.write(config, conn, str(tmp_path / "paper.db"), today=_at("2026-08-17", "09:00").date())
    payload = json.loads(streamrequests.request_path("calendars").read_text(encoding="utf-8"))
    assert payload["symbols"] == ["SPX"]
    assert payload["expirations"] == {"SPX": [FRONT, BACK]}
    assert "dc_legs" in payload["leg_sources"][0]["query"]


# ---------------------------------------------------------------- the ex-dividend week guards
SPY_DIVS = {
    "settlement_style": {"SPY": "physical"},
    "dividends": {"SPY": {"declared_through": "2026-12-31", "ex_dates": ["2026-08-21"]}},
}


def test_a_physical_week_containing_an_ex_date_is_skipped_and_journaled(tmp_path):
    cache = _seed_cache(tmp_path, spot=780.0, symbol="SPY", root="SPY")
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPY"], **SPY_DIVS}  # ex-date 08-21 IS the week's short expiry
    paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 0
    attempt = conn.execute(
        "SELECT outcome, block_detail FROM dc_entry_attempts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert attempt["outcome"] == "ex_dividend_week"
    assert "2026-08-21" in attempt["block_detail"]


def test_a_physical_week_past_the_declared_horizon_is_refused_not_assumed_dividend_free(tmp_path):
    cache = _seed_cache(tmp_path, spot=780.0, symbol="SPY", root="SPY")
    conn = db.connect(str(tmp_path / "paper.db"))
    lapsed = {
        "symbols": ["SPY"],
        "settlement_style": {"SPY": "physical"},
        # The horizon ends mid-week: the back expiration (08-24) is past it.
        "dividends": {"SPY": {"declared_through": "2026-08-21", "ex_dates": []}},
    }
    paper_loop.run_once(lapsed, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 0
    outcome = conn.execute("SELECT outcome FROM dc_entry_attempts ORDER BY id DESC LIMIT 1").fetchone()
    assert outcome["outcome"] == "dividend_calendar_lapsed"


def test_a_physical_week_with_no_dividends_block_is_lapsed_not_dividend_free(tmp_path):
    """A missing table and 'no dividend that week' must never look alike."""
    cache = _seed_cache(tmp_path, spot=780.0, symbol="SPY", root="SPY")
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPY"], "settlement_style": {"SPY": "physical"}}
    paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 0
    outcome = conn.execute("SELECT outcome FROM dc_entry_attempts ORDER BY id DESC LIMIT 1").fetchone()
    assert outcome["outcome"] == "dividend_calendar_lapsed"


def test_a_covered_ex_free_physical_week_enters_normally(tmp_path):
    cache = _seed_cache(tmp_path, spot=780.0, symbol="SPY", root="SPY")
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {
        "symbols": ["SPY"],
        "settlement_style": {"SPY": "physical"},
        "dividends": {"SPY": {"declared_through": "2026-12-31", "ex_dates": ["2026-09-18"]}},
    }
    out = paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert out["ok"]
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 4
    outcomes = [r["outcome"] for r in conn.execute("SELECT outcome FROM dc_entry_attempts")]
    assert outcomes == ["filled"]


def test_a_cash_symbol_needs_no_dividends_block(tmp_path):
    """SPX weeks must be untouched by the dividend guards — cash settlement has no assignment."""
    cache = _seed_cache(tmp_path)
    conn = db.connect(str(tmp_path / "paper.db"))
    config = {"symbols": ["SPX"], "occ_roots": {"SPX": "SPXW"}}  # no dividends block anywhere
    paper_loop.run_once(config, conn, cache_path=cache, when=_at("2026-08-17", "10:05"))
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 4
