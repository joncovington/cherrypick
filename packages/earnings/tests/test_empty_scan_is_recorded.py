"""A session the scan reached no symbol on is still a recorded session.

Every other `scan_log` row is per (symbol, strategy), so a night with an empty calendar wrote
nothing at all -- and an absent row cannot say which absence it is. "The scan ran and found nothing"
and "the scan never ran" read identically, and the second is a failure mode this module has actually
had: nine sessions in a 45-day window have no scan rows, six of them the contiguous 2026-08-17..24
block. A person reading this table has already drawn the wrong conclusion from that silence once.

`curve_regime` settled the pattern for the suite -- a refusal is a row with its reason, never a gap.
"""

from cherrypick.earnings import strat_test_harness as harness

DAY = "2026-08-24"


def _capture(monkeypatch):
    rows = []
    monkeypatch.setattr(
        harness,
        "_log_scan_row",
        lambda date, symbol, strategy, profile, **kw: rows.append(
            {"date": date, "symbol": symbol, "strategy": strategy, "profile": profile, **kw}
        ),
    )
    return rows


def test_an_empty_calendar_leaves_a_row_saying_so(monkeypatch):
    rows = _capture(monkeypatch)

    harness._log_empty_scan(DAY, calendar=[], prefiltered={}, scanned=[])

    assert len(rows) == 1, "the session is on file even though no symbol was reached"
    row = rows[0]
    assert row["stage"] == "session" and row["outcome"] == "no_candidates"
    assert "no earnings candidates" in row["reason"]


def test_the_reason_separates_the_three_ways_to_reach_zero(monkeypatch):
    """They point at different code, so recording "zero" without which one is half an answer.

    An empty calendar is the earnings feed. A calendar emptied by the prefilter is the liquid-universe
    criteria. A non-empty calendar that still reached no symbol is the prefilter dropping every name
    it was handed -- which looks like the second but is reached differently and is worth telling
    apart when the number is surprising.
    """
    rows = _capture(monkeypatch)

    harness._log_empty_scan(DAY, calendar=[], prefiltered={}, scanned=[])
    harness._log_empty_scan(DAY, calendar=[], prefiltered={"AAPL": "adv"}, scanned=[])
    harness._log_empty_scan(DAY, calendar=[{"symbol": "AAPL"}], prefiltered={"AAPL": "adv"}, scanned=[])

    reasons = [r["reason"] for r in rows]
    assert "no earnings candidates" in reasons[0]
    assert "calendar empty after prefilter" in reasons[1] and "1 symbol" in reasons[1]
    assert "every candidate prefiltered" in reasons[2]
    assert len(set(reasons)) == 3, "three distinct causes must not collapse to one message"


def test_a_session_that_reached_symbols_writes_no_session_row(monkeypatch):
    """The row exists only to fill a gap. Where the per-symbol rows are the record, adding a
    session row on top would double-count every scan in any query grouping by scan_date.
    """
    rows = _capture(monkeypatch)

    harness._log_empty_scan(DAY, calendar=[{"symbol": "AAPL"}], prefiltered={}, scanned=[("AAPL", [], None)])

    assert rows == []


def test_the_session_row_cannot_be_mistaken_for_a_ticker(monkeypatch):
    """`symbol` is NOT NULL, so the row needs one. A sentinel that is not a valid ticker keeps
    "how many symbols did we screen" answerable by filtering the stage, without a real name ever
    appearing on a session that screened nothing.
    """
    rows = _capture(monkeypatch)

    harness._log_empty_scan(DAY, calendar=[], prefiltered={}, scanned=[])

    assert rows[0]["symbol"] == "-" and rows[0]["strategy"] == "-"
    assert rows[0]["profile"] == "session"
