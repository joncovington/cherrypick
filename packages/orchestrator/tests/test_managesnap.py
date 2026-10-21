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
    monkeypatch.setattr(managesnap, "skate_dir", lambda: Path("no-such-skate-dir"))
    monkeypatch.setattr(managesnap, "history_db", lambda: Path("no-such-history.db"))
    # Nor the real home's stream cache.
    monkeypatch.setattr(managesnap, "_cached_greeks", lambda syms: {})
    monkeypatch.setattr(managesnap, "_cached_listing", lambda name: [])
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
    assert snap["snapshot_version"] == 14
    assert snap["underlying_history"] == {} and snap["underlying_history_error"] is None  # no long call
    assert snap["roll_chains"] == {}  # no short strangle held, no chain read
    assert not any("get_option_chain" in c[0] and "GLD" in c[0] for c in calls)
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


GREEKS_POSITIONS = {
    "ok": True,
    "broker_module": "meic",
    "accounts": [
        {
            "account": "****1234",
            "underlyings": [
                {
                    "underlying": "GLD",
                    "legs": [
                        {"symbol": "GLD   261120P00370000", "right": "P"},
                        {"symbol": "GLD   261120P00360000", "right": "P"},
                        {"symbol": "GLD", "right": None},
                    ],
                }
            ],
        }
    ],
}


def test_the_brokers_greeks_come_from_the_cache_first_and_the_feed_for_the_rest(monkeypatch):
    calls = _fake(
        monkeypatch,
        {
            "get_greeks": {
                "ok": True,
                "greeks": {".GLD261120P360": {"delta": -0.2, "theta": -0.05, "gamma": 0.01}},
            }
        },
    )
    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: GREEKS_POSITIONS)
    cached = {".GLD261120P370": {"delta": -0.3, "theta": -0.06, "gamma": 0.02, "iv": 0.2, "vega": 0.3}}
    monkeypatch.setattr(
        managesnap, "_cached_greeks", lambda syms: {s: cached[s] for s in syms if s in cached}
    )
    snap = managesnap.build({})
    got = snap["broker_greeks"]
    assert set(got) == {"GLD   261120P00370000", "GLD   261120P00360000"}  # shares carry none
    assert got["GLD   261120P00370000"]["source"] == "cache" and got["GLD   261120P00370000"]["delta"] == -0.3
    assert got["GLD   261120P00360000"]["source"] == "feed" and got["GLD   261120P00360000"]["theta"] == -0.05
    asked = [c[0] for c in calls if "get_greeks" in c[0]]
    assert len(asked) == 1 and asked[0][asked[0].index("--symbols") + 1 :] == [".GLD261120P360"]
    assert snap["broker_greeks_error"] is None


def test_a_feed_that_cannot_answer_for_greeks_is_recorded_not_fatal(monkeypatch):
    _fake(monkeypatch, {"get_greeks": {"ok": False, "error": "DXLink timeout"}})
    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: GREEKS_POSITIONS)
    snap = managesnap.build({})
    assert snap["broker_greeks"] == {} and "DXLink timeout" in snap["broker_greeks_error"]


def test_the_days_orders_are_kept_without_the_account_or_the_user(monkeypatch):
    order = {
        "id": 77,
        "account_number": "5WT12345",
        "username": "someone",
        "user_id": 9,
        "status": "Live",
        "underlying_symbol": "SLV",
        "order_type": "Limit",
        "time_in_force": "Day",
        "price": "0.45",
        "size": 1,
        "legs": [
            {
                "symbol": "SLV   261120C00055000",
                "action": "Sell to Open",
                "quantity": 1,
                "remaining_quantity": 1,
                "instrument_type": "Equity Option",
                "fills": [{"fill_id": "x"}],
            }
        ],
    }
    _fake(monkeypatch, {"get_working_orders": {"ok": True, "orders": [order]}})
    snap = managesnap.build({})
    (kept,) = snap["working_orders"]
    text = json.dumps(kept)
    assert "5WT12345" not in text and "someone" not in text and "fills" not in text
    assert kept["status"] == "Live" and kept["legs"][0]["action"] == "Sell to Open"
    assert snap["working_orders_error"] is None


