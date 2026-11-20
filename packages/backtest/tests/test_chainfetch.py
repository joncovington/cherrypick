"""The end-of-day chain fetch: exactly the requests asked for, each wholly written or not at all,
resumable, a refusal recorded and retried, a fatal one stopping the run. Synthetic rows only."""

from __future__ import annotations

import json
import sqlite3
import threading

from cherrypick.backtest import chainfetch, paths, settings
from cherrypick.backtest.vendor import VendorRefusal


def _settings(tmp_path):
    return settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})


def _rows(n=3):
    return [
        {"strike": 100.0 + k, "right": r, "bid": 1.0, "ask": 1.1, "close": 1.05, "volume": 10.0,
         "bid_size": 5.0, "ask_size": 6.0}
        for k in range(n)
        for r in ("CALL", "PUT")
    ]  # fmt: skip


class FakeChains:
    def __init__(self, empty=(), refuse=(), fatal_at=None):
        self.empty, self.refuse, self.fatal_at = set(empty), set(refuse), fatal_at
        self.calls = []
        self.lock = threading.Lock()

    def eod_chain(self, root, day, expiration):
        with self.lock:
            self.calls.append((root, day, expiration))
            if self.fatal_at is not None and len(self.calls) > self.fatal_at:
                raise VendorRefusal("unauthenticated", "another login took the session")
        if (root, day) in self.refuse:
            raise VendorRefusal("permission_denied", "not on this tier")
        if (root, day) in getattr(self, "no_data", set()):
            raise VendorRefusal("no_data", "No data found")
        return [] if (root, day) in self.empty else _rows()


REQS = [
    ("AAPL", "2024-02-02", "2024-03-15"),
    ("DAL", "2024-02-02", "2024-03-15"),
    ("MSFT", "2024-02-05", "2024-03-15"),
]


_SETTINGS_DIR = {}


def _settings_for():
    import tempfile

    d = _SETTINGS_DIR.setdefault("d", tempfile.mkdtemp(prefix="chainfetch-"))
    return settings.resolve({"store": {"dir": d + "/vendor"}})


def _db(s):
    return sqlite3.connect(chainfetch.db_path(s))


def test_the_store_lives_in_the_marked_licensed_directory(tmp_path):
    s = _settings(tmp_path)
    chainfetch.fetch_chains(s, FakeChains(), REQS[:1], log=lambda m: None)
    assert chainfetch.db_path(s).parent == paths.vendor_dir(s)
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()


def test_it_fetches_each_request_once_and_records_what_became_of_it(tmp_path):
    s = _settings(tmp_path)
    fake = FakeChains(empty={("DAL", "2024-02-02")}, refuse={("MSFT", "2024-02-05")})
    out = chainfetch.fetch_chains(s, fake, REQS, workers=3, log=lambda m: None)
    assert (out["complete"], out["empty"], out["refused"], out["rows"]) == (1, 1, 1, 6)
    with _db(s) as c:
        status = dict(((sym, st) for sym, st in c.execute("SELECT symbol, status FROM eod_chain_requests")))
        assert status == {"AAPL": "complete", "DAL": "empty", "MSFT": "refused"}
        assert c.execute("SELECT COUNT(*) FROM eod_chain_quotes WHERE symbol = 'AAPL'").fetchone()[0] == 6


def test_a_rerun_skips_complete_and_empty_and_retries_refused(tmp_path):
    s = _settings(tmp_path)
    first = FakeChains(empty={("DAL", "2024-02-02")}, refuse={("MSFT", "2024-02-05")})
    chainfetch.fetch_chains(s, first, REQS, log=lambda m: None)
    again = FakeChains()
    out = chainfetch.fetch_chains(s, again, REQS, log=lambda m: None)
    assert again.calls == [("MSFT", "2024-02-05", "2024-03-15")]
    assert (out["already"], out["complete"]) == (2, 1)
    out = chainfetch.fetch_chains(s, FakeChains(), REQS, refetch=True, log=lambda m: None)
    assert out["todo"] == 3


def test_a_fatal_refusal_stops_the_run_instead_of_marking_everything_refused(tmp_path):
    s = _settings(tmp_path)
    many = [(f"N{k:03d}", "2024-02-02", "2024-03-15") for k in range(20)]
    out = chainfetch.fetch_chains(s, FakeChains(fatal_at=2), many, workers=1, log=lambda m: None)
    assert "unauthenticated" in out["aborted"]
    assert out["complete"] == 2 and out["refused"] == 1
    with _db(s) as c:
        assert c.execute("SELECT COUNT(*) FROM eod_chain_requests").fetchone()[0] == 3


