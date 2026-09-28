"""Per-symbol earnings entry-review notifications (trade_notifier)._earnings_* review path.

Unit lane: an in-memory earnings paper DB with an `entry_reviews` table; asserts the id-watermark reader,
the bullet-format message the account owner asked for, and that a DB predating the feature is guarded.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_COLS = (
    "scan_date",
    "symbol",
    "timing",
    "price",
    "volume",
    "winrate",
    "winrate_sample",
    "iv_rv_ratio",
    "term_structure",
    "market_cap",
    "best_tier",
    "selected",
    "reason",
    "profile",
)


def _row(**kw):
    kw.setdefault("scan_date", "2026-07-16")
    kw.setdefault("profile", "strat_test")
    return tuple(kw.get(c) for c in _COLS)


def _conn_with_reviews(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE entry_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT, symbol TEXT, "
        "timing TEXT, price REAL, volume REAL, winrate REAL, winrate_sample INTEGER, iv_rv_ratio REAL, "
        "term_structure REAL, market_cap REAL, expected_move REAL, best_tier TEXT, selected INTEGER, "
        "reason TEXT, profile TEXT)"
    )
    conn.executemany(
        f"INSERT INTO entry_reviews ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows
    )
    conn.commit()
    return conn


_ISRG = _row(
    symbol="ISRG",
    timing="AMC",
    price=402.05,
    volume=2702779,
    winrate=0.75,
    winrate_sample=12,
    iv_rv_ratio=1.47,
    term_structure=-0.019,
    market_cap=142391166303,
    best_tier="accepted",
    selected=1,
    reason="opened iron_fly, iron_condor",
)
_NFLX = _row(
    symbol="NFLX",
    winrate=0.60,
    winrate_sample=8,
    best_tier="rejected",
    selected=0,
    reason="screen_rejected (7 strategies evaluated)",
)


def test_new_reviews_respects_id_watermark():
    conn = _conn_with_reviews([_ISRG, _NFLX])
    assert [r["symbol"] for r in tn._earnings_new_reviews(conn, set())] == ["ISRG", "NFLX"]
    # After id 1 is notified, only id 2 is new.
    assert [r["symbol"] for r in tn._earnings_new_reviews(conn, {1})] == ["NFLX"]


def test_fmt_review_matches_requested_bullet_layout():
    conn = _conn_with_reviews([_ISRG])
    msg = tn._fmt_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert "ISRG" in msg and "chosen" in msg and "opened iron_fly, iron_condor" in msg
    assert "• Price: $402.05" in msg
    assert "• Volume: 2,702,779" in msg
    assert "• Winrate: 75.0% over last 12 earnings" in msg
    assert "• IV/RV Ratio: 1.47" in msg
    assert "• Term Structure: -0.019" in msg
    assert "• Market Cap: 142,391,166,303" in msg


def test_rejected_review_reads_as_rejected_and_omits_missing_fields():
    conn = _conn_with_reviews([_NFLX])
    msg = tn._fmt_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert "NFLX" in msg and "rejected" in msg and "screen_rejected" in msg
    assert "Price" not in msg  # None fields are omitted, not shown as n/a


def test_reviews_guarded_when_table_absent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert tn._earnings_new_reviews(conn, set()) == []
    assert tn._all_review_ids(conn) == []


# --------------------------------------------------------------------------- the card
def test_chosen_review_card_matches_the_house_language():
    """Same shape as the meic/flies cards: verb · subject title, numbers in one Details field, the
    profile in the footer. The review was the one earnings push still going out as bare text."""
    conn = _conn_with_reviews([_ISRG])
    embed = tn._embed_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert embed["title"] == "CHOSEN · ISRG (AMC)"
    assert embed["color"] == tn.COLOR_CHOSEN
    assert embed["footer"]["text"] == "strat_test"
    details = embed["fields"][0]["value"]
    assert details.startswith("opened iron_fly, iron_condor")
    assert "• Price: $402.05" in details
    assert "• Winrate: 75.0% over last 12 earnings" in details


def test_rejected_review_card_takes_its_own_color_and_verb():
    conn = _conn_with_reviews([_NFLX])
    embed = tn._embed_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert embed["title"] == "REJECTED · NFLX"  # no timing recorded -> no parenthetical
    assert embed["color"] == tn.COLOR_REJECTED
    assert "Price" not in embed["fields"][0]["value"]


def test_review_card_never_reuses_the_entry_stripe():
    """A chosen symbol is not yet a position — sharing entry-blue would make the review and the fill
    that follows it read as the same event twice."""
    assert tn.COLOR_CHOSEN != tn.COLOR_ENTRY
    assert tn.COLOR_REJECTED not in (tn.COLOR_ENTRY, tn.COLOR_EXIT, tn.COLOR_STOP)


def test_card_and_plain_line_report_the_same_figures():
    """Both render from one bullet builder, so they cannot drift into disagreeing about a review."""
    conn = _conn_with_reviews([_ISRG])
    row = tn._earnings_new_reviews(conn, set())[0]
    bullets = tn._earnings_review_bullets(row)
    line, details = tn._fmt_earnings_review(row), tn._embed_earnings_review(row)["fields"][0]["value"]
    assert bullets and all(b in line and b in details for b in bullets)


def test_an_overlong_reason_is_truncated_rather_than_dropping_the_push():
    """Discord rejects a field value past 1024 chars and the notifier swallows push failures, so an
    untruncated free-text reason would lose the notification silently."""
    conn = _conn_with_reviews([_row(symbol="AAPL", selected=0, reason="x" * 4000)])
    details = tn._embed_earnings_review(tn._earnings_new_reviews(conn, set())[0])["fields"][0]["value"]
    assert len(details) == tn._FIELD_MAX and details.endswith("…")


def test_review_with_no_figures_still_produces_a_valid_card():
    """Discord rejects an empty field value, so a review with nothing recorded needs a placeholder."""
    conn = _conn_with_reviews([_row(symbol="AAPL", selected=0, reason=None)])
    assert tn._embed_earnings_review(tn._earnings_new_reviews(conn, set())[0])["fields"][0]["value"]


# --- chosen pushed, rejected summarised ---
class _Recorder:
    """A notifier double: `notify` is a push, `record` is the log floor only."""

    def __init__(self):
        self.pushed, self.recorded = [], []

    def notify(self, level, key, title, message, embed=None):
        self.pushed.append((key, title, message))

    def record(self, level, key, title, message):
        self.recorded.append((key, title, message))


def _process(rows, monkeypatch):
    conn = _conn_with_reviews(rows)
    # Only the review path is under test; entries and exits read tables this DB does not have.
    monkeypatch.setattr(tn, "_earnings_new_entries", lambda conn, seen: [])
    monkeypatch.setattr(tn, "_earnings_new_exits", lambda conn, seen: [])
    rec = _Recorder()
    st = {"notified_entry_ids": [], "notified_exit_ids": [], "notified_review_ids": []}
    result = tn._earnings_process(conn, st, rec, "earnings")
    return rec, result


def test_a_chosen_review_is_pushed_and_a_rejection_is_only_recorded(monkeypatch):
    """1,054 of 1,103 review notices in two months were rejections, each its own push."""
    rec, result = _process(
        [
            _row(symbol="DHI", selected=1, reason="opened atm_calendar"),
            _row(symbol="IVA", selected=0, reason="avg_volume_below_minimum", volume=1216800.0),
            _row(symbol="GNS", selected=0, reason="no_listed_options"),
        ],
        monkeypatch,
    )
    pushed_titles = [t for _, t, _ in rec.pushed]
    assert pushed_titles.count("Earnings review") == 1 and "DHI" in rec.pushed[0][2]
    assert [k.split(".")[-1] for k, _, _ in rec.recorded] == ["2", "3"], "every rejection kept in the log"
    assert (
        result["reviews_notified"] == 1
        and result["reviews_recorded"] == 2
        and result["review_summaries"] == 1
    )


def _rows(*rows):
    return [dict(zip(("id", *_COLS), (i, *r), strict=True)) for i, r in enumerate(rows, 1)]


BARS = {"near_miss_min_avg_volume": 1_000_000.0}


def test_one_summary_per_scan_counts_checks_and_names_only_real_near_misses():
    rows = _rows(
        _row(symbol="DHI", selected=1, reason="opened atm_calendar"),
        _row(
            symbol="IVA",
            selected=0,
            reason="avg_volume_below_minimum (6 strategies evaluated)",
            volume=1216800.0,
        ),
        _row(symbol="RGS", selected=0, reason="avg_volume_below_minimum", volume=5833.0),
        _row(symbol="GNS", selected=0, reason="no_listed_options (6 strategies evaluated)"),
        _row(symbol="ZZZ", selected=0, reason="price_below_minimum; avg_volume_below_minimum", volume=2e6),
        _row(symbol="NEXT", scan_date="2026-07-17", selected=0, reason="no_listed_options"),
    )
    summaries = tn.review_summaries(rows, BARS)
    assert sorted(summaries) == ["2026-07-16", "2026-07-17"]
    text = summaries["2026-07-16"]
    assert "5 reviewed, 1 chosen, 4 rejected" in text
    assert "avg_volume_below_minimum 3" in text, "the 'strategies evaluated' tail must not split a count"
    near = text.split("Near misses:")[1]
    assert "IVA (volume 1,216,800" in near
    assert "RGS" not in near, "one failed check far below its near-miss bar is not a near miss"
    assert "GNS" not in near, "a hard check is never a near miss"
    assert "ZZZ" not in near, "two failed checks is not a near miss"


def test_without_the_earnings_config_a_summary_gives_counts_only():
    rows = _rows(_row(symbol="IVA", selected=0, reason="avg_volume_below_minimum", volume=1216800.0))
    assert "Near misses" not in tn.review_summaries(rows, {})["2026-07-16"]


def test_a_scan_that_rejected_nothing_sends_no_summary():
    assert (
        tn.review_summaries(
            [dict(zip(("id", *_COLS), (1, *_row(symbol="DHI", selected=1, reason="opened")), strict=True))]
        )
        == {}
    )


def test_a_name_with_no_data_is_counted_once_not_once_per_check():
    rows = _rows(
        _row(
            symbol="X",
            selected=0,
            reason="price_unverified; avg_volume_unverified; chain_complete_unverified",
        )
    )
    text = tn.review_summaries(rows, BARS)["2026-07-16"]
    assert "data_unavailable 1" in text and "price_unverified" not in text