def _strangle_positions(*legs):
    return {
        "ok": True,
        "broker_module": "meic",
        "accounts": [{"account": "****1234", "underlyings": [{"underlying": "SLV", "legs": list(legs)}]}],
    }


def test_a_short_strangles_expiry_is_quoted_around_spot(monkeypatch):
    def chain(argv):
        if argv[argv.index("--symbol") + 1] != "SLV":
            return {"ok": False, "error": "not this test"}
        assert argv[argv.index("--expiration") + 1] == "2026-11-20"
        assert argv[argv.index("--around_price") + 1] == "52.4"
        assert argv[argv.index("--strike_count") + 1] == str(managesnap.ROLL_STRIKES)
        row = {"symbol": "SLV 261120P51", "option_type": "P", "strike_price": "51", "bid": 1.0, "ask": 1.1,
               "mid": 1.05, "delta": -0.38}  # fmt: skip
        return {"ok": True, "quotes_complete": True, "chain": {"2026-11-20": [row]}}

    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "SLV", "last": 52.4}]},
            "get_transactions": {"ok": True, "transactions": {}},
            "get_option_chain": chain,
        },
    )
    monkeypatch.setattr(
        managesnap.positions_mod,
        "run",
        lambda cfg: _strangle_positions(
            {"symbol": "SLV   261120P00048000", "right": "P", "quantity": "-1", "expiration": "2026-11-20"},
            {"symbol": "SLV   261120C00055000", "right": "C", "quantity": -1, "expiration": "2026-11-20"},
            # A put spread in another expiry is not a strangle: no chain for it.
            {"symbol": "SLV   261218P00045000", "right": "P", "quantity": -1, "expiration": "2026-12-18"},
            {"symbol": "SLV   261218P00043000", "right": "P", "quantity": 1, "expiration": "2026-12-18"},
        ),
    )
    snap = managesnap.build({})
    assert set(snap["roll_chains"]) == {"SLV|2026-11-20"}
    got = snap["roll_chains"]["SLV|2026-11-20"]
    assert got["error"] is None and got["spot"] == 52.4 and got["quotes_complete"] is True
    assert [(o["strike"], o["right"], o["mid"]) for o in got["options"]] == [(51.0, "P", 1.05)]
    slv_reads = [c for c in calls if "get_option_chain" in c[0] and "SLV" in c[0]]
    assert len(slv_reads) == 1


def test_a_strangle_chain_that_cannot_be_read_is_recorded_not_fatal(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "SLV", "last": 52.4}]},
            "get_transactions": {"ok": True, "transactions": {}},
            "get_option_chain": {"ok": False, "error": "HTTP 504"},
        },
    )
    monkeypatch.setattr(
        managesnap.positions_mod,
        "run",
        lambda cfg: _strangle_positions(
            {"symbol": "SLV   261120P00048000", "right": "P", "quantity": -1, "expiration": "2026-11-20"},
            {"symbol": "SLV   261120C00055000", "right": "C", "quantity": -1, "expiration": "2026-11-20"},
        ),
    )
    got = managesnap.build({})["roll_chains"]["SLV|2026-11-20"]
    assert got["options"] == [] and "HTTP 504" in got["error"]


def test_an_orders_read_that_fails_is_recorded_not_fatal(monkeypatch):
    _fake(monkeypatch, {"get_working_orders": {"ok": False, "error": "HTTP 502"}})
    snap = managesnap.build({})
    assert snap["working_orders"] == [] and "HTTP 502" in snap["working_orders_error"]


# --------------------------------------------------------------------------- skate bands (v12)
def _band(level, skate=(0.5, 0.9), recent=(0.4, 0.8)):
    return {
        "haircut": 0.75,
        "band": level,
        "n": 2900,
        "n_recent": 740,
        "skate": list(skate),
        "skate_recent": list(recent),
    }


