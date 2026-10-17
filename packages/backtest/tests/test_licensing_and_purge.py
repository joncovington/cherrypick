"""The licence clock and the purge: dry run by default, and only marked directories are removed."""

from __future__ import annotations

from datetime import date

from cherrypick.backtest import licensing, paths, purge, settings


def test_an_active_subscription_has_no_deadline():
    st = licensing.status(settings.resolve({}), today=date(2026, 10, 17))
    assert st["active"] is True and st["purge_by"] is None and st["overdue"] is False


def test_termination_starts_a_thirty_day_clock():
    s = settings.resolve({"vendor": {"license": {"terminated_on": "2026-11-01"}}})
    before = licensing.status(s, today=date(2026, 10, 20))
    assert before["active"] is True and before["purge_by"] == "2026-12-01"
    after = licensing.status(s, today=date(2026, 11, 25))
    assert after["active"] is False and after["days_left"] == 6 and not after["overdue"]
    late = licensing.status(s, today=date(2026, 12, 2))
    assert late["overdue"] is True and late["days_left"] == -1


def _seed(s):
    vendor = paths.ensure_vendor_dir(s)
    (vendor / "SPX-2026-10.db").write_bytes(b"x" * 10)
    paths.results_dir().mkdir(parents=True, exist_ok=True)
    paths.results_db().write_bytes(b"r")
    return vendor


def test_the_default_is_a_dry_run_that_lists_and_touches_nothing():
    s = settings.resolve({})
    vendor = _seed(s)
    report = purge.plan(s)
    assert report["targets"] == [str(vendor)]
    assert any(f["path"].endswith("SPX-2026-10.db") for f in report["listing"])
    assert (vendor / "SPX-2026-10.db").exists()


def test_apply_removes_the_vendor_data_and_keeps_results_unless_asked():
    s = settings.resolve({})
    vendor = _seed(s)
    report = purge.apply(s)
    assert report["applied"] and not vendor.exists()
    assert paths.results_db().exists(), "derived results go only with --derived"
    _seed(s)
    purge.apply(s, derived=True)
    assert not paths.results_dir().exists()


def test_an_unmarked_directory_is_refused(tmp_path):
    """A mistyped store.dir must not turn purge into `rm -rf` of something else."""
    elsewhere = tmp_path / "my-documents"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("mine", encoding="utf-8")
    s = settings.resolve({"store": {"dir": str(elsewhere)}})
    report = purge.apply(s)
    assert report["targets"] == [] and report["refused"][0]["path"] == str(elsewhere)
    assert (elsewhere / "keep.txt").exists()


def test_the_marker_is_written_before_anything_else():
    s = settings.resolve({})
    vendor = paths.ensure_vendor_dir(s)
    assert [p.name for p in vendor.iterdir()] == [paths.LICENSED_MARKER]
