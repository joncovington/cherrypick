"""The signal study on the underlying alone (#1179): synthetic bars only."""

from __future__ import annotations

import importlib.util
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import calendar as cal
from cherrypick.core import signals as sg

from cherrypick.backtest import paths, readout, signalstudy

ROOT = Path(__file__).resolve().parents[1]
GAP = {"kind": "gap", "direction": "up", "pct": 1.0}


def weekdays(n: int, start: date = date(2024, 1, 2)) -> list[str]:
    """`n` exchange trading days from `start`."""
    out, d = [], start
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_a_shut_day_and_an_intraday_session_that_stops_early_are_dropped_and_counted():
    flat = sg.from_trail("2024-01-06", [(m, 100.0) for m in range(570, 960)], 30)  # a Saturday
    full = sg.from_trail("2024-01-08", [(m, 100.0) for m in range(570, 960)], 30)
    broke_off = sg.from_trail("2024-01-09", [(m, 100.0) for m in range(570, 700)], 30)
    early = sg.from_trail("2024-11-29", [(m, 100.0) for m in range(570, 780)], 30)  # 13:00 close
    kept, dropped = signalstudy.sessions_kept(flat + full + broke_off + early, intraday=True)
    assert sorted({b.session for b in kept}) == ["2024-01-08", "2024-11-29"]
    assert dropped == {"not_trading_day": 1, "incomplete": 1}
    # A daily bar always spans the session, so only the shut day goes.
    days = sg.daily(
        {"session": s, "open": 1, "high": 1, "low": 1, "close": 1} for s in ("2024-01-06", "2024-01-08")
    )
    assert signalstudy.sessions_kept(days, intraday=False)[1] == {"not_trading_day": 1, "incomplete": 0}


def daily(rows: list[tuple[float, float, float, float]]) -> list[sg.Bar]:
    days = weekdays(len(rows))
    return sg.daily(
        {"session": s, "open": o, "high": h, "low": lo, "close": c}
        for s, (o, h, lo, c) in zip(days, rows, strict=True)
    )


def request(**over) -> dict:
    base = {
        "name": "gap-up",
        "signal": GAP,
        "symbol": "SPY",
        "source": "history",
        "horizons": [1],
        "distances": [{"unit": "pct", "value": 2.0}, {"unit": "em", "value": 1.0}],
    }
    return {**base, **over}


# --- the request -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"colour": 1}, "request.colour: unknown key"),
        (
            {
                "signal": {
                    "kind": "event",
                    "event": "fomc",
                    "timing": "on",
                    "days": 0,
                    "unit": "sessions",
                    "at": "10:00",
                }
            },
            "event signals are refused",
        ),
        ({"bar_minutes": 5}, "a daily source has one bar a session"),
        ({"horizons": [0]}, "needs an intraday source"),
        ({"horizons": [1, 1]}, "distinct whole numbers"),
        ({"source": "recorder"}, "request.bar_minutes"),
        ({"symbol": "AAPL"}, "has no vol index"),
        ({"distances": [{"unit": "pts", "value": 1}]}, "request.distances[0].unit"),
        ({"name": "Gap Up"}, "request.name"),
        ({"from": "2025-01-01", "through": "2024-01-01"}, "after through"),
        ({"signal": {"kind": "gap", "direction": "up"}}, "request.signal"),
    ],
)
def test_validate_refuses_naming_the_path(over, message):
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        signalstudy.validate(request(**over))


def test_an_event_signal_hidden_in_only_when_is_refused_too():
    spec = {
        **GAP,
        "only_when": [
            {"kind": "event", "event": "fomc", "timing": "on", "days": 0, "unit": "sessions", "at": "10:00"}
        ],
    }
    with pytest.raises(ValueError, match="event signals are refused"):
        signalstudy.validate(request(signal=spec))


# --- one observation ---------------------------------------------------------------------------------


