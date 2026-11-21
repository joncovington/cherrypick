"""Item 101's context run (wheel_context_item101.py): invented chains and trades only, no store read."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs
from cherrypick.core import calendar as cal

from cherrypick.backtest.study.chains import Chain

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ctx = _load("wheel_context_item101")
declared = _load("wheel_cycle_item101")
base = ctx.base


# ------------------------------------------------------------------------------------------------
# The declared run is not touched.


def test_the_declared_scripts_chain_and_files_are_what_the_queue_runs():
    # Rows 541-544 queue this chain: its steps, arguments and outputs are the declared run's.
    assert [(s["step"], s.get("args")) for s in declared.CHAIN if s["kind"] == "script"] == [
        ("plan", ["plan"]),
        ("smoke", ["study", "--smoke"]),
        ("study", ["study"]),
    ]
    assert [s.get("output") for s in declared.CHAIN if s["kind"] == "script"] == [
        "{results}/path_requests.json",
        "{results}/smoke.json",
        "{results}/results.json",
    ]
    assert declared.REPLAY_CORRECTIONS == ()


def test_the_declared_script_takes_no_symbols_option_and_names_no_context_file():
    for step in ("plan", "study"):
        with pytest.raises(SystemExit):
            declared.main([step, "--symbols", "SOFI"])
    assert "extra-" not in (_SCRIPTS / "wheel_cycle_item101.py").read_text(encoding="utf-8")


def test_the_context_run_loads_the_declared_arms_and_rules_unchanged():
    assert base.ARMS == declared.ARMS and base.SPEC == declared.SPEC and base.RULE == declared.RULE
    assert ctx.REPLAY_CORRECTIONS == ()


def test_only_extra_files_may_be_written_never_the_declared_runs(tmp_path):
    for name in ("results.json", "smoke.json", "path_requests.json", "universe.json", "earnings_dates.json"):
        with pytest.raises(SystemExit):
            ctx.write_json(tmp_path / name, {})
        assert not (tmp_path / name).exists()
    for name in ("extra.json", "results.version.json", "extra-SOFI.txt"):
        with pytest.raises(SystemExit):
            ctx.checked_path(tmp_path / name)
    out = ctx.write_json(tmp_path / "extra-SOFI.json", {"verdict": ctx.VERDICT})
    assert out.exists()


def test_the_symbols_are_plain_sorted_tickers():
    assert ctx.parse_symbols("sofi, uber,SOFI") == ["SOFI", "UBER"]
    assert ctx.tag_of(["SOFI", "UBER"]) == "SOFI-UBER"
    for bad in ("", " , ", "../x", "SO FI", "TOOLONGSYM"):
        with pytest.raises(SystemExit):
            ctx.parse_symbols(bad)


# ------------------------------------------------------------------------------------------------
# Requests, starts, holes.


def test_the_requests_are_the_declared_ones_from_the_names_first_session():
    declared_reqs = base.requests(["SOFI"])
    assert ctx.requests_for("SOFI", date.fromisoformat(base.SPEC["entry_start"])) == declared_reqs
    later = ctx.requests_for("SOFI", date(2021, 6, 1))
    assert later[0]["start"] == "2021-06-01" and later[0]["expiration"] >= "2021-06-01"
    assert all(r["start"] >= "2021-06-01" for r in later)
    by_exp = {r["expiration"]: r for r in declared_reqs}
    mid = next(r for r in later if r["expiration"] == "2025-06-20")
    assert mid == by_exp["2025-06-20"]  # a path wholly after the start is the declared one


class _Inputs:
    """Stands in for `Inputs`: the stored sessions of each monthly, from the day the name lists."""

    def __init__(self, closes: dict, listed: date):
        self.closes, self.listed = closes, listed

    def sessions(self, conn, symbol, expiry):
        e = date.fromisoformat(expiry)
        return [
            d.isoformat()
            for d in sorted(self.closes)
            if self.listed <= d <= e and d >= e - timedelta(days=66)
        ]


def test_a_names_first_stored_session_is_the_first_monthly_chain_the_store_holds():
    closes = {d: 10.0 for d in _sessions(date(2024, 11, 1), 400)}
    got = ctx.first_stored(_Inputs(closes, date(2025, 3, 3)), None, "SOFI")
    assert got == date(2025, 3, 3)
    assert ctx.first_stored(_Inputs({}, date(2025, 3, 3)), None, "SOFI") is None


def test_the_holes_in_the_report_calendar_are_counted_not_filled():
    days = _sessions(date(2025, 1, 2), 120)
    reports = [date(2024, 11, 5), date(2025, 2, 4), date(2025, 7, 1)]  # a 147-day gap in the middle
    got = ctx.earnings_holes(reports, days)
    assert got["reports"] == 3 and got["gaps_over_limit"] == [
        {"from": "2025-02-04", "to": "2025-07-01", "days": 147}
    ]
    assert 0 < got["sessions_unknown"] < got["sessions"]
    assert ctx.earnings_holes([], days)["sessions_unknown"] == len(days)


# ------------------------------------------------------------------------------------------------
# The run, on invented chains.


def _sessions(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _chain(session: date, expiry: date, spot: float) -> Chain:
    years = max((expiry - session).days, 1) / 365.0
    rows = []
    for k in range(40, 200):
        for r in ("C", "P"):
            px = bs.price(spot, float(k), years, 0.25, r, 0.0)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(px - 0.02, 0.0), px + 0.02))
    return Chain(rows, session.isoformat(), expiry.isoformat())


class _Rep:
    def __init__(self):
        self.phases, self.ticks = {}, {}

    def phase(self, name, *, total, unit, by):
        self.phases[name] = (total, by)

    def advance(self, name, *, key, current):
        self.ticks[key] = self.ticks.get(key, 0) + 1


def _run(monkeypatch, listed: date, reports):
    monkeypatch.setitem(base.SPEC, "entry_start", "2025-01-02")
    closes = {d: 100.0 for d in _sessions(date(2025, 1, 2), 300)}
    nd = base.NameData("SOFI", False, closes, list(reports), {}, {}, lambda d, e: _chain(d, e, 100.0))
    rep = _Rep()
    block = ctx.context(_Inputs(closes, listed), None, {"SOFI": nd}, rep, lambda s: 0.04)
    return block, rep


def test_the_context_run_starts_at_the_first_stored_session_and_judges_nothing(monkeypatch):
    reports = [date(2024, 10, 30) + timedelta(days=91 * i) for i in range(8)]
    block, rep = _run(monkeypatch, date(2025, 3, 3), reports)
    assert block["verdict"] == "context, not judged" and block["starts"] == {"SOFI": "2025-03-03"}
    assert not {"rule", "half_rule", "arms", "half"} & set(block)
    assert set(block["per_name"]["SOFI"]) == set(base.ARMS)
    assert block["per_name"]["SOFI"]["m45-40"]["rows"] > 0
    assert set(block["expire_vs_half_credit"]["per_name"]) == {"SOFI"}
    # Progress is overall and per symbol, every arm's session counted once.
    total, by = rep.phases["sessions"]
    assert total == by["SOFI"] == rep.ticks["SOFI"] and total > 0


def test_an_unknown_report_quarter_enters_nothing_and_is_counted(monkeypatch):
    block, _ = _run(monkeypatch, date(2025, 3, 3), [])
    arms = block["per_name"]["SOFI"]
    assert all(arms[a]["rows"] == 0 for a in base.ARMS)
    assert block["earnings"]["SOFI"]["sessions_unknown"] == block["earnings"]["SOFI"]["sessions"]


def test_a_name_with_no_stored_chain_is_refused_not_run_on_nothing(monkeypatch):
    monkeypatch.setitem(base.SPEC, "entry_start", "2025-01-02")
    closes = {d: 100.0 for d in _sessions(date(2025, 1, 2), 30)}
    nd = base.NameData("SOFI", False, closes, [], {}, {}, lambda d, e: None)
    with pytest.raises(SystemExit):
        ctx.context(_Inputs({}, date(2025, 1, 2)), None, {"SOFI": nd}, _Rep(), lambda s: 0.04)
