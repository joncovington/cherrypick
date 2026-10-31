"""Item 31's event list and coverage pilot (scripts/earnings_item31.py): the entry session of each
timing, the seeded outcome-blind draw, the requests built from an event, the declared basis rule and
the coverage report, which carries counts and never a price. Invented events and quotes only, no
Dolt, no store of real data.

Each guard is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from cherrypick.backtest import intradayfetch as ix

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "earnings_item31.py"


def _module():
    spec = importlib.util.spec_from_file_location("earnings_item31", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e31 = _module()


def _events(n_per_year=80, years=range(2020, 2027)):
    out = []
    for y in years:
        for k in range(n_per_year):
            report = date(y, 1 + k % 12, 3 + k % 20)
            if report.weekday() >= 5:
                continue
            out.append(e31.event_row(f"S{k:03d}", report, "after_close" if k % 2 else "before_open"))
    return out


# --------------------------------------------------------------------------- the events


def test_an_after_close_report_enters_that_day_and_a_pre_market_one_the_session_before():
    friday, monday = date(2024, 2, 2), date(2024, 2, 5)
    assert e31.entry_session(friday, "after_close") == friday
    assert e31.entry_session(monday, "before_open") == friday, "the session before skips the weekend"
    assert e31.reaction_session(friday, "after_close") == monday
    assert e31.reaction_session(monday, "before_open") == monday


def test_build_events_drops_an_unstated_timing_keeps_one_event_per_name_and_date_and_maps_spellings():
    rows = [
        ("AAPL", date(2023, 2, 1), "After market close"),
        ("AAPL", date(2023, 2, 1), "After market close"),
        ("DAL", date(2024, 2, 1), None),
        ("DAL", date(2024, 4, 11), "Before market open"),
        ("BRK.B", date(2024, 5, 4), "After market close"),
    ]
    events, no_timing = e31.build_events(rows)
    assert no_timing == 1
    assert [(e["symbol"], e["timing"]) for e in events] == [
        ("AAPL", "after_close"),
        ("DAL", "before_open"),
        ("BRK.B", "after_close"),
    ]
    counts = e31.summarise(events, no_timing)
    assert counts["design"] == {"after_close": 1, "before_open": 0}
    assert counts["holdout"] == {"after_close": 1, "before_open": 1}
    assert counts["no_stated_timing_dropped"] == 1


# --------------------------------------------------------------------------- the draw


def test_the_draw_takes_thirty_a_year_for_2020_to_2025_and_is_stable():
    events = _events()
    first = e31.pilot_draw(events)
    assert first == e31.pilot_draw(list(reversed(events))), "the order events arrive in does not matter"
    by_year = {y: sum(1 for e in first if e["report_date"].startswith(str(y))) for y in range(2019, 2028)}
    assert {y: n for y, n in by_year.items() if n} == dict.fromkeys(range(2020, 2026), 30)
    assert e31.draw_key(31, "AAPL", "2024-02-01") == e31.draw_key(31, "AAPL", "2024-02-01")
    assert e31.draw_key(31, "AAPL", "2024-02-01") != e31.draw_key(32, "AAPL", "2024-02-01")
    # a fixed point: the hash of a seed, a name and a date, so a draw is the same on any machine
    assert e31.draw_key(31, "AAPL", "2024-02-01").startswith("3f5bc2")


def test_the_draw_is_blind_to_anything_but_the_name_and_the_date():
    events = _events()
    baseline = e31.pilot_draw(events)
    # an outcome-like field, however it is set, reaches nothing
    loaded = [{**e, "outcome": (-1) ** i * i, "move": i / 7} for i, e in enumerate(events)]
    again = e31.pilot_draw(loaded)
    assert [(e["symbol"], e["report_date"]) for e in again] == [
        (e["symbol"], e["report_date"]) for e in baseline
    ]


def test_the_outcome_blindness_check_fires_on_a_draw_that_reads_an_outcome(monkeypatch):
    events = _events()
    baseline = {(e["symbol"], e["report_date"]) for e in e31.pilot_draw(events)}
    loaded = [{**e, "outcome": (i * 7919) % 101} for i, e in enumerate(events)]
    outcomes = {(e["symbol"], e["report_date"]): e["outcome"] for e in loaded}
    # broken on purpose: rank by outcome instead of the hash
    monkeypatch.setattr(e31, "draw_key", lambda seed, sym, d: f"{outcomes[(sym, d)]:05d}")
    broken = {(e["symbol"], e["report_date"]) for e in e31.pilot_draw(loaded)}
    assert broken != baseline


def test_an_early_close_entry_is_left_out_of_the_pool_and_a_thin_year_gives_all_it_has():
    # the day after Thanksgiving is a 13:00 close: an after-close report that day has no 15:40 bar
    early = e31.event_row("ZZZ", date(2024, 11, 29), "after_close")
    assert early["early_close_entry"]
    events = [early, *(e31.event_row(f"S{k}", date(2024, 3, 4 + k), "after_close") for k in range(5))]
    drawn = e31.pilot_draw(events, per_year=30)
    assert "ZZZ" not in {e["symbol"] for e in drawn}
    assert len(drawn) == 5


# --------------------------------------------------------------------------- the requests


def test_an_event_becomes_an_entry_window_and_the_next_sessions_day_on_the_weekly():
    ev = e31.event_row("AAPL", date(2024, 2, 1), "after_close")  # Thursday after the close
    entry, later = e31.requests_for(ev)
    assert (entry["kind"], entry["session"], entry["start"], entry["end"]) == (
        "entry",
        "2024-02-01",
        "15:35",
        "15:50",
    )
    assert entry["required"] == ["15:35", "15:40", "15:50"] and entry["interval"] == "5m"
    assert (later["kind"], later["session"], later["start"], later["end"]) == (
        "later",
        "2024-02-02",
        "09:40",
        "15:55",
    )
    assert later["required"] == ["09:40", "15:55"]
    assert entry["expiration"] == later["expiration"] == "2024-02-02", "the Friday on or after the reaction"
    pre = e31.requests_for(e31.event_row("DAL", date(2024, 2, 5), "before_open"))  # Monday before the open
    assert [r["session"] for r in pre] == ["2024-02-02", "2024-02-05"]
    assert pre[0]["expiration"] == "2024-02-09"


def test_the_weekly_is_the_thursday_when_the_friday_is_a_holiday_and_an_early_close_ends_the_window_early():
    assert e31.weekly_expiry(date(2024, 3, 25)) == date(2024, 3, 28), "Good Friday 2024"
    assert e31.later_window(date(2024, 11, 29)) == ("09:40", "12:55")
    assert e31.later_window(date(2024, 11, 27)) == ("09:40", "15:55")


def test_a_pilot_request_file_reads_back_through_the_fetchs_own_reader_once_each(tmp_path):
    reqs = e31.pilot_requests(_events())
    assert len(reqs) == 2 * 30 * 6, "an entry window and a later day for 30 events in each of 6 years"
    f = tmp_path / "pilot-requests.json"
    f.write_text(json.dumps({"requests": reqs}), encoding="utf-8")
    keys = ix.read_requests(f)
    assert len(keys) == len(set(keys)) == len(reqs)


# --------------------------------------------------------------------------- the basis rule


def _years(shares, n=60):
    return {
        y: {"requests": n, "complete": round(n * s)} for y, s in zip(range(2020, 2026), shares, strict=True)
    }


def test_the_basis_is_intraday_only_when_every_year_reaches_ninety_percent():
    assert e31.basis(_years([0.95, 0.9, 1.0, 0.92, 0.97, 0.9])) == "intraday"
    assert e31.basis(_years([0.95, 0.9, 1.0, 0.9, 0.97, 0.88])) == "end_of_day", "one year under"
    assert e31.basis(_years([1.0] * 5 + [0.0])) == "end_of_day", "an average over years would pass this"


def test_the_basis_rule_check_fires_on_a_rule_that_averages_the_years(monkeypatch):
    per_year = _years([1.0, 1.0, 1.0, 1.0, 1.0, 0.5])
    assert e31.basis(per_year) == "end_of_day"

    def averaged(per_year, threshold=0.9):  # broken on purpose
        done = sum(v["complete"] for v in per_year.values())
        return (
            "intraday" if done / sum(v["requests"] for v in per_year.values()) >= threshold else "end_of_day"
        )

    assert averaged(per_year) == "intraday", "so the per-year test above would have caught it"


def test_a_year_with_no_requests_or_no_years_at_all_is_not_a_pass():
    assert e31.basis({}) == "end_of_day"
    assert e31.basis({2020: {"requests": 0, "complete": 0}}) == "end_of_day"


# --------------------------------------------------------------------------- the coverage report

PRICE = 123.456


def _bars(bars, **over):
    rows = []
    for b in bars:
        for strike, right in ((100.0, "CALL"), (100.0, "PUT")):
            row = {"bar": b, "strike": strike, "right": right, "bid": PRICE, "ask": PRICE + 0.1, "delta": 0.4,
                   "iv": 0.3, "underlying": PRICE + 7}  # fmt: skip
            row.update(over)
            rows.append(row)
    return rows


def _store(tmp_path):
    conn = ix.sqlite3.connect(tmp_path / "s.db")
    conn.executescript(ix.SCHEMA)
    return conn


def _req(**over):
    base = {
        "symbol": "AAPL",
        "session": "2024-02-01",
        "expiration": "2024-02-02",
        "start": "15:35",
        "end": "15:50",
        "interval": "5m",
        "kind": "entry",
        "year": 2024,
        "required": ["15:35", "15:40", "15:50"],
    }
    return {**base, **over}


def _put(conn, req, rows):
    key = (req["symbol"], req["session"], req["expiration"], req["start"], req["end"], req["interval"])
    ix.write_window(conn, key, rows)


def test_a_request_is_complete_only_when_every_required_bar_is_whole(tmp_path):
    conn = _store(tmp_path)
    ok, no_iv, short, gap = _req(), _req(symbol="B"), _req(symbol="C"), _req(symbol="D")
    _put(conn, ok, _bars(["15:35", "15:40", "15:45", "15:50"]))
    _put(conn, no_iv, _bars(["15:35", "15:40"]) + _bars(["15:50"], iv=0.0))
    _put(conn, short, _bars(["15:35", "15:40"]))
    _put(conn, gap, _bars(["15:35", "15:40", "15:50"], delta=None))
    assert e31.judge(conn, ok)["state"] == "complete"
    j = e31.judge(conn, no_iv)
    assert (j["state"], j["missing"]) == ("incomplete", ["15:50"]), (
        "an implied vol of zero is the solver's failure"
    )
    assert e31.judge(conn, short)["missing"] == ["15:50"]
    assert e31.judge(conn, gap)["state"] == "incomplete"


def test_the_completeness_check_fires_when_a_required_bar_is_dropped(tmp_path):
    conn = _store(tmp_path)
    req = _req()
    _put(conn, req, _bars(["15:35", "15:40"]))
    assert e31.judge(conn, req)["state"] == "incomplete"
    assert e31.judge(conn, {**req, "required": ["15:35", "15:40"]})["state"] == "complete", (
        "so the missing 15:50 is what the check above read"
    )


def test_coverage_counts_by_year_decides_the_basis_and_carries_no_price(tmp_path):
    conn = _store(tmp_path)
    reqs = []
    for year in (2020, 2021):
        for k in range(10):
            req = _req(symbol=f"S{k}", year=year, session=f"{year}-02-01", expiration=f"{year}-02-05")
            reqs.append(req)
            _put(conn, req, _bars(["15:35", "15:40", "15:50"]) if not (year == 2021 and k == 0) else [])
    report = e31.coverage(conn, reqs)
    assert report["decided"] and report["basis"] == "intraday", "nine in ten is the bar, met"
    assert report["years"]["2021"]["share_complete"] == 0.9
    assert report["years"]["2020"]["share_complete"] == 1.0
    assert report["years"]["2021"]["empty"] == 1 and report["years"]["2021"]["rows"] == 54
    text = json.dumps(report)
    assert str(PRICE) not in text and "123.4" not in text and "130.4" not in text
    assert not {"bid", "ask", "price", "pnl", "return", "mid"} & set(_all_keys(report))


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)


def test_one_year_under_ninety_percent_sends_the_whole_study_to_the_end_of_day_basis(tmp_path):
    conn = _store(tmp_path)
    reqs = []
    for year in (2020, 2021):
        for k in range(10):
            req = _req(symbol=f"S{k}", year=year, session=f"{year}-02-01", expiration=f"{year}-02-05")
            reqs.append(req)
            _put(conn, req, _bars(["15:35", "15:40", "15:50"]) if not (year == 2021 and k < 2) else [])
    report = e31.coverage(conn, reqs)
    assert report["decided"] and report["basis"] == "end_of_day"


def test_unfetched_requests_and_runs_that_could_not_ask_leave_the_decision_pending(tmp_path):
    conn = _store(tmp_path)
    done, waiting, blocked = _req(), _req(symbol="B"), _req(symbol="C")
    _put(conn, done, _bars(["15:35", "15:40", "15:50"]))
    with conn:
        ix._record(conn, ("C", "2024-02-01", "2024-02-02", "15:35", "15:50", "5m"), "refused", 0, 0,
                   "session_in_use: another client held the session")  # fmt: skip
    report = e31.coverage(conn, [done, waiting, blocked])
    assert report["unanswered"] == 2 and report["decided"] is False and report["basis"] is None
    # a refusal about the data (the tier does not serve it) is an answer, and counts against coverage
    with conn:
        ix._record(
            conn,
            ("B", "2024-02-01", "2024-02-02", "15:35", "15:50", "5m"),
            "refused",
            0,
            0,
            "permission_denied: not on this tier",
        )
        ix._record(
            conn,
            ("C", "2024-02-01", "2024-02-02", "15:35", "15:50", "5m"),
            "refused",
            0,
            0,
            "permission_denied: not on this tier",
        )
    again = e31.coverage(conn, [done, waiting, blocked])
    assert (
        again["decided"]
        and again["basis"] == "end_of_day"
        and again["refusal_reasons"] == {"permission_denied": 2}
    )


def test_the_script_computes_no_price_anywhere():
    """The pilot reads presence and counts. No arithmetic on bid, ask, mid or underlying exists in it."""
    src = SCRIPT.read_text(encoding="utf-8")
    for needle in ("(bid + ask)", "bid + ask", "mid =", "pnl", "- entry", "net ="):
        assert needle not in src
    # the only reads of the quotes table test a column for being there
    selects = [line for line in src.splitlines() if "intraday_chain_quotes" in line and "SELECT" in line]
    assert selects and all(line.strip().startswith('"SELECT 1 FROM') for line in selects)