def test_an_observation_reads_the_bars_after_the_entry_through_the_horizon_close():
    bars = daily([(100, 101, 99, 100), (100.5, 103, 98, 101), (101, 102, 100, 101.5)])
    idx = signalstudy._Index(bars)
    o = signalstudy.observe(bars, idx, 0, 1, 16.0)
    assert o["move_pct"] == pytest.approx(1.0)
    assert (o["up_pts"], o["down_pts"]) == (3.0, 2.0)
    em = 100 * 0.16 * math.sqrt(1 / 252)
    assert o["em_pts"] == pytest.approx(em)
    assert o["range_em"] == pytest.approx(3.0 / em)
    # Two sessions on: the second bar's extremes still count, the entry bar's own never do.
    o2 = signalstudy.observe(bars, idx, 0, 2, None)
    assert (o2["up_pts"], o2["down_pts"], o2["em_pts"]) == (3.0, 2.0, None)
    assert signalstudy.observe(bars, idx, 1, 2, None) is None


def test_an_intraday_entry_reads_the_vol_close_before_its_session_a_close_entry_its_own():
    dates = ["2024-01-02", "2024-01-03"]
    vol = {"2024-01-02": 15.0, "2024-01-03": 30.0}
    assert signalstudy._vol_at(vol, dates, "2024-01-03", 600) == 15.0
    assert signalstudy._vol_at(vol, dates, "2024-01-03", sg.CLOSE_MINUTE) == 30.0
    assert signalstudy._vol_at(vol, dates, "2024-01-02", 600) is None


def test_breach_by_unit():
    o = {"up_pts": 3.0, "down_pts": 1.0, "up_pct": 3.0, "down_pct": 1.0, "em_pts": 2.0}
    assert signalstudy.breached(o, {"unit": "points", "value": 2}) == (True, False)
    assert signalstudy.breached(o, {"unit": "pct", "value": 1}) == (True, True)
    assert signalstudy.breached(o, {"unit": "em", "value": 1.5}) == (True, False)
    assert signalstudy.breached({**o, "em_pts": None}, {"unit": "em", "value": 1}) is None


# --- the study ---------------------------------------------------------------------------------------


def gap_history() -> list[sg.Bar]:
    """Ten sessions; sessions 3 and 7 open 2% above the close before (the gap fires), and each gap is
    followed by a 1% rise; the rest drift 0.1% a session."""
    rows, close = [], 100.0
    for k in range(10):
        if k in (3, 7):
            o = close * 1.02
            c = o
        else:
            o = close
            c = close * (1.01 if k in (4, 8) else 1.001)
        rows.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
        close = c
    return daily(rows)


def test_the_study_puts_the_firings_against_every_other_session():
    bars = gap_history()
    out = signalstudy.study(request(), bars, vol={b.session: 16.0 for b in bars})
    assert out["firings_n"] == 2
    assert [f["session"] for f in out["firings"]] == [bars[3].session, bars[7].session]
    h1 = out["horizons"][0]
    assert h1["fired"]["n"] == 2
    assert h1["fired"]["mean_pct"] == pytest.approx(1.0)
    # The control: every session without a firing, a defined gap (not the first) and a next session.
    assert h1["control"]["n"] == 6
    assert h1["difference"]["mean_pct"] > 0
    assert out["licensed"] is False and out["em_index"] == "VIX"
    assert {r["year"] for r in out["by_year"]} == {"2024"}


def test_the_span_bounds_firings_and_control_but_bars_before_it_still_warm_the_signal():
    bars = gap_history()
    out = signalstudy.study(request(**{"from": bars[5].session}), bars)
    assert [f["session"] for f in out["firings"]] == [bars[7].session]
    assert out["horizons"][0]["control"]["n"] == 3  # sessions 5, 6 and 8; 9 has no next


def trail_day(session: str, rise_at: int | None) -> list[sg.Bar]:
    """A flat session at 100 that rises 1% from `rise_at` (minutes since midnight) on, in 30-min bars."""
    trail = [(m, 101.0 if rise_at is not None and m >= rise_at else 100.0) for m in range(570, 960)]
    return sg.from_trail(session, trail, 30)


