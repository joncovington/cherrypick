"""The bearing lookup: tiers, fitting, missing inputs, and the across-days check."""

from __future__ import annotations

import json

from cherrypick.technicals import compass


def row(code, s1, s6, score, sym="X"):
    return {
        "symbol": sym,
        "directionalEdge": code,
        "sentiment1M": s1,
        "sentiment6M": s6,
        "technicalRank": score,
    }


def test_the_score_falls_into_three_tiers_and_zero_is_no_score():
    assert [compass.tier(s) for s in (1, 4, 5, 6, 7, 10)] == [0, 0, 1, 1, 2, 2]
    assert compass.tier(0) is None and compass.tier(None) is None


def test_each_cell_takes_its_most_common_label():
    rows = [row("sb", 4, 4, 9)] * 3 + [row("bn", 4, 4, 8)] + [row("bn", 4, 4, 6)] * 2
    lookup = compass.fit(rows)
    assert lookup[(4, 4, 2)] == "strong bull", "the tier-2 cell: 3 strong bull to 1 bull"
    assert lookup[(4, 4, 1)] == "bull", "the same trends at a score of 6 are only bull"


def test_a_missing_input_or_an_unseen_cell_is_no_bearing():
    lookup = compass.fit([row("sbe", -3, -3, 2)])
    assert compass.bearing(lookup, -3, -3, 1) == "strong bear"
    assert compass.bearing(lookup, -3, None, 1) is None
    assert compass.bearing(lookup, -3, -3, 0) is None
    assert compass.bearing(lookup, 4, 4, 9) is None, "a cell the lists never showed"


def test_rows_without_a_known_code_are_not_fitted_or_scored():
    rows = [row(None, 4, 4, 9), row("zz", 4, 4, 9), row("sb", 4, 4, 9)]
    assert compass.evaluate(compass.fit(rows), rows)["rows"] == 1


def test_the_across_days_check_fits_on_the_earlier_lists_and_scores_the_latest(tmp_path):
    for day, rows in {
        "2026-10-07": [row("sb", 4, 4, 9), row("sbe", -3, -3, 2)],
        "2026-10-08": [row("sb", 4, 4, 9), row("sbe", -3, -3, 2), row("bn", -3, -3, 2)],
    }.items():
        (tmp_path / f"{day}.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    (tmp_path / "2026-10-09.rejected.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    got = compass.score_across_days(compass.load_snapshots(tmp_path))
    assert got["fitted_on"] == ["2026-10-07"] and got["scored"] == "2026-10-08"
    assert (got["agree"], got["rows"]) == (2, 3)
    assert compass.score_across_days({"2026-10-07": []})["ok"] is False
