"""The position evaluation's snapshot: positions, market metrics and trade history, all reads."""

import json
import subprocess
from datetime import date
from pathlib import Path

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import managesnap

POSITIONS = {
    "ok": True,
    "broker_module": "meic",
    "accounts": [{"account": "****1234", "underlyings": [{"underlying": "GLD", "legs": []}]}],
}


def _fake(monkeypatch, answers):
    calls = []

    def run(root, argv, timeout=30):
        calls.append((list(argv), timeout))
        verb = next(a for a in argv if a.startswith("get_"))
        answer = answers.get(verb, {"ok": False, "error": f"{verb} not faked"})
        if callable(answer):
            answer = answer(list(argv))
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: POSITIONS)
    monkeypatch.setattr(managesnap.doctor, "_run", run)
    # Never the real home's income readings.
    monkeypatch.setattr(managesnap, "income_dir", lambda: Path("no-such-income-dir"))
    monkeypatch.setattr(managesnap, "earnings_times_dir", lambda: Path("no-such-times-dir"))
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: {"meic": {"path": "."}})
    monkeypatch.setattr(cfgmod, "module_root", lambda mcfg, name: ".")
    monkeypatch.setattr(cfgmod, "broker_tool", lambda mcfg, name: ["-m", "cherrypick.meic.tt"])
    return calls


def test_the_snapshot_carries_each_underlyings_trade_history(monkeypatch):
    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "GLD", "last": 381.65}]},
            "get_transactions": {"ok": True, "transactions": {"GLD": [{"order_id": 1}]}},
        },
    )
    snap = managesnap.build({})
    assert snap["snapshot_version"] == 8
    assert snap["transactions"] == {"GLD": [{"order_id": 1}]}
    assert snap["transactions_error"] is None
    (tx_argv, tx_timeout) = next(c for c in calls if "get_transactions" in c[0])
    assert tx_argv[tx_argv.index("--underlyings") + 1] == "GLD"
    assert "--since" in tx_argv and tx_timeout > 35  # a year of history outlasts the quote timeout


def test_a_failed_history_read_is_recorded_not_fatal(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": []},
            "get_transactions": {"ok": False, "error": "HTTP 503"},
        },
    )
    snap = managesnap.build({})
    assert snap["positions"]["ok"]
    assert snap["transactions"] == {}
    assert "HTTP 503" in snap["transactions_error"]


def test_the_benchmark_is_quoted_with_each_beta_but_its_history_is_not_read(monkeypatch):
    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {
                "ok": True,
                "metrics": [
                    {"symbol": "GLD", "last": 381.65, "beta": "0.12", "corr_spy_3month": "0.2"},
                    {
                        "symbol": "SPY",
                        "last": 667.1,
                        "beta": "1.0",
                        "implied_volatility_index": "0.196",
                        "implied_volatility_30_day": "19.61",
                        "historical_volatility_30_day": "10.45",
                    },
                ],
            },
            "get_transactions": {"ok": True, "transactions": {}},
        },
    )
    snap = managesnap.build({})
    assert snap["benchmark"] == "SPY"
    assert snap["metrics"]["GLD"]["beta"] == "0.12"
    assert snap["metrics"]["GLD"]["corr_spy_3month"] == "0.2"
    assert snap["metrics"]["SPY"]["last"] == 667.1
    # Each kept in the broker's own unit, under a name that says which.
    assert snap["metrics"]["SPY"]["iv_index"] == "0.196"
    assert snap["metrics"]["SPY"]["hv_30_day_pct"] == "10.45"
    (mo_argv, _) = next(c for c in calls if "get_market_overview" in c[0])
    assert {"GLD", "SPY"} <= set(mo_argv)  # SPY is not held, and is quoted anyway
    (tx_argv, _) = next(c for c in calls if "get_transactions" in c[0])
    assert "SPY" not in tx_argv


def test_a_missing_beta_is_null_never_zero(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "GLD", "last": 381.65}]},
            "get_transactions": {"ok": True, "transactions": {}},
        },
    )
    assert managesnap.build({})["metrics"]["GLD"]["beta"] is None


def test_a_timed_out_read_is_recorded_not_fatal(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": subprocess.TimeoutExpired(["tt"], 35),
            "get_transactions": {"ok": True, "transactions": {}},
        },
    )
    snap = managesnap.build({})
    assert snap["metrics"] == {}
    assert "timed out after 35s" in snap["metrics_error"]
    assert snap["hedge_chain"]["error"]  # no XSP price to centre on, said so


def _listing(*expiries):
    return {e: [{"expiration_type": kind, "option_type": "P", "strike_price": "760"}] for e, kind in expiries}


def test_the_hedge_expiry_is_the_monthly_preferring_45_to_60_days():
    session = date(2026, 10, 19)
    listing = _listing(("2026-11-20", "Regular"), ("2026-12-04", "Weekly"), ("2026-12-18", "Regular"))
    assert managesnap.pick_hedge_expiry(listing, session) == "2026-12-18"
    # No monthly at 45-60: the 30-44 one beats a weekly nearer 45.
    listing = _listing(("2026-11-20", "Regular"), ("2026-12-04", "Weekly"), ("2027-01-15", "Regular"))
    assert managesnap.pick_hedge_expiry(listing, session) == "2026-11-20"
    # Only weeklies in the window: the one nearest 45 days, at or past it first.
    listing = _listing(("2026-11-27", "Weekly"), ("2026-12-04", "Weekly"), ("2027-01-15", "Regular"))
    assert managesnap.pick_hedge_expiry(listing, session) == "2026-12-04"
    assert managesnap.pick_hedge_expiry(_listing(("2027-01-15", "Regular")), session) is None


