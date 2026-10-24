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