def _skate_row(side, expiry, fixed, by_expiry):
    return {
        "side": side,
        "expiry": expiry,
        "days": 32,
        "sessions": 23,
        "range": 4.2,
        "same_band": False,
        "bands": {"fixed": _band(fixed), "by_expiry": _band(by_expiry)},
    }


def _skate_name(spot, *rows):
    return {"spot": spot, "fund": False, "earnings": "2026-11-05", "div_yield": 0.01, "rows": list(rows)}


def _skate_file(folder, day, names):
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "day": day,
        "built_at": f"{day}T06:46:00-0400",
        "earnings_reading": {},
        "config": {"haircut": 0.75, "history_from": "2015-01-01", "recent_years": 3, "soft": {"skate": 0.84}},
        "offsets": [0.0, 0.01],
        "universe": 900,
        "skipped": {"earnings date unknown": ["ZZZ"]},
        "names": names,
    }
    (folder / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")


XYZ_BANDS = _skate_name(
    50.0, _skate_row("P", "2026-11-20", 47.0, 48.5), _skate_row("C", "2026-11-20", 53.0, 52.5)
)


def test_the_snapshot_carries_only_the_held_names_bands_and_only_what_manage_reads(tmp_path):
    _skate_file(tmp_path, "2026-10-15", {"XYZ": _skate_name(1.0)})  # older: not read
    _skate_file(tmp_path, "2026-10-16", {"XYZ": XYZ_BANDS, "AAPL": XYZ_BANDS, "BRK.B": XYZ_BANDS})
    _skate_file(tmp_path, "2026-10-20", {"XYZ": _skate_name(2.0)})  # after the session: not yet
    got, error = managesnap.skate_bands(date(2026, 10, 19), ["BRK/B", "QQQ", "XYZ"], tmp_path)
    assert error is None
    assert got["day"] == "2026-10-16" and got["sessions_behind"] == 1
    assert got["offsets"] == [0.0, 0.01]
    assert got["config"] == {"history_from": "2015-01-01", "recent_years": 3, "soft": {"skate": 0.84}}
    # AAPL is not held; BRK.B is the file's spelling of the broker's BRK/B; QQQ is held and unbanded.
    assert set(got["names"]) == {"BRK/B", "XYZ"} and got["not_banded"] == ["QQQ"]
    xyz = got["names"]["XYZ"]
    assert set(xyz) == {"spot", "rows"} and xyz["spot"] == 50.0
    put = xyz["rows"][0]
    assert put == {
        "side": "P",
        "expiry": "2026-11-20",
        "days": 32,
        "sessions": 23,
        "bands": {"fixed": _band(47.0), "by_expiry": _band(48.5)},
    }


def test_a_missing_band_file_is_recorded_not_fatal(monkeypatch, tmp_path):
    got, error = managesnap.skate_bands(date(2026, 10, 19), ["XYZ"], tmp_path / "none")
    assert got is None and "no skate band file" in error
    _fake(monkeypatch, {})
    snap = managesnap.build({})
    assert snap["skate_bands"] is None and "no skate band file" in snap["skate_bands_error"]


def test_a_stale_band_file_is_refused(tmp_path):
    # Wednesday 10-14 is three sessions behind Monday 10-19 (Thu, Fri, Mon): still read.
    _skate_file(tmp_path, "2026-10-14", {"XYZ": XYZ_BANDS})
    got, error = managesnap.skate_bands(date(2026, 10, 19), ["XYZ"], tmp_path)
    assert error is None and got["sessions_behind"] == 3
    # The same file on Tuesday 10-20 is four behind: refused, and said why.
    got, error = managesnap.skate_bands(date(2026, 10, 20), ["XYZ"], tmp_path)
    assert got is None and "4 sessions old" in error


def test_an_unreadable_band_file_is_recorded_not_fatal(tmp_path):
    tmp_path.joinpath("2026-10-16.json").write_text("{not json", encoding="utf-8")
    got, error = managesnap.skate_bands(date(2026, 10, 19), ["XYZ"], tmp_path)
    assert got is None and "unreadable" in error


def test_build_copies_the_held_names_bands(monkeypatch, tmp_path):
    _fake(monkeypatch, {"get_market_overview": {"ok": True, "metrics": [{"symbol": "GLD", "last": 381.65}]}})
    _skate_file(tmp_path, "2026-10-16", {"GLD": XYZ_BANDS, "AAPL": XYZ_BANDS})
    monkeypatch.setattr(managesnap, "skate_dir", lambda: tmp_path)
    monkeypatch.setattr(managesnap.clock, "today_iso", lambda: "2026-10-19")
    snap = managesnap.build({})
    assert snap["skate_bands_error"] is None and set(snap["skate_bands"]["names"]) == {"GLD"}


def test_the_wheel_check_reads_the_copied_band_moved_to_todays_price(tmp_path):
    """The contract with manage: what the snapshot copies is what the wheel view's band check reads.
    The band file is Friday's at a 50.00 close; on Monday XYZ is at 51.00, so every band moves up a
    dollar (manage moves it, `wheel._band`). A short put at 47.50 is past the moved 48.00 fixed band
    (it would sit inside the unmoved 47.00); one at 48.50 is inside it and past the 49.50 by-expiry band."""
    import pytest

    wheel = pytest.importorskip("cherrypick.manage.wheel")
    _skate_file(tmp_path, "2026-10-16", {"XYZ": XYZ_BANDS})
    bands, error = managesnap.skate_bands(date(2026, 10, 19), ["XYZ"], tmp_path)
    assert error is None

    def short_put(strike):
        sym = f"XYZ   261120P{int(strike * 1000):08d}"
        row = {
            "id": f"1-{sym}",
            "order_id": 1,
            "executed_at": "2026-10-15T15:00:00Z",
            "transaction_type": "Trade",
            "transaction_sub_type": "Sell to Open",
            "action": "Sell to Open",
            "instrument_type": "Equity Option",
            "symbol": sym,
            "underlying_symbol": "XYZ",
            "quantity": "1",
            "price": "1.0",
            "value": "100.0",
            "commission": "-1.0",
            "clearing_fees": "-0.1",
            "regulatory_fees": "0.0",
            "proprietary_index_option_fees": "0.0",
        }
        leg = {
            "symbol": sym,
            "underlying": "XYZ",
            "instrument_type": "Equity Option",
            "quantity": -1,
            "expiration": "2026-11-20",
            "right": "P",
            "strike": strike,
            "open_price": 1.0,
            "mid": 0.8,
            "priced": True,
        }
        view = wheel.build(
            [{"underlying": "XYZ", "legs": [leg]}],
            {"XYZ": [row]},
            date(2026, 10, 19),
            {"XYZ": {"last": 51.0}},
            [],
            bands,
        )
        (cycle,) = view["cycles"]
        (today,) = cycle["today"]
        return today

    outside = short_put(47.5)
    assert outside["band_missing"] is None
    assert outside["band"]["fixed"] == pytest.approx(48.0) and outside["band"]["by_expiry"] == pytest.approx(
        49.5
    )
    assert outside["beyond_band"] == {"fixed": True, "by_expiry": True}
    inside = short_put(48.5)
    assert inside["beyond_band"] == {"fixed": False, "by_expiry": True}


# --------------------------------------------------------------------------- underlying history (v13)
def _store(path, bars=(), dividends=(), splits=()):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL);"
        "CREATE TABLE dividends (symbol TEXT, ex_date TEXT, amount REAL);"
        "CREATE TABLE splits (symbol TEXT, ex_date TEXT, to_factor REAL, for_factor REAL);"
    )
    conn.executemany("INSERT INTO bars VALUES (?, ?, 0, 0, 0, ?, 0)", bars)
    conn.executemany("INSERT INTO dividends VALUES (?, ?, ?)", dividends)
    conn.executemany("INSERT INTO splits VALUES (?, ?, 2, 1)", splits)
    conn.commit()
    conn.close()
    return path