def test_the_snapshot_carries_one_quoted_xsp_expiry(monkeypatch):
    slice_ = [
        {
            "symbol": "XSP 261218P760",
            "option_type": "P",
            "strike_price": "760.0",
            "bid": 14.17,
            "ask": 15.46,
            "mid": 14.815,
            "delta": -0.40,
            "iv": 0.155,
            "settlement_type": "PM",
            "exercise_style": "European",
        },
        {
            "symbol": "XSP 261218C790",
            "option_type": "C",
            "strike_price": "790.0",
            "bid": 6.1,
            "ask": 6.5,
            "mid": 6.3,
            "delta": 0.30,
            "iv": 0.13,
        },
    ]

    def chain(argv):
        if "--expiration" not in argv:
            return {"ok": True, "chain": _listing(("2026-11-20", "Regular"), ("2026-12-18", "Regular"))}
        assert argv[argv.index("--expiration") + 1] == "2026-12-18"
        assert argv[argv.index("--around_price") + 1] == "765.94"
        return {"ok": True, "quotes_complete": True, "chain": {"2026-12-18": slice_}}

    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {
                "ok": True,
                "metrics": [{"symbol": "XSP", "last": 765.94, "beta": "0.9996"}],
            },
            "get_transactions": {"ok": True, "transactions": {}},
            "get_option_chain": chain,
        },
    )
    monkeypatch.setattr(managesnap.clock, "today_iso", lambda: "2026-10-19")
    snap = managesnap.build({})
    hedge = snap["hedge_chain"]
    assert hedge["expiration"] == "2026-12-18" and hedge["dte"] == 60 and hedge["error"] is None
    assert hedge["exercise_style"] == "European"
    assert [(o["strike"], o["right"], o["mid"]) for o in hedge["options"]] == [
        (760.0, "P", 14.815),
        (790.0, "C", 6.3),
    ]
    (mo_argv, _) = next(c for c in calls if "get_market_overview" in c[0])
    assert "XSP" in mo_argv and "VIX" in mo_argv
    (tx_argv, _) = next(c for c in calls if "get_transactions" in c[0])
    assert "XSP" not in tx_argv


def _income_file(folder, day, **body):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(
        json.dumps({"session": day, "symbols": {"AA": {}}, **body}), encoding="utf-8"
    )


def test_the_income_reading_is_the_newest_on_or_before_the_session(tmp_path):
    _income_file(tmp_path, "2026-10-15")
    _income_file(tmp_path, "2026-10-16")
    _income_file(tmp_path, "2026-10-20")  # after the session: not yet
    (tmp_path / "2026-10-19.trial.json").write_text("{}", encoding="utf-8")  # a test run, never read
    got = managesnap.income_reading(date(2026, 10, 19), tmp_path)
    assert got["session"] == "2026-10-16" and got["error"] is None


def test_a_stale_or_missing_income_reading_is_said_not_used(tmp_path):
    _income_file(tmp_path, "2026-10-10")
    stale = managesnap.income_reading(date(2026, 10, 19), tmp_path)
    assert "too old" in stale["error"] and "symbols" not in stale
    assert "no income reading" in managesnap.income_reading(date(2026, 10, 19), tmp_path / "none")["error"]


def _times_file(folder, day, dates):
    (folder / f"{day}.json").write_text(json.dumps({"fetched_at": day, "dates": dates}), encoding="utf-8")


def test_an_untimed_report_takes_the_timings_files_time_for_the_same_date(tmp_path):
    _times_file(tmp_path, "2026-10-10", {"2026-10-19": {"CLF": "amc"}})  # older: not read
    _times_file(
        tmp_path, "2026-10-17", {"2026-10-19": {"CLF": "bmo"}, "2026-10-20": {"NFLX": "amc", "RTX": "bmo"}}
    )
    _times_file(tmp_path, "2026-10-19", {"2026-10-19": {"CLF": "amc"}})  # after the session: not yet
    times = managesnap.earnings_times(date(2026, 10, 18), tmp_path)
    metrics = {
        "CLF": {"earnings_date": "2026-10-19", "earnings_time": None},
        "NFLX": {"earnings_date": "2026-10-20", "earnings_time": None},
        "RTX": {"earnings_date": "2026-10-27", "earnings_time": None},  # a different date: left blank
        "UAL": {"earnings_date": "2026-10-20", "earnings_time": "AMC"},  # the broker's own time stands
        "SPY": {"earnings_date": None, "earnings_time": None},
    }
    managesnap.fill_earnings_times(metrics, times)
    assert metrics["CLF"] == {
        "earnings_date": "2026-10-19",
        "earnings_time": "BTO",
        "earnings_time_source": "nasdaq",
    }
    assert metrics["NFLX"]["earnings_time"] == "AMC"
    assert metrics["RTX"]["earnings_time"] is None and "earnings_time_source" not in metrics["RTX"]
    assert "earnings_time_source" not in metrics["UAL"]
    assert metrics["SPY"]["earnings_time"] is None


def test_a_stale_or_missing_timings_file_fills_nothing(tmp_path):
    _times_file(tmp_path, "2026-10-01", {"2026-10-19": {"CLF": "bmo"}})
    assert managesnap.earnings_times(date(2026, 10, 18), tmp_path) == {}
    assert managesnap.earnings_times(date(2026, 10, 18), tmp_path / "none") == {}
