"""Validation over synthetic stores: identical data agrees exactly, a shifted vendor shows its shift,
and the unlock rule compares the vendor with the recording, never with perfection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_fetch import DAY, _rows

from cherrypick.backtest import fetch, settings, store, validate


def _store_at(tmp_path, name, rows, cadence=15):
    s = settings.resolve({"store": {"dir": str(tmp_path / name)}})
    conn = store.connect(s, "SPX", cadence)
    built = fetch.build_session(
        rows, {(r, k): 100 for k in range(5000, 7000, 5) for r in ("CALL", "PUT")}, day=DAY, each_side=20
    )
    fetch.write_session(conn, built, symbol="SPX", day=DAY, vendor="fake", root="SPXW", cadence=cadence)
    conn.commit()
    conn.close()
    return store.db_path(s, "SPX", cadence)


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


#: (passing measure, failing measure, threshold key) per check, in flies' own threshold names: a
#: summary's measures agree with its pass/fail, as a real replay check's do.
_MEASURES = {
    "entries_matched": (0.95, 0.80, "entries_matched_min"),
    "extra_entry_episodes": (0.05, 0.20, "extra_entry_episodes_max"),
    "completions_matched": (0.90, 0.70, "completions_matched_min"),
    "early_completion_episodes": (0.05, 0.20, "early_completion_episodes_max"),
    "credit_abs_median": (0.05, 0.20, "credit_abs_median_max"),
    "credit_abs_p90": (0.20, 0.50, "credit_abs_p90_max"),
}


def _summary(sessions, measures=None, **checks):
    base = dict.fromkeys(_MEASURES, True)
    base.update(checks)
    m = {k: (good if base[k] else bad) for k, (good, bad, _) in _MEASURES.items()}
    m.update(measures or {})
    thresholds = {key: 0.0 for _, _, key in _MEASURES.values()}
    return {"summary": {"measures": {"sessions": sessions, **m}, "checks": base, "thresholds": thresholds,
                        "passes": all(base.values())}}  # fmt: skip


def _runner(recorder, vendor, thinned=None):
    """Answers by the history file's own name, never the test directory's (which carries the test name)."""

    def run(args):
        name = Path(args[args.index("--history-db") + 1]).name
        if "thinned" in name:
            return thinned
        return recorder if "recorder" in name else vendor

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


def test_a_coarser_vendor_is_judged_against_the_recording_at_its_own_cadence(tmp_path):
    """The 2026-10-17 amendment. A 60 s vendor that misses entries exactly as the recording thinned to
    60 s does is no worse than our data: it unlocks against the thinned recording, where the bar as
    first written (against the full 15 s recording) refuses it."""
    rec_db, ven_db, thin_db = tmp_path / "recorder.db", tmp_path / "vendor.db", tmp_path / "thinned.db"
    full = _summary(12, completions_matched=False)
    at_60 = _summary(12, entries_matched=False, completions_matched=False)
    vendor = _summary(12, entries_matched=False, completions_matched=False)
    runner = _runner(full, vendor, thinned=at_60)
    old = validate.decisions_layer(rec_db, ven_db, ["d"], runner=runner)
    assert old["unlocks_history"] is False and old["data_basis"] == "recorder"
    new = validate.decisions_layer(rec_db, ven_db, ["d"], runner=runner, thinned_db=thin_db)
    assert new["unlocks_history"] is True and new["data_basis"] == "recorder_at_vendor_cadence"
    # The cost of the minute is reported beside the bar, not hidden by it.
    assert "cadence_cost" in new and new["cadence_cost"]["completion_outcome_agreement"].keys() == {
        "full",
        "at_vendor_cadence",
        "vendor",
    }


def test_a_vendor_worse_than_the_thinned_recording_still_never_unlocks(tmp_path):
    """The guard shown to fail: on a check the recording passes at the vendor's cadence, a vendor that
    fails it is refused, and the check is named."""
    rec_db, ven_db, thin_db = tmp_path / "recorder.db", tmp_path / "vendor.db", tmp_path / "thinned.db"
    at_60 = _summary(12, completions_matched=False)
    vendor = _summary(12, credit_abs_median=False, completions_matched=False)
    out = validate.decisions_layer(
        rec_db, ven_db, ["d"], runner=_runner(_summary(12), vendor, thinned=at_60), thinned_db=thin_db
    )
    assert out["unlocks_history"] is False
    assert out["vendor_at_least_recorder"]["credit_abs_median"] is False
    # And too few sessions on any of the three keeps it locked.
    short = validate.decisions_layer(
        rec_db,
        ven_db,
        ["d"],
        runner=_runner(_summary(12), _summary(12), thinned=_summary(4)),
        thinned_db=thin_db,
    )
    assert short["unlocks_history"] is False and short["sessions_compared"] == 4


def test_thinning_keeps_the_recorded_sample_nearest_each_vendor_instant(tmp_path):
    rec = _store_at(tmp_path, "rec", _rows(DAY, cadence=15), cadence=15)
    ven = _store_at(tmp_path, "ven", _rows(DAY, cadence=60), cadence=60)
    out = validate.thin_to_vendor_instants(rec, ven, "SPX", [DAY], tmp_path / "thinned.db")

    def instants(db, table):
        conn = sqlite3.connect(db)
        try:
            return [
                r[0] for r in conn.execute(f"SELECT ts FROM {table} WHERE trade_date = ? ORDER BY ts", (DAY,))
            ]
        finally:
            conn.close()

    vendor_ts = instants(ven, "gex_quote_samples")
    kept = instants(out, "gex_quote_samples")
    assert len(instants(rec, "gex_quote_samples")) > 3 * len(kept)
    assert len(kept) == len(vendor_ts)
    assert all(min(abs(k - v) for v in vendor_ts) <= validate.QUOTE_MATCH_SECONDS for k in kept)
    spots = instants(out, "gex_spot_history")
    assert len(spots) <= len(vendor_ts) and all(min(abs(s - v) for v in vendor_ts) <= 30 for s in spots)
    conn = sqlite3.connect(out)
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM gex_quotes WHERE sample_id NOT IN (SELECT id FROM gex_quote_samples)"
        ).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0
    assert validate.median_gap(rec, "SPX", [DAY]) == 15 and validate.median_gap(ven, "SPX", [DAY]) == 60


def test_run_thins_only_when_the_vendor_is_coarser(tmp_path):
    rec = _store_at(tmp_path, "rec", _rows(DAY, cadence=15), cadence=15)
    _store_at(tmp_path, "ven", _rows(DAY, cadence=60), cadence=60)
    s = settings.resolve({"store": {"dir": str(tmp_path / "ven")}})
    seen = []

    def runner(args):
        seen.append(Path(args[args.index("--history-db") + 1]).name)
        return _summary(12)

    coarse = validate.run(s, "SPX", [DAY], recorder_db=rec, cadence=60, runner=runner)
    assert coarse["decisions"]["data_basis"] == "recorder_at_vendor_cadence"
    assert "thinned.db" in seen and coarse["sample_gap_seconds"] == {"recorder": 15, "vendor": 60}
    seen.clear()
    _store_at(tmp_path, "ven", _rows(DAY, cadence=15), cadence=15)
    same = validate.run(s, "SPX", [DAY], recorder_db=rec, cadence=15, runner=runner)
    assert same["decisions"]["data_basis"] == "recorder" and "thinned.db" not in seen


def test_a_failed_check_run_never_unlocks(tmp_path):
    broken = {"ok": False, "error": "boom"}
    out = validate.decisions_layer(
        tmp_path / "recorder.db", tmp_path / "vendor.db", ["d"], runner=_runner(broken, broken)
    )
    assert out["unlocks_history"] is False


def test_a_check_both_sides_fail_still_needs_the_vendor_close_on_its_measure(tmp_path):
    """The 2026-10-25 measure bar. The recording at the vendor's cadence and the vendor both fail entry
    matching; the pass/fail comparison read that as "at least as good" however far behind the vendor
    was. Now the vendor's measure must be within the declared tolerance of the recording's."""
    rec_db, ven_db, thin_db = tmp_path / "recorder.db", tmp_path / "vendor.db", tmp_path / "thinned.db"
    at_60 = _summary(12, entries_matched=False, measures={"entries_matched": 0.85})
    close = _summary(12, entries_matched=False, measures={"entries_matched": 0.82})
    far = _summary(12, entries_matched=False, measures={"entries_matched": 0.40})
    ok = validate.decisions_layer(rec_db, ven_db, ["d"], runner=_runner(_summary(12), close, thinned=at_60),
                                  thinned_db=thin_db)  # fmt: skip
    assert ok["unlocks_history"] is True
    bad = validate.decisions_layer(rec_db, ven_db, ["d"], runner=_runner(_summary(12), far, thinned=at_60),
                                   thinned_db=thin_db)  # fmt: skip
    assert bad["vendor_at_least_recorder"]["entries_matched"] is True  # the old bar would have passed it
    assert bad["vendor_within_tolerance"]["entries_matched"] is False
    assert bad["unlocks_history"] is False


def test_the_tolerance_reads_each_measure_in_its_own_direction():
    assert validate.measure_within("entries_matched", "min", 0.90, 0.86)
    assert not validate.measure_within("entries_matched", "min", 0.90, 0.84)
    assert validate.measure_within("extra_entry_episodes", "max", 0.05, 0.09)
    assert not validate.measure_within("extra_entry_episodes", "max", 0.05, 0.11)
    # A dollar gap: the larger of 0.02 and a quarter of the recording's.
    assert validate.measure_within("credit_abs_p90", "max", 0.40, 0.49)
    assert not validate.measure_within("credit_abs_p90", "max", 0.40, 0.51)
    assert validate.measure_within("credit_abs_median", "max", 0.04, 0.059)
    assert not validate.measure_within("credit_abs_median", "max", None, 0.01)  # nothing to compare


def test_quotes_are_also_compared_at_the_vendor_instants():
    def sample(t):
        return {
            "ts": t,
            "underlying_price": 100.0,
            "puts": {100.0: {"bid": 1.0, "ask": 1.2, "mid": 1.1}},
            "calls": {},
        }

    rec = [sample(t) for t in range(0, 120, 15)]
    ven = [sample(t) for t in (0, 60)]
    out = validate._at_vendor_instants(rec, ven)
    assert out["paired_samples"] == 1.0 and out["shared_quotes"] == 2 and out["identical"] == 1.0