def test_a_stop_starts_nothing_more_keeps_what_finished_and_a_rerun_resumes(tmp_path):
    s = _settings(tmp_path)
    many = [(f"N{k:03d}", "2024-02-02", "2024-03-15") for k in range(10)]
    asked = {"n": 0}

    def stop() -> bool:
        asked["n"] += 1
        return asked["n"] > 4  # four requests start, then the deadline passes

    out = chainfetch.fetch_chains(s, FakeChains(), many, workers=1, stop=stop, log=lambda m: None)
    assert out["stopped"] is True and out["complete"] == 4
    again = chainfetch.fetch_chains(s, FakeChains(), many, workers=1, log=lambda m: None)
    assert again["stopped"] is False and again["already"] == 4 and again["complete"] == 6


def test_the_request_file_keeps_its_order_and_reads_each_request_once(tmp_path):
    f = tmp_path / "req.json"
    items = [{"symbol": "aapl", "session": "2024-02-02", "expiration": "2024-03-15"}] * 2
    items.append({"symbol": "DAL", "session": "2024-02-02T00:00", "expiration": "2024-03-15"})
    f.write_text(json.dumps({"requests": items}), encoding="utf-8")
    assert chainfetch.read_requests(f) == [
        ("AAPL", "2024-02-02", "2024-03-15"),
        ("DAL", "2024-02-02", "2024-03-15"),
    ]
    f.write_text(json.dumps(items), encoding="utf-8")  # a bare list reads too
    assert len(chainfetch.read_requests(f)) == 2


def test_status_reports_the_store(tmp_path):
    s = _settings(tmp_path)
    assert chainfetch.status(s) == {"store": None}
    chainfetch.fetch_chains(s, FakeChains(), REQS, log=lambda m: None)
    st = chainfetch.status(s)
    assert st["requests"] == {"complete": 3} and st["rows"] == 18 and st["names"] == 3


def test_a_refusal_never_costs_a_worker_so_every_request_is_tried():
    """2026-10-20: a refused request recorded itself and skipped submitting the next one, so each
    refusal lost a worker for good. Three refusals with three workers ended a 21,707-request run
    after 78."""
    many = [(f"N{k:03d}", "2024-02-02", "2024-03-15") for k in range(30)]
    fake = FakeChains(refuse={(f"N{k:03d}", "2024-02-02") for k in range(0, 30, 3)})
    out = chainfetch.fetch_chains(_settings_for(), fake, many, workers=2, log=lambda m: None)
    assert len(fake.calls) == 30
    assert (out["complete"], out["refused"]) == (20, 10)


def test_no_data_is_empty_and_never_retried(tmp_path):
    s = _settings(tmp_path)
    fake = FakeChains()
    fake.no_data = {("LRCX", "2020-02-05")}
    out = chainfetch.fetch_chains(s, fake, [("LRCX", "2020-02-05", "2020-03-20")], log=lambda m: None)
    assert (out["empty"], out["refused"]) == (1, 0)
    again = FakeChains()
    chainfetch.fetch_chains(s, again, [("LRCX", "2020-02-05", "2020-03-20")], log=lambda m: None)
    assert again.calls == []


def test_the_vendor_is_asked_in_its_own_spelling_and_the_store_keeps_the_studys(tmp_path):
    s = _settings(tmp_path)
    fake = FakeChains()
    chainfetch.fetch_chains(s, fake, [("BRK.B", "2020-01-22", "2020-02-21")], log=lambda m: None)
    assert fake.calls == [("BRKB", "2020-01-22", "2020-02-21")]
    with _db(s) as c:
        assert c.execute("SELECT symbol FROM eod_chain_requests").fetchone()[0] == "BRK.B"
    assert chainfetch.vendor_root("BRK.B") == "BRKB" and chainfetch.vendor_root("AAPL") == "AAPL"