LONG = "XYZ   290119C00040000"
SHORT = "XYZ   261120C00050000"
HELD = {
    "accounts": [
        {
            "underlyings": [
                {
                    "underlying": "XYZ",
                    "legs": [
                        {"symbol": LONG, "right": "C", "quantity": 1.0},
                        {"symbol": SHORT, "right": "C", "quantity": -1.0},
                    ],
                },
                {
                    "underlying": "ABC",
                    "legs": [{"symbol": "ABC   261120P00010000", "right": "P", "quantity": 1.0}],
                },
            ]
        }
    ]
}
OPENED = {
    "XYZ": [
        {"action": "Buy to Open", "symbol": LONG, "executed_at": "2026-09-25T19:50:00Z"},
        # The short was once held long (bought 09-24, sold), then sold to open: it is no long call now.
        {"action": "Buy to Open", "symbol": SHORT, "executed_at": "2026-09-24T15:00:00Z"},
        {"action": "Sell to Close", "symbol": SHORT, "executed_at": "2026-09-24T16:00:00Z"},
        {"action": "Sell to Open", "symbol": SHORT, "executed_at": "2026-09-26T15:00:00Z"},
        {"action": "Buy to Open", "symbol": "XYZ   261016C00045000", "executed_at": "2026-09-01T15:00:00Z"},
    ]
}


