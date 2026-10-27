"""check-adjust: our adjusted closes against Yahoo's, read as the ratio of the two.

Each fixture lands a name's RAW bars in a fixture store and writes Yahoo's adjusted closes as the
correct adjustment of those bars (`adjust.adjust` with every true event). Leaving an event out of
the store is then exactly the defect the check exists for, and it must name the date.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.technicals import adjust, adjust_check, paths, store

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_yahoo_adjusted.py"


def _sessions(n: int, start: date = date(2026, 3, 2)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _raw(days: list[str], split_on: str | None = None, ratio: float = 2.0) -> list[adjust.Bar]:
    """A wandering price; from `split_on` the raw prices are divided by `ratio` (a split)."""
    bars = []
    for i, d in enumerate(days):
        p = 100 + 5 * math.sin(i / 4) + i * 0.1
        if split_on and d >= split_on:
            p /= ratio
        bars.append(adjust.Bar(d, p, p * 1.01, p * 0.99, p, 1000))
    return bars


def _land(symbol, bars, splits=(), dividends=(), yahoo_closes=None, yahoo_divs=(), yahoo_splits=()):
    """The store holds `bars` with `splits`/`dividends`; Yahoo's file holds `yahoo_closes`
    (default: the correct adjustment of the bars by the store's own events)."""
    conn = store.connect()
    store.upsert_bars(conn, [(symbol, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars])
    store.upsert_splits(conn, [(symbol, s.ex_date, s.to_factor, s.for_factor) for s in splits])
    store.upsert_dividends(conn, [(symbol, d.ex_date, d.amount) for d in dividends])
    conn.commit()
    conn.close()
    if yahoo_closes is None:
        yahoo_closes = {b.date: b.close for b in adjust.adjust(bars, list(splits), list(dividends))}
    y = adjust_check.connect_yahoo()
    with y:
        y.execute("DELETE FROM closes WHERE symbol = ?", (symbol,))
        y.executemany(
            "INSERT INTO closes VALUES (?, ?, ?, ?)",
            [(symbol, d, c, c) for d, c in sorted(yahoo_closes.items())],
        )
        last = max(yahoo_closes)
        y.execute(
            "INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                "2026-10-27T00:00:00+00:00",
                "2026-10-27",
                "USD",
                min(yahoo_closes),
                last,
                str([list(e) for e in yahoo_divs]).replace("'", '"'),
                str([list(e) for e in yahoo_splits]).replace("'", '"'),
            ),
        )
    y.close()


def _yahoo_truth(bars, splits=(), dividends=()) -> dict[str, float]:
    return {b.date: b.close for b in adjust.adjust(bars, list(splits), list(dividends))}


@pytest.fixture
def days():
    return _sessions(80)


def test_names_that_adjust_alike_agree(days):
    bars = _raw(days, split_on=days[30])
    _land("AAA", bars, splits=[adjust.Split(days[30], 2, 1)], dividends=[adjust.Dividend(days[50], 0.8)])
    result = adjust_check.check(names=["AAA"])
    assert result["agree"] == 1 and result["disagree"] == 0 and not result["stale"]


def test_an_unmodelled_two_for_one_split_is_flagged_on_its_date(days):
    """The guard shown to fail: the store lacks a split Yahoo applied."""
    split_day = days[40]
    bars = _raw(days, split_on=split_day)
    truth = _yahoo_truth(bars, [adjust.Split(split_day, 2, 1)])
    _land("AAA", bars, yahoo_closes=truth, yahoo_splits=[(split_day, 2.0, 1.0)])
    result = adjust_check.check(names=["AAA"])
    assert result["disagree"] == 1
    (step,) = result["by_symbol"]["AAA"]["steps"]
    assert step["date"] == split_day
    assert step["size"] == pytest.approx(2.0, rel=1e-6)  # our earlier bars are twice Yahoo's
    assert step["cause"] == "split: Yahoo's only"
    assert step["yahoo"]["splits"] == [[split_day, 2.0, 1.0]]


def test_once_the_split_is_in_the_store_the_same_name_agrees(days):
    split_day = days[40]
    bars = _raw(days, split_on=split_day)
    _land("AAA", bars, splits=[adjust.Split(split_day, 2, 1)])
    assert adjust_check.check(names=["AAA"])["disagree"] == 0


def test_a_split_neither_side_lists_is_still_named_by_its_size(days):
    split_day = days[40]
    bars = _raw(days, split_on=split_day, ratio=1.5)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, [adjust.Split(split_day, 3, 2)]))
    (step,) = adjust_check.check(names=["AAA"])["by_symbol"]["AAA"]["steps"]
    assert step["date"] == split_day and step["cause"] == "split-sized: neither lists a split"


