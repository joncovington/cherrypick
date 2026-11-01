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


def _days(start: str, n: int) -> list[str]:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_weekday_and_event_day_are_day_cells_with_a_placebo():
    # 70 calendar days from a Monday: 10 of each weekday. Thursdays earn 1R, every other day loses
    # 0.1R, so Thursday is the best set of days and the placebo says chance rarely matches it.
    recs = []
    for d in _days("2026-01-05", 70):
        r = 1.0 if slices._weekday({"entry_day": d}) == "Thu" else -0.1
        recs += [_rec(r, entry_day=d, event_day="FOMC" if d.endswith("-14") else "none")] * 3
    out = slices.report(recs)
    wd = out["dimensions"]["weekday"]
    assert set(wd) == {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    thu = wd["Thu"]
    assert thu["n"] == 30 and thu["placebo"]["sessions"] == 10 and thu["placebo"]["of"] == 70
    assert thu["placebo"]["beaten_by"] < 0.01
    assert wd["Mon"]["placebo"]["beaten_by"] > 0.5
    ev = out["dimensions"]["event_day"]
    fomc = sum(d.endswith("-14") for d in _days("2026-01-05", 70))
    assert ev["FOMC"]["greyed"] and "placebo" not in ev["FOMC"]  # a few days, under 30 trades: unread
    assert ev["none"]["placebo"]["sessions"] == 70 - fomc


def test_a_day_cell_with_many_trades_from_few_days_is_greyed_and_a_trade_cell_is_not():
    # MEIC-like: 40 trades on each of 2 NFP days (80 trades, 2 sessions) and 40 quiet days of 3.
    recs = []
    for d in _days("2026-03-02", 42):
        many = d in ("2026-03-06", "2026-04-03")
        recs += [_rec(0.5 if many else 0.1, entry_day=d, event_day="NFP" if many else "none",
                      environment="uptrend")] * (40 if many else 3)  # fmt: skip
    out = slices.report(recs)
    nfp = out["dimensions"]["event_day"]["NFP"]
    assert nfp["n"] == 80 and nfp["sessions"] == 2
    assert nfp["greyed"] and "placebo" not in nfp
    none = out["dimensions"]["event_day"]["none"]
    assert none["sessions"] == 40 and not none["greyed"] and none["placebo"]["sessions"] == 40
    env = out["dimensions"]["environment"]["uptrend"]
    assert env["sessions"] == 42 and not env["greyed"]  # trade cuts keep the trade-count rule
    assert out["ranked_cells"] == sum(
        1 for d in out["dimensions"].values() for c in d.values() if not c["greyed"]
    )


def test_a_trade_dimension_never_carries_a_placebo():
    recs = [_rec(0.2, entry_day=d, environment="uptrend") for d in _days("2026-02-02", 40)]
    recs += [_rec(-0.2, entry_day=d, environment="downtrend") for d in _days("2026-04-02", 40)]
    env = slices.report(recs)["dimensions"]["environment"]
    assert all("placebo" not in c for c in env.values())


def test_a_schema_without_r_reports_net_and_says_so():
    recs = [{"net_pnl": 10.0, "r_multiple": None, "environment": "sideways"}] * 2
    out = slices.report(recs)
    assert out["unit"] == "net $" and out["overall"]["mean"] == 10.0


def test_interval_by_hand():
    out = slices.cell([1.0, 2.0, 3.0, 4.0, 5.0])
    assert out["mean"] == 3.0 and out["ci95"] == [1.0368, 4.9632]
