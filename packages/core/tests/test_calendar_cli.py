"""The shared NYSE-holiday CLI (python -m cherrypick.core.calendar): a JSON wrapper over the
package's own `nyse_holidays`, for a read-only TypeScript bridge (the console) that cannot import
Python directly.
"""

import argparse

from cherrypick.core.calendar import __main__ as cli
from cherrypick.core.calendar import nyse_holidays


def _args(**kw):
    return argparse.Namespace(**kw)


def test_holidays_matches_nyse_holidays_for_one_year():
    out = cli.cmd_holidays(_args(year=[2026]))
    assert out["ok"] is True
    expected = sorted(d.isoformat() for d in nyse_holidays(2026))
    assert out["holidays"] == {"2026": expected}
    assert len(expected) > 0


def test_holidays_accepts_multiple_years_keyed_separately():
    out = cli.cmd_holidays(_args(year=[2025, 2026]))
    assert set(out["holidays"].keys()) == {"2025", "2026"}
    assert out["holidays"]["2025"] != out["holidays"]["2026"]


def test_holidays_dates_are_sorted_iso_strings():
    out = cli.cmd_holidays(_args(year=[2026]))
    dates = out["holidays"]["2026"]
    assert dates == sorted(dates)
    assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in dates)