def test_the_long_call_s_entry_close_dividends_and_splits_are_copied(tmp_path):
    db = _store(
        tmp_path / "history.db",
        bars=[
            ("XYZ", "2026-09-01", 47.0),
            ("XYZ", "2026-09-24", 49.0),
            ("XYZ", "2026-09-25", 50.0),
            ("XYZ", "2026-09-26", 50.5),
            ("XYZ", "2026-10-19", 52.0),
        ],
        dividends=[
            ("XYZ", "2025-09-01", 0.40),  # more than a year before the entry: not copied
            ("XYZ", "2025-12-01", 0.50),
            ("XYZ", "2026-09-01", 0.50),
            ("ABC", "2026-09-01", 9.99),
        ],
        splits=[("XYZ", "2020-01-02"), ("XYZ", "2026-10-01")],
    )
    got, error = managesnap.underlying_history(date(2026, 10, 19), HELD, OPENED, db)
    assert error is None
    # Only the held long call's opening day: not the short's, and not a call no longer held.
    assert got == {
        "XYZ": {
            "store_symbol": "XYZ",
            "through": "2026-10-19",
            "closes": {"2026-09-25": 50.0},
            "dividends": [["2025-12-01", 0.5], ["2026-09-01", 0.5]],
            "splits": ["2026-10-01"],
        }
    }


def test_a_long_bought_before_the_store_s_last_bar_has_no_close_and_says_nothing_else(tmp_path):
    db = _store(tmp_path / "history.db", bars=[("XYZ", "2026-09-24", 49.0)])
    got, error = managesnap.underlying_history(date(2026, 10, 19), HELD, OPENED, db)
    assert error is None and got["XYZ"]["closes"] == {} and got["XYZ"]["dividends"] == []


def test_a_missing_store_is_recorded_not_fatal(tmp_path):
    got, error = managesnap.underlying_history(date(2026, 10, 19), HELD, OPENED, tmp_path / "none.db")
    assert got is None and "no history store" in error
    # No long call held: nothing is wanted, so nothing is read and nothing is wrong.
    got, error = managesnap.underlying_history(
        date(2026, 10, 19), {"accounts": []}, OPENED, tmp_path / "none.db"
    )
    assert got == {} and error is None


