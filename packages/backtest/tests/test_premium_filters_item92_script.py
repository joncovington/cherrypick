"""Item 92's script (premium_filters_item92.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "premium_filters_item92", Path(__file__).resolve().parents[1] / "scripts" / "premium_filters_item92.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    # Fails until the declaration fills every value of RULE.
    kit_rule.check_rule(m.RULE)


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


def test_the_declaration_is_what_the_plan_states():
    # A change to any of these is a change to the declared test: it needs a logged correction.
    assert m.RULE["splits"] == ["2021-06-01"] and m.RULE["delta"] is True and m.RULE["t_bar"] == 2.4
    assert (m.RULE["min_n"], m.RULE["holdout_min_n"]) == (30, 30)
    assert m.ARMS == ("put-ivr30", "put-hv_lt_iv", "spread-ivr30", "spread-hv_lt_iv")
    assert m.FILTER_RULE == {
        "t_bar": 2.4,
        "min_entries": 20,
        "holdout_min_entries": 20,
        "cluster": "cycle",
        "split": "2021-06-01",
    }
    assert m.SPEC["funds"] == {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX", "GLD": "GVZ"}
    assert m.SPEC["structures"] == {
        "put": {"short_delta": 0.16},
        "spread": {"short_delta": 0.20, "long_delta": 0.10},
    }
    assert (
        m.SPEC["exit"]["profit_target_pct_of_credit"] == 50 and m.SPEC["exit"]["close_days_to_expiry"] == 21
    )
    assert not set(m.ARMS) & set(m.REPORTED)


# ------------------------------------------------------------------------------------------------
# The replay, on invented chains priced from a model: the store is never read.

from datetime import date, timedelta  # noqa: E402

from cherrypick.core import bs  # noqa: E402

from cherrypick.backtest.study.chains import Chain  # noqa: E402

EXPIRY = "2025-03-21"


def chain(session: str, spot: float, vol: float = 0.18, expiry: str = EXPIRY, spread: float = 0.04) -> Chain:
    years = max((date.fromisoformat(expiry) - date.fromisoformat(session)).days, 1) / 365.0
    rows = []
    for k in range(300, 900, 5):  # one fixed strike grid, as a listed chain has, whatever the spot
        for r in ("C", "P"):
            px = bs.price(spot, float(k), years, vol, r, 0.0)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(px - spread / 2, 0.0), px + spread / 2))
    return Chain(rows, session, expiry)


def path(spots: list[float], start: str = "2025-01-21", vol: float = 0.18) -> list[tuple[str, Chain]]:
    out, d = [], date.fromisoformat(start)
    for s in spots:
        out.append((d.isoformat(), chain(d.isoformat(), s, vol)))
        d += timedelta(days=1)
    return out


def test_the_entry_is_60_days_out_or_the_next_session_and_never_under_45():
    assert m.entry_day("2025-03-21") == "2025-01-21"  # 60 days before is a Tuesday
    assert m.entry_day("2025-04-17") == "2025-02-18"  # 2025-02-16 is a Sunday, the 17th a holiday
    assert all(
        (date.fromisoformat(e) - date.fromisoformat(m.entry_day(e))).days >= 45
        for e in ("2025-03-21", "2025-04-17")
    )


def test_the_filters_read_the_rank_and_realised_vol_as_declared():
    assert m.iv_rank([10, 20, 30], 3) == pytest.approx(100.0)
    assert m.iv_rank([10, 20, 15], 3) == pytest.approx(50.0)
    assert m.iv_rank([10, 20], 3) is None and m.iv_rank([5, 5, 5], 3) is None
    flat = [100.0 * (1.01 if i % 2 else 1.0) for i in range(40)]
    assert m.realised_vol(flat, 30) > 0 and m.realised_vol(flat[:30], 30) is None
    assert m.filters(31, 15, 16) == {"ivr30": True, "ivr50": False, "ivr70": False, "hv_lt_iv": True}
    assert m.filters(29, 17, 16) == {"ivr30": False, "ivr50": False, "ivr70": False, "hv_lt_iv": False}
    assert m.filters(None, 15, 16) is None


def test_the_exit_rules_stop_first_then_target_then_time():
    assert m.exit_rule(100, None, 200, 40) == "stop"  # a loss equal to the credit
    assert m.exit_rule(100, None, 199, 40) is None
    assert m.exit_rule(100, None, 50, 40) == "target"
    assert m.exit_rule(100, None, 120, 21) == "time"
    assert m.exit_rule(100, None, 250, 10) == "stop"
    # A spread whose credit is more than half the width: the stop is half the max loss instead.
    assert m.exit_rule(300, 500, 399, 40) is None and m.exit_rule(300, 500, 400, 40) == "stop"
    # One whose credit stop can fire keeps it.
    assert m.exit_rule(200, 500, 399, 40) is None and m.exit_rule(200, 500, 400, 40) == "stop"


def _check_money(t):
    assert t["gross"] - t["fees"] - t["settlement"] - t["slippage"] == pytest.approx(t["net"], abs=1e-3)


def test_a_quiet_put_is_closed_at_the_target_and_its_money_adds_up():
    t = m.trade("SPY", "put", EXPIRY, path([600.0] + [610.0] * 59))
    assert t["exit_kind"] == "target" and t["gross"] > 0 and t["bp"] == 100.0 * t["strikes"][0]
    assert t["delta"] > 0  # a short put is long the fund
    _check_money(t)


def test_a_fall_stops_the_put_and_the_spread_holds_its_width():
    falling = [600.0 - 4.0 * i for i in range(60)]
    put = m.trade("SPY", "put", EXPIRY, path(falling))
    assert put["exit_kind"] == "stop" and put["net"] < 0
    spread = m.trade("SPY", "spread", EXPIRY, path(falling))
    width = 100.0 * (spread["strikes"][0] - spread["strikes"][1])
    assert spread["bp"] == pytest.approx(width - spread["credit"]) and spread["net"] > -width
    _check_money(put)
    _check_money(spread)


def test_a_flat_market_is_closed_at_21_days():
    t = m.trade("SPY", "put", EXPIRY, path([600.0] * 60, vol=0.18))
    assert t["exit_kind"] in ("time", "target")
    if t["exit_kind"] == "time":
        assert (date.fromisoformat(EXPIRY) - date.fromisoformat(t["exit"])).days <= 21


def test_a_session_without_a_mark_holds_to_the_next():
    p = path([600.0] + [610.0] * 59)
    p[1] = (p[1][0], None)
    t = m.trade("SPY", "put", EXPIRY, p)
    assert t["exit"] != p[1][0]


def test_cycle_rows_pool_the_funds_entered_that_cycle():
    ts = [
        {
            "cycle": "2025-03-21",
            "entry": "2025-01-21",
            "net": 10.0,
            "bp": 100.0,
            "days": 20,
            "delta": 30.0,
            "f0": 600,
            "f1": 610,
        },
        {
            "cycle": "2025-03-21",
            "entry": "2025-01-21",
            "net": -4.0,
            "bp": 300.0,
            "days": 40,
            "delta": 10.0,
            "f0": 200,
            "f1": 190,
        },
    ]
    (row,) = m.cycle_rows(ts, lambda s: 0.04)
    assert row["net"] == pytest.approx(6.0) and row["bp"] == pytest.approx(400.0)
    assert row["days"] == pytest.approx((20 * 100 + 40 * 300) / 400) and row["funds"] == 2


def test_the_clustered_difference_is_kept_less_skipped_and_none_when_one_side_is_empty():
    wobble = [((i * 7) % 11 - 5) / 10.0 for i in range(30)]  # varied, deterministic
    kept = [{"net": 3.0 + w, "bp": 100.0, "cycle": f"k{i}"} for i, w in enumerate(wobble)]
    skipped = [{"net": -1.0 - w, "bp": 100.0, "cycle": f"s{i}"} for i, w in enumerate(wobble)]
    d = m.clustered_difference(kept, skipped)
    want = sum(t["net"] for t in kept) / 3000 - sum(t["net"] for t in skipped) / 3000
    assert d["difference"] == pytest.approx(want) and d["t"] > 2.4 and d["clusters"] == 60
    assert m.clustered_difference(kept, [])["t"] is None
    flipped = m.clustered_difference(skipped, kept)
    assert flipped["difference"] == pytest.approx(-want) and flipped["t"] == pytest.approx(-d["t"])
    # Two entries of one cycle count once: the same values in fewer clusters give a smaller t.
    paired = [{**s, "cycle": k["cycle"]} for k, s in zip(kept, skipped, strict=True)]
    assert m.clustered_difference(kept, paired)["clusters"] == 30


def test_the_filter_rule_reads_its_holdout_only_after_both_design_passes():
    good = {"kept": 30, "skipped": 30, "difference": 0.02, "t": 3.0}
    thin = {"kept": 19, "skipped": 30, "difference": 0.02, "t": 3.0}
    assert m.filter_verdict(thin, good, True)["verdict"] == "no verdict"
    out = m.filter_verdict(good, good, False)
    assert out["holdout"]["verdict"] == "not read"
    out = m.filter_verdict(good, {**good, "difference": -0.01}, True)
    assert out["holdout"]["verdict"] == "fail" and out["verdict"] == "fail"
    assert m.filter_verdict(good, good, True)["verdict"] == "pass"
    assert m.filter_verdict({**good, "t": 2.0}, good, True)["verdict"] == "fail"


def test_the_requests_are_item_46s_paths_for_the_four_funds(tmp_path):
    reqs = [
        {"symbol": s, "expiration": e, "start": "x", "through": e}
        for s in ("SPY", "QQQ", "IWM", "GLD", "SLV", "SPXW")
        for e in ("2016-03-18", "2026-10-16", "2026-11-20")
    ]
    f = tmp_path / "path_requests.json"
    f.write_text(__import__("json").dumps({"requests": reqs}), encoding="utf-8")
    got = m.requests(f)
    assert {r["symbol"] for r in got} == {"SPY", "QQQ", "IWM", "GLD"}
    assert {r["expiration"] for r in got} == {"2016-03-18", "2026-10-16"}