def test_the_control_matches_each_firing_minute_from_sessions_that_had_not_fired_by_then():
    days = weekdays(6)
    rises = {days[1]: 600, days[2]: 600, days[4]: 660}
    bars = [b for d in days for b in trail_day(d, rises.get(d))]
    spec = {"kind": "change", "from": "open", "direction": "up", "pct": 0.5}
    req = request(signal=spec, source="recorder", bar_minutes=30, horizons=[0, 1], distances=[])
    out = signalstudy.study(req, bars)
    assert [(f["session"], f["minute"]) for f in out["firings"]] == [
        (days[1], 630),
        (days[2], 630),
        (days[4], 690),
    ]
    idx = signalstudy._Index(bars)
    first = {days[1]: 630, days[2]: 630, days[4]: 690}
    entries = signalstudy.control_entries(req, idx, sg.state(spec, bars), {630: 2, 690: 1}, first)
    at = {m: {bars[i].session for i, _ in entries if bars[i].end == m} for m in (630, 690)}
    # days[4] fires at 11:30, so at 10:30 it had not: it is in that minute's control. Never at 11:30,
    # and the 10:30 firings are in neither.
    assert at[630] == {days[0], days[3], days[4], days[5]}
    assert at[690] == {days[0], days[3], days[5]}
    by_minute: dict[int, float] = {}
    for i, w in entries:
        by_minute[bars[i].end] = by_minute.get(bars[i].end, 0.0) + w
    assert by_minute == pytest.approx({630: 2.0, 690: 1.0})
    h0 = out["horizons"][0]
    assert h0["fired"]["n"] == 3 and h0["control"]["n"] == 7
    assert h0["fired"]["mean_pct"] == pytest.approx(0.0)  # flat after the rise, to the close


def test_a_result_is_licensed_only_over_the_vendor_store_and_lives_where_purge_reaches():
    assert paths.signals_dir(True).parent == paths.results_dir()
    assert paths.signals_dir(False).parent == paths.data_dir()
    assert {k for k, v in signalstudy.SOURCES.items() if v["licensed"]} == {"vendor"}


# --- the read verbs and the script -------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_DATA_DIR", str(tmp_path / "bt"))
    return tmp_path


def test_the_reads_list_both_folders_and_refuse_a_bad_name(home):
    bars = gap_history()
    pub = signalstudy.study(request(), bars)
    lic = signalstudy.study(request(name="gap-vendor", source="vendor", bar_minutes=390, symbol="SPX"), bars)
    for doc in (pub, lic):
        d = paths.signals_dir(doc["licensed"])
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{doc['name']}.json").write_text(json.dumps(doc), encoding="utf-8")
    (paths.signals_dir(False) / "broken.json").write_text("{", encoding="utf-8")
    listing = readout.signal_studies()
    assert {(r["name"], r["licensed"]) for r in listing["studies"]} == {
        ("gap-up", False),
        ("gap-vendor", True),
    }
    assert listing["unreadable"] == ["broken.json"]
    assert readout.signal_study("gap-up")["firings_n"] == 2
    assert readout.signal_study("../x")["ok"] is False
    assert readout.signal_study("absent")["ok"] is False


def _script():
    spec = importlib.util.spec_from_file_location("signal_study_script", ROOT / "scripts" / "signal_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_script_writes_the_result_where_its_source_says(home, monkeypatch, capsys):
    mod = _script()
    bars = gap_history()
    monkeypatch.setattr(mod, "load", lambda req: (bars, {"store": "test"}))
    monkeypatch.setattr(mod, "vol_closes", lambda index: {b.session: 16.0 for b in bars})
    req = home / "req.json"
    req.write_text(json.dumps(request()), encoding="utf-8")
    assert mod.main(["run", "--request", str(req)]) == 0
    doc = json.loads((paths.signals_dir(False) / "gap-up.json").read_text(encoding="utf-8"))
    assert doc["source"] == {"store": "test", "em_file": "market-files/cboe/VIX.csv"}
    assert doc["firings_n"] == 2
    req.write_text(json.dumps(request(colour=1)), encoding="utf-8")
    assert mod.main(["run", "--request", str(req)]) == 1
    assert "unknown key" in capsys.readouterr().out


def test_the_script_reads_cboe_closes(tmp_path, monkeypatch):
    mod = _script()
    cboe = tmp_path / "market-files" / "cboe"
    cboe.mkdir(parents=True)
    (cboe / "VIX.csv").write_text(
        "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2024,13,14,12,13.5\nbad,1,1,1,1\n", encoding="utf-8"
    )
    monkeypatch.setattr(mod.home, "data_dir", lambda name=None, **_k: tmp_path / name)
    assert mod.vol_closes("VIX") == {"2024-01-02": 13.5}
    assert mod.vol_closes("VXN") is None
    assert mod.vol_closes(None) is None
