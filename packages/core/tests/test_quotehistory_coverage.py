"""`python -m cherrypick.core.quotehistory coverage`: what the recorder's quote history holds per
session, read only through `session()`.

A day with no samples is listed, never skipped; a gap is measured between consecutive samples inside
the session; a sample whose spot could not be joined is counted with its reason.
"""

from datetime import datetime

from cherrypick.core import clock
from cherrypick.core.quotehistory import __main__ as qhcli


def _ts(hhmmss: str, day: str = "2026-10-13") -> float:
    return datetime.fromisoformat(f"{day}T{hhmmss}").replace(tzinfo=clock.ET).timestamp()


def _sample(hhmmss: str, *, spot_reason=None, strikes=3) -> dict:
    return {
        "ts": _ts(hhmmss),
        "spot_reason": spot_reason,
        "puts": {k: {} for k in range(strikes)},
        "calls": {k: {} for k in range(strikes)},
    }


def test_a_session_reports_samples_gaps_and_unjoined_spot():
    samples = [
        _sample("09:30:00"),
        _sample("09:30:15"),
        _sample("09:33:15", spot_reason="spot_stale"),  # a three-minute hole before it
        _sample("09:33:30"),
    ]
    out = qhcli.session_coverage(samples, gap_seconds=60)
    assert out["recorded"] and out["samples"] == 4
    assert (out["first"], out["last"]) == ("09:30:00", "09:33:30")
    assert out["gaps"] == [{"from": "09:30:15", "to": "09:33:15", "seconds": 180.0}]
    assert out["max_gap_s"] == 180.0
    assert out["no_spot"] == 1 and out["no_spot_reasons"] == {"spot_stale": 1}
    assert out["strikes_median"] == 6


def test_every_trading_day_is_listed_and_a_missing_one_says_so():
    calls = []

    def reader(symbol, day, history_db=None):
        calls.append((symbol, day))
        return [_sample("10:00:00")] if day == "2026-10-13" else []

    out = qhcli.coverage(["SPX"], "2026-10-13", "2026-10-19", reader=reader)
    spx = out["symbols"]["SPX"]
    # The weekend is not a session; Monday the 19th is, and it recorded nothing.
    assert [r["session"] for r in spx["sessions"]] == [
        "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19",
    ]  # fmt: skip
    assert spx["recorded"] == 1 and spx["missing"] == ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
    assert ("SPX", "2026-10-17") not in calls


def test_the_verb_reads_an_absent_store_as_nothing_recorded(tmp_path, capsys):
    import json

    assert qhcli.main(["coverage", "--db", str(tmp_path / "none.db"), "--since", "2026-10-13",
                       "--until", "2026-10-13"]) == 0  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is False and out["symbols"]["SPX"]["missing"] == ["2026-10-13"]
    assert not (tmp_path / "none.db").exists()