class FakeDays:
    """The vendor's end-of-day answer for one session: every expiry it lists, each with a chain."""

    def __init__(self, expiries=("2024-02-16", "2024-03-15"), refuse=(), no_data=(), fatal_at=None):
        self.expiries, self.refuse, self.no_data = tuple(expiries), set(refuse), set(no_data)
        self.fatal_at, self.calls = fatal_at, []
        self.lock = threading.Lock()

    def eod_chain_day(self, root, day):
        with self.lock:
            self.calls.append((root, day))
            if self.fatal_at is not None and len(self.calls) > self.fatal_at:
                raise VendorRefusal("unauthenticated", "another login took the session")
        if root in self.refuse or (root, day) in self.refuse:
            reason = "permission_denied" if root in self.refuse else "vendor_error"
            raise VendorRefusal(reason, "refused")
        if (root, day) in self.no_data or day in self.no_data:
            raise VendorRefusal("no_data", "No data found")
        return {e: _rows(2) for e in self.expiries}


# 2024-02-12 .. 02-16 are five sessions; 02-19 is Presidents' Day; the path ends at its expiry.
PATHS = [
    ("AAPL", "2024-02-16", "2024-02-12", "2024-02-20"),
    ("MSFT", "2024-02-16", "2024-02-12", "2024-02-20"),
]
WEEK = ["2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16"]


def test_a_path_is_asked_by_session_and_each_session_is_its_own_complete_chain(tmp_path):
    s = _settings(tmp_path)
    fake = FakeDays()
    out = chainfetch.fetch_paths(s, fake, PATHS[:1], log=lambda m: None)
    assert (out["complete"], out["sessions"], out["rows"]) == (1, 5, 20)
    assert fake.calls == [("AAPL", d) for d in WEEK]  # trading days only, none after the expiry
    with _db(s) as c:
        days = [d for (d,) in c.execute("SELECT session FROM eod_chain_requests ORDER BY session")]
        assert days == WEEK
        # only the expiry a path wants is kept; the day records every expiry it held
        assert {e for (e,) in c.execute("SELECT DISTINCT expiration FROM eod_chain_quotes")} == {"2024-02-16"}
        held = c.execute("SELECT expirations FROM eod_chain_days WHERE session = '2024-02-12'").fetchone()[0]
        assert json.loads(held) == ["2024-02-16", "2024-03-15"]
        assert c.execute("SELECT status, sessions, rows FROM eod_chain_paths").fetchone() == (
            "complete",
            5,
            20,
        )
    # a single-session request already covered by the path is not fetched again
    again = FakeChains()
    chainfetch.fetch_chains(s, again, [("AAPL", "2024-02-13", "2024-02-16")], log=lambda m: None)
    assert again.calls == []


def test_a_session_several_paths_cover_is_asked_once(tmp_path):
    s = _settings(tmp_path)
    fake = FakeDays()
    both = [
        ("AAPL", "2024-02-16", "2024-02-12", "2024-02-16"),
        ("AAPL", "2024-03-15", "2024-02-12", "2024-02-16"),
    ]
    out = chainfetch.fetch_paths(s, fake, both, workers=2, log=lambda m: None)
    assert (out["complete"], out["sessions"]) == (2, 10)
    assert sorted(fake.calls) == [("AAPL", d) for d in WEEK]
    assert out["days"] == {"asked": 5, "answered": 5, "no_data": 0, "refused": 0}


def test_a_later_path_on_a_held_day_asks_only_for_an_expiry_the_day_held_but_did_not_keep(tmp_path):
    s = _settings(tmp_path)
    chainfetch.fetch_paths(s, FakeDays(), PATHS[:1], log=lambda m: None)
    # 2024-03-15 was listed on those days but not kept: asked again
    wants_kept = FakeDays()
    chainfetch.fetch_paths(
        s, wants_kept, [("AAPL", "2024-03-15", "2024-02-12", "2024-02-13")], log=lambda m: None
    )
    assert wants_kept.calls == [("AAPL", "2024-02-12"), ("AAPL", "2024-02-13")]
    # an expiry the day did not list is that day's answer: nothing to ask, the path is empty
    unlisted = FakeDays()
    out = chainfetch.fetch_paths(
        s, unlisted, [("AAPL", "2024-04-19", "2024-02-12", "2024-02-13")], log=lambda m: None
    )
    assert unlisted.calls == [] and out["empty"] == 1


