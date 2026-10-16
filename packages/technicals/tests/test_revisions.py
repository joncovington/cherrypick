"""The analyst-revision rule, the board decoder it is scored on, and the collector's append-only store."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.technicals import editions, revisions
from cherrypick.technicals.editions import BoardRow
from cherrypick.technicals.revisions import Event, score, session_of, tags

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_analyst_revisions.py"


def ev(
    action="main",
    prior=100.0,
    current=100.0,
    to_grade="Buy",
    at="2026-10-13T13:00:00+00:00",
    sym="X",
    firm="F",
):
    return Event(sym, datetime.fromisoformat(at), firm, "", to_grade, action, prior, current)


# --------------------------------------------------------------------------- the rule


def test_an_upgrade_or_downgrade_counts_whatever_the_target():
    assert score(ev("up", 0.0, 0.0)) == 1
    assert score(ev("down", 60.0, 80.0)) == -1, "MRNA: downgraded to Sell while the target rose"


def test_an_initiation_counts_only_when_it_starts_on_a_side():
    assert score(ev("init", 0.0, 130.0, "Outperform")) == 1
    assert score(ev("init", 0.0, 65.0, "Underweight")) == -1
    assert score(ev("init", 0.0, 210.0, "Hold")) == 0, "a neutral initiation is not bullish or bearish"


def test_a_target_change_counts_from_five_percent():
    assert score(ev("main", 2000.0, 2100.0)) == 1, "MU's DA Davidson +5% was tagged"
    assert score(ev("main", 100.0, 104.9)) == 0
    assert score(ev("main", 52.0, 48.0)) == -1
    assert score(ev("reit", 770.0, 770.0)) == 0, "a reiteration at the same target never counts"


def test_a_missing_target_is_no_change_not_a_crash():
    assert score(ev("main", 0.0, 133.0)) == 0
    assert score(ev("main", None, None)) == 0


def test_an_event_belongs_to_its_new_york_trading_day():
    assert session_of(datetime(2026, 10, 13, 13, 0, tzinfo=UTC)) == "2026-10-13"
    assert session_of(datetime(2026, 10, 14, 2, 0, tzinfo=UTC)) == "2026-10-13", "22:00 ET is still the 13th"
    assert session_of(datetime(2026, 10, 10, 15, 0, tzinfo=UTC)) == "2026-10-12", "a Saturday rolls to Monday"


def test_tags_hold_one_session_only():
    events = [
        ev("up", at="2026-10-13T13:00:00+00:00", sym="A"),
        ev("up", at="2026-10-12T13:00:00+00:00", sym="B"),
    ]
    assert set(tags(events, "2026-10-13")) == {"A"}


def test_a_rating_change_outranks_a_target_change_and_the_largest_target_move_decides():
    mixed = [ev("main", 1150.0, 1300.0, firm="Citi"), ev("main", 1525.0, 1400.0, firm="WF")]
    assert tags(mixed, "2026-10-13")["X"].direction == 1, "MU 2026-09-23: +13% over -8%"
    rating = [ev("main", 100.0, 140.0), ev("down", 100.0, 100.0)]
    assert tags(rating, "2026-10-13")["X"].direction == -1


# --------------------------------------------------------------------------- the board decoder


def _chip(text, detail=""):
    extra = f'<span style="opacity: 0.8;">Â· {detail}</span>' if detail else ""
    return f'<span style="padding: 3px 7px; border-radius: 10px; white-space: nowrap;">{text} {extra}</span>'


def _row(sym, *chips):
    return f'<tr><td><a href="https://x/?symbol={sym}">{sym}</a></td><td>{"".join(chips)}</td></tr>'


def _edition(bullish, bearish):
    badge = '<span style="x">Bearish</span>'  # a D-Edge badge, before the list headings
    return (
        f"<h2>4. Stacked Signals</h2>{badge}<div>Bullish</div><table>{''.join(bullish)}</table>"
        f"<div>Bearish</div><table>{''.join(bearish)}</table><h2>5. Sector Rotation</h2>"
    )


def test_the_board_decoder_reads_sides_tags_and_the_revision_detail():
    html = _edition(
        [_row("AEP", _chip("Early Breakout"), _chip("Analyst Bullish Revision", "KeyBanc upgrade"))],
        [
            _row("BMY", _chip("Analyst Bearish Revision", "Wells Fargo PT -8%")),
            _row("JNJ", _chip("Weak fundamentals")),
        ],
    )
    board = editions.decode_board(html)
    assert board["AEP"] == BoardRow(
        "bullish", ("Early Breakout", "Analyst Bullish Revision"), 1, "KeyBanc upgrade"
    )
    assert board["BMY"].side == "bearish" and board["BMY"].revision == -1
    assert board["BMY"].revision_detail == "Wells Fargo PT -8%"
    assert board["JNJ"] == BoardRow("bearish", ("Weak fundamentals",), 0, "")


def test_a_bearish_badge_before_the_headings_does_not_flip_the_bullish_list():
    html = _edition([_row("AAA", _chip("Compelling fundamentals"))], [])
    assert editions.decode_board(html)["AAA"].side == "bullish"


# --------------------------------------------------------------------------- the scorer


def test_the_scorer_separates_agreement_gaps_extras_and_a_day_without_the_method():
    s = "2026-10-13"
    boards = {
        s: {
            "AGREE": BoardRow("bullish", (), 1, "a"),
            "FLIP": BoardRow("bullish", (), 1, "b"),
            "MISS": BoardRow("bearish", (), -1, "c"),
            "GAP": BoardRow("bearish", (), -1, "d"),
            "EXTRA": BoardRow("bullish", (), 0, ""),
            "QUIET": BoardRow("bullish", (), 0, ""),
        },
        "2026-10-07": {"MU": BoardRow("bullish", (), 0, "")},
    }
    events = [
        ev("up", sym="AGREE"),
        ev("down", sym="FLIP"),
        ev("main", 100.0, 101.0, sym="MISS"),  # an event that day, below the rule
        ev("init", 0.0, 50.0, "Overweight", sym="EXTRA"),
        ev("up", at="2026-10-07T13:00:00+00:00", sym="MU"),
    ]
    got = revisions.score_boards(events, boards)
    assert got["sessions"] == 1 and got["method_absent"] == ["2026-10-07"]
    assert got["tagged"] == 4 and got["agree"] == 1
    assert [c.split()[1] for c in got["opposite"]] == ["FLIP:"]
    assert [c.split()[1] for c in got["missed"]] == ["MISS:"]
    assert [c.split()[1] for c in got["not_in_source"]] == ["GAP:"]
    assert got["untagged"] == 2 and [c.split()[1] for c in got["extra"]] == ["EXTRA:"]


# --------------------------------------------------------------------------- the collector


def _script():
    spec = importlib.util.spec_from_file_location("fetch_analyst_revisions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HISTORY = {
    "quoteSummary": {
        "result": [
            {
                "upgradeDowngradeHistory": {
                    "history": [
                        {
                            "epochGradeDate": 1791214001,
                            "firm": "Barclays",
                            "toGrade": "Overweight",
                            "fromGrade": "Overweight",
                            "action": "main",
                            "priceTargetAction": "Lowers",
                            "currentPriceTarget": 37.0,
                            "priorPriceTarget": 48.0,
                        }
                    ]
                }
            }
        ]
    }
}


def test_the_collector_keeps_what_it_first_saw_and_only_moves_last_seen(tmp_path):
    m = _script()
    conn = sqlite3.connect(tmp_path / "r.db")
    conn.executescript(m.SCHEMA)
    assert m.store(conn, m.rows_of("NKE", m.parse(HISTORY), "2026-10-15T00:00:00+00:00")) == 1
    note = HISTORY["quoteSummary"]["result"][0]["upgradeDowngradeHistory"]["history"][0]
    edited = {
        "quoteSummary": {
            "result": [{"upgradeDowngradeHistory": {"history": [{**note, "currentPriceTarget": 40.0}]}}]
        }
    }
    assert m.store(conn, m.rows_of("NKE", m.parse(edited), "2026-10-16T00:00:00+00:00")) == 0
    row = conn.execute("SELECT pt_current, first_seen_at, last_seen_at FROM revision_events").fetchone()
    assert row == (37.0, "2026-10-15T00:00:00+00:00", "2026-10-16T00:00:00+00:00")


def test_the_collector_reads_an_empty_or_unknown_symbol_as_no_history():
    m = _script()
    assert m.parse({"quoteSummary": {"result": None, "error": None}}) == []
    assert m.parse({"quoteSummary": {"result": [{}]}}) == []


def test_load_reads_what_the_collector_wrote(tmp_path):
    m = _script()
    path = tmp_path / "r.db"
    conn = sqlite3.connect(path)
    conn.executescript(m.SCHEMA)
    m.store(conn, m.rows_of("NKE", m.parse(HISTORY), "2026-10-15T00:00:00+00:00"))
    conn.commit()
    conn.close()
    (e,) = revisions.load(path)
    assert (e.symbol, e.firm, e.action, e.pt_prior, e.pt_current) == ("NKE", "Barclays", "main", 48.0, 37.0)
    assert score(e) == -1 and session_of(e.graded_at) == "2026-10-05"
