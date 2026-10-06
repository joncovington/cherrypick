"""The read side's three series -- the arm's NAV, buy-and-hold, the expected path -- over sessions the
loop actually ran."""

import pytest
from conftest import add_dividend
from test_paper_loop import tick

from cherrypick.contango import analytics, db

SESSIONS = [
    ("2026-10-06", 50.0, 0.85),
    ("2026-10-07", 51.0, 0.88),
    ("2026-10-08", 49.0, 1.02),  # inversion: both arms to cash
    ("2026-10-09", 49.5, 0.90),  # back in
]


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper_trades.db"))


def _run(config, conn, cache, technicals):
    for day, svxy, ratio in SESSIONS:
        cache.quote("SVXY", svxy - 0.01, svxy + 0.01).quote("SHV", 110.40, 110.41)
        cache.regime(ratio)
        tick(config, conn, cache, technicals, day, "15:51")


def test_marks_are_recorded_for_every_fund_at_the_decision_tick(config, conn, cache, technicals):
    _run(config, conn, cache, technicals)
    assert list(db.marks(conn, "SVXY").values()) == [50.0, 51.0, 49.0, 49.5]
    assert len(db.marks(conn, "SHV")) == 4


def test_the_arm_tracks_its_own_rule_and_the_benchmark_is_buy_and_hold(config, conn, cache, technicals):
    _run(config, conn, cache, technicals)
    out = analytics.metrics(conn, config, technicals_path=str(technicals))
    control = out["arms"]["control"]
    assert [d for d, _ in control["series"]] == [d for d, *_ in SESSIONS]
    assert [d for d, _ in control["expected"]] == [d for d, *_ in SESSIONS]
    # The fixture's half-spread is exactly the replay's 2 bps, so what is left is whole shares
    # and the SEC/TAF on sells: well under half a percent.
    assert abs(control["tracking"]) < 0.005
    bench = out["benchmarks"]["SVXY"]["series"]
    assert bench[0][1] == pytest.approx(10_000) and bench[-1][1] == pytest.approx(10_000 * 49.5 / 50.0)
    assert control["reading"]["basis"] == "daily" and control["reading"]["days"] == 3


def test_the_expected_cash_leg_earns_its_distributions():
    mids = {"2026-10-01": 100.0, "2026-10-02": 100.0, "2026-10-05": 100.0}
    tr = analytics.total_return(mids, [{"ex_date": "2026-10-02", "amount": 0.5}])
    assert tr["2026-10-01"] == 100.0 and tr["2026-10-05"] == pytest.approx(100.5)


def test_the_expected_path_reads_the_cash_funds_distributions_from_the_store(config, conn, cache, technicals):
    add_dividend(technicals, "SHV", "2026-10-09", 1.10)  # a large one, so the effect is visible
    _run(config, conn, cache, technicals)
    out = analytics.metrics(conn, config, technicals_path=str(technicals))
    # Cash only from 10-08 to 10-09; the arm sold SHV on 10-09 (the ex-date), so it was paid.
    acct, exp = db.account(conn, "control"), dict(out["arms"]["control"]["expected"])
    assert acct is not None and exp["2026-10-09"] > exp["2026-10-08"]