def test_paths_resume_retry_refusals_and_stop_on_a_fatal_one(tmp_path):
    s = _settings(tmp_path)
    out = chainfetch.fetch_paths(s, FakeDays(refuse={"MSFT"}), PATHS, log=lambda m: None)
    assert (out["complete"], out["refused"]) == (1, 1)
    again = FakeDays()
    out = chainfetch.fetch_paths(s, again, PATHS, log=lambda m: None)
    assert {c[0] for c in again.calls} == {"MSFT"} and out["already"] == 1 and out["complete"] == 1
    many = [(f"N{k:03d}", "2024-02-16", "2024-02-12", "2024-02-16") for k in range(10)]
    out = chainfetch.fetch_paths(s, FakeDays(fatal_at=12), many, workers=1, log=lambda m: None)
    assert "unauthenticated" in out["aborted"] and out["complete"] == 2
    with _db(s) as c:  # the third path's two answered days are kept, the path left open
        assert c.execute("SELECT COUNT(*) FROM eod_chain_paths WHERE symbol = 'N002'").fetchone()[0] == 0
    resumed = FakeDays()
    out = chainfetch.fetch_paths(s, resumed, many, workers=1, log=lambda m: None)
    assert out["complete"] == 8 and len(resumed.calls) == 8 * 5 - 2


def test_a_stopped_path_fetch_starts_nothing_more_and_a_rerun_resumes(tmp_path):
    s = _settings(tmp_path)
    many = [(f"N{k:03d}", "2024-02-16", "2024-02-12", "2024-02-16") for k in range(10)]
    asked = {"n": 0}

    def stop() -> bool:
        asked["n"] += 1
        return asked["n"] > 20  # four paths' sessions start, then the deadline passes

    out = chainfetch.fetch_paths(s, FakeDays(), many, workers=1, stop=stop, log=lambda m: None)
    assert out["stopped"] is True and out["complete"] == 4
    again = chainfetch.fetch_paths(s, FakeDays(), many, workers=1, log=lambda m: None)
    assert again["stopped"] is False and again["already"] == 4 and again["complete"] == 6


def test_a_limit_counts_paths(tmp_path):
    s = _settings(tmp_path)
    fake = FakeDays()
    out = chainfetch.fetch_paths(s, fake, PATHS, limit=1, log=lambda m: None)
    assert (out["todo"], out["complete"]) == (1, 1) and {c[0] for c in fake.calls} == {"AAPL"}


def test_a_path_with_a_hole_is_refused_and_retried_and_days_before_the_listing_are_not_a_hole(tmp_path):
    s = _settings(tmp_path)
    holed = FakeDays(no_data={("AAPL", "2024-02-14")})
    out = chainfetch.fetch_paths(s, holed, PATHS[:1], log=lambda m: None)
    assert (out["complete"], out["empty"], out["refused"]) == (0, 0, 1)
    with _db(s) as c:
        status, detail = c.execute("SELECT status, detail FROM eod_chain_paths").fetchone()
        assert status == "refused" and detail.startswith("path_gap")
    again = FakeDays()
    out = chainfetch.fetch_paths(s, again, PATHS[:1], log=lambda m: None)
    assert again.calls == [("AAPL", "2024-02-14")] and out["complete"] == 1  # only the hole is asked
    late = FakeDays(no_data={("MSFT", "2024-02-12"), ("MSFT", "2024-02-13")})
    out = chainfetch.fetch_paths(s, late, PATHS[1:], log=lambda m: None)
    assert (out["complete"], out["sessions"]) == (1, 3)
    nothing = FakeDays(no_data=set(WEEK))
    out = chainfetch.fetch_paths(
        s, nothing, [("DAL", "2024-02-16", "2024-02-12", "2024-02-16")], log=lambda m: None
    )
    assert out["empty"] == 1


def test_judge_path_reads_a_paths_sessions_in_order():
    A, X, N = chainfetch.ANSWERED, chainfetch.ABSENT, chainfetch.NODATA
    assert chainfetch.judge_path([(N, 0), (A, 4), (X, 0), (A, 6)]) == ("complete", 2, 10, None)
    assert chainfetch.judge_path([(X, 0), (N, 0)]) == ("empty", 0, 0, None)
    assert chainfetch.judge_path([]) == ("empty", 0, 0, None)
    status, *_, detail = chainfetch.judge_path([(A, 4), (N, 0), (A, 4)])
    assert status == "refused" and detail.startswith("path_gap")
    assert chainfetch.judge_path([(A, 4), ("refused", "stream_removed: x")])[0::3] == (
        "refused",
        "stream_removed: x",
    )


