"""Validation over synthetic stores: identical data agrees exactly, a shifted vendor shows its shift,
and the unlock rule compares the vendor with the recording, never with perfection."""

from __future__ import annotations

import pytest
from test_fetch import DAY, _rows

from cherrypick.backtest import fetch, settings, store, validate


def _store_at(tmp_path, name, rows):
    s = settings.resolve({"store": {"dir": str(tmp_path / name)}})
    conn = store.connect(s, "SPX")
    built = fetch.build_session(
        rows, {(r, k): 100 for k in range(5000, 7000, 5) for r in ("CALL", "PUT")}, day=DAY, each_side=20
    )
    fetch.write_session(conn, built, symbol="SPX", day=DAY, vendor="fake", root="SPXW", cadence=15)
    conn.commit()
    conn.close()
    return store.db_path(s, "SPX")


def test_identical_stores_agree_exactly(tmp_path):
    a = _store_at(tmp_path, "recorder", _rows(DAY))
    b = _store_at(tmp_path, "vendor", _rows(DAY))
    q = validate.quotes_layer(a, b, "SPX", DAY)
    assert q["matched"] == 1.0 and q["identical"] == 1.0 and q["mid_gap"]["median"] == 0.0
    p = validate.profile_layer(a, b, "SPX", DAY)
    assert p["pairs"] > 70 and p["call_wall_same"] == 1.0 and p["net_gex_sign_same"] == 1.0


def test_a_shifted_vendor_shows_its_shift(tmp_path):
    a = _store_at(tmp_path, "recorder", _rows(DAY))
    shifted = [{**r, "bid": r["bid"] + 0.1, "ask": r["ask"] + 0.1} for r in _rows(DAY)]
    b = _store_at(tmp_path, "vendor", shifted)
    q = validate.quotes_layer(a, b, "SPX", DAY)
    assert q["identical"] == 0.0
    assert q["mid_gap"]["mean_signed"] == pytest.approx(-0.1)


def test_a_missing_store_is_reported_not_raised(tmp_path):
    a = _store_at(tmp_path, "recorder", _rows(DAY))
    q = validate.quotes_layer(a, tmp_path / "absent.db", "SPX", DAY)
    assert q == {"ok": False, "reason": "no vendor samples"}


def _summary(sessions, **checks):
    base = dict.fromkeys(
        ("entries_matched", "extra_entry_episodes", "completions_matched", "early_completion_episodes",
         "credit_abs_median", "credit_abs_p90"),
        True,
    )  # fmt: skip
    base.update(checks)
    return {"summary": {"measures": {"sessions": sessions}, "checks": base, "passes": all(base.values())}}


def _runner(recorder, vendor):
    def run(args):
        return recorder if "recorder" in args[args.index("--history-db") + 1] else vendor

    return run


def test_unlock_needs_ten_sessions_and_a_vendor_no_worse_than_the_recording(tmp_path):
    rec_db, ven_db = tmp_path / "recorder.db", tmp_path / "vendor.db"
    # The recording itself fails completion timing; a vendor failing the same check still unlocks.
    rec = _summary(12, completions_matched=False)
    ok = validate.decisions_layer(
        rec_db, ven_db, ["d"], runner=_runner(rec, _summary(12, completions_matched=False))
    )
    assert ok["unlocks_history"] is True
    worse = validate.decisions_layer(
        rec_db,
        ven_db,
        ["d"],
        runner=_runner(rec, _summary(12, entries_matched=False, completions_matched=False)),
    )
    assert worse["unlocks_history"] is False and worse["vendor_at_least_recorder"]["entries_matched"] is False
    early = validate.decisions_layer(rec_db, ven_db, ["d"], runner=_runner(_summary(4), _summary(4)))
    assert early["unlocks_history"] is False and early["sessions_compared"] == 4


def test_a_failed_check_run_never_unlocks(tmp_path):
    broken = {"ok": False, "error": "boom"}
    out = validate.decisions_layer(
        tmp_path / "recorder.db", tmp_path / "vendor.db", ["d"], runner=_runner(broken, broken)
    )
    assert out["unlocks_history"] is False
