"""B5: the slice report -- fixed bins, n and interval per cell, thin cells greyed, in-sample."""

from cherrypick.core import slices


def _rec(r, **kw):
    return {"net_pnl": r * 100, "r_multiple": r, **kw}


def test_cells_carry_n_interval_and_grey_under_30():
    recs = [_rec(0.1, environment="uptrend")] * 30 + [_rec(-0.5, environment="downtrend")] * 3
    out = slices.report(recs, label="bwb/bounce")
    env = out["dimensions"]["environment"]
    assert env["uptrend"]["n"] == 30 and not env["uptrend"]["greyed"]
    assert env["downtrend"]["greyed"] and env["downtrend"]["mean"] == -0.5
    assert out["unit"] == "R" and out["in_sample"] is True
    assert out["cells"] == 2 and out["ranked_cells"] == 1
    assert "never a gate" in out["note"]


def test_bins_are_fixed():
    recs = [
        _rec(
            1,
            atr_pct=0.5,
            rel_volume=1.5,
            holding_days=0,
            spx_vs_200d=-0.01,
            entry_time="2026-10-15T14:05:00",
        ),
        _rec(
            1,
            atr_pct=3.0,
            rel_volume=0.9,
            holding_days=10,
            spx_vs_200d=0.05,
            entry_time="2026-10-15 09:45:00",
        ),
    ]
    d = slices.report(recs)["dimensions"]
    assert set(d["atr_pct"]) == {"<1%", "2-4%"}
    assert set(d["rel_volume"]) == {">=1.2", "0.8-1.2"}
    assert set(d["holding"]) == {"same day", "8-30d"}
    assert set(d["spx_vs_200d"]) == {"below", "above"}
    assert set(d["entry_hour"]) == {"14:00", "09:00"}


def test_a_schema_without_r_reports_net_and_says_so():
    recs = [{"net_pnl": 10.0, "r_multiple": None, "environment": "sideways"}] * 2
    out = slices.report(recs)
    assert out["unit"] == "net $" and out["overall"]["mean"] == 10.0


def test_interval_by_hand():
    out = slices.cell([1.0, 2.0, 3.0, 4.0, 5.0])
    assert out["mean"] == 3.0 and out["ci95"] == [1.0368, 4.9632]