def test_a_name_only_the_history_store_holds_is_resolved(monkeypatch, tmp_path):
    """The end-of-day store holds only the market report's names; the history store holds every
    tradable one. With both on disk under the technicals home, a name only the history store has (as
    IVV, FXI and BRK.B were on 2026-10-19) still gets its close, and BRK/B finds the store's BRK.B."""
    monkeypatch.setenv("TECHNICALS_DATA_DIR", str(tmp_path))
    _store(tmp_path / "eod.db", bars=[("SPY", "2026-09-25", 650.0)])
    _store(
        tmp_path / "history.db",
        bars=[("XYZ", "2026-09-25", 50.0), ("BRK.B", "2026-09-25", 480.0)],
        dividends=[("XYZ", "2026-09-01", 0.50)],
    )
    brk = "BRK/B 290119C00400000"
    held = {
        "accounts": [
            {
                "underlyings": [
                    *HELD["accounts"][0]["underlyings"],
                    {"underlying": "BRK/B", "legs": [{"symbol": brk, "right": "C", "quantity": 1.0}]},
                ]
            }
        ]
    }
    opened = {
        **OPENED,
        "BRK/B": [{"action": "Buy to Open", "symbol": brk, "executed_at": "2026-09-25T15:00:00Z"}],
    }
    assert managesnap.history_db() == tmp_path / "history.db"
    got, error = managesnap.underlying_history(date(2026, 10, 19), held, opened)
    assert error is None
    assert got["XYZ"]["closes"] == {"2026-09-25": 50.0} and got["XYZ"]["dividends"] == [["2026-09-01", 0.5]]
    assert got["BRK/B"]["store_symbol"] == "BRK.B" and got["BRK/B"]["closes"] == {"2026-09-25": 480.0}


# --------------------------------------------------------------------------- roll-out chains (v14)
def _leg(occ, right, qty, exp, strike):
    return {"symbol": occ, "right": right, "quantity": qty, "expiration": exp, "strike": strike}


ROLL_OUT_HELD = {
    "ok": True,
    "broker_module": "meic",
    "accounts": [
        {
            "account": "****1234",
            "underlyings": [
                # A short put with no long put on the name: rolled out within 60 days of its expiry.
                {"underlying": "SOFI", "legs": [_leg("SOFI  261120P00016000", "P", -2, "2026-11-20", 16.0)]},
                # A PMCC's shape: rolled out before the long's expiry.
                {
                    "underlying": "QQQ",
                    "legs": [
                        _leg("QQQ   270617C00400000", "C", 1, "2027-06-17", 400.0),
                        _leg("QQQ   261106C00520000", "C", -1, "2026-11-06", 520.0),
                    ],
                },
                # A covered call: 100 shares behind one short call.
                {
                    "underlying": "KO",
                    "legs": [
                        {"symbol": "KO", "right": None, "quantity": 100, "instrument_type": "Equity"},
                        _leg("KO    261120C00070000", "C", -1, "2026-11-20", 70.0),
                    ],
                },
                # None of these: a naked call, a put spread, and a PMCC short too far out to roll yet.
                {"underlying": "XLE", "legs": [_leg("XLE   261120C00095000", "C", -1, "2026-11-20", 95.0)]},
                {
                    "underlying": "IWM",
                    "legs": [
                        _leg("IWM   261218P00250000", "P", -1, "2026-12-18", 250.0),
                        _leg("IWM   261218P00240000", "P", 1, "2026-12-18", 240.0),
                    ],
                },
                {
                    "underlying": "TLT",
                    "legs": [
                        _leg("TLT   290119C00080000", "C", 1, "2029-01-19", 80.0),
                        _leg("TLT   270115C00085000", "C", -1, "2027-01-15", 85.0),
                    ],
                },
            ],
        }
    ],
}

LISTED = {
    "SOFI": [
        "2026-10-30",
        "2026-11-20",
        "2026-11-27",
        "2026-12-04",
        "2026-12-18",
        "2027-01-15",
        "2027-01-22",
    ],
    "QQQ": [
        "2026-11-06",
        "2026-11-13",
        "2026-11-20",
        "2026-11-27",
        "2026-12-04",
        "2026-12-11",
        "2026-12-18",
        "2027-06-17",
    ],  # fmt: skip
    "KO": ["2026-11-20", "2026-12-18", "2027-01-15", "2027-02-19"],
}