def test_the_path_request_file_reads_each_once_and_refuses_a_backwards_range(tmp_path):
    f = tmp_path / "paths.json"
    item = {"symbol": "spy", "expiration": "2024-03-15", "start": "2024-01-10", "through": "2024-03-15"}
    f.write_text(json.dumps({"requests": [item, item]}), encoding="utf-8")
    assert chainfetch.read_path_requests(f) == [("SPY", "2024-03-15", "2024-01-10", "2024-03-15")]
    f.write_text(json.dumps([dict(item, start="2024-04-01")]), encoding="utf-8")
    try:
        chainfetch.read_path_requests(f)
    except ValueError:
        pass
    else:
        raise AssertionError("a range ending before it starts must be refused")


def test_a_path_over_a_year_is_fetched_not_refused(tmp_path):
    """The full set's monthlies start 1,100 days before their expiry; asked by session, no range
    limit applies."""
    s = _settings(tmp_path)
    long = [("SPY", "2024-02-16", "2022-01-03", "2024-02-16")]
    out = chainfetch.fetch_paths(s, FakeDays(), long, workers=2, log=lambda m: None)
    assert out["complete"] == 1 and out["sessions"] > 500


def test_a_path_fetch_reports_its_progress_counting_paths_already_held(tmp_path, monkeypatch):
    """The queue's meter reads this file (core.progress): a resumed fetch starts where it stopped."""
    s = _settings(tmp_path)
    chainfetch.fetch_paths(s, FakeDays(), PATHS[:1], log=lambda m: None)
    target = tmp_path / "q9.json"
    monkeypatch.setenv("CHERRYPICK_PROGRESS_FILE", str(target))
    chainfetch.fetch_paths(s, FakeDays(), PATHS, log=lambda m: None)
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["job"] == "backtest-fetch-chain-paths" and doc["state"] == "done"
    (phase,) = doc["phases"]
    assert (phase["name"], phase["done"], phase["total"]) == ("paths", 2, 2)
    assert phase["current"] == "MSFT 2024-02-16"


def test_a_path_fetch_stopped_by_a_fatal_refusal_reports_failed(tmp_path, monkeypatch):
    target = tmp_path / "q10.json"
    monkeypatch.setenv("CHERRYPICK_PROGRESS_FILE", str(target))
    many = [(f"N{k:03d}", "2024-02-16", "2024-02-12", "2024-02-16") for k in range(10)]
    chainfetch.fetch_paths(_settings(tmp_path), FakeDays(fatal_at=10), many, workers=1, log=lambda m: None)
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["state"] == "failed" and doc["phases"][0]["done"] == 2


class Refusing:
    """Sessions refused with a chosen reason per root: `stream_removed` (fixed) or `vendor_error`."""

    def __init__(self, reasons):
        self.reasons = dict(reasons)

    def eod_chain_day(self, root, day):
        if root in self.reasons:
            raise VendorRefusal(self.reasons[root], "refused")
        return {"2024-02-16": _rows(2)}

    def eod_chain(self, root, day, expiration):
        if root in self.reasons:
            raise VendorRefusal(self.reasons[root], "refused")
        return _rows()


def test_a_fixed_refusal_repeated_is_settled_and_a_first_or_passing_one_is_not(tmp_path):
    # Item 57's forward retest: two SPXW paths the vendor drops (stream_removed) every time. The
    # repeat pass found them refused again; that is the vendor's fixed answer, not a failed fetch.
    s = _settings(tmp_path)
    first = chainfetch.fetch_paths(
        s, Refusing({"AAPL": "stream_removed", "MSFT": "vendor_error"}), PATHS, log=lambda m: None
    )
    assert (first["refused"], first["settled"]) == (2, 0)  # a first refusal is never settled
    again = chainfetch.fetch_paths(
        s, Refusing({"AAPL": "stream_removed", "MSFT": "vendor_error"}), PATHS, log=lambda m: None
    )
    assert (again["refused"], again["settled"]) == (2, 1)  # vendor_error may pass: not settled
    # A fixed reason that differs from the one recorded before is not the same answer.
    changed = chainfetch.fetch_paths(
        s, Refusing({"AAPL": "permission_denied", "MSFT": "vendor_error"}), PATHS, log=lambda m: None
    )
    assert changed["settled"] == 0
    # The single-chain fetch counts the same way.
    reqs = REQS[:1]
    chainfetch.fetch_chains(s, Refusing({"AAPL": "stream_removed"}), reqs, log=lambda m: None)
    out = chainfetch.fetch_chains(s, Refusing({"AAPL": "stream_removed"}), reqs, log=lambda m: None)
    assert (out["refused"], out["settled"]) == (1, 1)
