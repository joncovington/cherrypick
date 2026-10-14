"""`paper_loop.settle_overdue` -- a cash-settled session the in-window pass never settled.

2026-10-13: an internet outage from ~14:53 ET past the close. The paper loop settles cash-settled
0DTE positions only inside its window (to 16:05 ET) and only when the market overview returns a
price; every in-window tick logged "SPX: ERROR no price", every tick after 16:05 only skipped, and
656 positions stayed open until they were settled by hand at the official close. These tests pin
the automatic version of that hand fix, and its refusals: no official print, nothing settles.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.core.clock import ET  # noqa: E402

from cherrypick.meic import analytics, db, paper, paper_loop, tt  # noqa: E402

SESSION = "2026-10-13"
CLOSE = 7805.45
CFG = {"expiration_settlement_time": "16:00", "cash_settled_symbols": ["SPX", "XSP", "NDX", "RUT"]}


def _et(day: str, hhmm: str) -> datetime:
    naive = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M")
    localize = getattr(ET, "localize", None)
    return localize(naive) if localize else naive.replace(tzinfo=ET)


NEXT_MORNING = _et("2026-10-14", "08:00")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    monkeypatch.setattr(paper_loop, "_PAPER_DB", path)
    monkeypatch.setattr(paper_loop, "_DB", [sys.executable, "-m", "cherrypick.meic.db", "--db", path])
    monkeypatch.setattr(paper_loop, "_OUTSIDE_FILE", tmp_path / "paper_loop.outside")
    return path


def _open_trade(path, order_id, *, day=SESSION, symbol="SPX", put=7700.0, call=7900.0, **over):
    row = {
        "trade_date": day,
        "expiration": day,
        "symbol": symbol,
        "status": "open",
        "arm": "control",
        "era": analytics.CURRENT_ERA,
        "net_credit": 2.0,
        "put_credit": 1.0,
        "call_credit": 1.0,
        "wing_width": 10,
        "put_strike": put,
        "call_strike": call,
        "quantity": 1,
        "fees": 4.0,
        "entry_time": f"{day} 11:00:00-04:00",
        "ic_order_id": order_id,
        "created_at": "x",
        "updated_at": "x",
    }
    row.update(over)
    conn = sqlite3.connect(path)
    conn.execute(
        f"INSERT INTO ic_trades ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()
    conn.close()


def _rows(path) -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {r["ic_order_id"]: dict(r) for r in conn.execute("SELECT * FROM ic_trades")}
    conn.close()
    return out


def _summary(path, day=SESSION):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM daily_summary WHERE summary_date = ?", (day,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _entry_attempts(path) -> int:
    conn = sqlite3.connect(path)
    n = conn.execute("SELECT COUNT(*) FROM entry_attempts").fetchone()[0]
    conn.close()
    return n


class _Closes:
    """A stand-in official-close source: records every ask, answers with one (price, source)."""

    def __init__(self, price=CLOSE, source="yahoo_daily"):
        self.answer = (price, source)
        self.asked: list[tuple[str, str, str]] = []

    def __call__(self, symbol, session, today):
        self.asked.append((symbol, session, today))
        return self.answer


# --------------------------------------------------------------------------- settles, at the close
def test_an_overdue_session_settles_at_the_official_close(ledger):
    _open_trade(ledger, "OTM")  # 7700 / 7900 around 7805.45: both sides expire worthless
    _open_trade(ledger, "ITM-CALL", put=7600.0, call=7800.0)  # call 5.45 in the money
    closes = _Closes()

    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=closes)

    assert closes.asked == [("SPX", SESSION, "2026-10-14")]  # one print per (session, symbol)
    assert [(s["session"], s["symbol"], s["positions"], s["price"]) for s in out["settled"]] == [
        (SESSION, "SPX", 2, CLOSE)
    ]
    assert out["left"] == []
    rows = _rows(ledger)
    for oid in ("OTM", "ITM-CALL"):
        assert rows[oid]["status"] == "expired"
        assert rows[oid]["exit_reason"] == "expired_settlement"
        assert rows[oid]["settle_underlying"] == CLOSE
        # The bell, not the morning the outage ended.
        assert rows[oid]["exit_time"].startswith(f"{SESSION} 16:00:00")
    assert rows["OTM"]["pnl"] == pytest.approx(200.0)  # full credit kept
    assert rows["ITM-CALL"]["pnl"] == pytest.approx(200.0 - 545.0)  # call settles at 5.45 intrinsic
    assert rows["ITM-CALL"]["settlement_fees"] == pytest.approx(5.0)  # one ITM strike


def test_the_settled_session_is_rolled_up(ledger):
    _open_trade(ledger, "OTM")
    _open_trade(ledger, "ITM-CALL", put=7600.0, call=7800.0)
    assert _summary(ledger) is None

    paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes())

    summary = _summary(ledger)
    assert summary is not None
    assert summary["entries_expired"] == 2
    rows = _rows(ledger)
    net = sum(r["pnl"] - r["fees"] for r in rows.values())
    assert summary["net_pnl"] == pytest.approx(net)


def test_the_settlement_pass_evaluates_no_entry(ledger):
    """The snapshot carries a closing print and nothing else; an entry evaluation against it would
    record a refusal per arm for a tick that never happened."""
    _open_trade(ledger, "OTM")
    paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes())
    assert _entry_attempts(ledger) == 0


def test_todays_session_settles_once_the_print_has_posted(ledger):
    _open_trade(ledger, "OTM")
    closes = _Closes(source="tastytrade_close")

    early = paper_loop.settle_overdue(CFG, now=_et(SESSION, "16:10"), close_fn=closes)
    assert early == {"ok": True, "settled": [], "left": []}
    assert closes.asked == []  # inside the grace the in-window pass's price is not second-guessed

    late = paper_loop.settle_overdue(CFG, now=_et(SESSION, "16:30"), close_fn=closes)
    assert closes.asked == [("SPX", SESSION, SESSION)]
    assert late["settled"][0]["positions"] == 1
    assert _rows(ledger)["OTM"]["exit_time"].startswith(f"{SESSION} 16:00:00")


# --------------------------------------------------------------------------- refusals
@pytest.mark.parametrize(
    "answer",
    [
        (None, "no_dated_close"),  # nothing answered
        (None, "no_source_available"),
        (7790.0, "tastytrade_last_provisional"),  # an intraday tick, never a settlement price
        (7790.0, "market_overview"),  # anything not declared official
    ],
)
def test_no_official_print_settles_nothing(ledger, answer):
    _open_trade(ledger, "OTM")
    _open_trade(ledger, "ITM-CALL", put=7600.0, call=7800.0)
    before = _rows(ledger)

    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes(*answer))

    assert out["settled"] == []
    assert [(item["reason"], item["positions"]) for item in out["left"]] == [("no_official_print", 2)]
    assert _rows(ledger) == before  # not a column touched
    assert _summary(ledger) is None


def test_an_already_settled_day_is_a_no_op(ledger):
    _open_trade(ledger, "OTM")
    paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes())
    after_first = _rows(ledger)
    summary = _summary(ledger)
    closes = _Closes(price=9999.0)

    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=closes)

    assert out == {"ok": True, "settled": [], "left": []}
    assert closes.asked == []  # nothing open: no fetch at all
    assert _rows(ledger) == after_first
    assert _summary(ledger)["updated_at"] == summary["updated_at"]


def test_a_physically_settled_symbol_is_left_for_a_person(ledger):
    _open_trade(ledger, "QQQ-1", symbol="QQQ", put=590.0, call=610.0, wing_width=5)
    closes = _Closes()
    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=closes)
    assert closes.asked == []
    assert [item["reason"] for item in out["left"]] == ["physically_settled"]
    assert _rows(ledger)["QQQ-1"]["status"] == "open"


def test_a_session_already_settled_at_another_print_is_not_split(ledger):
    """One settlement price per (session, symbol): rows the in-window pass settled at one print and
    rows left open are a person's call, not a second price."""
    _open_trade(
        ledger,
        "DONE",
        status="expired",
        exit_reason="expired_settlement",
        settle_underlying=7801.0,
    )
    _open_trade(ledger, "OPEN")
    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes())
    assert [item["reason"] for item in out["left"]] == ["settled_at_another_price"]
    assert _rows(ledger)["OPEN"]["status"] == "open"