def _roll_out_chain(failing=()):
    """A fake chain read: a listing (no --expiration) from LISTED, or one quoted slice with a put and
    a call at the centre strike. An expiry in `failing` answers an error."""

    def chain(argv):
        name = argv[argv.index("--symbol") + 1]
        if "--expiration" not in argv:
            if name not in LISTED:
                return {"ok": False, "error": f"no listing for {name}"}
            return {"ok": True, "chain": {e: [] for e in LISTED[name]}}
        exp = argv[argv.index("--expiration") + 1]
        if (name, exp) in failing:
            return {"ok": False, "error": "HTTP 504"}
        k = argv[argv.index("--around_price") + 1]
        rows = [
            {"symbol": f"{name} {exp} C", "option_type": "C", "strike_price": k, "bid": 1.0, "ask": 1.2,
             "mid": 1.1, "delta": 0.35, "iv": 0.3},
            {"symbol": f"{name} {exp} P", "option_type": "P", "strike_price": k, "bid": 0.9, "ask": 1.0,
             "mid": 0.95, "delta": -0.4, "iv": 0.3},
        ]  # fmt: skip
        return {"ok": True, "quotes_complete": True, "chain": {exp: rows}}

    return chain


def _roll_out_build(monkeypatch, cfg=None, failing=(), listing=None):
    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "QQQ", "last": 600.0}]},
            "get_transactions": {"ok": True, "transactions": {}},
            "get_option_chain": listing or _roll_out_chain(failing),
        },
    )
    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: ROLL_OUT_HELD)
    monkeypatch.setattr(managesnap.clock, "today_iso", lambda: "2026-10-20")
    snap = managesnap.build(cfg or {})
    reads = [
        (
            c[0][c[0].index("--symbol") + 1],
            c[0][c[0].index("--expiration") + 1] if "--expiration" in c[0] else None,
        )
        for c in calls
        if "get_option_chain" in c[0] and "XSP" not in c[0]
    ]
    return snap, reads, calls


def test_each_short_gets_the_next_listed_expiries_within_its_bound(monkeypatch):
    snap, reads, calls = _roll_out_build(monkeypatch)
    ro = snap["roll_out_chains"]
    assert sorted({(a["underlying"], a["right"]) for a in ro["anchors"]}) == [
        ("KO", "C"),
        ("QQQ", "C"),
        ("SOFI", "P"),
    ]
    slices = sorted(r for r in reads if r[1])
    # The short put: every listed expiry after its own and within 60 days of it (four, under the five).
    sofi = [e for n, e in slices if n == "SOFI"]
    assert sofi == ["2026-11-27", "2026-12-04", "2026-12-18", "2027-01-15"]
    # The PMCC: the next five after the short, all before the long.
    assert [e for n, e in slices if n == "QQQ"] == [
        "2026-11-13",
        "2026-11-20",
        "2026-11-27",
        "2026-12-04",
        "2026-12-11",
    ]
    # The covered call: monthly-only, so two inside 60 days of the short.
    assert [e for n, e in slices if n == "KO"] == ["2026-12-18", "2027-01-15"]
    assert not {n for n, _ in reads} & {"XLE", "IWM", "TLT"}
    # One listing per name, one read per expiry, and every read counted.
    assert sorted(n for n, e in reads if e is None) == ["KO", "QQQ", "SOFI"]
    assert ro["calls"] == len(reads) == 3 + 4 + 5 + 2 and ro["skipped"] == []
    # Each read centred on the short's strike, as wide as a strangle's, and kept to the short's right.
    for argv, _ in calls:
        if "get_option_chain" in argv and "--expiration" in argv and "QQQ" in argv:
            assert argv[argv.index("--around_price") + 1] == "520.0"
            assert argv[argv.index("--strike_count") + 1] == str(managesnap.ROLL_STRIKES)
            assert "--include_quotes" in argv and "--include_greeks" in argv
    got = ro["chains"]["SOFI|P|2026-12-18"]
    assert got["error"] is None and got["centre"] == 16.0
    assert [(o["strike"], o["right"], o["mid"], o["broker_delta"]) for o in got["options"]] == [
        (16.0, "P", 0.95, -0.4)
    ]