@pytest.mark.parametrize("split_at", [40, 79])
def test_a_missed_split_beyond_3x_is_not_hidden_by_the_series_restarting(days, split_at):
    """A 10-for-1 move makes `series_break` start our series over on the split, so the overlap
    holds no step; the restart is read from the raw closes instead -- also when the split is the
    newest session and the overlap is a single bar."""
    split_day = days[split_at]
    bars = _raw(days, split_on=split_day, ratio=10)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, [adjust.Split(split_day, 10, 1)]))
    result = adjust_check.check(names=["AAA"])
    (step,) = result["by_symbol"]["AAA"]["steps"]
    assert step["date"] == split_day and step["series_restart"]
    assert step["size"] == pytest.approx(10, rel=1e-6)
    assert step["cause"] == "split-sized: neither lists a split"


def test_a_split_on_the_newest_session_is_seen_the_morning_after(days):
    """A split Dolt has not caught up with: one session after it, on the shrunken edge window."""
    split_day = days[-1]
    bars = _raw(days, split_on=split_day)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, [adjust.Split(split_day, 2, 1)]))
    (step,) = adjust_check.check(names=["AAA"])["by_symbol"]["AAA"]["steps"]
    assert step["date"] == split_day and step["window"] == 1


def test_a_dividend_we_lack_fails_and_one_only_we_apply_is_yahoos_gap(days):
    bars = _raw(days)
    div = adjust.Dividend(days[45], 1.0)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, dividends=[div]), yahoo_divs=[(div.ex_date, 1.0)])
    _land("BBB", bars, dividends=[div], yahoo_closes=_yahoo_truth(bars))
    result = adjust_check.check(names=["AAA", "BBB"])
    (step,) = result["by_symbol"]["AAA"]["steps"]
    assert step["date"] == div.ex_date and step["cause"] == "dividend: Yahoo's only"
    assert "BBB" not in result["by_symbol"]
    (gap,) = result["yahoo_gaps"]["BBB"]
    assert gap["date"] == div.ex_date and gap["cause"] == "dividend: ours only"


def test_dividend_amounts_that_differ_beyond_the_tolerance_fail(days):
    bars = _raw(days)
    ours, theirs = adjust.Dividend(days[45], 1.0), adjust.Dividend(days[45], 1.5)
    _land(
        "AAA",
        bars,
        dividends=[ours],
        yahoo_closes=_yahoo_truth(bars, dividends=[theirs]),
        yahoo_divs=[(theirs.ex_date, 1.5)],
    )
    (step,) = adjust_check.check(names=["AAA"])["by_symbol"]["AAA"]["steps"]
    assert step["cause"] == "dividend: amounts differ"


def test_one_bad_print_and_a_short_stretch_of_different_closes_are_not_steps(days):
    bars = _raw(days)
    truth = _yahoo_truth(bars)
    truth[days[30]] *= 1.05  # one session's close differs
    for d in days[50:54]:  # four sessions in a row differ, then agree again
        truth[d] *= 0.97
    _land("AAA", bars, yahoo_closes=truth)
    result = adjust_check.check(names=["AAA"])
    assert "AAA" not in result["by_symbol"]
    assert [s["cause"] for s in result["excursions"]["AAA"]] == [
        "excursion: raw closes differ for a stretch"
    ] * 2


def test_a_declared_step_is_reported_and_does_not_fail(days, monkeypatch):
    split_day = days[40]
    bars = _raw(days, split_on=split_day)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, [adjust.Split(split_day, 2, 1)]))
    monkeypatch.setitem(adjust_check.KNOWN_STEPS, ("AAA", split_day), "a reason, written down")
    result = adjust_check.check(names=["AAA"])
    assert result["disagree"] == 0
    assert result["known"]["AAA"][0]["known"] == "a reason, written down"


def test_a_known_dividend_name_excuses_its_amounts_but_never_a_split(days, monkeypatch):
    monkeypatch.setitem(adjust_check.KNOWN_DIVIDEND_NAMES, "AAA", "amounts in another currency")
    split_day, ex = days[30], days[55]
    bars = _raw(days, split_on=split_day)
    ours, theirs = adjust.Dividend(ex, 1.0), adjust.Dividend(ex, 0.7)
    truth = _yahoo_truth(bars, [adjust.Split(split_day, 2, 1)], [theirs])
    _land(
        "AAA",
        bars,
        dividends=[ours],
        yahoo_closes=truth,
        yahoo_divs=[(ex, 0.7)],
        yahoo_splits=[(split_day, 2.0, 1.0)],
    )
    result = adjust_check.check(names=["AAA"])
    assert [s["date"] for s in result["known"]["AAA"]] == [ex]
    assert [s["date"] for s in result["by_symbol"]["AAA"]["steps"]] == [split_day]