def test_an_advised_book_settles_under_its_management_twin(ledger):
    _open_trade(ledger, "ADV", arm="advised:some-experiment")
    out = paper_loop.settle_overdue(CFG, now=NEXT_MORNING, close_fn=_Closes())
    assert out["left"] == []
    assert _rows(ledger)["ADV"]["status"] == "expired"


# --------------------------------------------------------------------------- it actually runs
def test_the_tick_outside_its_window_settles_an_overdue_session(ledger, monkeypatch):
    """The supervisor's tick is the path that must do this unattended: before this, a tick past
    16:05 logged "outside trading window" and returned, every minute, with 656 positions open."""
    _open_trade(ledger, "OTM")
    monkeypatch.setattr(paper_loop, "_now_et", lambda: NEXT_MORNING)
    monkeypatch.setattr(paper_loop, "_official_close", _Closes(), raising=False)
    monkeypatch.setattr(paper_loop, "_beat", lambda: None)

    paper_loop.run_iteration(paper_loop._load_config())

    row = _rows(ledger)["OTM"]
    assert row["status"] == "expired"
    assert row["settle_underlying"] == CLOSE
    assert _summary(ledger) is not None


def test_the_tick_survives_a_failed_catch_up(ledger, monkeypatch):
    _open_trade(ledger, "OTM")
    monkeypatch.setattr(paper_loop, "_now_et", lambda: NEXT_MORNING)
    monkeypatch.setattr(paper_loop, "_beat", lambda: None)

    def boom(*_a):
        raise RuntimeError("network down")

    monkeypatch.setattr(paper_loop, "_official_close", boom)
    out = paper_loop.run_iteration(paper_loop._load_config())
    assert out["skipped"] == "outside_trading_window"
    assert _rows(ledger)["OTM"]["status"] == "open"