def test_a_pmcc_s_rolls_stop_before_its_long(monkeypatch):
    held = {
        "accounts": [
            {
                "underlyings": [
                    {
                        "underlying": "QQQ",
                        "legs": [
                            _leg("QQQ   261204C00500000", "C", 1, "2026-12-04", 500.0),
                            _leg("QQQ   261106C00520000", "C", -1, "2026-11-06", 520.0),
                        ],
                    }
                ]
            }
        ]
    }
    (anchor,) = managesnap.roll_out_anchors(held, date(2026, 10, 20), 60)
    assert anchor["before"] == "2026-12-04"
    monkeypatch.setattr(managesnap, "_cached_listing", lambda name: LISTED[name])
    calls = []

    def answer(cfg, prefer, argv, timeout=35):
        calls.append(argv)
        return {"ok": True, "chain": {argv[argv.index("--expiration") + 1]: []}}, None

    monkeypatch.setattr(managesnap, "_first_answer", answer)
    ro = managesnap.roll_out_chains(
        {}, held, date(2026, 10, 20), None, dict(managesnap.cfgmod.ROLL_OUT_DEFAULTS)
    )
    # The listing came from the stream cache: no listing read, and nothing on or after the long's expiry.
    assert all("--expiration" in a for a in calls)
    assert [a[a.index("--expiration") + 1] for a in calls] == ["2026-11-13", "2026-11-20", "2026-11-27"]
    assert ro["listings"]["QQQ"]["source"] == "stream cache" and ro["calls"] == 3


def test_the_broker_reads_are_capped_and_what_the_cap_cuts_is_said(monkeypatch):
    snap, reads, _ = _roll_out_build(monkeypatch, {"manage": {"roll_out": {"max_calls": 4}}})
    ro = snap["roll_out_chains"]
    assert ro["calls"] == len(reads) == 4
    # QQQ's short expires first, so it is read first: its listing and three of its five expiries.
    assert reads == [("QQQ", None), ("QQQ", "2026-11-13"), ("QQQ", "2026-11-20"), ("QQQ", "2026-11-27")]
    cut = {(s["underlying"], s.get("expiration")) for s in ro["skipped"]}
    assert ("QQQ", "2026-12-04") in cut and ("SOFI", None) in cut and ("KO", None) in cut
    assert all("over the cap of 4" in s["why"] for s in ro["skipped"])


def test_a_failed_or_timed_out_roll_out_read_is_recorded_not_fatal(monkeypatch):
    snap, _, _ = _roll_out_build(monkeypatch, failing={("SOFI", "2026-12-04")})
    ro = snap["roll_out_chains"]
    assert "HTTP 504" in ro["chains"]["SOFI|P|2026-12-04"]["error"]
    assert ro["chains"]["SOFI|P|2026-12-04"]["options"] == []
    assert ro["chains"]["SOFI|P|2026-12-18"]["error"] is None  # the next one is still read

    def slow(argv):
        if "--expiration" not in argv:
            raise subprocess.TimeoutExpired(["tt"], 35)
        return _roll_out_chain()(argv)

    snap, reads, _ = _roll_out_build(monkeypatch, listing=slow)
    ro = snap["roll_out_chains"]
    assert snap["positions"]["ok"] and ro["chains"] == {}
    assert "timed out" in ro["listings"]["SOFI"]["error"]
    assert all(a["wanted"] == [] and "no listed expiry" in a["why"] for a in ro["anchors"])