def test_a_scrip_sized_split_only_we_apply_does_not_fail_and_a_real_one_does(days):
    """Yahoo does not adjust for a scrip dividend (NGG, PHG, FER on the first run)."""
    bars = _raw(days)
    _land("AAA", bars, splits=[adjust.Split(days[45], 1.02, 1)], yahoo_closes=_yahoo_truth(bars))
    _land("BBB", bars, splits=[adjust.Split(days[45], 2, 1)], yahoo_closes=_yahoo_truth(bars))
    result = adjust_check.check(names=["AAA", "BBB"])
    assert result["small_splits"]["AAA"][0]["cause"] == "small split: ours only"
    assert "AAA" not in result["by_symbol"]
    assert result["by_symbol"]["BBB"]["steps"][0]["cause"] == "split: ours only"


def test_a_yahoo_file_that_stopped_updating_is_stale(days):
    bars = _raw(days)
    _land("AAA", bars, yahoo_closes={d: c for d, c in _yahoo_truth(bars).items() if d <= days[-10]})
    result = adjust_check.check(names=["AAA"])
    assert result["stale"] and result["disagree"] == 0


def test_no_yahoo_file_at_all_is_stale_not_clean(days):
    conn = store.connect()
    store.upsert_bars(conn, [("AAA", b.date, b.open, b.high, b.low, b.close, b.volume) for b in _raw(days)])
    conn.commit()
    conn.close()
    assert not paths.yahoo_adjusted().exists()
    result = adjust_check.check(names=["AAA"])
    assert result["stale"] and result["not_fetched"] == ["AAA"]


def test_the_cli_exits_non_zero_on_a_disagreement(days, capsys):
    from cherrypick.technicals.__main__ import main

    split_day = days[40]
    bars = _raw(days, split_on=split_day)
    _land("AAA", bars, yahoo_closes=_yahoo_truth(bars, [adjust.Split(split_day, 2, 1)]))
    assert main(["check-adjust", "--symbols", "AAA"]) == 1
    _land("AAA", bars, splits=[adjust.Split(split_day, 2, 1)])
    assert main(["check-adjust", "--symbols", "AAA"]) == 0
    capsys.readouterr()


# --- the fetch script -------------------------------------------------------------------------


def _script():
    spec = importlib.util.spec_from_file_location("fetch_yahoo_adjusted", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chart(stamps, closes, adjs, divs=None, splits=None):
    events = {}
    if divs:
        events["dividends"] = {str(t): {"amount": a, "date": t} for t, a in divs}
    if splits:
        events["splits"] = {str(t): {"date": t, "numerator": n, "denominator": d} for t, n, d in splits}
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "gmtoffset": -14400},
                    "timestamp": stamps,
                    "events": events,
                    "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": adjs}]},
                }
            ]
        }
    }


def test_the_script_parses_a_chart_into_exchange_dates_and_events():
    fy = _script()
    # 2026-10-23 and 10-26 at 09:30 ET (13:30 UTC), the shape Yahoo sent on 2026-10-27.
    body = _chart(
        [1792762200, 1793021400, 1793107800],
        [268.81, None, 270.0],
        [267.82, 268.0, 270.0],
        divs=[(1793021400, 0.26)],
        splits=[(1792762200, 4, 1)],
    )
    parsed = fy.parse(body)
    assert parsed["rows"] == [("2026-10-23", 268.81, 267.82), ("2026-10-27", 270.0, 270.0)]
    assert parsed["dividends"] == [("2026-10-26", 0.26)]
    assert parsed["splits"] == [("2026-10-23", 4.0, 1.0)]
    assert fy.parse({"chart": {"result": None, "error": {"code": "Not Found"}}}) is None
    assert fy.yahoo_symbol("BRK.B") == "BRK-B"


def test_the_script_replaces_a_name_whole_and_skips_names_fetched_today():
    from datetime import UTC, datetime

    fy = _script()
    conn = adjust_check.connect_yahoo(paths.yahoo_adjusted())
    now = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)
    fy.save(
        conn,
        "AAA",
        {
            "currency": "USD",
            "rows": [("2026-10-23", 10, 9), ("2026-10-26", 11, 10)],
            "dividends": [],
            "splits": [],
        },
        now,
    )
    fy.save(
        conn,
        "AAA",
        {
            "currency": "USD",
            "rows": [("2026-10-26", 11, 10.5)],
            "dividends": [["2026-10-26", 0.5]],
            "splits": [],
        },
        now,
    )
    rows = conn.execute("SELECT date, adjclose FROM closes WHERE symbol = 'AAA'").fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-26", 10.5)]
    today = now.astimezone().date()
    assert fy.due(["AAA", "BBB"], conn, today) == ["BBB"]
    assert fy.due(["AAA", "BBB"], conn, today, force=True) == ["AAA", "BBB"]
    loaded = adjust_check.load(conn)
    assert loaded["AAA"]["dividends"] == [["2026-10-26", 0.5]] and loaded["AAA"]["last_date"] == "2026-10-26"