def test_the_off_window_line_is_logged_once_per_stretch(ledger, monkeypatch, caplog):
    """Every off-hours minute used to log "outside trading window"; now the first tick says it and
    the rest are quiet until an in-window tick clears the marker."""
    monkeypatch.setattr(paper_loop, "_now_et", lambda: NEXT_MORNING)
    monkeypatch.setattr(paper_loop, "_beat", lambda: None)
    cfg = paper_loop._load_config()

    def said():
        return sum("outside trading window" in r.getMessage() for r in caplog.records)

    with caplog.at_level("INFO", logger="paper_loop"):
        for _ in range(3):
            assert paper_loop.run_iteration(cfg)["skipped"] == "outside_trading_window"
        assert said() == 1
        paper_loop._clear_outside()  # what the next in-window tick does
        paper_loop.run_iteration(cfg)
        assert said() == 2


# --------------------------------------------------------------------------- the clock and the source
def test_the_pinned_clock_is_released():
    with paper.pinned_clock(NEXT_MORNING):
        assert paper._now_et() == NEXT_MORNING
    assert paper._now_et() != NEXT_MORNING


def test_a_past_session_reads_the_dated_close(monkeypatch):
    seen = []
    monkeypatch.setattr(
        paper_loop._settlement, "dated_index_close", lambda s, d: seen.append((s, d)) or CLOSE
    )
    assert paper_loop._official_close("SPX", SESSION, "2026-10-14") == (CLOSE, "yahoo_daily")
    assert seen == [("SPX", SESSION)]


def test_todays_session_asks_tt_for_the_official_close(monkeypatch):
    calls = []

    def fake_run_json(cmd, timeout=90):
        calls.append(cmd)
        return {"ok": True, "price": CLOSE, "source": "tastytrade_close", "official": True}

    monkeypatch.setattr(paper_loop, "_run_json", fake_run_json)
    assert paper_loop._official_close("SPX", SESSION, SESSION) == (CLOSE, "tastytrade_close")
    assert "get_official_close" in calls[0]


@pytest.mark.parametrize(
    "answer, official",
    [
        ((CLOSE, "tastytrade_close"), True),
        ((7790.0, "tastytrade_last_provisional"), False),
        ((None, "no_source_available"), False),
    ],
)
def test_tt_get_official_close_labels_what_answered(monkeypatch, answer, official):
    async def fake_close(session, symbol):
        assert session is None  # no session: the public sources still answer
        return answer

    def no_session():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(tt, "get_session", no_session)
    monkeypatch.setattr("cherrypick.core.settlement.official_index_close", fake_close)
    out = asyncio.run(tt.cmd_get_official_close(SimpleNamespace(symbol="spx")))
    assert out["ok"] is True and out["symbol"] == "SPX"
    assert (out["price"], out["source"], out["official"]) == (*answer, official)
