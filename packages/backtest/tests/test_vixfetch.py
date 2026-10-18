"""The VIX minute series: written in the recorder's own form so `core.regime` reads a vendor session as
it reads a recorded one; resumable; never an empty series; one session's failure is that session's."""

from __future__ import annotations

from datetime import datetime

from cherrypick.core import regime
from cherrypick.core.clock import ET
from test_fetch import DAY, _rows

from cherrypick.backtest import fetch, settings, store, vixfetch


def _ts(hhmm: str) -> float:
    return datetime.fromisoformat(f"{DAY}T{hhmm}").replace(tzinfo=ET).timestamp()


def _store(tmp_path):
    s = settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})
    conn = store.connect(s, "SPX")
    built = fetch.build_session(
        _rows(DAY),
        {(r, k): 100 for k in range(5000, 7000, 5) for r in ("CALL", "PUT")},
        day=DAY,
        each_side=20,
    )
    fetch.write_session(conn, built, symbol="SPX", day=DAY, vendor="fake", root="SPXW", cadence=15)
    conn.commit()
    conn.close()
    return s


class FakeVix:
    def __init__(self, minutes=None, expirations=("2026-10-21", "2026-11-18"), fail=False):
        self.minutes = minutes if minutes is not None else [(_ts("09:30"), 16.0), (_ts("09:31"), 16.2)]
        self._expirations = list(expirations)
        self.fail = fail
        self.calls = []

    def expirations(self, root):
        assert root == "VIX"
        return self._expirations

    def vix_minutes(self, day, expiration):
        self.calls.append((day, expiration))
        if self.fail:
            raise RuntimeError("vendor said no")
        return self.minutes


def test_a_session_gets_the_recorders_form_and_core_reads_it(tmp_path):
    s = _store(tmp_path)
    client = FakeVix()
    out = vixfetch.fetch_vix(s, client, "SPX", DAY, DAY, log=lambda _m: None)
    assert out["written"] == {DAY: 2} and client.calls == [(DAY, "2026-10-21")]  # the nearest expiry after
    block = regime.markets_at([_ts("09:31") + 20], history_db=store.db_path(s, "SPX"))[0]
    assert block["status"] == "measured"
    assert block["readings"]["vix"] == {"value": 16.2, "symbol": "VIX", "usable": True, "reason": None}


def test_it_resumes_and_replaces_only_when_asked(tmp_path):
    s = _store(tmp_path)
    vixfetch.fetch_vix(s, FakeVix(), "SPX", DAY, DAY, log=lambda _m: None)
    again = FakeVix(minutes=[(_ts("09:30"), 30.0)])
    assert vixfetch.fetch_vix(s, again, "SPX", DAY, DAY, log=lambda _m: None)["sessions"] == 0
    assert again.calls == []
    out = vixfetch.fetch_vix(s, again, "SPX", DAY, DAY, refetch=True, log=lambda _m: None)
    assert out["written"] == {DAY: 1}
    block = regime.markets_at([_ts("09:30")], history_db=store.db_path(s, "SPX"))[0]
    assert block["readings"]["vix"]["value"] == 30.0


def test_an_empty_or_failed_session_writes_nothing(tmp_path):
    s = _store(tmp_path)
    assert vixfetch.fetch_vix(s, FakeVix(minutes=[]), "SPX", DAY, DAY, log=lambda _m: None)["empty"] == [DAY]
    failed = vixfetch.fetch_vix(s, FakeVix(fail=True), "SPX", DAY, DAY, log=lambda _m: None)
    assert DAY in failed["refused"] and "vendor said no" in failed["refused"][DAY]
    late = vixfetch.fetch_vix(s, FakeVix(expirations=("2026-09-16",)), "SPX", DAY, DAY, log=lambda _m: None)
    assert late["refused"] == {DAY: "no_vix_expiry_after_session"}
    assert regime.markets_at([_ts("09:31")], history_db=store.db_path(s, "SPX"))[0]["status"] != "measured"
